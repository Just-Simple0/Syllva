from __future__ import annotations

import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_notion import FakeNotionAdapter, FakeNotionReader
from phase4 import COURSE_KEY, COURSE_PAGE_ID, exam_record

from uls.adapters.notion.base import ProposalType, upsert_proposal
from uls.domain.approval_identity import (
    EXAM_SCOPE_APPROVAL_ACTION_SCHEMA,
    build_exam_scope_semantics,
    canonical_action_json,
    canonical_exam_scope_semantics,
    canonical_semantics_from_queue,
    derive_proposal_id,
)
from uls.domain.errors import PolicyViolation
from uls.proposal.exam_scope import build_exam_scope_proposal

COURSE = {"relation_page_id": COURSE_PAGE_ID, "course_key": COURSE_KEY}


def _semantics(*, desired: list[str] | None = None, evidence=None):
    desired_ids = ["COMP319-S01"] if desired is None else desired
    return build_exam_scope_semantics(
        operation="confirm_empty_scope" if not desired_ids else "confirm_scope",
        target_entity_id="COMP319-E01",
        course=COURSE,
        old_snapshot={"included_session_ids": None, "scope_confirmed": False},
        desired_snapshot={"included_session_ids": desired_ids, "scope_confirmed": True},
        session_dependencies=[
            {
                "session_id": session_id,
                "course_relation_page_id": COURSE_PAGE_ID,
                "course_key": COURSE_KEY,
                "source_ref": None,
                "source_hash": None,
                "source_version": None,
            }
            for session_id in desired_ids
        ],
        source_dependencies=None,
        evidence=evidence,
        review_reason="human scope review",
        processor_version="1.2.0",
    )


def test_exam_scope_identity_is_canonical_and_sensitive_to_all_semantic_fields() -> None:
    base = _semantics()
    assert base == canonical_exam_scope_semantics(base)
    base_id = derive_proposal_id("EXAM_SCOPE", base)

    changed = deepcopy(base)
    changed["evidence"] = {"locator": "COMP319-S01:t00:00:01"}
    assert derive_proposal_id("EXAM_SCOPE", changed) != base_id

    changed = deepcopy(base)
    changed["desired_snapshot"]["included_session_ids"] = ["COMP319-S02"]
    assert derive_proposal_id("EXAM_SCOPE", changed) != base_id


def test_exam_scope_identity_is_order_insensitive_after_validating_membership() -> None:
    base = _semantics(desired=["COMP319-S01", "COMP319-S02"])
    permuted = deepcopy(base)
    permuted["desired_snapshot"]["included_session_ids"] = [
        "COMP319-S02",
        "COMP319-S01",
    ]
    permuted["session_dependencies"] = list(reversed(permuted["session_dependencies"]))

    assert canonical_exam_scope_semantics(permuted) == base
    assert derive_proposal_id(
        "EXAM_SCOPE", canonical_exam_scope_semantics(permuted)
    ) == derive_proposal_id("EXAM_SCOPE", base)


def test_exam_scope_rejects_cross_course_session_dependency_before_queue_creation() -> None:
    action = _semantics()
    action["session_dependencies"][0]["course_relation_page_id"] = "course-page-2"
    action["session_dependencies"][0]["course_key"] = "2026-1_COMP319-003"

    with pytest.raises(ValueError, match="Course must match"):
        canonical_exam_scope_semantics(action)


@pytest.mark.parametrize("source_dependencies", [[], [{}]])
def test_exam_scope_rejects_unsupported_source_dependencies_without_silent_drop(
    source_dependencies,
) -> None:
    action = _semantics()
    action["source_dependencies"] = source_dependencies

    with pytest.raises(ValueError, match="source_dependencies.*unsupported"):
        canonical_exam_scope_semantics(action)


