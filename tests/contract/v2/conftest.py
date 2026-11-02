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
