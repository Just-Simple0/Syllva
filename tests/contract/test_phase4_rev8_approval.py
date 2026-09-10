"""Approval attribution and target-scoped physical Usage ambiguity."""

from copy import deepcopy

import pytest
from tests.contract.test_phase4_rev6_recovery import _apply, _approved_phase4

from uls.adapters.notion.base import QueueState


def _make_desired(reader, operation):
    target = reader.material_usage["COMP319-S05"][0]
    target.update({"Verified": True} if operation == "create_usage" else {"Start Page": 2})


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("timing", ["virgin", "marker_arm", "post_arm_recheck"])
def test_external_desired_state_before_target_call_cannot_be_audited(operation, timing):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    if timing == "virgin":
        _make_desired(reader, operation)
    elif timing == "marker_arm":
        original = writer.update_properties

        def update(target_db, entity_id, patch, **kwargs):
            result = original(target_db, entity_id, patch, **kwargs)
            if isinstance(patch.get("Last Error"), str):
                _make_desired(reader, operation)
            return result

        writer.update_properties = update
    else:
        original_read = drive.read_derived

        def read(ref):
            if writer.queue[proposal["Proposal ID"]].get("Last Error"):
                _make_desired(reader, operation)
            return original_read(ref)

        drive.read_derived = read
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state in {QueueState.SUPERSEDED, QueueState.APPROVED}
    assert not result.mutated
    assert writer.target_mutations == 0
    row = writer.queue[proposal["Proposal ID"]]
    assert row["State"] != "APPLIED"
    assert all(key not in row for key in ("Decision By", "Decision At", "Applied At"))


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_prior_attempt_marker_and_desired_still_finish_audit(operation):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    writer.fail_audit_once = True
    assert _apply(reader, writer, drive, resolver, proposal).state is QueueState.APPROVED
    assert writer.queue[proposal["Proposal ID"]]["Last Error"]
    second = _apply(reader, writer, drive, resolver, proposal)
    assert second.state is QueueState.APPLIED
    assert not second.mutated
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("junk", ["no_id", "duplicate_id", "malformed", "overlap"])
def test_unrelated_usage_id_defects_do_not_veto_target(operation, junk):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    rows = reader.material_usage["COMP319-S05"]
    sibling = deepcopy(rows[0])
    sibling.update({"ID": "MU:unrelated", "Role": "Reference", "End Page": 1})
    if junk == "no_id":
        sibling.pop("ID")
    elif junk == "malformed":
        sibling.pop("ID")
        sibling["Start Page"] = "broken"
    elif junk == "overlap":
        sibling.update({"Role": "Primary", "Start Page": 1, "End Page": 1})
    rows.append(sibling)
    if junk == "duplicate_id":
        rows.append(deepcopy(sibling))
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.APPLIED
    assert writer.target_mutations == 1


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
@pytest.mark.parametrize("defect", [
    "target_id", "target_id_missing_verified", "target_id_bad_verified",
    "exact_no_id", "exact_missing_verified", "exact_bad_verified",
])
def test_target_id_and_raw_exact_sibling_ambiguity_still_block(operation, defect):
    reader, writer, drive, resolver, proposal = _approved_phase4(operation)
    rows = reader.material_usage["COMP319-S05"]
    sibling = deepcopy(rows[0])
    if defect.startswith("target_id"):
        sibling["Role"] = "Reference"
        if defect == "target_id_missing_verified":
            sibling.pop("Verified")
        elif defect == "target_id_bad_verified":
            sibling["Verified"] = "invalid"
    else:
        sibling["ID"] = "MU:sibling"
        if operation == "update_range":
            sibling["Start Page"] = 2
        if defect == "exact_no_id":
            sibling.pop("ID")
        elif defect == "exact_missing_verified":
            sibling.pop("Verified")
        else:
            sibling["Verified"] = "invalid"
    rows.append(sibling)
    result = _apply(reader, writer, drive, resolver, proposal)
    assert result.state is QueueState.SUPERSEDED
    assert writer.target_mutations == 0
