import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from datetime import datetime, timezone

import pytest

from uls.adapters.notion.base import (
    AUTOMATION_QUEUE,
    Decision,
    HumanApprovalApplier,
    ProposalType,
    QueueState,
    enforce_write_policy,
    upsert_proposal,
)
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


class FakeNotionAdapter:
    def __init__(self) -> None:
        self.queue: dict[str, dict[str, object]] = {}
        self.entities: dict[tuple[str, str], dict[str, object]] = {}
        self.target_mutations = 0
        self.fail_audit_once = False

    def read_approval(self, proposal_id: str):
        return self.queue.get(proposal_id)

    def create_entity(self, target_db: str, properties, *, actor):
        enforce_write_policy(actor, target_db, properties, is_create=True)
        self.queue[str(properties["Proposal ID"])] = dict(properties)
        return self.queue[str(properties["Proposal ID"])]

    def find_entity_by_id(self, target_db: str, entity_id: str):
        return self.entities.get((target_db, entity_id))

    def update_properties(self, target_db: str, entity_id: str, patch, *, actor):
        enforce_write_policy(actor, target_db, patch)
        if target_db == AUTOMATION_QUEUE:
            if self.fail_audit_once:
                self.fail_audit_once = False
                raise RuntimeError("simulated audit outage")
            self.queue[entity_id].update(patch)
        else:
            self.target_mutations += 1
            self.entities[(target_db, entity_id)].update(patch)


def _proposal(**overrides):
    value = {
        "Proposal ID": "p-self",
        "Proposal Type": "MATERIAL_USAGE",
        "Target Entity ID": "MU-01",
        "Proposed Action": {"Verified": True},
        "Decision": "Pending",
        "State": "PENDING_REVIEW",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "overrides",
    [
        {"Decision": "Approve"},
        {"State": "APPROVED"},
        {"Decision": "Approve", "State": "APPROVED"},
    ],
)
def test_ordinary_automation_cannot_self_approve(overrides) -> None:
    fake = FakeNotionAdapter()
    with pytest.raises(PolicyViolation):
        upsert_proposal(fake, _proposal(**overrides))
    assert fake.queue == {}


def test_automation_actor_is_not_a_free_form_human_authority_selector() -> None:
    with pytest.raises(PolicyViolation):
        enforce_write_policy("HUMAN_APPROVAL_APPLIER", AUTOMATION_QUEUE, {"State": "APPLIED"})


def _approved_usage(**overrides):
    value = {
        "Proposal ID": "p-approved",
        "Proposal Type": ProposalType.MATERIAL_USAGE.value,
        "Target Entity ID": "MU-01",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Decision By": "reviewer@example.edu",
        "Proposed Action": {
            "Verified": True,
            "Session ID": "S-01",
            "Material ID": "M-01",
        },
        "Decision": Decision.Approve.value,
        "State": QueueState.APPROVED.value,
    }
    value.update(overrides)
    return value


def _approval_fake(proposal):
    fake = FakeNotionAdapter()
    fake.queue[proposal["Proposal ID"]] = dict(proposal)
    fake.entities[("Material Usage", "MU-01")] = {
        "ID": "MU-01",
        "Session": "S-01",
        "Material": "M-01",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Verified": False,
    }
    return fake


@pytest.mark.parametrize("decision_by", [None, "", "AUTOMATION", "APPROVAL_READER", "HUMAN_APPROVAL_APPLIER"])
def test_applier_requires_a_real_human_decision_by(decision_by) -> None:
    proposal = _approved_usage()
    if decision_by is None:
        proposal.pop("Decision By")
    else:
        proposal["Decision By"] = decision_by
    fake = _approval_fake(proposal)
    with pytest.raises(PolicyViolation, match="human decision_by required"):
        HumanApprovalApplier(fake, decision_by="human-reviewer").apply(proposal)
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is False


def test_invalid_applier_self_decision_by_is_not_accepted() -> None:
    proposal = _approved_usage()
    fake = _approval_fake(proposal)
    with pytest.raises(PolicyViolation, match="human decision_by required"):
        HumanApprovalApplier(fake, decision_by="HUMAN_APPROVAL_APPLIER").apply(proposal)


def test_promotion_requires_relation_snapshot() -> None:
    proposal = _approved_usage(**{"Proposed Action": {"Verified": True}})
    fake = _approval_fake(proposal)
    with pytest.raises(PolicyViolation, match="relation snapshot"):
        HumanApprovalApplier(fake).apply(proposal)
    assert fake.target_mutations == 0


def test_exam_promotion_requires_scope_snapshot() -> None:
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
    fake = FakeNotionAdapter()
    fake.queue[proposal["Proposal ID"]] = dict(proposal)
    fake.entities[("Exams", "E-01")] = {
        "ID": "E-01",
        "Included Sessions": ["S-01"],
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Scope Confirmed": False,
    }
    with pytest.raises(PolicyViolation, match="scope snapshot"):
        HumanApprovalApplier(fake).apply(proposal)
    assert fake.target_mutations == 0


def test_source_promotion_without_fingerprint_is_superseded() -> None:
    proposal = _approved_usage()
    proposal.pop("Source Hash")
    proposal.pop("Source Version")
    fake = _approval_fake(proposal)
    result = HumanApprovalApplier(fake).apply(proposal)
    assert result.state is QueueState.SUPERSEDED
    assert fake.queue[proposal["Proposal ID"]]["State"] == QueueState.SUPERSEDED.value
    assert fake.target_mutations == 0


def test_target_mutation_then_audit_failure_replays_without_duplicate_mutation() -> None:
    proposal = _approved_usage()
    fake = _approval_fake(proposal)
    fake.fail_audit_once = True
    applier = HumanApprovalApplier(
        fake,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    first = applier.apply(proposal)
    assert first.state is QueueState.APPROVED
    assert fake.queue[proposal["Proposal ID"]]["State"] == QueueState.APPROVED.value
    assert fake.target_mutations == 1
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is True

    second = applier.apply(proposal)
    assert second.state is QueueState.APPLIED
    assert fake.target_mutations == 1
    assert fake.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
