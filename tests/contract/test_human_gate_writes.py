import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from datetime import datetime, timezone

import pytest

from uls.adapters.notion.base import (
    AUTOMATION_QUEUE,
    ApprovalReader,
    Decision,
    HumanApprovalApplier,
    QueueState,
    ProposalType,
    enforce_write_policy,
)
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


class FakeNotionAdapter:
    def __init__(self) -> None:
        self.queue: dict[str, dict[str, object]] = {}
        self.entities: dict[tuple[str, str], dict[str, object]] = {}
        self.writes: list[tuple[str, str, dict[str, object], AutomationActor]] = []

    def read_approval(self, proposal_id: str):
        return self.queue.get(proposal_id)

    def find_entity_by_id(self, target_db: str, entity_id: str):
        return self.entities.get((target_db, entity_id))

    def update_properties(self, target_db: str, entity_id: str, patch, *, actor):
        enforce_write_policy(actor, target_db, patch)
        self.writes.append((target_db, entity_id, dict(patch), actor))
        if target_db == AUTOMATION_QUEUE:
            self.queue[entity_id].update(patch)
        else:
            self.entities[(target_db, entity_id)].update(patch)


def test_automation_human_only_fields_and_queue_states_are_denied() -> None:
    forbidden = [
        ("Material Usage", {"Verified": True}),
        ("Exams", {"Scope Confirmed": True}),
        (AUTOMATION_QUEUE, {"Decision": "Approve"}),
        (AUTOMATION_QUEUE, {"Decision": "Reject"}),
        (AUTOMATION_QUEUE, {"State": QueueState.APPROVED}),
        (AUTOMATION_QUEUE, {"State": QueueState.REJECTED}),
        (AUTOMATION_QUEUE, {"State": QueueState.APPLIED}),
    ]
    for target_db, patch in forbidden:
        with pytest.raises(PolicyViolation):
            enforce_write_policy(AutomationActor.AUTOMATION, target_db, patch)


def test_approval_reader_derives_state_without_decision_mutation() -> None:
    fake = FakeNotionAdapter()
    fake.queue["p-reader"] = {
        "Proposal ID": "p-reader",
        "Decision": Decision.Approve.value,
        "State": QueueState.PENDING_REVIEW.value,
    }
    reader = ApprovalReader(fake)
    assert reader.derive_state(Decision.Approve) is QueueState.APPROVED
    assert reader.sync_state("p-reader") is QueueState.APPROVED
    assert fake.queue["p-reader"]["Decision"] == Decision.Approve.value
    assert fake.queue["p-reader"]["State"] == QueueState.APPROVED.value
    assert not hasattr(reader, "set_decision")
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.APPROVAL_READER,
            AUTOMATION_QUEUE,
            {"Decision": Decision.Approve.value},
        )


def _approved_usage_proposal() -> dict[str, object]:
    return {
        "Proposal ID": "p-usage",
        "Proposal Type": ProposalType.MATERIAL_USAGE.value,
        "State": QueueState.APPROVED.value,
        "Decision": Decision.Approve.value,
        "Target Entity ID": "MU-01",
        "Proposed Action": {
            "Verified": True,
            "Session ID": "S-01",
            "Material ID": "M-01",
        },
    }


def test_applier_requires_exact_current_proposal_and_allows_exact_mutation() -> None:
    fake = FakeNotionAdapter()
    proposal = _approved_usage_proposal()
    fake.queue["p-usage"] = dict(proposal)
    fake.entities[("Material Usage", "MU-01")] = {
        "ID": "MU-01",
        "Session": "S-01",
        "Material": "M-01",
        "Verified": False,
    }
    applier = HumanApprovalApplier(
        fake,
        decision_by="human-reviewer",
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = applier.apply(proposal)
    assert result.state is QueueState.APPLIED
    assert fake.entities[("Material Usage", "MU-01")]["Verified"] is True
    assert fake.queue["p-usage"]["State"] == QueueState.APPLIED.value
    assert fake.queue["p-usage"]["Decision By"] == "human-reviewer"
    assert "Decision At" in fake.queue["p-usage"]
    assert "Applied At" in fake.queue["p-usage"]


def test_applier_without_current_approved_proposal_is_denied() -> None:
    fake = FakeNotionAdapter()
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(fake).apply(_approved_usage_proposal())


def test_applier_rejects_a_different_target_or_action_under_same_proposal_id() -> None:
    fake = FakeNotionAdapter()
    current = _approved_usage_proposal()
    fake.queue["p-usage"] = dict(current)
    fake.entities[("Material Usage", "MU-01")] = {
        "ID": "MU-01",
        "Session": "S-01",
        "Material": "M-01",
        "Verified": False,
    }
    wrong = dict(current)
    wrong["Target Entity ID"] = "MU-02"
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(fake).apply(wrong)


def test_applier_allows_exact_exam_scope_mutation() -> None:
    fake = FakeNotionAdapter()
    proposal = {
        "Proposal ID": "p-exam",
        "Proposal Type": ProposalType.EXAM_SCOPE.value,
        "State": QueueState.APPROVED.value,
        "Decision": Decision.Approve.value,
        "Target Entity ID": "E-01",
        "Proposed Action": {"Scope Confirmed": True, "Included Sessions": ["S-01"]},
    }
    fake.queue["p-exam"] = dict(proposal)
    fake.entities[("Exams", "E-01")] = {
        "ID": "E-01",
        "Included Sessions": ["S-01"],
        "Scope Confirmed": False,
    }
    result = HumanApprovalApplier(fake).apply(proposal)
    assert result.state is QueueState.APPLIED
    assert fake.entities[("Exams", "E-01")]["Scope Confirmed"] is True
