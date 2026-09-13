"""SQLite implementation of the durable StateStore contract.

Only orchestration metadata is persisted here.  Canonical source bodies remain
in their provider, as required by implementation spec §7/§8.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from uls.domain.enums import JobStatus, to_processing_status
from uls.domain.ids import parse_course_key, parse_entity_id

from uls.orchestration.locks import LocalWorkerLock
from uls.orchestration.retry import (
    DEFAULT_MAX_ATTEMPTS,
    coerce_error_class,
    should_retry,
)
from uls.orchestration.jobs import derive_job_key

from .models import (
    Checkpoint,
    EntityAllocation,
    EntityReservation,
    IntakeItem,
    IntakeObservation,
    IntakePlan,
    Job,
    ProcessingRecord,
    ProviderWriteAttempt,
    RequestReceipt,
    SemesterRegistration,
    SessionSourceBinding,
    SourceFile,
    SourceVersion,
)


_TERMINAL_STATUSES = {
    JobStatus.READY,
    JobStatus.PARTIAL,
    JobStatus.NEEDS_REVIEW,
    JobStatus.FAILED,
}
_COMPLETION_STATUSES = {
    JobStatus.READY,
    JobStatus.PARTIAL,
    JobStatus.NEEDS_REVIEW,
}
_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset(_TERMINAL_STATUSES | {JobStatus.PENDING}),
    JobStatus.READY: frozenset(),
    JobStatus.PARTIAL: frozenset(),
    JobStatus.NEEDS_REVIEW: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.FAILED, JobStatus.PENDING}),
}

_JOB_KEY_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


# The preview tables are installed idempotently after the frozen v1.2
# migration.  Keeping this additive bootstrap inside the store lets existing
# state files gain the new durable ledger without changing the v1.2 migration
# count or rewriting an applied migration.
_INTAKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS semester_registrations (
    semester TEXT PRIMARY KEY,
    config_fingerprint TEXT NOT NULL,
    workspace_fingerprint TEXT NOT NULL,
    drive_static_ids_json TEXT NOT NULL,
    notion_resolved_ids_json TEXT NOT NULL,
    provider_account_binding_id TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_items (
    intake_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_file_id TEXT NOT NULL,
    semester TEXT NOT NULL,
    original_parent_id TEXT NOT NULL,
    observed_parent_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    observed_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    course_candidates_json TEXT NOT NULL DEFAULT '[]',
    selected_course_key TEXT,
    selected_kind TEXT,
    file_intake_page_id TEXT,
    input_request_page_id TEXT,
    request_revision_hash TEXT,
    plan_revision TEXT,
    pending_request_key TEXT,
    canonical_entity_id TEXT,
    canonical_source_json TEXT,
    content_status TEXT NOT NULL DEFAULT 'Pending',
    last_error_code TEXT,
    last_error TEXT,
    last_successful_stage TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(provider, provider_file_id)
);
CREATE INDEX IF NOT EXISTS idx_intake_items_status ON intake_items(status);
CREATE INDEX IF NOT EXISTS idx_intake_items_semester ON intake_items(semester);
CREATE TABLE IF NOT EXISTS intake_observations (
    id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    source_hash TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(intake_id, source_hash, source_version)
);
CREATE TABLE IF NOT EXISTS request_receipts (
    receipt_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    input_requests_data_source_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_revision_hash TEXT NOT NULL,
    provider_page_id TEXT,
    normalized_user_hash TEXT,
    target_snapshot_hash TEXT NOT NULL,
    submitted_at TEXT,
    plan_revision TEXT,
    state TEXT NOT NULL,
    workspace_fingerprint TEXT NOT NULL DEFAULT '',
    request_type TEXT,
    intake_ids_json TEXT,
    UNIQUE(provider, input_requests_data_source_id, request_key)
);
CREATE INDEX IF NOT EXISTS idx_request_receipts_page ON request_receipts(provider_page_id);
CREATE TABLE IF NOT EXISTS intake_plans (
    plan_id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    request_revision_hash TEXT NOT NULL,
    plan_revision TEXT NOT NULL,
    resolved_workspace_fingerprint TEXT NOT NULL,
    target_snapshot_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_write_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    operation_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    target_id TEXT,
    prewrite_committed_at TEXT NOT NULL,
    dispatched_at TEXT,
    response_state TEXT NOT NULL,
    readback_json TEXT,
    error_class TEXT
);
CREATE TABLE IF NOT EXISTS entity_reservations (
    reservation_id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    entity_kind TEXT NOT NULL,
    entity_app_id TEXT NOT NULL,
    parent_folder_id TEXT NOT NULL,
    marker_key TEXT NOT NULL,
    state TEXT NOT NULL,
    plan_revision TEXT NOT NULL,
    source_file_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(intake_id, entity_kind),
    UNIQUE(entity_kind, entity_app_id)
);
CREATE TABLE IF NOT EXISTS session_source_bindings (
    binding_id TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_file_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(course_key, session_id),
    UNIQUE(provider, provider_file_id)
);
CREATE TABLE IF NOT EXISTS intake_stage_events (
    event_id TEXT PRIMARY KEY,
    intake_id TEXT,
    operation_key TEXT,
    stage TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_stage_events_intake ON intake_stage_events(intake_id, event_id);
"""


