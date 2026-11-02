"""Read-only state access (implementation spec §3.5, design §2 I1, C-02).

The connection is opened with the ``mode=ro`` URI and ``PRAGMA query_only``,
so any write statement fails inside SQLite itself. It lives in the same
process as the writer (only Core opens the database): this is least privilege
for the retrieval code, not OS isolation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class StateReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, db_path: Path) -> StateReader:
        uri = Path(db_path).absolute().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        return cls(conn)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        row = self._connection.execute(sql, tuple(params)).fetchone()
        return row if row is None or isinstance(row, sqlite3.Row) else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._connection.execute(sql, tuple(params)).fetchall())

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
