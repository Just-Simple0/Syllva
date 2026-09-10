import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from datetime import datetime, timezone

import pytest
from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import (
    ApprovalReader,
    Decision,
    HumanApprovalApplier,
    QueueState,
    upsert_proposal,
)
from uls.domain.errors import PolicyViolation
from uls.domain.source_ref import SourceFingerprint


def _approved(writer, proposal):
    writer.queue[proposal["Proposal ID"]]["Decision"] = Decision.Approve.value
    writer.queue[proposal["Proposal ID"]]["State"] = QueueState.APPROVED.value


def _applier(writer, reader, drive, resolver, *, decision_by=None):
    return HumanApprovalApplier(
        writer,
        decision_by=decision_by,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        **phase4_applier_kwargs(reader, drive, resolver),
    )


def test_creation_retry_has_one_queue_item_and_approve_apply_lifecycle() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    upsert_proposal(writer, proposal)
    assert writer.create_calls == 1
    assert len(writer.find_approval_rows(proposal["Proposal ID"])) == 1

    _approved(writer, proposal)
    assert ApprovalReader(writer).sync_state(proposal["Proposal ID"]) is QueueState.APPROVED
    result = _applier(writer, reader, drive, resolver, decision_by="reviewer").apply(proposal)
    assert result.state is QueueState.APPLIED
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is True


def test_pending_reject_becomes_rejected_and_cannot_apply() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["Decision"] = Decision.Reject.value
    assert ApprovalReader(writer).sync_state(proposal["Proposal ID"]) is QueueState.REJECTED
    with pytest.raises(PolicyViolation):
        _applier(writer, reader, drive, resolver, decision_by="reviewer").apply(proposal)
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.REJECTED.value
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


def test_approved_stale_source_is_superseded_without_target_mutation() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    _approved(writer, proposal)
    drive.fingerprints["material-m03"] = SourceFingerprint(2, "material-hash-v2")
    result = _applier(writer, reader, drive, resolver, decision_by="reviewer").apply(proposal)
    assert result.state is QueueState.SUPERSEDED
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.SUPERSEDED.value
    assert writer.target_mutations == 0
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


def test_applied_replay_is_idempotent() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    _approved(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["State"] = QueueState.APPLIED.value
    result = _applier(writer, reader, drive, resolver, decision_by="reviewer").apply(proposal)
    assert result.mutated is False
    assert writer.target_mutations == 0


def test_queue_create_committed_then_raised_is_recovered_without_duplicate() -> None:
    reader, writer, _, _ = ready_phase4()
    proposal = phase4_proposal(reader)
    writer.fail_create_after_commit = True
    recovered = upsert_proposal(writer, proposal)
    assert recovered["Proposal ID"] == proposal["Proposal ID"]
    assert writer.create_calls == 1
    assert len(writer.find_approval_rows(proposal["Proposal ID"])) == 1
