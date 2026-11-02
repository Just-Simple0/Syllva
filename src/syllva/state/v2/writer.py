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
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from syllva.domain.errors import PolicyViolation
from syllva.state.v2.ownership import (
    ALLOWED,
    HUMAN_ONLY_COLUMNS,
    INSERT_ONLY_OWNERS,
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

    # -- transactions ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[StateWriter]:
        if self._connection.in_transaction:
            yield self  # nested use joins the outer transaction
            return
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # -- generic writes -------------------------------------------------------

    def insert(self, owner: Owner, actor: Actor, table: str, values: Mapping[str, Any]) -> int:
        self._authorize(owner, actor, table, set(values), update=False)
        columns = list(values)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "  # identifiers validated in _authorize
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
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
        if not key:
            raise PolicyViolation("update needs a key")
        self._authorize(owner, actor, table, set(values) | set(key), update=True, changed=set(values))
        if table == "materials" and "processing_status" in values:
            raise PolicyViolation("use transition_material_status() for processing_status")
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

    # -- checks -----------------------------------------------------------------

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
