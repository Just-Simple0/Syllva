"""P-B2a: AUTO_PENDING plans (stage A), blank-draft auto-close (§3.5) with recovery, barriers."""
from __future__ import annotations

import contextlib
import hashlib
import json
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
        system["config"].intake.classification.canvas_course_map = {67535: COURSE}
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


# ---------------------------------------------------------------------------
# P-B2a FINAL r1 findings
# ---------------------------------------------------------------------------
def _request_writes(notion, since: int) -> list:
    return [e for e in notion.events[since:] if e[0] == "update" and e[1] == "synthetic-requests"]


def _second_file(system, *, name: str, raw: bytes, md5: bool, size: int | None = None) -> None:
    from dataclasses import replace

    drive = system["drive"]
    meta = drive.files[system["source_id"]]
    drive.files["synthetic-other"] = replace(
        meta, file_id="synthetic-other", name=name, size=len(raw) if size is None else size,
        md5_checksum=hashlib.md5(raw).hexdigest() if md5 else None,
    )
    drive.contents["synthetic-other"] = raw


def test_duplicate_gate_fails_closed_on_unprovable_bytes(tmp_path: Path) -> None:
    # r1 R1: another live file without an md5 and with the same declared size cannot be
    # ruled out as the same content, so the transcript stays HUMAN (fail closed).
    other = b"x" * len(RAW)
    with _system(tmp_path / "unproven", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="notes.md", raw=other, md5=False)
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "AUTO_BLOCK_DUPLICATE_UNPROVEN" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    # A different declared size proves the bytes differ: the gate passes.
    with _system(tmp_path / "proven-different", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="notes.md", raw=other + b"!", md5=False)
        worker.run_once()
        assert _item(system).classification_state == "CLASSIFIED" and len(_auto_plans(system["state"])) == 1
    # Same bytes under a second id discovered *after* the transcript in listing order: still blocked.
    with _system(tmp_path / "ordered", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="zzz-copy.md", raw=RAW, md5=True)
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "DUPLICATE_CONTENT" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def _add_details_draft(worker, system):
    item = _item(system)
    workspace = worker._workspace_for_item(item)
    context = worker._fresh_layout_context(worker._run_layout_workspaces())
    return worker._create_input_request_with_context(
        item, workspace, request_type="FILE_DETAILS", target_snapshot=None, layout_context=context)


def test_every_human_request_is_judged_before_any_draft_is_closed(tmp_path: Path) -> None:
    from tests.integration.test_intake_worker_preview import _details_request

    # r1 R2: one untouched Draft + one touched Draft of the same intake, in both orders.
    for touched in ("FILE_DETAILS", "ASSIGN_COURSE"):
        system_cm, system = _pre_v2_draft(tmp_path / touched)
        try:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _add_details_draft(worker, system)
            notion = system["notion"]
            page = _details_request(notion) if touched == "FILE_DETAILS" else _assign_request(notion)
            page["Kind"] = "Transcript"
            since = len(notion.events)
            worker.run_once()
            assert _request_writes(notion, since) == []
            assert {p["Request Status"] for p in notion.data_sources["synthetic-requests"]} == {"Draft"}
            assert _auto_plans(system["state"])[0]["status"] == "SUPERSEDED"
            assert _item(system).classification_state == "HUMAN"
            assert all(system["state"].get_auto_resolve_intent(r.request_key) is None
                       for r in system["state"].list_request_receipts())
        finally:
            system_cm.__exit__(None, None, None)
    # Both untouched: both close, two writes, both receipts terminal.
    system_cm, system = _pre_v2_draft(tmp_path / "both")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        notion = system["notion"]
        since = len(notion.events)
        worker.run_once()
        assert len(_request_writes(notion, since)) == 2
        assert {p["Request Status"] for p in notion.data_sources["synthetic-requests"]} == {"Auto Resolved"}
        assert {r.state for r in system["state"].list_request_receipts()} == {"AutoResolved"}
    finally:
        system_cm.__exit__(None, None, None)


def test_preflight_failure_parks_the_plan_without_any_write(tmp_path: Path) -> None:
    # r1 R3: the source moves (parent changes) between classification and the first
    # AUTO mutation.  Bytes are unchanged, yet the preflight must refuse.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        drive, notion, state = system["drive"], system["notion"], system["state"]
        original_download = drive.download
        from dataclasses import replace

        def download(file_id: str, *, max_bytes=None):
            data = original_download(file_id, max_bytes=max_bytes)
            drive.files[file_id] = replace(drive.files[file_id], parents=("synthetic-course-0",))
            return data
        drive.download = download  # type: ignore[method-assign]
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _assign_request(notion)["Request Status"] == "Draft"
        plans = _auto_plans(state)
        assert len(plans) == 1 and plans[0]["status"] == "RECONCILE_REQUIRED"
        item = _item(system)
        assert item.status == "RECONCILE_REQUIRED" and item.classification_state == "HUMAN"
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key) is None
        assert worker._submitted_request_keys() == []
    finally:
        system_cm.__exit__(None, None, None)


def _landing_hook(notion, on_landed) -> None:
    original_update = notion.update_record

    def update_record(data_source_id: str, page_id: str, properties):
        row = original_update(data_source_id, page_id, properties)
        status = (properties.get("Request Status") or {}).get("status") or {}
        if data_source_id == "synthetic-requests" and status.get("name") == "Auto Resolved":
            on_landed(page_id)
        return row
    notion.update_record = update_record  # type: ignore[method-assign]


def test_cancelled_after_the_write_lands_is_rolled_back(tmp_path: Path) -> None:
    # r1 R4: USER fields blank and Submitted=false, but Cancelled flipped right after the
    # write landed.  The exact snapshot comparison catches it: rollback, never DONE.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def cancel(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Cancelled"] = True
        _landing_hook(notion, cancel)
        worker.run_once()
        draft = _assign_request(notion)
        assert draft["Request Status"] == "Draft" and not draft.get("Result Reference") and draft["Cancelled"] is True
        receipt = next(r for r in state.list_request_receipts())
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "ABORTED" and receipt.state == "Draft"
        assert state.get_auto_resolve_rollback(intent.intent_id)["state"] == "DONE"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
    finally:
        system_cm.__exit__(None, None, None)


def _drop_first_auto_resolved_write(notion) -> dict:
    original_update = notion.update_record
    dropped = {"count": 0}

    def update_record(data_source_id: str, page_id: str, properties):
        status = (properties.get("Request Status") or {}).get("status") or {}
        if data_source_id == "synthetic-requests" and status.get("name") == "Auto Resolved" and dropped["count"] == 0:
            dropped["count"] += 1
            return notion.read_record(data_source_id, page_id)  # the write is lost on the wire
        return original_update(data_source_id, page_id, properties)
    notion.update_record = update_record  # type: ignore[method-assign]
    return dropped


def test_lost_write_is_retried_under_the_same_intent(tmp_path: Path) -> None:
    # r1 R5: the update never landed, the readback shows the untouched Draft.
    system_cm, system = _pre_v2_draft(tmp_path / "retry")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "PENDING" and _assign_request(notion)["Request Status"] == "Draft"
        assert _item(system).status == "RECONCILE_REQUIRED" and worker._submitted_request_keys() == []
        worker.run_once()  # recovery retries under the same intent and operation key
        assert state.get_auto_resolve_intent(receipt.request_key).intent_id == intent.intent_id
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        assert _assign_request(notion)["Request Status"] == "Auto Resolved"
        assert state.get_request_receipt(receipt.request_key).state == "AutoResolved"
    finally:
        system_cm.__exit__(None, None, None)
    # AUTO switched off after the lost write: the intent is ABORTED with zero writes and
    # the human path is released (the barrier lifts).
    system_cm, system = _pre_v2_draft(tmp_path / "disabled")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        system["config"].intake.classification.enabled = False
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED" and _item(system).classification_state == "HUMAN"
        _assign_request(notion).update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        assert worker._submitted_request_keys() == [receipt.request_key]
    finally:
        system_cm.__exit__(None, None, None)


def test_crash_after_the_rollback_write_completes_on_the_next_tick(tmp_path: Path) -> None:
    # r1 R6: the rollback write landed, then the process died before the local commit.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def edit(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Course"] = ["synthetic-course-page-0"]
        _landing_hook(notion, edit)
        original_complete = state.complete_auto_resolve_rollback
        crashes = {"left": 1}

        def complete(intent_id: str):
            if crashes["left"]:
                crashes["left"] -= 1
                raise RuntimeError("crash before the rollback commit")
            return original_complete(intent_id)
        state.complete_auto_resolve_rollback = complete  # type: ignore[method-assign]
        worker.run_once()  # the injected crash is absorbed per intent; the rollback stays PENDING
        receipt = next(r for r in state.list_request_receipts())
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "PENDING" and state.get_auto_resolve_rollback(intent.intent_id)["state"] == "PENDING"
        assert _assign_request(notion)["Request Status"] == "Draft"  # the provider write landed
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []  # no second rollback write
        assert state.get_auto_resolve_rollback(intent.intent_id)["state"] == "DONE"
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert state.get_request_receipt(receipt.request_key).state == "Draft"
        draft = _assign_request(notion)
        assert draft["Course"] == ["synthetic-course-page-0"] and not draft.get("Result Reference")
    finally:
        system_cm.__exit__(None, None, None)


