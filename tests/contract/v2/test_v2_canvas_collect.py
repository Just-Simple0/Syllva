"""R3-2: Canvas collection is fail-closed (spec §12; R3 plan §6.2, §6.3).

* A listing is complete only when its pages are proven: ``rel=next`` must be exactly
  page n+1, a full page without ``next`` is probed, ``rel=last`` beyond the current page
  without ``next`` is incomplete, pagination never leaves the listing, and every other
  oddity has its own INCOMPLETE reason (the table below).
* ``FULL_PAGE_PROBE``: a full page without ``next`` probes page n+1. An empty probe proves
  the end; a non-empty probe means Canvas withheld ``next`` — INCOMPLETE. That is stricter
  than the v1.2 observer (which used the probed page) and intentional.
* Only a COMPLETE run writes anything besides its own ``canvas_collections`` row; a run
  still RUNNING at start-up becomes ``FAILED(INTERRUPTED)``.
* The sync never touches ``submission_status`` (I4), never deletes an activity, writes
  only Canvas-origin activities, and stores no URL or download verifier.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import timedelta, timezone
from typing import Any

import pytest
from conftest import NOW

from syllva.canvas.collect import MAX_ITEMS, PER_PAGE, Incomplete, collect_course
from syllva.canvas.http import CanvasCheckError, CanvasResponse
from syllva.canvas.sync import CanvasSync
from syllva.domain.errors import PolicyViolation
from syllva.state.v2.database import Database
from syllva.state.v2.ownership import Actor, Owner

pytestmark = pytest.mark.contract

ORIGIN = "https://canvas.example.edu"
CID = "1234"
A = f"/api/v1/courses/{CID}/assignments?order_by=name&per_page={PER_PAGE}"
N = f"/api/v1/announcements?context_codes[]=course_{CID}&per_page={PER_PAGE}"
M = f"/api/v1/courses/{CID}/modules?include[]=items&per_page={PER_PAGE}"
KST = timezone(timedelta(hours=9))


def page(url: str, number: int) -> str:
    return f"{url}&page={number}" if number > 1 else url


def link(url: str, number: int, rel: str = "next") -> str:
    return f'<{ORIGIN}{page(url, number)}>; rel="{rel}"'


class Canvas:
    """A scripted Canvas: url -> (body, Link header or None)."""

    def __init__(self, routes: dict[str, tuple[Any, str | None]] | None = None) -> None:
        self.routes: dict[str, tuple[Any, str | None]] = {A: ([], None), N: ([], None), M: ([], None)}
        self.routes.update(routes or {})
        self.calls: list[str] = []

    def fetch(self, url: str, deadline: float) -> CanvasResponse:
        self.calls.append(url)
        key = url.removeprefix(ORIGIN)
        if key not in self.routes:
            return CanvasResponse(200, {}, b"[]")
        body, header = self.routes[key]
        headers = {} if header is None else {"Link": header}
        return CanvasResponse(200, headers, body if isinstance(body, bytes) else json.dumps(body).encode())


def assignment(i: int, **extra: Any) -> dict[str, Any]:
    return {"id": 100 + i, "name": f"HW{i}", "due_at": "2026-11-10T14:59:59Z", "updated_at": "2026-11-01T00:00:00Z",
            "html_url": f"{ORIGIN}/courses/{CID}/assignments/{100 + i}", **extra}


def module(mid: int, name: str, items: list[dict[str, Any]], count: int | None = None) -> dict[str, Any]:
    return {"id": mid, "name": name, "items": items, "items_count": len(items) if count is None else count}


def item(i: int, title: str = "Slides", kind: str = "File", **extra: Any) -> dict[str, Any]:
    return {"id": 500 + i, "title": title, "type": kind, "content_id": 900 + i,
            "url": f"{ORIGIN}/api/v1/courses/{CID}/files/{900 + i}?verifier=SECRETVERIFIER", **extra}


def reason_of(canvas: Canvas) -> str:
    with pytest.raises(Incomplete) as caught:
        collect_course(canvas.fetch, CID, origin=ORIGIN)
    return caught.value.reason


# --- completeness (pagination) ----------------------------------------------------------------


def test_a_proven_listing_is_complete() -> None:
    full = [assignment(i) for i in range(PER_PAGE)]
    canvas = Canvas({A: (full, link(A, 2)), page(A, 2): ([assignment(PER_PAGE)], None)})
    collected = collect_course(canvas.fetch, CID, origin=ORIGIN)
    assert len(collected["ASSIGNMENT"]) == PER_PAGE + 1


def test_a_full_page_without_next_is_probed_and_an_empty_probe_ends_it() -> None:
    canvas = Canvas({A: ([assignment(i) for i in range(PER_PAGE)], None), page(A, 2): ([], None)})
    assert len(collect_course(canvas.fetch, CID, origin=ORIGIN)["ASSIGNMENT"]) == PER_PAGE
    assert page(A, 2) in [c.removeprefix(ORIGIN) for c in canvas.calls]


def test_a_non_empty_probe_is_incomplete() -> None:
    canvas = Canvas({A: ([assignment(i) for i in range(PER_PAGE)], None), page(A, 2): ([assignment(99)], None)})
    assert reason_of(canvas) == "FULL_PAGE_PROBE"


@pytest.mark.parametrize(("routes", "reason"), [
    ({A: (b"not json", None)}, "MALFORMED_BODY"),
    ({A: ({"id": 1}, None)}, "MALFORMED_BODY"),
    ({A: ([assignment(1)], " ")}, "LINK_MALFORMED"),
    ({A: ([assignment(1)], "<x")}, "LINK_MALFORMED"),
    ({A: ([assignment(1)], f'<{ORIGIN}{A}&page=2>; rel=next, ')}, "LINK_MALFORMED"),
    ({A: ([assignment(1)], f'<{ORIGIN}{A}&page=2>; rel="next"; rel="prev"')}, "LINK_MALFORMED"),
    ({A: ([assignment(1)], "nextpage=2")}, "LINK_MALFORMED"),
    ({A: ([assignment(1)], f'<{ORIGIN}{A}&page=2>; rel="next-ish"')}, "LINK_UNRECOGNIZED"),
    ({A: ([assignment(1)], link(A, 3))}, "PAGE_SEQUENCE"),
    ({A: ([assignment(1)], link(A, 1))}, "PAGE_SEQUENCE"),
    ({A: ([assignment(1)], link(A, 4, "last"))}, "LAST_WITHOUT_NEXT"),
    ({A: ([assignment(1)], f'<{ORIGIN}/api/v1/courses/9999/assignments?order_by=name&per_page=50&page=2>; rel="next"')},
     "PAGINATION_SCOPE"),
    ({A: ([assignment(1)], f'<{ORIGIN}{A.replace("order_by=name", "order_by=due_at")}&page=2>; rel="next"')},
     "PAGINATION_SCOPE"),
    ({A: ([assignment(1), assignment(1)], None)}, "DUPLICATE_ID"),
    ({A: ([{"id": "x1", "name": "a"}], None)}, "MALFORMED_ITEM"),
    ({A: ([{"id": 1, "name": "  "}], None)}, "MALFORMED_ITEM"),
    ({A: (["just a string"], None)}, "MALFORMED_ITEM"),
    ({A: ([{"id": True, "name": "a"}], None)}, "MALFORMED_ITEM"),
    ({M: (["not a module"], None)}, "MALFORMED_MODULE"),
    ({M: ([{"id": 1, "items": [], "items_count": 0}], None)}, "MALFORMED_MODULE"),
    ({M: ([{"id": 1, "name": "1주차", "items": "x", "items_count": 0}], None)}, "MALFORMED_MODULE"),
    ({M: ([{"id": 1, "name": "1주차", "items": [], "items_count": "0"}], None)}, "MALFORMED_MODULE"),
    ({M: ([{"id": "1a", "name": "1주차", "items": [], "items_count": 0}], None)}, "MALFORMED_ITEM"),
    ({M: ([module(7, "1주차", [item(1)], count=2)], None)}, "MODULE_ITEMS_INCOMPLETE"),
])
def test_every_unproven_listing_has_its_reason(routes: dict[str, tuple[Any, str | None]], reason: str) -> None:
    assert reason_of(Canvas(routes)) == reason


def test_module_items_are_refetched_when_the_inline_list_is_short() -> None:
    items_url = f"/api/v1/courses/{CID}/modules/7/items?per_page={PER_PAGE}"
    canvas = Canvas({M: ([module(7, "1주차", [item(1)], count=2)], None), items_url: ([item(1), item(2)], None)})
    assert [i["resource_id"] for i in collect_course(canvas.fetch, CID, origin=ORIGIN)["MODULE_ITEM"]] == ["501", "502"]


def test_limits_are_incomplete() -> None:
    chain = {page(A, n): ([assignment(n * PER_PAGE + i) for i in range(PER_PAGE)], link(A, n + 1)) for n in range(1, 12)}
    assert reason_of(Canvas(chain)) in {"PAGE_LIMIT", "ITEM_LIMIT"}
    many = Canvas({A: ([assignment(i) for i in range(MAX_ITEMS + 1)], None)})
    assert reason_of(many) == "ITEM_LIMIT"
    ticks = iter(range(0, 10_000, 100))
    with pytest.raises(Incomplete) as caught:
        collect_course(Canvas().fetch, CID, origin=ORIGIN, monotonic=lambda: float(next(ticks)), max_seconds=45)
    assert caught.value.reason == "TIME_LIMIT"


def test_collected_rows_carry_no_urls_or_verifiers() -> None:
    announcement = {"id": 7, "title": "Notice", "posted_at": NOW, "message": "<b>html</b>",
                    "attachments": [{"id": 31, "display_name": "a.pdf", "size": 10, "content-type": "application/pdf",
                                     "updated_at": NOW, "url": f"{ORIGIN}/files/31/download?verifier=SECRETVERIFIER",
                                     "preview_url": "x?verifier=SECRETVERIFIER"}]}
    canvas = Canvas({N: ([announcement], None), M: ([module(7, "3주차", [item(1)])], None)})
    collected = collect_course(canvas.fetch, CID, origin=ORIGIN)
    text = json.dumps(collected)
    assert "SECRETVERIFIER" not in text and "url" not in text and "<b>" not in text
    assert collected["ANNOUNCEMENT"][0]["attachments"] == [
        {"id": "31", "display_name": "a.pdf", "size": 10, "content_type": "application/pdf", "updated_at": NOW}]
    assert collected["MODULE_ITEM"][0]["week"] == 3 and collected["MODULE_ITEM"][0]["attachments"] == [{"id": "901"}]


# --- the sync: what gets written ---------------------------------------------------------------


class FakeCredentials:
    def __init__(self, user_id: str = "42") -> None:
        self.user_id = user_id

    def token(self):  # type: ignore[no-untyped-def]
        from syllva.canvas.credential import CanvasToken

        return CanvasToken("7~token")

    def origin(self) -> str:
        return ORIGIN

    def status(self) -> dict[str, Any]:
        return {"connected": True, "profile_id": "c" + "0" * 32}


@pytest.fixture
def linked(v2_db: Database) -> Database:
    conn = v2_db.connection
    conn.execute("INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET','네트워크','NET','2026-2',?)", (NOW,))
    conn.execute("INSERT INTO canvas_profile(id, profile_id, origin, user_id, display_name, token_state, token_set_at, created_at) "
                 "VALUES (1, ?, ?, '42', 'Kim', 'SET', ?, ?)", ("c" + "0" * 32, ORIGIN, NOW, NOW))
    conn.execute("INSERT INTO canvas_courses(canvas_course_id, course_id, name, code, term_name, linked_at) "
                 "VALUES (?, 1, 'Networks', 'CS101', '2026-2', ?)", (CID, NOW))
    return v2_db


def make_sync(db: Database, canvas: Canvas, *, user: str = "42", verify_error: str | None = None) -> CanvasSync:
    def verify(origin: str, token: str) -> dict[str, str]:
        if verify_error:
            raise CanvasCheckError(verify_error)
        return {"user_id": user, "display_name": "Kim"}

    @contextmanager
    def open_fetch(origin: str, token: str):  # type: ignore[no-untyped-def]
        yield canvas.fetch

    return CanvasSync(db.writer(), FakeCredentials(), open_fetch=open_fetch, verify=verify, tz=KST)


def rows(db: Database, sql: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in db.connection.execute(sql)]


def test_a_complete_run_writes_items_and_canvas_activities(linked: Database) -> None:
    canvas = Canvas({A: ([assignment(1), assignment(2, due_at=None)], None),
                     M: ([module(7, "1주차", [item(1)])], None)})
    [result] = make_sync(linked, canvas).run()
    assert result["status"] == "COMPLETE"
    assert rows(linked, "SELECT activity_id, title, due_at, origin, canvas_assignment_id, submission_status FROM activities "
                        "ORDER BY id") == [
        ("NET-A01", "HW1", "2026-11-10T23:59:59+09:00", "CANVAS", "101", "NOT_SUBMITTED"),
        ("NET-A02", "HW2", None, "CANVAS", "102", "NOT_SUBMITTED")]
    assert rows(linked, "SELECT kind, resource_id, active FROM canvas_items ORDER BY kind, resource_id") == [
        ("ASSIGNMENT", "101", 1), ("ASSIGNMENT", "102", 1), ("MODULE_ITEM", "501", 1)]
    assert "SECRETVERIFIER" not in "\n".join(linked.connection.iterdump())


def test_an_incomplete_run_writes_only_its_collection_row(linked: Database) -> None:
    sync = make_sync(linked, Canvas({A: ([assignment(1)], None)}))
    sync.run()
    before = rows(linked, "SELECT activity_id, title, due_at FROM activities"), rows(linked, "SELECT * FROM canvas_items")
    broken = Canvas({A: ([assignment(1, name="CHANGED"), assignment(3)], link(A, 3))})
    [result] = make_sync(linked, broken).run()
    assert (result["status"], result["reason"]) == ("INCOMPLETE", "PAGE_SEQUENCE")
    assert (rows(linked, "SELECT activity_id, title, due_at FROM activities"), rows(linked, "SELECT * FROM canvas_items")) == before
    assert rows(linked, "SELECT status, reason FROM canvas_collections ORDER BY id") == [
        ("COMPLETE", None), ("INCOMPLETE", "PAGE_SEQUENCE")]


def test_a_vanished_assignment_keeps_its_activity(linked: Database) -> None:
    make_sync(linked, Canvas({A: ([assignment(1), assignment(2)], None)})).run()
    make_sync(linked, Canvas({A: ([assignment(1)], None)})).run()
    assert rows(linked, "SELECT resource_id, active FROM canvas_items WHERE kind='ASSIGNMENT' ORDER BY resource_id") == [
        ("101", 1), ("102", 0)]
    assert rows(linked, "SELECT activity_id FROM activities ORDER BY id") == [("NET-A01",), ("NET-A02",)]
    with pytest.raises(sqlite3.IntegrityError, match="ACTIVITY_DELETE"):
        linked.connection.execute("DELETE FROM activities WHERE activity_id = 'NET-A02'")


def test_a_resync_changes_title_and_due_but_never_the_submission_status(linked: Database) -> None:
    from conftest import gate

    make_sync(linked, Canvas({A: ([assignment(1)], None)})).run()
    gate_id = gate(linked.connection, "SET_SUBMISSION_STATUS", "activity", "NET-A01", "n1")
    linked.connection.execute("UPDATE activities SET submission_status='SUBMITTED', submission_gate_id=? WHERE activity_id='NET-A01'",
                              (gate_id,))
    make_sync(linked, Canvas({A: ([assignment(1, name="HW1 (revised)", due_at="2026-11-12T14:59:59Z")], None)})).run()
    assert rows(linked, "SELECT title, due_at, submission_status FROM activities") == [
        ("HW1 (revised)", "2026-11-12T23:59:59+09:00", "SUBMITTED")]


def test_the_principal_must_still_be_the_connected_user(linked: Database) -> None:
    [result] = make_sync(linked, Canvas({A: ([assignment(1)], None)}), user="77").run()
    assert (result["status"], result["reason"]) == ("FAILED", "PRINCIPAL_CHANGED")
    assert rows(linked, "SELECT count(*) FROM activities") == [(0,)]


def test_a_canvas_error_fails_the_run(linked: Database) -> None:
    [result] = make_sync(linked, Canvas(), verify_error="INVALID_CREDENTIAL").run()
    assert (result["status"], result["reason"]) == ("FAILED", "INVALID_CREDENTIAL")


def test_an_interrupted_run_is_failed_at_startup(linked: Database) -> None:
    linked.connection.execute("INSERT INTO canvas_collections(canvas_course_id, started_at, status) VALUES (?, ?, 'RUNNING')",
                              (CID, NOW))
    assert make_sync(linked, Canvas()).recover() == 1
    assert rows(linked, "SELECT status, reason FROM canvas_collections") == [("FAILED", "INTERRUPTED")]


def test_activity_ids_are_bounded_per_course(linked: Database) -> None:
    for n in range(1, 100):
        linked.connection.execute(
            "INSERT INTO activities(activity_id, course_id, title, created_at) VALUES (?, 1, 'user', ?)", (f"NET-A{n:02d}", NOW))
    [result] = make_sync(linked, Canvas({A: ([assignment(1)], None)})).run()
    assert (result["status"], result["reason"]) == ("COMPLETE", "ID_SPACE_EXHAUSTED")
    assert rows(linked, "SELECT resource_id FROM canvas_items WHERE kind='ASSIGNMENT'") == [("101",)]


def test_the_sync_runs_one_at_a_time(linked: Database) -> None:
    sync = make_sync(linked, Canvas())
    assert sync.start() is True
    sync.wait(10)
    assert sync.status()["running"] is False
    assert sync.status()["courses"][0]["last"]["status"] == "COMPLETE"


# --- the collector's write limits (C-03 for R3-2) ----------------------------------------------


def test_the_collector_writes_only_canvas_activities_and_only_their_canvas_columns(linked: Database) -> None:
    writer = linked.writer()
    linked.connection.execute("INSERT INTO activities(activity_id, course_id, title, created_at) VALUES ('NET-A01', 1, 'mine', ?)", (NOW,))
    with pytest.raises(PolicyViolation, match="origin='CANVAS'"):
        writer.update(Owner.CATALOG, Actor.CANVAS_COLLECTOR, "activities", {"activity_id": "NET-A01"}, {"title": "x"})
    with pytest.raises(PolicyViolation, match="origin='CANVAS'"):
        writer.insert(Owner.CATALOG, Actor.CANVAS_COLLECTOR, "activities",
                      {"activity_id": "NET-A02", "course_id": 1, "title": "x", "created_at": NOW})
    writer.insert(Owner.CATALOG, Actor.CANVAS_COLLECTOR, "activities",
                  {"activity_id": "NET-A02", "course_id": 1, "title": "x", "created_at": NOW, "origin": "CANVAS"})
    for column, value in [("submission_status", "SUBMITTED"), ("course_id", 2), ("origin", "USER"), ("created_at", NOW)]:
        with pytest.raises(PolicyViolation):
            writer.update(Owner.CATALOG, Actor.CANVAS_COLLECTOR, "activities", {"activity_id": "NET-A02"}, {column: value})
    for table, owner in [("courses", Owner.CATALOG), ("materials", Owner.CATALOG), ("source_files", Owner.SOURCE),
                         ("jobs", Owner.SYSTEM), ("ai_artifacts", Owner.AI), ("user_notes", Owner.USER)]:
        with pytest.raises(PolicyViolation):
            writer.insert(owner, Actor.CANVAS_COLLECTOR, table, {"id": 1})


def test_only_the_collector_writes_the_canvas_mirror(linked: Database) -> None:
    for actor in (Actor.INGESTER, Actor.UI_USER, Actor.CORE_AGENT_OUTPUT):
        with pytest.raises(PolicyViolation):
            linked.writer().insert(Owner.SOURCE, actor, "canvas_items", {"canvas_course_id": CID})
        with pytest.raises(PolicyViolation):
            linked.writer().insert(Owner.SYSTEM, actor, "canvas_collections", {"canvas_course_id": CID})
    with pytest.raises(sqlite3.IntegrityError, match="CANVAS_ITEM_DELETE"):
        make_sync(linked, Canvas({A: ([assignment(1)], None)})).run()
        linked.connection.execute("DELETE FROM canvas_items")
