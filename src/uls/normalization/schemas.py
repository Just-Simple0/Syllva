"""Typed schemas for deterministic normalized derivatives.

The transcript body is deliberately kept outside front matter.  A transcript
status therefore has one source of truth: ``front_matter.status``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import yaml

from uls.domain.enums import DerivativeStatus, to_derivative_status
from uls.domain.source_ref import SourceFingerprint, SourceRef


TRANSCRIPT_SCHEMA = "uls.transcript.v1"
ALLOWED_DERIVATIVE_STATUSES = frozenset(item.value for item in DerivativeStatus)


def _coerce_source_ref(value: SourceRef | Mapping[str, Any] | str) -> SourceRef:
    if isinstance(value, SourceRef):
        return value
    if isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id", value.get("id"))
        if not isinstance(provider, str) or not isinstance(file_id, str):
            raise ValueError("source_ref requires provider and file_id")
        web_url = value.get("web_url")
        return SourceRef(provider, file_id, web_url if isinstance(web_url, str) else None)
    if isinstance(value, str) and value.strip():
        # Structured SourceRef is normative.  Treat a bare ID as a local
        # compatibility input while still serializing a structured object.
        return SourceRef("google_drive", value.strip())
    raise TypeError("source_ref must be a SourceRef, mapping, or non-empty string")


def _iso_now(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise TypeError("normalized_at/now must be a datetime, non-empty string, or None")


@dataclass(frozen=True, order=True)
class TimestampMark:
    """A source timestamp and its LF-normalized Python ``str`` offset."""

    start_seconds: int
    char_offset: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_seconds, bool)
            or not isinstance(self.start_seconds, int)
            or self.start_seconds < 0
        ):
            raise ValueError("TimestampMark.start_seconds must be a non-negative integer")
        if (
            isinstance(self.char_offset, bool)
            or not isinstance(self.char_offset, int)
            or self.char_offset < 0
        ):
            raise ValueError("TimestampMark.char_offset must be a non-negative integer")

    @property
    def offset(self) -> int:
        return self.char_offset

    @property
    def seconds(self) -> int:
        return self.start_seconds

    def as_dict(self) -> dict[str, int]:
        return {
            "start_seconds": self.start_seconds,
            "char_offset": self.char_offset,
        }

    to_dict = as_dict


@dataclass(frozen=True)
class TranscriptFrontMatter:
    """The complete, typed ``uls.transcript.v1`` front matter."""

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
        if self.schema != TRANSCRIPT_SCHEMA:
            raise ValueError(f"schema must be {TRANSCRIPT_SCHEMA}")
        for value, name in (
            (self.entity_id, "entity_id"),
            (self.course_key, "course_key"),
            (self.source_hash, "source_hash"),
            (self.processor_version, "processor_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.source_ref, SourceRef):
            object.__setattr__(self, "source_ref", _coerce_source_ref(self.source_ref))
        if (
            isinstance(self.source_version, bool)
            or not isinstance(self.source_version, int)
            or self.source_version < 1
        ):
            raise ValueError("source_version must be a positive integer")
        object.__setattr__(self, "status", to_derivative_status(self.status))
        object.__setattr__(self, "normalized_at", _iso_now(self.normalized_at))

    @property
    def fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.source_version, self.source_hash)

    @property
    def source_fingerprint(self) -> SourceFingerprint:
        return self.fingerprint

    def as_dict(self) -> dict[str, Any]:
        # Keep this order equal to the frozen front-matter contract.  In
        # particular, ``status`` is emitted exactly once.
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
    def from_mapping(cls, value: Mapping[str, Any]) -> "TranscriptFrontMatter":
        if not isinstance(value, Mapping):
            raise TypeError("front matter must be a mapping")
        source_ref = value.get("source_ref")
        if source_ref is None:
            # A provider/file pair at the top level is not canonical but is a
            # useful compatibility bridge for simple fakes.
            source_ref = {
                "provider": value.get("provider", "google_drive"),
                "file_id": value.get("file_id"),
                "web_url": value.get("web_url"),
            }
        required = (
            "schema",
            "entity_id",
            "course_key",
            "source_hash",
            "source_version",
            "processor_version",
            "normalized_at",
            "status",
        )
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"front matter missing required fields: {', '.join(missing)}")
        for name in (
            "schema",
            "entity_id",
            "course_key",
            "source_hash",
            "processor_version",
        ):
            if not isinstance(value[name], str):
                raise TypeError(f"front matter {name} must be a string")
        return cls(
            schema=value["schema"],
            entity_id=value["entity_id"],
            course_key=value["course_key"],
            source_ref=_coerce_source_ref(source_ref),
            source_hash=value["source_hash"],
            source_version=value["source_version"],
            processor_version=value["processor_version"],
            normalized_at=value["normalized_at"],
            status=value["status"],
        )


# The shorter name is convenient for generic derivative validators and is
# retained as an alias, not a second schema.
NormalizedFrontMatter = TranscriptFrontMatter
NormalizedTranscriptFrontMatter = TranscriptFrontMatter


@dataclass(frozen=True)
class NormalizedTranscript:
    """Normalized transcript with verbatim LF-normalized body and sidecar marks."""

    front_matter: TranscriptFrontMatter
    body: str
    marks: tuple[TimestampMark, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.front_matter, TranscriptFrontMatter):
            object.__setattr__(
                self,
                "front_matter",
                TranscriptFrontMatter.from_mapping(self.front_matter),
            )
        if not isinstance(self.body, str):
            raise TypeError("normalized transcript body must be a string")
        if "\r" in self.body:
            raise ValueError("normalized transcript body must use LF newlines")
        normalized_marks: list[TimestampMark] = []
        for mark in self.marks:
            if isinstance(mark, TimestampMark):
                normalized_marks.append(mark)
            elif isinstance(mark, Mapping):
                normalized_marks.append(
                    TimestampMark(int(mark["start_seconds"]), int(mark["char_offset"]))
                )
            else:
                normalized_marks.append(TimestampMark(*mark))
        marks = tuple(normalized_marks)
        if any(mark.char_offset > len(self.body) for mark in marks):
            raise ValueError("timestamp mark offset exceeds body length")
        if tuple(sorted(marks, key=lambda mark: mark.char_offset)) != marks:
            raise ValueError("timestamp marks must be ordered by offset")
        object.__setattr__(self, "marks", marks)

    @property
    def status(self) -> DerivativeStatus:
        """The only public transcript status, sourced from front matter."""

        return self.front_matter.status  # type: ignore[return-value]

    @property
    def schema(self) -> str:
        return self.front_matter.schema

    @property
    def entity_id(self) -> str:
        return self.front_matter.entity_id

    @property
    def course_key(self) -> str:
        return self.front_matter.course_key

    @property
    def fingerprint(self) -> SourceFingerprint:
        return self.front_matter.fingerprint

    @property
    def source_ref(self) -> SourceRef:
        return self.front_matter.source_ref

    @property
    def metadata(self) -> TranscriptFrontMatter:
        return self.front_matter

    @property
    def frontmatter(self) -> TranscriptFrontMatter:
        return self.front_matter

    @property
    def timestamp_marks(self) -> tuple[TimestampMark, ...]:
        """Compatibility name for the deterministic timestamp sidecar."""

        return self.marks

    @property
    def sidecar_index(self) -> tuple[TimestampMark, ...]:
        return self.marks

    def as_front_matter(self) -> dict[str, Any]:
        return self.front_matter.as_dict()

    def as_dict(self) -> dict[str, Any]:
        """Return the typed derivative without duplicating status in the body."""

        return {
            "front_matter": self.front_matter.as_dict(),
            "body": self.body,
            "marks": [mark.as_dict() for mark in self.marks],
        }

    to_dict = as_dict

    def to_markdown(self) -> str:
        front = yaml.safe_dump(
            self.front_matter.as_dict(),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).rstrip("\n")
        return f"---\n{front}\n---\n{self.body}"

    render = to_markdown
    serialize = to_markdown
    markdown = to_markdown


__all__ = [
    "ALLOWED_DERIVATIVE_STATUSES",
    "NormalizedFrontMatter",
    "NormalizedTranscript",
    "NormalizedTranscriptFrontMatter",
    "TRANSCRIPT_SCHEMA",
    "TimestampMark",
    "TranscriptFrontMatter",
]
