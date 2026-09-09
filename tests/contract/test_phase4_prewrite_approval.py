"""A human grant must still be current after dependency reads finish."""

import sys
from copy import deepcopy
from pathlib import Path

import pytest

from uls.adapters.notion.base import ApprovalReader, HumanApprovalApplier, upsert_proposal
from uls.domain.errors import PolicyViolation


@pytest.mark.parametrize("change", ["reject", "duplicate", "type", "content", "delete"])
def test_dependency_read_cannot_outlive_the_human_approval(change: str) -> None:
    fixture_path = str(Path(__file__).resolve().parents[1] / "fixtures")
    if fixture_path not in sys.path:
        sys.path.insert(0, fixture_path)
    from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    proposal_id = proposal["Proposal ID"]
    upsert_proposal(writer, proposal)
    writer.queue[proposal_id]["Decision"] = "Approve"
    ApprovalReader(writer).sync_state(proposal_id)
    prior_updates = writer.update_calls
    original_read = drive.read_derived
    changed = False

    def read_then_change_approval(source_ref):
        nonlocal changed
        result = original_read(source_ref)
        if not changed:
            changed = True
            row = writer.queue[proposal_id]
            if change == "reject":
                row["Decision"] = "Reject"
            elif change == "duplicate":
                duplicate = deepcopy(row)
                duplicate["record_id"] = "second-physical-queue-row"
                writer.queue_rows.append(duplicate)
            elif change == "type":
                row["Proposal Type"] = "OTHER"
            elif change == "content":
                row["Review Reason"] = "changed during the source read"
            else:
                writer.queue.clear()
                writer.queue_rows.clear()
        return result

    drive.read_derived = read_then_change_approval
    applier = HumanApprovalApplier(
        writer,
        decision_by="reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    with pytest.raises(PolicyViolation):
        applier.apply(proposal_id)
    assert changed
    assert writer.target_mutations == 0
    assert writer.update_calls == prior_updates
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


@pytest.mark.parametrize("change", ["role", "range", "session", "type", "course", "fingerprint", "sibling"])
def test_dependency_read_cannot_outlive_the_target_basis(change: str) -> None:
    fixture_path = str(Path(__file__).resolve().parents[1] / "fixtures")
    if fixture_path not in sys.path:
        sys.path.insert(0, fixture_path)
    from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

    from uls.domain.source_ref import SourceFingerprint
    reader, writer, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    pid = proposal["Proposal ID"]
    upsert_proposal(writer, proposal)
    writer.queue[pid]["Decision"] = "Approve"
    ApprovalReader(writer).sync_state(pid)
    original_read = drive.read_derived
    changed = False
    usage = reader.get_material_usage("COMP319-S05")[0]
    def read_then_change_target(ref):
        nonlocal changed
        value = original_read(ref)
        if not changed:
            changed = True
            if change == "role":
                usage["Role"] = "Reference"
            elif change == "range":
                usage["Start Page"] = 2
            elif change == "session":
                usage["Session"] = {"relation": [{"id": "COMP319-S06"}]}
            elif change == "type":
                reader.materials["COMP319-M03"]["Type"] = "Professor Notes"
            elif change == "course":
                reader.courses["course-page-1"]["Course Key"] = "2026-2_COMP319-002"
            elif change == "fingerprint":
                drive.fingerprints["material-m03"] = SourceFingerprint(2, "changed")
            else:
                sibling = deepcopy(usage)
                sibling["ID"] = "MU:sibling"
                reader.material_usage["COMP319-S05"].append(sibling)
        return value
    drive.read_derived = read_then_change_target
    try:
        result = HumanApprovalApplier(writer, decision_by="reviewer", **phase4_applier_kwargs(reader, drive, resolver)).apply(pid)
        assert result.state.value != "APPLIED"
    except PolicyViolation:
        pass
    assert changed
    assert writer.target_mutations == 0
    assert usage["Verified"] is False
