import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.adapters.notion.base import AUTOMATION_QUEUE, enforce_write_policy, upsert_proposal
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


class FakeNotionAdapter:
    def __init__(self) -> None:
        self.queue: dict[str, dict[str, object]] = {}

    def read_approval(self, proposal_id: str):
        return self.queue.get(proposal_id)

    def create_entity(self, target_db: str, properties, *, actor):
        enforce_write_policy(actor, target_db, properties)
        self.queue[str(properties["Proposal ID"])] = dict(properties)
        return self.queue[str(properties["Proposal ID"])]

    def update_properties(self, target_db: str, entity_id: str, patch, *, actor):
        enforce_write_policy(actor, target_db, patch)
        self.queue[entity_id].update(patch)


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
