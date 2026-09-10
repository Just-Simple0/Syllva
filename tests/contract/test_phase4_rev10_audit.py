"""Permanent Phase 4 rev10 post-marker reconciliation regressions."""

from __future__ import annotations

import json

import pytest
from tests.contract.test_phase4_rev6_recovery import (
    _apply,
    _approved_phase4,
    _restore_old_target,
)
from tests.fixtures.phase4 import COURSE_PAGE_ID

from uls.adapters.drive.binding import SourceBindingRecord
from uls.adapters.notion.base import (
    _PHASE4_APPLY_MARKER_PREFIX,
    QueueState,
)
from uls.domain.source_ref import SourceFingerprint, SourceRef


def _is_effect_observed_marker(patch) -> bool:
    value = patch.get("Last Error")
    if not isinstance(value, str) or not value.startswith(_PHASE4_APPLY_MARKER_PREFIX):
        return False
    payload = json.loads(value[len(_PHASE4_APPLY_MARKER_PREFIX) :])
    return payload.get("phase") == "effect_observed"


def _install_after_effect_marker(writer, callback):
    original_update = writer.update_properties
    fired = {"value": False}

    def update(target_db, entity_id, patch, **kwargs):
        result = original_update(target_db, entity_id, patch, **kwargs)
        if (
            not fired["value"]
            and target_db == "Automation Queue"
            and _is_effect_observed_marker(patch)
        ):
            fired["value"] = True
            callback()
        return result

    writer.update_properties = update
    return fired


def _marker_phase(writer, proposal) -> str:
    value = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert isinstance(value, str)
    assert value.startswith(_PHASE4_APPLY_MARKER_PREFIX)
    return json.loads(value[len(_PHASE4_APPLY_MARKER_PREFIX) :])["phase"]


def _add_exact_sibling(reader, operation: str) -> None:
    if operation == "create_usage":
        start_page, end_page = 1, 2
    else:
        start_page, end_page = 2, 2
    reader.material_usage["COMP319-S05"].append(
        {
            "ID": "MU:appeared-sibling",
            "Session": {"relation": [{"id": "COMP319-S05"}]},
            "Material": {"relation": [{"id": "COMP319-M03"}]},
            "Role": "Primary",
            "Start Page": start_page,
            "End Page": end_page,
            "Verified": False,
        }
    )


def _drift_source_binding(drive) -> None:
    for index, record in enumerate(drive.source_bindings.records):
        if record.entity_id == "COMP319-M03":
            drive.source_bindings.records[index] = SourceBindingRecord(
                entity_id=record.entity_id,
                normalized_source_url=record.normalized_source_url,
                derivative_ref=record.derivative_ref,
                source_ref=SourceRef(
                    record.source_ref.provider,
                    "material-origin-drift",
                    record.source_ref.web_url,
                ),
            )
            return
    raise AssertionError("material source binding fixture is missing")


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_target_restored_after_effect_marker_commit_stays_recoverable(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    fired = _install_after_effect_marker(
        writer,
        lambda: _restore_old_target(reader, operation),
    )

    first = _apply(reader, writer, drive, resolver, proposal)

    assert fired["value"] is True
    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPROVED.value
    assert "Applied At" not in row
    assert _marker_phase(writer, proposal) == "effect_observed"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert row["State"] == QueueState.APPROVED.value
    assert "Applied At" not in row
    assert _marker_phase(writer, proposal) == "effect_observed"


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize(
    "drift",
    ["exact-sibling", "course", "type", "source-binding", "fingerprint"],
)
def test_effect_marker_followup_rejects_target_dependency_drift(
    operation: str,
    drift: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)

    def mutate_after_marker() -> None:
        if drift == "exact-sibling":
            _add_exact_sibling(reader, operation)
        elif drift == "course":
            reader.courses[COURSE_PAGE_ID]["Course Key"] = "2026-1_COMP319-003"
        elif drift == "type":
            reader.materials["COMP319-M03"]["Type"] = "Textbook"
        elif drift == "source-binding":
            _drift_source_binding(drive)
        elif drift == "fingerprint":
            drive.fingerprints["material-m03"] = SourceFingerprint(
                2,
                "material-hash-drift",
            )
        else:
            raise AssertionError(f"unknown drift case: {drift}")

    fired = _install_after_effect_marker(writer, mutate_after_marker)

    first = _apply(reader, writer, drive, resolver, proposal)

    assert fired["value"] is True
    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPROVED.value
    assert "Applied At" not in row
    assert _marker_phase(writer, proposal) == "effect_observed"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert row["State"] == QueueState.APPROVED.value
    assert "Applied At" not in row
    assert _marker_phase(writer, proposal) == "effect_observed"


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_unchanged_apply_still_writes_target_once(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)

    first = _apply(reader, writer, drive, resolver, proposal)
    second = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPLIED
    assert first.mutated is True
    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_fresh_effect_marker_audit_retry_remains_audit_only(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert _marker_phase(writer, proposal) == "effect_observed"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None