class SQLiteStateStore:
    """Thread-safe SQLite StateStore with repeatable migrations."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._worker_lock = LocalWorkerLock(f"{self.db_path}.worker.lock")
        # A store is usable immediately, while the public method remains
        # available for explicit/repeated migration checks.
        self.apply_migrations()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for diagnostics/tests without hiding it."""

        return self._connection

    def close(self) -> None:
        with self._lock:
            self._worker_lock.release()
            self._connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def apply_migrations(self) -> None:
        """Apply sorted SQL migrations once each.

        The initial migration creates ``schema_migrations`` itself.  A tiny
        bootstrap CREATE is used only to make the first version lookup safe;
        it is identical to the table declared in the frozen migration.
        """

        migrations_dir = Path(__file__).with_name("migrations")
        migration_files = sorted(migrations_dir.glob("*.sql"))
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for migration_path in migration_files:
                version = migration_path.stem
                row = self._connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if row is not None:
                    continue
                script = migration_path.read_text(encoding="utf-8")
                # The supplied migration uses IF NOT EXISTS throughout.  Use
                # executescript so future migrations can contain multiple SQL
                # statements, then record only successful execution.
                self._connection.executescript(script)
                self._connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _utc_now()),
                )
            self._connection.executescript(_INTAKE_SCHEMA)
            intake_columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(intake_items)").fetchall()
            }
            if "file_intake_page_id" not in intake_columns:
                self._connection.execute(
                    "ALTER TABLE intake_items ADD COLUMN file_intake_page_id TEXT"
                )
            if "pending_request_key" not in intake_columns:
                self._connection.execute(
                    "ALTER TABLE intake_items ADD COLUMN pending_request_key TEXT"
                )
            receipt_columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(request_receipts)").fetchall()
            }
            if "request_type" not in receipt_columns:
                self._connection.execute(
                    "ALTER TABLE request_receipts ADD COLUMN request_type TEXT"
                )
            if "intake_ids_json" not in receipt_columns:
                self._connection.execute(
                    "ALTER TABLE request_receipts ADD COLUMN intake_ids_json TEXT"
                )

    # ------------------------------------------------------------------
    # v1.3 intake preview ledger
    # ------------------------------------------------------------------
    def register_semester_registration(
        self,
        registration: SemesterRegistration | None = None,
        *,
        semester: str | None = None,
        config_fingerprint: str | None = None,
        workspace_fingerprint: str | None = None,
        drive_static_ids_json: Any = None,
        notion_resolved_ids_json: Any = None,
        provider_account_binding_id: str | None = None,
        captured_at: str | None = None,
    ) -> SemesterRegistration:
        if registration is not None:
            if any(
                value is not None
                for value in (
                    semester,
                    config_fingerprint,
                    workspace_fingerprint,
                    provider_account_binding_id,
                )
            ):
                raise TypeError("pass either registration or registration fields")
            semester = registration.semester
            config_fingerprint = registration.config_fingerprint
            workspace_fingerprint = registration.workspace_fingerprint
            drive_static_ids_json = registration.drive_static_ids_json
            notion_resolved_ids_json = registration.notion_resolved_ids_json
            provider_account_binding_id = registration.provider_account_binding_id
            captured_at = registration.captured_at
        for value, name in (
            (semester, "semester"),
            (config_fingerprint, "config_fingerprint"),
            (workspace_fingerprint, "workspace_fingerprint"),
            (provider_account_binding_id, "provider_account_binding_id"),
        ):
            _require_text(value, name)
        captured = captured_at or _utc_now()
        drive_json = _json_text(drive_static_ids_json)
        notion_json = _json_text(notion_resolved_ids_json)
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO semester_registrations(
                    semester, config_fingerprint, workspace_fingerprint,
                    drive_static_ids_json, notion_resolved_ids_json,
                    provider_account_binding_id, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(semester) DO UPDATE SET
                    config_fingerprint=excluded.config_fingerprint,
                    workspace_fingerprint=excluded.workspace_fingerprint,
                    drive_static_ids_json=excluded.drive_static_ids_json,
                    notion_resolved_ids_json=excluded.notion_resolved_ids_json,
                    provider_account_binding_id=excluded.provider_account_binding_id,
                    captured_at=excluded.captured_at
                """,
                (
                    semester,
                    config_fingerprint,
                    workspace_fingerprint,
                    drive_json,
                    notion_json,
                    provider_account_binding_id,
                    captured,
                ),
            )
            return _semester_registration_from_row(
                connection.execute(
                    "SELECT * FROM semester_registrations WHERE semester = ?", (semester,)
                ).fetchone()
            )

    def get_semester_registration(self, semester: str) -> SemesterRegistration | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM semester_registrations WHERE semester = ?", (semester,)
            ).fetchone()
            return None if row is None else _semester_registration_from_row(row)

    def upsert_intake_item(
        self,
        item: IntakeItem | None = None,
        *,
        intake_id: str | None = None,
        provider: str | None = None,
        provider_file_id: str | None = None,
        semester: str | None = None,
        original_parent_id: str | None = None,
        observed_parent_id: str | None = None,
        original_name: str | None = None,
        mime_type: str | None = None,
        source_hash: str | None = None,
        source_version: int | None = None,
        status: str = "OBSERVED",
        observed_kind: str = "UNKNOWN",
        course_candidates_json: Any = None,
        selected_course_key: str | None = None,
        selected_kind: str | None = None,
        file_intake_page_id: str | None = None,
        input_request_page_id: str | None = None,
        request_revision_hash: str | None = None,
        plan_revision: str | None = None,
        canonical_entity_id: str | None = None,
        canonical_source_json: Any = None,
        content_status: str = "Pending",
        last_error_code: str | None = None,
        last_error: str | None = None,
        last_successful_stage: str | None = None,
        first_seen_at: str | None = None,
        last_seen_at: str | None = None,
    ) -> IntakeItem:
        if item is not None:
            if any(value is not None for value in (intake_id, provider, provider_file_id, semester)):
                raise TypeError("pass either item or intake fields")
            values = item
            intake_id = values.intake_id
            provider = values.provider
            provider_file_id = values.provider_file_id
            semester = values.semester
            original_parent_id = values.original_parent_id
            observed_parent_id = values.observed_parent_id
            original_name = values.original_name
            mime_type = values.mime_type
            source_hash = values.source_hash
            source_version = values.source_version
            status = values.status
            observed_kind = values.observed_kind
            course_candidates_json = values.course_candidates_json
            selected_course_key = values.selected_course_key
            selected_kind = values.selected_kind
            file_intake_page_id = values.file_intake_page_id
            input_request_page_id = values.input_request_page_id
            request_revision_hash = values.request_revision_hash
            plan_revision = values.plan_revision
            canonical_entity_id = values.canonical_entity_id
            canonical_source_json = values.canonical_source_json
            content_status = values.content_status
            last_error_code = values.last_error_code
            last_error = values.last_error
            last_successful_stage = values.last_successful_stage
            first_seen_at = values.first_seen_at
            last_seen_at = values.last_seen_at
        for value, name in (
            (intake_id, "intake_id"),
            (provider, "provider"),
            (provider_file_id, "provider_file_id"),
            (semester, "semester"),
            (original_parent_id, "original_parent_id"),
            (observed_parent_id, "observed_parent_id"),
            (original_name, "original_name"),
            (mime_type, "mime_type"),
            (source_hash, "source_hash"),
        ):
            _require_text(value, name)
        if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
            raise ValueError("source_version must be a positive integer")
        first_seen = first_seen_at or _utc_now()
        last_seen = last_seen_at or first_seen
        candidates_json = _json_text([] if course_candidates_json is None else course_candidates_json)
        canonical_json = None if canonical_source_json is None else _json_text(canonical_source_json)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM intake_items WHERE provider = ? AND provider_file_id = ?",
                (provider, provider_file_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO intake_items(
                        intake_id, provider, provider_file_id, semester,
                        original_parent_id, observed_parent_id, original_name, mime_type,
                        source_hash, source_version, status, observed_kind,
                        course_candidates_json, selected_course_key, selected_kind,
                        file_intake_page_id, input_request_page_id, request_revision_hash, plan_revision,
                        canonical_entity_id, canonical_source_json, content_status,
                        last_error_code, last_error, last_successful_stage,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intake_id,
                        provider,
                        provider_file_id,
                        semester,
                        original_parent_id,
                        observed_parent_id,
                        original_name,
                        mime_type,
                        source_hash,
                        source_version,
                        status,
                        observed_kind,
                        candidates_json,
                        selected_course_key,
                        selected_kind,
                        file_intake_page_id,
                        input_request_page_id,
                        request_revision_hash,
                        plan_revision,
                        canonical_entity_id,
                        canonical_json,
                        content_status,
                        last_error_code,
                        last_error,
                        last_successful_stage,
                        first_seen,
                        last_seen,
                    ),
                )
            else:
                if existing["intake_id"] != intake_id:
                    raise ValueError("provider/file identity is already bound to another intake ID")
                # Discovery updates only immutable observations and derived
                # metadata.  It never overwrites user-selected routing or a
                # canonical binding that may already exist.
                connection.execute(
                    """
                    UPDATE intake_items SET
                        observed_parent_id=?, original_name=?, mime_type=?,
                        source_hash=?, source_version=?, observed_kind=?,
                        course_candidates_json=?, last_seen_at=?
                    WHERE intake_id=?
                    """,
                    (
                        observed_parent_id,
                        original_name,
                        mime_type,
                        source_hash,
                        source_version,
                        observed_kind,
                        candidates_json,
                        last_seen,
                        intake_id,
                    ),
                )
            return _intake_item_from_row(
                connection.execute("SELECT * FROM intake_items WHERE intake_id = ?", (intake_id,)).fetchone()
            )

    def get_intake_item(self, intake_id: str) -> IntakeItem | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM intake_items WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            return None if row is None else _intake_item_from_row(row)

    def get_intake_item_by_provider_file(self, provider: str, provider_file_id: str) -> IntakeItem | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM intake_items WHERE provider = ? AND provider_file_id = ?",
                (provider, provider_file_id),
            ).fetchone()
            return None if row is None else _intake_item_from_row(row)

    def list_intake_items(
        self, *, semester: str | None = None, status: str | None = None, limit: int = 1000
    ) -> list[IntakeItem]:
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError("intake listing limit must be 1–10000")
        clauses: list[str] = []
        values: list[Any] = []
        if semester is not None:
            clauses.append("semester = ?")
            values.append(semester)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        query = "SELECT * FROM intake_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY first_seen_at ASC, intake_id ASC LIMIT ?"
        values.append(limit)
        with self._lock:
            return [_intake_item_from_row(row) for row in self._connection.execute(query, values)]

    def update_intake_item(self, intake_id: str, **patch: Any) -> IntakeItem:
        allowed = {
            "observed_parent_id", "status", "observed_kind", "course_candidates_json",
            "selected_course_key", "selected_kind", "file_intake_page_id", "input_request_page_id",
            "request_revision_hash", "plan_revision", "pending_request_key", "canonical_entity_id",
            "canonical_source_json", "content_status", "last_error_code", "last_error",
            "last_successful_stage", "last_seen_at",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError("unknown intake fields: " + ", ".join(sorted(unknown)))
        if not patch:
            current = self.get_intake_item(intake_id)
            if current is None:
                raise KeyError(intake_id)
            return current
        values: dict[str, Any] = {}
        for key, value in patch.items():
            if key.endswith("_json") and value is not None:
                value = _json_text(value)
            values[key] = value
        values["last_seen_at"] = values.get("last_seen_at") or _utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                f"UPDATE intake_items SET {assignments} WHERE intake_id = ?",
                [*values.values(), intake_id],
            )
            if cursor.rowcount != 1:
                raise KeyError(intake_id)
            return _intake_item_from_row(
                connection.execute("SELECT * FROM intake_items WHERE intake_id = ?", (intake_id,)).fetchone()
            )

    def record_intake_observation(
        self,
        observation: IntakeObservation | None = None,
        *,
        intake_id: str | None = None,
        source_hash: str | None = None,
        source_version: int | None = None,
        metadata: Any = None,
        observation_id: str | None = None,
        observed_at: str | None = None,
    ) -> IntakeObservation:
        if observation is not None:
            intake_id = observation.intake_id
            source_hash = observation.source_hash
            source_version = observation.source_version
            metadata = observation.metadata_json
            observation_id = observation.id
            observed_at = observation.observed_at
        for value, name in ((intake_id, "intake_id"), (source_hash, "source_hash")):
            _require_text(value, name)
        if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
            raise ValueError("source_version must be a positive integer")
        identifier = observation_id or _new_id("obs_")
        metadata_json = metadata if isinstance(metadata, str) else _json_text(metadata or {})
        captured = observed_at or _utc_now()
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO intake_observations(id, intake_id, source_hash, source_version, metadata_json, observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(intake_id, source_hash, source_version) DO NOTHING
                """,
                (identifier, intake_id, source_hash, source_version, metadata_json, captured),
            )
            row = connection.execute(
                """
                SELECT * FROM intake_observations
                WHERE intake_id=? AND source_hash=? AND source_version=?
                """,
                (intake_id, source_hash, source_version),
            ).fetchone()
            return _intake_observation_from_row(row)

    def list_intake_observations(self, intake_id: str) -> list[IntakeObservation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM intake_observations WHERE intake_id = ? ORDER BY observed_at, id",
                (intake_id,),
            ).fetchall()
            return [_intake_observation_from_row(row) for row in rows]

    def create_request_receipt(
        self,
        receipt: RequestReceipt | None = None,
        **kwargs: Any,
    ) -> RequestReceipt:
        if receipt is not None:
            if kwargs:
                raise TypeError("pass either receipt or receipt fields")
            values = dict(receipt.__dict__)
        else:
            values = dict(kwargs)
        if not values.get("receipt_id"):
            values["receipt_id"] = _new_id("receipt_")
        values.setdefault("state", "Draft")
        values.setdefault("workspace_fingerprint", "")
        values.setdefault("provider_page_id", None)
        values.setdefault("normalized_user_hash", None)
        values.setdefault("submitted_at", None)
        values.setdefault("plan_revision", None)
        values.setdefault("request_type", None)
        values.setdefault("intake_ids_json", None)
        for name in (
            "receipt_id", "provider", "input_requests_data_source_id", "request_key",
            "request_revision_hash", "target_snapshot_hash", "state",
        ):
            _require_text(values.get(name), name)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM request_receipts
                WHERE provider=? AND input_requests_data_source_id=? AND request_key=?
                """,
                (values["provider"], values["input_requests_data_source_id"], values["request_key"]),
            ).fetchone()
            if existing is not None:
                if existing["request_revision_hash"] != values["request_revision_hash"] or existing["target_snapshot_hash"] != values["target_snapshot_hash"]:
                    raise ValueError("Request Key is already bound to a different request tuple")
                return _request_receipt_from_row(existing)
            connection.execute(
                """
                INSERT INTO request_receipts(
                    receipt_id, provider, input_requests_data_source_id, request_key,
                    request_revision_hash, provider_page_id, normalized_user_hash,
                    target_snapshot_hash, submitted_at, plan_revision, state,
                    workspace_fingerprint, request_type, intake_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "receipt_id", "provider", "input_requests_data_source_id", "request_key",
                    "request_revision_hash", "provider_page_id", "normalized_user_hash",
                    "target_snapshot_hash", "submitted_at", "plan_revision", "state",
                    "workspace_fingerprint", "request_type", "intake_ids_json",
                )),
            )
            return _request_receipt_from_row(
                connection.execute(
                    "SELECT * FROM request_receipts WHERE receipt_id=?", (values["receipt_id"],)
                ).fetchone()
            )

    def get_request_receipt(
        self, request_key: str | None = None, *, receipt_id: str | None = None
    ) -> RequestReceipt | None:
        if request_key is None and receipt_id is None:
            raise TypeError("request_key or receipt_id is required")
        query = "SELECT * FROM request_receipts WHERE receipt_id=?" if receipt_id else "SELECT * FROM request_receipts WHERE request_key=?"
        value = receipt_id or request_key
        with self._lock:
            row = self._connection.execute(query, (value,)).fetchone()
            return None if row is None else _request_receipt_from_row(row)

    def list_request_receipts(
        self, *, provider: str | None = None, data_source_id: str | None = None
    ) -> list[RequestReceipt]:
        clauses: list[str] = []
        values: list[Any] = []
        if provider is not None:
            clauses.append("provider=?")
            values.append(provider)
        if data_source_id is not None:
            clauses.append("input_requests_data_source_id=?")
            values.append(data_source_id)
        query = "SELECT * FROM request_receipts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY receipt_id"
        with self._lock:
            return [_request_receipt_from_row(row) for row in self._connection.execute(query, values)]

    def bind_request_page(self, request_key: str, provider_page_id: str) -> RequestReceipt:
        _require_text(provider_page_id, "provider_page_id")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM request_receipts WHERE request_key=?", (request_key,)
            ).fetchone()
            if row is None:
                raise KeyError(request_key)
            if row["provider_page_id"] not in (None, provider_page_id):
                raise ValueError("Request Key is already bound to a different provider page")
            connection.execute(
                "UPDATE request_receipts SET provider_page_id=? WHERE request_key=?",
                (provider_page_id, request_key),
            )
            return _request_receipt_from_row(
                connection.execute(
                    "SELECT * FROM request_receipts WHERE request_key=?", (request_key,)
                ).fetchone()
            )

    def update_request_receipt(self, request_key: str, **patch: Any) -> RequestReceipt:
        allowed = {
            "provider_page_id", "normalized_user_hash", "submitted_at", "plan_revision",
            "state", "workspace_fingerprint",
        }
        if set(patch) - allowed:
            raise ValueError("unknown request receipt fields")
        if "provider_page_id" in patch:
            current = self.get_request_receipt(request_key)
            if current is None:
                raise KeyError(request_key)
            if current.provider_page_id not in (None, patch["provider_page_id"]):
                raise ValueError("provider page ID cannot be rebound")
        if not patch:
            current = self.get_request_receipt(request_key)
            if current is None:
                raise KeyError(request_key)
            return current
        assignments = ", ".join(f"{key}=?" for key in patch)
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                f"UPDATE request_receipts SET {assignments} WHERE request_key=?",
                [*patch.values(), request_key],
            )
            if cursor.rowcount != 1:
                raise KeyError(request_key)
            return _request_receipt_from_row(
                connection.execute(
                    "SELECT * FROM request_receipts WHERE request_key=?", (request_key,)
                ).fetchone()
            )

    def create_intake_plan(self, plan: IntakePlan | None = None, **kwargs: Any) -> IntakePlan:
        values = dict(plan.__dict__) if plan is not None else dict(kwargs)
        values.setdefault("plan_id", _new_id("plan_"))
        values.setdefault("status", "PLANNED")
        values.setdefault("created_at", _utc_now())
        for name in (
            "plan_id", "intake_id", "request_revision_hash", "plan_revision",
            "resolved_workspace_fingerprint", "plan_hash",
        ):
            _require_text(values.get(name), name)
        target_json = _json_text(values.get("target_snapshot_json", {}))
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM intake_plans WHERE plan_hash=?", (values["plan_hash"],)
            ).fetchone()
            if existing is not None:
                if existing["intake_id"] != values["intake_id"]:
                    raise ValueError("plan hash is bound to another intake")
                return _intake_plan_from_row(existing)
            connection.execute(
                """
                INSERT INTO intake_plans(
                    plan_id, intake_id, request_revision_hash, plan_revision,
                    resolved_workspace_fingerprint, target_snapshot_json,
                    plan_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["plan_id"], values["intake_id"], values["request_revision_hash"],
                    values["plan_revision"], values["resolved_workspace_fingerprint"],
                    target_json, values["plan_hash"], values["status"], values["created_at"],
                ),
            )
            return _intake_plan_from_row(
                connection.execute("SELECT * FROM intake_plans WHERE plan_id=?", (values["plan_id"],)).fetchone()
            )

    def get_intake_plan(self, plan_revision: str) -> IntakePlan | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM intake_plans WHERE plan_revision=? ORDER BY created_at DESC LIMIT 1",
                (plan_revision,),
            ).fetchone()
            return None if row is None else _intake_plan_from_row(row)

    def record_provider_write_attempt(
        self, attempt: ProviderWriteAttempt | None = None, **kwargs: Any
    ) -> ProviderWriteAttempt:
        values = dict(attempt.__dict__) if attempt is not None else dict(kwargs)
        values.setdefault("attempt_id", _new_id("write_"))
        values.setdefault("target_id", None)
        values.setdefault("prewrite_committed_at", _utc_now())
        values.setdefault("dispatched_at", None)
        values.setdefault("response_state", "PREPARED")
        values.setdefault("readback_json", None)
        values.setdefault("error_class", None)
        if values["readback_json"] is not None:
            # Provider readbacks are commonly passed as mappings/dataclasses
            # by the worker.  Normalize them at the StateStore boundary so
            # the durable TEXT column never receives a Python object.
            values["readback_json"] = _json_text(values["readback_json"])
        for name in ("attempt_id", "operation", "operation_key", "provider", "response_state"):
            _require_text(values.get(name), name)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM provider_write_attempts WHERE operation_key=?",
                (values["operation_key"],),
            ).fetchone()
            if existing is not None:
                if existing["operation"] != values["operation"] or existing["provider"] != values["provider"]:
                    raise ValueError("operation key is bound to a different write")
                return _provider_write_attempt_from_row(existing)
            connection.execute(
                """
                INSERT INTO provider_write_attempts(
                    attempt_id, operation, operation_key, provider, target_id,
                    prewrite_committed_at, dispatched_at, response_state,
                    readback_json, error_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "attempt_id", "operation", "operation_key", "provider", "target_id",
                    "prewrite_committed_at", "dispatched_at", "response_state",
                    "readback_json", "error_class",
                )),
            )
            return _provider_write_attempt_from_row(
                connection.execute(
                    "SELECT * FROM provider_write_attempts WHERE attempt_id=?", (values["attempt_id"],)
                ).fetchone()
            )

    def get_provider_write_attempt(self, operation_key: str) -> ProviderWriteAttempt | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM provider_write_attempts WHERE operation_key=?", (operation_key,)
            ).fetchone()
            return None if row is None else _provider_write_attempt_from_row(row)

    def update_provider_write_attempt(self, operation_key: str, **patch: Any) -> ProviderWriteAttempt:
        allowed = {"target_id", "dispatched_at", "response_state", "readback_json", "error_class"}
        if set(patch) - allowed:
            raise ValueError("unknown provider write attempt fields")
        if "readback_json" in patch and patch["readback_json"] is not None:
            patch["readback_json"] = _json_text(patch["readback_json"])
        if not patch:
            row = self.get_provider_write_attempt(operation_key)
            if row is None:
                raise KeyError(operation_key)
            return row
        assignments = ", ".join(f"{key}=?" for key in patch)
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                f"UPDATE provider_write_attempts SET {assignments} WHERE operation_key=?",
                [*patch.values(), operation_key],
            )
            if cursor.rowcount != 1:
                raise KeyError(operation_key)
            return _provider_write_attempt_from_row(
                connection.execute(
                    "SELECT * FROM provider_write_attempts WHERE operation_key=?", (operation_key,)
                ).fetchone()
            )

    def reserve_entity(
        self, reservation: EntityReservation | None = None, **kwargs: Any
    ) -> EntityReservation:
        values = dict(reservation.__dict__) if reservation is not None else dict(kwargs)
        values.setdefault("reservation_id", _new_id("reserve_"))
        values.setdefault("source_file_id", None)
        values.setdefault("created_at", _utc_now())
        for name in (
            "reservation_id", "intake_id", "entity_kind", "entity_app_id",
            "parent_folder_id", "marker_key", "state", "plan_revision",
        ):
            _require_text(values.get(name), name)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM entity_reservations WHERE intake_id=? AND entity_kind=?",
                (values["intake_id"], values["entity_kind"]),
            ).fetchone()
            if existing is not None:
                if any(existing[name] != values[name] for name in ("entity_app_id", "parent_folder_id", "marker_key")):
                    raise ValueError("intake already has a different entity reservation")
                return _entity_reservation_from_row(existing)
            collision = connection.execute(
                "SELECT * FROM entity_reservations WHERE entity_kind=? AND entity_app_id=?",
                (values["entity_kind"], values["entity_app_id"]),
            ).fetchone()
            if collision is not None:
                raise ValueError("entity app ID is already reserved")
            connection.execute(
                """
                INSERT INTO entity_reservations(
                    reservation_id, intake_id, entity_kind, entity_app_id,
                    parent_folder_id, marker_key, state, plan_revision,
                    source_file_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "reservation_id", "intake_id", "entity_kind", "entity_app_id",
                    "parent_folder_id", "marker_key", "state", "plan_revision",
                    "source_file_id", "created_at",
                )),
            )
            return _entity_reservation_from_row(
                connection.execute(
                    "SELECT * FROM entity_reservations WHERE reservation_id=?", (values["reservation_id"],)
                ).fetchone()
            )

    def get_entity_reservation(
        self, reservation_id: str | None = None, *, intake_id: str | None = None, entity_kind: str | None = None
    ) -> EntityReservation | None:
        if reservation_id is None and (intake_id is None or entity_kind is None):
            raise TypeError("reservation ID or intake/entity kind is required")
        if reservation_id is not None:
            query, values = "SELECT * FROM entity_reservations WHERE reservation_id=?", (reservation_id,)
        else:
            query, values = "SELECT * FROM entity_reservations WHERE intake_id=? AND entity_kind=?", (intake_id, entity_kind)
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
            return None if row is None else _entity_reservation_from_row(row)

    def update_entity_reservation(self, reservation_id: str, **patch: Any) -> EntityReservation:
        allowed = {"state"}
        if set(patch) - allowed:
            raise ValueError("entity reservation identity is immutable")
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE entity_reservations SET state=? WHERE reservation_id=?",
                (patch.get("state"), reservation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(reservation_id)
            return _entity_reservation_from_row(
                connection.execute(
                    "SELECT * FROM entity_reservations WHERE reservation_id=?", (reservation_id,)
                ).fetchone()
            )

    def record_session_source_binding(
        self, binding: SessionSourceBinding | None = None, **kwargs: Any
    ) -> SessionSourceBinding:
        values = dict(binding.__dict__) if binding is not None else dict(kwargs)
        values.setdefault("binding_id", _new_id("binding_"))
        values.setdefault("created_at", _utc_now())
        for name in (
            "binding_id", "course_key", "session_id", "provider",
            "provider_file_id", "reservation_id", "state",
        ):
            _require_text(values.get(name), name)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM session_source_bindings WHERE provider=? AND provider_file_id=?",
                (values["provider"], values["provider_file_id"]),
            ).fetchone()
            if existing is not None:
                if any(existing[name] != values[name] for name in ("course_key", "session_id", "reservation_id")):
                    raise ValueError("source file is already bound to a different Session")
                return _session_source_binding_from_row(existing)
            connection.execute(
                """
                INSERT INTO session_source_bindings(
                    binding_id, course_key, session_id, provider, provider_file_id,
                    reservation_id, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "binding_id", "course_key", "session_id", "provider",
                    "provider_file_id", "reservation_id", "state", "created_at",
                )),
            )
            return _session_source_binding_from_row(
                connection.execute(
                    "SELECT * FROM session_source_bindings WHERE binding_id=?", (values["binding_id"],)
                ).fetchone()
            )

    def record_intake_stage_event(
        self,
        stage: str,
        *,
        intake_id: str | None = None,
        operation_key: str | None = None,
        detail: Any = None,
        event_id: str | None = None,
    ) -> str:
        _require_text(stage, "stage")
        identifier = event_id or _new_id("event_")
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO intake_stage_events(event_id, intake_id, operation_key, stage, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identifier, intake_id, operation_key, stage, _json_text(detail or {}), _utc_now()),
            )
        return identifier

    def list_intake_stage_events(self, intake_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intake_stage_events"
        values: tuple[Any, ...] = ()
        if intake_id is not None:
            query += " WHERE intake_id=?"
            values = (intake_id,)
        query += " ORDER BY created_at, event_id"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
            return [
                {
                    "event_id": row["event_id"],
                    "intake_id": row["intake_id"],
                    "operation_key": row["operation_key"],
                    "stage": row["stage"],
                    "detail": json.loads(row["detail_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    def create_job(
        self,
        job_key: str | Job | None = None,
        operation: str | None = None,
        stage: str | None = None,
        status: JobStatus | str = JobStatus.PENDING,
        course_key: str | None = None,
        source_file_id: str | None = None,
        source_hash: str | None = None,
        target_entity_id: str | None = None,
        *,
        job_id: str | None = None,
        attempt_count: int = 0,
        error_class: str | None = None,
        last_error: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        completed_at: str | None = None,
        processor_version: str | None = None,
        job: Job | None = None,
    ) -> Job:
        """Create or return the row for a deterministic ``job_key``.

        Passing a :class:`Job` object is supported for callers that already
        have a complete row.  On a duplicate key the existing row is returned
        unchanged; this is the final idempotency guard required by §8.1.1.
        """

        if job is not None:
            if job_key is not None:
                raise TypeError("pass either job or job_key, not both")
            job_key = job
        if isinstance(job_key, Job):
            supplied = job_key
            job_key = supplied.job_key
            operation = supplied.operation
            stage = supplied.stage
            status = supplied.status
            course_key = supplied.course_key
            source_file_id = supplied.source_file_id
            source_hash = supplied.source_hash
            target_entity_id = supplied.target_entity_id
            attempt_count = supplied.attempt_count
            error_class = supplied.error_class
            last_error = supplied.last_error
            created_at = supplied.created_at or created_at
            updated_at = supplied.updated_at or updated_at
            completed_at = supplied.completed_at
            processor_version = getattr(supplied, "processor_version", processor_version)

        operation = _canonical_operation(operation)
        _require_text(stage, "stage")
        normalized_status = to_processing_status(status)
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 0:
            raise ValueError("attempt_count must be a non-negative integer")
        source_identity_values = (source_file_id, source_hash, processor_version)
        is_source_processing = any(value is not None for value in source_identity_values)
        if is_source_processing:
            for value, name in (
                (source_file_id, "source_file_id"),
                (source_hash, "source_hash"),
                (processor_version, "processor_version"),
            ):
                _require_text(value, name)
            derived_key = derive_job_key(
                source_file_id,  # type: ignore[arg-type]
                source_hash,  # type: ignore[arg-type]
                operation,
                processor_version,  # type: ignore[arg-type]
            )
            if job_key is None:
                job_key = derived_key
            else:
                _validate_job_key(job_key)
                if job_key != derived_key:
                    raise ValueError("job_key does not match the canonical source identity")
        else:
            if job_key is None:
                raise ValueError(
                    "non-source jobs require an explicit deterministic job_key identity"
                )
        _validate_job_key(job_key)

        created = created_at if created_at is not None else _utc_now()
        updated = updated_at if updated_at is not None else created
        identifier = job_id or _new_id("job_")

        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing is not None:
                return _job_from_row(existing)
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, job_key, operation, stage, status,
                        course_key, source_file_id, source_hash, target_entity_id,
                        attempt_count, error_class, last_error,
                        created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        job_key,
                        operation,
                        stage,
                        normalized_status.value,
                        course_key,
                        source_file_id,
                        source_hash,
                        target_entity_id,
                        attempt_count,
                        error_class,
                        last_error,
                        created,
                        updated,
                        completed_at,
                    ),
                )
            except sqlite3.IntegrityError:
                # Another StateStore instance may have won the UNIQUE race.
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
                ).fetchone()
                if existing is None:
                    raise
                return _job_from_row(existing)
            return _job_from_row(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )

    def get_job(self, job_id: str | None = None, *, job_key: str | None = None) -> Job | None:
        if job_id is None and job_key is None:
            raise TypeError("get_job requires job_id or job_key")
        with self._lock:
            if job_key is not None:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    # Accepting a job key as a compatibility fallback keeps
                    # this lookup convenient without weakening the primary
                    # id-based contract.
                    row = self._connection.execute(
                        "SELECT * FROM jobs WHERE job_key = ?", (job_id,)
                    ).fetchone()
            return None if row is None else _job_from_row(row)

    def bind_job_source_identity(
        self,
        job_id: str,
        *,
        source_file_id: str,
        source_hash: str,
        course_key: str | None = None,
    ) -> Job:
        """Bind a plan job to the verified source generation before provenance.

        Intake jobs are created after USER routing but before a content read, so
        their deterministic plan key is not the legacy source-processing key.
        Once the provider bytes have passed the freshness gate, this narrow
        binding supplies the source identity needed by the existing read-only
        provenance join.  A bound job can never be rebound to another source
        generation.
        """

        _require_text(job_id, "job_id")
        _require_text(source_file_id, "source_file_id")
        _require_text(source_hash, "source_hash")
        if course_key is not None:
            _require_text(course_key, "course_key")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_key = ?", (job_id,)
                ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            if row["source_file_id"] not in (None, source_file_id):
                raise ValueError("job source file identity cannot be rebound")
            if row["source_hash"] not in (None, source_hash):
                raise ValueError("job source hash cannot be rebound")
            connection.execute(
                """
                UPDATE jobs
                SET source_file_id = ?, source_hash = ?,
                    course_key = COALESCE(course_key, ?), updated_at = ?
                WHERE id = ?
                """,
                (source_file_id, source_hash, course_key, _utc_now(), row["id"]),
            )
            return _job_from_row(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            )

    def list_jobs(self, *, limit: int = 100, entity_id: str | None = None) -> list[Job]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("job listing limit must be 1–1000")
        with self._lock:
            if entity_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
                )
            else:
                rows = self._connection.execute(
                    "SELECT * FROM jobs WHERE target_entity_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (entity_id, limit),
                )
            return [_job_from_row(row) for row in rows]

    def request_reprocess(self, job_id: str) -> Job:
        """Explicit local operator reprocessing; preserve prior attempt records.

        Caller must hold the local worker lock. This is deliberately distinct
        from automatic retry policy and cannot restart an active job.
        """
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in {"READY", "PARTIAL", "NEEDS_REVIEW", "FAILED"}:
                raise ValueError("reprocess requires an existing terminal job")
            source = connection.execute(
                "SELECT current_hash FROM source_files WHERE source_file_id=?", (row["source_file_id"],)
            ).fetchone()
            if source is not None and source["current_hash"] != row["source_hash"]:
                raise ValueError("reprocess requires the current source version; run sync first")
            connection.execute(
                """UPDATE jobs SET status='PENDING', attempt_count=0, error_class=NULL,
                last_error=NULL, completed_at=NULL, updated_at=? WHERE id=?""", (_utc_now(), job_id)
            )
            return _job_from_row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim_job(self, job_id: str | None = None, *, worker_id: str | None = None) -> Job | None:
        """Atomically claim a pending job and increment its attempt count."""

        del worker_id  # reserved for a future worker-identity column
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            if job_id is None:
                row = connection.execute(
                    """
                    SELECT id FROM jobs
                    WHERE status = ?
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """,
                    (JobStatus.PENDING.value,),
                ).fetchone()
                if row is None:
                    return None
                selected_id = row["id"]
            else:
                row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT id FROM jobs WHERE job_key = ?", (job_id,)
                    ).fetchone()
            if row is None:
                return None
            selected_id = row["id"]
            selected = connection.execute(
                "SELECT attempt_count FROM jobs WHERE id = ?", (selected_id,)
            ).fetchone()
            if selected is None:
                return None
            if int(selected["attempt_count"]) >= DEFAULT_MAX_ATTEMPTS:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error_class = COALESCE(error_class, ?),
                        last_error = COALESCE(last_error, ?), updated_at = ?,
                        completed_at = COALESCE(completed_at, ?)
                    WHERE id = ? AND status = ?
                    """,
                    (
                        JobStatus.FAILED.value,
                        "TRANSIENT",
                        "maximum retry attempts exceeded",
                        now,
                        now,
                        selected_id,
                        JobStatus.PENDING.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE job_id = ? AND finished_at IS NULL
                    """,
                    (JobStatus.FAILED.value, now, selected_id),
                )
                return None
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.PROCESSING.value,
                    now,
                    selected_id,
                    JobStatus.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                """
                UPDATE processing_records
                SET status = ?, finished_at = NULL
                WHERE job_id = ? AND finished_at IS NULL
                """,
                (JobStatus.PROCESSING.value, selected_id),
            )
            return _job_from_row(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (selected_id,)).fetchone()
            )

    def transition_job(
        self,
        job_id: str,
        status: JobStatus | str,
        *,
        error_class: str | None = None,
        last_error: str | None = None,
        completed_at: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """Perform one legal job-state transition and return the new row."""

        desired = to_processing_status(status)
        _validate_max_attempts(max_attempts)
        normalized_error_class = None
        if error_class is not None:
            normalized_error_class = _stored_error_class(error_class)
            coerce_error_class(normalized_error_class)
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_key = ?", (job_id,)
                ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            job_id = row["id"]
            current = to_processing_status(row["status"])
            if current == desired:
                return _job_from_row(row)
            if desired not in _ALLOWED_TRANSITIONS[current]:
                raise ValueError(f"invalid job transition: {current.value} -> {desired.value}")

            effective_error_class = (
                normalized_error_class
                if normalized_error_class is not None
                else row["error_class"]
            )
            if desired is JobStatus.PENDING and current in {
                JobStatus.PROCESSING,
                JobStatus.FAILED,
            }:
                retry_allowed = False
                if effective_error_class is not None:
                    retry_allowed = should_retry(
                        effective_error_class,
                        int(row["attempt_count"]),
                        max_attempts,
                    )
                if not retry_allowed:
                    # Once the retry budget is exhausted, or when the error is
                    # permanent/policy-denied/ambiguous, PENDING is not a legal
                    # destination.  Keep/close the row as FAILED.
                    desired = JobStatus.FAILED

            finished = completed_at
            if desired in _TERMINAL_STATUSES and finished is None:
                finished = _utc_now()
            if desired not in _TERMINAL_STATUSES:
                finished = None
            attempts = row["attempt_count"]
            if current is JobStatus.PENDING and desired is JobStatus.PROCESSING:
                attempts += 1
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, attempt_count = ?, error_class = ?, last_error = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    desired.value,
                    attempts,
                    normalized_error_class
                    if normalized_error_class is not None
                    else row["error_class"],
                    last_error if last_error is not None else row["last_error"],
                    _utc_now(),
                    finished,
                    job_id,
                ),
            )
            if desired is JobStatus.PROCESSING:
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = NULL
                    WHERE job_id = ? AND finished_at IS NULL
                    """,
                    (desired.value, job_id),
                )
            elif desired is JobStatus.PENDING:
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = NULL
                    WHERE job_id = ? AND finished_at IS NULL
                    """,
                    (desired.value, job_id),
                )
            elif desired in _TERMINAL_STATUSES:
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE job_id = ? AND finished_at IS NULL
                    """,
                    (desired.value, finished, job_id),
                )
            return _job_from_row(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )

    def complete_job(
        self,
        job_id: str,
        status: JobStatus | str = JobStatus.READY,
        *,
        error_class: str | None = None,
        last_error: str | None = None,
    ) -> Job:
        desired = to_processing_status(status)
        if desired not in _COMPLETION_STATUSES:
            raise ValueError("complete_job status must be READY, PARTIAL, or NEEDS_REVIEW")
        return self.transition_job(
            job_id,
            desired,
            error_class=error_class,
            last_error=last_error,
        )

    def requeue_job(
        self,
        job_id: str,
        error_class: str,
        *,
        last_error: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """Conditionally return a failed/processing job to ``PENDING``.

        Only retryable error classes may requeue.  ``attempt_count`` is the
        number of attempts already started (incremented by ``claim_job``), so
        the bounded retry check prevents an infinite loop while retaining the
        first three attempts by default.
        """

        normalized_error = _stored_error_class(error_class)
        # Validate the class before touching the row, including spelling.
        coerce_error_class(normalized_error)
        return self.transition_job(
            job_id,
            JobStatus.PENDING,
            error_class=normalized_error,
            last_error=last_error,
            max_attempts=max_attempts,
        )

    def fail_job(
        self,
        job_id: str,
        error_class: str | None = None,
        last_error: str | None = None,
        *,
        error: str | BaseException | None = None,
    ) -> Job:
        if error is not None and last_error is None:
            last_error = str(error)
        return self.transition_job(
            job_id,
            JobStatus.FAILED,
            error_class=error_class,
            last_error=last_error,
        )

    # ------------------------------------------------------------------
    # Source identity and versions
    # ------------------------------------------------------------------
    def get_source_file(self, source_file_id: str) -> SourceFile | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()
            return None if row is None else _source_file_from_row(row)

    def register_source_file(
        self,
        source_file_id: str | SourceFile | None = None,
        provider: str | None = None,
        provider_file_id: str | None = None,
        course_key: str | None = None,
        source_kind: str | None = None,
        original_filename: str | None = None,
        current_hash: str | None = None,
        *,
        canonical_entity_id: str | None = None,
        first_seen_at: str | None = None,
        last_seen_at: str | None = None,
        source_file: SourceFile | None = None,
    ) -> SourceFile:
        if source_file is not None:
            if source_file_id is not None:
                raise TypeError("pass either source_file or source_file_id, not both")
            source_file_id = source_file
        if isinstance(source_file_id, SourceFile):
            supplied = source_file_id
            source_file_id = supplied.source_file_id
            provider = supplied.provider
            provider_file_id = supplied.provider_file_id
            course_key = supplied.course_key
            source_kind = supplied.source_kind
            original_filename = supplied.original_filename
            current_hash = supplied.current_hash
            canonical_entity_id = supplied.canonical_entity_id
            first_seen_at = supplied.first_seen_at or first_seen_at
            last_seen_at = supplied.last_seen_at or last_seen_at
        if source_file_id is None and provider and provider_file_id:
            # Provider/file identity is deterministic and safe as a fallback
            # when an adapter did not precompute a source_file_id.
            source_file_id = f"{provider}:{provider_file_id}"
        for value, name in (
            (source_file_id, "source_file_id"),
            (provider, "provider"),
            (provider_file_id, "provider_file_id"),
            (course_key, "course_key"),
            (source_kind, "source_kind"),
        ):
            _require_text(value, name)
        first_seen = first_seen_at if first_seen_at is not None else _utc_now()
        last_seen = last_seen_at if last_seen_at is not None else first_seen

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT * FROM source_files
                    WHERE provider = ? AND provider_file_id = ?
                    """,
                    (provider, provider_file_id),
                ).fetchone()
            if row is not None:
                existing_id = row["source_file_id"]
                connection.execute(
                    """
                    UPDATE source_files
                    SET original_filename = COALESCE(?, original_filename),
                        current_hash = COALESCE(?, current_hash),
                        canonical_entity_id = COALESCE(canonical_entity_id, ?),
                        last_seen_at = ?
                    WHERE source_file_id = ?
                    """,
                    (original_filename, current_hash, canonical_entity_id, last_seen, existing_id),
                )
                current_canonical = connection.execute(
                    "SELECT canonical_entity_id FROM source_files WHERE source_file_id = ?",
                    (existing_id,),
                ).fetchone()["canonical_entity_id"]
                if current_canonical is not None:
                    _seed_entity_allocation(connection, row["course_key"], current_canonical)
                return _source_file_from_row(
                    connection.execute(
                        "SELECT * FROM source_files WHERE source_file_id = ?", (existing_id,)
                    ).fetchone()
                )
            try:
                connection.execute(
                    """
                    INSERT INTO source_files(
                        source_file_id, provider, provider_file_id, course_key, source_kind,
                        original_filename, current_hash, canonical_entity_id,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_file_id,
                        provider,
                        provider_file_id,
                        course_key,
                        source_kind,
                        original_filename,
                        current_hash,
                        canonical_entity_id,
                        first_seen,
                        last_seen,
                    ),
                )
                if canonical_entity_id is not None:
                    _seed_entity_allocation(connection, course_key, canonical_entity_id)
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM source_files
                    WHERE source_file_id = ? OR (provider = ? AND provider_file_id = ?)
                    """,
                    (source_file_id, provider, provider_file_id),
                ).fetchone()
                if row is None:
                    raise
                return _source_file_from_row(row)
            return _source_file_from_row(
                connection.execute(
                    "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
                ).fetchone()
            )

    def register_source_version(
        self,
        source_file_id: str | SourceVersion | None = None,
        source_hash: str | None = None,
        canonical_entity_id: str | None = None,
        source_ref_json: Any = None,
        processor_version: str | None = None,
        version: int | None = None,
        *,
        source_version: SourceVersion | None = None,
        version_id: str | None = None,
        id: str | None = None,
        first_seen_at: str | None = None,
    ) -> SourceVersion:
        if id is not None:
            if version_id is not None:
                raise TypeError("pass either id or version_id, not both")
            version_id = id
        if source_version is not None:
            if source_file_id is not None:
                raise TypeError("pass either source_version or source_file_id, not both")
            source_file_id = source_version
        if isinstance(source_file_id, SourceVersion):
            supplied = source_file_id
            source_file_id = supplied.source_file_id
            source_hash = supplied.source_hash
            canonical_entity_id = supplied.canonical_entity_id
            source_ref_json = supplied.source_ref_json
            processor_version = supplied.processor_version
            version = supplied.version
            version_id = supplied.id
            first_seen_at = supplied.first_seen_at or first_seen_at
        _require_text(source_file_id, "source_file_id")
        _require_text(source_hash, "source_hash")
        source_ref = _json_text(source_ref_json)
        first_seen = first_seen_at if first_seen_at is not None else _utc_now()

        with self._transaction(immediate=True) as connection:
            source_row = connection.execute(
                "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()
            if source_row is None:
                raise KeyError(f"unknown source file: {source_file_id}")
            stored_canonical = source_row["canonical_entity_id"]
            if (
                stored_canonical is not None
                and canonical_entity_id is not None
                and canonical_entity_id != stored_canonical
            ):
                raise ValueError(
                    "canonical_entity_id does not match the source file's canonical entity"
                )
            existing = connection.execute(
                """
                SELECT * FROM source_versions
                WHERE source_file_id = ? AND source_hash = ?
                """,
                (source_file_id, source_hash),
            ).fetchone()
            if existing is not None:
                existing_canonical = existing["canonical_entity_id"]
                if stored_canonical is not None and existing_canonical != stored_canonical:
                    raise ValueError(
                        "existing source version does not match the source file's canonical entity"
                    )
                if stored_canonical is None:
                    if (
                        canonical_entity_id is not None
                        and canonical_entity_id != existing_canonical
                    ):
                        raise ValueError(
                            "canonical_entity_id does not match the existing source version"
                        )
                    connection.execute(
                        "UPDATE source_files SET canonical_entity_id = ? WHERE source_file_id = ?",
                        (existing_canonical, source_file_id),
                    )
                    _seed_entity_allocation(
                        connection, source_row["course_key"], existing_canonical
                    )
                return _source_version_from_row(existing)
            if canonical_entity_id is None:
                canonical_entity_id = stored_canonical
            _require_text(canonical_entity_id, "canonical_entity_id")
            if version is None:
                latest = connection.execute(
                    "SELECT MAX(version) AS version FROM source_versions WHERE source_file_id = ?",
                    (source_file_id,),
                ).fetchone()["version"]
                version = 1 if latest is None else int(latest) + 1
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("version must be a positive integer")
            identifier = version_id or _new_id("srcver_")
            try:
                connection.execute(
                    """
                    INSERT INTO source_versions(
                        id, source_file_id, source_hash, version, canonical_entity_id,
                        source_ref_json, first_seen_at, processor_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        source_file_id,
                        source_hash,
                        version,
                        canonical_entity_id,
                        source_ref,
                        first_seen,
                        processor_version,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    """
                    SELECT * FROM source_versions
                    WHERE source_file_id = ? AND source_hash = ?
                    """,
                    (source_file_id, source_hash),
                ).fetchone()
                if existing is None:
                    raise
                return _source_version_from_row(existing)
            connection.execute(
                "UPDATE source_files SET current_hash = ?, last_seen_at = ? WHERE source_file_id = ?",
                (source_hash, first_seen, source_file_id),
            )
            if stored_canonical is None:
                connection.execute(
                    "UPDATE source_files SET canonical_entity_id = ? WHERE source_file_id = ?",
                    (canonical_entity_id, source_file_id),
                )
                _seed_entity_allocation(connection, source_row["course_key"], canonical_entity_id)
            return _source_version_from_row(
                connection.execute("SELECT * FROM source_versions WHERE id = ?", (identifier,)).fetchone()
            )

    def find_source_versions(
        self,
        source_file_id: str | None = None,
        source_hash: str | None = None,
        *,
        canonical_entity_id: str | None = None,
    ) -> list[SourceVersion]:
        clauses: list[str] = []
        values: list[str] = []
        if source_file_id is not None:
            clauses.append("source_file_id = ?")
            values.append(source_file_id)
        if source_hash is not None:
            clauses.append("source_hash = ?")
            values.append(source_hash)
        if canonical_entity_id is not None:
            clauses.append("canonical_entity_id = ?")
            values.append(canonical_entity_id)
        query = "SELECT * FROM source_versions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY source_file_id ASC, version ASC"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
            return [_source_version_from_row(row) for row in rows]

    def find_processed_source(
        self,
        source_file_id: str | None = None,
        source_hash: str | None = None,
        operation: str | None = None,
        processor_version: str | None = None,
        *,
        input_hash: str | None = None,
    ) -> ProcessingRecord | None:
        """Return the latest successful processing record for a source.

        ``input_hash`` is preferred when present; the joined job hash is used
        as a compatibility fallback for records created without it.
        """

        if source_hash is None:
            source_hash = input_hash
        clauses = ["pr.status IN (?, ?)"]
        values: list[Any] = [JobStatus.READY.value, JobStatus.PARTIAL.value]
        if source_file_id is not None:
            clauses.insert(0, "j.source_file_id = ?")
            values.insert(0, source_file_id)
        if source_hash is not None:
            clauses.append("(pr.input_hash = ? OR j.source_hash = ?)")
            values.extend([source_hash, source_hash])
        if operation is not None:
            clauses.append("pr.operation = ?")
            values.append(operation)
        if processor_version is not None:
            clauses.append("pr.processor_version = ?")
            values.append(processor_version)
        query = f"""
            SELECT pr.*
            FROM processing_records AS pr
            JOIN jobs AS j ON j.id = pr.job_id
            WHERE {' AND '.join(clauses)}
            ORDER BY pr.finished_at DESC, pr.started_at DESC, pr.id DESC
            LIMIT 1
        """
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
            return None if row is None else _processing_record_from_row(row)

    def create_processing_record(
        self,
        record: ProcessingRecord | None = None,
        *,
        record_id: str | None = None,
        job_id: str | None = None,
        operation: str | None = None,
        processor_version: str | None = None,
        input_hash: str | None = None,
        output_ref_json: Any = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        status: JobStatus | str = JobStatus.PROCESSING,
    ) -> ProcessingRecord:
        if record is not None:
            if any(value is not None for value in (record_id, job_id, operation, processor_version)):
                raise TypeError("pass either record or processing-record fields, not both")
            record_id = record.id
            job_id = record.job_id
            operation = record.operation
            processor_version = record.processor_version
            input_hash = record.input_hash
            output_ref_json = record.output_ref_json
            started_at = record.started_at
            finished_at = record.finished_at
            status = record.status
        for value, name in (
            (job_id, "job_id"),
            (operation, "operation"),
            (processor_version, "processor_version"),
        ):
            _require_text(value, name)
        identifier = record_id or _new_id("proc_")
        started = started_at if started_at is not None else _utc_now()
        normalized_status = to_processing_status(status)
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO processing_records(
                    id, job_id, operation, processor_version, input_hash,
                    output_ref_json, started_at, finished_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    job_id,
                    operation,
                    processor_version,
                    input_hash,
                    _json_text(output_ref_json) if output_ref_json is not None else None,
                    started,
                    finished_at,
                    normalized_status.value,
                ),
            )
            return _processing_record_from_row(
                connection.execute(
                    "SELECT * FROM processing_records WHERE id = ?", (identifier,)
                ).fetchone()
            )

    def get_processing_record(
        self, job_id: str, *, operation: str | None = None
    ) -> ProcessingRecord | None:
        """Read one job's durable processing result without provider access."""

        _require_text(job_id, "job_id")
        query = "SELECT * FROM processing_records WHERE job_id = ?"
        values: list[Any] = [job_id]
        if operation is not None:
            _require_text(operation, "operation")
            query += " AND operation = ?"
            values.append(operation)
        query += " ORDER BY started_at DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
            return None if row is None else _processing_record_from_row(row)

    # ------------------------------------------------------------------
    # Checkpoints and entity allocation
    # ------------------------------------------------------------------
    def get_checkpoint(self, provider: str, scope: str) -> Checkpoint | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM checkpoints WHERE provider = ? AND scope = ?",
                (provider, scope),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    def set_checkpoint(self, provider: str, scope: str, value: str) -> Checkpoint:
        _require_text(provider, "provider")
        _require_text(scope, "scope")
        _require_text(value, "value")
        checkpoint = Checkpoint(provider=provider, scope=scope, checkpoint_value=value, updated_at=_utc_now())
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(provider, scope, checkpoint_value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, scope) DO UPDATE SET
                    checkpoint_value = excluded.checkpoint_value,
                    updated_at = excluded.updated_at
                """,
                (provider, scope, value, checkpoint.updated_at),
            )
            return _checkpoint_from_row(
                connection.execute(
                    "SELECT * FROM checkpoints WHERE provider = ? AND scope = ?",
                    (provider, scope),
                ).fetchone()
            )

    def allocate_entity(self, course_key: str, entity_type: str, source_file_id: str) -> str:
        """Allocate once per source identity using the §8.6.1 algorithm."""

        parsed_course = parse_course_key(course_key)
        if not isinstance(entity_type, str) or len(entity_type) != 1:
            raise ValueError("entity_type must be a single character")
        normalized_type = entity_type.upper()
        if not normalized_type.isalpha() or not normalized_type.isascii():
            raise ValueError("entity_type must be one ASCII alphabetic character")
        _require_text(source_file_id, "source_file_id")

        with self._transaction(immediate=True) as connection:
            source = connection.execute(
                "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()
            if source is None:
                raise KeyError(f"unknown source file: {source_file_id}")
            if source["course_key"] != course_key:
                raise ValueError("course_key does not match the source file")
            if source["canonical_entity_id"] is not None:
                return source["canonical_entity_id"]

            allocation = connection.execute(
                """
                SELECT * FROM entity_allocations
                WHERE course_key = ? AND entity_type = ?
                """,
                (course_key, normalized_type),
            ).fetchone()
            if allocation is None:
                connection.execute(
                    """
                    INSERT INTO entity_allocations(course_key, entity_type, next_sequence)
                    VALUES (?, ?, 1)
                    """,
                    (course_key, normalized_type),
                )
                sequence = 1
            else:
                sequence = int(allocation["next_sequence"])
            if sequence < 1:
                raise ValueError("entity allocation sequence must start at one")
            if sequence > 99:
                raise ValueError("entity sequence exhausted for two-digit ID format")
            new_entity_id = f"{parsed_course.code}-{normalized_type}{sequence:02d}"

            cursor = connection.execute(
                """
                UPDATE source_files
                SET canonical_entity_id = ?
                WHERE source_file_id = ? AND canonical_entity_id IS NULL
                """,
                (new_entity_id, source_file_id),
            )
            if cursor.rowcount != 1:
                existing = connection.execute(
                    "SELECT canonical_entity_id FROM source_files WHERE source_file_id = ?",
                    (source_file_id,),
                ).fetchone()
                if existing is None or existing["canonical_entity_id"] is None:
                    raise RuntimeError("source entity allocation lost without a stored ID")
                return existing["canonical_entity_id"]

            connection.execute(
                """
                UPDATE entity_allocations
                SET next_sequence = ?
                WHERE course_key = ? AND entity_type = ?
                """,
                (sequence + 1, course_key, normalized_type),
            )
            return new_entity_id

    # ------------------------------------------------------------------
    # Local worker lock delegation
    # ------------------------------------------------------------------
    def acquire_local_worker_lock(self, timeout: float | None = 0.0) -> bool:
        return self._worker_lock.acquire(timeout=timeout)

    def release_local_worker_lock(self) -> None:
        self._worker_lock.release()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _canonical_operation(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    _require_text(value, "operation")
    if value != value.strip():
        raise ValueError("operation must use its canonical spelling")
    return value


def _validate_job_key(value: Any) -> None:
    if not isinstance(value, str) or _JOB_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError("job_key must match sha256:<64 lowercase hexadecimal characters>")


def _stored_error_class(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("error_class must be a non-empty string")
    return value.strip().upper()


def _validate_max_attempts(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("max_attempts must be a positive integer")


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise TypeError("source_ref_json must be JSON serializable") from exc


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(**dict(row))


def _source_file_from_row(row: sqlite3.Row) -> SourceFile:
    return SourceFile(**dict(row))


def _source_version_from_row(row: sqlite3.Row) -> SourceVersion:
    return SourceVersion(**dict(row))


def _processing_record_from_row(row: sqlite3.Row) -> ProcessingRecord:
    return ProcessingRecord(**dict(row))


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(**dict(row))


def _entity_allocation_from_row(row: sqlite3.Row) -> EntityAllocation:
    return EntityAllocation(**dict(row))


def _semester_registration_from_row(row: sqlite3.Row) -> SemesterRegistration:
    return SemesterRegistration(**dict(row))


def _intake_item_from_row(row: sqlite3.Row) -> IntakeItem:
    return IntakeItem(**dict(row))


def _intake_observation_from_row(row: sqlite3.Row) -> IntakeObservation:
    return IntakeObservation(**dict(row))


def _request_receipt_from_row(row: sqlite3.Row) -> RequestReceipt:
    return RequestReceipt(**dict(row))


def _intake_plan_from_row(row: sqlite3.Row) -> IntakePlan:
    return IntakePlan(**dict(row))


def _provider_write_attempt_from_row(row: sqlite3.Row) -> ProviderWriteAttempt:
    return ProviderWriteAttempt(**dict(row))


def _entity_reservation_from_row(row: sqlite3.Row) -> EntityReservation:
    return EntityReservation(**dict(row))


def _session_source_binding_from_row(row: sqlite3.Row) -> SessionSourceBinding:
    return SessionSourceBinding(**dict(row))


def _seed_entity_allocation(
    connection: sqlite3.Connection,
    course_key: str,
    canonical_entity_id: str,
) -> None:
    """Reserve sequence space when importing an already-canonical source."""

    parsed_course = parse_course_key(course_key)
    parsed_entity = parse_entity_id(canonical_entity_id)
    if parsed_entity.course_code != parsed_course.code:
        raise ValueError("canonical_entity_id does not belong to course_key")
    next_sequence = max(1, parsed_entity.sequence + 1)
    connection.execute(
        """
        INSERT INTO entity_allocations(course_key, entity_type, next_sequence)
        VALUES (?, ?, ?)
        ON CONFLICT(course_key, entity_type) DO UPDATE SET
            next_sequence = MAX(entity_allocations.next_sequence, excluded.next_sequence)
        """,
        (course_key, parsed_entity.entity_type, next_sequence),
    )


__all__ = ["SQLiteStateStore"]
