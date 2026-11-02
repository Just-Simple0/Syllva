"""Shared fixtures for the v2 contract tests (implementation spec §14)."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from syllva.library.paths import layout
from syllva.state.v2.database import Database, open_database

NOW = "2026-11-02T00:00:00+00:00"


@pytest.fixture
def v2_db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(layout(tmp_path / "data"))
    try:
        yield database
    finally:
        database.close()


def blob(conn: sqlite3.Connection, content: bytes = b"x") -> str:
    key = hashlib.sha256(content).hexdigest()
    conn.execute("INSERT OR IGNORE INTO blobs(sha256, size_bytes, created_at) VALUES (?, ?, ?)", (key, len(content), NOW))
    return key


def seed_catalog(conn: sqlite3.Connection) -> dict[str, int]:
    """One course, session, source file+version, material, activity, exam and AI artifact (raw SQL)."""

    key = blob(conn, b"lecture")
    ids: dict[str, int] = {}
    ids["course"] = conn.execute(
        "INSERT INTO courses(course_key, name, semester, created_at) VALUES ('LMS1', 'Algorithms 2', '2026-2', ?)", (NOW,)
    ).lastrowid or 0
    ids["session"] = conn.execute(
        "INSERT INTO sessions(session_id, course_id, number, created_at) VALUES ('LMS1-S01', ?, 1, ?)", (ids["course"], NOW)
    ).lastrowid or 0
    ids["source_file"] = conn.execute(
        "INSERT INTO source_files(source_id, provider, display_name, origin, created_at) "
        "VALUES ('SRC-1', 'local', 'ch04.pdf', 'USER_DROP', ?)",
        (NOW,),
    ).lastrowid or 0
    ids["source_version"] = conn.execute(
        "INSERT INTO source_versions(source_file_id, source_version, source_hash, blob_sha256, created_at) "
        "VALUES (?, 1, ?, ?, ?)",
        (ids["source_file"], "sha256:" + key, key, NOW),
    ).lastrowid or 0
    ids["material"] = conn.execute(
        "INSERT INTO materials(material_id, course_id, session_id, source_file_id, kind, role, created_at) "
        "VALUES ('M-0001', ?, ?, ?, 'LECTURE_SLIDES', 'PRIMARY', ?)",
        (ids["course"], ids["session"], ids["source_file"], NOW),
    ).lastrowid or 0
    ids["activity"] = conn.execute(
        "INSERT INTO activities(activity_id, course_id, title, created_at) VALUES ('A-0001', ?, 'HW2', ?)",
        (ids["course"], NOW),
    ).lastrowid or 0
    ids["exam"] = conn.execute(
        "INSERT INTO exams(exam_id, course_id, created_at) VALUES ('E-0001', ?, ?)", (ids["course"], NOW)
    ).lastrowid or 0
    body = blob(conn, b"summary body")
    ids["artifact"] = conn.execute(
        "INSERT INTO ai_artifacts(artifact_id, kind, based_on_source_version_id, based_on_source_hash, processor_version, "
        "agent_adapter, prompt_template_version, body_blob_sha256, citation_status, created_at) "
        "VALUES ('AI-0001', 'summary', ?, ?, 'p1', 'claude', 'summary.v4', ?, 'CITED', ?)",
        (ids["source_version"], "sha256:" + key, body, NOW),
    ).lastrowid or 0
    return ids


def gate(conn: sqlite3.Connection, action: str, target_kind: str, target_id: str, nonce: str) -> int:
    return conn.execute(
        "INSERT INTO human_gate_log(action, target_kind, target_id, request_hash, nonce_id, confirmed_at, shell_version) "
        "VALUES (?, ?, ?, ?, ?, ?, '0.0.0')",
        (action, target_kind, target_id, "a" * 64, nonce, NOW),
    ).lastrowid or 0


# Copied from tests/unit/test_normalization_pdf.py: a real minimal text PDF without extra deps.
def make_pdf_bytes(page_texts: list[str | None]) -> bytes:
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