def test_superseded_plan_is_never_revived_by_re_evaluation(tmp_path: Path) -> None:
    # r1 R7: the same classification revision re-evaluated after supersession.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _assign_request(notion)["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        since = len(notion.events)
        for _ in range(2):
            worker.run_once()
        plans = _auto_plans(state)
        assert len(plans) == 1 and plans[0]["status"] == "SUPERSEDED"
        item = _item(system)
        assert item.classification_state == "HUMAN"
        assert "AUTO_PLAN_SUPERSEDED" in state.get_intake_suggestion(item.intake_id)["suggestion_note"]
        assert _request_writes(notion, since) == [] and _assign_request(notion)["Request Status"] == "Draft"
        assert all(state.get_auto_resolve_intent(r.request_key) is None for r in state.list_request_receipts())
    finally:
        system_cm.__exit__(None, None, None)


def test_supersession_voids_jobs_atomically_and_idempotently(tmp_path: Path) -> None:
    # r1 R8: plan close and job VOID are one transaction, and a job left behind a plan
    # that is already closed is still voided on the next call.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state = system["state"]
        plan = _auto_plans(state)[0]
        state.create_job(job_key="sha256:" + "1" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        assert state.supersede_intake_plan(plan["plan_revision"], "TEST") == 1
        assert state.get_job(job_key="sha256:" + "1" * 64).voided_at is not None
        state.create_job(job_key="sha256:" + "2" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        assert state.supersede_intake_plan(plan["plan_revision"], "TEST") == 0  # already closed
        assert state.get_job(job_key="sha256:" + "2" * 64).voided_at is not None     # ... but still voided


def test_recovery_runs_after_the_feature_is_switched_off(tmp_path: Path) -> None:
    # r1 R9: a closure started, the readback vanished, then AUTO was disabled.  The
    # next tick still settles the intent from the readback instead of leaving the
    # human request barred forever.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_read = notion.read_record
        vanished = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if vanished["on"] and data_source_id == "synthetic-requests":
                return None
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        _landing_hook(notion, lambda page_id: vanished.__setitem__("on", True))
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        system["config"].intake.classification.enabled = False
        vanished["on"] = False
        worker.run_once()
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        assert state.get_request_receipt(receipt.request_key).state == "AutoResolved"
        assert worker._submitted_request_keys() == []
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r2 findings
# ---------------------------------------------------------------------------
def test_duplicate_gate_treats_unknown_size_as_unprovable(tmp_path: Path) -> None:
    # r2 R1: the other file has no md5 and no observed size → cannot be ruled out.
    from dataclasses import replace

    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="notes.md", raw=b"y" * 3, md5=False)
        drive = system["drive"]
        drive.files["synthetic-other"] = replace(drive.files["synthetic-other"], size=None)
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "AUTO_BLOCK_DUPLICATE_UNPROVEN" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_applied_sibling_request_blocks_the_closure(tmp_path: Path) -> None:
    # r2 R2: a human already applied ASSIGN_COURSE; the follow-up FILE_DETAILS Draft is
    # untouched but must not be auto-closed.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        notion, state = system["notion"], system["state"]
        _assign_request(notion).update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        system["worker"].run_once()
        assert {r.request_type: r.state for r in state.list_request_receipts()} == {
            "ASSIGN_COURSE": "Applied", "FILE_DETAILS": "Draft"}
        worker = _enable(system)
        _complete_calendar(state)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert all(r.state != "AutoResolved" for r in state.list_request_receipts())
        plans = _auto_plans(state)
        assert all(p["status"] == "SUPERSEDED" for p in plans)
    finally:
        system_cm.__exit__(None, None, None)


def test_human_change_during_the_preflight_download_stops_the_write(tmp_path: Path) -> None:
    # r2 R3 (A): the human edits a Draft while the preflight re-downloads the source.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        drive, notion, state = system["drive"], system["notion"], system["state"]
        original_download = drive.download

        def download(file_id: str, *, max_bytes=None):
            data = original_download(file_id, max_bytes=max_bytes)
            _assign_request(notion)["Course"] = ["synthetic-course-page-0"]
            return data
        drive.download = download  # type: ignore[method-assign]
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _assign_request(notion)["Request Status"] == "Draft"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        assert all(state.get_auto_resolve_intent(r.request_key) is None for r in state.list_request_receipts())
    finally:
        system_cm.__exit__(None, None, None)


def test_pending_retry_rechecks_every_sibling_draft(tmp_path: Path) -> None:
    # r2 R3 (B): the first closure write is lost; before the retry the human edits the
    # *other* Draft of the same intake.  The retry must not close the first Draft.
    from tests.integration.test_intake_worker_preview import _details_request

    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        pending = [r for r in state.list_request_receipts()
                   if (i := state.get_auto_resolve_intent(r.request_key)) is not None and i.state == "PENDING"]
        assert len(pending) == 1
        other = _details_request(notion) if pending[0].request_type == "ASSIGN_COURSE" else _assign_request(notion)
        other["Course"] = ["synthetic-course-page-0"]
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert {p["Request Status"] for p in notion.data_sources["synthetic-requests"]} == {"Draft"}
        assert state.get_auto_resolve_intent(pending[0].request_key).state == "ABORTED"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
    finally:
        system_cm.__exit__(None, None, None)


def test_changed_request_identity_in_the_readback_is_never_done(tmp_path: Path) -> None:
    # r2 R4: the page's Request Revision Hash drifts right after the write landed.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def drift(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Request Revision Hash"] = "drifted"
        _landing_hook(notion, drift)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        assert state.get_request_receipt(receipt.request_key).state == "Draft"
        assert _item(system).status == "RECONCILE_REQUIRED" and worker._submitted_request_keys() == []
        worker.run_once()  # identity still wrong: the barrier stays, nothing is closed
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
    finally:
        system_cm.__exit__(None, None, None)


def test_rollback_never_overwrites_a_status_the_human_moved_on(tmp_path: Path) -> None:
    # r2 R5: rollback pending, the human then set Submitted while our reference lingers.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def edit(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Course"] = ["synthetic-course-page-0"]
        _landing_hook(notion, edit)
        original_complete = state.complete_auto_resolve_rollback
        crashes = {"left": 1}

        def complete(intent_id: str):
            if crashes["left"]:
                crashes["left"] -= 1
                raise RuntimeError("crash before the rollback commit")
            return original_complete(intent_id)
        state.complete_auto_resolve_rollback = complete  # type: ignore[method-assign]
        worker.run_once()  # the injected crash is absorbed per intent; the rollback stays PENDING
        item = _item(system)
        draft = _assign_request(notion)
        draft.update({"Request Status": "Submitted", "Submitted": True, "Result Reference": item.classification_record_id})
        since = len(notion.events)
        worker.run_once()
        writes = _request_writes(notion, since)
        assert set(writes[0][3]) == {"Result Reference"}  # only our own reference is cleared first
        assert not any(((w[3].get("Request Status") or {}).get("status") or {}).get("name") == "Draft" for w in writes)
        assert not draft.get("Result Reference") or draft["Result Reference"] != item.classification_record_id
        receipt = next(r for r in state.list_request_receipts() if r.request_type == "ASSIGN_COURSE")
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
    finally:
        system_cm.__exit__(None, None, None)


def test_closure_reconcile_state_is_cleared_once_the_intent_settles(tmp_path: Path) -> None:
    # r2 R6: an unknown readback marks the item; the later DONE removes only that mark.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_read = notion.read_record
        vanished = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if vanished["on"] and data_source_id == "synthetic-requests":
                return None
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        _landing_hook(notion, lambda page_id: vanished.__setitem__("on", True))
        worker.run_once()
        assert _item(system).status == "RECONCILE_REQUIRED"
        vanished["on"] = False
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        item = _item(system)
        assert item.status != "RECONCILE_REQUIRED" and item.last_error_code is None
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r3 findings
# ---------------------------------------------------------------------------
def _spy_preflight(worker, on_call) -> None:
    original = worker._auto_preflight
    calls = {"n": 0}

    def preflight(*args, **kwargs):
        calls["n"] += 1
        on_call(calls["n"])
        return original(*args, **kwargs)
    worker._auto_preflight = preflight  # type: ignore[method-assign]


def _auto_resolved_pages(notion) -> list:
    return [p for p in notion.data_sources["synthetic-requests"] if p["Request Status"] == "Auto Resolved"]


def test_every_closure_repeats_the_source_preflight(tmp_path: Path) -> None:
    # r3 #1: the source moves after the first closure; the second Draft must not be closed.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        drive, notion, state = system["drive"], system["notion"], system["state"]
        from dataclasses import replace

        def move(call: int) -> None:
            if call == 2:
                drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], parents=("synthetic-course-0",))
        _spy_preflight(worker, move)
        worker.run_once()
        assert len(_auto_resolved_pages(notion)) == 1
        assert sum(1 for p in notion.data_sources["synthetic-requests"] if p["Request Status"] == "Draft") == 1
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
    finally:
        system_cm.__exit__(None, None, None)


def test_pending_retry_judges_humans_after_its_preflight(tmp_path: Path) -> None:
    # r3 #1: the sibling Draft changes *during the retry's preflight download*.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        pending = [r for r in state.list_request_receipts()
                   if (i := state.get_auto_resolve_intent(r.request_key)) is not None and i.state == "PENDING"]
        assert len(pending) == 1
        from tests.integration.test_intake_worker_preview import _details_request
        other = _details_request(notion) if pending[0].request_type == "ASSIGN_COURSE" else _assign_request(notion)
        _spy_preflight(worker, lambda call: other.__setitem__("Course", ["synthetic-course-page-0"]))
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert state.get_auto_resolve_intent(pending[0].request_key).state == "ABORTED"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
    finally:
        system_cm.__exit__(None, None, None)


