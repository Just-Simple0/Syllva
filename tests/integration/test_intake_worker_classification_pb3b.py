"""P-B3b: V2 request generations (plan §3.4): derivation, durable binding before create,
response-loss recovery and claim-time verification by derivation_version."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.integration.test_intake_worker_preview import _details_request, _system

from uls.intake.identity import derive_request_key, derive_request_revision, sha256_hex
from uls.intake.requests import RequestType, build_request_generation
from uls.intake.worker import IntakeReconcileRequired

pytestmark = pytest.mark.integration

BASE = {
    "provider": "google_drive", "provider_account_binding_id": "binding", "semester": "2026-2",
    "request_type": "ASSIGN_COURSE", "observation_refs": [("i1", "obs")], "config_fingerprint": "cfg",
    "target_snapshot_hash": "snap",
}


def test_legacy_derivation_is_unchanged_and_v2_is_a_distinct_domain() -> None:
    legacy_revision = derive_request_revision(**BASE)
    assert legacy_revision == sha256_hex([
        "intake.request-revision.v1", "google_drive", "binding", "2026-2", "ASSIGN_COURSE",
        [["i1", "obs"]], "cfg", "snap"])
    v2_revision = derive_request_revision(**BASE, superseded_request_key="old", request_generation=2)
    assert v2_revision != legacy_revision
    assert v2_revision == derive_request_revision(**BASE, superseded_request_key="old", request_generation=2)
    assert v2_revision != derive_request_revision(**BASE, superseded_request_key="old", request_generation=3)
    key_args = {k: BASE[k] for k in ("provider", "provider_account_binding_id", "semester", "request_type",
                                     "target_snapshot_hash")}
    legacy_key = derive_request_key(**key_args, request_revision_hash=legacy_revision, intake_ids=["i1"])
    v2_key = derive_request_key(**key_args, request_revision_hash=v2_revision, intake_ids=["i1"],
                                superseded_request_key="old", request_generation=2)
    assert legacy_key != v2_key and legacy_key != "old"


@pytest.mark.parametrize("extra", [
    {"superseded_request_key": "old"}, {"request_generation": 2},
    {"superseded_request_key": "", "request_generation": 2},
    {"superseded_request_key": "old", "request_generation": 0},
    {"superseded_request_key": "old", "request_generation": True},
])
def test_supersession_inputs_come_together_and_valid(extra) -> None:
    with pytest.raises(ValueError):
        derive_request_revision(**BASE, **extra)


def test_build_request_generation_carries_the_supersession() -> None:
    kwargs = {
        "provider": "google_drive", "provider_account_binding_id": "binding", "semester": "2026-2",
        "request_type": "ASSIGN_COURSE", "observation_refs": [("i1", "obs")], "config_fingerprint": "cfg",
        "target_snapshot": {"intake_id": "i1"}, "intake_ids": ["i1"],
    }
    legacy = build_request_generation(**kwargs)
    v2 = build_request_generation(**kwargs, superseded_request_key=legacy.request_key, request_generation=2)
    assert legacy.superseded_request_key is None and v2.superseded_request_key == legacy.request_key
    assert v2.request_key != legacy.request_key and v2.request_generation == 2


def _first_request(system):
    worker, state = system["worker"], system["state"]
    worker.run_once()
    item = state.list_intake_items()[0]
    return worker, state, item, item.pending_request_key


def _assign_pages(notion):
    return [r for r in notion.data_sources["synthetic-requests"] if r["Request Type"] == "ASSIGN_COURSE"]


def test_superseding_request_is_one_new_draft_bound_before_create(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker, state, item, old_key = _first_request(system)
        receipt = worker.create_input_request(
            item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        assert receipt.request_key != old_key and len(_assign_pages(system["notion"])) == 2
        page = next(r for r in _assign_pages(system["notion"]) if r["Request Key"] == receipt.request_key)
        assert page["Result Reference"] == f"AUTO_SUPERSEDED:{old_key}"
        assert page["Request Status"] == "Draft" and page["Submitted"] is False
        record = state.get_request_generation(receipt.request_key, item.intake_id)
        assert (record["derivation_version"], record["generation"], record["superseded_request_key"]) == ("V2", 2, old_key)
        assert state.get_request_generation(old_key, item.intake_id)["derivation_version"] == "LEGACY"
        again = worker.create_input_request(
            item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        assert again.request_key == receipt.request_key and len(_assign_pages(system["notion"])) == 2


def test_create_response_loss_recovers_the_same_generation_without_a_duplicate(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker, state, item, old_key = _first_request(system)
        system["notion"].drop_next_create_response = True
        with pytest.raises(Exception):  # noqa: B017 - the simulated provider outage
            worker.create_input_request(
                item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        assert len(_assign_pages(system["notion"])) == 2     # the create reached the provider
        pending = state.pending_request_generation(item.intake_id, "ASSIGN_COURSE")
        assert pending is None                                 # the key was bound before the create
        receipt = worker.create_input_request(
            item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        assert len(_assign_pages(system["notion"])) == 2
        assert receipt.provider_page_id is not None
        generations = [state.get_request_generation(k, item.intake_id) for k in (old_key, receipt.request_key)]
        assert [g["generation"] for g in generations] == [1, 2]


def test_only_a_proven_request_key_can_be_superseded(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker, _state, item, _old_key = _first_request(system)
        with pytest.raises(IntakeReconcileRequired):
            worker.create_input_request(
                item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key="not-a-key")
        assert len(_assign_pages(system["notion"])) == 1


def _submit_new(system, receipt):
    page = next(r for r in _assign_pages(system["notion"]) if r["Request Key"] == receipt.request_key)
    page.update({"Course": ["synthetic-course-page-0"], "Submitted": True})


def test_a_v2_generation_is_verified_by_its_derivation_and_claims(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker, _state, item, old_key = _first_request(system)
        receipt = worker.create_input_request(
            item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        _submit_new(system, receipt)
        worker.claim_request(receipt.request_key)
        assert _details_request(system["notion"]) is not None   # the HUMAN path continued


def test_a_v2_generation_whose_record_conflicts_is_reconciled_not_guessed(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        worker, _state, item, old_key = _first_request(system)
        receipt = worker.create_input_request(
            item.intake_id, request_type=RequestType.ASSIGN_COURSE.value, superseded_request_key=old_key)
        _submit_new(system, receipt)
        connection = sqlite3.connect(system["state"].path if hasattr(system["state"], "path") else tmp_path / "state.sqlite3")
        connection.execute(
            "UPDATE intake_request_generations SET superseded_request_key = 'forged' WHERE request_key = ?",
            (receipt.request_key,))
        connection.commit()
        connection.close()
        with pytest.raises(IntakeReconcileRequired):
            worker.claim_request(receipt.request_key)
        assert not [r for r in system["notion"].data_sources["synthetic-requests"] if r["Request Type"] == "FILE_DETAILS"]
