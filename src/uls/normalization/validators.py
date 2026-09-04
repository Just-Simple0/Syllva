"""Validation and parsing for normalized Markdown derivatives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from uls.domain.enums import DerivativeStatus
from uls.domain.errors import SourcePartialError
from uls.domain.source_ref import SourceFingerprint

from .schemas import NormalizedTranscript, TimestampMark, TranscriptFrontMatter


@dataclass(frozen=True)
class ParsedDerivative:
    """Generic parsed derivative used by retrieval for material/transcript text."""

    front_matter: Mapping[str, Any]
    body: str

    @property
    def schema(self) -> str | None:
        value = self.front_matter.get("schema")
        return value if isinstance(value, str) else None

    @property
    def entity_id(self) -> str | None:
        value = self.front_matter.get("entity_id")
        return value if isinstance(value, str) else None

    @property
    def source_version(self) -> int | None:
        value = self.front_matter.get("source_version")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def source_hash(self) -> str | None:
        value = self.front_matter.get("source_hash")
        return value if isinstance(value, str) else None

    @property
    def status(self) -> str | None:
        value = self.front_matter.get("status")
        return value if isinstance(value, str) else None


def split_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """Split a YAML-front-matter derivative with newline-only normalization."""

    if not isinstance(markdown, str):
        raise TypeError("derivative must be a string")
    # Derivative output is canonical LF, but accepting provider-written CRLF
    # here keeps parsing deterministic and preserves the same newline-only
    # normalization rule used by transcript normalization.
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not markdown.startswith("---\n"):
        raise ValueError("normalized derivative must start with YAML front matter")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise ValueError("normalized derivative front matter is unterminated")
    raw_front = markdown[4:end]
    parsed = yaml.safe_load(raw_front)
    if not isinstance(parsed, Mapping):
        raise ValueError("normalized derivative front matter must be a mapping")
    return dict(parsed), markdown[end + len("\n---\n") :]


def parse_derivative(markdown: str | NormalizedTranscript) -> ParsedDerivative:
    if isinstance(markdown, NormalizedTranscript):
        return ParsedDerivative(markdown.as_front_matter(), markdown.body)
    front, body = split_front_matter(markdown)
    return ParsedDerivative(front, body)


def parse_transcript_derivative(
    markdown: str | NormalizedTranscript,
) -> NormalizedTranscript:
    """Parse a transcript derivative and recover its deterministic sidecar."""

    if isinstance(markdown, NormalizedTranscript):
        validate_normalized_transcript(markdown)
        return markdown
    parsed = parse_derivative(markdown)
    front = TranscriptFrontMatter.from_mapping(parsed.front_matter)
    # Import lazily to keep the schema module independent from extraction.
    from .transcript import extract_timestamp_marks

    marks, extraction_failed = extract_timestamp_marks(parsed.body)
    status = front.status
    if extraction_failed and status is not DerivativeStatus.PARTIAL:
        front = TranscriptFrontMatter(
            schema=front.schema,
            entity_id=front.entity_id,
            course_key=front.course_key,
            source_ref=front.source_ref,
            source_hash=front.source_hash,
            source_version=front.source_version,
            processor_version=front.processor_version,
            normalized_at=front.normalized_at,
            status=DerivativeStatus.PARTIAL,
        )
    return NormalizedTranscript(front, parsed.body, marks)


def validate_front_matter(front_matter: TranscriptFrontMatter | Mapping[str, Any]) -> bool:
    """Validate the typed front matter and return ``True`` on success."""

    if not isinstance(front_matter, TranscriptFrontMatter):
        front_matter = TranscriptFrontMatter.from_mapping(front_matter)
    if front_matter.status not in set(DerivativeStatus):
        raise ValueError(f"unsupported derivative status: {front_matter.status!r}")
    return True


def validate_normalized_transcript(
    transcript: NormalizedTranscript | str,
    *,
    current_fingerprint: SourceFingerprint | None = None,
    require_ready: bool = False,
) -> bool:
    """Validate schema/marks and enforce the Partial/Ready gate.

    ``partial`` is a valid, explicitly limited output.  Callers that require
    a complete derivative opt into ``require_ready`` and receive a structured
    ``SOURCE_PARTIAL`` error.
    """

    if isinstance(transcript, str):
        transcript = parse_transcript_derivative(transcript)
    if not isinstance(transcript, NormalizedTranscript):
        raise TypeError("expected NormalizedTranscript or derivative Markdown")
    validate_front_matter(transcript.front_matter)
    if transcript.front_matter.entity_id != transcript.entity_id:
        raise ValueError("front matter entity_id mismatch")
    for mark in transcript.marks:
        if not isinstance(mark, TimestampMark):
            raise TypeError("marks must contain TimestampMark values")
        if mark.char_offset >= len(transcript.body):
            raise ValueError("timestamp mark offset must point inside body")
        if transcript.body[mark.char_offset] not in "[【(":
            raise ValueError("timestamp mark offset must point to marker opening")
    if current_fingerprint is not None and transcript.fingerprint != current_fingerprint:
        raise SourcePartialError(
            "derivative fingerprint does not match the current canonical source",
            details={
                "derivative": transcript.fingerprint,
                "current": current_fingerprint,
            },
        )
    if require_ready and transcript.status is DerivativeStatus.PARTIAL:
        raise SourcePartialError(
            "partial derivative cannot pass a ready-only validation gate",
            details={"status": transcript.status.value},
        )
    return True


def validate_derivative(
    derivative: NormalizedTranscript | str,
    *,
    current_fingerprint: SourceFingerprint | None = None,
    require_ready: bool = False,
) -> bool:
    return validate_normalized_transcript(
        derivative,
        current_fingerprint=current_fingerprint,
        require_ready=require_ready,
    )


def validate_derivative_fingerprint(
    derivative: NormalizedTranscript | str,
    current_fingerprint: SourceFingerprint,
) -> bool:
    return validate_normalized_transcript(
        derivative,
        current_fingerprint=current_fingerprint,
    )


__all__ = [
    "ParsedDerivative",
    "parse_derivative",
    "parse_transcript_derivative",
    "split_front_matter",
    "validate_derivative",
    "validate_derivative_fingerprint",
    "validate_front_matter",
    "validate_normalized_transcript",
]
