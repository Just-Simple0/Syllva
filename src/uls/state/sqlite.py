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
    Job,
    ProcessingRecord,
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
                    WHERE job_id = ?
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
                WHERE job_id = ?
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
                    WHERE job_id = ?
                    """,
                    (desired.value, job_id),
                )
            elif desired is JobStatus.PENDING:
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = NULL
                    WHERE job_id = ?
                    """,
                    (desired.value, job_id),
                )
            elif desired in _TERMINAL_STATUSES:
                connection.execute(
                    """
                    UPDATE processing_records
                    SET status = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE job_id = ?
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
