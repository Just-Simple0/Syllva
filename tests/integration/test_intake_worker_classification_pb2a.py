"""P-B2a: AUTO_PENDING plans (stage A), blank-draft auto-close (§3.5) with recovery, barriers."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _enable
from tests.integration.test_intake_worker_classification_profile import _rewire
from tests.integration.test_intake_worker_preview import COURSE_KEYS, _assign_request, _system

from uls.intake.classification import RecordingEntry, SemesterRange, build_calendar
from uls.intake.worker import IntakeReconcileRequired

pytestmark = pytest.mark.integration

COURSE = COURSE_KEYS[1]  # "Synthetic Course 1"
SEMESTER = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
MATCHED_NAME = "2026.09.10_Synthetic Course 1_2주차.md"
RAW = b"# [00:10] matched transcript\n"


def _complete_calendar(state) -> None:
    """Three weekly recordings observed from a complete collection: week 2 is 2026-09-10."""

    entries = []
    for week in (1, 2, 3):
        day = date(2026, 9, 3) + timedelta(days=7 * (week - 1))
        state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                        resource_id=f"r{week}", observation_revision=1, title=day.isoformat(),
                                        module_name=f"{week}주차", module_week=week, item_type="ExternalTool",
                                        collection_complete=True)
        entries.append(RecordingEntry(67535, f"r{week}", 1, week, day))
    state.replace_recording_calendar_current(
        COURSE, 67535, build_calendar(COURSE, entries, semester=SEMESTER, collection_complete=True),
        collection_complete=True)


def _auto_plans(state):
    return state.list_intake_plans(plan_authority="AUTO_CLASSIFICATION")


def _item(system):
    return system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])


def test_matched_transcript_gets_an_auto_pending_plan_and_no_draft(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        result = worker.run_once()
        item = _item(system)
        assert item.classification_state == "CLASSIFIED" and item.calendar_match == "MATCHED"
        plans = _auto_plans(system["state"])
        assert len(plans) == 1 and plans[0]["status"] == "AUTO_PENDING"
        assert plans[0]["classification_revision_hash"] == system["state"].get_classification_record(
            item.classification_record_id).classification_revision_hash
        assert system["notion"].data_sources["synthetic-requests"] == []   # no HUMAN draft
        assert system["notion"].data_sources["synthetic-sessions"] == []   # no execution in P-B2a
        assert system["state"].list_jobs() == [] and item.plan_revision is None
        assert result["readiness"]["classification"]["auto_pending_plans"] == 1
        assert result["readiness"]["classification"]["auto_enabled"] is True
        # Idempotent across ticks: one plan, still no draft.
        worker.run_once()
        assert len(_auto_plans(system["state"])) == 1
        assert system["notion"].data_sources["synthetic-requests"] == []


def test_stage_a_blockers_keep_the_human_path(tmp_path: Path) -> None:
    # Same bytes under another provider file id: DUPLICATE_CONTENT, no AUTO plan (r6 R2 gate).
    with _system(tmp_path / "dup", name=MATCHED_NAME, raw=RAW, duplicate=True) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        original = system["state"].get_intake_item_by_provider_file("google_drive", system["source_ids"][0])
        copy = system["state"].get_intake_item_by_provider_file("google_drive", system["source_ids"][1])
        assert original.classified_kind == "TRANSCRIPT" and original.classification_state == "HUMAN"
        assert "DUPLICATE_CONTENT" in system["state"].get_intake_suggestion(original.intake_id)["suggestion_note"]
        assert copy.classification_state == "HUMAN"  # "copy-…" never matches the transcript pattern
        assert _auto_plans(system["state"]) == []
    # Legacy profile: classification runs but AUTO is unavailable (AUTO_NOT_ENABLED), draft created.
    with _system(tmp_path / "legacy", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system, profile=None)
        _complete_calendar(system["state"])
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "AUTO_NOT_ENABLED" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
        assert len(system["notion"].data_sources["synthetic-requests"]) == 1


def test_material_from_a_proven_binding_in_a_course_folder_is_auto_eligible(tmp_path: Path) -> None:
    csv = b"name,score\nA,1\n"
    with _system(tmp_path, name="mbti.csv", mime_type="text/csv", raw=csv) as system:
        worker = _enable(system)
        # Move the file into the registered course upload folder so the course is explicit.
        drive = system["drive"]
        from dataclasses import replace
        meta = drive.files[system["source_id"]]
        drive.files[system["source_id"]] = replace(meta, parents=("synthetic-course-upload-1",))
        from tests.integration.test_intake_worker_preview import _privacy

        from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata
        drive.files["synthetic-course-upload-1"] = DriveMetadata(
            file_id="synthetic-course-upload-1", name="upload", mime_type=DRIVE_FOLDER_MIME,
            parents=("synthetic-course-1",), modified_time="2026-09-13T10:00:00Z", size=0, md5_checksum=None,
            **_privacy())
        registry = system["config"].google_drive.semester_registries[0]
        registry.optional_course_upload_folder_ids[COURSE] = "synthetic-course-upload-1"
        # The worker resolves its workspaces at construction: rebuild it on the new layout.
        from uls.config.credentials import ResolvedCredentials
        from uls.intake.identity import provider_binding_id
        from uls.runtime import build_intake_worker
        system["worker"] = build_intake_worker(
            system["config"], ResolvedCredentials({}), state=system["state"], drive=drive, notion=system["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2")
        worker = _rewire(system, "legacy5-cls")
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="assignment",
            resource_id="as-1", observation_revision=1, attachment_id="att-9", attachment_filename="mbti.csv",
            attachment_size=len(csv), byte_sha256=hashlib.sha256(csv).hexdigest())
        worker.run_once()
        item = _item(system)
        assert (item.classified_kind, item.origin, item.inferred_course_key) == ("ASSIGNMENT_RESOURCE", "PROFESSOR_SOURCE", COURSE)
        plans = _auto_plans(system["state"])
        assert len(plans) == 1 and plans[0]["status"] == "AUTO_PENDING"
        assert system["notion"].data_sources["synthetic-materials"] == []


def _pre_v2_draft(tmp_path: Path, name: str = MATCHED_NAME):
    """A workspace where the legacy (disabled) flow already created an untouched ASSIGN_COURSE Draft."""

    system_cm = _system(tmp_path, name=name, raw=RAW)
    system = system_cm.__enter__()
    worker = _rewire(system, "legacy5-cls")
    worker.run_once()
    draft = _assign_request(system["notion"])
    assert draft["Request Status"] == "Draft" and draft["Submitted"] is False
    return system_cm, system


def test_blank_pre_v2_draft_is_auto_resolved_atomically(tmp_path: Path) -> None:
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        state, notion = system["state"], system["notion"]
        events_before = len(notion.events)
        worker.run_once()
        item = _item(system)
        draft = _assign_request(notion)
        receipt = next(r for r in state.list_request_receipts() if r.request_type == "ASSIGN_COURSE")
        assert draft["Request Status"] == "Auto Resolved"
        assert draft["Result Reference"] == item.classification_record_id
        assert all(not draft.get(f) for f in ("Course", "Kind", "Actual Date", "Session Mode"))
        assert draft["Submitted"] is False and draft["Cancelled"] is False
        assert receipt.state == "AutoResolved"
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "DONE" and intent.terminal_snapshot_hash and intent.pre_close_snapshot_hash
        assert intent.terminal_snapshot_hash != intent.pre_close_snapshot_hash  # status changed between them
        assert len(_auto_plans(state)) == 1 and _auto_plans(state)[0]["status"] == "AUTO_PENDING"
        writes = [e for e in notion.events[events_before:] if e[0] == "update" and e[1] == "synthetic-requests"]
        assert len(writes) == 1 and set(writes[0][3]) == {"Request Status", "Result Reference"}
        # Terminal: never re-claimed, idempotent on the next tick, no second write.
        assert worker._submitted_request_keys() == []
        worker.run_once()
        assert len([e for e in notion.events[events_before:] if e[0] == "update" and e[1] == "synthetic-requests"]) == 1
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
    finally:
        system_cm.__exit__(None, None, None)


def test_human_touched_draft_supersedes_the_auto_plan(tmp_path: Path) -> None:
    # (a) a USER field set before the first classified tick
    system_cm, system = _pre_v2_draft(tmp_path / "touched")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"])["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        item = _item(system)
        draft = _assign_request(system["notion"])
        assert draft["Request Status"] == "Draft" and draft.get("Result Reference") is None
        assert item.classification_state == "HUMAN"
        plans = _auto_plans(system["state"])
        assert len(plans) == 1 and plans[0]["status"] == "SUPERSEDED"
        assert system["state"].get_auto_resolve_intent(
            next(r for r in system["state"].list_request_receipts()).request_key) is None
    finally:
        system_cm.__exit__(None, None, None)
    # (b) Submitted=True: the human path wins and the normal claim still works.
    system_cm, system = _pre_v2_draft(tmp_path / "submitted")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"]).update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        worker.run_once()
        assert _auto_plans(system["state"])[0]["status"] == "SUPERSEDED"
        receipts = {r.request_type: r.state for r in system["state"].list_request_receipts()}
        assert receipts["ASSIGN_COURSE"] == "Applied" and receipts["FILE_DETAILS"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


def test_pending_intent_recovery_paths(tmp_path: Path) -> None:
    from uls.adapters.notion.intake import InMemoryNotionWorker

    # Simulate a crash between the Notion write and the local commit by making the
    # readback vanish: the intent stays PENDING, the key is barred, and the item is
    # marked RECONCILE_REQUIRED (iv).  Then restore the page and let recovery settle (i).
    system_cm, system = _pre_v2_draft(tmp_path / "recover")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        state, notion = system["state"], system["notion"]
        original_read = notion.read_record
        vanished = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if vanished["on"] and data_source_id == "synthetic-requests":
                return None
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        original_update = notion.update_record

        def update_record(data_source_id: str, page_id: str, properties):
            row = original_update(data_source_id, page_id, properties)
            if data_source_id == "synthetic-requests" and properties.get("Request Status", {}).get("status", {}).get("name") == "Auto Resolved":
                vanished["on"] = True
            return row
        notion.update_record = update_record  # type: ignore[method-assign]
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "PENDING" and receipt.state == "Draft"
        assert _item(system).status == "RECONCILE_REQUIRED"
        # Barrier while unresolved: the tick queue skips it and a direct claim is refused.
        _assign_request(notion)["Submitted"] = True
        assert worker._submitted_request_keys() == []
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(receipt.request_key)
        _assign_request(notion)["Submitted"] = False
        # The page comes back untouched and already Auto Resolved: (i) DONE + receipt flip.
        vanished["on"] = False
        worker.run_once()
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        assert state.get_request_receipt(receipt.request_key).state == "AutoResolved"
    finally:
        system_cm.__exit__(None, None, None)
    # (iii): the write landed but the human edited the page meanwhile → rollback to Draft.
    system_cm, system = _pre_v2_draft(tmp_path / "rollback")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        state, notion = system["state"], system["notion"]
        original_update = notion.update_record

        def update_record(data_source_id: str, page_id: str, properties):
            row = original_update(data_source_id, page_id, properties)
            if data_source_id == "synthetic-requests" and "Request Status" in properties:
                status = properties["Request Status"]
                if (status.get("status") or {}).get("name") == "Auto Resolved":
                    # The human sets a USER field right after the write lands.
                    for page in notion.data_sources["synthetic-requests"]:
                        if page["id"] == page_id:
                            page["Course"] = ["synthetic-course-page-0"]
            return row
        notion.update_record = update_record  # type: ignore[method-assign]
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        draft = _assign_request(notion)
        assert draft["Request Status"] == "Draft" and not draft.get("Result Reference")
        assert draft["Course"] == ["synthetic-course-page-0"]  # the human's value survives
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "ABORTED" and state.get_request_receipt(receipt.request_key).state == "Draft"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED" and _item(system).classification_state == "HUMAN"
        assert isinstance(notion, InMemoryNotionWorker)
    finally:
        system_cm.__exit__(None, None, None)
