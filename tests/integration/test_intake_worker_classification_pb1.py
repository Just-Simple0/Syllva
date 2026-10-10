"""P-B1: S0/S1 classification in the sync path, S3 suggestion fields, v2 projections, terminal receipts."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_intake_worker_classification_profile import _rewire
from tests.integration.test_intake_worker_preview import (
    COURSE_KEYS,
    _assign_request,
    _details_request,
    _system,
)

from uls.intake.worker import RequestTerminalError

pytestmark = pytest.mark.integration

TRANSCRIPT_NAME = "2026.09.10_Synthetic Course 1_2주차.md"
SUGGESTED = ("Suggested Course", "Suggested Kind", "Suggested Date", "Suggested Week", "Suggestion Source",
             "Suggestion Note")
USER_FIELDS = ("Course", "Kind", "Actual Date", "Session", "Session Mode", "Session No", "Material Role")


def _enable(system, *, profile: str | None = "legacy5-cls"):
    system["config"].intake.classification.enabled = True
    registry = system["config"].google_drive.semester_registries[0]
    registry.start_date, registry.end_date = "2026-09-01", "2026-12-20"
    return _rewire(system, profile) if profile else system["worker"]


def test_transcript_upload_is_classified_locally_and_suggested_on_the_draft(tmp_path: Path) -> None:
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _enable(system)
        state, notion = system["state"], system["notion"]
        result = worker.run_once()
        assert result["status"] == "ok" and result["discovered"] == 1
        item = state.get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert (item.classified_kind, item.classification_source) == ("TRANSCRIPT", "rule:P3:transcript_filename")
        assert (item.origin, item.inferred_course_key, item.inferred_week, item.inferred_date) == (
            "USER_AUTHORED_TRANSCRIPT", COURSE_KEYS[1], 2, "2026-09-10")
        # Decided and byte-proven, but stage A blocks on the calendar: an S3 (HUMAN) decision.
        assert item.calendar_match == "NO_CALENDAR" and item.classification_state == "HUMAN"
        record = state.get_classification_record(item.classification_record_id)
        assert record is not None and record.kind == "TRANSCRIPT" and record.byte_sha256 and record.byte_md5
        assert record.course_key == COURSE_KEYS[1] and record.semester_range_basis_json is not None
        assert state.get_intake_suggestion(item.intake_id)["written_to_notion"] == 1
        # The draft carries only SYSTEM_DERIVED suggestions; USER fields stay blank (plan §3.3).
        assign = _assign_request(notion)
        assert assign["Suggested Course"] == COURSE_KEYS[1] and assign["Suggested Kind"] == "TRANSCRIPT"
        assert assign["Suggested Date"] == "2026-09-10" and assign["Suggested Week"] == 2
        assert assign["Suggestion Source"] == "rule:P3:transcript_filename"
        assert "NO_CALENDAR" in assign["Suggestion Note"] and "AUTO_BLOCK_CALENDAR" in assign["Suggestion Note"]
        assert all(not assign.get(field) for field in USER_FIELDS)
        assert assign["Submitted"] is False and assign["Cancelled"] is False
        # File Intake carries the classification projection.
        intake_page = notion.data_sources["synthetic-intake"][0]
        assert intake_page["AI Kind"] == "TRANSCRIPT" and intake_page["Origin"] == "USER_AUTHORED_TRANSCRIPT"
        assert intake_page["Classification Source"] == "rule:P3:transcript_filename"
        assert intake_page["Classification Record"] == item.classification_record_id
        assert notion.data_sources["synthetic-sessions"] == []  # no automation in P-B1
        assert result["readiness"]["classification"]["status"] == "READY"
        # A second tick is idempotent: same record, no second draft, no suggestion rewrite.
        worker.run_once()
        assert len(notion.data_sources["synthetic-requests"]) == 1
        assert state.get_intake_item(item.intake_id).classification_record_id == item.classification_record_id
        # After the human assigns the course, the FILE_DETAILS draft repeats the suggestions (§3.3 O2).
        assign.update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        assert details["Suggested Kind"] == "TRANSCRIPT" and details["Suggested Week"] == 2
        assert all(not details.get(field) for field in USER_FIELDS)


def test_undecided_upload_gets_only_the_fixed_note(tmp_path: Path) -> None:
    with _system(tmp_path) as system:  # lecture.md: no single rule
        worker = _enable(system)
        worker.run_once()
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert item.classified_kind is None and item.classification_state == "HUMAN"
        assert item.classification_record_id is None and item.origin == "UNKNOWN"
        assign = _assign_request(system["notion"])
        assert assign["Suggestion Note"] == "NO_SINGLE_RULE;CLASSIFIER_DISABLED;AUTO_BLOCK_NOT_DECIDED"
        assert all(assign.get(field) is None for field in SUGGESTED if field != "Suggestion Note")


def test_disabled_classification_changes_nothing(tmp_path: Path) -> None:
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _rewire(system, "legacy5-cls")  # v2 profile but classification disabled
        result = worker.run_once()
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert item.classification_state == "NONE" and item.classified_kind is None
        assert system["state"].get_intake_suggestion(item.intake_id) is None
        assign = _assign_request(system["notion"])
        assert all(field not in assign for field in SUGGESTED)
        assert "AI Kind" not in system["notion"].data_sources["synthetic-intake"][0]
        assert result["readiness"]["classification"]["status"] == "DISABLED"


def test_legacy_profile_keeps_suggestions_local_only(tmp_path: Path) -> None:
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _enable(system, profile=None)  # enabled, but the Notion profile is legacy5
        result = worker.run_once()
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert item.classified_kind == "TRANSCRIPT"
        suggestion = system["state"].get_intake_suggestion(item.intake_id)
        assert suggestion["suggested_kind"] == "TRANSCRIPT" and suggestion["written_to_notion"] == 0
        assign = _assign_request(system["notion"])
        assert all(field not in assign for field in SUGGESTED)
        assert "AI Kind" not in system["notion"].data_sources["synthetic-intake"][0]
        assert result["readiness"]["classification"] == {
            **result["readiness"]["classification"], "status": "DISABLED",
            "reason": "no verified v2 Notion schema profile"}


def test_terminal_receipts_are_refused_without_any_write(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker = _rewire(system, "legacy5-cls")
        notion, state = system["notion"], system["state"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        applied = next(r for r in state.list_request_receipts() if r.state == "Applied")
        events_before = len(notion.events)
        with pytest.raises(RequestTerminalError):
            worker.claim_request(applied.request_key)
        assert len(notion.events) == events_before
        # An AutoResolved receipt is terminal for the tick queue as well.
        details = _details_request(notion)
        details.update({"Course": ["synthetic-course-page-0"], "Kind": "TRANSCRIPT",
                        "Actual Date": {"start": "2026-09-01", "end": None}, "Session Mode": "NEW", "Submitted": True})
        draft = next(r for r in state.list_request_receipts() if r.request_type == "FILE_DETAILS")
        state.update_request_receipt(draft.request_key, state="AutoResolved")
        assert worker._submitted_request_keys() == []
        with pytest.raises(RequestTerminalError):
            worker.claim_request(draft.request_key)
        assert notion.data_sources["synthetic-sessions"] == [] and state.list_jobs() == []


# --- P-B1 review r1 regressions -------------------------------------------------------

def _tick_item(system):
    system["worker"].run_once()
    return system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])


def test_kind_without_byte_proof_stays_a_local_suggestion(tmp_path: Path) -> None:
    from dataclasses import replace

    # Too large by declared size: no download, no record, HUMAN fallback with the fixed note (R1/R2).
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _enable(system)
        system["config"].intake.classification.max_source_bytes = 8
        item = _tick_item(system)
        assert item.classification_record_id is None and item.classified_kind is None
        assert item.classification_state == "HUMAN"
        suggestion = system["state"].get_intake_suggestion(item.intake_id)
        assert suggestion["suggested_kind"] == "TRANSCRIPT" and "SOURCE_TOO_LARGE" in suggestion["suggestion_note"]
        assert system["state"].latest_classification_record(item.intake_id) is None
        assert not [e for e in system["drive"].events if e[0] == "download"] if hasattr(system["drive"], "events") else True
        del worker
    # Size missing or misreported: the read cannot be proven complete, so no record either (R2).
    for misreport in (None, 3):
        with _system(tmp_path / f"m{misreport}", name=TRANSCRIPT_NAME) as system:
            _enable(system)
            drive = system["drive"]
            drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], size=misreport)
            item = _tick_item(system)
            assert item.classification_record_id is None and item.classification_state == "HUMAN"
            assert "SOURCE_UNAVAILABLE" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_origin_is_independent_of_kind_and_follows_verified_bindings(tmp_path: Path) -> None:
    import hashlib

    # text/plain transcript name: S1 does not decide, but S0 still says USER_AUTHORED_TRANSCRIPT (R3).
    with _system(tmp_path, name=TRANSCRIPT_NAME, mime_type="text/plain") as system:
        _enable(system)
        item = _tick_item(system)
        assert item.classified_kind is None and item.origin == "USER_AUTHORED_TRANSCRIPT"
    # A stored Canvas binding proven by the bytes read promotes the origin to PROFESSOR_SOURCE ...
    raw = b"# [00:10] bound transcript\n"
    with _system(tmp_path / "bound", name=TRANSCRIPT_NAME, raw=raw) as system:
        _enable(system)
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="announcement",
            resource_id="a1", observation_revision=1, attachment_id="att-1", attachment_filename=TRANSCRIPT_NAME,
            attachment_size=len(raw), byte_sha256=hashlib.sha256(raw).hexdigest())
        item = _tick_item(system)
        assert item.origin == "PROFESSOR_SOURCE" and item.classified_kind == "TRANSCRIPT"
    # ... while a binding whose bytes differ proves nothing: the title alone never binds.
    with _system(tmp_path / "unbound", name=TRANSCRIPT_NAME, raw=raw) as system:
        _enable(system)
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="announcement",
            resource_id="a1", observation_revision=1, attachment_id="att-1", attachment_filename=TRANSCRIPT_NAME,
            attachment_size=len(raw), byte_sha256="f" * 64)
        item = _tick_item(system)
        assert item.origin == "USER_AUTHORED_TRANSCRIPT"


def test_code_upload_is_classified_before_any_request_and_p0_is_terminal(tmp_path: Path) -> None:
    # A .c upload reaches P4 (observed Kind UNKNOWN is not a reason to skip S1) and only then gets its draft (R4).
    with _system(tmp_path, name="tcp_server.c", mime_type="text/x-csrc", raw=b"#include <stdio.h>\n") as system:
        _enable(system)
        item = _tick_item(system)
        assert item.classified_kind == "PROVIDED_CODE" and item.classification_state == "HUMAN"
        assert "AUTO_BLOCK_ORIGIN_UNKNOWN" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
        assert len(system["notion"].data_sources["synthetic-requests"]) == 1
        assert _assign_request(system["notion"])["Suggested Kind"] == "PROVIDED_CODE"
    # A P0 title is terminal: UNSUPPORTED status, fixed code, no download, no draft, no Material (R5).
    with _system(tmp_path / "p0", name="Windows용 VMware Workstation 설치 프로그램.pdf", mime_type="application/pdf") as system:
        _enable(system)
        downloads_before = sum(1 for e in getattr(system["drive"], "events", []) if e[0] == "download")
        item = _tick_item(system)
        assert item.status == "UNSUPPORTED" and item.last_error_code == "UNSUPPORTED_FORMAT"
        assert item.classified_kind == "UNSUPPORTED" and item.classification_record_id is None
        assert system["notion"].data_sources["synthetic-requests"] == []
        assert system["notion"].data_sources["synthetic-materials"] == []
        assert sum(1 for e in getattr(system["drive"], "events", []) if e[0] == "download") == downloads_before
        page = system["notion"].data_sources["synthetic-intake"][0]
        assert page["Status"] == "UNSUPPORTED" and page["AI Kind"] == "UNSUPPORTED"


def test_disabling_classification_stops_projecting_stored_classification(tmp_path: Path) -> None:
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _enable(system)
        item = _tick_item(system)
        assert system["notion"].data_sources["synthetic-intake"][0]["AI Kind"] == "TRANSCRIPT"
        # Feature switched off while the stored columns remain: a re-projection writes none of them (R6).
        system["config"].intake.classification.enabled = False
        system["state"].update_intake_item(item.intake_id, last_error="forced reprojection")
        events_before = len(system["notion"].events)
        worker.run_once()
        new_updates = [e for e in system["notion"].events[events_before:] if e[0] == "update" and e[1] == "synthetic-intake"]
        assert new_updates and all(
            not ({"AI Kind", "Origin", "Classification Source", "Classification Record"} & set(e[3])) for e in new_updates
        )


def test_incomplete_recording_collection_keeps_the_calendar_ambiguous(tmp_path: Path) -> None:
    from datetime import date

    from uls.intake.classification import SemesterRange, build_calendar

    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        _enable(system)
        course = COURSE_KEYS[1]
        semester = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
        # A collection was started for the course but is incomplete and holds no rows yet (R7).
        system["state"].replace_recording_calendar_current(
            course, 67535, build_calendar(course, [], semester=semester, collection_complete=False),
            collection_complete=False)
        item = _tick_item(system)
        assert item.calendar_match == "AMBIGUOUS"
        assert "CALENDAR_AMBIGUOUS" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    with _system(tmp_path / "none", name=TRANSCRIPT_NAME) as system:
        _enable(system)
        item = _tick_item(system)
        assert item.calendar_match == "NO_CALENDAR"


def test_multi_intake_file_details_is_rejected_before_the_matrix_gate(tmp_path: Path) -> None:
    # O1: validate_request_input refuses FILE_DETAILS over two intakes before any plan or matrix probe.
    with _system(tmp_path, duplicate=True) as system:
        worker = _rewire(system, "legacy5-cls")
        notion, state = system["notion"], system["state"]
        worker.run_once()
        requests_ = [r for r in notion.data_sources["synthetic-requests"] if r["Request Type"] == "ASSIGN_COURSE"]
        assert len(requests_) == 2
        for page in requests_:
            page.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = [r for r in notion.data_sources["synthetic-requests"] if r["Request Type"] == "FILE_DETAILS"]
        assert len(details) == 2
        intake_pages = [p["id"] for p in notion.data_sources["synthetic-intake"]]
        details[0].update({"Intake Items": intake_pages, "Course": ["synthetic-course-page-0"], "Kind": "LAB_MATERIAL",
                           "Material Role": "Reference", "Submitted": True})
        worker.run_once()
        assert state.list_jobs() == [] and notion.data_sources["synthetic-materials"] == []
        # The widened relation no longer matches the receipt's intake binding: the claim is
        # refused as a reconcile before validation, plan, matrix probe or any Material write.
        assert details[0]["Request Status"] == "Reconcile Required"
        assert all(i.plan_revision is None for i in state.list_intake_items(limit=100))


def test_suggestion_flags_survive_ticks_and_restart(tmp_path: Path) -> None:
    from uls.state.sqlite import SQLiteStateStore

    # O2: written_to_notion stays set across ticks and the follow-up draft; the first draft is never rewritten.
    with _system(tmp_path, name=TRANSCRIPT_NAME) as system:
        worker = _enable(system)
        item = _tick_item(system)
        first = dict(_assign_request(system["notion"]))
        worker.run_once()
        assert system["state"].get_intake_suggestion(item.intake_id)["written_to_notion"] == 1
        assert dict(_assign_request(system["notion"])) == first
        assert not [e for e in system["notion"].events if e[0] == "update" and e[1] == "synthetic-requests"]
        _assign_request(system["notion"]).update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        worker.run_once()
        assert system["state"].get_intake_suggestion(item.intake_id)["written_to_notion"] == 1
    with SQLiteStateStore(tmp_path / "state.sqlite3") as reopened:
        assert reopened.get_intake_suggestion(item.intake_id)["written_to_notion"] == 1


# --- P-B1 review r2 regressions -------------------------------------------------------

def test_download_is_bounded_and_must_match_the_declared_size(tmp_path: Path) -> None:
    from dataclasses import replace

    body = b"# [00:10] transcript line\n" * 4
    # Declared size under the limit but the body is larger: the bounded transfer aborts
    # before the whole body is accepted; nothing is proven, no record (r2 R2).
    with _system(tmp_path / "oversized", name=TRANSCRIPT_NAME, raw=body) as system:
        _enable(system)
        system["config"].intake.classification.max_source_bytes = 64
        drive = system["drive"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], size=32)
        item = _tick_item(system)
        assert item.classification_record_id is None and item.classification_state == "HUMAN"
        assert "SOURCE_UNAVAILABLE" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    # The body is shorter than declared (partial receive): not complete, no record.
    with _system(tmp_path / "short", name=TRANSCRIPT_NAME, raw=body) as system:
        _enable(system)
        drive = system["drive"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], size=len(body) + 10)
        item = _tick_item(system)
        assert item.classification_record_id is None and item.classification_state == "HUMAN"
    # The source changed between observation and download (the metadata readback right
    # before the transfer carries another checksum): refused.
    with _system(tmp_path / "changed", name=TRANSCRIPT_NAME, raw=body) as system:
        _enable(system)
        drive = system["drive"]
        original_read = drive.read_metadata
        drive.read_metadata = lambda file_id: replace(original_read(file_id), md5_checksum="0" * 32)
        item = _tick_item(system)
        assert item.classification_record_id is None and item.classification_state == "HUMAN"
        assert "SOURCE_UNAVAILABLE" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    # Within the bound and matching the declared size: proven, record created.
    with _system(tmp_path / "ok", name=TRANSCRIPT_NAME, raw=body) as system:
        _enable(system)
        system["config"].intake.classification.max_source_bytes = len(body)
        item = _tick_item(system)
        assert item.classification_record_id is not None and item.classified_kind == "TRANSCRIPT"


def test_verified_binding_settles_origin_and_p4_even_when_s1_is_undecided(tmp_path: Path) -> None:
    import hashlib

    raw = b"# lecture notes without a transcript name\n"
    # Undecided by name, but a byte-proven Canvas binding: PROFESSOR_SOURCE is kept (r2 R3).
    with _system(tmp_path / "lecture", name="lecture.md", raw=raw) as system:
        _enable(system)
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="announcement",
            resource_id="a1", observation_revision=1, attachment_id="att-1", attachment_filename="lecture.md",
            attachment_size=len(raw), byte_sha256=hashlib.sha256(raw).hexdigest())
        item = _tick_item(system)
        assert item.classified_kind is None and item.origin == "PROFESSOR_SOURCE"
        assert item.classification_state == "HUMAN"
    csv = b"name,score\nA,1\n"
    # A proven Assignment attachment carries its resource kind into P4: ASSIGNMENT_RESOURCE (r2 #3) ...
    with _system(tmp_path / "assignment", name="mbti.csv", mime_type="text/csv", raw=csv) as system:
        _enable(system)
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="assignment",
            resource_id="as-1", observation_revision=1, attachment_id="att-9", attachment_filename="mbti.csv",
            attachment_size=len(csv), byte_sha256=hashlib.sha256(csv).hexdigest())
        item = _tick_item(system)
        assert (item.classified_kind, item.origin) == ("ASSIGNMENT_RESOURCE", "PROFESSOR_SOURCE")
        assert item.classification_source == "rule:P4:tabular_assignment_attachment"
    # ... an unverified binding passes no signal, and a plain upload CSV stays undecided.
    with _system(tmp_path / "unverified", name="mbti.csv", mime_type="text/csv", raw=csv) as system:
        _enable(system)
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="assignment",
            resource_id="as-1", observation_revision=1, attachment_id="att-9", attachment_filename="mbti.csv",
            attachment_size=len(csv), byte_sha256="e" * 64)
        item = _tick_item(system)
        assert item.classified_kind is None and item.origin == "UNKNOWN"
    with _system(tmp_path / "plain", name="mbti.csv", mime_type="text/csv", raw=csv) as system:
        _enable(system)
        item = _tick_item(system)
        assert item.classified_kind is None and item.origin == "UNKNOWN"