def _rollback_pending(tmp_path: Path):
    """Crash after the rollback write: rollback PENDING, page already back to Draft."""

    system_cm, system = _pre_v2_draft(tmp_path)
    worker = _enable(system)
    _complete_calendar(system["state"])
    notion, state = system["notion"], system["state"]

    def edit(page_id: str) -> None:
        for page in notion.data_sources["synthetic-requests"]:
            if page["id"] == page_id:
                page["Course"] = ["synthetic-course-page-0"]
    _landing_hook(notion, edit)
    original_complete = state.complete_auto_resolve_rollback
    crashes = {"left": 1}

    def complete(intent_id: str):
        if crashes["left"]:
            crashes["left"] -= 1
            raise RuntimeError("crash before the rollback commit")
        return original_complete(intent_id)
    state.complete_auto_resolve_rollback = complete  # type: ignore[method-assign]
    worker.run_once()  # the injected crash is absorbed per intent; the rollback stays PENDING
    return system_cm, system, worker


def test_rollback_reentry_checks_identity_and_ownership(tmp_path: Path) -> None:
    # r3 #2: identity drift, a foreign reference and a Submitted flip on re-entry.
    system_cm, system, worker = _rollback_pending(tmp_path / "identity")
    try:
        notion, state = system["notion"], system["state"]
        _assign_request(notion)["Request Revision Hash"] = "drifted"
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
    finally:
        system_cm.__exit__(None, None, None)
    system_cm, system, worker = _rollback_pending(tmp_path / "foreign")
    try:
        notion, state = system["notion"], system["state"]
        _assign_request(notion).update({"Request Status": "Auto Resolved", "Result Reference": "someone-else"})
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _assign_request(notion)["Result Reference"] == "someone-else"
    finally:
        system_cm.__exit__(None, None, None)
    system_cm, system, worker = _rollback_pending(tmp_path / "submitted")
    try:
        notion, state = system["notion"], system["state"]
        record_id = _item(system).classification_record_id
        _assign_request(notion).update({"Request Status": "Auto Resolved", "Result Reference": record_id, "Submitted": True})
        worker.run_once()
        # The persisted target was Draft, but the live checkbox wins: never turned back to Draft.
        assert _assign_request(notion)["Request Status"] != "Draft"
        assert _assign_request(notion).get("Result Reference") != record_id  # our reference is gone
    finally:
        system_cm.__exit__(None, None, None)


def test_changed_intake_binding_is_not_untouched(tmp_path: Path) -> None:
    # r3 #3: the human re-pointed the Draft to another intake; also drift after the write.
    system_cm, system = _pre_v2_draft(tmp_path / "before")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"])["Intake Items"] = ["some-other-intake-page"]
        since = len(system["notion"].events)
        worker.run_once()
        assert _request_writes(system["notion"], since) == []
        assert _auto_plans(system["state"])[0]["status"] == "SUPERSEDED"
    finally:
        system_cm.__exit__(None, None, None)
    system_cm, system = _pre_v2_draft(tmp_path / "after")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def repoint(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Intake Items"] = ["some-other-intake-page"]
        _landing_hook(notion, repoint)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        assert state.get_request_receipt(receipt.request_key).state == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


def test_a_closed_sibling_the_human_edited_stops_further_closures(tmp_path: Path) -> None:
    # r3 #4: the first Draft is closed, then the human edits it; the second must stay open.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        notion, state = system["notion"], system["state"]

        def edit_closed(call: int) -> None:
            if call == 2:
                for page in _auto_resolved_pages(notion):
                    page["Course"] = ["synthetic-course-page-0"]
        _spy_preflight(worker, edit_closed)
        worker.run_once()
        assert len(_auto_resolved_pages(notion)) == 1
        assert sum(1 for p in notion.data_sources["synthetic-requests"] if p["Request Status"] == "Draft") == 1
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
    finally:
        system_cm.__exit__(None, None, None)


def _seed_session(notion, entity_id: str, *, recording_status: str | None = "Pending") -> None:
    row = {"id": f"page-{entity_id}", "ID": entity_id, "Course": ["synthetic-course-page-1"], "Date": "2026-09-10"}
    if recording_status is not None:
        row["Recording Status"] = recording_status
    notion.data_sources["synthetic-sessions"].append(row)


def test_stage_a_binds_the_transcript_to_the_session_inventory(tmp_path: Path) -> None:
    # r3 #5: NEW only when no Session exists; one free Session → EXISTING; otherwise blocked.
    cases = {
        "free": (["TEST102-S01"], None, "EXISTING"),
        "occupied": (["TEST102-S01"], "Ready", None),
        "ambiguous": (["TEST102-S01", "TEST102-S02"], None, None),
    }
    for name, (ids, occupied, expected_mode) in cases.items():
        with _system(tmp_path / name, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            for entity_id in ids:
                _seed_session(system["notion"], entity_id, recording_status=occupied or "Pending")
            worker.run_once()
            item, plans = _item(system), _auto_plans(system["state"])
            record = system["state"].get_classification_record(item.classification_record_id)
            if expected_mode is None:
                assert item.classification_state == "HUMAN" and plans == []
                note = system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
                assert ("AUTO_BLOCK_SESSION_OCCUPIED" if occupied else "AUTO_BLOCK_SESSION_AMBIGUOUS") in note
            else:
                assert item.classification_state == "CLASSIFIED" and len(plans) == 1
                assert (record.session_mode, record.session_id) == ("EXISTING", "TEST102-S01")
                assert record.sessions_inventory_hash
    with _system(tmp_path / "new", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        record = system["state"].get_classification_record(_item(system).classification_record_id)
        assert (record.session_mode, record.session_id) == ("NEW", None) and record.sessions_inventory_hash


def test_preflight_rechecks_alias_and_canvas_provenance(tmp_path: Path) -> None:
    # r3 #6 (alias): an alias edit after classification parks the plan, zero writes.
    system_cm, system = _pre_v2_draft(tmp_path / "alias")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _spy_preflight(worker, lambda call: system["config"].courses[1].__setattr__("aliases", ["새별칭"]))
        notion, state = system["notion"], system["state"]
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
    finally:
        system_cm.__exit__(None, None, None)
    # r3 #6 (Canvas): a PROFESSOR_SOURCE record whose binding was replaced is refused.
    csv = b"name,score\nA,1\n"
    with _system(tmp_path / "canvas", name="mbti.csv", mime_type="text/csv", raw=csv) as system:
        from dataclasses import replace
        _enable(system, profile=None)
        drive, state = system["drive"], system["state"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], parents=("synthetic-course-upload-1",))
        from tests.integration.test_intake_worker_preview import _privacy

        from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata
        drive.files["synthetic-course-upload-1"] = DriveMetadata(
            file_id="synthetic-course-upload-1", name="upload", mime_type=DRIVE_FOLDER_MIME,
            parents=("synthetic-course-1",), modified_time="2026-09-13T10:00:00Z", size=0, md5_checksum=None, **_privacy())
        system["config"].google_drive.semester_registries[0].optional_course_upload_folder_ids[COURSE] = "synthetic-course-upload-1"
        system["config"].intake.classification.canvas_course_map = {67535: COURSE}
        from uls.config.credentials import ResolvedCredentials
        from uls.intake.identity import provider_binding_id
        from uls.runtime import build_intake_worker
        system["worker"] = build_intake_worker(
            system["config"], ResolvedCredentials({}), state=state, drive=drive, notion=system["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2")
        worker = _rewire(system, "legacy5-cls")
        state.record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="assignment", resource_id="as-1",
            observation_revision=1, attachment_id="att-9", attachment_filename="mbti.csv", attachment_size=len(csv),
            byte_sha256=hashlib.sha256(csv).hexdigest())
        worker.run_once()
        item = _item(system)
        record = state.get_classification_record(item.classification_record_id)
        assert record.origin == "PROFESSOR_SOURCE" and _auto_plans(state)[0]["status"] == "AUTO_PENDING"
        workspace = worker._workspace_for_item(item)
        assert worker._provenance_mismatch(item, record, workspace) is None
        original = state.get_canvas_drive_binding
        # A later revision replaced the binding (P-B3 will own the lifecycle): the record's
        # exact basis no longer matches and the preflight must refuse.
        state.get_canvas_drive_binding = lambda file_id: {**original(file_id), "resource_id": "as-2"}  # type: ignore[method-assign]
        assert worker._provenance_mismatch(item, record, workspace) == "CANVAS_BINDING"
        state.get_canvas_drive_binding = lambda file_id: None  # type: ignore[method-assign]
        assert worker._provenance_mismatch(item, record, workspace) == "CANVAS_BINDING"


# ---------------------------------------------------------------------------
# P-B2a FINAL r4 findings
# ---------------------------------------------------------------------------
def test_session_occupancy_needs_pointer_and_binding_proof(tmp_path: Path) -> None:
    # r4 #1 / r5 R1: a Pending Session with a Normalized Transcript pointer or any canonical
    # source binding (another file's or this very file's) is occupied.
    for name in ("pointer", "foreign-binding", "own-binding"):
        with _system(tmp_path / name, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            row = system["notion"].data_sources["synthetic-sessions"][0]
            if name == "pointer":
                row["Normalized Transcript"] = "https://drive.google.com/file/d/elsewhere/view"
            else:
                system["state"].record_session_source_binding(
                    binding_id=f"b-{name}", course_key=COURSE, session_id="TEST102-S01", provider="google_drive",
                    provider_file_id="elsewhere" if name == "foreign-binding" else system["source_id"],
                    reservation_id="r-1", state="ACTIVE")
            worker.run_once()
            item = _item(system)
            # r5 R1: even a binding to this very file is occupied — P-B2a carries no
            # source-version/plan proof for an idempotent re-entry.
            assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
            assert "AUTO_BLOCK_SESSION_OCCUPIED" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_preflight_rechecks_session_semester_and_metadata(tmp_path: Path) -> None:
    from dataclasses import replace

    def run(label: str, mutate) -> tuple:
        system_cm, system = _pre_v2_draft(tmp_path / label)
        try:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _spy_preflight(worker, lambda call: mutate(system) if call == 1 else None)
            notion, state = system["notion"], system["state"]
            since = len(notion.events)
            worker.run_once()
            return _request_writes(notion, since), _auto_plans(state)[0]["status"], _assign_request(notion)["Request Status"]
        finally:
            system_cm.__exit__(None, None, None)

    # r4 #2: a Session appeared for the date after the plan said NEW.
    writes, status, request_status = run("session", lambda system: _seed_session(system["notion"], "TEST102-S01"))
    assert writes == [] and status == "RECONCILE_REQUIRED" and request_status == "Draft"
    # r4 #2: the effective semester range changed.
    def widen(system) -> None:
        system["config"].google_drive.semester_registries[0].end_date = "2026-12-31"
    writes, status, _ = run("semester", widen)
    assert writes == [] and status == "RECONCILE_REQUIRED"
    # r4 #2: the calendar projection changed (week 2 moved by a day).
    def shift(system) -> None:
        state = system["state"]
        state.record_canvas_observation(origin="canvas.knu", canvas_course_id=67535, resource_kind="module_item",
                                        resource_id="r4", observation_revision=1, title="2026-09-24",
                                        module_name="4주차", module_week=4, item_type="ExternalTool",
                                        collection_complete=True)
        entries = [RecordingEntry(67535, f"r{w}", 1, w, date(2026, 9, 3) + timedelta(days=7 * (w - 1))) for w in (1, 2, 3, 4)]
        state.replace_recording_calendar_current(
            COURSE, 67535, build_calendar(COURSE, entries, semester=SEMESTER, collection_complete=True),
            collection_complete=True)
    writes, status, _ = run("calendar", shift)
    assert writes == [] and status == "RECONCILE_REQUIRED"
    # r4 #4: renamed after S1 decided, bytes and md5 unchanged.
    def rename(system) -> None:
        drive = system["drive"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], name="notes.md")
    writes, status, _ = run("rename", rename)
    assert writes == [] and status == "RECONCILE_REQUIRED"
    def retype(system) -> None:
        drive = system["drive"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], mime_type="text/plain")
    writes, status, _ = run("mime", retype)
    assert writes == [] and status == "RECONCILE_REQUIRED"


def test_canvas_course_must_match_the_classified_course(tmp_path: Path) -> None:
    # r4 #3: a verified binding of another (or an unmapped) Canvas course never reaches AUTO.
    csv = b"name,score\nA,1\n"
    for label, mapping in (("other-course", {67535: COURSE_KEYS[2]}), ("unmapped", {})):
        with _system(tmp_path / label, name="mbti.csv", mime_type="text/csv", raw=csv) as system:
            from dataclasses import replace
            _enable(system, profile=None)
            drive, state = system["drive"], system["state"]
            drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], parents=("synthetic-course-upload-1",))
            from tests.integration.test_intake_worker_preview import _privacy

            from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata
            drive.files["synthetic-course-upload-1"] = DriveMetadata(
                file_id="synthetic-course-upload-1", name="upload", mime_type=DRIVE_FOLDER_MIME,
                parents=("synthetic-course-1",), modified_time="2026-09-13T10:00:00Z", size=0, md5_checksum=None, **_privacy())
            system["config"].google_drive.semester_registries[0].optional_course_upload_folder_ids[COURSE] = "synthetic-course-upload-1"
            system["config"].intake.classification.canvas_course_map = mapping
            from uls.config.credentials import ResolvedCredentials
            from uls.intake.identity import provider_binding_id
            from uls.runtime import build_intake_worker
            system["worker"] = build_intake_worker(
                system["config"], ResolvedCredentials({}), state=state, drive=drive, notion=system["notion"],
                provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
                semester="2026-2")
            worker = _rewire(system, "legacy5-cls")
            state.record_canvas_drive_binding(
                drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="assignment", resource_id="as-1",
                observation_revision=1, attachment_id="att-9", attachment_filename="mbti.csv", attachment_size=len(csv),
                byte_sha256=hashlib.sha256(csv).hexdigest())
            worker.run_once()
            item = _item(system)
            assert item.classification_state == "HUMAN" and _auto_plans(state) == []
            assert "AUTO_BLOCK_CANVAS_COURSE" in state.get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_a_foreign_result_reference_is_never_overwritten(tmp_path: Path) -> None:
    # r4 #5: first closure.
    system_cm, system = _pre_v2_draft(tmp_path / "first")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"])["Result Reference"] = "someone-elses-record"
        since = len(system["notion"].events)
        worker.run_once()
        assert _request_writes(system["notion"], since) == []
        assert _assign_request(system["notion"])["Result Reference"] == "someone-elses-record"
        assert _assign_request(system["notion"])["Request Status"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)
    # r4 #5: lost-write retry.
    system_cm, system = _pre_v2_draft(tmp_path / "retry")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        _assign_request(notion)["Result Reference"] = "someone-elses-record"
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _assign_request(notion)["Result Reference"] == "someone-elses-record"
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
    finally:
        system_cm.__exit__(None, None, None)


