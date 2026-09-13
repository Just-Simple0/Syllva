"""Deterministic intake routing and immutable plan creation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

from uls.domain.ids import parse_course_key
from uls.state.models import IntakeItem

from .identity import sha256_hex
from .models import FileDetails, FileKind, IntakeStatus, RoutingDecision, SessionMode
from .requests import RequestGeneration, build_request_generation


def route_intake(
    item: IntakeItem | Mapping[str, Any],
    *,
    course_key: str | None = None,
    kind: str | None = None,
    actual_date: str | None = None,
    session_mode: str | None = None,
    session_id: str | None = None,
    session_no: int | None = None,
    material_role: str | None = None,
    known_course_keys: Iterable[str] = (),
    duplicate_file_ids: Iterable[str] = (),
) -> RoutingDecision:
    """Resolve only explicitly supplied values; ambiguity remains input."""

    get = item.get if isinstance(item, Mapping) else lambda name, default=None: getattr(item, name, default)
    reasons: list[str] = []
    selected_course = course_key or get("selected_course_key")
    configured_courses = set(known_course_keys)
    if selected_course is not None:
        try:
            parse_course_key(selected_course)
        except Exception:
            reasons.append("Course Key is invalid")
        if configured_courses and selected_course not in configured_courses:
            reasons.append("Course is not in the current semester registry")
    else:
        candidates = get("course_candidates_json", [])
        if isinstance(candidates, str):
            try:
                import json

                candidates = json.loads(candidates)
            except (TypeError, ValueError):
                candidates = []
        if isinstance(candidates, Sequence) and len(candidates) == 1:
            candidate = candidates[0]
            candidate = candidate.get("course_key") if isinstance(candidate, Mapping) else candidate
            if isinstance(candidate, str) and candidate in configured_courses:
                selected_course = candidate
        if selected_course is None:
            reasons.append("Course selection is required")

    selected_kind = kind or get("selected_kind")
    if selected_kind is None:
        selected_kind = get("observed_kind")
    selected_kind = _kind_value(selected_kind)
    if selected_kind not in {FileKind.TRANSCRIPT.value, FileKind.MATERIAL_PDF.value}:
        reasons.append("File kind must be explicitly selected")

    if duplicate_file_ids:
        reasons.append(
            "same content exists under another provider file ID; duplicate candidate requires review"
        )

    if selected_kind == FileKind.TRANSCRIPT.value:
        if not actual_date:
            reasons.append("actual lecture date is required")
        else:
            try:
                date.fromisoformat(actual_date)
            except ValueError:
                reasons.append("actual lecture date must be YYYY-MM-DD")
        mode = _kind_value(session_mode or get("session_mode"))
        if mode not in {SessionMode.NEW.value, SessionMode.EXISTING.value}:
            reasons.append("NEW or EXISTING Session selection is required")
        if mode == SessionMode.NEW.value and session_id:
            reasons.append("NEW transcript cannot select an existing Session")
        if mode == SessionMode.EXISTING.value and not session_id:
            reasons.append("EXISTING transcript requires one exact Session")
        if mode == SessionMode.EXISTING.value and session_no is not None:
            reasons.append("EXISTING transcript cannot set Session No")
        if mode == SessionMode.NEW.value and session_no is not None and (
            isinstance(session_no, bool) or not isinstance(session_no, int) or session_no <= 0
        ):
            reasons.append("Session No must be a positive integer when supplied")
        if material_role:
            reasons.append("Material Role is forbidden for transcript intake")
    elif selected_kind == FileKind.MATERIAL_PDF.value:
        if material_role not in {"Lecture Slides", "Textbook"}:
            reasons.append("PDF material role must be Lecture Slides or Textbook")
        if any(value is not None for value in (actual_date, session_mode, session_id, session_no)):
            reasons.append("date and Session fields are forbidden for PDF intake")

    if reasons:
        return RoutingDecision(
            status=IntakeStatus.NEEDS_INPUT,
            reasons=tuple(dict.fromkeys(reasons)),
            course_key=selected_course,
            kind=selected_kind,
            target_session_id=session_id,
            material_role=material_role,
            actual_date=actual_date,
            session_no=session_no,
        )
    return RoutingDecision(
        status=IntakeStatus.PLANNED,
        reasons=(),
        course_key=selected_course,
        kind=selected_kind,
        target_session_id=session_id,
        material_role=material_role,
        actual_date=actual_date,
        session_no=session_no,
    )


def make_request_generation(
    *,
    item: IntakeItem,
    request_type: str,
    provider: str,
    provider_account_binding_id: str,
    config_fingerprint: str,
    target_snapshot: Mapping[str, Any] | None = None,
) -> RequestGeneration:
    """Build the pre-receipt key from durable observation metadata only."""

    return build_request_generation(
        provider=provider,
        provider_account_binding_id=provider_account_binding_id,
        semester=item.semester,
        request_type=request_type,
        observation_refs=[(
            item.intake_id,
            sha256_hex(["intake.observation.v1", item.source_hash, item.source_version]),
        )],
        config_fingerprint=config_fingerprint,
        target_snapshot=target_snapshot or {
            "intake_id": item.intake_id,
            "course_key": None,
            "kind": None,
            "session": None,
        },
        intake_ids=[item.intake_id],
    )


def make_plan_revision(
    *,
    request_key: str,
    request_revision_hash: str,
    normalized_user_hash: str,
    target_snapshot: Mapping[str, Any],
    workspace_fingerprint: str,
    provider: str = "google_drive",
) -> str:
    from .requests import build_plan_revision

    return build_plan_revision(
        provider=provider,
        request_key=request_key,
        normalized_user_hash=normalized_user_hash,
        target_snapshot_hash=sha256_hex(["intake.target-snapshot.v1", dict(target_snapshot)]),
        workspace_fingerprint=workspace_fingerprint,
    )


def _kind_value(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    if not isinstance(value, str):
        return None
    upper = value.upper()
    if upper == "MATERIAL":
        return FileKind.MATERIAL_PDF.value
    return upper


__all__ = ["make_plan_revision", "make_request_generation", "route_intake"]
