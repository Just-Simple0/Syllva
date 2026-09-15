"""Regression tests for bounded PDF extraction (src/uls/normalization/pdf.py).

There was no prior test coverage for extract_pdf() at all.  These tests
cover the three review findings:

1. len(reader.pages) must fail closed (Needs Review), not raise, when a
   PDF opens but its page tree cannot actually be read.
2. A PDF whose declared page count exceeds max_pages must be rejected
   before the per-page extraction loop runs, so an oversized page tree
   can never become an unbounded CPU/memory sink.
3. Extracted text is bounded by max_extracted_chars; once the bound is
   hit, extraction stops and the result becomes Partial (or Needs Review
   if nothing was extracted yet) -- Partial must never silently become
   Ready just because a size bound was hit.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.source_ref import SourceRef
from uls.normalization.pdf import PDFContentStatus, extract_pdf

_SOURCE_REF = SourceRef("google_drive", "file-1")


def _make_pdf_bytes(page_texts: list[str | None]) -> bytes:
    """Build a minimal, real, multi-page text PDF without external deps.

    Each entry becomes one page.  ``None`` produces a page with no text
    content stream (extract_text() returns an empty string for it, same
    as a scanned/image-only page).  This hand-built structure is real PDF
    syntax parsed by pypdf, not a mock -- the tests below exercise the
    actual PdfReader/extract_text() code path.
    """

    def content_stream(text: str | None) -> bytes:
        if not text:
            return b""
        escaped = text.replace(chr(92), chr(92)*2).replace("(", chr(92)+"(").replace(")", chr(92)+")")
        return f"BT /F1 12 Tf 10 700 Td ({escaped}) Tj ET".encode("latin-1")

    n = len(page_texts)
    catalog_num, pages_num, font_num = 1, 2, 3
    page_nums = [4 + 2 * i for i in range(n)]
    content_nums = [5 + 2 * i for i in range(n)]

    obj_bodies: dict[int, bytes] = {
        catalog_num: f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode(),
        font_num: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids = " ".join(f"{pn} 0 R" for pn in page_nums)
    obj_bodies[pages_num] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
    for i in range(n):
        pn, cn = page_nums[i], content_nums[i]
        obj_bodies[pn] = (
            f"<< /Type /Page /Parent {pages_num} 0 R "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {cn} 0 R >>"
        ).encode()
        stream_body = content_stream(page_texts[i])
        obj_bodies[cn] = (
            f"<< /Length {len(stream_body)} >>\nstream\n".encode()
            + stream_body
            + b"\nendstream"
        )

    buf = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    max_obj = max(obj_bodies)
    for num in range(1, max_obj + 1):
        offsets[num] = len(buf)
        body = obj_bodies.get(num, b"<< >>")
        buf += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(buf)
    buf += f"xref\n0 {max_obj + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for num in range(1, max_obj + 1):
        buf += f"{offsets[num]:010d} 00000 n \n".encode()
    buf += (
        f"trailer\n<< /Size {max_obj + 1} /Root {catalog_num} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(buf)


def _extract(raw: bytes, **overrides):
    return extract_pdf(
        raw,
        entity_id="COMP319-M03",
        course_key="2026-1_COMP319-002",
        source_ref=_SOURCE_REF,
        source_hash="sha256:fixed",
        **overrides,
    )


def test_normal_pdf_with_all_pages_readable_is_ready() -> None:
    raw = _make_pdf_bytes(["Hello World", "Second Page Text"])
    result = _extract(raw)
    assert result.status == PDFContentStatus.READY
    assert result.page_count == 2
    assert result.extracted_pages == 2
    assert "Hello World" in result.text
    assert "Second Page Text" in result.text


def test_corrupted_pdf_bytes_fail_closed_to_needs_review() -> None:
    raw = b"%PDF-1.4\nthis is not a real pdf body at all\n%%EOF"
    result = _extract(raw)
    assert result.status == PDFContentStatus.NEEDS_REVIEW
    assert result.extracted_pages == 0
    assert result.reason


def test_page_tree_read_failure_after_open_fails_closed_not_uncaught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: len(reader.pages) used to run outside the try/except that
    catches open failures.  A PDF that opens but whose page tree raises on
    access (e.g. certain encrypted/malformed structures) must still convert
    to Needs Review instead of propagating an uncaught exception up through
    the intake worker.
    """

    import pypdf

    # ``PdfReader.pages`` is a property returning a lazy _VirtualList backed
    # by get_num_pages()/get_page(); len(reader.pages) calls get_num_pages()
    # under the hood.  Patching get_num_pages (rather than the ``pages``
    # attribute itself, which is a property/data-descriptor and cannot be
    # shadowed by an instance attribute) reliably simulates a page tree
    # that fails only when its size is actually read.
    def boom_get_num_pages(self, *args, **kwargs):
        raise ValueError("simulated page-tree parse failure")

    monkeypatch.setattr(pypdf.PdfReader, "get_num_pages", boom_get_num_pages)
    raw = _make_pdf_bytes(["Hello World"])
    result = _extract(raw)
    assert result.status == PDFContentStatus.NEEDS_REVIEW
    assert result.reason == "PDF cannot be opened or is encrypted"


def test_page_count_over_bound_is_rejected_before_extraction_loop() -> None:
    raw = _make_pdf_bytes(["one", "two", "three", "four"])
    result = _extract(raw, max_pages=2)
    assert result.status == PDFContentStatus.NEEDS_REVIEW
    assert result.page_count == 4
    assert result.extracted_pages == 0
    assert result.text == ""
    assert "page-count limit" in (result.reason or "")


def test_extracted_text_over_char_bound_becomes_partial_not_ready() -> None:
    pages = ["A" * 100, "B" * 100, "C" * 100]
    raw = _make_pdf_bytes(pages)
    result = _extract(raw, max_extracted_chars=150)

    assert result.status == PDFContentStatus.PARTIAL
    assert result.page_count == 3
    # Only the first page fits fully within the 150-char budget; the
    # second page's 100 chars would push the running total to 200 and
    # must therefore be rejected, along with everything after it.
    assert result.extracted_pages == 1
    assert "A" * 100 in result.text
    assert "B" * 100 not in result.text
    assert "C" * 100 not in result.text
    assert "output-size" in (result.reason or "")
    assert 2 in result.missing_pages and 3 in result.missing_pages


def test_extracted_text_over_char_bound_with_nothing_extracted_is_needs_review() -> None:
    pages = ["A" * 500]
    raw = _make_pdf_bytes(pages)
    result = _extract(raw, max_extracted_chars=10)

    assert result.status == PDFContentStatus.NEEDS_REVIEW
    assert result.extracted_pages == 0
    assert result.text == ""
