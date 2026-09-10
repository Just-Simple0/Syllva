"""Deterministic normalization for official Activity instructions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import yaml  # type: ignore[import-untyped]

from uls.domain.course_identity import parse_course_key
from uls.domain.enums import DerivativeStatus, to_derivative_status
from uls.domain.ids import strict_entity_id
from uls.domain.source_ref import SourceFingerprint, SourceRef

from .transcript import normalize_newlines

ACTIVITY_SCHEMA = "uls.activity.v1"


def _source_ref(value: SourceRef | Mapping[str, Any] | str) -> SourceRef:
    if isinstance(value, SourceRef):
        return value
    if isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id", value.get("id"))
        web_url = value.get("web_url")
        if isinstance(provider, str) and isinstance(file_id, str) and provider.strip() and file_id.strip():
            return SourceRef(provider.strip(), file_id.strip(), web_url if isinstance(web_url, str) else None)
    if isinstance(value, str) and value.strip():
        return SourceRef("google_drive", value.strip())
    raise TypeError("activity source_ref must be a SourceRef or structured reference")


def _timestamp(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat()
    if isinstance(value, datetime):
        return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise TypeError("normalized_at must be a datetime, string, or None")


@dataclass(frozen=True)
class ActivityFrontMatter:
    schema: str
    entity_id: str
    course_key: str
    source_ref: SourceRef
    source_hash: str
    source_version: int
    processor_version: str
    normalized_at: str
    status: DerivativeStatus | str = DerivativeStatus.READY

    def __post_init__(self) -> None:
        if self.schema != ACTIVITY_SCHEMA:
            raise ValueError(f"schema must be {ACTIVITY_SCHEMA}")
        for value, name in (
            (self.entity_id, "entity_id"),
            (self.course_key, "course_key"),
            (self.source_hash, "source_hash"),
            (self.processor_version, "processor_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if strict_entity_id(self.entity_id, "A") is None:
            raise ValueError("entity_id must be a canonical Activity ID")
        try:
            parse_course_key(self.course_key)
        except Exception as exc:
            raise ValueError("course_key must be a canonical Course Key") from exc
        object.__setattr__(self, "source_ref", _source_ref(self.source_ref))
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int) or self.source_version < 1:
            raise ValueError("source_version must be a positive integer")
        object.__setattr__(self, "status", to_derivative_status(self.status))
        object.__setattr__(self, "normalized_at", _timestamp(self.normalized_at))

    @property
    def fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.source_version, self.source_hash)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "entity_id": self.entity_id,
            "course_key": self.course_key,
            "source_ref": {
                "provider": self.source_ref.provider,
                "file_id": self.source_ref.file_id,
                "web_url": self.source_ref.web_url,
            },
            "source_hash": self.source_hash,
            "source_version": self.source_version,
            "processor_version": self.processor_version,
            "normalized_at": self.normalized_at,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
        }

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ActivityFrontMatter:
        if not isinstance(value, Mapping):
            raise TypeError("activity front matter must be a mapping")
        required = (
            "schema", "entity_id", "course_key", "source_ref", "source_hash",
            "source_version", "processor_version", "normalized_at", "status",
        )
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError("activity front matter missing: " + ", ".join(missing))
        return cls(
            value["schema"], value["entity_id"], value["course_key"],
            _source_ref(value["source_ref"]), value["source_hash"],
            value["source_version"], value["processor_version"],
            value["normalized_at"], value["status"],
        )


@dataclass(frozen=True)
class NormalizedActivity:
    front_matter: ActivityFrontMatter
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.front_matter, ActivityFrontMatter):
            object.__setattr__(self, "front_matter", ActivityFrontMatter.from_mapping(self.front_matter))
        if not isinstance(self.body, str):
            raise TypeError("normalized activity body must be a string")
        if "\r" in self.body:
            raise ValueError("normalized activity body must use LF newlines")

    @property
    def entity_id(self) -> str:
        return self.front_matter.entity_id

    @property
    def schema(self) -> str:
        return self.front_matter.schema

    @property
    def status(self) -> DerivativeStatus:
        return self.front_matter.status  # type: ignore[return-value]

    @property
    def course_key(self) -> str:
        return self.front_matter.course_key

    @property
    def fingerprint(self) -> SourceFingerprint:
        return self.front_matter.fingerprint

    @property
    def source_ref(self) -> SourceRef:
        return self.front_matter.source_ref

    def as_front_matter(self) -> dict[str, Any]:
        return self.front_matter.as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {"front_matter": self.front_matter.as_dict(), "body": self.body}

    to_dict = as_dict

    def to_markdown(self) -> str:
        front = yaml.safe_dump(
            self.front_matter.as_dict(), allow_unicode=True, sort_keys=False,
            default_flow_style=False,
        ).rstrip("\n")
        return f"---\n{front}\n---\n{self.body}"

    render = to_markdown
    serialize = to_markdown
    markdown = to_markdown


def normalize_activity_instructions(
    raw: str | bytes,
    *,
    entity_id: str,
    course_key: str,
    source_ref: SourceRef | Mapping[str, Any] | str,
    source_hash: str,
    source_version: int,
    processor_version: str,
    now: datetime | str | None = None,
    status: DerivativeStatus | str = DerivativeStatus.READY,
) -> NormalizedActivity:
    """Normalize only newlines and wrap the supplied instruction body."""

    body = normalize_newlines(raw)
    front = ActivityFrontMatter(
        ACTIVITY_SCHEMA, entity_id, course_key, _source_ref(source_ref), source_hash,
        source_version, processor_version, _timestamp(now), status,
    )
    return NormalizedActivity(front, body)


normalize_activity = normalize_activity_instructions
normalize = normalize_activity_instructions


__all__ = [
    "ACTIVITY_SCHEMA",
    "ActivityFrontMatter",
    "NormalizedActivity",
    "normalize",
    "normalize_activity",
    "normalize_activity_instructions",
]
