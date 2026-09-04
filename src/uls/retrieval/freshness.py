"""Freshness checks and stale-locator revalidation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from uls.domain.enums import FreshnessStatus
from uls.domain.models import PageLocator, TimeLocator, parse_locator
from uls.domain.source_ref import SourceFingerprint
from uls.enrichment.schemas import EnrichmentRecord, coerce_enrichment
from uls.normalization.schemas import NormalizedTranscript, TimestampMark, TRANSCRIPT_SCHEMA
from uls.normalization.validators import ParsedDerivative, parse_derivative, parse_transcript_derivative


@dataclass(frozen=True)
class FreshnessResult:
    status: FreshnessStatus
    current: SourceFingerprint
    based_on: SourceFingerprint

    @property
    def is_fresh(self) -> bool:
        return self.status is FreshnessStatus.FRESH


def check_freshness(
    record: EnrichmentRecord | Mapping[str, Any],
    current_fingerprint: SourceFingerprint,
) -> FreshnessStatus:
    enrichment = coerce_enrichment(record)
    if not isinstance(current_fingerprint, SourceFingerprint):
        raise TypeError("current_fingerprint must be a SourceFingerprint")
    return (
        FreshnessStatus.FRESH
        if enrichment.source_fingerprint == current_fingerprint
        else FreshnessStatus.STALE
    )


assess_freshness = check_freshness


def is_fresh(record: EnrichmentRecord | Mapping[str, Any], current: SourceFingerprint) -> bool:
    return check_freshness(record, current) is FreshnessStatus.FRESH


def is_stale(record: EnrichmentRecord | Mapping[str, Any], current: SourceFingerprint) -> bool:
    return check_freshness(record, current) is FreshnessStatus.STALE


def _derivative_view(
    value: Any,
) -> tuple[str | None, str, tuple[TimestampMark, ...], bool]:
    if isinstance(value, NormalizedTranscript):
        return value.entity_id, value.body, value.marks, True
    if isinstance(value, ParsedDerivative):
        entity = value.entity_id
        body = value.body
        _, marks = _marks_from_body(body)
        return entity, body, marks, _is_transcript_front(value.front_matter)
    if isinstance(value, str):
        try:
            parsed = parse_derivative(value)
        except (TypeError, ValueError):
            normalized = value.replace("\r\n", "\n").replace("\r", "\n")
            if normalized.startswith("---\n"):
                raise
            return None, normalized, _marks_from_body(normalized)[1], False
        _, marks = _marks_from_body(parsed.body)
        return parsed.entity_id, parsed.body, marks, _is_transcript_front(parsed.front_matter)
    if isinstance(value, Mapping):
        front_value = value.get("front_matter", value.get("metadata", {}))
        if isinstance(front_value, Mapping):
            front = front_value
        elif callable(getattr(front_value, "as_dict", None)):
            candidate = front_value.as_dict()
            front = candidate if isinstance(candidate, Mapping) else {}
        else:
            front = {}
        body = _lf(str(value.get("body", "")))
        marks_raw = value.get("marks", value.get("timestamp_marks", ()))
        marks: list[TimestampMark] = []
        if isinstance(marks_raw, Sequence) and not isinstance(marks_raw, (str, bytes)):
            for item in marks_raw:
                if isinstance(item, TimestampMark):
                    marks.append(item)
                elif isinstance(item, Mapping):
                    marks.append(TimestampMark(int(item["start_seconds"]), int(item["char_offset"])))
                elif isinstance(item, Sequence) and len(item) == 2:
                    marks.append(TimestampMark(int(item[0]), int(item[1])))
        if not marks:
            _, marks_tuple = _marks_from_body(body)
            marks = list(marks_tuple)
        entity = front.get("entity_id", value.get("entity_id"))
        is_transcript = _is_transcript_front(front) or (
            "marks" in value or "timestamp_marks" in value
        )
        return (
            entity if isinstance(entity, str) else None,
            body,
            tuple(marks),
            is_transcript,
        )
    if value is not None and hasattr(value, "body"):
        front_value = getattr(value, "front_matter", getattr(value, "metadata", {}))
        if isinstance(front_value, Mapping):
            front = front_value
        elif callable(getattr(front_value, "as_dict", None)):
            candidate = front_value.as_dict()
            front = candidate if isinstance(candidate, Mapping) else {}
        else:
            front = {}
        body = _lf(str(getattr(value, "body", "")))
        marks_raw = getattr(value, "marks", getattr(value, "timestamp_marks", ()))
        marks: list[TimestampMark] = []
        if isinstance(marks_raw, Sequence) and not isinstance(marks_raw, (str, bytes)):
            for item in marks_raw:
                if isinstance(item, TimestampMark):
                    marks.append(item)
                elif isinstance(item, Mapping):
                    marks.append(TimestampMark(int(item["start_seconds"]), int(item["char_offset"])))
                elif isinstance(item, Sequence) and len(item) == 2:
                    marks.append(TimestampMark(int(item[0]), int(item[1])))
        if not marks:
            marks = list(_marks_from_body(body)[1])
        entity = front.get("entity_id", getattr(value, "entity_id", None))
        is_transcript = _is_transcript_front(front) or any(
            hasattr(value, name) for name in ("marks", "timestamp_marks")
        )
        return (
            entity if isinstance(entity, str) else None,
            body,
            tuple(marks),
            is_transcript,
        )
    raise TypeError("unsupported derivative value")


def _is_transcript_front(front: Mapping[str, Any]) -> bool:
    return front.get("schema") == TRANSCRIPT_SCHEMA


def _marks_from_body(body: str) -> tuple[bool, tuple[TimestampMark, ...]]:
    # Local import avoids a normalization→retrieval import cycle.
    from uls.normalization.transcript import extract_timestamp_marks

    marks, failed = extract_timestamp_marks(body.replace("\r\n", "\n").replace("\r", "\n"))
    return failed, marks


def _lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def revalidate_locator(
    symbolic_hint: Any,
    current_derivative: Any,
) -> PageLocator | TimeLocator | None:
    """Resolve a stale symbolic topic/heading against the current derivative.

    Absolute locators found in ``symbolic_hint`` are intentionally ignored.
    Only current-body search results can produce the returned locator.
    """

    try:
        entity_id, body, marks, is_transcript = _derivative_view(current_derivative)
    except (TypeError, ValueError):
        return None
    if entity_id is None:
        entity = _hint_value(symbolic_hint, "entity_id", "entity", "id")
        entity_id = entity if isinstance(entity, str) else None
    if entity_id is None:
        return None
    terms = _hint_terms(symbolic_hint)
    if not terms:
        return None
    body_fold = body.casefold()
    position: int | None = None
    matched_term: str | None = None
    for term in terms:
        candidate = term.casefold()
        found = body_fold.find(candidate)
        if found >= 0 and (position is None or found < position):
            position = found
            matched_term = term
    if position is None:
        return None

    if marks:
        preceding = [mark for mark in marks if mark.char_offset <= position]
        if preceding:
            mark = preceding[-1]
            next_marks = [item for item in marks if item.char_offset > mark.char_offset]
            # Timestamp ranges are inclusive.  Keep this identical to
            # retrieval.chunking.timestamp_chunks: the next marker begins at
            # the following second, so this chunk ends one second earlier.
            end = (
                next_marks[0].start_seconds - 1
                if next_marks
                else mark.start_seconds
            )
            return TimeLocator(entity_id, mark.start_seconds, max(mark.start_seconds, end))
        # A topic can occur in a short preamble before the first timestamp.
        # Keep the revalidated locator typed as a transcript time range rather
        # than falling through to the page fallback.
        first = marks[0]
        return TimeLocator(entity_id, 0, max(0, first.start_seconds - 1))

    if is_transcript:
        # A transcript with no timestamp marks is still time-addressed.  Keep
        # its zero-mark locator identical to ``timestamp_chunks`` so stale
        # revalidation and capability containment use the same subtype/range.
        return TimeLocator(entity_id, 0, 0)

    # Page-bearing derivatives commonly use an explicit Page/페이지 heading or
    # marker.  Return the page containing the symbolic term.
    line_start = body.rfind("\n", 0, position) + 1
    line_end = body.find("\n", position)
    if line_end < 0:
        line_end = len(body)
    window_start = max(0, line_start - 500)
    window = body[window_start:line_end]
    page_matches = list(re.finditer(r"(?i)(?:page|페이지)\s*[:#-]?\s*([1-9][0-9]*)", window))
    if page_matches:
        page = int(page_matches[-1].group(1))
        return PageLocator(entity_id, page, page)
    return PageLocator(entity_id, 1, 1)


def _hint_value(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        wanted = {name.casefold().replace("_", "") for name in names}
        for key, item in value.items():
            if isinstance(key, str) and key.casefold().replace("_", "") in wanted:
                return item
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return None


def _hint_terms(value: Any) -> list[str]:
    terms: list[str] = []
    if isinstance(value, str):
        if not _looks_like_locator(value):
            terms.append(value.strip())
    elif isinstance(value, Mapping):
        for key in ("topic", "heading", "term", "text", "keyword", "section", "title"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                terms.append(item.strip())
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                terms.extend(str(part).strip() for part in item if str(part).strip())
        nested = value.get("symbolic_hint", value.get("hint"))
        if nested is not None and nested is not value:
            terms.extend(_hint_terms(nested))
    else:
        for name in ("topic", "heading", "term", "text", "keyword", "section", "title"):
            item = getattr(value, name, None)
            if isinstance(item, str) and item.strip():
                terms.append(item.strip())
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term.casefold() not in seen:
            result.append(term)
            seen.add(term.casefold())
    return result


def _looks_like_locator(value: str) -> bool:
    try:
        parse_locator(value)
    except Exception:
        return False
    return True


__all__ = [
    "FreshnessResult",
    "assess_freshness",
    "check_freshness",
    "is_fresh",
    "is_stale",
    "revalidate_locator",
]
