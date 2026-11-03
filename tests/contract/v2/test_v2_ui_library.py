"""R2-3: the v2 UI library API (implementation spec §10.3, §11; errata E28).

Through the real HTTP boundary (session + CSRF + app Origin): upload -> classify
-> confirm -> process -> read evidence, replace, dismiss, logical delete. The
evidence capability is bound to the UI session (caller_scope), so another
session cannot read it (C-10 through HTTP).
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from conftest import NOW
from starlette.testclient import TestClient

from syllva.intake.v2 import LocalIntake
from syllva.library.blobs import BlobStore
from syllva.state.v2.database import Database
from syllva.ui.http import create_app
from syllva.ui.library import LibraryApi
from syllva.ui.sessions import SessionStore

pytestmark = pytest.mark.contract

PORT, ORIGIN = 49152, "syllva://app"


@pytest.fixture
def ui(v2_db: Database):  # type: ignore[no-untyped-def]
    v2_db.connection.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET', '네트워크', 'NET', '2026-2', ?)", (NOW,))
    blobs = BlobStore(v2_db.layout.blobs_dir, v2_db.layout.tmp_dir)
    intake = LocalIntake(v2_db.writer(), blobs)
    store = SessionStore()
    library = LibraryApi(v2_db, blobs, intake, kick=lambda: intake.run_pending(), upload_limit=64 * 1024)
    app = create_app(store, port=PORT, allowed_origins=(ORIGIN,), library=library)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    csrf = client.post("/api/session", json={"bootstrap": store.issue_bootstrap("main")}, headers={"Origin": ORIGIN}).json()["csrf"]
    client.headers.update({"Origin": ORIGIN, "X-Syllva-CSRF": csrf})
    return client, store


def _upload(client: TestClient, name: str, data: bytes, course: str | None = None):  # type: ignore[no-untyped-def]
    params = {"course": course} if course else {}
    return client.post("/api/library/upload", content=data, params=params,
                       headers={"X-Syllva-Filename": quote(name), "Content-Type": "application/octet-stream"})


def test_upload_review_confirm_process_and_read(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    up = _upload(client, "녹음 정리.txt", b"[00:00] TCP handshake\n[01:00] SYN ACK\n")
    assert up.status_code == 200 and up.json()["state"] == "NEEDS_REVIEW"
    cid = up.json()["candidate_id"]
    assert [c["candidate_id"] for c in client.get("/api/intake/candidates").json()["candidates"]] == [cid]
    done = client.post(f"/api/library/{cid}/classify", json={"course_key": "NET", "kind": "TRANSCRIPT", "new_session_held_on": "2026-09-01"})
    assert done.status_code == 200 and done.json()["material_id"] == "NET-M01"
    [item] = client.get("/api/library/items").json()["items"]
    assert (item["material_id"], item["processing_status"], item["title"], item["verified"]) == ("NET-M01", "READY", "녹음 정리.txt", False)
    context = client.get("/api/library/NET-M01/context", params={"query": "handshake"}).json()
    assert context["evidence"] and "TCP" in context["evidence"][0]["text"]
    chunk = client.get("/api/library/NET-M01/chunk", params={
        "context_id": context["context_id"], "locator": context["evidence"][0]["locator"], "part": 1}).json()
    assert chunk["text"] == context["evidence"][0]["text"]


def test_another_session_cannot_read_this_sessions_evidence(ui) -> None:  # type: ignore[no-untyped-def]
    client, store = ui
    cid = _upload(client, "a.txt", b"[00:00] secret topic\n").json()["candidate_id"]
    client.post(f"/api/library/{cid}/classify", json={"course_key": "NET", "kind": "TRANSCRIPT", "new_session_held_on": "2026-09-01"})
    context = client.get("/api/library/NET-M01/context").json()
    other = TestClient(client.app, base_url=client.base_url)
    other.post("/api/session", json={"bootstrap": store.issue_bootstrap("notes")}, headers={"Origin": ORIGIN})
    refused = other.get("/api/library/NET-M01/chunk", params={
        "context_id": context["context_id"], "locator": context["evidence"][0]["locator"], "part": 1})
    assert refused.status_code == 403


def test_mutations_need_csrf_and_origin(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    bare = {"X-Syllva-Filename": "a.txt", "Content-Type": "application/octet-stream"}
    assert client.post("/api/library/upload", content=b"x", headers={**bare, "X-Syllva-CSRF": "wrong"}).status_code == 403
    assert client.post("/api/library/upload", content=b"x", headers={**bare, "Origin": "http://127.0.0.1:1"}).status_code == 403


def test_upload_limits_and_validation(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    assert _upload(client, "big.txt", b"x" * (64 * 1024 + 1)).status_code == 413
    assert client.post("/api/library/upload", content=b"x", headers={"Content-Type": "application/octet-stream"}).status_code == 400
    exe = _upload(client, "setup.exe", b"MZ")
    assert exe.status_code == 200 and (exe.json()["state"], exe.json()["reason"]) == ("UNSUPPORTED", "UNSUPPORTED_TYPE")


def test_classify_accepts_only_the_documented_fields(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    cid = _upload(client, "a.txt", b"[00:00] x\n").json()["candidate_id"]
    bad = client.post(f"/api/library/{cid}/classify", json={"course_key": "NET", "kind": "TRANSCRIPT", "verified": True})
    assert bad.status_code == 400
    wrong = client.post(f"/api/library/{cid}/classify", json={"course_key": "NOPE", "kind": "TRANSCRIPT"})
    assert wrong.status_code == 409 and wrong.json()["error"] == "INTAKE_INVALID"


def test_no_route_sets_human_only_columns(ui) -> None:  # type: ignore[no-untyped-def]
    """C-04 via HTTP: nothing but the gate (begin over HTTP, commit over stdio) marks a material verified."""

    client, _ = ui
    routes = {getattr(r, "path", "") for r in client.app.app.routes}  # Boundary wraps the Starlette app
    assert not any(word in path for path in routes for word in ("verify", "verified", "scope", "submission", "adopt", "commit"))


def test_replace_dismiss_and_delete(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    cid = _upload(client, "a.txt", b"[00:00] TCP\n").json()["candidate_id"]
    client.post(f"/api/library/{cid}/classify", json={"course_key": "NET", "kind": "TRANSCRIPT", "new_session_held_on": "2026-09-01"})
    rep = client.post("/api/library/NET-M01/replace", content=b"[00:00] UDP\n",
                      headers={"X-Syllva-Filename": "a2.txt", "Content-Type": "application/octet-stream"})
    assert rep.status_code == 200 and rep.json()["state"] in ("CONFIRMED", "READY")
    assert "UDP" in client.get("/api/library/NET-M01/context").json()["evidence"][0]["text"]
    dup = _upload(client, "again.txt", b"[00:00] UDP\n").json()
    assert dup["state"] == "DUPLICATE"
    assert client.post(f"/api/library/{dup['candidate_id']}/dismiss").json()["state"] == "DISMISSED"
    assert client.delete("/api/library/NET-M01").status_code == 200
    assert client.get("/api/library/items").json()["items"] == []
    assert client.get("/api/library/NET-M01/context").status_code == 404


def test_reads_need_a_session(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    anonymous = TestClient(client.app, base_url=client.base_url)
    for path in ("/api/library/items", "/api/intake/candidates", "/api/library/NET-M01/context"):
        assert anonymous.get(path).status_code == 401


# --- review of PR #51 --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["a%0Ab.txt", "a%0Db.txt", "%1B%5B31m.txt", "", "..", "x" * 201 + ".txt"])
def test_bad_filenames_are_refused(ui, raw: str) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    response = client.post("/api/library/upload", content=b"[00:00] x\n",
                           headers={"X-Syllva-Filename": raw, "Content-Type": "application/octet-stream"})
    assert response.status_code == 400 and response.json() == {"error": "BAD_FILENAME"}


def test_paths_in_filenames_keep_only_the_basename(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    for raw in ("../../etc/passwd.txt", "..%5C..%5Cwin.txt"):
        up = _upload(client, raw, f"[00:00] {raw}\n".encode())
        assert up.status_code == 200 and "/" not in up.json()["name"] and "\\" not in up.json()["name"]


def test_role_must_be_known(ui) -> None:  # type: ignore[no-untyped-def]
    client, _ = ui
    cid = _upload(client, "a.txt", b"[00:00] x\n").json()["candidate_id"]
    bad = client.post(f"/api/library/{cid}/classify", json={"course_key": "NET", "kind": "TRANSCRIPT", "role": "<b>"})
    assert bad.status_code == 400


def test_spooled_uploads_stay_inside_the_library(ui, v2_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import tempfile

    seen: list[object] = []
    real = tempfile.SpooledTemporaryFile

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs.get("dir"))
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", spy)
    client, _ = ui
    _upload(client, "a.txt", b"[00:00] x\n")
    assert seen == [str(v2_db.layout.tmp_dir)]