def test_rollback_aligns_status_when_submitted_flips_mid_rollback(tmp_path: Path) -> None:
    # r4 #6: the human submits while the rollback write is in flight.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def edit(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Course"] = ["synthetic-course-page-0"]
        _landing_hook(notion, edit)
        original_update = notion.update_record

        def update_record(data_source_id: str, page_id: str, properties):
            row = original_update(data_source_id, page_id, properties)
            status = ((properties.get("Request Status") or {}).get("status") or {}).get("name")
            if data_source_id == "synthetic-requests" and status == "Draft" and "Result Reference" in properties:
                for page in notion.data_sources["synthetic-requests"]:
                    if page["id"] == page_id:
                        page["Submitted"] = True
            return row
        notion.update_record = update_record  # type: ignore[method-assign]
        since = len(notion.events)
        worker.run_once()
        status_only = [e for e in _request_writes(notion, since) if set(e[3]) == {"Request Status"}
                       and e[3]["Request Status"]["status"]["name"] == "Submitted"]
        assert status_only, "the status projection must follow the live Submitted checkbox"
        receipt = next(r for r in state.list_request_receipts() if r.request_type == "ASSIGN_COURSE")
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "ABORTED" and state.get_auto_resolve_rollback(intent.intent_id)["state"] == "DONE"
    finally:
        system_cm.__exit__(None, None, None)


def test_json_boolean_size_is_not_a_proven_size(tmp_path: Path) -> None:
    # r4 #7: {"size": true} must not clear the duplicate ambiguity.
    from dataclasses import replace

    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="notes.md", raw=b"z" * 9, md5=False)
        drive = system["drive"]
        drive.files["synthetic-other"] = replace(drive.files["synthetic-other"], size=True)  # type: ignore[arg-type]
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "AUTO_BLOCK_DUPLICATE_UNPROVEN" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


