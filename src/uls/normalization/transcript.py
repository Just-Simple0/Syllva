"""Deterministic transcript normalization.

Normalization is intentionally boring: CRLF/CR become LF and every other
character stays in the body.  Timestamp markers are parsed into a sidecar
index; they are never removed, rewritten, or used to rewrite the transcript.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from uls.domain.source_ref import SourceRef

from .schemas import (
    NormalizedTranscript,
    TimestampMark,
    TranscriptFrontMatter,
    TRANSCRIPT_SCHEMA,
)


# Square brackets are the Alt export form.  The paired Unicode/round forms
# are accepted as harmless provider variants while preserving their spelling
# in the body.  A marker's offset is always the opening-bracket index.
_MARKER_RE = re.compile(
    r"(?P<open>\[|【|\()(?P<stamp>[^\]\】\)]*)(?P<close>\]|】|\))"
)
_TIMESTAMP_TEXT_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}\Z")
_TIMESTAMP_LIKE_RE = re.compile(r"\d[^\]\】\)]*:[^\]\】\)]*:[^\]\】\)]*")


def normalize_newlines(raw: str | bytes) -> str:
    """Return ``raw`` with only CRLF/CR newline normalization applied."""

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise TypeError("raw transcript must be str or UTF-8 bytes")
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def timestamp_to_seconds(value: str) -> int:
    """Parse an ``H:MM:SS``/``HH:MM:SS`` timestamp deterministically."""

    if not isinstance(value, str) or _TIMESTAMP_TEXT_RE.fullmatch(value) is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    hours, minutes, seconds = (int(piece) for piece in value.split(":"))
    if minutes > 59 or seconds > 59:
        raise ValueError(f"invalid timestamp: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def _paired_marker_is_valid(opening: str, closing: str) -> bool:
    return (opening, closing) in {("[", "]"), ("【", "】"), ("(", ")")}


def extract_timestamp_marks(body: str) -> tuple[tuple[TimestampMark, ...], bool]:
    """Extract sidecar marks and report whether any timestamp parse failed.

    Ordinary bracketed prose is ignored.  A bracket whose contents clearly
    attempts a three-part timestamp is considered a timestamp marker; if its
    digits/ranges are invalid the derivative is marked ``partial`` rather
    than silently claiming ``ready``.
    """

    if not isinstance(body, str):
        raise TypeError("body must be a string")
    marks: list[TimestampMark] = []
    had_failure = False
    for match in _MARKER_RE.finditer(body):
        opening = match.group("open")
        closing = match.group("close")
        if not _paired_marker_is_valid(opening, closing):
            continue
        stamp = match.group("stamp").strip()
        if _TIMESTAMP_TEXT_RE.fullmatch(stamp):
            try:
                marks.append(TimestampMark(timestamp_to_seconds(stamp), match.start()))
            except ValueError:
                had_failure = True
        elif _TIMESTAMP_LIKE_RE.fullmatch(stamp):
            # This is a timestamp-shaped marker (for example 00:61:02 or
            # 1x:02:03), so treating it as ordinary prose would hide a
            # provider extraction failure.
            had_failure = True
    # A provider can emit a truncated opening marker.  When its contents are
    # timestamp-shaped it is an extraction failure, not ordinary bracketed
    # prose, and the derivative must be marked Partial.
    for opening_match in re.finditer(r"(\[|【|\()[^\]\】\)]*", body):
        opening_content = opening_match.group(0)[1:].strip()
        if (
            body.find("\n", opening_match.start(), opening_match.end()) >= 0
            and _TIMESTAMP_LIKE_RE.fullmatch(opening_content) is None
        ):
            continue
        if opening_match.end() < len(body) and _paired_marker_is_valid(
            opening_match.group(0)[0], body[opening_match.end()]
        ):
            continue
        if _TIMESTAMP_LIKE_RE.fullmatch(opening_content):
            had_failure = True
    return tuple(marks), had_failure


def normalize_transcript(
    raw: str | bytes,
    *,
    entity_id: str,
    course_key: str,
    source_ref: SourceRef | Mapping[str, Any] | str,
    source_hash: str,
    source_version: int,
    processor_version: str,
    now: datetime | str | None = None,
) -> NormalizedTranscript:
    """Create a typed ``uls.transcript.v1`` derivative.

    The function is deterministic for fixed inputs except for the caller's
    explicit ``now`` value.  No LLM or cleanup/correction pass is involved.
    """

    body = normalize_newlines(raw)
    marks, had_failure = extract_timestamp_marks(body)
    front_matter = TranscriptFrontMatter(
        schema=TRANSCRIPT_SCHEMA,
        entity_id=entity_id,
        course_key=course_key,
        source_ref=source_ref,  # type: ignore[arg-type]
        source_hash=source_hash,
        source_version=source_version,
        processor_version=processor_version,
        normalized_at=now,
        status="partial" if had_failure else "ready",
    )
    return NormalizedTranscript(front_matter=front_matter, body=body, marks=marks)


# A short alias is useful for normalization pipelines that already use a
# generic ``normalize`` entry point.
normalize = normalize_transcript
parse_timestamp = timestamp_to_seconds
extract_marks = extract_timestamp_marks


def render_normalized_transcript(transcript: NormalizedTranscript) -> str:
    if not isinstance(transcript, NormalizedTranscript):
        raise TypeError("expected NormalizedTranscript")
    return transcript.to_markdown()


__all__ = [
    "extract_marks",
    "extract_timestamp_marks",
    "normalize",
    "normalize_newlines",
    "normalize_transcript",
    "parse_timestamp",
    "render_normalized_transcript",
    "timestamp_to_seconds",
]
