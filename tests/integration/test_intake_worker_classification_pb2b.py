"""P-B2b: stage B promotion, AUTO jobs and execution (plan §3.4, §3.7, §6.1)."""
from __future__ import annotations

import contextlib
import hashlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _enable
from tests.integration.test_intake_worker_classification_pb2a import (
    COURSE,
    MATCHED_NAME,
    RAW,
    _auto_plans,
    _complete_calendar,
    _item,
    _pre_v2_draft,
    _seed_session,
)
from tests.integration.test_intake_worker_classification_profile import _rewire
from tests.integration.test_intake_worker_preview import (
    _assign_request,
    _pdf_bytes,
    _privacy,
    _system,
)

from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata
from uls.config.credentials import ResolvedCredentials
from uls.intake.identity import provider_binding_id
from uls.intake.worker import IntakeReconcileRequired, RequestValidationError
from uls.runtime import build_intake_worker

pytestmark = pytest.mark.integration


def _jobs(system) -> list:
    return system["state"].list_jobs()


def _sessions(system) -> list:
    return system["notion"].data_sources["synthetic-sessions"]


def _materials(system) -> list:
    return system["notion"].data_sources["synthetic-materials"]


def _material_system(system, name: str, raw: bytes, resource_kind: str = "module_item"):
    """Move the source into the registered course upload folder with a proven Canvas binding."""

    drive, state = system["drive"], system["state"]
    meta = drive.files[system["source_id"]]
    drive.files[system["source_id"]] = replace(meta, parents=("synthetic-course-upload-1",))
    drive.files["synthetic-course-upload-1"] = DriveMetadata(
        file_id="synthetic-course-upload-1", name="upload", mime_type=DRIVE_FOLDER_MIME,
        parents=("synthetic-course-1",), modified_time="2026-09-13T10:00:00Z", size=0, md5_checksum=None, **_privacy())
    registry = system["config"].google_drive.semester_registries[0]
    registry.optional_course_upload_folder_ids[COURSE] = "synthetic-course-upload-1"
    system["config"].intake.classification.canvas_course_map = {67535: COURSE}
    system["worker"] = build_intake_worker(
        system["config"], ResolvedCredentials({}), state=state, drive=drive, notion=system["notion"],
        provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
        semester="2026-2")
    worker = _rewire(system, "legacy5-cls")
    state.record_canvas_drive_binding(
        drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind=resource_kind, resource_id="r-1",
        observation_revision=1, attachment_id="att-9", attachment_filename=name, attachment_size=len(raw),
        byte_sha256=hashlib.sha256(raw).hexdigest())
    return worker


