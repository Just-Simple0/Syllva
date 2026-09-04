"""Deterministic page/timestamp chunk selection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from uls.domain.models import PageLocator, TimeLocator, is_contained
from uls.normalization.schemas import NormalizedTranscript, TimestampMark
from uls.normalization.validators import ParsedDerivative, parse_derivative


@dataclass(frozen=True)
class DerivativeChunk:
    entity_id: str
    locator: PageLocator | TimeLocator
    content: str
    start_offset: int = 0
    end_offset: int = 0
    symbolic_hint: str | None = None


def derivative_parts(derivative: Any) -> tuple[str | None, str, tuple[TimestampMark, ...], dict[str, Any]]:
    """Return entity/body/marks/front matter from common fake-adapter shapes."""

    if isinstance(derivative, NormalizedTranscript):
        return derivative.entity_id, derivative.body, derivative.marks, derivative.as_front_matter()
    if isinstance(derivative, ParsedDerivative):
        return derivative.entity_id, derivative.body, _marks(derivative.body), dict(derivative.front_matter)
    if isinstance(derivative, str):
        try:
            parsed = parse_derivative(derivative)
        except (TypeError, ValueError):
            normalized = _lf(derivative)
            # A string that claims to be a Markdown derivative but has broken
            # front matter must not be reinterpreted as source body text.
            # Plain-text compatibility remains available for simple material
            # fakes that do not use the canonical wrapper.
            if normalized.startswith("---\n"):
                raise
            return None, normalized, _marks(normalized), {}
        return parsed.entity_id, parsed.body, _marks(parsed.body), dict(parsed.front_matter)
    if isinstance(derivative, Mapping):
        front = _front_mapping(derivative.get("front_matter", derivative.get("metadata", {})))
        body = _lf(str(derivative.get("body", "")))
        marks = _coerce_marks(derivative.get("marks", derivative.get("timestamp_marks", ())))
        entity = front.get("entity_id", derivative.get("entity_id"))
        return entity if isinstance(entity, str) else None, body, tuple(marks or _marks(body)), front
    # Provider fakes sometimes expose a small typed derivative object rather
    # than a mapping.  This branch is deliberately attribute-only and does
    # not import a provider SDK.
    if derivative is not None and hasattr(derivative, "body"):
        front = _front_mapping(
            getattr(derivative, "front_matter", getattr(derivative, "metadata", {}))
        )
        body = _lf(str(getattr(derivative, "body", "")))
        marks = _coerce_marks(
            getattr(derivative, "marks", getattr(derivative, "timestamp_marks", ()))
        )
        entity = front.get("entity_id", getattr(derivative, "entity_id", None))
        return entity if isinstance(entity, str) else None, body, tuple(marks or _marks(body)), front
    raise TypeError("unsupported normalized derivative")


def _front_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for name in ("as_dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            result = method()
            if isinstance(result, Mapping):
                return dict(result)
    return {}


def _coerce_marks(value: Any) -> list[TimestampMark]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    marks: list[TimestampMark] = []
    for item in value:
        if isinstance(item, TimestampMark):
            marks.append(item)
        elif isinstance(item, Mapping):
            marks.append(TimestampMark(int(item["start_seconds"]), int(item["char_offset"])))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
            marks.append(TimestampMark(int(item[0]), int(item[1])))
    return marks


def timestamp_chunks(
    derivative: Any,
    *,
    entity_id: str | None = None,
    max_chunks: int | None = None,
) -> list[DerivativeChunk]:
    """Split a transcript at sidecar offsets while preserving body text."""

    derived_entity, body, marks, _ = derivative_parts(derivative)
    actual_entity = entity_id or derived_entity
    if not actual_entity:
        raise ValueError("timestamp chunks require an entity_id")
    ordered = sorted(marks, key=lambda mark: mark.char_offset)
    chunks: list[DerivativeChunk] = []
    if not ordered:
        if body:
            chunks.append(
                DerivativeChunk(
                    actual_entity,
                    TimeLocator(actual_entity, 0, 0),
                    body,
                    0,
                    len(body),
                )
            )
        return chunks[:max_chunks] if max_chunks is not None else chunks

    if ordered[0].char_offset > 0:
        end_seconds = max(0, ordered[0].start_seconds - 1)
        preamble = body[: ordered[0].char_offset]
        if preamble:
            chunks.append(
                DerivativeChunk(
                    actual_entity,
                    TimeLocator(actual_entity, 0, end_seconds),
                    preamble,
                    0,
                    ordered[0].char_offset,
                )
            )
    for index, mark in enumerate(ordered):
        end_offset = ordered[index + 1].char_offset if index + 1 < len(ordered) else len(body)
        end_seconds = (
            max(mark.start_seconds, ordered[index + 1].start_seconds - 1)
            if index + 1 < len(ordered)
            else mark.start_seconds
        )
        content = body[mark.char_offset:end_offset]
        if not content:
            continue
        chunks.append(
            DerivativeChunk(
                actual_entity,
                TimeLocator(actual_entity, mark.start_seconds, max(mark.start_seconds, end_seconds)),
                content,
                mark.char_offset,
                end_offset,
            )
        )
    return chunks[:max_chunks] if max_chunks is not None else chunks


_PAGE_MARKER_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:(?:#{1,6})\s*)?(?:page|페이지)\s*[:#-]?\s*([1-9][0-9]*)\b"
    r"|(?:\[\[\s*page\s*[:=]\s*([1-9][0-9]*)\s*\]\])"
    r"|(?:<!--\s*page\s*[:=]\s*([1-9][0-9]*)\s*-->)"
)


def page_chunks(
    derivative: Any,
    *,
    entity_id: str,
    start_page: int | None = None,
    end_page: int | None = None,
    max_chunks: int | None = None,
) -> list[DerivativeChunk]:
    """Split a material derivative using explicit page markers when present."""

    _, body, _, _ = derivative_parts(derivative)
    markers: list[tuple[int, int]] = []
    for match in _PAGE_MARKER_RE.finditer(body):
        page_text = next((value for value in match.groups() if value is not None), None)
        if page_text is not None:
            markers.append((match.start(), int(page_text)))
    allowed_start = start_page if start_page is not None else 1
    allowed_end = end_page if end_page is not None else 10**9
    result: list[DerivativeChunk] = []
    if not markers:
        if body and allowed_start <= allowed_end:
            result.append(
                DerivativeChunk(
                    entity_id,
                    PageLocator(entity_id, allowed_start, allowed_end if end_page is not None else allowed_start),
                    body,
                    0,
                    len(body),
                )
            )
        return result[:max_chunks] if max_chunks is not None else result

    if markers[0][0] > 0 and allowed_start <= 1 <= allowed_end:
        result.append(DerivativeChunk(entity_id, PageLocator(entity_id, 1, 1), body[: markers[0][0]], 0, markers[0][0]))
    for index, (offset, page) in enumerate(markers):
        end_offset = markers[index + 1][0] if index + 1 < len(markers) else len(body)
        next_page = markers[index + 1][1] if index + 1 < len(markers) else None
        page_end = next_page - 1 if next_page is not None and next_page > page else page
        if end_page is not None and next_page is None:
            page_end = end_page
        page_end = max(page, min(page_end, allowed_end))
        if allowed_start <= page <= allowed_end and end_offset > offset:
            result.append(
                DerivativeChunk(
                    entity_id,
                    PageLocator(entity_id, page, page_end),
                    body[offset:end_offset],
                    offset,
                    end_offset,
                )
            )
    return result[:max_chunks] if max_chunks is not None else result


def select_chunks(
    chunks: Iterable[DerivativeChunk],
    query: str | None = None,
    *,
    max_chunks: int | None = None,
) -> list[DerivativeChunk]:
    values = list(chunks)
    if query:
        terms = [piece.casefold() for piece in re.findall(r"[0-9A-Za-z가-힣]+", query) if piece]
        if terms:
            matching = [chunk for chunk in values if all(term in chunk.content.casefold() for term in terms)]
            if matching:
                values = matching
    return values[:max_chunks] if max_chunks is not None else values


def find_chunk_containing(chunks: Iterable[DerivativeChunk], locator: Any) -> DerivativeChunk | None:
    for chunk in chunks:
        try:
            if is_contained(locator, chunk.locator):
                return chunk
        except Exception:
            continue
    return None


def _lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _marks(body: str) -> tuple[TimestampMark, ...]:
    from uls.normalization.transcript import extract_timestamp_marks

    return extract_timestamp_marks(_lf(body))[0]


__all__ = [
    "DerivativeChunk",
    "derivative_parts",
    "find_chunk_containing",
    "page_chunks",
    "select_chunks",
    "timestamp_chunks",
]
