"""Forward-only schema migrations with checksums (implementation spec §3.4).

* migrations are ``migrations/NNN_name.sql`` files shipped with the package;
* each file is applied in ONE transaction (statements are split with
  ``sqlite3.complete_statement`` so trigger bodies stay intact — no
  ``executescript``, which would commit implicitly);
* ``schema_migrations(version, checksum, applied_at)`` records each file; an
  applied file whose checksum changed, or a recorded version with no file
  (downgrade), refuses start-up.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources

_NAME = re.compile(r"(\d{3})_[a-z0-9_]+\.sql")


class MigrationError(RuntimeError):
    code = "MIGRATION_FAILED"
    exit_code = 4


class MigrationChecksumMismatch(MigrationError):
    code = "MIGRATION_CHECKSUM_MISMATCH"


class MigrationDowngrade(MigrationError):
    code = "MIGRATION_DOWNGRADE"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def bundled_migrations() -> list[Migration]:
    folder = resources.files("syllva.state.v2").joinpath("migrations")
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _NAME.fullmatch(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 001: {versions}")
    return found


def split_statements(sql: str) -> Iterator[str]:
    buffer: list[str] = []
    for line in sql.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement and not _only_comments(statement):
                yield statement
            buffer = []
    rest = "".join(buffer).strip()
    if rest and not _only_comments(rest):
        raise MigrationError("migration ends with an incomplete statement")


def _only_comments(text: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in text.splitlines())


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )


def applied_versions(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_table(conn)
    return {int(v): str(c) for v, c in conn.execute("SELECT version, checksum FROM schema_migrations")}


def pending(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[Migration]:
    migrations = bundled_migrations() if migrations is None else migrations
    applied = applied_versions(conn)
    known = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        if version not in known:
            raise MigrationDowngrade(f"database has migration {version:03d} that this build does not know")
        if known[version].checksum != checksum:
            raise MigrationChecksumMismatch(f"migration {known[version].name} changed after it was applied")
    return [m for m in migrations if m.version not in applied]


def migrate(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[int]:
    """Apply pending migrations; returns the versions applied. Connection must be in autocommit mode."""

    if conn.in_transaction:
        raise MigrationError("migrate() needs a connection without an open transaction")
    todo = pending(conn, migrations)
    done: list[int] = []
    for migration in todo:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.checksum, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute("COMMIT")
        except BaseException as exc:
            conn.execute("ROLLBACK")
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"migration {migration.name} failed: {exc}") from exc
        done.append(migration.version)
    return done