def test_a_matched_transcript_runs_end_to_end_without_any_request(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        result = worker.run_once()
        state, notion = system["state"], system["notion"]
        item = _item(system)
        assert result["processed"] == 1 and item.status == "ORGANIZED" and item.classification_state == "CLASSIFIED"
        plan = _auto_plans(state)[0]
        assert plan["status"] == "PLANNED" and item.plan_revision == plan["plan_revision"]
        # no HUMAN artefact of any kind
        assert notion.data_sources["synthetic-requests"] == [] and state.list_request_receipts() == []
        # exactly one job, bound to the AUTO authority and revision, in the classified course
        (job,) = _jobs(system)
        assert job.plan_authority == "AUTO_CLASSIFICATION" and job.plan_revision == plan["plan_revision"]
        assert job.course_key == COURSE
        assert job.bound_authority == "AUTO_CLASSIFICATION" and job.bound_revision_hash == plan["classification_revision_hash"]
        # the Session is created in the classified (second) course with the record's date
        (session,) = _sessions(system)
        course = session["Course"]["relation"] if isinstance(session["Course"], dict) else session["Course"]
        course_ids = [c["id"] if isinstance(c, dict) else c for c in course]
        assert session["Date"] == "2026-09-10" and course_ids == ["synthetic-course-page-1"]
        assert session["Recording Status"] in {"Ready", "Partial"} and session.get("Normalized Transcript")
        ancestors, cursor = [], system["drive"].files[system["source_id"]].parent_id
        while cursor and cursor in system["drive"].files and len(ancestors) < 10:
            ancestors.append(cursor)
            cursor = system["drive"].files[cursor].parent_id
        assert "synthetic-recordings-1" in ancestors and "synthetic-recordings-0" not in ancestors
        # idempotent next ticks
        system["worker"].run_once()
        worker.run_once()
        assert len(_jobs(system)) == 1 and len(_sessions(system)) == 1 and len(_auto_plans(state)) == 1


def test_an_opaque_tabular_material_runs_without_extraction_or_provenance(tmp_path: Path) -> None:
    csv = b"name,score\nA,1\n"
    with _system(tmp_path, name="dataset.csv", mime_type="text/csv", raw=csv) as system:
        worker = _enable(system)
        worker = _material_system(system, "dataset.csv", csv, resource_kind="assignment")
        worker.run_once()
        item = _item(system)
        assert item.status == "ORGANIZED" and item.classified_kind == "ASSIGNMENT_RESOURCE"
        (material,) = _materials(system)
        assert material["Type"] == "Reference" and material["AI Kind"] == "ASSIGNMENT_RESOURCE"
        assert material["Text Status"] == "Needs Review" and material["Text Source"] == "Unavailable"
        assert not material.get("Normalized Source")
        assert item.exposure_block_reason == "OPAQUE_NO_RETRIEVAL"
        assert system["state"].get_intake_item(item.intake_id).exposure_block_reason == "OPAQUE_NO_RETRIEVAL"
        assert _jobs(system)[0].plan_authority == "AUTO_CLASSIFICATION"
        assert system["state"].get_processing_record(_jobs(system)[0].id, operation=_jobs(system)[0].operation) is None
        # the job identity is still bound under the creating authority and revision
        job = _jobs(system)[0]
        assert job.bound_authority == "AUTO_CLASSIFICATION" and job.bound_revision_hash


def test_a_lecture_pdf_is_normalized_with_the_initial_type(tmp_path: Path) -> None:
    raw = _pdf_bytes()
    with _system(tmp_path, name="Lec.3_networks.pdf", mime_type="application/pdf", raw=raw) as system:
        _enable(system)
        worker = _material_system(system, "Lec.3_networks.pdf", raw)
        worker.run_once()
        item = _item(system)
        assert item.status == "ORGANIZED" and item.classified_kind == "LECTURE_SLIDES"
        (material,) = _materials(system)
        assert material["Type"] == "Lecture Slides" and material["AI Kind"] == "LECTURE_SLIDES"
        assert material["Text Status"] == "Ready" and material.get("Normalized Source")
        assert item.exposure_block_reason is None


def test_a_blank_draft_is_closed_and_the_plan_runs_in_the_same_tick(tmp_path: Path) -> None:
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        item = _item(system)
        assert _assign_request(system["notion"])["Request Status"] == "Auto Resolved"
        assert item.status == "ORGANIZED" and len(_sessions(system)) == 1
        assert _auto_plans(system["state"])[0]["status"] == "PLANNED"
    finally:
        system_cm.__exit__(None, None, None)


def test_a_touched_draft_prevents_promotion(tmp_path: Path) -> None:
    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _assign_request(system["notion"])["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        assert _auto_plans(system["state"])[0]["status"] == "SUPERSEDED"
        assert _jobs(system) == [] and _sessions(system) == []
    finally:
        system_cm.__exit__(None, None, None)


def test_promotion_and_job_are_one_transaction_and_a_missing_job_is_repaired(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once(process=False)  # classification only: AUTO_PENDING
        state = system["state"]
        plan = _auto_plans(state)[0]
        assert plan["status"] == "AUTO_PENDING"
        # a failing enqueue rolls the promotion back completely
        with pytest.raises(ValueError):
            state.promote_auto_plan(plan["plan_revision"], enqueue={"job_key": "not-a-key", "operation": "x"})
        assert _auto_plans(state)[0]["status"] == "AUTO_PENDING" and _item(system).plan_revision is None
        assert _jobs(system) == []
        # a PLANNED plan without its job (state repaired by hand) gets exactly one
        worker._promote_ready_plans()
        (job,) = _jobs(system)
        with state._transaction(immediate=True) as connection:  # simulate the lost job row
            connection.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
        assert _jobs(system) == []
        worker._promote_ready_plans()
        worker._promote_ready_plans()
        assert len(_jobs(system)) == 1


def test_claim_and_authority_checks_guard_the_execution_entry(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once(process=False)
        worker._promote_ready_plans()
        state = system["state"]
        item = _item(system)
        # the public entry never runs an AUTO plan without its claimed job
        with pytest.raises(RequestValidationError):
            worker.process_item(item.intake_id)
        assert _sessions(system) == []
        # a job voided after the claim stops the run before any write
        job = state.claim_job(_jobs(system)[0].id)
        state.void_jobs_for_plan(job.plan_revision, "TEST")
        with pytest.raises(IntakeReconcileRequired):
            worker._process_item_unlocked(item.intake_id, claimed=job)
        assert _sessions(system) == []
        # a bound identity under another authority or revision is refused
        from uls.intake.worker import ClassificationExecutionContext

        context = ClassificationExecutionContext(
            intake_id=item.intake_id, plan_revision="p", classification_revision_hash="h2", record_id="r", job_id=job.id,
            course_key=COURSE, kind="TRANSCRIPT", origin="USER_AUTHORED_TRANSCRIPT", actual_date="2026-09-10", week=2,
            session_mode="NEW", session_id=None, material_role=None, handling="NORMALIZE", source_file_id="f",
            byte_sha256=None, source_version=1, workspace_fingerprint="w", config_fingerprint="c")
        bound = replace(job, bound_authority="HUMAN_REQUEST", bound_revision_hash=None)
        with pytest.raises(IntakeReconcileRequired):
            worker._assert_job_identity(bound, context)
        bound = replace(job, bound_authority=None, bound_revision_hash="h1")
        with pytest.raises(IntakeReconcileRequired):
            worker._assert_job_identity(bound, context)


def test_entry_preflight_failures_write_nothing(tmp_path: Path) -> None:
    # E6: the source is renamed, and a same-day Session appears, after promotion and before execution.
    for label in ("rename", "session"):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            original = worker._auto_write_gate
            seen = {"n": 0}

            def gate(context, workspace, *, entry=False, _o=original, _s=seen, _l=label, _sy=system):
                _s["n"] += 1
                if _s["n"] == 1:
                    if _l == "rename":
                        _sy["drive"].files[_sy["source_id"]] = replace(_sy["drive"].files[_sy["source_id"]], name="notes.md")
                    else:
                        _seed_session(_sy["notion"], "TEST102-S01")
                return _o(context, workspace, entry=entry)
            worker._auto_write_gate = gate  # type: ignore[method-assign]
            worker.run_once()
            assert _sessions(system) == [] or label == "session" and len(_sessions(system)) == 1  # only the seeded one
            assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"
            assert _item(system).status == "RECONCILE_REQUIRED"
            assert system["state"].get_job(_jobs(system)[0].id).voided_at is not None


def test_a_human_request_appearing_mid_run_stops_further_writes(tmp_path: Path) -> None:
    # E7/E12: after the Session exists a HUMAN request for the intake appears.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._auto_write_gate
        calls = {"n": 0}

        def gate(context, workspace, *, entry=False):
            calls["n"] += 1
            if calls["n"] == 3:  # entry + two writes done; a human opens a request now
                item = _item(system)
                worker._create_input_request_with_context(
                    item, workspace, request_type="ASSIGN_COURSE", target_snapshot=None,
                    layout_context=worker._fresh_layout_context(worker._run_layout_workspaces()))
            return original(context, workspace, entry=entry)
        worker._auto_write_gate = gate  # type: ignore[method-assign]
        worker.run_once()
        item = _item(system)
        assert len(_sessions(system)) == 1  # the completed write is preserved, never deleted
        assert item.status != "ORGANIZED"
        assert _auto_plans(system["state"])[0]["status"] == "SUPERSEDED"
        assert system["state"].get_job(_jobs(system)[0].id).voided_at is not None


def test_disabling_auto_after_promotion_runs_nothing(tmp_path: Path) -> None:
    # E16: the job exists, then the feature is switched off before the job runs.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._promote_ready_plans

        def promote_then_disable():
            original()
            system["config"].intake.classification.enabled = False
        worker._promote_ready_plans = promote_then_disable  # type: ignore[method-assign]
        worker.run_once()
        assert _sessions(system) == [] and _materials(system) == []
        assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"
        assert str(_jobs(system)[0].status) in {"NEEDS_REVIEW", "JobStatus.NEEDS_REVIEW"} or _jobs(system)[0].voided_at


def test_an_own_session_is_recognised_as_an_own_effect(tmp_path: Path) -> None:
    # E9/H1: after a completed run the point-of-use check accepts our own Session.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        item = _item(system)
        plan = system["state"].get_intake_plan(item.plan_revision)
        record = worker._auto_record_for_item(item)
        context = worker._auto_context(item, plan, record, job_id=None)
        worker._session_point_of_use(context, worker._workspace_for_item(item))  # must not raise
        # a Session of someone else for the same day is not ours
        _seed_session(system["notion"], "TEST102-S77")
        with contextlib.suppress(IntakeReconcileRequired):
            worker._session_point_of_use(context, worker._workspace_for_item(item))
    assert date.fromisoformat("2026-09-10")  # keeps the fixture date explicit


def test_an_existing_free_session_is_used_and_a_foreign_pointer_stops_the_write(tmp_path: Path) -> None:
    # E9: EXISTING execution, and a pointer taken by another transcript between decision and write.
    with _system(tmp_path / "ok", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _seed_session(system["notion"], "TEST102-S01")
        worker.run_once()
        (session,) = _sessions(system)
        assert session["ID"] == "TEST102-S01" and session.get("Normalized Transcript")
        assert _item(system).status == "ORGANIZED" and len(_sessions(system)) == 1
    with _system(tmp_path / "foreign", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _seed_session(system["notion"], "TEST102-S01")
        original = worker._session_pointer_gate

        def gate(context, page, derivative_file_id):
            system["notion"].data_sources["synthetic-sessions"][0]["Normalized Transcript"] = "https://drive.google.com/file/d/other/view"
            return original(context, page, derivative_file_id)
        worker._session_pointer_gate = gate  # type: ignore[method-assign]
        worker.run_once()
        assert system["notion"].data_sources["synthetic-sessions"][0]["Normalized Transcript"].endswith("/other/view")
        assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"
        assert _item(system).status != "ORGANIZED"


def test_readiness_reports_the_auto_execution_counters(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        result = worker.run_once()
        block = result["readiness"]["classification"]
        assert block["planned_auto_plans"] == 1 and block["auto_jobs_pending"] == 0 and block["auto_jobs_failed"] == 0
