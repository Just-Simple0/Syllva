"""P-B2b: stage B promotion, AUTO jobs and execution (plan §3.4, §3.7, §6.1)."""
from __future__ import annotations

import hashlib
from dataclasses import replace
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
    COURSE_KEYS,
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
        # r1 R1: repeated ticks never move a finished item back to PLANNED
        assert _item(system).status == "ORGANIZED" and _jobs(system)[0].completed_at


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
        # the read-only state used by retrieval marks it as an AUTO product (exposure gate, plan §9 P-B)
        from uls.state.reader import ReadOnlyState

        reader = ReadOnlyState(system["state"].db_path)
        assert reader.is_v2_material(material["ID"]) is True
        assert reader.is_v2_material("COMP319-M99") is False


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


def test_only_our_own_session_is_an_own_effect(tmp_path: Path) -> None:
    # E9/H1 + r1 R3: our proven Session passes; an extra same-day Session or a Session we cannot prove
    # we created is refused and parks the plan.
    for label in ("own", "extra-session", "unproven"):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            worker.run_once()
            item = _item(system)
            plan = system["state"].get_intake_plan(item.plan_revision)
            context = worker._auto_context(item, plan, worker._auto_record_for_item(item), job_id=None)
            workspace = worker._workspace_for_item(item)
            if label == "extra-session":
                _seed_session(system["notion"], "TEST102-S77")
            if label == "unproven":
                from uls.intake.worker import INTAKE_SESSION_OPERATION

                op_key = worker._plan_operation_key(INTAKE_SESSION_OPERATION, item, plan, workspace, context, target_id=None)
                with system["state"]._transaction(immediate=True) as connection:
                    connection.execute("DELETE FROM provider_write_attempts WHERE operation_key = ?", (op_key,))
            if label == "own":
                worker._session_point_of_use(context, workspace)  # must not raise
            else:
                with pytest.raises(IntakeReconcileRequired):
                    worker._session_point_of_use(context, workspace)
                assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"


def test_a_session_that_changed_date_or_course_before_the_pointer_write_is_refused(tmp_path: Path) -> None:
    # r1 R4
    for label, change in (("date", ("Date", "2026-09-11")), ("course", ("Course", ["synthetic-course-page-0"]))):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            _seed_session(system["notion"], "TEST102-S01")
            original = worker._session_pointer_gate

            def gate(context, page, derivative_file_id, _o=original, _c=change, _sy=system):
                _sy["notion"].data_sources["synthetic-sessions"][0][_c[0]] = _c[1]
                return _o(context, page, derivative_file_id)
            worker._session_pointer_gate = gate  # type: ignore[method-assign]
            worker.run_once()
            assert not system["notion"].data_sources["synthetic-sessions"][0].get("Normalized Transcript"), label
            assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED", label


def test_a_finished_job_is_never_duplicated_or_regressed_by_repair(tmp_path: Path) -> None:
    # r1 R1/R2: the repair loop sees a PLANNED plan whose job exists under another key.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        state = system["state"]
        (job,) = _jobs(system)
        with state._transaction(immediate=True) as connection:
            connection.execute("UPDATE jobs SET job_key = ? WHERE id = ?", ("sha256:" + "a" * 64, job.id))
        worker._promote_ready_plans()
        worker._promote_ready_plans()
        assert len(_jobs(system)) == 1 and _item(system).status == "ORGANIZED"
        # the database itself refuses a second AUTO job for one plan
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError), state._transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO jobs(id, job_key, operation, stage, status, created_at, updated_at, plan_revision, plan_authority) "
                "VALUES ('dup', ?, 'intake_session', 'intake', 'PENDING', 'x', 'x', ?, 'AUTO_CLASSIFICATION')",
                ("sha256:" + "b" * 64, job.plan_revision))


def test_a_source_moved_between_binding_and_the_move_is_not_an_own_effect(tmp_path: Path) -> None:
    # r1 R5: status REGISTERED alone never opens the parent exception.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._auto_write_gate
        moved = {"done": False}

        def gate(context, workspace, *, entry=False):
            if _item(system).status == "REGISTERED" and not moved["done"]:
                moved["done"] = True
                drive = system["drive"]
                drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], parents=("synthetic-course-0",))
            return original(context, workspace, entry=entry)
        worker._auto_write_gate = gate  # type: ignore[method-assign]
        worker.run_once()
        assert moved["done"]
        assert _item(system).status != "ORGANIZED"
        assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"
        assert system["drive"].files[system["source_id"]].parents == ("synthetic-course-0",)  # never moved by us


def test_job_record_and_workspace_must_agree_on_the_course(tmp_path: Path) -> None:
    # r1 H1
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._promote_ready_plans

        def promote_then_corrupt():
            original()
            with system["state"]._transaction(immediate=True) as connection:
                connection.execute("UPDATE jobs SET course_key = ?", (COURSE_KEYS[0],))
        worker._promote_ready_plans = promote_then_corrupt  # type: ignore[method-assign]
        worker.run_once()
        assert _sessions(system) == [] and _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"


def _retry_after_failure(system, worker, status: str) -> None:
    """Simulate the operator/worker re-running a failed job: item and job are made runnable again."""

    state = system["state"]
    item = _item(system)
    state.update_intake_item(item.intake_id, status=status, last_error_code=None, last_error=None)
    with state._transaction(immediate=True) as connection:
        connection.execute("UPDATE jobs SET status = 'PENDING', completed_at = NULL, error_class = NULL, last_error = NULL")