@pytest.mark.parametrize("mirror", ["Source Ref", "Source Hash", "Source Version"])
def test_exam_scope_rejects_unbound_source_mirrors_even_when_null(mirror: str) -> None:
    proposal = build_exam_scope_proposal(
        "COMP319-E01",
        COURSE,
        None,
        ["COMP319-S01"],
        review_reason="human scope review",
    )
    properties = proposal.as_properties()
    properties[mirror] = None

    with pytest.raises(ValueError, match="mirrors are unsupported"):
        canonical_semantics_from_queue(properties)


def test_empty_scope_is_explicit_and_preserves_absent_vs_empty() -> None:
    empty = _semantics(desired=[])
    assert empty["schema"] == EXAM_SCOPE_APPROVAL_ACTION_SCHEMA
    assert empty["operation"] == "confirm_empty_scope"
    assert empty["old_snapshot"]["included_session_ids"] is None
    assert empty["desired_snapshot"]["included_session_ids"] == []

    with pytest.raises(ValueError):
        build_exam_scope_semantics(
            operation="confirm_scope",
            target_entity_id="COMP319-E01",
            course=COURSE,
            old_snapshot={"included_session_ids": None, "scope_confirmed": False},
            desired_snapshot={"included_session_ids": [], "scope_confirmed": True},
            session_dependencies=[],
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["desired_snapshot"]["included_session_ids"].append("COMP319-S01"),
        lambda value: value["session_dependencies"].append(value["session_dependencies"][0]),
    ],
)
def test_duplicate_membership_is_rejected_before_identity_sorting(mutator) -> None:
    action = _semantics()
    mutator(action)
    with pytest.raises(ValueError):
        canonical_exam_scope_semantics(action)


def test_exam_scope_proposal_has_strict_queue_mirrors_and_enqueue_only_behavior() -> None:
    proposal = build_exam_scope_proposal(
        "COMP319-E01",
        COURSE,
        None,
        ["COMP319-S01"],
        review_reason="human scope review",
    )
    properties = proposal.as_properties()
    assert proposal.proposal_type is ProposalType.EXAM_SCOPE
    assert properties["Course"] == {"relation": [{"id": COURSE_PAGE_ID}]}
    assert canonical_semantics_from_queue(properties) == proposal.proposed_action

    reader = FakeNotionReader(
        exams=[exam_record(exam_id="COMP319-E01", included_sessions=None)],
    )
    writer = FakeNotionAdapter(reader)
    row = upsert_proposal(writer, proposal)
    assert row["Proposal ID"] == proposal.proposal_id
    assert writer.target_mutations == 0
    assert reader.exams["COMP319-E01"]["Scope Confirmed"] is False

    tampered = dict(properties)
    tampered["Proposed Action"] = canonical_action_json(
        build_exam_scope_semantics(
            operation="confirm_scope",
            target_entity_id="COMP319-E01",
            course=COURSE,
            old_snapshot={"included_session_ids": None, "scope_confirmed": False},
            desired_snapshot={"included_session_ids": ["COMP319-S02"], "scope_confirmed": True},
            session_dependencies=[
                {
                    "session_id": "COMP319-S02",
                    "course_relation_page_id": COURSE_PAGE_ID,
                    "course_key": COURSE_KEY,
                    "source_ref": None,
                    "source_hash": None,
                    "source_version": None,
                }
            ],
        )
    )
    with pytest.raises(PolicyViolation):
        upsert_proposal(writer, tampered)


def test_exam_scope_queue_boundary_reports_unsupported_source_dependencies() -> None:
    proposal = build_exam_scope_proposal(
        "COMP319-E01",
        COURSE,
        None,
        ["COMP319-S01"],
        review_reason="human scope review",
    )
    properties = proposal.as_properties()
    action = deepcopy(properties["Proposed Action"])
    action["source_dependencies"] = []
    properties["Proposed Action"] = action
    reader = FakeNotionReader(exams=[exam_record(exam_id="COMP319-E01")])
    writer = FakeNotionAdapter(reader)

    with pytest.raises(PolicyViolation, match="source_dependencies.*unsupported"):
        upsert_proposal(writer, properties)
    assert writer.queue_rows == []
