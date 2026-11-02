"""DB-level last line of defence for human-only fields (spec §3.7; part of C-17, R1b-2).

These tests write with RAW SQL on purpose: they prove that even code that
bypasses ``StateWriter`` cannot promote a human-only field without a matching,
unused ``human_gate_log`` row. The gate commit protocol itself (nonce, MAC,
re-verification) is C-17's R2 part.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import gate, seed_catalog

from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract


def _raises(conn: sqlite3.Connection, sql: str, params: tuple[object, ...], message: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=message):
        conn.execute(sql, params)


def test_material_verified_requires_matching_unused_gate(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    mid = ids["material"]
    _raises(conn, "INSERT INTO material_usage(material_id, verified) VALUES (?, 1)", (mid,), "CHECK|GATE_MISMATCH")
    wrong_action = gate(conn, "CONFIRM_EXAM_SCOPE", "material", "M-0001", "n1")
    wrong_target = gate(conn, "VERIFY_MATERIAL", "material", "M-9999", "n2")
    for bad in (wrong_action, wrong_target):
        _raises(conn, "INSERT INTO material_usage(material_id, verified, verified_gate_id) VALUES (?, 1, ?)", (mid, bad), "GATE_MISMATCH")
    good = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "n3")
    conn.execute("INSERT INTO material_usage(material_id, verified, verified_gate_id) VALUES (?, 1, ?)", (mid, good))
    assert conn.execute("SELECT used_by FROM human_gate_uses WHERE gate_id = ?", (good,)).fetchone()[0] == f"material_usage:{mid}"


def test_material_reverify_cannot_reuse_a_gate(v2_db: Database) -> None:
    conn = v2_db.connection
    mid = seed_catalog(conn)["material"]
    conn.execute("INSERT INTO material_usage(material_id) VALUES (?)", (mid,))
    first = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "n1")
    conn.execute("UPDATE material_usage SET verified = 1, verified_gate_id = ? WHERE material_id = ?", (first, mid))
    conn.execute("UPDATE material_usage SET verified = 0, verified_gate_id = NULL WHERE material_id = ?", (mid,))
    _raises(conn, "UPDATE material_usage SET verified = 1, verified_gate_id = ? WHERE material_id = ?", (first, mid), "GATE_REUSED")
    _raises(conn, "UPDATE material_usage SET verified = 1 WHERE material_id = ?", (mid,), "GATE_REQUIRED")
    second = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "n2")
    conn.execute("UPDATE material_usage SET verified = 1, verified_gate_id = ? WHERE material_id = ?", (second, mid))


def test_exam_scope_confirmed_requires_gate(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    _raises(conn, "UPDATE exams SET scope_confirmed = 1 WHERE id = ?", (ids["exam"],), "GATE_REQUIRED")
    bad = gate(conn, "VERIFY_MATERIAL", "exam", "E-0001", "n1")
    _raises(conn, "UPDATE exams SET scope_confirmed = 1, scope_gate_id = ? WHERE id = ?", (bad, ids["exam"]), "GATE_MISMATCH")
    good = gate(conn, "CONFIRM_EXAM_SCOPE", "exam", "E-0001", "n2")
    conn.execute("UPDATE exams SET scope_confirmed = 1, scope_gate_id = ? WHERE id = ?", (good, ids["exam"]))
    # INSERT path is guarded too
    _raises(
        conn,
        "INSERT INTO exams(exam_id, course_id, scope_confirmed, scope_gate_id, created_at) VALUES ('E-0002', ?, 1, ?, 'x')",
        (ids["course"], good),
        "GATE_MISMATCH",
    )


def test_every_submission_status_change_needs_a_new_gate(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    aid = ids["activity"]
    _raises(conn, "UPDATE activities SET submission_status = 'SUBMITTED' WHERE id = ?", (aid,), "GATE_REQUIRED|CHECK")
    g1 = gate(conn, "SET_SUBMISSION_STATUS", "activity", "A-0001", "n1")
    conn.execute("UPDATE activities SET submission_status = 'SUBMITTED', submission_gate_id = ? WHERE id = ?", (g1, aid))
    _raises(conn, "UPDATE activities SET submission_status = 'NOT_SUBMITTED' WHERE id = ?", (aid,), "GATE_REUSED")
    _raises(conn, "UPDATE activities SET submission_status = 'NOT_SUBMITTED', submission_gate_id = NULL WHERE id = ?", (aid,), "GATE_REQUIRED")
    g2 = gate(conn, "SET_SUBMISSION_STATUS", "activity", "A-0001", "n2")
    conn.execute("UPDATE activities SET submission_status = 'NOT_SUBMITTED', submission_gate_id = ? WHERE id = ?", (g2, aid))
    _raises(
        conn,
        "INSERT INTO activities(activity_id, course_id, title, submission_status, created_at) VALUES ('A-2', ?, 't', 'SUBMITTED', 'x')",
        (ids["course"],),
        "CHECK|GATE_MISMATCH",
    )


def test_adopting_ai_output_as_user_note_requires_gate_and_is_immutable(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    body = conn.execute("SELECT body_blob_sha256 FROM ai_artifacts WHERE id = ?", (ids["artifact"],)).fetchone()[0]
    insert = (
        "INSERT INTO user_notes(note_id, body_blob_sha256, adopted_from_artifact_id, adopt_gate_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'x', 'x')"
    )
    _raises(conn, insert, ("N-1", body, ids["artifact"], None), "CHECK|GATE_MISMATCH")
    bad = gate(conn, "ADOPT_AI_ARTIFACT", "ai_artifact", "AI-9999", "n1")
    _raises(conn, insert, ("N-1", body, ids["artifact"], bad), "GATE_MISMATCH")
    good = gate(conn, "ADOPT_AI_ARTIFACT", "ai_artifact", "AI-0001", "n2")
    conn.execute(insert, ("N-1", body, ids["artifact"], good))
    _raises(conn, "UPDATE user_notes SET adopted_from_artifact_id = NULL, adopt_gate_id = NULL WHERE note_id = 'N-1'", (), "ADOPTION_IMMUTABLE")
    _raises(conn, insert, ("N-2", body, ids["artifact"], good), "GATE_REUSED")  # a gate row is used once


def test_gate_log_is_append_only(v2_db: Database) -> None:
    conn = v2_db.connection
    seed_catalog(conn)
    gid = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "n1")
    _raises(conn, "UPDATE human_gate_log SET target_id = 'M-2' WHERE id = ?", (gid,), "GATE_LOG_APPEND_ONLY")
    _raises(conn, "DELETE FROM human_gate_log WHERE id = ?", (gid,), "GATE_LOG_APPEND_ONLY")
    _raises(conn, "INSERT INTO human_gate_log(action, target_kind, target_id, request_hash, nonce_id, confirmed_at, shell_version) "
            "VALUES ('VERIFY_MATERIAL', 'material', 'M-0001', ?, 'n1', 'x', 'v')", ("b" * 64,), "UNIQUE")


# --- conflict clauses must not bypass single use (review of PR #39) ----------

def _used_gate(conn: sqlite3.Connection, ids: dict[str, int]) -> dict[str, int]:
    """Consume one valid gate per target so later statements try to REUSE it."""

    used: dict[str, int] = {}
    used["material"] = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "u-m")
    conn.execute("INSERT INTO material_usage(material_id, verified, verified_gate_id) VALUES (?, 1, ?)", (ids["material"], used["material"]))
    conn.execute("UPDATE material_usage SET verified = 0, verified_gate_id = NULL WHERE material_id = ?", (ids["material"],))
    used["exam"] = gate(conn, "CONFIRM_EXAM_SCOPE", "exam", "E-0001", "u-e")
    conn.execute("UPDATE exams SET scope_confirmed = 1, scope_gate_id = ? WHERE id = ?", (used["exam"], ids["exam"]))
    conn.execute("UPDATE exams SET scope_confirmed = 0, scope_gate_id = NULL WHERE id = ?", (ids["exam"],))
    used["activity"] = gate(conn, "SET_SUBMISSION_STATUS", "activity", "A-0001", "u-a")
    conn.execute("UPDATE activities SET submission_status = 'SUBMITTED', submission_gate_id = ? WHERE id = ?", (used["activity"], ids["activity"]))
    used["adopt"] = gate(conn, "ADOPT_AI_ARTIFACT", "ai_artifact", "AI-0001", "u-n")
    body = conn.execute("SELECT body_blob_sha256 FROM ai_artifacts").fetchone()[0]
    conn.execute(
        "INSERT INTO user_notes(note_id, body_blob_sha256, adopted_from_artifact_id, adopt_gate_id, created_at, updated_at) "
        "VALUES ('N-1', ?, ?, ?, 'x', 'x')",
        (body, ids["artifact"], used["adopt"]),
    )
    return used


CONFLICT_ATTEMPTS = [
    ("material INSERT OR REPLACE", "INSERT OR REPLACE INTO material_usage(material_id, verified, verified_gate_id) VALUES (:material, 1, :g_material)"),
    ("material INSERT OR IGNORE", "INSERT OR IGNORE INTO material_usage(material_id, verified, verified_gate_id) VALUES (:material, 1, :g_material)"),
    ("material REPLACE INTO", "REPLACE INTO material_usage(material_id, verified, verified_gate_id) VALUES (:material, 1, :g_material)"),
    ("material UPDATE OR REPLACE", "UPDATE OR REPLACE material_usage SET verified = 1, verified_gate_id = :g_material WHERE material_id = :material"),
    ("material UPDATE OR IGNORE", "UPDATE OR IGNORE material_usage SET verified = 1, verified_gate_id = :g_material WHERE material_id = :material"),
    ("exam UPDATE OR IGNORE", "UPDATE OR IGNORE exams SET scope_confirmed = 1, scope_gate_id = :g_exam WHERE id = :exam"),
    ("exam UPDATE OR REPLACE", "UPDATE OR REPLACE exams SET scope_confirmed = 1, scope_gate_id = :g_exam WHERE id = :exam"),
    ("exam REPLACE INTO", "REPLACE INTO exams(id, exam_id, course_id, scope_confirmed, scope_gate_id, created_at) VALUES (:exam, 'E-0001', :course, 1, :g_exam, 'x')"),
    ("activity REPLACE INTO", "REPLACE INTO activities(id, activity_id, course_id, title, submission_status, submission_gate_id, created_at) VALUES (:activity, 'A-0001', :course, 't', 'EXCUSED', :g_activity, 'x')"),
    ("activity UPDATE OR IGNORE", "UPDATE OR IGNORE activities SET submission_status = 'EXCUSED', submission_gate_id = :g_activity WHERE id = :activity"),
    ("note INSERT OR IGNORE", "INSERT OR IGNORE INTO user_notes(note_id, body_blob_sha256, adopted_from_artifact_id, adopt_gate_id, created_at, updated_at) SELECT 'N-2', body_blob_sha256, :artifact, :g_adopt, 'x', 'x' FROM ai_artifacts"),
    ("note INSERT OR REPLACE", "INSERT OR REPLACE INTO user_notes(note_id, body_blob_sha256, adopted_from_artifact_id, adopt_gate_id, created_at, updated_at) SELECT 'N-3', body_blob_sha256, :artifact, :g_adopt, 'x', 'x' FROM ai_artifacts"),
]


@pytest.mark.parametrize(("label", "sql"), CONFLICT_ATTEMPTS, ids=[label for label, _ in CONFLICT_ATTEMPTS])
def test_conflict_clauses_cannot_reuse_a_gate(v2_db: Database, label: str, sql: str) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    used = _used_gate(conn, ids)
    params = {**ids, **{f"g_{k}": v for k, v in used.items()}}
    with pytest.raises(sqlite3.IntegrityError, match="GATE_REUSED"):
        conn.execute(sql, params)
    # nothing changed: no promotion happened and each gate is still used exactly once
    assert conn.execute("SELECT verified FROM material_usage").fetchone()[0] == 0
    assert conn.execute("SELECT scope_confirmed FROM exams").fetchone()[0] == 0
    assert conn.execute("SELECT submission_status FROM activities").fetchone()[0] == "SUBMITTED"
    assert conn.execute("SELECT count(*) FROM user_notes").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM human_gate_uses").fetchone()[0] == 4


IDENTITY_UPDATES = [
    "UPDATE material_usage SET material_id = :other_material WHERE material_id = :material",
    "UPDATE materials SET material_id = 'M-0002' WHERE id = :material",
    "UPDATE exams SET exam_id = 'E-0002' WHERE id = :exam",
    "UPDATE activities SET activity_id = 'A-0002' WHERE id = :activity",
    "UPDATE user_notes SET note_id = 'N-9' WHERE note_id = 'N-1'",
]


@pytest.mark.parametrize("sql", IDENTITY_UPDATES)
def test_gate_target_identity_cannot_move(v2_db: Database, sql: str) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    _used_gate(conn, ids)
    other = conn.execute(
        "INSERT INTO materials(material_id, course_id, source_file_id, kind, role, created_at) "
        "VALUES ('M-0009', ?, ?, 'LECTURE_SLIDES', 'PRIMARY', 'x')",
        (ids["course"], ids["source_file"]),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="IDENTITY_IMMUTABLE"):
        conn.execute(sql, {**ids, "other_material": other})


def test_user_edit_history_is_append_only(v2_db: Database) -> None:
    conn = v2_db.connection
    conn.execute("INSERT INTO user_edits(record_kind, record_id, edited_at) VALUES ('user_notes', 1, 'x')")
    _raises(conn, "UPDATE user_edits SET record_id = 2", (), "HISTORY_APPEND_ONLY")
    _raises(conn, "DELETE FROM user_edits", (), "HISTORY_APPEND_ONLY")


def test_writer_connection_cannot_change_the_schema_after_migrations(v2_db: Database) -> None:
    """Defence in depth for the trigger guards (spec §3.3): DDL and guard PRAGMAs are denied."""

    conn = v2_db.connection
    for sql in (
        "DROP TRIGGER material_usage_gate_insert",
        "CREATE TRIGGER evil BEFORE INSERT ON courses BEGIN SELECT 1; END",
        "DROP TABLE human_gate_uses",
        "ALTER TABLE material_usage ADD COLUMN x",
        "CREATE TEMP TRIGGER evil AFTER INSERT ON courses BEGIN SELECT 1; END",
        "PRAGMA writable_schema = ON",
        "PRAGMA recursive_triggers = OFF",
        "PRAGMA foreign_keys = OFF",
        "ATTACH DATABASE ':memory:' AS other",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            conn.execute(sql)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # reading is allowed
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_materials_start_pending_and_keep_their_source(v2_db: Database) -> None:
    conn = v2_db.connection
    ids = seed_catalog(conn)
    _raises(
        conn,
        "REPLACE INTO materials(id, material_id, course_id, source_file_id, kind, role, processing_status, created_at) "
        "VALUES (?, 'M-0001', ?, ?, 'K', 'R', 'READY', 'x')",
        (ids["material"], ids["course"], ids["source_file"]),
        "NEW_MATERIAL_NOT_PENDING",
    )
    other = conn.execute(
        "INSERT INTO source_files(source_id, provider, display_name, origin, created_at) VALUES ('SRC-2', 'local', 'b.pdf', 'USER_DROP', 'x')"
    ).lastrowid
    _raises(conn, "UPDATE materials SET source_file_id = ? WHERE id = ?", (other, ids["material"]), "IDENTITY_IMMUTABLE")