def test_a_source_without_md5_is_reproven_before_every_write(tmp_path: Path) -> None:
    # r2 R1: no MD5 -> SHA-256 of the downloaded bytes is compared at each write, not only at the entry.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        drive = system["drive"]
        drive.files[system["source_id"]] = replace(drive.files[system["source_id"]], md5_checksum=None)
        worker = _enable(system)
        _complete_calendar(system["state"])
        original = worker._auto_write_gate
        calls = {"n": 0}

        def gate(context, workspace, *, entry=False):
            calls["n"] += 1
            if calls["n"] == 3:  # after the first writes: same length, different content
                drive.contents[system["source_id"]] = b"X" * len(RAW)
            return original(context, workspace, entry=entry)
        worker._auto_write_gate = gate  # type: ignore[method-assign]
        worker.run_once()
        assert _item(system).status != "ORGANIZED"
        assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED"
        assert system["state"].get_job(_jobs(system)[0].id).voided_at is not None


def test_a_second_session_before_the_pointer_write_is_refused(tmp_path: Path) -> None:
    # r2 R2: NEW (our Session exists, a human adds another one) and EXISTING (a second Session appears).
    for label in ("new", "existing"):
        with _system(tmp_path / label, name=MATCHED_NAME, raw=RAW) as system:
            worker = _enable(system)
            _complete_calendar(system["state"])
            if label == "existing":
                _seed_session(system["notion"], "TEST102-S01")
            original = worker._session_pointer_gate

            def gate(context, page, derivative_file_id, _o=original, _sy=system):
                _seed_session(_sy["notion"], "TEST102-S88")
                return _o(context, page, derivative_file_id)
            worker._session_pointer_gate = gate  # type: ignore[method-assign]
            worker.run_once()
            first = system["notion"].data_sources["synthetic-sessions"][0]
            assert not first.get("Normalized Transcript"), label
            assert _auto_plans(system["state"])[0]["status"] == "RECONCILE_REQUIRED", label


def test_a_response_lost_session_create_is_proven_by_a_read_and_resumed(tmp_path: Path) -> None:
    # r2 R4 (A): Notion created the Session but the response was lost.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["notion"].drop_next_create_response = False
        original_create = system["notion"].create_record
        lost = {"done": False}

        def create_record(data_source_id, properties):
            row = original_create(data_source_id, properties)
            if data_source_id == "synthetic-sessions" and not lost["done"]:
                lost["done"] = True
                raise ConnectionError("response lost after a successful create")
            return row
        system["notion"].create_record = create_record  # type: ignore[method-assign]
        worker.run_once()
        assert len(_sessions(system)) == 1 and _item(system).status != "ORGANIZED"
        assert _auto_plans(system["state"])[0]["status"] == "PLANNED"  # not parked: the effect is still ours to prove
        _retry_after_failure(system, worker, "PLANNED")
        worker.run_once()
        assert len(_sessions(system)) == 1  # no duplicate Session
        assert _item(system).status == "ORGANIZED" and _sessions(system)[0].get("Normalized Transcript")


def test_a_response_lost_drive_move_is_proven_by_a_read_before_it_is_judged(tmp_path: Path) -> None:
    # r2 R4 (B): the provider moved the file but the response (and so READBACK_OK) was lost.  The gate
    # proves the move from a read (same plan, file ID, destination, private) instead of calling it external.
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["drive"].drop_next_move_response = True
        worker.run_once()
        state = system["state"]
        item = _item(system)
        assert item.status != "ORGANIZED"
        assert system["drive"].files[system["source_id"]].parent_id != "synthetic-upload"  # it did move
        plan = state.get_intake_plan(item.plan_revision)
        context = worker._auto_context(item, plan, worker._auto_record_for_item(item), job_id=None)
        state.update_intake_item(item.intake_id, status="REGISTERED", last_error_code=None, last_error=None)
        assert state.get_intake_plan(plan.plan_revision).status == "PLANNED"
        worker._auto_write_gate(context, worker._workspace_for_item(item))  # must not raise
        move_attempts = [
            a for a in (state.get_provider_write_attempt(k) for k in _move_keys(state)) if a is not None]
        assert move_attempts and all(a.response_state == "READBACK_OK" for a in move_attempts)
    # without any recorded attempt the same parent is an external change
    with _system(tmp_path / "foreign", name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["drive"].drop_next_move_response = True
        worker.run_once()
        state = system["state"]
        item = _item(system)
        plan = state.get_intake_plan(item.plan_revision)
        context = worker._auto_context(item, plan, worker._auto_record_for_item(item), job_id=None)
        state.update_intake_item(item.intake_id, status="REGISTERED", last_error_code=None, last_error=None)
        with state._transaction(immediate=True) as connection:
            connection.execute("DELETE FROM provider_write_attempts WHERE operation LIKE '%move%'")
        with pytest.raises(IntakeReconcileRequired):
            worker._auto_write_gate(context, worker._workspace_for_item(item))
        assert state.get_intake_plan(plan.plan_revision).status == "RECONCILE_REQUIRED"


def _move_keys(state) -> list[str]:
    with state._transaction() as connection:
        return [row["operation_key"] for row in connection.execute(
            "SELECT operation_key FROM provider_write_attempts WHERE operation LIKE '%move%'")]


def test_the_claimed_job_tuple_itself_must_match_the_stored_job(tmp_path: Path) -> None:
    # r2 H1
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once(process=False)
        worker._promote_ready_plans()
        item = _item(system)
        job = system["state"].claim_job(_jobs(system)[0].id)
        forged = replace(job, plan_revision="other-plan")
        with pytest.raises(IntakeReconcileRequired):
            worker._process_item_unlocked(item.intake_id, claimed=forged)
        assert _sessions(system) == []
