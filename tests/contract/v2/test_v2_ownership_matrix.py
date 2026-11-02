"""C-03: owner/actor write matrix (implementation spec §5.1, design §2 I2).

Covers the full matrix through ``StateWriter`` (this file) plus the static rule
"no write SQL outside state/" (test_v2_static_invariants.py).
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import NOW, blob, seed_catalog

from syllva.domain.errors import PolicyViolation
from syllva.state.v2.database import Database
from syllva.state.v2.ownership import (
    ALLOWED,
    HUMAN_ONLY_COLUMNS,
    TABLE_OWNERS,
    UNWRITABLE_TABLES,
    Actor,
    Owner,
)

pytestmark = pytest.mark.contract

MATRIX = [(table, owner, actor) for table, owner in sorted(TABLE_OWNERS.items()) for actor in Actor]


@pytest.mark.parametrize(("table", "owner", "actor"), MATRIX, ids=[f"{t}-{a}" for t, _, a in MATRIX])
def test_insert_permission_matrix(v2_db: Database, table: str, owner: Owner, actor: Actor) -> None:
    writer = v2_db.writer()
    columns = {row[0] for row in v2_db.connection.execute("SELECT name FROM pragma_table_info(?)", (table,))}
    probe = {next(iter(sorted(columns - set(HUMAN_ONLY_COLUMNS.get(table, ()))))): None}
    if actor in ALLOWED[owner]:
        # authorized: the call reaches SQLite (which may reject the incomplete row, not the policy)
        try:
            writer.insert(owner, actor, table, probe)
        except PolicyViolation as exc:  # pragma: no cover - failure path
            pytest.fail(f"{actor} should be allowed to write {table}: {exc}")
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            return  # constraint errors are expected for an incomplete probe row
    else:
        with pytest.raises(PolicyViolation):
            writer.insert(owner, actor, table, probe)


def test_agent_output_can_only_write_ai_tables() -> None:
    writable = {t for t, o in TABLE_OWNERS.items() if Actor.CORE_AGENT_OUTPUT in ALLOWED[o]}
    assert writable == {"ai_artifacts", "artifact_bases", "artifact_citations"}


def test_declared_owner_must_match_table(v2_db: Database) -> None:
    with pytest.raises(PolicyViolation, match="declared owner"):
        v2_db.writer().insert(Owner.SOURCE, Actor.CORE_AGENT_OUTPUT, "ai_artifacts", {"artifact_id": "x"})
    with pytest.raises(PolicyViolation, match="declared owner"):
        v2_db.writer().insert(Owner.USER, Actor.UI_USER, "ai_artifacts", {"artifact_id": "x"})


@pytest.mark.parametrize("table", sorted(UNWRITABLE_TABLES))
def test_gate_and_migration_tables_are_not_writable(v2_db: Database, table: str) -> None:
    for owner in Owner:
        for actor in Actor:
            with pytest.raises(PolicyViolation):
                v2_db.writer().insert(owner, actor, table, {"id": 1})


@pytest.mark.parametrize(("table", "columns"), sorted(HUMAN_ONLY_COLUMNS.items()))
def test_human_only_columns_rejected_for_every_actor(v2_db: Database, table: str, columns: frozenset[str]) -> None:
    owner = TABLE_OWNERS[table]
    pk = next(r[1] for r in v2_db.connection.execute("SELECT * FROM pragma_table_info(?)", (table,)) if r[5] == 1)
    for actor in ALLOWED[owner]:
        for column in columns:
            with pytest.raises(PolicyViolation, match="human-only"):
                v2_db.writer().insert(owner, actor, table, {column: 1})
            with pytest.raises(PolicyViolation, match="human-only"):
                v2_db.writer().update(owner, actor, table, {pk: 1}, {column: 1})


def test_ai_output_is_never_relabelled_or_rewritten(v2_db: Database) -> None:
    ids = seed_catalog(v2_db.connection)
    writer = v2_db.writer()
    with pytest.raises(PolicyViolation):
        writer.update(Owner.AI, Actor.CORE_AGENT_OUTPUT, "ai_artifacts", {"id": ids["artifact"]}, {"citation_status": "UNCITED"})
    with pytest.raises(PolicyViolation, match="owner labels"):
        writer.update(Owner.AI, Actor.SYSTEM_MIGRATION, "ai_artifacts", {"id": ids["artifact"]}, {"owner": "SOURCE"})
    # and the schema refuses it even through raw SQL
    with pytest.raises(Exception, match="AI_ARTIFACT_IMMUTABLE"):
        v2_db.connection.execute("UPDATE ai_artifacts SET kind = 'answer' WHERE id = ?", (ids["artifact"],))
    with pytest.raises(Exception, match="CHECK"):
        v2_db.connection.execute(
            "INSERT INTO ai_artifacts(artifact_id, owner, kind, based_on_source_version_id, based_on_source_hash, "
            "processor_version, agent_adapter, prompt_template_version, body_blob_sha256, citation_status, created_at) "
            "SELECT 'AI-2', 'SOURCE', kind, based_on_source_version_id, based_on_source_hash, processor_version, "
            "agent_adapter, prompt_template_version, body_blob_sha256, citation_status, created_at FROM ai_artifacts"
        )


def test_source_rows_are_insert_only_except_bookkeeping(v2_db: Database) -> None:
    ids = seed_catalog(v2_db.connection)
    writer = v2_db.writer()
    writer.update(Owner.SOURCE, Actor.INGESTER, "source_files", {"id": ids["source_file"]}, {"deleted_at": NOW})
    with pytest.raises(PolicyViolation, match="insert-only"):
        writer.update(Owner.SOURCE, Actor.INGESTER, "source_versions", {"id": ids["source_version"]}, {"source_hash": "sha256:" + "0" * 64})
    with pytest.raises(PolicyViolation):
        writer.update(Owner.SOURCE, Actor.UI_USER, "source_files", {"id": ids["source_file"]}, {"deleted_at": NOW})


def test_user_content_is_edited_only_by_the_user_and_keeps_history(v2_db: Database) -> None:
    conn = v2_db.connection
    first, second = blob(conn, b"v1"), blob(conn, b"v2")
    writer = v2_db.writer()
    writer.insert(Owner.USER, Actor.UI_USER, "user_notes", {"note_id": "N-1", "body_blob_sha256": first, "created_at": NOW, "updated_at": NOW})
    for actor in (Actor.INGESTER, Actor.CORE_AGENT_OUTPUT):
        with pytest.raises(PolicyViolation):
            writer.update(Owner.USER, actor, "user_notes", {"note_id": "N-1"}, {"body_blob_sha256": second})
    writer.update(Owner.USER, Actor.UI_USER, "user_notes", {"note_id": "N-1"}, {"body_blob_sha256": second})
    history = conn.execute("SELECT record_kind, previous_blob_sha256 FROM user_edits").fetchall()
    assert [tuple(row) for row in history] == [("user_notes", first)]


def test_unknown_tables_and_columns_are_rejected_before_sql(v2_db: Database) -> None:
    writer = v2_db.writer()
    with pytest.raises(PolicyViolation, match="unknown table"):
        writer.insert(Owner.CATALOG, Actor.INGESTER, "courses; DROP TABLE blobs", {"name": "x"})
    with pytest.raises(PolicyViolation, match="unknown columns"):
        writer.insert(Owner.CATALOG, Actor.INGESTER, "courses", {"name) VALUES ('x'); --": "x"})
