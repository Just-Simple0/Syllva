import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from datetime import datetime, timezone

import pytest
from fake_notion import FakeNotionAdapter
from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import (
    AUTOMATION_QUEUE,
    Decision,
    HumanApprovalApplier,
    ProposalType,
    QueueState,
    enforce_write_policy,
    upsert_proposal,
)
from uls.domain.errors import PolicyViolation


def _approved_phase4():
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["Decision"] = Decision.Approve.value
    writer.queue[proposal["Proposal ID"]]["State"] = QueueState.APPROVED.value
    return reader, writer, drive, resolver, proposal


def _apply(reader, writer, drive, resolver, proposal, *, decision_by=None):
    return HumanApprovalApplier(
        writer,
        decision_by=decision_by,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        **phase4_applier_kwargs(reader, drive, resolver),
    ).apply(proposal)


@pytest.mark.parametrize(
    "overrides",
    [
        {"Decision": "Approve"},
        {"State": "APPROVED"},
        {"Decision": "Approve", "State": "APPROVED"},
    ],
)
def test_ordinary_automation_cannot_self_approve(overrides) -> None:
    reader, writer, _, _ = ready_phase4()
    proposal = phase4_proposal(reader)
    proposal.update(overrides)
    with pytest.raises(PolicyViolation):
        upsert_proposal(writer, proposal)
    assert writer.queue == {}


def test_automation_actor_is_not_a_free_form_human_authority_selector() -> None:
    with pytest.raises(PolicyViolation):
        enforce_write_policy("HUMAN_APPROVAL_APPLIER", AUTOMATION_QUEUE, {"State": "APPLIED"})


def test_missing_stored_decision_by_uses_trusted_applier_identity() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    assert "Decision By" not in writer.queue[proposal["Proposal ID"]]
    result = _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer@example.edu")
    assert result.state is QueueState.APPLIED
    assert writer.queue[proposal["Proposal ID"]]["Decision By"] == "reviewer@example.edu"


def test_invalid_stored_decision_by_falls_back_to_trusted_constructor_identity() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.queue[proposal["Proposal ID"]]["Decision By"] = "AUTOMATION"
    result = _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer@example.edu")
    assert result.state is QueueState.APPLIED
    assert writer.queue[proposal["Proposal ID"]]["Decision By"] == "reviewer@example.edu"


def test_valid_stored_decision_by_is_preserved_without_constructor_identity() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.queue[proposal["Proposal ID"]]["Decision By"] = "human-reviewer"
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.APPLIED
    assert writer.queue[proposal["Proposal ID"]]["Decision By"] == "human-reviewer"


def test_no_attributable_decision_by_fails_closed_before_target_write() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    with pytest.raises(PolicyViolation, match="no attributable human decision_by"):
        _apply(reader, writer, drive, resolver, proposal)
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False
    assert writer.target_mutations == 0


def test_invalid_applier_constructor_identity_is_rejected() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    with pytest.raises(PolicyViolation, match="human decision_by required"):
        _apply(reader, writer, drive, resolver, proposal, decision_by="HUMAN_APPROVAL_APPLIER")


def test_stored_queue_semantic_tamper_fails_before_target_mutation() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.queue[proposal["Proposal ID"]]["Proposed Action"] = '{"Verified":true}'
    with pytest.raises(PolicyViolation):
        _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer")
    assert writer.target_mutations == 0


def test_promotion_requires_the_strict_canonical_action() -> None:
    writer = FakeNotionAdapter()
    writer.queue["legacy-invalid"] = {
        "Proposal ID": "legacy-invalid",
        "Proposal Type": ProposalType.MATERIAL_USAGE.value,
        "Target Entity ID": "MU-01",
        "Proposed Action": {"Verified": True},
        "Decision": Decision.Approve.value,
        "State": QueueState.APPROVED.value,
    }
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(writer).apply("legacy-invalid")
    assert writer.target_mutations == 0


def test_exam_promotion_requires_scope_snapshot() -> None:
    fake = FakeNotionAdapter()
    proposal = {
        "Proposal ID": "p-exam-no-snapshot",
        "Proposal Type": ProposalType.EXAM_SCOPE.value,
        "Target Entity ID": "E-01",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Decision By": "reviewer@example.edu",
        "Proposed Action": {"Scope Confirmed": True},
        "Decision": Decision.Approve.value,
        "State": QueueState.APPROVED.value,
    }
    fake.queue[proposal["Proposal ID"]] = dict(proposal)
    fake.entities[("Exams", "E-01")] = {
        "ID": "E-01",
        "Included Sessions": ["S-01"],
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Scope Confirmed": False,
    }
    with pytest.raises(PolicyViolation, match="stored strict Queue action is malformed"):
        HumanApprovalApplier(fake).apply(proposal)
    assert fake.target_mutations == 0


def test_source_promotion_without_fingerprint_is_superseded() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    drive.fingerprints.pop("material-m03")
    result = _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer")
    assert result.state is QueueState.SUPERSEDED
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.SUPERSEDED.value
    assert writer.target_mutations == 0


def test_target_mutation_then_audit_failure_replays_without_duplicate_mutation() -> None:
    reader, writer, drive, resolver, proposal = _approved_phase4()
    writer.fail_audit_once = True
    first = _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer")
    assert first.state is QueueState.APPROVED
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPROVED.value
    assert writer.target_mutations == 1
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is True

    second = _apply(reader, writer, drive, resolver, proposal, decision_by="reviewer")
    assert second.state is QueueState.APPLIED
    assert writer.target_mutations == 1
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
