"""v2 migration runner and database opening (implementation spec §3.3-§3.4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from syllva.library.paths import layout
from syllva.state.v2 import migrate
from syllva.state.v2.database import DatabaseCorrupt, open_database

pytestmark = pytest.mark.unit


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:", isolation_level=None)


def test_bundled_migrations_are_contiguous_and_split_trigger_bodies_intact() -> None:
    migrations = migrate.bundled_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    statements = list(migrate.split_statements(migrations[0].sql))
    triggers = [s for s in statements if s.startswith("CREATE TRIGGER")]
    assert triggers and all(s.rstrip().endswith("END;") for s in triggers)


def test_migrate_applies_once_and_records_checksums() -> None:
    conn = _conn()
    assert migrate.migrate(conn) == [1]
    assert migrate.migrate(conn) == []
    (version, checksum), = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    assert version == 1 and checksum == migrate.bundled_migrations()[0].checksum


def test_changed_applied_migration_refuses_startup() -> None:
    conn = _conn()
    migrate.migrate(conn)
    conn.execute("UPDATE schema_migrations SET checksum = 'x'")
    with pytest.raises(migrate.MigrationChecksumMismatch):
        migrate.migrate(conn)


def test_unknown_applied_version_is_a_downgrade() -> None:
    conn = _conn()
    migrate.migrate(conn)
    conn.execute("INSERT INTO schema_migrations VALUES (99, 'c', 'now')")
    with pytest.raises(migrate.MigrationDowngrade):
        migrate.migrate(conn)


def test_failed_migration_rolls_back_everything() -> None:
    conn = _conn()
    broken = migrate.Migration(1, "001_broken.sql", "CREATE TABLE a(x);\nCREATE TABLE a(x);\n")
    with pytest.raises(migrate.MigrationError):
        migrate.migrate(conn, [broken])
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchone() is None
    assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0


def test_incomplete_statement_is_rejected() -> None:
    with pytest.raises(migrate.MigrationError):
        list(migrate.split_statements("CREATE TABLE a(x"))


def test_open_database_sets_pragmas(tmp_path: Path) -> None:
    with open_database(layout(tmp_path / "d")) as db:
        conn = db.connection
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert db.applied_migrations == [1]


def test_backup_is_taken_before_migrating_an_existing_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = layout(tmp_path / "d")
    with open_database(data) as db:
        assert db.backup_path is None  # fresh database: nothing to back up
    extra = migrate.Migration(2, "002_extra.sql", "CREATE TABLE extra(x);\n")
    original = migrate.bundled_migrations
    monkeypatch.setattr(migrate, "bundled_migrations", lambda: [*original(), extra])
    with open_database(data) as db:
        assert db.applied_migrations == [2]
        assert db.backup_path is not None and db.backup_path.is_file()
        backup = sqlite3.connect(db.backup_path)
        try:
            assert [r[0] for r in backup.execute("SELECT version FROM schema_migrations")] == [1]
        finally:
            backup.close()


def test_corrupt_database_refuses_startup(tmp_path: Path) -> None:
    data = layout(tmp_path / "d").ensure()
    data.db_path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises((DatabaseCorrupt, sqlite3.DatabaseError)):
        open_database(data)
    # the lock is released after the failure
    with pytest.raises((DatabaseCorrupt, sqlite3.DatabaseError)):
        open_database(data)
