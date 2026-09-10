"""Enqueue-only Exam scope proposals with canonical Phase 5 identity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from uls.adapters.notion.base import Proposal, ProposalType, upsert_proposal
from uls.domain.approval_identity import (
    build_exam_scope_semantics,
    derive_proposal_id,
)
from uls.domain.course_identity import CourseIdentity
from uls.domain.errors import PolicyViolation
from uls.domain.ids import strict_entity_id


def build_exam_scope_proposal(
    exam_id: str,
    course: CourseIdentity | Mapping[str, Any],
    old_included_session_ids: Sequence[str] | None,
    desired_included_session_ids: Sequence[str],
    *,
    old_scope_confirmed: bool = False,
    session_dependencies: Sequence[Mapping[str, Any]] | None = None,
    source_dependencies: Sequence[Mapping[str, Any]] | None = None,
    evidence: Any = None,
    review_reason: str | None = None,
    processor_version: str = "1.2.0",
) -> Proposal:
    """Build a strict Queue proposal without performing a write."""

    if strict_entity_id(exam_id, "E") is None:
        raise PolicyViolation("exam_id must be a canonical E ID")
    course_mapping = course.as_dict() if isinstance(course, CourseIdentity) else dict(course)
    desired_ids = list(desired_included_session_ids)
    old_ids = None if old_included_session_ids is None else list(old_included_session_ids)
    operation = "confirm_empty_scope" if not desired_ids else "confirm_scope"
    dependencies = [
        _dependency_for_session(session_id, course_mapping)
        for session_id in desired_ids
    ] if session_dependencies is None else [dict(item) for item in session_dependencies]
    semantics = build_exam_scope_semantics(
        operation=operation,
        target_entity_id=exam_id,
        course=course_mapping,
        old_snapshot={
            "included_session_ids": old_ids,
            "scope_confirmed": old_scope_confirmed,
        },
        desired_snapshot={
            "included_session_ids": desired_ids,
            "scope_confirmed": True,
        },
        session_dependencies=dependencies,
        source_dependencies=source_dependencies,
        evidence=evidence,
        review_reason=review_reason,
        processor_version=processor_version,
    )
    proposal_id = derive_proposal_id(ProposalType.EXAM_SCOPE.value, semantics)
    return Proposal(
        proposal_id=proposal_id,
        proposal_type=ProposalType.EXAM_SCOPE,
        target_entity_id=exam_id,
        proposed_action=semantics,
        target_db="Exams",
        name=f"Exam scope · {exam_id}",
    )


def propose_exam_scope(
    adapter: Any,
    *args: Any,
    automation_queue_id: str | None = None,
    automation_queue_db_id: str | None = None,
    automation_queue_ids: set[str] | None = None,
    **kwargs: Any,
) -> Any:
    """Build and enqueue one proposal through the guarded producer path."""

    proposal = build_exam_scope_proposal(*args, **kwargs)
    return upsert_proposal(
        adapter,
        proposal,
        automation_queue_id=automation_queue_id,
        automation_queue_db_id=automation_queue_db_id,
        automation_queue_ids=automation_queue_ids,
    )


def _dependency_for_session(session_id: str, course: Mapping[str, Any]) -> dict[str, Any]:
    if strict_entity_id(session_id, "S") is None:
        raise PolicyViolation("session dependencies require canonical Session IDs")
    relation = course.get("relation_page_id", course.get("course_relation_page_id"))
    course_key = course.get("course_key")
    return {
        "session_id": session_id,
        "course_relation_page_id": relation,
        "course_key": course_key,
        "source_ref": None,
        "source_hash": None,
        "source_version": None,
    }


__all__ = ["build_exam_scope_proposal", "propose_exam_scope"]
