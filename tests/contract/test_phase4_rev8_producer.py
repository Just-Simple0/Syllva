"""Usage identity defects must affect only their own producer basis."""

from copy import deepcopy

import pytest
from test_phase4_rev3_producer import Proposer, _producer, ready_phase4


class RecordingProposer(Proposer):
    def propose_material_usage(self, **kwargs):
        self.existing = kwargs["existing_usages"]
        return super().propose_material_usage(**kwargs)


@pytest.mark.parametrize("defect", ["missing_id", "duplicate_id"])
@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_unrelated_invalid_ids_are_excluded_without_veto(defect, operation):
    fixture = ready_phase4()
    rows = fixture[0].material_usage["COMP319-S05"]
    unrelated = deepcopy(rows[0])
    unrelated.update({"Role": "Reference", "Start Page": 1, "End Page": 1})
    if defect == "missing_id":
        del unrelated["ID"]
        rows.append(unrelated)
    else:
        unrelated["ID"] = "MU:unrelated"
        rows.extend([unrelated, deepcopy(unrelated)])
    candidate = None if operation == "create_usage" else {
        "operation": operation, "material_id": "COMP319-M03",
        "usage_id": "MU:existing", "role": "Primary", "start_page": 2, "end_page": 2,
    }
    proposer = RecordingProposer(candidate)
    result = _producer(fixture, proposer).propose("COMP319-S05")
    assert proposer.calls == 1
    assert [row["ID"] for row in proposer.existing] == ["MU:existing"]
    assert len(result.proposals) == 1
    assert len(result.created_usage_ids) == int(operation == "create_usage")
    assert result.warnings
    assert not result.skipped and not result.retry_pending


@pytest.mark.parametrize("defect", ["missing_id", "missing_verified", "invalid_verified"])
@pytest.mark.parametrize("during_model", [False, True])
def test_exact_malformed_sibling_still_blocks_creation(defect, during_model):
    fixture = ready_phase4()
    rows = fixture[0].material_usage["COMP319-S05"]
    sibling = deepcopy(rows[0])
    sibling.update({"ID": "MU:sibling", "Role": "Supporting", "Start Page": 2, "End Page": 2})
    if defect == "missing_id":
        del sibling["ID"]
    elif defect == "missing_verified":
        del sibling["Verified"]
    else:
        sibling["Verified"] = "invalid"
    if not during_model:
        rows.append(sibling)
    proposer = RecordingProposer(callback=(lambda: rows.append(sibling)) if during_model else None)
    result = _producer(fixture, proposer).propose("COMP319-S05")
    assert proposer.calls == 1
    assert not result.proposals and not result.created_usage_ids
    assert result.warnings or result.skipped
    assert fixture[1].create_calls == 0


@pytest.mark.parametrize("operation", ["create_usage", "update_range"])
def test_selected_existing_target_id_requires_one_physical_row(operation):
    fixture = ready_phase4()
    rows = fixture[0].material_usage["COMP319-S05"]
    sibling = deepcopy(rows[0])
    sibling.update({"Role": "Reference", "End Page": 1, "Verified": "invalid"})
    rows.append(sibling)
    proposer = RecordingProposer({
        "operation": operation, "material_id": "COMP319-M03", "usage_id": "MU:existing",
        "role": "Primary", "start_page": 1 if operation == "create_usage" else 2, "end_page": 2,
    })
    result = _producer(fixture, proposer).propose("COMP319-S05")
    assert proposer.calls == 1
    assert proposer.existing == ()
    assert not result.proposals and not result.created_usage_ids
    assert result.warnings or result.skipped
    assert fixture[1].create_calls == 0
