"""v2 chunking for locally normalized sources (implementation spec §8.8, errata E12/E14).

Chunks are built from the normalizer's STRUCTURED output, never by re-parsing
the Markdown body:

* PDF: one chunk per extracted page (``NormalizedPDF.page_texts``); a body line
  such as "Page 1" can no longer be mistaken for a page marker (E12);
* transcript: one chunk per timestamp segment from the sidecar marks
  (``NormalizedTranscript.marks``); a transcript without marks is one chunk at
  ``t00:00:00``.

A chunk longer than ``max_chars`` is split into ordered PARTS that keep the same
locator (E14): citing any part of page 3 still truthfully cites page 3. Splits
prefer paragraph, then line, then sentence boundaries and fall back to a hard cut.
Marker-only or empty chunks are not produced (they carry no SOURCE text).

This module depends only on domain and normalization types, so normalization no
longer imports retrieval (the normalization<->retrieval cycle noted in E12).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from syllva.domain.models import PageLocator, TimeLocator, serialize_locator
from syllva.normalization.pdf import NormalizedPDF
from syllva.normalization.schemas import NormalizedTranscript, TimestampMark

DEFAULT_MAX_CHARS = 4000
CHUNKS_SCHEMA = "syllva.chunks.v2"  # rows carry part/parts (errata E16)
CHUNKS_SCHEMA_VERSION = "2"  # derivatives.schema_version of chunk derivatives
_BOUNDARIES = (re.compile(r"\n\s*\n"), re.compile(r"\n"), re.compile(r"(?<=[.!?。])\s+"))


@dataclass(frozen=True)
class Chunk:
    locator: PageLocator | TimeLocator
    content: str
    start_offset: int  # offsets within the source text the chunk came from (page text / transcript body)
    end_offset: int
    part: int = 1
    parts: int = 1

    def as_row(self) -> dict[str, object]:
        return {
            "locator": serialize_locator(self.locator),
            "content": self.content,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "part": self.part,
            "parts": self.parts,
        }


def split_text(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Return (start, end) spans of ``text``, each at most ``max_chars`` long, covering all of it."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    spans: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > max_chars:
        window = text[start : start + max_chars]
        cut = 0
        for boundary in _BOUNDARIES:
            ends = [m.end() for m in boundary.finditer(window) if m.end() > max_chars // 4]
            if ends:
                cut = ends[-1]
                break
        if cut == 0:
            cut = max_chars
        spans.append((start, start + cut))
        start += cut
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _emit(locator: PageLocator | TimeLocator, text: str, base: int, max_chars: int) -> list[Chunk]:
    spans = [(a, b) for a, b in split_text(text, max_chars) if text[a:b].strip()]
    return [
        Chunk(locator, text[a:b].strip("\n"), base + a, base + b, index, len(spans))
        for index, (a, b) in enumerate(spans, start=1)
    ]


def pdf_chunks(pdf: NormalizedPDF, entity_id: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    pages: Sequence[str] = pdf.page_texts
    if not pages and pdf.text and pdf.page_count == 1 and pdf.extracted_pages == 1:
        pages = (pdf.text,)
    chunks: list[Chunk] = []
    for number, text in enumerate(pages, start=1):
        if text and text.strip():
            chunks.extend(_emit(PageLocator(entity_id, number, number), text, 0, max_chars))
    return chunks


def transcript_chunks(transcript: NormalizedTranscript, entity_id: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    body = transcript.body
    ordered: list[TimestampMark] = sorted(transcript.marks, key=lambda m: m.char_offset)
    if not ordered:
        return _emit(TimeLocator(entity_id, 0, 0), body, 0, max_chars) if body.strip() else []
    # A repeated or backwards timestamp is not a new time range (E14): its text joins the
    # current segment, so time ranges strictly increase and every locator is unique; with
    # parts numbered within a segment, a (locator, part) pair names exactly one piece of text.
    marks: list[TimestampMark] = []
    for mark in ordered:
        if not marks or mark.start_seconds > marks[-1].start_seconds:
            marks.append(mark)
    chunks: list[Chunk] = []
    first = marks[0]
    if first.char_offset > 0 and first.start_seconds > 0:
        chunks.extend(_emit(TimeLocator(entity_id, 0, first.start_seconds - 1), body[: first.char_offset], 0, max_chars))
    for index, mark in enumerate(marks):
        following = marks[index + 1] if index + 1 < len(marks) else None
        start_offset = 0 if index == 0 and mark.start_seconds == 0 else mark.char_offset  # untimed lead text at t0
        end_offset = following.char_offset if following else len(body)
        end_seconds = following.start_seconds - 1 if following else mark.start_seconds
        locator = TimeLocator(entity_id, mark.start_seconds, end_seconds)
        chunks.extend(_emit(locator, body[start_offset:end_offset], start_offset, max_chars))
    return chunks