# ---------------------------------------------------------------------------
# P-B2a FINAL r5 findings
# ---------------------------------------------------------------------------
def test_a_session_without_an_exact_pending_status_is_occupied(tmp_path: Path) -> None:
    # r5 R1: missing or empty Recording Status proves nothing.
    for label, status in (("missing", None), ("empty", "")):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01", recording_status=status)
            if status == "":
                system["notion"].data_sources["synthetic-sessions"][0]["Recording Status"] = ""
            worker.run_once()
            item = _item(system)
            assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
            assert "AUTO_BLOCK_SESSION_OCCUPIED" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_preflight_pins_the_original_parent_even_when_the_observation_follows(tmp_path: Path) -> None:
    # r5 R2: the file moved to another registered folder and the item observation was
    # updated to match; the bytes and md5 are unchanged.
    from dataclasses import replace

    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        drive, notion, state = system["drive"], system["notion"], system["state"]

        def moved(call: int) -> None:
            if call == 1:
                drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], parents=("synthetic-course-0",))
                state.update_intake_item(_item(system).intake_id, observed_parent_id="synthetic-course-0")
        _spy_preflight(worker, moved)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert _assign_request(notion)["Request Status"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


def test_stale_auto_plans_are_retired_and_a_human_claim_yields_them(tmp_path: Path) -> None:
    # r5 R3 (late duplicate): the plan exists, then identical bytes appear under another id.
    with _system(tmp_path / "late-duplicate", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        plan = _auto_plans(system["state"])[0]
        assert plan["status"] == "AUTO_PENDING"
        state = system["state"]
        state.create_job(job_key="sha256:" + "3" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        _second_file(system, name="zzz-copy.md", raw=RAW, md5=True)
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN"
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert state.get_job(job_key="sha256:" + "3" * 64).voided_at is not None
        assert len(system["notion"].data_sources["synthetic-requests"]) >= 1  # the HUMAN draft appears
    # r5 R3 (direct claim): a sibling HUMAN request is claimed while an AUTO plan is pending.
    with _system(tmp_path / "claim", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state, notion = system["state"], system["notion"]
        assert _auto_plans(state)[0]["status"] == "AUTO_PENDING"
        item = _item(system)
        receipt = worker._create_input_request_with_context(
            item, worker._workspace_for_item(item), request_type="ASSIGN_COURSE", target_snapshot=None,
            layout_context=worker._fresh_layout_context(worker._run_layout_workspaces()))
        _assign_request(notion).update({"Course": ["synthetic-course-page-1"], "Submitted": True})
        with contextlib.suppress(Exception):  # the claim outcome is irrelevant; the authority transition is not
            worker.claim_request(receipt.request_key)
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        assert _item(system).classification_state == "HUMAN"


def test_a_closed_sibling_that_lost_its_reference_stops_further_closures(tmp_path: Path) -> None:
    # r5 R4: the first Draft is closed, then its system reference is removed.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _add_details_draft(worker, system)
        notion, state = system["notion"], system["state"]

        def drop_reference(call: int) -> None:
            if call == 2:
                for page in _auto_resolved_pages(notion):
                    page["Result Reference"] = None
        _spy_preflight(worker, drop_reference)
        worker.run_once()
        assert sum(1 for p in notion.data_sources["synthetic-requests"] if p["Request Status"] == "Draft") == 1
        assert len(_auto_resolved_pages(notion)) == 1
        assert _auto_resolved_pages(notion)[0].get("Result Reference") is None  # never silently restored
        receipts = {r.request_type: r.state for r in state.list_request_receipts()}
        assert sorted(receipts.values()) == ["AutoResolved", "Draft"]
    finally:
        system_cm.__exit__(None, None, None)


def test_without_full_intake_capability_nothing_automatic_happens(tmp_path: Path) -> None:
    # r5 O1: enabled + verified profile but no full-intake Drive capability.
    from dataclasses import replace

    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        drive, notion, state = system["drive"], system["notion"], system["state"]
        drive.capabilities = replace(drive.capabilities, file_id_preserving_move=False)
        worker = _enable(system)
        _complete_calendar(state)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == [] and _auto_plans(state) == []
        assert _assign_request(notion)["Request Status"] == "Draft"
        assert all(r.state == "Draft" for r in state.list_request_receipts())
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r6 findings
# ---------------------------------------------------------------------------
def test_a_rejected_direct_claim_leaves_the_auto_plan_alone(tmp_path: Path) -> None:
    # r6 #1: an unsubmitted (blank) or tampered request does not take the authority.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state, notion = system["state"], system["notion"]
        plan = _auto_plans(state)[0]
        state.create_job(job_key="sha256:" + "4" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        item = _item(system)
        receipt = worker._create_input_request_with_context(
            item, worker._workspace_for_item(item), request_type="ASSIGN_COURSE", target_snapshot=None,
            layout_context=worker._fresh_layout_context(worker._run_layout_workspaces()))
        with contextlib.suppress(Exception):
            worker.claim_request(receipt.request_key)  # blank Draft: not submitted
        assert _auto_plans(state)[0]["status"] == "AUTO_PENDING" and _item(system).classification_state == "CLASSIFIED"
        assert state.get_job(job_key="sha256:" + "4" * 64).voided_at is None
        _assign_request(notion).update({"Course": ["synthetic-course-page-1"], "Submitted": True, "Request Key": "tampered"})
        with contextlib.suppress(Exception):
            worker.claim_request(receipt.request_key)  # identity mismatch
        assert _auto_plans(state)[0]["status"] == "AUTO_PENDING"
        _assign_request(notion)["Request Key"] = receipt.request_key
        with contextlib.suppress(Exception):
            worker.claim_request(receipt.request_key)  # genuine submission
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        assert state.get_job(job_key="sha256:" + "4" * 64).voided_at is not None


def test_provider_exceptions_converge_to_the_unknown_outcome_contract(tmp_path: Path) -> None:
    from uls.adapters.notion.intake import ProviderUnavailableError

    # (a) the write landed, then the readback raises.
    system_cm, system = _pre_v2_draft(tmp_path / "readback")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_read = notion.read_record
        broken = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if broken["on"] and data_source_id == "synthetic-requests":
                raise ProviderUnavailableError("timeout")
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        _landing_hook(notion, lambda page_id: broken.__setitem__("on", True))
        worker.run_once()  # must not raise
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        assert state.get_request_receipt(receipt.request_key).state == "Draft"
        item = _item(system)
        assert item.status == "RECONCILE_REQUIRED" and item.last_error_code == "AUTO_RESOLVE_RECONCILE"
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(receipt.request_key)
        worker.run_once()  # still broken: recovery is absorbed, the barrier stays
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        broken["on"] = False
        since = len(notion.events)
        worker.run_once()
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        assert state.get_request_receipt(receipt.request_key).state == "AutoResolved"
        assert _request_writes(notion, since) == []  # no second provider mutation
        assert _item(system).last_error_code is None
    finally:
        system_cm.__exit__(None, None, None)
    # (b) the update response is lost although the server applied it.
    system_cm, system = _pre_v2_draft(tmp_path / "update")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_update = notion.update_record
        flaky = {"on": True}

        def update_record(data_source_id: str, page_id: str, properties):
            row = original_update(data_source_id, page_id, properties)
            status = ((properties.get("Request Status") or {}).get("status") or {}).get("name")
            if flaky["on"] and data_source_id == "synthetic-requests" and status == "Auto Resolved":
                flaky["on"] = False
                raise ProviderUnavailableError("response lost")
            return row
        notion.update_record = update_record  # type: ignore[method-assign]
        worker.run_once()  # must not raise
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        since = len(notion.events)
        worker.run_once()
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        assert _request_writes(notion, since) == []  # recovery (i): no duplicate mutation
    finally:
        system_cm.__exit__(None, None, None)
    # (c) the rollback write landed, then the confirming readback raises once.
    system_cm, system = _pre_v2_draft(tmp_path / "rollback")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def edit(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Course"] = ["synthetic-course-page-0"]
        _landing_hook(notion, edit)
        original_update = notion.update_record
        original_read = notion.read_record
        arm = {"on": False}

        def update_record(data_source_id: str, page_id: str, properties):
            row = original_update(data_source_id, page_id, properties)
            status = ((properties.get("Request Status") or {}).get("status") or {}).get("name")
            if data_source_id == "synthetic-requests" and status == "Draft" and "Result Reference" in properties:
                arm["on"] = True
            return row

        def read_record(data_source_id: str, page_id: str):
            if arm["on"] and data_source_id == "synthetic-requests":
                arm["on"] = False
                raise ProviderUnavailableError("timeout")
            return original_read(data_source_id, page_id)
        notion.update_record = update_record  # type: ignore[method-assign]
        notion.read_record = read_record  # type: ignore[method-assign]
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "PENDING" and state.get_auto_resolve_rollback(intent.intent_id)["state"] == "PENDING"
        since = len(notion.events)
        worker.run_once()
        assert state.get_auto_resolve_rollback(intent.intent_id)["state"] == "DONE"
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert _request_writes(notion, since) == []
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r7 findings
# ---------------------------------------------------------------------------
def test_a_changed_request_type_is_never_closed(tmp_path: Path) -> None:
    # r7 #1 (before): the page's Request Type no longer matches the receipt.
    system_cm, system = _pre_v2_draft(tmp_path / "before")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"])["Request Type"] = "FILE_DETAILS"
        since = len(system["notion"].events)
        worker.run_once()
        assert _request_writes(system["notion"], since) == []
        assert all(r.state == "Draft" for r in system["state"].list_request_receipts())
    finally:
        system_cm.__exit__(None, None, None)
    # r7 #1 (after): the type changes right after the write landed → never DONE.
    system_cm, system = _pre_v2_draft(tmp_path / "after")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def retype(page_id: str) -> None:
            for page in notion.data_sources["synthetic-requests"]:
                if page["id"] == page_id:
                    page["Request Type"] = "FILE_DETAILS"
        _landing_hook(notion, retype)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        assert state.get_request_receipt(receipt.request_key).state == "Draft"
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(receipt.request_key)
    finally:
        system_cm.__exit__(None, None, None)


def test_an_independent_reconcile_cause_survives_the_closure_marks(tmp_path: Path) -> None:
    # r7 #2: another RECONCILE_REQUIRED cause must not be replaced or cleared by the closure.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_read = notion.read_record
        vanished = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if vanished["on"] and data_source_id == "synthetic-requests":
                return None
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        _landing_hook(notion, lambda page_id: vanished.__setitem__("on", True))
        worker.run_once()
        assert _item(system).last_error_code == "AUTO_RESOLVE_RECONCILE"
        state.update_intake_item(_item(system).intake_id, status="RECONCILE_REQUIRED",
                                 last_error_code="SOURCE_CHANGED_ELSEWHERE", last_error="independent cause")
        worker.run_once()  # recovery still cannot read: the independent cause stays
        assert _item(system).last_error_code == "SOURCE_CHANGED_ELSEWHERE"
        vanished["on"] = False
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        item = _item(system)
        assert item.status == "RECONCILE_REQUIRED" and item.last_error_code == "SOURCE_CHANGED_ELSEWHERE"
    finally:
        system_cm.__exit__(None, None, None)


def test_a_duplicate_found_after_stage_a_stops_the_first_write(tmp_path: Path) -> None:
    # r7 O1: identical bytes become known locally between stage A and the first write.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def duplicate_appears(call: int) -> None:
            if call == 1:
                item = _item(system)
                state.upsert_intake_item(
                    intake_id="intake-late-duplicate", provider=item.provider, provider_file_id="late-duplicate",
                    semester=item.semester, original_parent_id=item.original_parent_id,
                    observed_parent_id=item.observed_parent_id, original_name="late.md", mime_type="text/markdown",
                    source_hash="md5:" + hashlib.md5(RAW).hexdigest(), source_version=1, observed_kind="UNKNOWN",
                    course_candidates_json=[], status="NEEDS_INPUT", content_status="Pending")
        _spy_preflight(worker, duplicate_appears)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert _assign_request(notion)["Request Status"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r8 findings
# ---------------------------------------------------------------------------
def test_abort_is_atomic_with_plan_supersession_and_job_void(tmp_path: Path) -> None:
    # r8 R1: a crash right after the ABORTED commit must not leave a live AUTO plan.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        plan = _auto_plans(state)[0]
        state.create_job(job_key="sha256:" + "5" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        _assign_request(notion)["Course"] = ["synthetic-course-page-0"]
        original_event = state.record_intake_stage_event

        def crash_after_commit(event: str, **kwargs):
            if event == "auto_plan_superseded":
                raise RuntimeError("crash right after the abort commit")
            return original_event(event, **kwargs)
        state.record_intake_stage_event = crash_after_commit  # type: ignore[method-assign]
        worker._recover_auto_resolve_intents()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        assert state.get_job(job_key="sha256:" + "5" * 64).voided_at is not None
        assert _item(system).classification_state == "HUMAN"
    finally:
        system_cm.__exit__(None, None, None)


def test_every_closure_error_path_respects_reconcile_ownership(tmp_path: Path) -> None:
    for label, independent in (("independent", True), ("own-only", False)):
        system_cm, system = _pre_v2_draft(tmp_path / label)
        try:
            worker = _enable(system)
            _complete_calendar(system["state"])
            notion, state = system["notion"], system["state"]
            _drop_first_auto_resolved_write(notion)
            worker.run_once()
            if independent:
                state.update_intake_item(_item(system).intake_id, status="RECONCILE_REQUIRED",
                                         last_error_code="SOURCE_CHANGED_ELSEWHERE", last_error="independent cause")
            page = _assign_request(notion)
            page["Request Type"] = "FILE_DETAILS"   # identity broken
            worker.run_once()
            expected = "SOURCE_CHANGED_ELSEWHERE" if independent else "AUTO_RESOLVE_RECONCILE"
            assert _item(system).last_error_code == expected
            page["Request Type"] = "ASSIGN_COURSE"  # identity restored
            worker.run_once()
            receipt = next(r for r in state.list_request_receipts())
            item = _item(system)
            if independent:
                # r11 R3: an independent reconcile cause blocks the retry write; the closure
                # ends without a write and the independent cause is untouched.
                assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
                assert _assign_request(notion)["Request Status"] == "Draft"
                assert item.status == "RECONCILE_REQUIRED" and item.last_error_code == "SOURCE_CHANGED_ELSEWHERE"
            else:
                assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
                assert item.status != "RECONCILE_REQUIRED" and item.last_error_code is None
        finally:
            system_cm.__exit__(None, None, None)


def test_the_closure_mark_is_cleared_in_the_terminal_commit(tmp_path: Path) -> None:
    # r8 R3: a crash right after DONE cannot leave the closure's reconcile mark behind.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_read = notion.read_record
        vanished = {"on": False}

        def read_record(data_source_id: str, page_id: str):
            if vanished["on"] and data_source_id == "synthetic-requests":
                return None
            return original_read(data_source_id, page_id)
        notion.read_record = read_record  # type: ignore[method-assign]
        _landing_hook(notion, lambda page_id: vanished.__setitem__("on", True))
        worker.run_once()
        assert _item(system).last_error_code == "AUTO_RESOLVE_RECONCILE"
        vanished["on"] = False

        def crash(item) -> None:
            raise RuntimeError("crash between the terminal commit and the cleanup")
        worker._clear_auto_close_reconcile = crash  # type: ignore[method-assign]
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "DONE"
        item = _item(system)
        assert item.last_error_code is None and item.status != "RECONCILE_REQUIRED"
    finally:
        system_cm.__exit__(None, None, None)


def test_a_session_with_an_unreadable_date_is_unknown(tmp_path: Path) -> None:
    # r8 O1: no Date, or an unparsable one, may be the target; a clearly different date is fine.
    for label, date_value, expected in (("missing", None, "HUMAN"), ("garbage", "tomorrow", "HUMAN"),
                                        ("other-day", "2026-09-11", "CLASSIFIED")):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            row = system["notion"].data_sources["synthetic-sessions"][0]
            if date_value is None:
                row.pop("Date")
            else:
                row["Date"] = date_value
            worker.run_once()
            item = _item(system)
            assert item.classification_state == expected
            if expected == "HUMAN":
                assert "AUTO_BLOCK_SESSION_UNKNOWN" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


# ---------------------------------------------------------------------------
# P-B2a FINAL r9 findings
# ---------------------------------------------------------------------------
def test_reconcile_verdict_parks_the_plan_and_keeps_an_independent_cause(tmp_path: Path) -> None:
    # r9 #1: first closure with a foreign Result Reference → plan RECONCILE_REQUIRED, jobs VOID,
    # no write; and an independent RECONCILE_REQUIRED cause is not replaced.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        # first tick: classification only closes nothing yet because the reference is foreign
        _assign_request(notion)["Result Reference"] = "someone-elses-record"
        # a job that must not survive the parked plan is created once the plan exists
        _spy_preflight(worker, lambda call: None)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        plan = _auto_plans(state)[0]
        assert plan["status"] == "RECONCILE_REQUIRED" and _item(system).classification_state == "HUMAN"
        # direct verdict on an item carrying an independent cause
        item = _item(system)
        record = state.get_classification_record(item.classification_record_id)
        state.update_intake_item(item.intake_id, status="RECONCILE_REQUIRED",
                                 last_error_code="SOURCE_CHANGED_ELSEWHERE", last_error="independent cause")
        worker._apply_human_verdict(_item(system), record, worker._workspace_for_item(item), "RECONCILE", "sibling unreadable")
        kept = _item(system)
        assert kept.status == "RECONCILE_REQUIRED" and kept.last_error_code == "SOURCE_CHANGED_ELSEWHERE"
    finally:
        system_cm.__exit__(None, None, None)


def test_a_late_canvas_binding_changes_the_provenance(tmp_path: Path) -> None:
    # r9 #2: a USER transcript decided without any Canvas binding; one appears before the write.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]

        def bind(call: int) -> None:
            if call == 1:
                state.record_canvas_drive_binding(
                    drive_file_id=system["source_id"], canvas_course_id=99999, resource_kind="assignment",
                    resource_id="as-9", observation_revision=1, attachment_id="att-1",
                    attachment_filename="x.md", attachment_size=len(RAW), byte_sha256=hashlib.sha256(RAW).hexdigest())
        _spy_preflight(worker, bind)
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert _assign_request(notion)["Request Status"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


def test_the_alias_hash_comes_from_the_snapshot_the_decision_used(tmp_path: Path) -> None:
    # r9 #3: the aliases change right after the classification snapshot was taken.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._course_alias_entries
        calls = {"n": 0}

        def entries(semester: str):
            calls["n"] += 1
            snapshot = original(semester)
            if calls["n"] == 1:
                system["config"].courses[1].aliases = ["다른별칭"]  # changes after the first (decision) snapshot
            return snapshot
        worker._course_alias_entries = entries  # type: ignore[method-assign]
        worker.run_once()
        item = _item(system)
        record = system["state"].get_classification_record(item.classification_record_id)
        # the recorded hash is the *old* snapshot's, so a fresh readback differs and is detected
        assert worker._provenance_mismatch(item, record, worker._workspace_for_item(item)) == "ALIAS_BASIS"


def test_session_dates_must_be_complete_iso_values(tmp_path: Path) -> None:
    # r9 #4: valid-looking prefixes of invalid text prove nothing; valid datetimes do.
    cases = (("prefix", "2026-09-10invalid", "HUMAN"), ("bad-suffix", "2026-09-10T99:99:00", "HUMAN"),
             ("datetime-other-day", "2026-09-11T09:00:00+09:00", "CLASSIFIED"),
             ("date-other-day", "2026-09-11", "CLASSIFIED"))
    for label, value, expected in cases:
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            system["notion"].data_sources["synthetic-sessions"][0]["Date"] = value
            worker.run_once()
            assert _item(system).classification_state == expected, label


# ---------------------------------------------------------------------------
# P-B2a FINAL r10 findings
# ---------------------------------------------------------------------------
def test_a_human_supersession_survives_a_new_classification_revision(tmp_path: Path) -> None:
    # r10 R1: human edit → SUPERSEDED → fields cleared → classification input changes.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        page = _assign_request(notion)
        page["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED"
        page["Course"] = []
        system["config"].courses[1].aliases = ["다른별칭"]  # new alias snapshot → new classification revision
        since = len(notion.events)
        worker.run_once()
        worker.run_once()
        plans = _auto_plans(state)
        assert len(plans) == 1 and plans[0]["status"] == "SUPERSEDED"
        assert _request_writes(notion, since) == [] and page["Request Status"] == "Draft"
        item = _item(system)
        assert item.classification_state == "HUMAN"
        assert "AUTO_PLAN_SUPERSEDED" in state.get_intake_suggestion(item.intake_id)["suggestion_note"]
    finally:
        system_cm.__exit__(None, None, None)


def test_disabling_the_feature_returns_a_classified_item_to_the_human_path(tmp_path: Path) -> None:
    # r10 R2: AUTO_PENDING, no draft → feature off → exactly one HUMAN draft, no AUTO write.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state, notion = system["state"], system["notion"]
        assert _item(system).classification_state == "CLASSIFIED" and notion.data_sources["synthetic-requests"] == []
        plan = _auto_plans(state)[0]
        state.create_job(job_key="sha256:" + "6" * 64, operation="intake.test", stage="intake",
                         target_entity_id=plan["intake_id"], plan_revision=plan["plan_revision"],
                         plan_authority="AUTO_CLASSIFICATION")
        system["config"].intake.classification.enabled = False
        since = len(notion.events)
        worker.run_once()
        worker.run_once()
        assert len(notion.data_sources["synthetic-requests"]) == 1
        assert notion.data_sources["synthetic-requests"][0]["Request Status"] == "Draft"
        assert not any(((e[3].get("Request Status") or {}).get("status") or {}).get("name") == "Auto Resolved"
                       for e in notion.events[since:] if e[0] == "update")
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert state.get_job(job_key="sha256:" + "6" * 64).voided_at is not None
        assert _item(system).classification_state == "HUMAN"


def test_session_dates_reject_non_iso_separators(tmp_path: Path) -> None:
    # r10 R3: Python's lenient parser would accept 'X', '_' and spaces as the date/time separator.
    bad = {"x": "2026-09-10X09:00:00", "underscore": "2026-09-10_09:00:00", "space": "2026-09-10 09:00:00",
           "unicode": "2026-09-10 09:00:00", "date-suffix": "2026-09-10Z"}
    for label, value in bad.items():
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            system["notion"].data_sources["synthetic-sessions"][0]["Date"] = value
            worker.run_once()
            assert _item(system).classification_state == "HUMAN", label
    for label, value in {"date": "2026-09-11", "datetime": "2026-09-11T09:00:00", "offset": "2026-09-11T09:00:00+09:00",
                         "zulu": "2026-09-11T00:00:00Z"}.items():
        with _system(tmp_path / ("ok-" + label), name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            system["notion"].data_sources["synthetic-sessions"][0]["Date"] = value
            worker.run_once()
            assert _item(system).classification_state == "CLASSIFIED", label


def test_a_confirmed_write_that_was_reverted_is_not_retried(tmp_path: Path) -> None:
    # r10 R4: READBACK_OK, crash before DONE, the page is reverted to an untouched Draft.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_transition = state.transition_auto_resolve_intent
        crashes = {"left": 1}

        def transition(intent_id: str, new_state: str, **kwargs):
            if new_state == "DONE" and crashes["left"]:
                crashes["left"] -= 1
                raise RuntimeError("crash before the atomic DONE commit")
            return original_transition(intent_id, new_state, **kwargs)
        state.transition_auto_resolve_intent = transition  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        page = _assign_request(notion)
        page["Request Status"] = "Draft"
        page["Result Reference"] = None
        since = len(notion.events)
        worker.run_once()
        worker.run_once()
        assert _request_writes(notion, since) == []
        intent = state.get_auto_resolve_intent(receipt.request_key)
        assert intent.state == "ABORTED" and state.get_request_receipt(receipt.request_key).state == "Draft"
        assert _auto_plans(state)[0]["status"] == "SUPERSEDED" and _item(system).classification_state == "HUMAN"
        assert page["Request Status"] == "Draft"
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r11 findings
# ---------------------------------------------------------------------------
def _course_rows(notion) -> dict:
    return {row["Course Key"]: row for row in notion.data_sources["synthetic-courses"]}


def test_notion_user_aliases_join_the_auto_course_evidence(tmp_path: Path) -> None:
    # r11 R1 (conflict): a Notion alias equal to this course's name on another course disables it.
    with _system(tmp_path / "conflict", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE_KEYS[2]]["Aliases"] = "Synthetic Course 1, 다른이름"
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        # the duplicated alias is disabled for both courses (plan §2.3): the course is unresolved
        assert "AUTO_BLOCK_COURSE_UNRESOLVED" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
        assert item.inferred_course_key is None
    # r11 R1 (unreadable): alias-based AUTO fails closed.
    with _system(tmp_path / "unreadable", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE]["Aliases"] = {"unexpected": "shape"}
        worker.run_once()
        assert _item(system).classification_state == "HUMAN" and _auto_plans(system["state"]) == []
    # r11 R1 (changed after the decision): the first-write preflight sees the new Notion Aliases.
    system_cm, system = _pre_v2_draft(tmp_path / "changed")
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _spy_preflight(worker, lambda call: _course_rows(notion)[COURSE_KEYS[0]].__setitem__("Aliases", "새별칭"))
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
    finally:
        system_cm.__exit__(None, None, None)
    # control: a harmless Notion alias for an unrelated course does not block AUTO.
    with _system(tmp_path / "control", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE_KEYS[3]]["Aliases"] = "무관한별칭"
        worker.run_once()
        assert _item(system).classification_state == "CLASSIFIED"


def test_canonical_bindings_without_an_intake_row_count_as_duplicates(tmp_path: Path) -> None:
    # r11 R2: a Canvas binding with the same bytes (proven), a Session binding of an unknown
    # file (unprovable) and a Canvas binding with different bytes (harmless).
    cases = {
        "canvas-same": ("canvas", hashlib.sha256(RAW).hexdigest(), "DUPLICATE_CONTENT"),
        "session-binding": ("session", None, "AUTO_BLOCK_DUPLICATE_UNPROVEN"),
        "canvas-different": ("canvas", hashlib.sha256(b"other").hexdigest(), None),
    }
    for label, (kind, sha, expected) in cases.items():
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            state = system["state"]
            if kind == "canvas":
                state.record_canvas_drive_binding(
                    drive_file_id="legacy-only-file", canvas_course_id=1, resource_kind="assignment",
                    resource_id="as-1", observation_revision=1, attachment_id="att-1", attachment_filename="x.md",
                    attachment_size=len(RAW), byte_sha256=sha)
            else:
                state.record_session_source_binding(
                    binding_id="b-legacy", course_key=COURSE, session_id="TEST102-S09", provider="google_drive",
                    provider_file_id="legacy-only-file", reservation_id="r-9", state="ACTIVE")
            worker.run_once()
            item = _item(system)
            if expected is None:
                assert item.classification_state == "CLASSIFIED", label
            else:
                assert item.classification_state == "HUMAN" and _auto_plans(state) == [], label
                assert expected in state.get_intake_suggestion(item.intake_id)["suggestion_note"], label


def test_preflight_blocks_on_the_items_own_state(tmp_path: Path) -> None:
    # r11 R3: DUPLICATE_CANDIDATE and an independent reconcile cause set after stage A.
    for label, fields in (("duplicate", {"last_error_code": "DUPLICATE_CANDIDATE", "last_error": "late"}),
                          ("reconcile", {"status": "RECONCILE_REQUIRED", "last_error_code": "SOURCE_CHANGED_ELSEWHERE",
                                         "last_error": "independent"})):
        system_cm, system = _pre_v2_draft(tmp_path / label)
        try:
            worker = _enable(system)
            _complete_calendar(system["state"])
            notion, state = system["notion"], system["state"]
            def mark(call: int, f=fields, st=state, sy=system) -> None:
                if call == 1:
                    st.update_intake_item(_item(sy).intake_id, **f)
            _spy_preflight(worker, mark)
            since = len(notion.events)
            worker.run_once()
            assert _request_writes(notion, since) == [], label
            assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED", label
            assert _item(system).last_error_code == fields["last_error_code"], label  # cause untouched
        finally:
            system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r12 findings
# ---------------------------------------------------------------------------
def test_an_unreadable_notion_course_key_makes_the_alias_evidence_uncertain(tmp_path: Path) -> None:
    # r12 R1: a row whose semester cannot be judged hides a possibly conflicting alias.
    for label, key in (("missing", None), ("garbage", "not a course key")):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            system["notion"].data_sources["synthetic-courses"].append(
                {"id": "synthetic-course-page-x", "Course Key": key, "Aliases": "Synthetic Course 1"})
            worker.run_once()
            item = _item(system)
            assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == [], label
            assert "AUTO_BLOCK_ALIAS_EVIDENCE" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    # a well-formed key of another semester is certainly out of scope
    with _system(tmp_path / "other-semester", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["notion"].data_sources["synthetic-courses"].append(
            {"id": "synthetic-course-page-y", "Course Key": "2025-1_TEST999-001", "Aliases": "Synthetic Course 1"})
        worker.run_once()
        assert _item(system).classification_state == "CLASSIFIED"


def test_a_notion_only_alias_resolves_the_course_for_auto(tmp_path: Path) -> None:
    # r12 R2: the alias exists only in the USER Notion Aliases.
    name = "2026.09.10_별칭전용_2주차.md"
    with _system(tmp_path / "ok", name=name, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE]["Aliases"] = "별칭전용"
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "CLASSIFIED" and item.inferred_course_key == COURSE
        assert len(_auto_plans(system["state"])) == 1 and system["notion"].data_sources["synthetic-requests"] == []
        record = system["state"].get_classification_record(item.classification_record_id)
        assert json.loads(record.course_basis_json)["alias_dependent"] is True
    with _system(tmp_path / "duplicate", name=name, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE]["Aliases"] = "별칭전용"
        _course_rows(system["notion"])[COURSE_KEYS[3]]["Aliases"] = "별칭전용"
        worker.run_once()
        assert _item(system).classification_state == "HUMAN" and _auto_plans(system["state"]) == []
    with _system(tmp_path / "unreadable", name=name, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE]["Aliases"] = {"unexpected": "shape"}
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []


def test_the_closure_mark_never_replaces_a_duplicate_candidate_code(tmp_path: Path) -> None:
    # r12 R3: DUPLICATE_CANDIDATE arrives while the closure outcome is unknown.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original_update = notion.update_record
        done = {"once": False}

        def update_record(data_source_id: str, page_id: str, properties):
            status = ((properties.get("Request Status") or {}).get("status") or {}).get("name")
            if data_source_id == "synthetic-requests" and status == "Auto Resolved" and not done["once"]:
                done["once"] = True
                state.update_intake_item(_item(system).intake_id, status="NEEDS_INPUT",
                                         last_error_code="DUPLICATE_CANDIDATE", last_error="late duplicate")
                return notion.read_record(data_source_id, page_id)  # the write is lost
            return original_update(data_source_id, page_id, properties)
        notion.update_record = update_record  # type: ignore[method-assign]
        worker.run_once()
        assert _item(system).last_error_code == "DUPLICATE_CANDIDATE"
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert _item(system).last_error_code == "DUPLICATE_CANDIDATE"
    finally:
        system_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# P-B2a FINAL r13 findings
# ---------------------------------------------------------------------------
def test_an_unresolved_closure_freezes_reclassification_and_new_requests(tmp_path: Path) -> None:
    # r13 R1: closure A is unresolved, the classification input changes (revision B).
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        _drop_first_auto_resolved_write(notion)
        worker.run_once()
        receipt = next(r for r in state.list_request_receipts())
        assert state.get_auto_resolve_intent(receipt.request_key).state == "PENDING"
        records_before = len(state.list_classification_records_by_bytes(hashlib.sha256(RAW).hexdigest()))
        requests_before = len(notion.data_sources["synthetic-requests"])
        # revision B would be created now: the intake is frozen instead
        original_recover = worker._recover_auto_resolve_intents
        worker._recover_auto_resolve_intents = lambda: None  # type: ignore[method-assign]
        system["config"].courses[1].aliases = ["다른별칭"]
        item = _item(system)
        state.update_intake_item(item.intake_id, status="NEEDS_INPUT", last_error_code=None, last_error=None)
        worker.run_once()
        assert len(_auto_plans(state)) == 1
        assert len(state.list_classification_records_by_bytes(hashlib.sha256(RAW).hexdigest())) == records_before
        assert len(notion.data_sources["synthetic-requests"]) == requests_before
        assert worker._request_creation_allowed(_item(system)) is False
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(receipt.request_key)
        # the human changes the Draft; recovery then supersedes every live AUTO plan of the intake
        worker._recover_auto_resolve_intents = original_recover  # type: ignore[method-assign]
        _assign_request(notion)["Course"] = ["synthetic-course-page-0"]
        since = len(notion.events)
        worker.run_once()
        assert state.get_auto_resolve_intent(receipt.request_key).state == "ABORTED"
        assert all(p["status"] != "AUTO_PENDING" for p in _auto_plans(state))
        assert _request_writes(notion, since) == []
    finally:
        system_cm.__exit__(None, None, None)


def test_the_recorded_course_basis_names_the_alias_source(tmp_path: Path) -> None:
    # r13 R2
    name = "2026.09.10_별칭전용_2주차.md"
    with _system(tmp_path / "notion", name=name, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _course_rows(system["notion"])[COURSE]["Aliases"] = "별칭전용"
        worker.run_once()
        record = system["state"].get_classification_record(_item(system).classification_record_id)
        basis = json.loads(record.course_basis_json)
        assert basis["type"] == "notion_alias" and basis["alias_dependent"] is True
    with _system(tmp_path / "config", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        record = system["state"].get_classification_record(_item(system).classification_record_id)
        assert json.loads(record.course_basis_json)["type"] == "config_alias"


def test_disabling_leaves_a_p_b1_classified_item_without_auto_plan_untouched(tmp_path: Path) -> None:
    # r13 R3: CLASSIFIED written by P-B1 (never an AUTO plan) is not modified when the feature is off.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system, profile=None)
        _complete_calendar(system["state"])
        worker.run_once()
        state = system["state"]
        item = _item(system)
        state.update_intake_item(item.intake_id, classification_state="CLASSIFIED")
        assert _auto_plans(state) == []
        system["config"].intake.classification.enabled = False
        worker.run_once()
        assert _item(system).classification_state == "CLASSIFIED"
        released = [e for e in state.list_intake_stage_events(item.intake_id) if e.get("stage") == "auto_authority_released"] \
            if hasattr(state, "list_intake_stage_events") else []
        assert released == []


def test_closing_a_plan_hands_the_item_back_in_the_same_transaction(tmp_path: Path) -> None:
    # Self-audit sweep: plan close and the item's classification_state are one commit.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state = system["state"]
        assert _item(system).classification_state == "CLASSIFIED"
        plan = _auto_plans(state)[0]
        state.reconcile_intake_plan(plan["plan_revision"], "TEST")
        assert _item(system).classification_state == "HUMAN"
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"


# ---------------------------------------------------------------------------
# P-B2a FINAL r14 findings
# ---------------------------------------------------------------------------
def test_a_canvas_binding_with_an_intake_row_is_still_duplicate_evidence(tmp_path: Path) -> None:
    # r14 R1: file A has an intake row of *different* current bytes, but its canonical Canvas
    # binding still records the transcript's bytes.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _second_file(system, name="a.md", raw=b"completely different content here", md5=True)
        system["state"].record_canvas_drive_binding(
            drive_file_id="synthetic-other", canvas_course_id=1, resource_kind="assignment", resource_id="as-1",
            observation_revision=1, attachment_id="att-1", attachment_filename="a.md", attachment_size=len(RAW),
            byte_sha256=hashlib.sha256(RAW).hexdigest())
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "HUMAN" and _auto_plans(system["state"]) == []
        assert "DUPLICATE_CONTENT" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]


def test_a_session_date_range_is_not_a_single_day(tmp_path: Path) -> None:
    # r14 R2
    cases = {"range": ({"start": "2026-09-10", "end": "2026-09-12"}, "HUMAN"),
             "same-day": ({"start": "2026-09-11", "end": "2026-09-11"}, "CLASSIFIED"),
             "open-end": ({"start": "2026-09-11", "end": None}, "CLASSIFIED"),
             "no-start": ({"end": "2026-09-10"}, "HUMAN")}
    for label, (value, expected) in cases.items():
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            system["notion"].data_sources["synthetic-sessions"][0]["Date"] = value
            worker.run_once()
            assert _item(system).classification_state == expected, label


def test_rollback_never_acts_on_a_non_boolean_checkbox(tmp_path: Path) -> None:
    # r14 R3: Submitted is None, a string or missing when the rollback is about to write.
    for label, value in (("none", None), ("string", "true"), ("missing", "MISSING")):
        system_cm, system = _pre_v2_draft(tmp_path / label)
        try:
            worker = _enable(system)
            _complete_calendar(system["state"])
            notion, state = system["notion"], system["state"]

            def corrupt(page_id: str, v=value, n=notion) -> None:
                for page in n.data_sources["synthetic-requests"]:
                    if page["id"] == page_id:
                        page["Course"] = ["synthetic-course-page-0"]
                        if v == "MISSING":
                            page.pop("Submitted", None)
                        else:
                            page["Submitted"] = v
            _landing_hook(notion, corrupt)
            since = len(notion.events)
            worker.run_once()
            receipt = next(r for r in state.list_request_receipts())
            intent = state.get_auto_resolve_intent(receipt.request_key)
            assert intent.state == "PENDING", label
            assert _assign_request(notion)["Request Status"] == "Auto Resolved", label
            assert len(_request_writes(notion, since)) == 1, label  # only the original closure write
            assert state.get_auto_resolve_rollback(intent.intent_id) is None or \
                state.get_auto_resolve_rollback(intent.intent_id)["state"] == "PENDING", label
        finally:
            system_cm.__exit__(None, None, None)


def test_the_first_closure_write_requires_auto_to_be_usable_right_now(tmp_path: Path) -> None:
    # r14 R4: the feature is switched off between stage A and the first write.
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        notion, state = system["notion"], system["state"]
        original = worker._judge_human_requests
        flipped = {"done": False}

        def judge(*args, **kwargs):
            result = original(*args, **kwargs)
            if not flipped["done"]:
                flipped["done"] = True
                system["config"].intake.classification.enabled = False
            return result
        worker._judge_human_requests = judge  # type: ignore[method-assign]
        since = len(notion.events)
        worker.run_once()
        assert _request_writes(notion, since) == []
        assert _auto_plans(state)[0]["status"] == "RECONCILE_REQUIRED"
        assert _assign_request(notion)["Request Status"] == "Draft"
        assert _item(system).classification_state == "HUMAN"
    finally:
        system_cm.__exit__(None, None, None)
