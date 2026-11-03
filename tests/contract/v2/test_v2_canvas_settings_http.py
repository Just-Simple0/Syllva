"""R3-1: Canvas settings over the real HTTP boundary (spec §10.3; R3 plan §6.1, I9).

The token goes in once and never comes back out: not in any response body or
header, not in the log, not in the database. Errors are fixed codes. The token
body is bounded to 4 KiB by the boundary itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import NOW
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.keychain import MemoryKeychain
from syllva.canvas.credential import CanvasCredentials
from syllva.canvas.http import CanvasCheckError
from syllva.core import logging as core_logging
from syllva.state.v2.database import Database
from syllva.ui.canvas import CanvasApi
from syllva.ui.http import create_app
from syllva.ui.sessions import SessionStore

pytestmark = pytest.mark.contract

PORT, APP = 49152, "syllva://localhost"
ORIGIN = "https://canvas.example.edu"
TOKEN = "7~SECRETtokenVALUE0123456789abcdefABCDEF"
COURSES = [{"course_id": "1234", "name": "Networks", "code": "CS101", "term_id": "9", "term_name": "2026-2"},
           {"course_id": "5678", "name": "Algorithms", "code": "CS202", "term_id": "9", "term_name": "2026-2"}]


class FakeClient:
    def __init__(self) -> None:
        self.seen_tokens: list[str] = []

    def verify(self, origin: str, token: str) -> dict[str, str]:
        if token != TOKEN:
            raise CanvasCheckError("INVALID_CREDENTIAL")
        return {"user_id": "42", "display_name": "Kim"}

    def discover(self, origin: str, token: str, profile_id: str) -> dict[str, object]:
        self.seen_tokens.append(token)
        return {"terms": [], "courses": COURSES}

    def reread(self, origin: str, token: str, course_ids) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
        found = [c for c in COURSES if c["course_id"] in list(course_ids)]
        if not found:
            raise CanvasCheckError("NOT_FOUND")
        return found


@pytest.fixture
def ui(v2_db: Database, tmp_path: Path):  # type: ignore[no-untyped-def]
    v2_db.connection.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET', '네트워크', 'NET', '2026-2', ?)", (NOW,))
    keychain, client = MemoryKeychain(), FakeClient()
    credentials = CanvasCredentials(v2_db.writer(), keychain=lambda: keychain, verify=client.verify)
    store = SessionStore()
    app = create_app(store, port=PORT, allowed_origins=(APP,), canvas=CanvasApi(credentials, client))
    http = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    csrf = http.post("/api/session", json={"bootstrap": store.issue_bootstrap("main")}, headers={"Origin": APP}).json()["csrf"]
    http.headers.update({"Origin": APP, "X-Syllva-CSRF": csrf})
    core_logging.configure(tmp_path / "logs")
    yield http, keychain, client, v2_db, tmp_path / "logs"
    core_logging.configure(None)


def _no_token_anywhere(responses, db: Database, logs: Path) -> None:  # type: ignore[no-untyped-def]
    for response in responses:
        assert TOKEN not in response.text and all(TOKEN not in v for v in response.headers.values())
    assert TOKEN not in "\n".join(db.connection.iterdump())
    assert all(TOKEN not in p.read_text() for p in logs.glob("*"))


def test_connect_show_link_and_forget_without_the_token_ever_coming_back(ui) -> None:  # type: ignore[no-untyped-def]
    http, keychain, client, db, logs = ui
    seen = [http.get("/api/settings/canvas")]
    assert seen[0].json() == {"connected": False}
    seen.append(http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN}))
    assert seen[-1].status_code == 200 and seen[-1].json()["connected"] is True
    assert list(keychain.entries.values()) == [TOKEN]
    seen.append(http.get("/api/settings/canvas"))
    assert set(seen[-1].json()) == {"connected", "needs_token", "profile_id", "origin", "display_name", "token_set_at",
                                     "file_hosts", "courses"}
    seen.append(http.get("/api/canvas/courses"))
    assert [c["canvas_course_id"] for c in seen[-1].json()["courses"]] == ["1234", "5678"] and client.seen_tokens == [TOKEN]
    seen.append(http.put("/api/canvas/courses/1234", json={"course_key": "NET"}))
    assert seen[-1].json()["courses"] == [{"canvas_course_id": "1234", "name": "Networks", "code": "CS101",
                                           "term_name": "2026-2", "course_key": "NET"}]
    seen.append(http.get("/api/canvas/courses"))
    assert seen[-1].json()["courses"][0]["linked_course_key"] == "NET"
    seen.append(http.put("/api/settings/canvas/file-hosts", json={"hosts": ["files.example-cdn.com"]}))
    assert seen[-1].json() == {"file_hosts": ["files.example-cdn.com"]}
    seen.append(http.delete("/api/settings/canvas"))
    assert seen[-1].json() == {"ok": True} and keychain.entries == {}
    _no_token_anywhere(seen, db, logs)


def test_errors_are_fixed_codes_without_the_token(ui) -> None:  # type: ignore[no-untyped-def]
    http, keychain, _, db, logs = ui
    seen = [http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN + "x"})]
    assert (seen[0].status_code, seen[0].json()) == (409, {"error": "INVALID_CREDENTIAL"})
    seen.append(http.put("/api/settings/canvas/token", json={"origin": "http://canvas.example.edu", "token": TOKEN}))
    assert seen[-1].json() == {"error": "DESTINATION_NOT_ALLOWED"}
    seen.append(http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN + " \n"}))
    assert (seen[-1].status_code, seen[-1].json()) == (400, {"error": "TOKEN_INVALID"})
    keychain.fail_set = True
    seen.append(http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN}))
    assert seen[-1].json() == {"error": "KEYCHAIN_WRITE_FAILED"}
    seen.append(http.get("/api/canvas/courses"))
    assert seen[-1].json() == {"error": "TOKEN_NOT_SET"}
    _no_token_anywhere(seen, db, logs)


def test_the_token_body_is_bounded_by_the_boundary(ui) -> None:  # type: ignore[no-untyped-def]
    http, keychain, *_ = ui
    big = http.put("/api/settings/canvas/token", content=b'{"origin":"' + b"a" * 5000 + b'","token":"x"}',
                   headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and keychain.entries == {}


@pytest.mark.parametrize("body", [{"origin": ORIGIN}, {"origin": ORIGIN, "token": TOKEN, "extra": 1},
                                  {"origin": 1, "token": TOKEN}, ["x"]])
def test_the_token_body_has_exactly_two_strings(ui, body) -> None:  # type: ignore[no-untyped-def]
    http, keychain, *_ = ui
    response = http.put("/api/settings/canvas/token", json=body)
    assert (response.status_code, response.json()) == (400, {"error": "BAD_REQUEST"}) and keychain.entries == {}


def test_changes_need_csrf_and_reads_need_a_session(ui) -> None:  # type: ignore[no-untyped-def]
    http, keychain, *_ = ui
    assert http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN},
                    headers={"X-Syllva-CSRF": "wrong"}).status_code == 403
    assert keychain.entries == {}
    anonymous = TestClient(http.app, base_url=http.base_url)
    for path in ("/api/settings/canvas", "/api/canvas/courses"):
        assert anonymous.get(path).status_code == 401


def test_linking_needs_a_course_canvas_really_has(ui) -> None:  # type: ignore[no-untyped-def]
    http, *_ = ui
    http.put("/api/settings/canvas/token", json={"origin": ORIGIN, "token": TOKEN})
    assert http.put("/api/canvas/courses/9999", json={"course_key": "NET"}).json() == {"error": "NOT_FOUND"}
    assert http.put("/api/canvas/courses/12a", json={"course_key": "NET"}).status_code in (400, 404)
    assert http.put("/api/canvas/courses/1234", json={"course_key": "NOPE"}).json() == {"error": "COURSE_UNKNOWN"}
    assert http.put("/api/canvas/courses/1234", json={"course_key": "NET"}).status_code == 200
    assert http.put("/api/canvas/courses/1234", json={"course_key": None}).json()["courses"] == []


def test_no_route_returns_or_writes_secrets_or_human_only_state(ui) -> None:  # type: ignore[no-untyped-def]
    http, *_ = ui
    paths = {getattr(r, "path", "") for r in http.app.app.routes}
    canvas = {p for p in paths if "canvas" in p}
    assert canvas == {"/api/settings/canvas", "/api/settings/canvas/token", "/api/settings/canvas/file-hosts",
                      "/api/canvas/courses", "/api/canvas/courses/{canvas_course_id}", "/api/canvas/sync",
                      "/api/canvas/status"}
    token_route = next(r for r in http.app.app.routes if getattr(r, "path", "") == "/api/settings/canvas/token")
    assert token_route.methods == {"PUT"}  # never readable
