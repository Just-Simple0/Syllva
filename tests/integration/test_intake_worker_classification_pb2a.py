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
        with pytest.raises(RuntimeError):
            worker.run_once()
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
        with pytest.raises(RuntimeError):
            worker.run_once()
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
    with pytest.raises(RuntimeError):
        worker.run_once()
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
    # r4 #1: a Pending Session with a Normalized Transcript pointer, or bound to another
    # file, is occupied; a Session bound to this very file is an idempotent re-entry.
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
            if name == "own-binding":
                assert item.classification_state == "CLASSIFIED"
            else:
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
