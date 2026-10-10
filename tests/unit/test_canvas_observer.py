"""P-B3a: read-only Canvas metadata observation (plan §2.3, §6.3)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from uls.intake.canvas_observer import CanvasObserver, ObservationResult, _Incomplete
from uls.intake.classification.calendar import SemesterRange
from uls.settings.canvas_checks import (
    CanvasCheckError,
    CanvasOrigin,
    _observation_target,
    validate_canvas_origin,
)
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.unit

ORIGIN = "https://canvas.example.edu"
COURSE_ID = 67535
KEY = "2026-2_LMS67535-001"
SEMESTER = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
WEEK_DATES = {1: "2026-09-02", 2: "2026-09-09", 3: "2026-09-16", 4: "2026-09-23"}


def _modules(skip_week: int | None = None, bare_week: bool = False) -> list[dict[str, Any]]:
    modules = []
    for week, day in WEEK_DATES.items():
        if week == skip_week:
            continue
        name = "참고자료" if bare_week and week == 4 else f"{week}주차"
        modules.append({
            "id": 100 + week, "name": name, "position": week, "items_count": 1,
            "items": [{"id": 1000 + week, "title": day, "type": "ExternalTool"}],
        })
    return modules


class _Killed(BaseException):
    pass


class FakeReader:
    def __init__(self, modules=None, assignments=None, announcements=None, *, fail_after: int | None = None,
                 extra_next: bool = False, module_items: dict[int, list] | None = None) -> None:
        self.modules = _modules() if modules is None else modules
        self.assignments = [{"id": 7, "name": "과제 1", "updated_at": "2026-09-01T00:00:00Z"}] if assignments is None else assignments
        self.announcements = [{"id": 9, "title": "공지", "posted_at": "2026-09-01T00:00:00Z"}] if announcements is None else announcements
        self.urls: list[str] = []
        self.fail_after = fail_after
        self.extra_next = extra_next
        self.module_items = module_items or {}

    def get_page(self, url: str, deadline: float):
        self.urls.append(url)
        if self.fail_after is not None and len(self.urls) > self.fail_after:
            raise _Killed("process killed")
        if "/assignments" in url:
            rows = self.assignments
        elif "/announcements" in url:
            rows = self.announcements
        elif "/items" in url:
            module_id = int(url.split("/modules/")[1].split("/")[0])
            rows = self.module_items[module_id]
        else:
            rows = self.modules
        nxt = url if self.extra_next and "/modules?" in url else None
        return rows, nxt


class Harness:
    def __init__(self, tmp_path, *, reader: FakeReader | None = None, course_map=None,
                 verified_origin: str = ORIGIN, token: str | None = "tok", semester=SEMESTER) -> None:
        self.state = SQLiteStateStore(tmp_path / "state.sqlite3")
        self.reader = reader or FakeReader()
        self.entered = 0
        self.token = token
        harness = self

        @contextmanager
        def factory():
            harness.entered += 1
            yield harness.reader if harness.token else None

        self.observer = CanvasObserver(
            self.state, origin=ORIGIN, verified_origin=verified_origin,
            course_map={COURSE_ID: KEY} if course_map is None else course_map,
            reader_factory=factory, semester_for=lambda _key: semester,
        )

    def run(self) -> ObservationResult:
        return self.observer.observe_course(COURSE_ID)

    def close(self) -> None:
        self.state.close()


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.close()


def _rows(state, table: str) -> int:
    with state._transaction() as connection:
        return int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def test_complete_observation_records_metadata_and_opens_the_calendar(harness) -> None:
    result = harness.run()
    assert (result.status, result.reason) == ("COMPLETE", "")
    assert harness.state.recording_calendar_complete(KEY)
    rows = harness.state.list_recording_calendar_current(KEY)
    assert [r.status for r in rows] == ["CALENDAR"] * 4 and [r.week for r in rows] == [1, 2, 3, 4]
    kinds = {c["resource_kind"]: c["kind"] for c in harness.state.list_canvas_classifications(COURSE_ID)}
    assert kinds["assignment"] == "ASSIGNMENT_BRIEF" and kinds["announcement"] == "ANNOUNCEMENT"
    recording = [c for c in harness.state.list_canvas_classifications(COURSE_ID) if c["resource_kind"] == "module_item"]
    assert {c["kind"] for c in recording} == {"RECORDING"}
    decision = json.loads(recording[0]["decision_json"])
    assert decision["handling_mode"] == "CANVAS_METADATA_ONLY" and decision["week"] in (1, 2, 3, 4)


def test_metadata_path_creates_no_intake_plan_job_material_or_request(harness) -> None:
    harness.run()
    for table in ("intake_items", "intake_plans", "intake_jobs", "entity_reservations"):
        try:
            assert _rows(harness.state, table) == 0, table
        except Exception as error:  # noqa: BLE001 - a table that does not exist is also zero
            assert "no such table" in str(error)


def test_unmapped_course_does_nothing(tmp_path) -> None:
    h = Harness(tmp_path, course_map={})
    try:
        result = h.run()
        assert (result.status, result.reason) == ("INELIGIBLE", "COURSE_NOT_MAPPED")
        assert h.entered == 0 and h.reader.urls == []
        assert _rows(h.state, "canvas_observations") == 0 and _rows(h.state, "canvas_classifications") == 0
        assert _rows(h.state, "recording_calendar_courses") == 0
    finally:
        h.close()


def test_profile_mismatch_and_missing_credential_make_no_calls(tmp_path) -> None:
    h = Harness(tmp_path, verified_origin="https://other.example.edu")
    try:
        assert h.run().reason == "PROFILE_MISMATCH" and h.entered == 0
    finally:
        h.close()
    h = Harness(tmp_path / "x" if False else tmp_path, token=None)
    try:
        result = h.run()
        assert result.status == "DISABLED" and h.reader.urls == []
    finally:
        h.close()


def test_revision_follows_the_whole_payload_not_only_updated_at(harness) -> None:
    harness.run()
    harness.run()
    latest = harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "assignment", "7")
    assert latest["observation_revision"] == 1  # identical response: no new revision
    harness.reader.assignments = [{"id": 7, "name": "과제 1 (수정)", "updated_at": "2026-09-01T00:00:00Z"}]
    harness.run()
    latest = harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "assignment", "7")
    assert latest["observation_revision"] == 2 and latest["title"] == "과제 1 (수정)"
    harness.reader.assignments = [{"id": 7, "name": "과제 1 (수정)"}]  # updated_at disappears
    harness.run()
    assert harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "assignment", "7")["observation_revision"] == 3


def test_moving_a_recording_to_another_module_creates_a_new_revision(harness) -> None:
    harness.run()
    modules = _modules()
    modules[0]["items"], modules[1]["items"] = modules[1]["items"], modules[0]["items"]
    harness.reader.modules = modules
    harness.run()
    moved = harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "module_item", "1002")
    assert moved["observation_revision"] == 2 and moved["module_week"] == 1


def test_an_incomplete_collection_closes_a_previously_complete_calendar(harness) -> None:
    assert harness.run().status == "COMPLETE" and harness.state.recording_calendar_complete(KEY)
    harness.reader.extra_next = True  # a next page that never ends: the page limit is reached
    result = harness.run()
    assert (result.status, result.reason) == ("INCOMPLETE", "PAGE_LIMIT")
    assert not harness.state.recording_calendar_complete(KEY)
    assert harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "module_item", "1001")["active"] == 1


def test_a_process_death_mid_collection_leaves_the_calendar_closed(harness) -> None:
    assert harness.run().status == "COMPLETE"
    harness.reader.fail_after = 1
    with pytest.raises(_Killed):
        harness.run()
    assert not harness.state.recording_calendar_complete(KEY)


def test_a_connection_failure_before_any_read_still_closes_a_complete_calendar(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        assert h.run().status == "COMPLETE" and h.state.recording_calendar_complete(KEY)

        @contextmanager
        def broken():
            raise RuntimeError("token provider exploded")
            yield  # pragma: no cover

        h.observer._factory = broken
        result = h.run()
        assert (result.status, result.reason) == ("INCOMPLETE", "OBSERVATION_FAILED")
        assert not h.state.recording_calendar_complete(KEY)
    finally:
        h.close()


def test_a_next_link_to_another_course_of_the_same_origin_is_incomplete(harness) -> None:
    from uls.intake.canvas_observer import _next_url

    here = "/api/v1/courses/1/modules?include[]=items&per_page=50"
    for foreign in ("/api/v1/courses/2/modules?include[]=items&per_page=50&page=2",
                    "/api/v1/courses/1/assignments?include[]=items&per_page=50&page=2",
                    "/api/v1/courses/1/modules?include[]=items&per_page=50&page=2&order_by=name"):
        with pytest.raises(_Incomplete):
            _next_url(f'<{foreign}>; rel="next"', here, ORIGIN)
    assert _next_url('</api/v1/courses/1/modules?include[]=items&per_page=50&page=2>; rel="next"', here, ORIGIN)


def test_two_canvas_courses_for_one_course_key_are_ineligible(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        assert h.run().status == "COMPLETE"
        h.observer._map_source[99999] = KEY
        result = h.run()
        assert (result.status, result.reason) == ("INELIGIBLE", "MANY_TO_ONE_MAPPING")
        assert not h.state.recording_calendar_complete(KEY)
    finally:
        h.close()


def test_module_items_missing_from_the_listing_are_fetched_and_verified(harness) -> None:
    modules = _modules()
    modules[0]["items"] = []
    harness.reader.modules = modules
    harness.reader.module_items = {101: [{"id": 1001, "title": "2026-09-02", "type": "ExternalTool"}]}
    assert harness.run().status == "COMPLETE"
    harness.reader.module_items = {101: []}
    result = harness.run()
    assert (result.status, result.reason) == ("INCOMPLETE", "MODULE_ITEMS_INCOMPLETE")


def test_a_recording_without_a_week_keeps_the_calendar_closed(harness) -> None:
    harness.reader.modules = _modules(bare_week=True)
    result = harness.run()
    assert (result.status, result.reason) == ("INCOMPLETE", "RECORDING_WITHOUT_WEEK")
    assert not harness.state.recording_calendar_complete(KEY)


def test_a_recording_that_disappears_is_deactivated_by_a_complete_collection(harness) -> None:
    harness.run()
    harness.reader.modules = _modules(skip_week=4)
    assert harness.run().status == "COMPLETE"
    assert harness.state.latest_canvas_observation(ORIGIN, COURSE_ID, "module_item", "1004")["active"] == 0
    assert [r.week for r in harness.state.list_recording_calendar_current(KEY)] == [1, 2, 3]


def test_malformed_responses_are_incomplete_not_guessed(harness) -> None:
    harness.reader.assignments = [{"id": 7, "name": ""}]
    result = harness.run()
    assert (result.status, result.reason) == ("INCOMPLETE", "MALFORMED_TITLE")
    harness.reader.assignments = [{"id": 7, "name": "a"}, {"id": 7, "name": "b"}]
    assert harness.run().reason == "DUPLICATE_ASSIGNMENT"
    assert not harness.state.recording_calendar_complete(KEY)


def test_the_incomplete_error_type_carries_only_a_fixed_reason() -> None:
    assert _Incomplete("X").reason == "X"


@pytest.mark.parametrize("path", [
    "/api/v1/courses/1/assignments?order_by=name&per_page=50",
    "/api/v1/courses/1/modules?include[]=items&per_page=50&page=2",
    "/api/v1/courses/1/modules/9/items?per_page=50",
    "/api/v1/announcements?context_codes[]=course_1&per_page=50",
])
def test_observation_allowlist_accepts_only_the_metadata_listings(path) -> None:
    origin = validate_canvas_origin(ORIGIN)
    assert _observation_target(origin, path).startswith("/api/v1/")


@pytest.mark.parametrize("path", [
    "/api/v1/courses/1/files",
    "/api/v1/courses/1/users",
    "/api/v1/courses/1/assignments?per_page=500",
    "/api/v1/courses/1/assignments?access_token=abc",
    "/api/v1/users/self",
    "/files/1/download",
    "https://evil.example.com/api/v1/courses/1/assignments",
])
def test_observation_allowlist_rejects_everything_else(path) -> None:
    origin = validate_canvas_origin(ORIGIN)
    with pytest.raises(CanvasCheckError):
        _observation_target(origin, path)


def test_origin_object_is_the_pinned_one() -> None:
    assert isinstance(validate_canvas_origin(ORIGIN), CanvasOrigin)


class _Transport:
    def __init__(self, pages: dict[str, tuple[bytes, dict[str, str]]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def request(self, method, target, headers, timeout):
        from uls.settings.canvas_checks import CanvasResponse

        self.requests.append((method, target, dict(headers)))
        body, response_headers = self.pages[target]
        return CanvasResponse(200, response_headers, body)

    def close(self) -> None:
        return None


def test_the_production_reader_follows_a_same_origin_next_link_with_get_only() -> None:
    from uls.intake.canvas_observer import ConnectionReader
    from uls.settings.canvas_checks import open_canvas_connection

    first = "/api/v1/courses/1/assignments?per_page=50"
    second = "/api/v1/courses/1/assignments?per_page=50&page=2"
    transport = _Transport({
        first: (b'[{"id":1}]', {"Link": f'<{ORIGIN}{second}>; rel="next"'}),
        second: (b'[{"id":2}]', {}),
    })
    with open_canvas_connection(ORIGIN, resolver=lambda _h, _p: ["93.184.216.34"],
                                connector=lambda *_a: transport) as connection:
        reader = ConnectionReader(connection, "secret-token")
        page, nxt = reader.get_page(first, 1e12)
        assert page == [{"id": 1}] and nxt == f"{ORIGIN}{second}"
        page, nxt = reader.get_page(nxt, 1e12)
        assert page == [{"id": 2}] and nxt is None
        with pytest.raises(CanvasCheckError):
            reader.get_page("/api/v1/courses/1/files", 1e12)
    assert [r[0] for r in transport.requests] == ["GET", "GET"]
    assert all(r[2]["Authorization"] == "Bearer secret-token" for r in transport.requests)
    assert "secret-token" not in repr(connection)


def test_a_next_link_to_another_origin_is_refused() -> None:
    from uls.intake.canvas_observer import ConnectionReader
    from uls.settings.canvas_checks import open_canvas_connection

    first = "/api/v1/courses/1/assignments?per_page=50"
    transport = _Transport({first: (b"[]", {"Link": '<https://evil.example.com/api/v1/courses/1/assignments>; rel="next"'})})
    with open_canvas_connection(ORIGIN, resolver=lambda _h, _p: ["93.184.216.34"],
                                connector=lambda *_a: transport) as connection:
        reader = ConnectionReader(connection, "t0k")
        with pytest.raises(_Incomplete):
            reader.get_page(first, 1e12)
    assert len(transport.requests) == 1


def test_a_moved_mapping_drops_the_old_projection_and_reopens_only_after_a_complete_observation(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        assert h.run().status == "COMPLETE"
        h.observer._map_source.clear()
        h.observer._map_source[99999] = KEY
        result = h.observer.observe_course(99999)
        assert result.status == "COMPLETE"
        assert h.state.recording_calendar_course(KEY)["canvas_course_id"] == 99999
    finally:
        h.close()


def test_the_live_profile_provider_is_read_on_every_check(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        current = {"origin": ORIGIN}
        h.observer._profile_provider = lambda: current["origin"]
        assert h.observer.profile_matches()
        current["origin"] = "https://other.example.edu"
        assert not h.observer.profile_matches()
        current["origin"] = None
        assert not h.observer.profile_matches()
    finally:
        h.close()


def test_projection_never_mixes_observations_of_another_origin(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        other = "https://other.example.edu"
        h.state.record_canvas_observation(
            origin=other, canvas_course_id=COURSE_ID, resource_kind="module_item", resource_id="9999",
            observation_revision=1, title="2026-10-01", module_name="9주차", module_week=9,
            item_type="ExternalTool", collection_complete=True)
        assert h.run().status == "COMPLETE"
        weeks = [r.week for r in h.state.list_recording_calendar_current(KEY)]
        assert weeks == [1, 2, 3, 4]
    finally:
        h.close()


def test_the_proof_follows_origin_principal_and_mapping(tmp_path) -> None:
    h = Harness(tmp_path)
    try:
        principal = {"value": "user-1"}
        h.observer._principal_provider = lambda: principal["value"]
        first = h.observer.current_proof(KEY)
        assert first and h.run().status == "COMPLETE"
        assert h.state.get_classification_cursor(f"calendar_proof:{KEY}") == first
        principal["value"] = "user-2"
        assert h.observer.current_proof(KEY) not in (None, first)
        principal["value"] = None
        assert h.observer.current_proof(KEY) is None
        h.observer._principal_provider = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert h.observer.current_proof(KEY) is None
        h.observer._map_source[99999] = KEY                   # the live map is re-read: now ambiguous
        assert h.observer.current_proof(KEY) is None
    finally:
        h.close()
