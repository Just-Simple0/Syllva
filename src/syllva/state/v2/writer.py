"""The single write path for v2 state (implementation spec §5, design §2 I2-I4, C-03).

Every INSERT/UPDATE outside migrations goes through ``StateWriter``:

* the caller declares ``owner`` and ``actor``; the declared owner must match the
  table's owner and the actor must be allowed for it (``ownership.ALLOWED``);
* SOURCE and AI rows are insert-only (AI output is never relabelled or
  rewritten; new source content is a new ``source_versions`` row);
* human-only columns and the gate tables are rejected here — only the gate
  commit path (R2, spec §6.6) may set them — and the schema triggers reject
  them again if this layer is bypassed;
* USER rows may only be edited by ``UI_USER`` and every edit records history.

Identifiers are checked against the live schema before SQL is built, so table
or column names can never come from data.

Thread safety (spec §3.3): one ``StateWriter`` per connection
(``Database.writer()`` returns the same instance). An ``RLock`` serialises every
write and transaction; nesting is tracked per owning thread, so a thread never
"joins" another thread's transaction.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from syllva.domain.errors import PolicyViolation
from syllva.state.v2.ownership import (
    ALLOWED,
    HUMAN_ONLY_COLUMNS,
    IMMUTABLE_COLUMNS,
    INSERT_ONLY_OWNERS,
    INTAKE_INITIAL_STATES,
    INTAKE_TRANSITIONS,
    INTAKE_USER_TRANSITIONS,
    SOURCE_MUTABLE_COLUMNS,
    UNWRITABLE_TABLES,
    Actor,
    Owner,
    can_transition,
    owner_of,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StateWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if connection.isolation_level is not None:
            raise ValueError("StateWriter needs an autocommit connection (isolation_level=None)")
        self._connection = connection
        self._columns: dict[str, frozenset[str]] = {}
        self._lock = threading.RLock()
        self._depth = 0  # transaction nesting; only touched while holding _lock

    # -- transactions ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[StateWriter]:
        with self._lock:
            if self._depth:
                self._depth += 1  # nested use in the SAME thread joins the outer transaction
                try:
                    yield self
                finally:
                    self._depth -= 1
                return
            if self._connection.in_transaction:
                raise RuntimeError("connection has a transaction not opened by StateWriter")
            self._connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")
            finally:
                self._depth = 0

    @property
    def in_transaction(self) -> bool:
        """True while a StateWriter transaction is open on this connection."""

        return self._depth > 0 or self._connection.in_transaction

    # -- reads inside the write transaction ------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Read through the writer connection (consistent with an open transaction). SELECT only."""

        # Only a plain SELECT: a CTE may wrap DML (``WITH x AS (...) DELETE ...``), which the
        # schema authorizer does not stop. sqlite3 executes a single statement per call.
        if not sql.lstrip().upper().startswith("SELECT") or "RETURNING" in sql.upper():
            raise PolicyViolation("StateWriter.query accepts a single plain SELECT statement only")
        with self._lock:
            return list(self._connection.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- generic writes -------------------------------------------------------

    def insert(self, owner: Owner, actor: Actor, table: str, values: Mapping[str, Any]) -> int:
        self._authorize(owner, actor, table, set(values), update=False)
        if table == "materials" and values.get("processing_status", "PENDING") != "PENDING":
            raise PolicyViolation("new materials start as PENDING; use transition_material_status()")
        if table == "intake_candidates" and "state" in values and values["state"] not in INTAKE_INITIAL_STATES:
            raise PolicyViolation("a new intake candidate starts as RECEIVED, DUPLICATE or UNSUPPORTED")
        columns = list(values)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "  # identifiers validated in _authorize
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        with self.transaction():
            cursor = self._connection.execute(sql, [values[c] for c in columns])
            return int(cursor.lastrowid or 0)

    def update(
        self,
        owner: Owner,
        actor: Actor,
        table: str,
        key: Mapping[str, Any],
        values: Mapping[str, Any],
    ) -> int:
        if len(key) != 1:
            raise PolicyViolation("update needs exactly one key column")
        self._authorize(owner, actor, table, set(values) | set(key), update=True, changed=set(values))
        (key_column,) = key
        if key_column not in self._unique_columns(table):
            raise PolicyViolation(
                "update key must be a primary key or UNIQUE column",
                details={"table": table, "key": key_column},
            )
        if table == "materials" and "processing_status" in values:
            raise PolicyViolation("use transition_material_status() for processing_status")
        if table == "intake_candidates" and "state" in values:
            raise PolicyViolation("use transition_intake_state() for intake state")
        with self.transaction():
            if owner is Owner.USER:
                self._record_user_edits(table, key)
            assignments = ", ".join(f"{c} = ?" for c in values)
            conditions = " AND ".join(f"{c} = ?" for c in key)
            cursor = self._connection.execute(
                f"UPDATE {table} SET {assignments} WHERE {conditions}",  # identifiers validated
                [*values.values(), *key.values()],
            )
            return int(cursor.rowcount)

    def transition_material_status(self, actor: Actor, material_id: str, new_status: str) -> str:
        """Move a material through the processing state machine (design §2 I3)."""

        self._authorize(Owner.CATALOG, actor, "materials", {"processing_status"}, update=True, changed={"processing_status"})
        if actor is not Actor.INGESTER and actor is not Actor.SYSTEM_MIGRATION:
            raise PolicyViolation("only processing may change processing_status")
        with self.transaction():
            row = self._connection.execute(
                "SELECT processing_status FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if row is None:
                raise PolicyViolation("unknown material", details={"material_id": material_id})
            old = str(row[0])
            if not can_transition(old, new_status):
                raise PolicyViolation(
                    "processing status transition not allowed",
                    details={"from": old, "to": new_status},
                )
            self._connection.execute(
                "UPDATE materials SET processing_status = ? WHERE material_id = ?", (new_status, material_id)
            )
            return old

    def transition_intake_state(
        self,
        actor: Actor,
        candidate_id: str,
        new_state: str,
        *,
        values: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> str:
        """Move an intake candidate through the §11.2 state machine (also enforced by the schema).

        ``values`` are other columns written in the same statement (reason, material, suggestion).
        Only the user confirms or dismisses; every other transition is processing (INGESTER).
        """

        extra = dict(values or {})
        if "state" in extra:
            raise PolicyViolation("state is the transition target, not a value")
        self._authorize(
            Owner.CATALOG, actor, "intake_candidates", {"state", "updated_at", *extra}, update=True,
            changed={"state", "updated_at", *extra},
        )
        with self.transaction():
            row = self._connection.execute(
                "SELECT state FROM intake_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise PolicyViolation("unknown intake candidate", details={"candidate_id": candidate_id})
            old = str(row[0])
            if new_state not in INTAKE_TRANSITIONS.get(old, frozenset()):
                raise PolicyViolation("intake transition not allowed", details={"from": old, "to": new_state})
            by_user = (old, new_state) in INTAKE_USER_TRANSITIONS
            if by_user and actor is not Actor.UI_USER and actor is not Actor.SYSTEM_MIGRATION:
                raise PolicyViolation("only the user confirms or dismisses", details={"from": old, "to": new_state})
            if not by_user and actor is Actor.UI_USER:
                raise PolicyViolation("processing transitions are not user actions", details={"from": old, "to": new_state})
            extra["state"] = new_state
            extra["updated_at"] = now or _now()
            assignments = ", ".join(f"{c} = ?" for c in extra)
            self._connection.execute(
                f"UPDATE intake_candidates SET {assignments} WHERE candidate_id = ?",  # identifiers validated
                [*extra.values(), candidate_id],
            )
            return old

    # -- checks -----------------------------------------------------------------

    def _unique_columns(self, table: str) -> frozenset[str]:
        """Single columns that identify at most one row (PK, rowid alias or single-column UNIQUE)."""

        columns = {str(r[1]) for r in self._connection.execute("SELECT * FROM pragma_table_info(?)", (table,)) if r[5] == 1}
        pk_count = sum(1 for r in self._connection.execute("SELECT * FROM pragma_table_info(?)", (table,)) if r[5] > 0)
        if pk_count > 1:
            columns = set()  # a composite primary key is not a single-column identifier
        for index in self._connection.execute("SELECT name, \"unique\" FROM pragma_index_list(?)", (table,)):
            if index[1]:
                cols = [str(r[2]) for r in self._connection.execute("SELECT * FROM pragma_index_info(?)", (index[0],))]
                if len(cols) == 1:
                    columns.add(cols[0])
        return frozenset(columns)

    def _table_columns(self, table: str) -> frozenset[str]:
        if table not in self._columns:
            rows = self._connection.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
            self._columns[table] = frozenset(str(r[0]) for r in rows)
        return self._columns[table]

    def _authorize(
        self,
        owner: Owner,
        actor: Actor,
        table: str,
        columns: set[str],
        *,
        update: bool,
        changed: set[str] | None = None,
    ) -> None:
        if table in UNWRITABLE_TABLES:
            raise PolicyViolation("table is not writable through StateWriter", details={"table": table})
        try:
            table_owner = owner_of(table)
        except KeyError:
            raise PolicyViolation("unknown table", details={"table": table}) from None
        if owner is not table_owner:
            raise PolicyViolation("declared owner does not match the table", details={"table": table, "owner": str(owner)})
        if actor not in ALLOWED[table_owner]:
            raise PolicyViolation(
                "actor may not write this owner",
                details={"table": table, "owner": str(owner), "actor": str(actor)},
            )
        known = self._table_columns(table)
        unknown = columns - known
        if not known or unknown:
            raise PolicyViolation("unknown columns", details={"table": table, "columns": sorted(unknown)})
        human_only = HUMAN_ONLY_COLUMNS.get(table, frozenset())
        touched = changed if update else columns
        if touched is not None and touched & human_only:
            raise PolicyViolation(
                "human-only columns can only be set by the gate commit path",
                details={"table": table, "columns": sorted(touched & human_only)},
            )
        if "owner" in columns and update:
            raise PolicyViolation("owner labels are immutable", details={"table": table})
        immutable = IMMUTABLE_COLUMNS.get(table, frozenset())
        if update and changed is not None and changed & immutable:
            raise PolicyViolation(
                "identifiers are immutable after insert",
                details={"table": table, "columns": sorted(changed & immutable)},
            )
        if update and table_owner in INSERT_ONLY_OWNERS:
            allowed = SOURCE_MUTABLE_COLUMNS.get(table, frozenset())
            if table_owner is Owner.AI or not (changed or set()) <= allowed:
                raise PolicyViolation("SOURCE/AI rows are insert-only", details={"table": table})
            if actor is not Actor.INGESTER and actor is not Actor.SYSTEM_MIGRATION:
                raise PolicyViolation("actor may not update SOURCE rows", details={"table": table})

    def _record_user_edits(self, table: str, key: Mapping[str, Any]) -> None:
        conditions = " AND ".join(f"{c} = ?" for c in key)
        has_body = "body_blob_sha256" in self._table_columns(table)
        select = "id, body_blob_sha256" if has_body else "id"
        rows = self._connection.execute(
            f"SELECT {select} FROM {table} WHERE {conditions}", list(key.values())  # identifiers validated
        ).fetchall()
        for row in rows:
            self._connection.execute(
                "INSERT INTO user_edits(record_kind, record_id, previous_blob_sha256, edited_at) VALUES (?, ?, ?, ?)",
                (table, int(row[0]), row[1] if has_body else None, _now()),
            )
