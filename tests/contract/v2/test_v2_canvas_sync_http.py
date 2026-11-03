"""R3-2: sync trigger, sync status and the activity list over the HTTP boundary.

The sync starts only for a connected profile, one run at a time; the activity list is
read-only (no route sets a submission status: that stays the gate's, C-04).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import NOW
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.keychain import MemoryKeychain
from syllva.canvas.credential import CanvasCredentials, CanvasToken
from syllva.state.v2.database import Database
from syllva.ui.canvas import CanvasApi
from syllva.ui.http import create_app
from syllva.ui.sessions import SessionStore

pytestmark = pytest.mark.contract

PORT, APP, ORIGIN = 49152, "syllva://localhost", "https://canvas.example.edu"


class FakeSync:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0

    def start(self) -> bool:
        if self.running:
            return False
        self.starts += 1
        self.running = True
        return True

    def status(self) -> dict[str, object]:
        return {"running": self.running, "error": None, "courses": []}


@pytest.fixture
def ui(v2_db: Database):  # type: ignore[no-untyped-def]
    conn = v2_db.connection
    conn.execute("INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET','네트워크','NET','2026-2',?)", (NOW,))
    conn.execute("INSERT INTO activities(activity_id, course_id, title, due_at, created_at, origin, canvas_assignment_id) "
                 "VALUES ('NET-A01', 1, 'HW1', '2026-11-10T23:59:59+09:00', ?, 'CANVAS', '101')", (NOW,))
    conn.execute("INSERT INTO activities(activity_id, course_id, title, created_at) VALUES ('NET-A02', 1, 'Mine', ?)", (NOW,))
    credentials = CanvasCredentials(v2_db.writer(), keychain=MemoryKeychain, verify=lambda o, t: {"user_id": "42", "display_name": "K"})
    sync = FakeSync()
    store = SessionStore()
    app = create_app(store, port=PORT, allowed_origins=(APP,), canvas=CanvasApi(credentials, None, sync))  # type: ignore[arg-type]
    http = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    csrf = http.post("/api/session", json={"bootstrap": store.issue_bootstrap("main")}, headers={"Origin": APP}).json()["csrf"]
    http.headers.update({"Origin": APP, "X-Syllva-CSRF": csrf})
    return http, credentials, sync


def test_sync_needs_a_connection_and_runs_once_at_a_time(ui) -> None:  # type: ignore[no-untyped-def]
    http, credentials, sync = ui
    assert http.post("/api/canvas/sync").json() == {"error": "TOKEN_NOT_SET"}
    keychain = MemoryKeychain()
    credentials._keychain = lambda: keychain
    credentials.connect(ORIGIN, CanvasToken("7~token"))
    assert http.post("/api/canvas/sync", headers={"X-Syllva-CSRF": "wrong"}).status_code == 403
    started = http.post("/api/canvas/sync")
    assert (started.status_code, started.json()) == (202, {"started": True})
    assert http.post("/api/canvas/sync").json() == {"error": "SYNC_RUNNING"} and sync.starts == 1
    assert http.get("/api/canvas/status").json()["running"] is True


def test_activities_are_listed_by_due_date(ui) -> None:  # type: ignore[no-untyped-def]
    http, *_ = ui
    listed = http.get("/api/activities").json()["activities"]
    assert [(a["activity_id"], a["origin"], a["submission_status"]) for a in listed] == [
        ("NET-A01", "CANVAS", "NOT_SUBMITTED"), ("NET-A02", "USER", "NOT_SUBMITTED")]
    assert TestClient(http.app, base_url=http.base_url).get("/api/activities").status_code == 401


def test_no_route_writes_a_submission_status(ui) -> None:  # type: ignore[no-untyped-def]
    http, *_ = ui
    for route in http.app.app.routes:
        path, methods = getattr(route, "path", ""), getattr(route, "methods", set()) or set()
        if "activit" in path:
            assert methods <= {"GET", "HEAD"}, path
        assert "submission" not in path
