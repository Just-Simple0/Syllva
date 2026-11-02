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
    _raises(conn, "UPDATE material_usage SET verified = 1, verified_gate_id = ? WHERE material_id = ?", (first, mid), "UNIQUE")
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
    _raises(conn, insert, ("N-2", body, ids["artifact"], good), "UNIQUE")  # a gate row is used once


def test_gate_log_is_append_only(v2_db: Database) -> None:
    conn = v2_db.connection
    seed_catalog(conn)
    gid = gate(conn, "VERIFY_MATERIAL", "material", "M-0001", "n1")
    _raises(conn, "UPDATE human_gate_log SET target_id = 'M-2' WHERE id = ?", (gid,), "GATE_LOG_APPEND_ONLY")
    _raises(conn, "DELETE FROM human_gate_log WHERE id = ?", (gid,), "GATE_LOG_APPEND_ONLY")
    _raises(conn, "INSERT INTO human_gate_log(action, target_kind, target_id, request_hash, nonce_id, confirmed_at, shell_version) "
            "VALUES ('VERIFY_MATERIAL', 'material', 'M-0001', ?, 'n1', 'x', 'v')", ("b" * 64,), "UNIQUE")
