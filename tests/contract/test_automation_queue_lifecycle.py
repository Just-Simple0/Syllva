import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from datetime import datetime, timezone

import pytest

from uls.adapters.notion.base import (
    AUTOMATION_QUEUE,
    ApprovalReader,
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
        self.create_calls = 0
        self.update_calls = 0
        self.target_mutations = 0

    def read_approval(self, proposal_id: str):
        return self.queue.get(proposal_id)

    def find_entity_by_id(self, target_db: str, entity_id: str):
        return self.entities.get((target_db, entity_id))

    def create_entity(self, target_db: str, properties, *, actor):
        enforce_write_policy(actor, target_db, properties)
        self.create_calls += 1
        item = dict(properties)
        self.queue[str(item["Proposal ID"])] = item
        return item

    def update_properties(self, target_db: str, entity_id: str, patch, *, actor):
        enforce_write_policy(actor, target_db, patch)
        self.update_calls += 1
        if target_db == AUTOMATION_QUEUE:
            self.queue[entity_id].update(patch)
        else:
            self.target_mutations += 1
            self.entities[(target_db, entity_id)].update(patch)


def _proposal(proposal_id: str, *, decision: str = "Pending", state: str = "PENDING_REVIEW"):
    return {
        "Proposal ID": proposal_id,
        "Proposal Type": ProposalType.MATERIAL_USAGE.value,
        "Target Entity ID": "MU-01",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Proposed Action": {
            "Verified": True,
            "Session ID": "S-01",
            "Material ID": "M-01",
        },
        "Decision": decision,
        "State": state,
    }


def _ready_fake() -> FakeNotionAdapter:
    fake = FakeNotionAdapter()
    fake.entities[("Material Usage", "MU-01")] = {
        "ID": "MU-01",
        "Session": "S-01",
        "Material": "M-01",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Verified": False,
    }
    return fake


def test_creation_retry_has_one_queue_item_and_approve_apply_lifecycle() -> None:
    fake = _ready_fake()
    proposal = _proposal("p-lifecycle")
    upsert_proposal(fake, proposal)
    upsert_proposal(fake, proposal)
    assert fake.create_calls == 1
    assert list(fake.queue) == ["p-lifecycle"]

    fake.queue["p-lifecycle"]["Decision"] = Decision.Approve.value
    assert ApprovalReader(fake).sync_state("p-lifecycle") is QueueState.APPROVED
    result = HumanApprovalApplier(
        fake,
        decision_by="reviewer",
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).apply(proposal)
    assert result.state is QueueState.APPLIED
    assert fake.queue["p-lifecycle"]["State"] == QueueState.APPLIED.value
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is True


def test_pending_reject_becomes_rejected_and_cannot_apply() -> None:
    fake = _ready_fake()
    proposal = _proposal("p-rejected")
    upsert_proposal(fake, proposal)
    fake.queue["p-rejected"]["Decision"] = Decision.Reject.value
    assert ApprovalReader(fake).sync_state("p-rejected") is QueueState.REJECTED
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(fake).apply(proposal)
    assert fake.queue["p-rejected"]["State"] == QueueState.REJECTED.value
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is False


def test_approved_stale_source_is_superseded_without_target_mutation() -> None:
    fake = _ready_fake()
    proposal = _proposal(
        "p-stale",
        decision=Decision.Approve.value,
        state=QueueState.APPROVED.value,
    )
    fake.queue["p-stale"] = dict(proposal)
    fake.entities[("Material Usage", "MU-01")]["Source Hash"] = "hash-b"
    result = HumanApprovalApplier(fake).apply(proposal)
    assert result.state is QueueState.SUPERSEDED
    assert fake.queue["p-stale"]["State"] == QueueState.SUPERSEDED.value
    assert fake.target_mutations == 0
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is False


def test_applied_replay_is_idempotent() -> None:
    fake = _ready_fake()
    proposal = _proposal(
        "p-applied",
        decision=Decision.Approve.value,
        state=QueueState.APPLIED.value,
    )
    fake.queue["p-applied"] = dict(proposal)
    result = HumanApprovalApplier(fake).apply(proposal)
    assert result.mutated is False
    assert fake.target_mutations == 0

