"""Rev4 regressions for caller-supplied Phase4 approval records."""

from __future__ import annotations

import json
import pathlib
import sys
from copy import deepcopy
from itertools import combinations

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import phase4_applier_kwargs, phase4_proposal, ready_phase4

from uls.adapters.notion.base import HumanApprovalApplier, QueueState, upsert_proposal
from uls.adapters.notion.guarded import GuardedNotionWriter
from uls.domain.approval_identity import canonical_action_json, derive_proposal_id
from uls.domain.errors import PolicyViolation


def _approved() -> tuple[object, object, dict, HumanApprovalApplier]:
    reader, backend, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    upsert_proposal(backend, proposal)
    row = backend.queue[proposal["Proposal ID"]]
    row.update({"Decision": "Approve", "State": "APPROVED"})
    applier = HumanApprovalApplier(
        GuardedNotionWriter(backend),
        decision_by="trusted-reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    return reader, backend, row, applier


def _approved_with_grounded_evidence() -> tuple[object, object, dict, HumanApprovalApplier]:
    reader, backend, drive, resolver = ready_phase4()
    proposal = phase4_proposal(reader)
    evidence = [{"locator": "COMP319-M03:p1", "quote": "Master theorem material"}]
    action = json.loads(proposal["Proposed Action"])
    action["evidence"] = evidence
    proposal["Evidence"] = evidence
    proposal["Proposed Action"] = canonical_action_json(action)
    # Keep the stored action valid and derive its identity from that action;
    # this test must reach mirror comparison rather than fail on a forged ID.
    proposal["Proposal ID"] = derive_proposal_id(proposal["Proposal Type"], action)
    upsert_proposal(backend, proposal)
    row = backend.queue[proposal["Proposal ID"]]
    row.update({"Decision": "Approve", "State": "APPROVED"})
    applier = HumanApprovalApplier(
        GuardedNotionWriter(backend),
        decision_by="trusted-reviewer",
        **phase4_applier_kwargs(reader, drive, resolver),
    )
    return reader, backend, row, applier


def _tampered_value(field: str, proposal: dict) -> object:
    if field == "Proposal Type":
        return "PAGE_RANGE"
    if field == "Target Entity ID":
        return "MU:caller-forgery"
    if field == "Course":
        return {"relation": [{"id": "course-attacker"}]}
    if field == "Source Ref":
        return {"provider": "google_drive", "file_id": "material-attacker"}
    if field == "Source Hash":
        return "material-hash-attacker"
    if field == "Source Version":
        return 99
    if field == "Evidence":
        return [{"locator": "COMP319-M03:p1", "quote": "caller-forgery"}]
    if field == "Review Reason":
        return "caller-forgery"
    if field == "Proposed Action":
        action = json.loads(proposal["Proposed Action"])
        action["review_reason"] = "caller-forgery"
        return action
    if field == "Proposal ID":
        return proposal["Proposal ID"] + "-caller-forgery"
    if field == "Target DB":
        return "Exams"
    raise AssertionError(field)


_MIRROR_ALIAS_GROUPS = (
    ("Proposal Type", ("Proposal Type", "proposal_type", "proposalType")),
    (
        "Target Entity ID",
        ("Target Entity ID", "target_entity_id", "target_id", "Target Entity"),
    ),
    ("Course", ("Course", "course")),
    ("Source Ref", ("Source Ref", "source_ref", "sourceRef")),
    ("Source Hash", ("Source Hash", "source_hash", "sourceHash")),
    ("Source Version", ("Source Version", "source_version", "sourceVersion")),
    ("Evidence", ("Evidence", "evidence")),
    ("Review Reason", ("Review Reason", "review_reason", "reviewReason")),
    ("Proposed Action", ("Proposed Action", "proposed_action", "action")),
    ("Proposal ID", ("Proposal ID", "proposal_id", "proposalId")),
    ("Target DB", ("Target DB", "Target Database", "target_db", "target_database")),
)

_MIRROR_ALIASES = tuple(
    (field, alias)
    for field, aliases in _MIRROR_ALIAS_GROUPS
    for alias in aliases
)

_CONFLICTING_MIRROR_ALIASES = tuple(
    (field, left, right)
    for field, aliases in _MIRROR_ALIAS_GROUPS
    for left, right in combinations(aliases, 2)
)


@pytest.mark.parametrize(("field", "alias"), _MIRROR_ALIASES)
def test_caller_semantic_mirror_alias_cannot_authorize_mutation(field: str, alias: str) -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    proposal_id = proposal["Proposal ID"]
    supplied: dict[str, object] = {"Proposal ID": proposal_id}
    if field == "Proposal ID" and alias != "Proposal ID":
        supplied[alias] = _tampered_value(field, proposal)
    elif field == "Proposal ID":
        supplied = {alias: _tampered_value(field, proposal)}
    else:
        supplied[alias] = _tampered_value(field, proposal)
    before_row = deepcopy(row)
    before_queue_updates = backend.update_calls

    with pytest.raises(PolicyViolation):
        applier.apply(supplied)

    assert row == before_row
    assert backend.update_calls == before_queue_updates
    assert backend.target_mutations == 0
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


@pytest.mark.parametrize(
    ("field", "alias"),
    [
        (field, alias)
        for field, aliases in (
            ("Course", ("Course", "course")),
            ("Source Ref", ("Source Ref", "source_ref", "sourceRef")),
            ("Source Hash", ("Source Hash", "source_hash", "sourceHash")),
            ("Source Version", ("Source Version", "source_version", "sourceVersion")),
            ("Review Reason", ("Review Reason", "review_reason", "reviewReason")),
            ("Proposed Action", ("Proposed Action", "proposed_action", "action")),
            ("Proposal ID", ("Proposal ID", "proposal_id", "proposalId")),
            ("Target DB", ("Target DB", "Target Database", "target_db", "target_database")),
            ("Proposal Type", ("Proposal Type", "proposal_type", "proposalType")),
            (
                "Target Entity ID",
                ("Target Entity ID", "target_entity_id", "target_id", "Target Entity"),
            ),
        )
        for alias in aliases
    ],
)
def test_explicit_null_semantic_mirror_is_compared(field: str, alias: str) -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    supplied = {"Proposal ID": proposal["Proposal ID"], alias: None}
    before_row = deepcopy(row)
    before_queue_updates = backend.update_calls

    with pytest.raises(PolicyViolation):
        applier.apply(supplied)

    assert row == before_row
    assert backend.update_calls == before_queue_updates
    assert backend.target_mutations == 0


def test_explicit_null_evidence_mirror_matches_the_canonical_null() -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    result = applier.apply({"Proposal ID": proposal["Proposal ID"], "evidence": None})

    assert result.state is QueueState.APPLIED
    assert row["State"] == QueueState.APPLIED.value
    assert backend.target_mutations == 1


def test_explicit_null_evidence_cannot_replace_grounded_evidence() -> None:
    reader, backend, row, applier = _approved_with_grounded_evidence()
    proposal_id = row["Proposal ID"]
    before_row = deepcopy(row)
    before_queue_updates = backend.update_calls

    assert row["Evidence"] is not None
    with pytest.raises(PolicyViolation):
        applier.apply({"Proposal ID": proposal_id, "Evidence": None})

    assert row == before_row
    assert backend.update_calls == before_queue_updates
    assert backend.target_mutations == 0
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


def _caller_mirror_value(field: str, proposal: dict) -> object:
    if field == "Target DB":
        return "Material Usage"
    return proposal[field]


@pytest.mark.parametrize(
    ("field", "matching_alias", "conflicting_alias"),
    _CONFLICTING_MIRROR_ALIASES,
)
def test_conflicting_duplicate_caller_aliases_cannot_authorize_mutation(
    field: str,
    matching_alias: str,
    conflicting_alias: str,
) -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    supplied: dict[str, object] = {
        matching_alias: _caller_mirror_value(field, proposal),
        conflicting_alias: _tampered_value(field, proposal),
    }
    if field != "Proposal ID":
        supplied["Proposal ID"] = proposal["Proposal ID"]
    before_row = deepcopy(row)
    before_queue_updates = backend.update_calls

    with pytest.raises(PolicyViolation):
        applier.apply(supplied)

    assert row == before_row
    assert backend.update_calls == before_queue_updates
    assert backend.target_mutations == 0
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is False


@pytest.mark.parametrize("form", ["string_id", "partial_mapping", "exact_mapping"])
def test_valid_caller_forms_use_the_current_approved_row(form: str) -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    proposal_id = proposal["Proposal ID"]
    if form == "string_id":
        supplied: object = proposal_id
    elif form == "partial_mapping":
        supplied = {"Proposal ID": proposal_id}
    else:
        # This is an exact pre-approval copy.  Its stale Pending lifecycle
        # fields must not override the freshly read approved Queue row.
        supplied = deepcopy(proposal)

    result = applier.apply(supplied)

    assert result.state is QueueState.APPLIED
    assert row["State"] == QueueState.APPLIED.value
    assert backend.target_mutations == 1
    assert reader.get_material_usage("COMP319-S05")[0]["Verified"] is True


@pytest.mark.parametrize("alias", ["Target DB", "Target Database", "target_db", "target_database"])
def test_legacy_target_db_mirror_is_checked_against_action(alias: str) -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    supplied = {"Proposal ID": proposal["Proposal ID"], alias: "Material Usage"}

    result = applier.apply(supplied)

    assert result.state is QueueState.APPLIED
    assert row["State"] == QueueState.APPLIED.value
    assert backend.target_mutations == 1


def test_nested_legacy_target_db_mirror_is_checked_against_action() -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    supplied = {
        "Proposal ID": proposal["Proposal ID"],
        "Target": {"Database": "Material Usage"},
    }

    result = applier.apply(supplied)

    assert result.state is QueueState.APPLIED
    assert row["State"] == QueueState.APPLIED.value
    assert backend.target_mutations == 1


def test_navigation_only_source_ref_mirror_preserves_approval_identity() -> None:
    reader, backend, row, applier = _approved()
    proposal = phase4_proposal(reader)
    supplied = {
        "Proposal ID": proposal["Proposal ID"],
        "source_ref": {
            "provider": "google_drive",
            "file_id": "material-m03",
            "web_url": "https://drive.google.com/file/d/material-m03/view?usp=sharing",
        },
    }

    result = applier.apply(supplied)

    assert result.state is QueueState.APPLIED
    assert row["State"] == QueueState.APPLIED.value
    assert backend.target_mutations == 1
