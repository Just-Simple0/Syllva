"""Opening the v2 database (implementation spec §2.2 step 4a-4b, §2.4, §3.3).

``open_database`` is the only way Core gets a database: it takes the exclusive
OS lock first, then opens one writer connection with the mandated PRAGMAs,
runs ``quick_check``, backs up before pending migrations, and migrates.

After migrating, an SQLite authorizer denies schema changes on the writer
connection. This also denies VACUUM, ANALYZE and ``PRAGMA optimize``; a future
maintenance/GC step must lift the authorizer explicitly and reinstall it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from syllva.library.dblock import DatabaseLock
from syllva.library.paths import DataLayout
from syllva.state.v2 import migrate as _migrate
from syllva.state.v2.reader import StateReader
from syllva.state.v2.writer import StateWriter

WRITER_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 30000",
    "PRAGMA wal_autocheckpoint = 1000",
    "PRAGMA trusted_schema = OFF",
    # REPLACE's implicit DELETE then fires DELETE triggers (append-only guards).
    "PRAGMA recursive_triggers = ON",
)


# After migrations the writer connection may not change the schema (defence in depth
# for the trigger guards; same-process code could remove the authorizer, design §5.6).
_SCHEMA_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_ALTER_TABLE", "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_CREATE_VTABLE", "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_DROP_VTABLE", "SQLITE_ATTACH",
        "SQLITE_DETACH",
    )
    if hasattr(sqlite3, name)
)
_LOCKED_PRAGMAS = frozenset({"writable_schema", "recursive_triggers", "foreign_keys", "trusted_schema", "query_only", "journal_mode"})


def _schema_guard(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
    if action in _SCHEMA_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and arg1 is not None and arg1.lower() in _LOCKED_PRAGMAS and arg2 is not None:
        return sqlite3.SQLITE_DENY  # reading these is fine; changing them is not
    return sqlite3.SQLITE_OK


def lock_schema(conn: sqlite3.Connection) -> None:
    conn.set_authorizer(_schema_guard)


class DatabaseCorrupt(RuntimeError):
    code = "DB_CORRUPT"


def apply_pragmas(conn: sqlite3.Connection) -> None:
    # Defensive mode (Python >= 3.12 exposes it) blocks writable_schema and other
    # schema-corrupting statements on this connection.
    defensive = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
    if defensive is not None and hasattr(conn, "setconfig"):
        conn.setconfig(defensive, True)
    for pragma in WRITER_PRAGMAS:
        conn.execute(pragma)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "wal":
        raise RuntimeError(f"journal_mode must be WAL, got {mode!r}")


@dataclass
class Database:
    layout: DataLayout
    lock: DatabaseLock
    connection: sqlite3.Connection
    applied_migrations: list[int] = field(default_factory=list)
    backup_path: Path | None = None
    _writer: StateWriter | None = None

    def writer(self) -> StateWriter:
        """The single writer for this connection (its lock serialises all writes, spec §3.3)."""

        if self._writer is None:
            self._writer = StateWriter(self.connection)
        return self._writer

    def reader(self) -> StateReader:
        return StateReader.open(self.layout.db_path)

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()
        return destination

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self.lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def open_database(layout: DataLayout) -> Database:
    """Lock, open, check, back up (if migrating an existing DB) and migrate."""

    layout.ensure()
    lock = DatabaseLock(layout.lock_path).acquire()  # raises DatabaseLocked
    try:
        existed = layout.db_path.exists() and layout.db_path.stat().st_size > 0
        conn = sqlite3.connect(layout.db_path, isolation_level=None, check_same_thread=False, timeout=30.0)
        try:
            conn.row_factory = sqlite3.Row
            apply_pragmas(conn)
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise DatabaseCorrupt(f"quick_check failed: {result}")
            todo = _migrate.pending(conn)
            database = Database(layout, lock, conn)
            if existed and todo:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                database.backup_path = database.backup(layout.backups_dir / f"state-{stamp}-pre-migration.sqlite3")
            database.applied_migrations = _migrate.migrate(conn)
            lock_schema(conn)
            return database
        except BaseException:
            conn.close()
            raise
    except BaseException:
        lock.release()
        raise
