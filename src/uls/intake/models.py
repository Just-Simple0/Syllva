"""Typed local intake records and request input values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class IntakeStatus(_ValueEnum):
    OBSERVED = "OBSERVED"
    NEEDS_INPUT = "NEEDS_INPUT"
    PLANNED = "PLANNED"
    REGISTERED = "REGISTERED"
    MOVING = "MOVING"
    ORGANIZED = "ORGANIZED"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"


class RequestType(_ValueEnum):
    ASSIGN_COURSE = "ASSIGN_COURSE"
    FILE_DETAILS = "FILE_DETAILS"


class FileKind(_ValueEnum):
    TRANSCRIPT = "TRANSCRIPT"
    MATERIAL_PDF = "MATERIAL_PDF"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class SessionMode(_ValueEnum):
    NEW = "NEW"
    EXISTING = "EXISTING"


class MaterialRole(_ValueEnum):
    LECTURE_SLIDES = "Lecture Slides"
    TEXTBOOK = "Textbook"


@dataclass(frozen=True)
class FileDetails:
    """Normalized USER values for one FILE_DETAILS request."""

    intake_id: str
    course_key: str | None = None
    kind: str | None = None
    actual_date: str | None = None
    session_mode: str | None = None
    session_id: str | None = None
    session_no: int | None = None
    material_role: str | None = None


@dataclass(frozen=True)
class RequestInput:
    """Provider-neutral snapshot of a submitted Input Request page."""

    request_key: str
    request_type: str
    intake_ids: tuple[str, ...] = ()
    course_key: str | None = None
    kind: str | None = None
    actual_date: str | None = None
    session_mode: str | None = None
    session_id: str | None = None
    session_no: int | None = None
    material_role: str | None = None
    submitted: bool = False
    cancelled: bool = False
    input_hash: str | None = None
    request_revision_hash: str | None = None
    provider_page_id: str | None = None
    workspace_fingerprint: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    status: IntakeStatus
    reasons: tuple[str, ...] = ()
    course_key: str | None = None
    kind: str | None = None
    target_session_id: str | None = None
    material_role: str | None = None
    actual_date: str | None = None
    session_no: int | None = None

    @property
    def needs_input(self) -> bool:
        return self.status is IntakeStatus.NEEDS_INPUT


@dataclass(frozen=True)
class DriveFileMetadata:
    provider: str
    file_id: str
    name: str
    mime_type: str
    parent_id: str
    modified_time: str | None = None
    size: int | None = None
    trashed: bool = False
    web_url: str | None = None
    app_properties: dict[str, str] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider, self.file_id


@dataclass(frozen=True)
class IntakePlanSpec:
    intake_id: str
    request_key: str
    request_revision_hash: str
    plan_revision: str
    target_snapshot: dict[str, Any]
    decision: RoutingDecision


def as_kind(value: str | FileKind | None) -> str | None:
    if isinstance(value, FileKind):
        return value.value
    return value.upper() if isinstance(value, str) else None


__all__ = [
    "DriveFileMetadata",
    "FileDetails",
    "FileKind",
    "IntakePlanSpec",
    "IntakeStatus",
    "MaterialRole",
    "RequestInput",
    "RequestType",
    "RoutingDecision",
    "SessionMode",
    "as_kind",
]
