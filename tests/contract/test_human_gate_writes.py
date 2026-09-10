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
    ApprovalReader,
    Decision,
    HumanApprovalApplier,
    ProposalType,
    QueueState,
    enforce_write_policy,
    mark_proposal_failed,
    mark_proposal_superseded,
    upsert_proposal,
)
from uls.domain.enums import AutomationActor
from uls.domain.errors import PolicyViolation


def test_automation_human_only_fields_and_queue_states_are_denied() -> None:
    forbidden = [
        ("Material Usage", {"Verified": True}),
        ("Material Usage", {"Verified": 1}),
        ("Material Usage", {"Verified": "true"}),
        ("Material Usage", {"Role": "Reference"}),
        ("Exams", {"Scope Confirmed": True}),
        (AUTOMATION_QUEUE, {"Decision": Decision.Pending}),
        (AUTOMATION_QUEUE, {"Decision": "Approve"}),
        (AUTOMATION_QUEUE, {"Decision": "Reject"}),
        (AUTOMATION_QUEUE, {"State": QueueState.PENDING_REVIEW}),
        (AUTOMATION_QUEUE, {"State": QueueState.APPROVED}),
        (AUTOMATION_QUEUE, {"State": QueueState.REJECTED}),
        (AUTOMATION_QUEUE, {"State": QueueState.APPLIED}),
        (AUTOMATION_QUEUE, {"State": QueueState.SUPERSEDED}),
        (AUTOMATION_QUEUE, {"State": QueueState.FAILED}),
    ]
    for target_db, patch in forbidden:
        with pytest.raises(PolicyViolation):
            enforce_write_policy(AutomationActor.AUTOMATION, target_db, patch)

    enforce_write_policy(
        AutomationActor.AUTOMATION,
        AUTOMATION_QUEUE,
        {"Decision": Decision.Pending, "State": QueueState.PENDING_REVIEW},
        is_create=True,
    )


def test_automation_system_transition_is_the_only_update_path_for_terminal_states() -> None:
    for state in (QueueState.SUPERSEDED, QueueState.FAILED):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            AUTOMATION_QUEUE,
            {"State": state, "Last Error": "source/application failure"},
            system_transition=True,
        )

    for state in (
        QueueState.APPROVED,
        QueueState.REJECTED,
        QueueState.APPLIED,
        QueueState.PENDING_REVIEW,
    ):
        with pytest.raises(PolicyViolation):
            enforce_write_policy(
                AutomationActor.AUTOMATION,
                AUTOMATION_QUEUE,
                {"State": state, "Last Error": "not an allowed system state"},
                system_transition=True,
            )
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            AUTOMATION_QUEUE,
            {"State": QueueState.FAILED},
            system_transition=True,
        )
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            AUTOMATION_QUEUE,
            {"State": QueueState.SUPERSEDED, "Last Error": "not creatable"},
            is_create=True,
            system_transition=True,
        )


def test_defined_system_helpers_allow_only_automation_terminal_transitions() -> None:
    fake = FakeNotionAdapter()
    fake.queue["p-superseded"] = {
        "Proposal ID": "p-superseded",
        "State": QueueState.APPROVED.value,
        "Decision": Decision.Approve.value,
    }
    fake.queue["p-failed"] = {
        "Proposal ID": "p-failed",
        "State": QueueState.APPROVED.value,
        "Decision": Decision.Approve.value,
    }

    mark_proposal_superseded(fake, "p-superseded", "source fingerprint changed")
    mark_proposal_failed(fake, "p-failed", "provider application failed")

    assert fake.queue["p-superseded"]["State"] == QueueState.SUPERSEDED.value
    assert fake.queue["p-superseded"]["Last Error"] == "source fingerprint changed"
    assert fake.queue["p-failed"]["State"] == QueueState.FAILED.value
    assert fake.queue["p-failed"]["Last Error"] == "provider application failed"


def test_uuid_queue_identifier_is_not_treated_as_an_ordinary_database() -> None:
    queue_id = "4a9f0c1d-automation-queue"
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            queue_id,
            {"Decision": "Approve"},
            automation_queue_ids={queue_id},
        )
    with pytest.raises(PolicyViolation):
        enforce_write_policy(
            AutomationActor.AUTOMATION,
            queue_id,
            {"Decision": "Pending", "State": "PENDING_REVIEW"},
        )
    enforce_write_policy(
        AutomationActor.AUTOMATION,
        queue_id,
        {"Decision": Decision.Pending, "State": QueueState.PENDING_REVIEW},
        automation_queue_ids={queue_id},
        is_create=True,
    )


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


def test_applier_requires_exact_current_phase4_proposal_and_allows_exact_mutation() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(writer, proposal)
    writer.queue[proposal["Proposal ID"]]["Decision"] = Decision.Approve.value
    writer.queue[proposal["Proposal ID"]]["State"] = QueueState.APPROVED.value
    result = HumanApprovalApplier(
        writer,
        decision_by="human-reviewer",
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        **phase4_applier_kwargs(reader, drive, resolver),
    ).apply(proposal)
    assert result.state is QueueState.APPLIED
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is True
    assert writer.queue[proposal["Proposal ID"]]["State"] == QueueState.APPLIED.value
    assert writer.queue[proposal["Proposal ID"]]["Decision By"] == "human-reviewer"
    assert "Decision At" in writer.queue[proposal["Proposal ID"]]
    assert "Applied At" in writer.queue[proposal["Proposal ID"]]


def test_applier_without_current_approved_proposal_is_denied() -> None:
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(
            writer,
            decision_by="human-reviewer",
            **phase4_applier_kwargs(reader, drive, resolver),
        ).apply(proposal)


def test_applier_rejects_a_different_target_under_same_proposal_id() -> None:
    reader, writer, drive, resolver = ready_phase4()
    current = phase4_proposal(reader)
    upsert_proposal(writer, current)
    writer.queue[current["Proposal ID"]]["Decision"] = Decision.Approve.value
    writer.queue[current["Proposal ID"]]["State"] = QueueState.APPROVED.value
    wrong = dict(current)
    wrong["Target Entity ID"] = "MU:other"
    with pytest.raises(PolicyViolation):
        HumanApprovalApplier(
            writer,
            decision_by="human-reviewer",
            **phase4_applier_kwargs(reader, drive, resolver),
        ).apply(wrong)
    assert writer.target_mutations == 0


def test_applier_allows_exact_exam_scope_mutation() -> None:
    fake = FakeNotionAdapter()
    proposal = {
        "Proposal ID": "p-exam",
        "Proposal Type": ProposalType.EXAM_SCOPE.value,
        "State": QueueState.APPROVED.value,
        "Decision": Decision.Approve.value,
        "Decision By": "human-reviewer",
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Target Entity ID": "E-01",
        "Proposed Action": {"Scope Confirmed": True, "Included Sessions": ["S-01"]},
    }
    fake.queue["p-exam"] = dict(proposal)
    fake.entities[("Exams", "E-01")] = {
        "ID": "E-01",
        "Included Sessions": ["S-01"],
        "Source Hash": "hash-a",
        "Source Version": 1,
        "Scope Confirmed": False,
    }
    result = HumanApprovalApplier(fake).apply(proposal)
    assert result.state is QueueState.APPLIED
    assert fake.entities[("Exams", "E-01")]["Scope Confirmed"] is True
