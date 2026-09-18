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
from uls.intake.identity import canonical_json, derive_study_note_key
from uls.orchestration.jobs import derive_job_key
from uls.orchestration.locks import LocalWorkerLock
from uls.orchestration.retry import (
    DEFAULT_MAX_ATTEMPTS,
    coerce_error_class,
    should_retry,
)

from .models import (
    Checkpoint,
    EntityAllocation,
    EntityReservation,
    IntakeItem,
    IntakeObservation,
    IntakePlan,
    Job,
    NoteArtifact,
    NoteAttempt,
    NoteJob,
    NoteRequestReference,
    ProcessingRecord,
    ProviderWriteAttempt,
    RequestReceipt,
    SemesterRegistration,
    SessionSourceBinding,
    SourceFile,
    SourceVersion,
    StudyNoteHead,
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

_RESERVATION_STATES = frozenset(
    {"PENDING", "APPLIED", "RECONCILE_REQUIRED", "RELEASED"}
)

class _Unset:
    """Sentinel distinguishing an omitted guard argument from an explicit None."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<unset>"


_UNSET: Any = _Unset()
_RESERVATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"APPLIED", "RECONCILE_REQUIRED", "RELEASED"}),
    "RECONCILE_REQUIRED": frozenset({"APPLIED", "RELEASED"}),
    "APPLIED": frozenset(),
    "RELEASED": frozenset(),
}
_RESERVATION_RECONCILED_STAGE = "RECONCILED"
_RESERVATION_NO_MUTATION_RESPONSE = "RECONCILED_NO_MUTATION"

_NOTE_ACTIVE_STATES = frozenset(
    {"REQUESTED", "WAITING_CONTEXT", "GENERATING", "STAGED", "PUBLISHING"}
)
_NOTE_TERMINAL_STATES = frozenset({"READY", "PARTIAL", "FAILED", "CANCELLED", "STALE"})
_NOTE_STATES = _NOTE_ACTIVE_STATES | _NOTE_TERMINAL_STATES
_NOTE_TRANSITIONS: dict[str, frozenset[str]] = {
    "REQUESTED": frozenset({"WAITING_CONTEXT", "GENERATING", "FAILED", "CANCELLED", "STALE"}),
    "WAITING_CONTEXT": frozenset({"GENERATING", "FAILED", "CANCELLED", "STALE"}),
    "GENERATING": frozenset({"STAGED", "FAILED", "CANCELLED", "STALE"}),
    "STAGED": frozenset({"PUBLISHING", "FAILED", "CANCELLED", "STALE"}),
    "PUBLISHING": frozenset({"READY", "PARTIAL", "FAILED", "CANCELLED", "STALE"}),
    "READY": frozenset(),
    "PARTIAL": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "STALE": frozenset(),
}

_REFERENCE_STATES = frozenset({"ACTIVE", "CANCEL_REQUESTED", "CANCELLED", "SUPERSEDED"})
_REFERENCE_TRANSITIONS: dict[str, frozenset[str]] = {
    "ACTIVE": frozenset({"CANCEL_REQUESTED", "CANCELLED", "SUPERSEDED"}),
    "CANCEL_REQUESTED": frozenset({"ACTIVE", "CANCELLED", "SUPERSEDED"}),
    "CANCELLED": frozenset(),
    "SUPERSEDED": frozenset(),
}

_ARTIFACT_STATES = frozenset({"STAGED", "VERIFIED", "PUBLISHED", "STALE"})
_ARTIFACT_TRANSITIONS: dict[str, frozenset[str]] = {
    "STAGED": frozenset({"VERIFIED", "STALE"}),
    "VERIFIED": frozenset({"PUBLISHED", "STALE"}),
    "PUBLISHED": frozenset({"STALE"}),
    "STALE": frozenset(),
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
    reservation_id TEXT,
    stage TEXT,
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
    state TEXT NOT NULL CHECK(state IN ('PENDING','APPLIED','RECONCILE_REQUIRED','RELEASED')),
    plan_revision TEXT NOT NULL,
    source_file_id TEXT,
    receipt_id TEXT,
    plan_hash TEXT,
    source_snapshot_hash TEXT,
    target_snapshot_hash TEXT,
    operation_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    released_at TEXT,
    UNIQUE(entity_kind, entity_app_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_reservations_active_intake_kind
    ON entity_reservations(intake_id, entity_kind)
    WHERE state <> 'RELEASED';
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
CREATE TABLE IF NOT EXISTS study_note_heads (
    provider TEXT NOT NULL,
    session_provider_page_id TEXT NOT NULL,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    current_request_id TEXT NOT NULL,
    current_receipt_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 1),
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    inactive_reason TEXT,
    evidence_mode TEXT,
    selected_materials_json TEXT NOT NULL DEFAULT '[]',
    receipt_hash TEXT NOT NULL,
    current_note_key TEXT,
    current_attempt_no INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider, session_provider_page_id)
);
CREATE TABLE IF NOT EXISTS note_jobs (
    note_key TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    learner_request_hash TEXT NOT NULL,
    template_version TEXT NOT NULL,
    generator_config_version TEXT NOT NULL,
    current_attempt_no INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_attempts (
    note_key TEXT NOT NULL REFERENCES note_jobs(note_key),
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    state TEXT NOT NULL CHECK(state IN (
        'REQUESTED','WAITING_CONTEXT','GENERATING','STAGED','PUBLISHING',
        'READY','PARTIAL','FAILED','CANCELLED','STALE'
    )),
    seed_artifact_id TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    next_retry_at TEXT,
    last_successful_stage TEXT,
    error_class TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    PRIMARY KEY(note_key, attempt_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_note_attempts_one_active
    ON note_attempts(note_key)
    WHERE state IN ('REQUESTED','WAITING_CONTEXT','GENERATING','STAGED','PUBLISHING');
CREATE TABLE IF NOT EXISTS note_request_references (
    reference_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    provider_request_id TEXT NOT NULL,
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    head_generation INTEGER NOT NULL CHECK(head_generation >= 1),
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','CANCEL_REQUESTED','CANCELLED','SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY(note_key, attempt_no) REFERENCES note_attempts(note_key, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_note_request_references_attempt
    ON note_request_references(note_key, attempt_no, state);
CREATE TABLE IF NOT EXISTS note_artifacts (
    artifact_id TEXT PRIMARY KEY,
    note_key TEXT NOT NULL REFERENCES note_jobs(note_key),
    output_identity TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    writer_version TEXT NOT NULL,
    ai_region_id TEXT,
    ai_block_ids_json TEXT NOT NULL DEFAULT '[]',
    last_publish_hash TEXT,
    state TEXT NOT NULL CHECK(state IN ('STAGED','VERIFIED','PUBLISHED','STALE')),
    created_at TEXT NOT NULL,
    verified_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(note_key, output_hash, manifest_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_note_artifacts_one_reusable
    ON note_artifacts(note_key)
    WHERE state IN ('VERIFIED','PUBLISHED');
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
            write_attempt_columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(provider_write_attempts)"
                ).fetchall()
            }
            if "reservation_id" not in write_attempt_columns:
                self._connection.execute(
                    "ALTER TABLE provider_write_attempts ADD COLUMN reservation_id TEXT"
                )
            if "stage" not in write_attempt_columns:
                self._connection.execute(
                    "ALTER TABLE provider_write_attempts ADD COLUMN stage TEXT"
                )
            self._migrate_c1_entity_reservations()

    def _migrate_c1_entity_reservations(self) -> None:
        """Upgrade the preview reservation table without losing released history."""

        columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(entity_reservations)"
            ).fetchall()
        }
        required_columns = {
            "receipt_id",
            "plan_hash",
            "source_snapshot_hash",
            "target_snapshot_hash",
            "operation_key",
            "updated_at",
            "released_at",
        }
        has_legacy_active_unique = False
        for index_row in self._connection.execute(
            "PRAGMA index_list(entity_reservations)"
        ).fetchall():
            if not index_row[2] or index_row[4]:
                continue
            index_name = index_row[1]
            indexed_columns = [
                row[2]
                for row in self._connection.execute(
                    f"PRAGMA index_info('{index_name}')"
                ).fetchall()
            ]
            if indexed_columns == ["intake_id", "entity_kind"]:
                has_legacy_active_unique = True
                break
        if required_columns.issubset(columns) and not has_legacy_active_unique:
            return

        allowed_states = {"PENDING", "APPLIED", "RECONCILE_REQUIRED", "RELEASED"}
        current_states = {
            row[0]
            for row in self._connection.execute(
                "SELECT DISTINCT state FROM entity_reservations"
            ).fetchall()
        }
        if current_states - allowed_states:
            raise ValueError("entity reservation migration found an unsupported state")

        # Rebuild under a temporary table name instead of renaming the legacy
        # table.  Modern SQLite rewrites child FK targets on ALTER TABLE
        # RENAME, which would otherwise strand session_source_bindings on the
        # temporary legacy table after it is dropped.
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._transaction(immediate=True) as connection:
                existing_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(entity_reservations)"
                    ).fetchall()
                }
                connection.execute(
                    """
                    CREATE TABLE entity_reservations_c1_new (
                    reservation_id TEXT PRIMARY KEY,
                    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
                    entity_kind TEXT NOT NULL,
                    entity_app_id TEXT NOT NULL,
                    parent_folder_id TEXT NOT NULL,
                    marker_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'PENDING','APPLIED','RECONCILE_REQUIRED','RELEASED'
                    )),
                    plan_revision TEXT NOT NULL,
                    source_file_id TEXT,
                    receipt_id TEXT,
                    plan_hash TEXT,
                    source_snapshot_hash TEXT,
                    target_snapshot_hash TEXT,
                    operation_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    released_at TEXT,
                    UNIQUE(entity_kind, entity_app_id)
                )
                """
                )
                target_columns = (
                    "reservation_id",
                    "intake_id",
                    "entity_kind",
                    "entity_app_id",
                    "parent_folder_id",
                    "marker_key",
                    "state",
                    "plan_revision",
                    "source_file_id",
                    "receipt_id",
                    "plan_hash",
                    "source_snapshot_hash",
                    "target_snapshot_hash",
                    "operation_key",
                    "created_at",
                    "updated_at",
                    "released_at",
                )
                select_expressions: list[str] = []
                for column in target_columns:
                    if column in existing_columns:
                        select_expressions.append(column)
                    elif column == "updated_at":
                        select_expressions.append("created_at")
                    elif column == "released_at":
                        select_expressions.append(
                            "CASE WHEN state='RELEASED' THEN created_at ELSE NULL END"
                        )
                    else:
                        select_expressions.append("NULL")
                connection.execute(
                    f"""
                    INSERT INTO entity_reservations_c1_new({', '.join(target_columns)})
                    SELECT {', '.join(select_expressions)}
                    FROM entity_reservations
                    """
                )
                connection.execute("DROP TABLE entity_reservations")
                connection.execute(
                    "ALTER TABLE entity_reservations_c1_new RENAME TO entity_reservations"
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX idx_entity_reservations_active_intake_kind
                    ON entity_reservations(intake_id, entity_kind)
                    WHERE state <> 'RELEASED'
                    """
                )
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

        fk_targets = {
            row[2]
            for row in self._connection.execute(
                "PRAGMA foreign_key_list(session_source_bindings)"
            ).fetchall()
        }
        if fk_targets and "entity_reservations" not in fk_targets:
            raise RuntimeError("C1 migration changed the reservation foreign-key target")
        violation = self._connection.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise RuntimeError("C1 reservation migration introduced a foreign-key violation")

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
        values.setdefault("reservation_id", None)
        values.setdefault("stage", None)
        values.setdefault("dispatched_at", None)
        values.setdefault("response_state", "PREPARED")
        values.setdefault("readback_json", None)
        values.setdefault("error_class", None)
        if (
            values.get("stage") == _RESERVATION_RECONCILED_STAGE
            or values.get("response_state") == _RESERVATION_NO_MUTATION_RESPONSE
        ):
            raise ValueError(
                "reconciliation markers require the dedicated reservation reconciliation API"
            )
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
                    prewrite_committed_at, reservation_id, stage, dispatched_at,
                    response_state, readback_json, error_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "attempt_id", "operation", "operation_key", "provider", "target_id",
                    "prewrite_committed_at", "reservation_id", "stage", "dispatched_at",
                    "response_state", "readback_json", "error_class",
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
        allowed = {
            "target_id",
            "reservation_id",
            "stage",
            "dispatched_at",
            "response_state",
            "readback_json",
            "error_class",
        }
        if set(patch) - allowed:
            raise ValueError("unknown provider write attempt fields")
        if (
            patch.get("stage") == _RESERVATION_RECONCILED_STAGE
            or patch.get("response_state") == _RESERVATION_NO_MUTATION_RESPONSE
        ):
            raise ValueError(
                "reconciliation markers require the dedicated reservation reconciliation API"
            )
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

    def record_reservation_no_mutation_reconciliation(
        self,
        reservation_id: str,
        *,
        operation_key: str,
        verified_readback: Any,
    ) -> ProviderWriteAttempt:
        _require_text(reservation_id, "reservation_id")
        _require_text(operation_key, "operation_key")
        if verified_readback is None:
            raise ValueError("verified reconciliation readback is required")
        readback_json = _json_text(verified_readback)
        with self._transaction(immediate=True) as connection:
            reservation = connection.execute(
                "SELECT * FROM entity_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation["state"] != "RECONCILE_REQUIRED":
                raise ValueError("reservation reconciliation requires RECONCILE_REQUIRED state")
            attempt = connection.execute(
                "SELECT * FROM provider_write_attempts WHERE operation_key=? AND reservation_id=?",
                (operation_key, reservation_id),
            ).fetchone()
            if attempt is None:
                raise ValueError("reconciliation write attempt is not linked to reservation")
            connection.execute(
                "UPDATE provider_write_attempts SET stage=?, response_state=?, readback_json=? WHERE attempt_id=?",
                (
                    _RESERVATION_RECONCILED_STAGE,
                    _RESERVATION_NO_MUTATION_RESPONSE,
                    readback_json,
                    attempt["attempt_id"],
                ),
            )
            return _provider_write_attempt_from_row(
                connection.execute(
                    "SELECT * FROM provider_write_attempts WHERE attempt_id=?",
                    (attempt["attempt_id"],),
                ).fetchone()
            )

    def reserve_entity(
        self, reservation: EntityReservation | None = None, **kwargs: Any
    ) -> EntityReservation:
        values = dict(reservation.__dict__) if reservation is not None else dict(kwargs)
        values.setdefault("reservation_id", _new_id("reserve_"))
        values.setdefault("source_file_id", None)
        values.setdefault("receipt_id", None)
        values.setdefault("plan_hash", None)
        values.setdefault("source_snapshot_hash", None)
        values.setdefault("target_snapshot_hash", None)
        values.setdefault("operation_key", None)
        values.setdefault("created_at", _utc_now())
        values.setdefault("updated_at", values["created_at"])
        values.setdefault("released_at", None)
        for name in (
            "reservation_id", "intake_id", "entity_kind", "entity_app_id",
            "parent_folder_id", "marker_key", "state", "plan_revision",
        ):
            _require_text(values.get(name), name)
        if values["state"] not in _RESERVATION_STATES:
            raise ValueError("unsupported entity reservation state")
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM entity_reservations
                WHERE intake_id=? AND entity_kind=? AND state <> 'RELEASED'
                """,
                (values["intake_id"], values["entity_kind"]),
            ).fetchone()
            if existing is not None:
                identity_fields = (
                    "reservation_id",
                    "entity_app_id",
                    "parent_folder_id",
                    "marker_key",
                    "plan_revision",
                    "source_file_id",
                )
                if any(existing[name] != values[name] for name in identity_fields):
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
                    source_file_id, receipt_id, plan_hash, source_snapshot_hash,
                    target_snapshot_hash, operation_key, created_at, updated_at,
                    released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values.get(name) for name in (
                    "reservation_id", "intake_id", "entity_kind", "entity_app_id",
                    "parent_folder_id", "marker_key", "state", "plan_revision",
                    "source_file_id", "receipt_id", "plan_hash", "source_snapshot_hash",
                    "target_snapshot_hash", "operation_key", "created_at", "updated_at",
                    "released_at",
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
        query: str
        values: tuple[Any, ...]
        if reservation_id is not None:
            query, values = "SELECT * FROM entity_reservations WHERE reservation_id=?", (reservation_id,)
        else:
            query, values = (
                """
                SELECT * FROM entity_reservations
                WHERE intake_id=? AND entity_kind=? AND state <> 'RELEASED'
                """,
                (intake_id, entity_kind),
            )
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
            return None if row is None else _entity_reservation_from_row(row)

    def update_entity_reservation(self, reservation_id: str, **patch: Any) -> EntityReservation:
        allowed = {"state"}
        if set(patch) - allowed:
            raise ValueError("entity reservation identity is immutable")
        if not patch:
            row = self.get_entity_reservation(reservation_id)
            if row is None:
                raise KeyError(reservation_id)
            return row
        desired = patch["state"]
        if desired not in _RESERVATION_STATES:
            raise ValueError("unsupported entity reservation state")
        if desired == "RELEASED":
            raise ValueError("RELEASED requires release_pending_entity_reservation")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM entity_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            current = row["state"]
            if current == desired:
                return _entity_reservation_from_row(row)
            if desired not in _RESERVATION_TRANSITIONS[current]:
                raise ValueError(f"invalid entity reservation transition: {current} -> {desired}")
            if current == "RECONCILE_REQUIRED" and desired == "APPLIED":
                attempts = connection.execute(
                    """
                    SELECT reservation_id, response_state, readback_json
                    FROM provider_write_attempts
                    WHERE reservation_id=? OR operation_key=?
                    ORDER BY attempt_id
                    """,
                    (reservation_id, row["operation_key"]),
                ).fetchall()
                if not attempts or any(
                    attempt["reservation_id"] != reservation_id
                    or attempt["response_state"] != "READBACK_OK"
                    or attempt["readback_json"] is None
                    for attempt in attempts
                ):
                    raise ValueError(
                        "RECONCILE_REQUIRED -> APPLIED requires verified linked readback evidence"
                    )
            connection.execute(
                """
                UPDATE entity_reservations
                SET state=?, updated_at=?
                WHERE reservation_id=?
                """,
                (desired, _utc_now(), reservation_id),
            )
            return _entity_reservation_from_row(
                connection.execute(
                    "SELECT * FROM entity_reservations WHERE reservation_id=?", (reservation_id,)
                ).fetchone()
            )

    def release_pending_entity_reservation(
        self,
        reservation_id: str,
        replacement: EntityReservation | None = None,
        **kwargs: Any,
    ) -> EntityReservation:
        """Release one provably unmutated reservation and atomically replace it."""

        values = dict(replacement.__dict__) if replacement is not None else dict(kwargs)
        now = _utc_now()
        values.setdefault("reservation_id", _new_id("reserve_"))
        values.setdefault("state", "PENDING")
        values.setdefault("receipt_id", None)
        values.setdefault("plan_hash", None)
        values.setdefault("source_snapshot_hash", None)
        values.setdefault("target_snapshot_hash", None)
        values.setdefault("operation_key", None)
        values.setdefault("created_at", now)
        values.setdefault("updated_at", values["created_at"])
        values.setdefault("released_at", None)
        for name in (
            "reservation_id",
            "intake_id",
            "entity_kind",
            "entity_app_id",
            "parent_folder_id",
            "marker_key",
            "plan_revision",
        ):
            _require_text(values.get(name), name)
        if values["state"] != "PENDING":
            raise ValueError("replacement reservation must start PENDING")

        with self._transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM entity_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if current is None:
                raise KeyError(reservation_id)
            current_state = current["state"]
            if current_state not in {"PENDING", "RECONCILE_REQUIRED"}:
                raise ValueError(
                    "only a PENDING or RECONCILE_REQUIRED reservation can be safely released"
                )
            for field in (
                "receipt_id",
                "plan_hash",
                "source_snapshot_hash",
                "target_snapshot_hash",
                "operation_key",
            ):
                _require_text(current[field], f"current reservation {field}")
            if (
                values["intake_id"] != current["intake_id"]
                or values["entity_kind"] != current["entity_kind"]
                or values.get("source_file_id") != current["source_file_id"]
            ):
                raise ValueError("replacement must preserve intake, entity kind, and source identity")
            if values["reservation_id"] == current["reservation_id"]:
                raise ValueError("replacement must use a new reservation ID")
            attempts = connection.execute(
                """
                SELECT reservation_id, stage, response_state, readback_json
                FROM provider_write_attempts
                WHERE reservation_id=? OR operation_key=?
                ORDER BY attempt_id
                """,
                (reservation_id, current["operation_key"]),
            ).fetchall()
            if current_state == "PENDING":
                if attempts:
                    raise ValueError(
                        "provider mutation attempt exists; reservation requires reconciliation"
                    )
            else:
                if not attempts:
                    raise ValueError(
                        "durable no-mutation reconciliation proof is required before release"
                    )
                if any(
                    attempt["reservation_id"] != reservation_id
                    or attempt["stage"] != _RESERVATION_RECONCILED_STAGE
                    or attempt["response_state"] != _RESERVATION_NO_MUTATION_RESPONSE
                    or attempt["readback_json"] is None
                    for attempt in attempts
                ):
                    raise ValueError(
                        "all reservation write attempts require verified no-mutation reconciliation"
                    )
            collision = connection.execute(
                """
                SELECT 1 FROM entity_reservations
                WHERE entity_kind=? AND entity_app_id=?
                LIMIT 1
                """,
                (values["entity_kind"], values["entity_app_id"]),
            ).fetchone()
            if collision is not None:
                raise ValueError("entity app ID has already been consumed")

            binding = connection.execute(
                "SELECT * FROM session_source_bindings WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if binding is not None and binding["state"] != "PENDING":
                raise ValueError("applied source binding prevents reservation replacement")

            connection.execute(
                """
                UPDATE entity_reservations
                SET state='RELEASED', updated_at=?, released_at=?
                WHERE reservation_id=?
                """,
                (now, now, reservation_id),
            )
            connection.execute(
                """
                INSERT INTO entity_reservations(
                    reservation_id, intake_id, entity_kind, entity_app_id,
                    parent_folder_id, marker_key, state, plan_revision,
                    source_file_id, receipt_id, plan_hash, source_snapshot_hash,
                    target_snapshot_hash, operation_key, created_at, updated_at,
                    released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    values.get(name)
                    for name in (
                        "reservation_id",
                        "intake_id",
                        "entity_kind",
                        "entity_app_id",
                        "parent_folder_id",
                        "marker_key",
                        "state",
                        "plan_revision",
                        "source_file_id",
                        "receipt_id",
                        "plan_hash",
                        "source_snapshot_hash",
                        "target_snapshot_hash",
                        "operation_key",
                        "created_at",
                        "updated_at",
                        "released_at",
                    )
                ),
            )
            if binding is not None:
                connection.execute(
                    """
                    UPDATE session_source_bindings
                    SET reservation_id=?
                    WHERE binding_id=? AND reservation_id=? AND state='PENDING'
                    """,
                    (values["reservation_id"], binding["binding_id"], reservation_id),
                )
            return _entity_reservation_from_row(
                connection.execute(
                    "SELECT * FROM entity_reservations WHERE reservation_id=?",
                    (values["reservation_id"],),
                ).fetchone()
            )

    def claim_study_note_head(
        self,
        *,
        provider: str,
        session_provider_page_id: str,
        course_key: str,
        session_id: str,
        request_id: str,
        receipt_id: str,
        receipt_hash: str,
        evidence_mode: str | None = None,
        selected_materials: Any = None,
    ) -> StudyNoteHead:
        """Claim the newest validated request for one provider Session page."""

        for value, name in (
            (provider, "provider"),
            (session_provider_page_id, "session_provider_page_id"),
            (course_key, "course_key"),
            (session_id, "session_id"),
            (request_id, "request_id"),
            (receipt_id, "receipt_id"),
            (receipt_hash, "receipt_hash"),
        ):
            _require_text(value, name)
        selected_materials_json = _canonical_json_field(
            [] if selected_materials is None else selected_materials,
            "selected_materials",
        )
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            current = connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            if current is not None and current["current_receipt_id"] == receipt_id:
                for column, expected in (
                    ("course_key", course_key),
                    ("session_id", session_id),
                    ("current_request_id", request_id),
                    ("receipt_hash", receipt_hash),
                    ("evidence_mode", evidence_mode),
                    ("selected_materials_json", selected_materials_json),
                ):
                    if current[column] != expected:
                        raise ValueError(
                            f"same study-note receipt has conflicting {column}"
                        )
                return _study_note_head_from_row(current)

            generation = 1 if current is None else int(current["generation"]) + 1
            if current is None:
                connection.execute(
                    """
                    INSERT INTO study_note_heads(
                        provider, session_provider_page_id, course_key, session_id,
                        current_request_id, current_receipt_id, generation, active,
                        inactive_reason, evidence_mode, selected_materials_json,
                        receipt_hash, current_note_key, current_attempt_no,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        provider,
                        session_provider_page_id,
                        course_key,
                        session_id,
                        request_id,
                        receipt_id,
                        generation,
                        evidence_mode,
                        selected_materials_json,
                        receipt_hash,
                        now,
                        now,
                    ),
                )
            else:
                if (
                    current["course_key"] != course_key
                    or current["session_id"] != session_id
                ):
                    raise ValueError("study-note head Session identity is immutable")
                connection.execute(
                    """
                    UPDATE study_note_heads
                    SET current_request_id=?, current_receipt_id=?, generation=?,
                        active=1, inactive_reason=NULL, evidence_mode=?,
                        selected_materials_json=?, receipt_hash=?,
                        current_note_key=NULL, current_attempt_no=NULL, updated_at=?
                    WHERE provider=? AND session_provider_page_id=?
                    """,
                    (
                        request_id,
                        receipt_id,
                        generation,
                        evidence_mode,
                        selected_materials_json,
                        receipt_hash,
                        now,
                        provider,
                        session_provider_page_id,
                    ),
                )
            row = connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            return _study_note_head_from_row(row)

    def get_study_note_head(
        self, provider: str, session_provider_page_id: str
    ) -> StudyNoteHead | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            return None if row is None else _study_note_head_from_row(row)

    def deactivate_study_note_head(
        self,
        *,
        provider: str,
        session_provider_page_id: str,
        expected_generation: int,
        expected_request_id: str,
        expected_receipt_id: str,
        expected_receipt_hash: str,
        reason: str,
        expected_note_key: str | None = _UNSET,
        expected_attempt_no: int | None = _UNSET,
    ) -> StudyNoteHead:
        """Deactivate only the exact current generation; stale events are harmless.

        expected_note_key and expected_attempt_no default to an unset sentinel that
        skips the check for backward compatibility. Passing an explicit value,
        including None, requires the head's current pointer to match exactly before
        deactivating; this closes the TOCTOU where a snapshot taken before an
        attach_note_request() call is still accepted after the head has since had a
        note_key/attempt_no attached (attach does not bump generation).
        """

        _require_text(expected_request_id, "expected_request_id")
        _require_text(expected_receipt_id, "expected_receipt_id")
        _require_text(expected_receipt_hash, "expected_receipt_hash")
        _require_text(reason, "reason")
        if isinstance(expected_generation, bool) or expected_generation < 1:
            raise ValueError("expected_generation must be a positive integer")
        check_note_key = expected_note_key is not _UNSET
        check_attempt_no = expected_attempt_no is not _UNSET
        if check_note_key and expected_note_key is not None:
            _require_text(expected_note_key, "expected_note_key")
        if (
            check_attempt_no
            and expected_attempt_no is not None
            and (isinstance(expected_attempt_no, bool) or expected_attempt_no < 1)
        ):
            raise ValueError("expected_attempt_no must be a positive integer")
        with self._transaction(immediate=True) as connection:
            current = connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            if current is None:
                raise KeyError((provider, session_provider_page_id))
            if (
                int(current["generation"]) != expected_generation
                or current["current_request_id"] != expected_request_id
                or current["current_receipt_id"] != expected_receipt_id
                or current["receipt_hash"] != expected_receipt_hash
                or (check_note_key and current["current_note_key"] != expected_note_key)
                or (
                    check_attempt_no
                    and current["current_attempt_no"] != expected_attempt_no
                )
            ):
                return _study_note_head_from_row(current)
            connection.execute(
                """
                UPDATE study_note_heads
                SET active=0, inactive_reason=?, updated_at=?
                WHERE provider=? AND session_provider_page_id=?
                  AND generation=? AND current_request_id=?
                  AND current_receipt_id=? AND receipt_hash=?
                  AND (? = 0 OR current_note_key IS ?)
                  AND (? = 0 OR current_attempt_no IS ?)
                """,
                (
                    reason,
                    _utc_now(),
                    provider,
                    session_provider_page_id,
                    expected_generation,
                    expected_request_id,
                    expected_receipt_id,
                    expected_receipt_hash,
                    1 if check_note_key else 0,
                    expected_note_key if check_note_key else None,
                    1 if check_attempt_no else 0,
                    expected_attempt_no if check_attempt_no else None,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            return _study_note_head_from_row(row)

    def ensure_note_job(
        self,
        *,
        course_key: str,
        session_id: str,
        evidence_manifest_hash: str,
        learner_request_hash: str,
        template_version: str,
        generator_config_version: str,
        note_key: str | None = None,
    ) -> NoteJob:
        expected_key = derive_study_note_key(
            course_key=course_key,
            session_id=session_id,
            evidence_manifest_hash=evidence_manifest_hash,
            learner_request_hash=learner_request_hash,
            template_version=template_version,
            generator_config_version=generator_config_version,
        )
        if note_key is not None and note_key != expected_key:
            raise ValueError("note_key does not match the canonical study-note identity")
        key = expected_key
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM note_jobs WHERE note_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                for column, expected in (
                    ("course_key", course_key),
                    ("session_id", session_id),
                    ("evidence_manifest_hash", evidence_manifest_hash),
                    ("learner_request_hash", learner_request_hash),
                    ("template_version", template_version),
                    ("generator_config_version", generator_config_version),
                ):
                    if existing[column] != expected:
                        raise ValueError(f"note job identity collision on {column}")
                return _note_job_from_row(existing)
            connection.execute(
                """
                INSERT INTO note_jobs(
                    note_key, course_key, session_id, evidence_manifest_hash,
                    learner_request_hash, template_version, generator_config_version,
                    current_attempt_no, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    key,
                    course_key,
                    session_id,
                    evidence_manifest_hash,
                    learner_request_hash,
                    template_version,
                    generator_config_version,
                    now,
                    now,
                ),
            )
            return _note_job_from_row(
                connection.execute(
                    "SELECT * FROM note_jobs WHERE note_key=?",
                    (key,),
                ).fetchone()
            )

    def get_note_job(self, note_key: str) -> NoteJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_jobs WHERE note_key=?",
                (note_key,),
            ).fetchone()
            return None if row is None else _note_job_from_row(row)

    def attach_note_request(
        self,
        *,
        provider: str,
        session_provider_page_id: str,
        head_generation: int,
        receipt_id: str,
        receipt_hash: str,
        provider_request_id: str,
        note_key: str,
        course_key: str,
        session_id: str,
        evidence_manifest_hash: str,
        learner_request_hash: str,
        template_version: str,
        generator_config_version: str,
    ) -> tuple[NoteAttempt, NoteRequestReference, bool]:
        """Attach one request to the shared active attempt or allocate the next attempt.

        The boolean is true only when this call created a new REQUESTED attempt that
        requires generation. Rediscovery and verified-artifact reuse return false.
        """

        for value, name in (
            (provider, "provider"),
            (session_provider_page_id, "session_provider_page_id"),
            (receipt_id, "receipt_id"),
            (receipt_hash, "receipt_hash"),
            (provider_request_id, "provider_request_id"),
            (note_key, "note_key"),
            (course_key, "course_key"),
            (session_id, "session_id"),
            (evidence_manifest_hash, "evidence_manifest_hash"),
            (learner_request_hash, "learner_request_hash"),
            (template_version, "template_version"),
            (generator_config_version, "generator_config_version"),
        ):
            _require_text(value, name)
        expected_note_key = derive_study_note_key(
            course_key=course_key,
            session_id=session_id,
            evidence_manifest_hash=evidence_manifest_hash,
            learner_request_hash=learner_request_hash,
            template_version=template_version,
            generator_config_version=generator_config_version,
        )
        if note_key != expected_note_key:
            raise ValueError("note_key does not match the canonical study-note identity")
        if isinstance(head_generation, bool) or head_generation < 1:
            raise ValueError("head_generation must be a positive integer")
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            existing_reference = connection.execute(
                "SELECT * FROM note_request_references WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            head = connection.execute(
                """
                SELECT * FROM study_note_heads
                WHERE provider=? AND session_provider_page_id=?
                """,
                (provider, session_provider_page_id),
            ).fetchone()
            if head is None and existing_reference is None:
                raise KeyError((provider, session_provider_page_id))
            if existing_reference is None and (
                not bool(head["active"])
                or int(head["generation"]) != head_generation
                or head["current_receipt_id"] != receipt_id
                or head["receipt_hash"] != receipt_hash
                or head["current_request_id"] != provider_request_id
            ):
                raise ValueError("study-note request is not the exact active head")
            job = connection.execute(
                "SELECT * FROM note_jobs WHERE note_key=?",
                (note_key,),
            ).fetchone()
            if job is None:
                connection.execute(
                    """
                    INSERT INTO note_jobs(
                        note_key, course_key, session_id, evidence_manifest_hash,
                        learner_request_hash, template_version, generator_config_version,
                        current_attempt_no, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        note_key,
                        course_key,
                        session_id,
                        evidence_manifest_hash,
                        learner_request_hash,
                        template_version,
                        generator_config_version,
                        now,
                        now,
                    ),
                )
                job = connection.execute(
                    "SELECT * FROM note_jobs WHERE note_key=?",
                    (note_key,),
                ).fetchone()
            if job is None:
                raise RuntimeError("note job was not created")
            if job["course_key"] != course_key or job["session_id"] != session_id:
                raise ValueError("note job identity collision")
            if job["evidence_manifest_hash"] != evidence_manifest_hash:
                raise ValueError("note job identity collision")
            if job["learner_request_hash"] != learner_request_hash:
                raise ValueError("note job identity collision")
            if job["template_version"] != template_version:
                raise ValueError("note job identity collision")
            if job["generator_config_version"] != generator_config_version:
                raise ValueError("note job identity collision")

            if existing_reference is not None:
                if (
                    existing_reference["provider_request_id"] != provider_request_id
                    or existing_reference["note_key"] != note_key
                    or int(existing_reference["head_generation"]) != head_generation
                ):
                    raise ValueError("receipt is already attached to a different note execution")
                attempt = connection.execute(
                    """
                    SELECT * FROM note_attempts
                    WHERE note_key=? AND attempt_no=?
                    """,
                    (note_key, existing_reference["attempt_no"]),
                ).fetchone()
                if attempt is None:
                    raise RuntimeError("note request reference points to a missing attempt")
                return (
                    _note_attempt_from_row(attempt),
                    _note_request_reference_from_row(existing_reference),
                    False,
                )

            active_attempt = connection.execute(
                """
                SELECT * FROM note_attempts
                WHERE note_key=?
                  AND state IN ('REQUESTED','WAITING_CONTEXT','GENERATING','STAGED','PUBLISHING')
                """,
                (note_key,),
            ).fetchone()
            generation_required = False
            if active_attempt is None:
                next_attempt = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM note_attempts WHERE note_key=?",
                        (note_key,),
                    ).fetchone()[0]
                )
                reusable = connection.execute(
                    """
                    SELECT * FROM note_artifacts
                    WHERE note_key=? AND state IN ('VERIFIED','PUBLISHED')
                    ORDER BY CASE state WHEN 'PUBLISHED' THEN 0 ELSE 1 END,
                             updated_at DESC, artifact_id
                    LIMIT 1
                    """,
                    (note_key,),
                ).fetchone()
                attempt_state = "STAGED" if reusable is not None else "REQUESTED"
                seed_artifact_id = None if reusable is None else reusable["artifact_id"]
                connection.execute(
                    """
                    INSERT INTO note_attempts(
                        note_key, attempt_no, state, seed_artifact_id, retry_count,
                        next_retry_at, last_successful_stage, error_class, error_code,
                        created_at, updated_at, terminal_at
                    ) VALUES (?, ?, ?, ?, 0, NULL, ?, NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        note_key,
                        next_attempt,
                        attempt_state,
                        seed_artifact_id,
                        "STAGED" if reusable is not None else None,
                        now,
                        now,
                    ),
                )
                active_attempt = connection.execute(
                    """
                    SELECT * FROM note_attempts
                    WHERE note_key=? AND attempt_no=?
                    """,
                    (note_key, next_attempt),
                ).fetchone()
                generation_required = reusable is None

            reference_id = _new_id("note_ref_")
            connection.execute(
                """
                INSERT INTO note_request_references(
                    reference_id, receipt_id, provider_request_id, note_key,
                    attempt_no, head_generation, state, created_at, updated_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, NULL)
                """,
                (
                    reference_id,
                    receipt_id,
                    provider_request_id,
                    note_key,
                    active_attempt["attempt_no"],
                    head_generation,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE note_jobs
                SET current_attempt_no=?, updated_at=?
                WHERE note_key=?
                """,
                (active_attempt["attempt_no"], now, note_key),
            )
            cursor = connection.execute(
                """
                UPDATE study_note_heads
                SET current_note_key=?, current_attempt_no=?, updated_at=?
                WHERE provider=? AND session_provider_page_id=?
                  AND generation=? AND current_receipt_id=? AND active=1
                """,
                (
                    note_key,
                    active_attempt["attempt_no"],
                    now,
                    provider,
                    session_provider_page_id,
                    head_generation,
                    receipt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("study-note head changed during request attachment")
            reference = connection.execute(
                "SELECT * FROM note_request_references WHERE reference_id=?",
                (reference_id,),
            ).fetchone()
            return (
                _note_attempt_from_row(active_attempt),
                _note_request_reference_from_row(reference),
                generation_required,
            )

    def get_note_attempt(self, note_key: str, attempt_no: int) -> NoteAttempt | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_attempts WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            return None if row is None else _note_attempt_from_row(row)

    def transition_note_attempt(
        self,
        note_key: str,
        attempt_no: int,
        state: str,
        *,
        retry_count: int | None = None,
        next_retry_at: str | None = None,
        last_successful_stage: str | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
        require_no_active_references: bool = False,
    ) -> NoteAttempt:
        if state not in _NOTE_STATES:
            raise ValueError("unsupported note attempt state")
        if retry_count is not None and (
            isinstance(retry_count, bool) or retry_count < 0 or retry_count > 3
        ):
            raise ValueError("retry_count must be an integer from 0 through 3")
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM note_attempts WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            if row is None:
                raise KeyError((note_key, attempt_no))
            current = row["state"]
            has_patch = any(
                value is not None
                for value in (
                    retry_count,
                    next_retry_at,
                    last_successful_stage,
                    error_class,
                    error_code,
                )
            )
            if current in _NOTE_TERMINAL_STATES:
                if current == state and not has_patch:
                    return _note_attempt_from_row(row)
                raise ValueError("terminal note attempts are immutable")
            if current != state and state not in _NOTE_TRANSITIONS[current]:
                raise ValueError(f"invalid note attempt transition: {current} -> {state}")
            if require_no_active_references:
                active_refs = connection.execute(
                    """
                    SELECT COUNT(*) FROM note_request_references
                    WHERE note_key=? AND attempt_no=? AND state='ACTIVE'
                    """,
                    (note_key, attempt_no),
                ).fetchone()[0]
                if int(active_refs) > 0:
                    raise ValueError(
                        "cannot transition attempt while active note request references remain"
                    )
            terminal_at = now if state in _NOTE_TERMINAL_STATES else None
            connection.execute(
                """
                UPDATE note_attempts
                SET state=?,
                    retry_count=COALESCE(?, retry_count),
                    next_retry_at=?,
                    last_successful_stage=COALESCE(?, last_successful_stage),
                    error_class=?,
                    error_code=?,
                    updated_at=?,
                    terminal_at=?
                WHERE note_key=? AND attempt_no=?
                """,
                (
                    state,
                    retry_count,
                    next_retry_at,
                    last_successful_stage,
                    error_class,
                    error_code,
                    now,
                    terminal_at,
                    note_key,
                    attempt_no,
                ),
            )
            return _note_attempt_from_row(
                connection.execute(
                    "SELECT * FROM note_attempts WHERE note_key=? AND attempt_no=?",
                    (note_key, attempt_no),
                ).fetchone()
            )

    def transition_note_request_reference(
        self,
        *,
        receipt_id: str,
        note_key: str,
        attempt_no: int,
        state: str,
        provider: str | None = None,
        session_provider_page_id: str | None = None,
        expected_receipt_hash: str | None = None,
        inactive_reason: str | None = None,
    ) -> NoteRequestReference:
        _require_text(receipt_id, "receipt_id")
        _require_text(note_key, "note_key")
        if isinstance(attempt_no, bool) or attempt_no < 1:
            raise ValueError("attempt_no must be a positive integer")
        if state not in _REFERENCE_STATES:
            raise ValueError("unsupported note request reference state")
        if (provider is None) != (session_provider_page_id is None):
            raise ValueError(
                "provider and session_provider_page_id must be provided together"
            )
        if provider is not None:
            _require_text(provider, "provider")
            _require_text(session_provider_page_id, "session_provider_page_id")
        now = _utc_now()
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM note_request_references WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            if row["note_key"] != note_key or int(row["attempt_no"]) != attempt_no:
                raise ValueError("note request reference does not match expected attempt identity")
            if (
                state in {"CANCELLED", "SUPERSEDED"}
                and provider is not None
                and session_provider_page_id is not None
            ):
                _require_text(expected_receipt_hash, "expected_receipt_hash")
                _require_text(inactive_reason, "inactive_reason")
            current = row["state"]
            if current == state:
                return _note_request_reference_from_row(row)
            if state not in _REFERENCE_TRANSITIONS[current]:
                raise ValueError(f"invalid note request reference transition: {current} -> {state}")
            ended_at = now if state in {"CANCELLED", "SUPERSEDED"} else None
            connection.execute(
                """
                UPDATE note_request_references
                SET state=?, updated_at=?, ended_at=?
                WHERE reference_id=?
                """,
                (state, now, ended_at, row["reference_id"]),
            )
            if (
                state in {"CANCELLED", "SUPERSEDED"}
                and provider is not None
                and session_provider_page_id is not None
            ):
                _require_text(expected_receipt_hash, "expected_receipt_hash")
                _require_text(inactive_reason, "inactive_reason")
                connection.execute(
                    """
                    UPDATE study_note_heads
                    SET active=0, inactive_reason=?, updated_at=?
                    WHERE provider=? AND session_provider_page_id=?
                      AND generation=? AND current_receipt_id=?
                      AND current_request_id=? AND receipt_hash=?
                      AND current_note_key=? AND current_attempt_no=? AND active=1
                    """,
                    (
                        inactive_reason,
                        now,
                        provider,
                        session_provider_page_id,
                        row["head_generation"],
                        row["receipt_id"],
                        row["provider_request_id"],
                        expected_receipt_hash,
                        note_key,
                        attempt_no,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM note_request_references WHERE reference_id=?",
                (row["reference_id"],),
            ).fetchone()
            return _note_request_reference_from_row(updated)

    def count_active_note_references(self, note_key: str, attempt_no: int) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) FROM note_request_references
                WHERE note_key=? AND attempt_no=? AND state='ACTIVE'
                """,
                (note_key, attempt_no),
            ).fetchone()
            return int(row[0])

    def record_note_artifact(
        self,
        artifact: NoteArtifact | None = None,
        **kwargs: Any,
    ) -> NoteArtifact:
        values = dict(artifact.__dict__) if artifact is not None else dict(kwargs)
        values.setdefault("artifact_id", _new_id("note_artifact_"))
        values.setdefault("ai_region_id", None)
        values.setdefault("ai_block_ids_json", "[]")
        values.setdefault("last_publish_hash", None)
        values.setdefault("state", "STAGED")
        values.setdefault("created_at", _utc_now())
        values.setdefault("verified_at", None)
        values.setdefault("updated_at", values["created_at"])
        for name in (
            "artifact_id",
            "note_key",
            "output_identity",
            "output_hash",
            "manifest_hash",
            "writer_version",
            "state",
        ):
            _require_text(values.get(name), name)
        if values["state"] not in _ARTIFACT_STATES:
            raise ValueError("unsupported note artifact state")
        values["ai_block_ids_json"] = _canonical_json_field(
            values["ai_block_ids_json"], "ai_block_ids_json"
        )
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM note_artifacts
                WHERE note_key=? AND output_hash=? AND manifest_hash=?
                """,
                (values["note_key"], values["output_hash"], values["manifest_hash"]),
            ).fetchone()
            if existing is not None:
                for column in (
                    "output_identity",
                    "writer_version",
                    "ai_region_id",
                    "ai_block_ids_json",
                    "last_publish_hash",
                ):
                    if existing[column] != values[column]:
                        raise ValueError(f"artifact identity collision on {column}")
                return _note_artifact_from_row(existing)
            connection.execute(
                """
                INSERT INTO note_artifacts(
                    artifact_id, note_key, output_identity, output_hash, manifest_hash,
                    writer_version, ai_region_id, ai_block_ids_json, last_publish_hash,
                    state, created_at, verified_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    values.get(name)
                    for name in (
                        "artifact_id",
                        "note_key",
                        "output_identity",
                        "output_hash",
                        "manifest_hash",
                        "writer_version",
                        "ai_region_id",
                        "ai_block_ids_json",
                        "last_publish_hash",
                        "state",
                        "created_at",
                        "verified_at",
                        "updated_at",
                    )
                ),
            )
            return _note_artifact_from_row(
                connection.execute(
                    "SELECT * FROM note_artifacts WHERE artifact_id=?",
                    (values["artifact_id"],),
                ).fetchone()
            )

    def get_reusable_note_artifact(self, note_key: str) -> NoteArtifact | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM note_artifacts
                WHERE note_key=? AND state IN ('VERIFIED','PUBLISHED')
                ORDER BY CASE state WHEN 'PUBLISHED' THEN 0 ELSE 1 END,
                         updated_at DESC, artifact_id
                LIMIT 1
                """,
                (note_key,),
            ).fetchone()
            return None if row is None else _note_artifact_from_row(row)

    def transition_note_artifact(
        self,
        artifact_id: str,
        state: str,
        *,
        ai_region_id: str | None = None,
        ai_block_ids: Any = None,
        last_publish_hash: str | None = None,
    ) -> NoteArtifact:
        if state not in _ARTIFACT_STATES:
            raise ValueError("unsupported note artifact state")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM note_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            current = row["state"]
            has_patch = any(
                value is not None
                for value in (ai_region_id, ai_block_ids, last_publish_hash)
            )
            if current == "STALE":
                if current == state and not has_patch:
                    return _note_artifact_from_row(row)
                raise ValueError("stale note artifact history is immutable")
            if current != state and state not in _ARTIFACT_TRANSITIONS[current]:
                raise ValueError(f"invalid note artifact transition: {current} -> {state}")
            blocks_json = (
                row["ai_block_ids_json"]
                if ai_block_ids is None
                else _canonical_json_field(ai_block_ids, "ai_block_ids")
            )
            now = _utc_now()
            verified_at = row["verified_at"]
            if state in {"VERIFIED", "PUBLISHED"} and verified_at is None:
                verified_at = now
            connection.execute(
                """
                UPDATE note_artifacts
                SET state=?, ai_region_id=COALESCE(?, ai_region_id),
                    ai_block_ids_json=?, last_publish_hash=COALESCE(?, last_publish_hash),
                    verified_at=?, updated_at=?
                WHERE artifact_id=?
                """,
                (
                    state,
                    ai_region_id,
                    blocks_json,
                    last_publish_hash,
                    verified_at,
                    now,
                    artifact_id,
                ),
            )
            return _note_artifact_from_row(
                connection.execute(
                    "SELECT * FROM note_artifacts WHERE artifact_id=?",
                    (artifact_id,),
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


def _canonical_json_field(value: Any, name: str) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must contain valid JSON") from exc
    try:
        return canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite JSON") from exc


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


def _study_note_head_from_row(row: sqlite3.Row) -> StudyNoteHead:
    return StudyNoteHead(**dict(row))


def _note_job_from_row(row: sqlite3.Row) -> NoteJob:
    return NoteJob(**dict(row))


def _note_attempt_from_row(row: sqlite3.Row) -> NoteAttempt:
    return NoteAttempt(**dict(row))


def _note_request_reference_from_row(row: sqlite3.Row) -> NoteRequestReference:
    return NoteRequestReference(**dict(row))


def _note_artifact_from_row(row: sqlite3.Row) -> NoteArtifact:
    return NoteArtifact(**dict(row))


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
