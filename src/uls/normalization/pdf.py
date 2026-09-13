"""Bounded deterministic text-PDF extraction.

This module extracts source text only.  It does not summarize, call an AI
provider, or claim a scanned/encrypted document is complete.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import yaml  # type: ignore[import-untyped]

from uls.domain.source_ref import SourceRef

MATERIAL_SCHEMA = "uls.material.v1"


class PDFContentStatus(str, Enum):
    READY = "Ready"
    PARTIAL = "Partial"
    NEEDS_REVIEW = "Needs Review"
    UNAVAILABLE = "Unavailable"
    FAILED = "Failed"


@dataclass(frozen=True)
class NormalizedPDF:
    schema: str
    entity_id: str
    course_key: str
    source_ref: SourceRef
    source_hash: str
    source_version: int
    processor_version: str
    normalized_at: str
    status: PDFContentStatus | str
    page_count: int
    text: str
    extracted_pages: int
    missing_pages: tuple[int, ...] = ()
    reason: str | None = None
    page_texts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema != MATERIAL_SCHEMA:
            raise ValueError(f"schema must be {MATERIAL_SCHEMA}")
        if not isinstance(self.source_ref, SourceRef):
            raise TypeError("source_ref must be a SourceRef")
        if not isinstance(self.source_hash, str) or not self.source_hash:
            raise ValueError("source_hash is required")
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int) or self.source_version < 1:
            raise ValueError("source_version must be positive")
        if isinstance(self.page_count, bool) or not isinstance(self.page_count, int) or self.page_count < 0:
            raise ValueError("page_count must be non-negative")
        if isinstance(self.extracted_pages, bool) or not isinstance(self.extracted_pages, int) or not 0 <= self.extracted_pages <= self.page_count:
            raise ValueError("extracted_pages is outside page count")
        if self.page_texts and len(self.page_texts) != self.page_count:
            raise ValueError("page_texts must contain one entry per PDF page")
        if any(not isinstance(value, str) for value in self.page_texts):
            raise TypeError("page_texts must contain strings")
        object.__setattr__(self, "status", PDFContentStatus(self.status))

    @property
    def content_status(self) -> str:
        return self.status.value if isinstance(self.status, PDFContentStatus) else str(self.status)

    @property
    def text_status(self) -> str:
        return self.content_status

    @property
    def text_source(self) -> str:
        return "PDF Extract" if self.extracted_pages else "Unavailable"

    @property
    def derivative_status(self) -> str:
        """Return the lower-case status used by normalized retrieval metadata."""

        status = PDFContentStatus(self.status)
        return {
            PDFContentStatus.READY: "ready",
            PDFContentStatus.PARTIAL: "partial",
            PDFContentStatus.NEEDS_REVIEW: "needs_review",
            PDFContentStatus.UNAVAILABLE: "failed",
            PDFContentStatus.FAILED: "failed",
        }[status]

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
            "status": self.derivative_status,
            "page_count": self.page_count,
            "extracted_pages": self.extracted_pages,
            "missing_pages": list(self.missing_pages),
            "reason": self.reason,
        }

    def to_markdown(self) -> str:
        front_matter = yaml.safe_dump(self.as_dict(), allow_unicode=True, sort_keys=False).rstrip("\n")
        return f"---\n{front_matter}\n---\n{self._page_body()}"

    def _page_body(self) -> str:
        if self.page_texts:
            pages: list[str] = []
            for page_number, page_text in enumerate(self.page_texts, start=1):
                marker = f"[[page:{page_number}]]"
                pages.append(f"{marker}\n{page_text}" if page_text else marker)
            return "\n\n".join(pages)
        if self.text and self.page_count == 1 and self.extracted_pages == 1:
            return f"[[page:1]]\n{self.text}"
        return self.text

    render = to_markdown
    serialize = to_markdown


def extract_pdf(
    raw: bytes,
    *,
    entity_id: str,
    course_key: str,
    source_ref: SourceRef,
    source_hash: str | None = None,
    source_version: int = 1,
    processor_version: str = "1.3.0",
    max_bytes: int = 20_000_000,
    now: datetime | str | None = None,
) -> NormalizedPDF:
    """Extract text from a bounded PDF, retaining explicit partial states."""

    if not isinstance(raw, bytes):
        raise TypeError("PDF content must be bytes")
    digest = source_hash or "sha256:" + hashlib.sha256(raw).hexdigest()
    normalized_at = _timestamp(now)
    if len(raw) > max_bytes:
        return _result(
            entity_id=entity_id, course_key=course_key, source_ref=source_ref,
            source_hash=digest, source_version=source_version, processor_version=processor_version,
            normalized_at=normalized_at, status=PDFContentStatus.NEEDS_REVIEW,
            reason="PDF exceeds the bounded extraction limit",
        )
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw), strict=False)
    except ImportError:
        return _result(
            entity_id=entity_id, course_key=course_key, source_ref=source_ref,
            source_hash=digest, source_version=source_version, processor_version=processor_version,
            normalized_at=normalized_at, status=PDFContentStatus.UNAVAILABLE,
            reason="PDF extraction dependency is not installed",
        )
    except Exception:  # noqa: BLE001 - malformed/encrypted PDF is an honest extraction state
        return _result(
            entity_id=entity_id, course_key=course_key, source_ref=source_ref,
            source_hash=digest, source_version=source_version, processor_version=processor_version,
            normalized_at=normalized_at, status=PDFContentStatus.NEEDS_REVIEW,
            reason="PDF cannot be opened or is encrypted",
        )

    pages = len(reader.pages)
    extracted: list[str] = []
    page_texts: list[str] = []
    missing: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text()
        except Exception:  # noqa: BLE001 - one unreadable page makes extraction partial
            text = None
        if not isinstance(text, str) or not text.strip():
            missing.append(index)
            page_texts.append("")
            continue
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
        extracted.append(normalized_text)
        page_texts.append(normalized_text)
    extracted_text = "\n\n".join(extracted)
    if pages == 0:
        status = PDFContentStatus.NEEDS_REVIEW
        reason = "PDF has no pages"
    elif missing:
        status = PDFContentStatus.PARTIAL if extracted else PDFContentStatus.NEEDS_REVIEW
        reason = "text could not be extracted for page(s): " + ", ".join(map(str, missing))
    else:
        status = PDFContentStatus.READY
        reason = None
    return _result(
        entity_id=entity_id, course_key=course_key, source_ref=source_ref,
        source_hash=digest, source_version=source_version, processor_version=processor_version,
        normalized_at=normalized_at, status=status, page_count=pages,
        text=extracted_text,
        extracted_pages=len(extracted),
        missing_pages=tuple(missing),
        reason=reason,
        page_texts=tuple(page_texts),
    )


normalize_pdf = extract_pdf
normalize_material_pdf = extract_pdf


def _result(
    *,
    entity_id: str,
    course_key: str,
    source_ref: SourceRef,
    source_hash: str,
    source_version: int,
    processor_version: str,
    normalized_at: str,
    status: PDFContentStatus,
    page_count: int = 0,
    text: str = "",
    extracted_pages: int = 0,
    missing_pages: tuple[int, ...] = (),
    reason: str | None = None,
    page_texts: tuple[str, ...] = (),
) -> NormalizedPDF:
    return NormalizedPDF(
        schema=MATERIAL_SCHEMA,
        entity_id=entity_id,
        course_key=course_key,
        source_ref=source_ref,
        source_hash=source_hash,
        source_version=source_version,
        processor_version=processor_version,
        normalized_at=normalized_at,
        status=status,
        page_count=page_count,
        text=text,
        extracted_pages=extracted_pages,
        missing_pages=missing_pages,
        reason=reason,
        page_texts=page_texts,
    )


def _timestamp(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


__all__ = [
    "MATERIAL_SCHEMA",
    "NormalizedPDF",
    "PDFContentStatus",
    "extract_pdf",
    "normalize_material_pdf",
    "normalize_pdf",
]
