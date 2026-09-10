"""Permanent Phase4 rev9 recovery regressions.

The marker lives in the existing Queue ``Last Error`` property.  These tests
make the process boundary explicit by constructing a fresh applier for every
recovery attempt.
"""

from __future__ import annotations

import json

import pytest
from tests.contract.test_phase4_rev6_recovery import (
    _apply,
    _approved_phase4,
    _restore_old_target,
)

from uls.adapters.notion import base
from uls.adapters.notion.base import (
    _PHASE4_APPLY_MARKER_PREFIX,
    QueueState,
    _phase4_apply_marker_text,
)
from uls.domain.approval_identity import canonical_semantics_from_queue
from uls.domain.errors import PolicyViolation


def _marker_payload(writer, proposal):
    value = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert isinstance(value, str)
    assert value.startswith(_PHASE4_APPLY_MARKER_PREFIX)
    return json.loads(value[len(_PHASE4_APPLY_MARKER_PREFIX) :])


def _set_desired(reader, operation: str) -> None:
    target = reader.material_usage["COMP319-S05"][0]
    if operation == "create_usage":
        target["Verified"] = True
    else:
        target.update({"Start Page": 2, "End Page": 2})


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_prepared_only_restart_never_attributes_human_desired_state(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    original = base._guarded_update

    def crash_before_dispatch(adapter, actor, target_db, entity_id, patch, **kwargs):
        if target_db == "Material Usage":
            raise SystemExit("process died before target dispatch")
        return original(adapter, actor, target_db, entity_id, patch, **kwargs)

    base._guarded_update = crash_before_dispatch
    try:
        with pytest.raises(SystemExit):
            _apply(reader, writer, drive, resolver, proposal)
    finally:
        base._guarded_update = original

    assert writer.target_mutations == 0
    assert _marker_payload(writer, proposal)["phase"] == "prepared"

    _set_desired(reader, operation)
    recovered = _apply(reader, writer, drive, resolver, proposal)

    assert recovered.state is QueueState.APPROVED
    assert recovered.mutated is False
    assert writer.target_mutations == 0
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPROVED.value
    assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))
    assert _marker_payload(writer, proposal)["phase"] == "prepared"


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_dispatch_then_crash_before_effect_durability_requires_reconciliation(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    original = base._guarded_update

    def crash_after_dispatch(adapter, actor, target_db, entity_id, patch, **kwargs):
        result = original(adapter, actor, target_db, entity_id, patch, **kwargs)
        if target_db == "Material Usage":
            raise SystemExit("process died before effect marker durability")
        return result

    base._guarded_update = crash_after_dispatch
    try:
        with pytest.raises(SystemExit):
            _apply(reader, writer, drive, resolver, proposal)
    finally:
        base._guarded_update = original

    assert writer.target_mutations == 1
    assert _marker_payload(writer, proposal)["phase"] == "prepared"

    recovered = _apply(reader, writer, drive, resolver, proposal)

    assert recovered.state is QueueState.APPROVED
    assert recovered.mutated is False
    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] == QueueState.APPROVED.value
    assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_effect_observed_marker_allows_fresh_audit_only_replay(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.fail_audit_once = True

    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    assert _marker_payload(writer, proposal)["phase"] == "effect_observed"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_mutate_then_raise_with_exact_desired_readback_proves_effect(operation: str) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.mutate_then_raise_target = True

    result = _apply(reader, writer, drive, resolver, proposal)

    assert result.state is QueueState.APPLIED
    assert result.mutated is True
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_trusted_no_effect_and_exact_old_snapshot_disarm_prepared_marker(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.raise_before_target = True

    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is True
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_generic_error_and_human_restored_old_snapshot_retain_prepared_marker(
    operation: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.mutate_then_raise_target = True
    original_read = drive.read_derived
    restored = False

    def read(ref):
        nonlocal restored
        if writer.target_mutations == 1 and not restored:
            _restore_old_target(reader, operation)
            restored = True
        return original_read(ref)

    drive.read_derived = read
    first = _apply(reader, writer, drive, resolver, proposal)

    assert restored is True
    assert first.state is QueueState.APPROVED
    assert first.mutated is False
    assert writer.target_mutations == 1
    assert _marker_payload(writer, proposal)["phase"] == "prepared"

    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPROVED
    assert second.mutated is False
    assert writer.target_mutations == 1
    assert _marker_payload(writer, proposal)["phase"] == "prepared"


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("mode", ["before_commit", "commit_then_raise"])
def test_effect_marker_write_failure_never_weakens_or_fakes_audit(
    operation: str,
    mode: str,
) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    original_update = writer.update_properties
    effect_marker_committed = False

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal effect_marker_committed
        if target_db == "Automation Queue" and isinstance(patch.get("Last Error"), str):
            payload = json.loads(
                patch["Last Error"][len(_PHASE4_APPLY_MARKER_PREFIX) :]
            ) if patch["Last Error"].startswith(_PHASE4_APPLY_MARKER_PREFIX) else {}
            if payload.get("phase") == "effect_observed":
                if mode == "before_commit":
                    raise TimeoutError("effect marker failed before commit")
                original_update(target_db, entity_id, patch, **kwargs)
                effect_marker_committed = True
                raise TimeoutError("effect marker committed before timeout")
        return original_update(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    first = _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 1
    row = writer.queue[proposal["Proposal ID"]]
    if mode == "before_commit":
        assert first.state is QueueState.APPROVED
        assert first.mutated is True
        assert _marker_payload(writer, proposal)["phase"] == "prepared"
        assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))
    else:
        assert effect_marker_committed is True
        assert first.state is QueueState.APPLIED
        assert first.mutated is True
        assert row.get("Last Error") is None


def test_effect_marker_commit_then_raise_with_unavailable_readback_stays_reconcilable() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    original_update = writer.update_properties
    original_find = writer.find_approval_rows
    readback_unavailable = False

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal readback_unavailable
        result = original_update(target_db, entity_id, patch, **kwargs)
        marker = patch.get("Last Error")
        if isinstance(marker, str) and marker.startswith(_PHASE4_APPLY_MARKER_PREFIX):
            payload = json.loads(marker[len(_PHASE4_APPLY_MARKER_PREFIX) :])
            if payload.get("phase") == "effect_observed":
                readback_unavailable = True
                raise TimeoutError("effect marker read-back unavailable")
        return result

    def find(proposal_id: str):
        if readback_unavailable:
            raise TimeoutError("Queue read-back unavailable")
        return original_find(proposal_id)

    writer.update_properties = update
    writer.find_approval_rows = find
    first = _apply(reader, writer, drive, resolver, proposal)

    assert first.state is QueueState.APPROVED
    assert first.mutated is True
    assert writer.target_mutations == 1
    assert _marker_payload(writer, proposal)["phase"] == "effect_observed"
    row = writer.queue[proposal["Proposal ID"]]
    assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))

    readback_unavailable = False
    second = _apply(reader, writer, drive, resolver, proposal)

    assert second.state is QueueState.APPLIED
    assert second.mutated is False
    assert writer.target_mutations == 1


@pytest.mark.parametrize("phase", [None, "future"])
def test_legacy_or_unknown_marker_phase_fails_closed(phase: str | None) -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4("create_usage")
    row = writer.queue[proposal["Proposal ID"]]
    payload = json.loads(
        _phase4_apply_marker_text(
            proposal["Proposal ID"],
            row["Proposal Type"],
            canonical_semantics_from_queue(row),
            {"Verified": True},
        )[len(_PHASE4_APPLY_MARKER_PREFIX) :]
    )
    if phase is None:
        payload.pop("phase")
    else:
        payload["phase"] = phase
    row["Last Error"] = _PHASE4_APPLY_MARKER_PREFIX + json.dumps(payload)

    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal)

    assert writer.target_mutations == 0
    assert row["State"] == QueueState.APPROVED.value
    assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))
