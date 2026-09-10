"""A snapshot is not proof that a failed provider write was never applied."""

import pytest
from tests.contract.test_phase4_rev6_recovery import (
    _apply,
    _approved_phase4,
    _restore_old_target,
)

from uls.adapters.notion.base import QueueState
from uls.domain.errors import ProviderUnavailableError, ProviderWriteNotAppliedError


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_committed_write_restored_during_first_reconciliation_retains_marker(operation):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.mutate_then_raise_target = True
    original = drive.read_derived
    restored = False

    def read(ref):
        nonlocal restored
        if writer.target_mutations == 1 and not restored:
            _restore_old_target(reader, operation)
            restored = True
        return original(ref)

    drive.read_derived = read
    first = _apply(reader, writer, drive, resolver, proposal)
    assert restored
    assert first.state is QueueState.APPROVED
    marker = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert marker
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPROVED
    assert not second.mutated
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["Last Error"] == marker


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("error_type", [RuntimeError, ProviderUnavailableError])
def test_old_snapshot_and_before_looking_error_do_not_prove_non_application(operation, error_type):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    original = writer.update_properties
    failed = False

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal failed
        if writer._target(target_db)[0] == "materialusage" and not failed:
            failed = True
            raise error_type("target write failed before mutation; retryable=True")
        return original(target_db, entity_id, patch, **kwargs)

    writer.update_properties = update
    assert _apply(reader, writer, drive, resolver, proposal).state is QueueState.APPROVED
    assert writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert _apply(reader, writer, drive, resolver, proposal).state is QueueState.APPROVED
    assert writer.target_mutations == 0


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("switch", ["raise_before_target", "target_raise_before_mutation"])
def test_trusted_before_write_failure_and_immediate_old_snapshot_allow_retry(operation, switch):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    setattr(writer, switch, True)
    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]].get("Last Error") is None
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("readback", ["divergent", "unavailable"])
def test_trusted_non_application_without_exact_old_readback_keeps_marker(operation, readback):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.raise_before_target = True
    original_update = writer.update_properties
    original_read = reader.get_material_usage
    blocked = False

    def read(session_id):
        if blocked:
            raise TimeoutError("readback unavailable")
        return original_read(session_id)

    def update(target_db, entity_id, patch, **kwargs):
        nonlocal blocked
        try:
            return original_update(target_db, entity_id, patch, **kwargs)
        except ProviderWriteNotAppliedError as exc:
            assert exc.code == "PROVIDER_UNAVAILABLE"
            if readback == "unavailable":
                blocked = True
            else:
                original_read("COMP319-S05")[0]["Role"] = "Supporting"
            raise

    writer.update_properties = update
    reader.get_material_usage = read
    first = _apply(reader, writer, drive, resolver, proposal)
    assert first.state is QueueState.APPROVED
    marker = writer.queue[proposal["Proposal ID"]].get("Last Error")
    assert marker
    blocked = False
    _restore_old_target(reader, operation)
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPROVED
    assert not second.mutated
    assert writer.target_mutations == 0
    assert writer.queue[proposal["Proposal ID"]]["Last Error"] == marker
