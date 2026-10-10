"""P-B3c: AutoResolved terminal scan (plan §3.4): a human change of a system-closed request
becomes exactly one new HUMAN generation; the terminal key stays terminal."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _enable
from tests.integration.test_intake_worker_classification_pb2a import (
    _auto_plans,
    _complete_calendar,
    _item,
    _pre_v2_draft,
)
from tests.integration.test_intake_worker_preview import _assign_request

pytestmark = pytest.mark.integration


@contextmanager
def _closed_request(tmp_path: Path):
    """AUTO ran to the end: the blank Draft is Auto Resolved and the item is ORGANIZED."""

    system_cm, system = _pre_v2_draft(tmp_path)
    try:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.run_once()
        assert _assign_request(system["notion"])["Request Status"] == "Auto Resolved"
        assert _item(system).status == "ORGANIZED"
        yield system, worker
    finally:
        system_cm.__exit__(None, None, None)


def _pages(system):
    return [r for r in system["notion"].data_sources["synthetic-requests"] if r["Request Type"] == "ASSIGN_COURSE"]


def _closed_page(system):
    return next(p for p in _pages(system) if p["Request Status"] == "Auto Resolved")


def test_an_untouched_closed_request_creates_nothing(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        def writes():
            return [e for e in system["notion"].events if e[0] in {"create", "update"}]

        before = len(writes())
        for _ in range(3):
            worker.run_once()
        assert len(writes()) == before and len(_pages(system)) == 1


def test_a_human_change_creates_exactly_one_new_generation(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        state = system["state"]
        sessions = len(system["notion"].data_sources["synthetic-sessions"])
        old = _closed_page(system)
        old_key = old["Request Key"]
        old["Course"] = ["synthetic-course-page-0"]
        for _ in range(3):
            worker.run_once()
        assert len(_pages(system)) == 2
        new = next(p for p in _pages(system) if p["Request Key"] != old_key)
        assert new["Request Status"] == "Draft" and new["Result Reference"] == f"AUTO_SUPERSEDED:{old_key}"
        record = state.get_request_generation(new["Request Key"], _item(system).intake_id)
        assert record["derivation_version"] == "V2" and record["superseded_request_key"] == old_key
        assert state.get_request_receipt(old_key).state == "AutoResolved"       # the terminal key stays terminal
        assert len(system["notion"].data_sources["synthetic-sessions"]) == sessions     # no entity touched
        assert _item(system).status == "ORGANIZED"
        assert all(p["status"] != "AUTO_PENDING" for p in _auto_plans(state))


def test_the_same_human_change_survives_a_restart_without_a_duplicate(tmp_path: Path) -> None:
    from uls.config.credentials import ResolvedCredentials
    from uls.intake.identity import provider_binding_id
    from uls.runtime import build_intake_worker

    with _closed_request(tmp_path) as (system, worker):
        _closed_page(system)["Cancelled"] = True
        worker.run_once()
        restarted = build_intake_worker(
            system["config"], ResolvedCredentials({}), state=system["state"], drive=system["drive"],
            notion=system["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2")
        restarted.run_once()
        restarted.run_once()
        assert len(_pages(system)) == 2


def test_only_a_human_change_counts_not_system_drift(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        page = _closed_page(system)
        page["Request Status"] = "Draft"            # identity/status drift, USER fields untouched
        worker.run_once()
        assert len(_pages(system)) == 1
        page["Request Status"] = "Auto Resolved"
        page["Request Key"] = "forged"
        worker.run_once()
        assert len(_pages(system)) == 1


def test_a_second_change_marks_the_open_generation_instead_of_creating_another(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        old = _closed_page(system)
        old["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        new = next(p for p in _pages(system) if p["Request Key"] != old["Request Key"])
        before = {k: new.get(k) for k in ("Course", "Kind", "Submitted", "Cancelled", "Request Status")}
        old["Kind"] = "TRANSCRIPT"
        worker.run_once()
        worker.run_once()
        assert len(_pages(system)) == 2 and new["Error"] == "TERMINAL_CHANGED_AGAIN"
        assert {k: new.get(k) for k in before} == before


def test_the_scan_runs_with_auto_switched_off(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        system["config"].intake.classification.enabled = False
        _closed_page(system)["Submitted"] = True
        worker.run_once()
        assert len(_pages(system)) == 2
        assert [p for p in _auto_plans(system["state"]) if p["status"] == "AUTO_PENDING"] == []


def test_the_circular_cursor_revisits_a_receipt_after_a_full_cycle(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        worker.run_once()
        worker.run_once()                           # a full cycle over the single receipt completed
        assert system["state"].get_classification_cursor("auto_resolved_scan_cursor")
        _closed_page(system)["Course"] = ["synthetic-course-page-0"]
        worker.run_once()
        assert len(_pages(system)) == 2


# --- AUTO -> HUMAN hand-over (plan §3.4 r8 R3, r10 R2) ------------------------------------

AUTO_COURSE_PAGE = "synthetic-course-page-1"      # the course the automatic classification chose


def _new_generation(system, worker, **edit):
    """A human edits the closed request; the scan opens the next generation; the human fills it."""

    old = _closed_page(system)
    old["Course"] = ["synthetic-course-page-0"]
    worker.run_once()
    new = next(p for p in _pages(system) if p["Request Key"] != old["Request Key"])
    new.update(edit)
    return new


def _state_snapshot(system):
    notion = system["notion"]
    return (
        len(notion.data_sources["synthetic-sessions"]), len(notion.data_sources["synthetic-materials"]),
        len(system["state"].list_jobs()), _item(system).status, len(system["state"].list_intake_plans()),
    )


def _details_pages(system):
    return [r for r in system["notion"].data_sources["synthetic-requests"] if r["Request Type"] == "FILE_DETAILS"]


def test_a_matching_course_keeps_the_item_organized_and_asks_only_for_the_details(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        before = _state_snapshot(system)
        new = _new_generation(system, worker, Course=[AUTO_COURSE_PAGE], Submitted=True)
        worker.claim_request(new["Request Key"])
        assert new["Request Status"] == "Applied" and _item(system).status == "ORGANIZED"
        assert len(_details_pages(system)) == 1 and _state_snapshot(system) == before


def test_a_different_course_is_reconcile_and_creates_nothing(tmp_path: Path) -> None:
    from uls.intake.worker import IntakeReconcileRequired

    with _closed_request(tmp_path) as (system, worker):
        before = _state_snapshot(system)
        new = _new_generation(system, worker, Course=["synthetic-course-page-0"], Submitted=True)
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(new["Request Key"])
        assert _details_pages(system) == [] and _state_snapshot(system) == before


def _fill_details(system, worker, **override):
    new = _new_generation(system, worker, Course=[AUTO_COURSE_PAGE], Submitted=True)
    worker.claim_request(new["Request Key"])
    details = _details_pages(system)[0]
    fields = {"Course": [AUTO_COURSE_PAGE], "Kind": "TRANSCRIPT", "Actual Date": {"start": "2026-09-10", "end": None},
              "Session Mode": "NEW", "Submitted": True}
    fields.update(override)
    details.update(fields)
    return details


def test_a_matching_complete_result_is_adopted_without_any_new_work(tmp_path: Path) -> None:
    with _closed_request(tmp_path) as (system, worker):
        details = _fill_details(system, worker)
        before = _state_snapshot(system)
        worker.claim_request(details["Request Key"])
        assert details["Request Status"] == "Applied" and details["Result Reference"] == "TEST102-S01"
        assert _state_snapshot(system) == before                      # no entity, job, plan or state change
        receipt = system["state"].get_request_receipt(details["Request Key"])
        assert receipt.state == "Applied"
        (record,) = system["state"].handovers_to_plan(receipt.plan_revision)
        assert list(json.loads(record["adopted_effects_json"])) == ["SESSION"]
        worker.run_once()
        assert _state_snapshot(system) == before


@pytest.mark.parametrize("override", [
    {"Actual Date": {"start": "2026-09-17", "end": None}},
    {"Course": ["synthetic-course-page-0"]},
])
def test_a_human_request_that_differs_from_the_automatic_effects_is_reconcile(tmp_path: Path, override) -> None:
    from uls.intake.worker import IntakeReconcileRequired

    with _closed_request(tmp_path) as (system, worker):
        details = _fill_details(system, worker, **override)
        before = _state_snapshot(system)
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(details["Request Key"])
        assert _state_snapshot(system) == before and details["Request Status"] != "Applied"


def test_an_unverifiable_effect_blocks_the_hand_over(tmp_path: Path) -> None:
    from uls.intake.worker import IntakeReconcileRequired

    with _closed_request(tmp_path) as (system, worker):
        details = _fill_details(system, worker)
        system["notion"].data_sources["synthetic-sessions"].clear()      # the Session vanished
        before = _state_snapshot(system)
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(details["Request Key"])
        assert _state_snapshot(system) == before


def test_the_gate_refuses_a_different_kind_before_anything_else(tmp_path: Path) -> None:
    from dataclasses import replace

    from uls.intake.requests import RequestInput
    from uls.intake.worker import IntakeReconcileRequired

    with _closed_request(tmp_path) as (system, worker):
        item = _item(system)
        workspace = worker._workspace_for_item(item)
        request = RequestInput("k", "FILE_DETAILS", intake_ids=(item.intake_id,), course_key=item.inferred_course_key,
                               kind="TRANSCRIPT", actual_date="2026-09-10", session_mode="NEW")
        assert worker._handover_gate(item, request, workspace).mode == "COMPLETE"
        with pytest.raises(IntakeReconcileRequired):
            worker._handover_gate(item, replace(request, kind="SUPPLEMENT", actual_date=None, session_mode=None,
                                                material_role="Supplementary"), workspace)


def _interrupted_auto_run(system, worker):
    """AUTO created the Session, then a human opened a request: AUTO stops, nothing is deleted."""

    from tests.integration.test_intake_worker_classification_pb2a import MATCHED_NAME  # noqa: F401

    original = worker._auto_write_gate
    calls = {"n": 0}

    def gate(context, workspace, *, entry=False):
        calls["n"] += 1
        if calls["n"] == 3:
            worker._create_input_request_with_context(
                _item(system), workspace, request_type="ASSIGN_COURSE", target_snapshot=None,
                layout_context=worker._fresh_layout_context(worker._run_layout_workspaces()))
        return original(context, workspace, entry=entry)

    worker._auto_write_gate = gate
    worker.run_once()
    worker._auto_write_gate = original


def test_an_interrupted_automatic_run_is_continued_by_the_human_plan_without_duplicates(tmp_path: Path) -> None:
    from tests.integration.test_intake_worker_classification_pb2a import MATCHED_NAME, RAW
    from tests.integration.test_intake_worker_preview import _system

    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        _interrupted_auto_run(system, worker)
        notion, state = system["notion"], system["state"]
        assert len(notion.data_sources["synthetic-sessions"]) == 1 and _item(system).status != "ORGANIZED"
        assign = _assign_request(notion)
        assign.update({"Course": [AUTO_COURSE_PAGE], "Submitted": True})
        worker.claim_request(assign["Request Key"])
        assert assign["Request Status"] == "Applied" and _item(system).status != "NEEDS_INPUT"
        details = _details_pages(system)[0]
        details.update({"Course": [AUTO_COURSE_PAGE], "Kind": "TRANSCRIPT", "Actual Date": {"start": "2026-09-10", "end": None},
                        "Session Mode": "NEW", "Submitted": True})
        worker.claim_request(details["Request Key"])
        receipt = state.get_request_receipt(details["Request Key"])
        assert state.handovers_to_plan(receipt.plan_revision)
        worker.run_once()
        assert len(notion.data_sources["synthetic-sessions"]) == 1          # the Session was adopted, not recreated
        (session,) = notion.data_sources["synthetic-sessions"]
        assert session["Recording Status"] == "Ready" and session.get("Normalized Transcript")
        assert _item(system).status == "ORGANIZED"
        human_jobs = [j for j in state.list_jobs() if j.plan_authority != "AUTO_CLASSIFICATION"]
        assert [str(getattr(j.status, "value", j.status)) for j in human_jobs] == ["READY"]
        worker.run_once()
        assert len(notion.data_sources["synthetic-sessions"]) == 1 and _item(system).status == "ORGANIZED"
        reservations = [r for r in (state.get_entity_reservation(intake_id=_item(system).intake_id, entity_kind="SESSION"),) if r]
        assert len(reservations) == 1 and reservations[0].plan_revision == receipt.plan_revision
