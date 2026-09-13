"""Input Request validation and deterministic pre-receipt generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from uls.domain.ids import parse_course_key, parse_entity_id

from .identity import canonical_json, derive_plan_revision, derive_request_key, derive_request_revision, sha256_hex
from .models import FileKind, IntakeStatus, RequestInput, RequestType, RoutingDecision, SessionMode


class RequestValidationError(ValueError):
    """A submitted request does not satisfy the exact input matrix."""

    code = "NEEDS_INPUT"

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class RequestGeneration:
    request_type: str
    intake_ids: tuple[str, ...]
    observation_refs: tuple[tuple[str, str], ...]
    target_snapshot: dict[str, Any]
    target_snapshot_hash: str
    request_revision_hash: str
    request_key: str


def validate_request_input(
    request: RequestInput,
    *,
    intake_exists: Callable[[str], bool] | None = None,
    course_exists: Callable[[str], bool] | None = None,
    session_course: Callable[[str], str | None] | None = None,
) -> tuple[str, ...]:
    """Return field-level errors without performing provider mutations."""

    errors: list[str] = []
    request_type = _enum_value(request.request_type)
    if request_type not in {RequestType.ASSIGN_COURSE.value, RequestType.FILE_DETAILS.value}:
        errors.append("Request Type must be ASSIGN_COURSE or FILE_DETAILS")
    if not request.request_key or re.fullmatch(r"[0-9a-f]{64}", request.request_key) is None:
        errors.append("Request Key must be one lowercase 64-hex string")
    intake_ids = tuple(request.intake_ids)
    if len(set(intake_ids)) != len(intake_ids) or any(not value for value in intake_ids):
        errors.append("Intake Items must contain unique non-empty IDs")
    if intake_exists is not None:
        errors.extend(f"unknown intake item: {value}" for value in intake_ids if not intake_exists(value))
    if request.cancelled:
        errors.append("cancelled requests cannot be claimed")
    if not request.submitted:
        errors.append("request must be submitted before claim")
    if request.course_key is not None:
        try:
            parse_course_key(request.course_key)
        except Exception:
            errors.append("Course must contain a valid Course Key")
        if course_exists is not None and not course_exists(request.course_key):
            errors.append("Course is not a current canonical course")

    common_nonempty = {
        "kind": request.kind,
        "actual_date": request.actual_date,
        "session_mode": request.session_mode,
        "session_id": request.session_id,
        "session_no": request.session_no,
        "material_role": request.material_role,
    }
    if request_type == RequestType.ASSIGN_COURSE.value:
        if not intake_ids:
            errors.append("ASSIGN_COURSE requires one or more Intake Items")
        if request.course_key is None:
            errors.append("ASSIGN_COURSE requires exactly one Course")
        for field_name, value in common_nonempty.items():
            if value is not None:
                errors.append(f"{field_name} must be empty for ASSIGN_COURSE")
    elif request_type == RequestType.FILE_DETAILS.value:
        if len(intake_ids) != 1:
            errors.append("FILE_DETAILS requires exactly one Intake Item")
        if request.course_key is None:
            errors.append("FILE_DETAILS requires exactly one Course")
        kind = _enum_value(request.kind)
        if kind not in {FileKind.TRANSCRIPT.value, FileKind.MATERIAL_PDF.value}:
            errors.append("FILE_DETAILS Kind must be TRANSCRIPT or MATERIAL_PDF")
        if kind == FileKind.TRANSCRIPT.value:
            _validate_transcript_fields(request, errors, session_course)
        elif kind == FileKind.MATERIAL_PDF.value:
            _validate_pdf_fields(request, errors)
    if request.raw:
        forbidden = {
            "Verified", "Scope Confirmed", "Decision", "Decision By", "Approval",
            "Usage Operation", "Study Note", "Learning Input",
        }
        present = forbidden.intersection(request.raw)
        if present:
            errors.append("approval and study-note fields are not accepted by intake requests")
    return tuple(errors)


def require_valid_request(
    request: RequestInput,
    **kwargs: Any,
) -> RequestInput:
    errors = validate_request_input(request, **kwargs)
    if errors:
        raise RequestValidationError(errors)
    return request


def build_request_generation(
    *,
    provider: str,
    provider_account_binding_id: str,
    semester: str,
    request_type: str,
    observation_refs: Sequence[Sequence[str] | Mapping[str, Any]],
    config_fingerprint: str,
    target_snapshot: Mapping[str, Any],
    intake_ids: Sequence[str],
) -> RequestGeneration:
    """Compute and freeze a pre-receipt generation before page creation."""

    snapshot = dict(target_snapshot)
    target_hash = sha256_hex(["intake.target-snapshot.v1", snapshot])
    revision = derive_request_revision(
        provider=provider,
        provider_account_binding_id=provider_account_binding_id,
        semester=semester,
        request_type=request_type,
        observation_refs=observation_refs,
        config_fingerprint=config_fingerprint,
        target_snapshot_hash=target_hash,
    )
    key = derive_request_key(
        provider=provider,
        provider_account_binding_id=provider_account_binding_id,
        semester=semester,
        request_type=request_type,
        request_revision_hash=revision,
        intake_ids=intake_ids,
        target_snapshot_hash=target_hash,
    )
    return RequestGeneration(
        request_type=request_type,
        intake_ids=tuple(sorted(intake_ids)),
        observation_refs=tuple(_observation_ref(value) for value in observation_refs),
        target_snapshot=snapshot,
        target_snapshot_hash=target_hash,
        request_revision_hash=revision,
        request_key=key,
    )


def build_plan_revision(
    *,
    provider: str,
    request_key: str,
    normalized_user_hash: str,
    target_snapshot_hash: str,
    workspace_fingerprint: str,
) -> str:
    return derive_plan_revision(
        provider=provider,
        request_key=request_key,
        normalized_user_hash=normalized_user_hash,
        target_snapshot_hash=target_snapshot_hash,
        workspace_fingerprint=workspace_fingerprint,
    )


def normalized_user_hash(request: RequestInput) -> str:
    """Hash only USER-controlled execution fields, excluding title/links."""

    return sha256_hex(
        [
            "intake.user-input.v1",
            request.request_type,
            sorted(request.intake_ids),
            request.course_key,
            request.kind,
            request.actual_date,
            request.session_mode,
            request.session_id,
            request.session_no,
            request.material_role,
        ]
    )


def _strict_submitted(value: Any) -> bool:
    """Only a literal boolean True is submitted; malformed values fail safe."""

    return value is True


def _strict_cancelled(value: Any) -> bool:
    """Only a literal boolean False clears cancellation; anything else stays cancelled."""

    return value is not False


def request_from_mapping(value: Mapping[str, Any]) -> RequestInput:
    """Coerce provider-neutral normalized Notion values into RequestInput."""

    def relation_ids(raw: Any) -> tuple[str, ...]:
        if raw is None:
            return ()
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, Mapping):
            raw = raw.get("relation", raw.get("ids", []))
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ()
        result: list[str] = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, Mapping):
                identifier = item.get("id", item.get("value"))
                if isinstance(identifier, str):
                    result.append(identifier)
        return tuple(result)

    def scalar(name: str, *aliases: str) -> Any:
        for key in (name, *aliases):
            if key in value:
                current = value[key]
                if isinstance(current, Mapping):
                    kind = current.get("type")
                    if kind in current:
                        current = current[kind]
                    elif "name" in current:
                        current = current["name"]
                    elif "value" in current:
                        current = current["value"]
                return current
        return None

    session_no = scalar("Session No")
    if session_no is not None and not isinstance(session_no, int):
        try:
            session_no = int(session_no)
        except (TypeError, ValueError):
            pass
    return RequestInput(
        request_key=str(scalar("Request Key") or ""),
        request_type=str(scalar("Request Type") or ""),
        intake_ids=relation_ids(value.get("Intake Items", value.get("대상 접수"))),
        course_key=_relation_course_key(value.get("Course", value.get("과목")), scalar("Course Key")),
        kind=_string_or_none(scalar("Kind", "종류")),
        actual_date=_date_string(value.get("Actual Date", value.get("실제 날짜"))),
        session_mode=_string_or_none(scalar("Session Mode", "수업 모드")),
        session_id=_relation_one(value.get("Session", value.get("수업"))),
        session_no=session_no if isinstance(session_no, int) else None,
        material_role=_string_or_none(scalar("Material Role", "자료 역할")),
        submitted=_strict_submitted(scalar("Submitted", "제출")),
        cancelled=_strict_cancelled(scalar("Cancelled", "취소")),
        input_hash=_string_or_none(scalar("Input Hash")),
        request_revision_hash=_string_or_none(scalar("Request Revision Hash")),
        provider_page_id=_string_or_none(value.get("id", value.get("page_id"))),
        workspace_fingerprint=_string_or_none(scalar("Workspace Fingerprint")),
        raw=dict(value),
    )


def _validate_transcript_fields(
    request: RequestInput,
    errors: list[str],
    session_course: Callable[[str], str | None] | None,
) -> None:
    if request.actual_date is None:
        errors.append("transcript FILE_DETAILS requires an actual lecture date")
    else:
        try:
            date.fromisoformat(request.actual_date)
        except ValueError:
            errors.append("Actual Date must be YYYY-MM-DD")
    mode = _enum_value(request.session_mode)
    if mode not in {SessionMode.NEW.value, SessionMode.EXISTING.value}:
        errors.append("transcript FILE_DETAILS requires NEW or EXISTING Session Mode")
    if mode == SessionMode.NEW.value:
        if request.session_id is not None:
            errors.append("NEW transcript must not select an existing Session")
        if request.session_no is not None and (
            isinstance(request.session_no, bool)
            or not isinstance(request.session_no, int)
            or request.session_no <= 0
        ):
            errors.append("new transcript Session No must be a positive integer or blank")
    if mode == SessionMode.EXISTING.value:
        if not request.session_id:
            errors.append("EXISTING transcript requires exactly one Session")
        if request.session_no is not None:
            errors.append("EXISTING transcript must not set Session No")
        if request.session_id and session_course is not None and session_course(request.session_id) != request.course_key:
            errors.append("selected Session does not belong to selected Course")


def _validate_pdf_fields(request: RequestInput, errors: list[str]) -> None:
    if request.material_role not in {"Lecture Slides", "Textbook"}:
        errors.append("PDF FILE_DETAILS requires Lecture Slides or Textbook role")
    for name, value in (
        ("Actual Date", request.actual_date),
        ("Session Mode", request.session_mode),
        ("Session", request.session_id),
        ("Session No", request.session_no),
    ):
        if value is not None:
            errors.append(f"{name} must be empty for PDF FILE_DETAILS")


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _observation_ref(value: Sequence[str] | Mapping[str, Any]) -> tuple[str, str]:
    if isinstance(value, Mapping):
        return (str(value["intake_id"]), str(value["immutable_observation_hash"]))
    return (str(value[0]), str(value[1]))


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _date_string(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("start")
    return value if isinstance(value, str) else None


def _relation_one(value: Any) -> str | None:
    values = value
    if isinstance(values, Mapping):
        values = values.get("relation", values.get("ids", []))
    if isinstance(values, str):
        return values
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and len(values) == 1:
        item = values[0]
        if isinstance(item, str):
            return item
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            return item["id"]
    return None


def _relation_course_key(value: Any, fallback: Any) -> str | None:
    relation = _relation_one(value)
    if relation:
        return relation
    return fallback if isinstance(fallback, str) and fallback else None


__all__ = [
    "RequestGeneration",
    "RequestValidationError",
    "build_plan_revision",
    "build_request_generation",
    "normalized_user_hash",
    "request_from_mapping",
    "require_valid_request",
    "validate_request_input",
]
