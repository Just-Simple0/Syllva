"""Durable local store for the C6 study-note submission service.

Owns its own SQLite connection/file, entirely separate from
``uls.state.sqlite.SQLiteStateStore``.  No cross-DB transaction is claimed or
attempted; the cross-store recovery discipline is the operation journal in
this module (plan section 7), not shared ACID.  Single-active-worker plus the
shared coordinator lock (owned outside this package) makes sequential access
safe; this store does not itself acquire any process-wide lock.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .identity import new_uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_REQUEST_TERMINAL_STATUSES = frozenset({
    "READY", "PARTIAL", "FAILED", "CANCELLED", "STALE", "SUPERSEDED",
})
_REQUEST_DISPLAY_STATUSES = _REQUEST_TERMINAL_STATUSES | frozenset({
    "PENDING", "CLAIMED", "REQUESTED", "WAITING_CONTEXT", "GENERATING",
    "STAGED", "PUBLISHING", "RECONCILIATION_REQUIRED", "RETRY_WAIT",
})


class DifferentPayloadReplayError(ValueError):
    """Same idempotency_key or grant with a different payload/draft.  No overwrite."""


class GrantUnavailableError(ValueError):
    """Grant is unknown, revoked, expired, or otherwise cannot be consumed."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_requests (
    client_request_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    caller_context TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    evidence_mode TEXT NOT NULL,
    selected_materials_json TEXT NOT NULL DEFAULT '[]',
    learner_request TEXT,
    origin TEXT NOT NULL DEFAULT 'client_submitted',
    created_time TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    cancel_requested_at TEXT,
    note_key TEXT,
    request_reference_id TEXT,
    head_generation INTEGER,
    session_provider_page_id TEXT,
    attempt_no INTEGER,
    waiting_reason TEXT,
    display_status TEXT,
    display_reason TEXT,
    artifact_link TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(idempotency_key, caller_context)
);

CREATE TABLE IF NOT EXISTS note_prepared_context (
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    context_json TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    PRIMARY KEY(note_key, attempt_no)
);

CREATE TABLE IF NOT EXISTS note_submission_grants (
    grant_id TEXT PRIMARY KEY,
    client_request_id TEXT,
    session_provider_page_id TEXT,
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    head_generation INTEGER NOT NULL,
    caller_context TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    consumed_at TEXT,
    consumed_draft_hash TEXT
);

CREATE TABLE IF NOT EXISTS note_draft_submissions (
    draft_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    draft_text TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    client_model_metadata_json TEXT,
    submitted_at TEXT NOT NULL,
    validated_at TEXT,
    validation_outcome TEXT,
    validation_reason TEXT
, dispatched_at TEXT
);

CREATE TABLE IF NOT EXISTS note_reject_counters (
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(note_key, attempt_no)
);

CREATE TABLE IF NOT EXISTS note_operation_journal (
    operation_id TEXT PRIMARY KEY,
    client_request_id TEXT,
    note_key TEXT,
    head_generation INTEGER,
    attempt_no INTEGER,
    boundary TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS note_drive_stage_receipts (
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    marker_json TEXT NOT NULL,
    state TEXT NOT NULL,
    file_id TEXT,
    content_hash TEXT,
    manifest_hash TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY(note_key, attempt_no)
);

CREATE TABLE IF NOT EXISTS note_folder_receipts (
    folder_key TEXT PRIMARY KEY,
    marker_json TEXT NOT NULL,
    state TEXT NOT NULL,
    folder_id TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS note_ai_block (
    session_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'NONE',
    block_id TEXT,
    last_worker_hash TEXT,
    intent_note_key TEXT,
    intent_attempt_no INTEGER,
    intent_payload_hash TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class StudyNoteStore:
    """Owns ``study_notes.sqlite3``.  Not thread-shared across processes;
    single-active-worker plus the caller-held coordinator lock make callers
    within one process safe under the module-level ``threading.Lock`` here."""

    def __init__(self, path: str | Path, *, clock: Callable[[], str] = _utc_now) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=OFF")
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()
        # Owner-only permissions: this file holds private prepared evidence,
        # draft text, and capability/grant identifiers.  Never logged; only
        # returned in an authorized MCP response to the caller that owns it.
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        self.clock = clock

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
    # -- client_requests --------------------------------------------------

    def create_or_replay_client_request(
        self,
        *,
        idempotency_key: str,
        caller_context: str,
        payload_hash: str,
        session_id: str,
        evidence_mode: str,
        selected_materials: Sequence[str] | None,
        learner_request: str | None,
    ) -> dict[str, Any]:
        """Insert a new immutable request, or replay an identical one.

        Same idempotency_key + caller_context + payload_hash returns the
        prior row unchanged.  Same key/caller with a DIFFERENT payload_hash
        is rejected -- no silent reuse across distinct intents.  This never
        looks up or reuses any existing active head (plan section 4).
        """
        for value, name in (
            (idempotency_key, "idempotency_key"),
            (caller_context, "caller_context"),
            (payload_hash, "payload_hash"),
            (session_id, "session_id"),
            (evidence_mode, "evidence_mode"),
        ):
            _require_text(value, name)
        now = self.clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM client_requests WHERE idempotency_key=? AND caller_context=?",
                (idempotency_key, caller_context),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise DifferentPayloadReplayError(
                        "idempotency_key reused with a different request payload"
                    )
                return dict(existing)
            client_request_id = new_uuid4()
            connection.execute(
                """
                INSERT INTO client_requests(
                    client_request_id, idempotency_key, caller_context, payload_hash,
                    session_id, evidence_mode, selected_materials_json, learner_request,
                    origin, created_time, state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'client_submitted', ?, 'PENDING', ?)
                """,
                (
                    client_request_id, idempotency_key, caller_context, payload_hash,
                    session_id, evidence_mode, _json(list(selected_materials or ())),
                    learner_request, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            return dict(row)

    def get_client_request(self, client_request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def list_pending_client_requests(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Ordered pending rows for the merged coordinator source dispatcher.

        Ordered by ``(created_time, client_request_id)`` -- the same stable
        claim ordering used elsewhere in this codebase.
        """
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM client_requests WHERE state='PENDING'
                ORDER BY created_time ASC, client_request_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_client_requests(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return the complete bounded request inventory in stable order."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM client_requests
                ORDER BY created_time ASC, client_request_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_client_request_status(
        self,
        client_request_id: str,
        status: str,
        *,
        reason: str | None = None,
        artifact_link: str | None = None,
    ) -> dict[str, Any]:
        """Persist the worker-visible lifecycle status and optional artifact link."""
        if status not in _REQUEST_DISPLAY_STATUSES:
            raise ValueError("unsupported client request status")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_request_id)
            if row["state"] in _REQUEST_TERMINAL_STATUSES and row["state"] != status:
                # Terminal request outcomes are durable history.  Replaying the
                # same terminal status may enrich the preserved link/reason.
                raise ValueError("terminal client request status cannot be changed")
            connection.execute(
                """
                UPDATE client_requests
                SET state=?, display_status=?, display_reason=?,
                    artifact_link=COALESCE(?, artifact_link), updated_at=?
                WHERE client_request_id=?
                """,
                (status, status, reason, artifact_link, now, client_request_id),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM client_requests WHERE client_request_id=?",
                    (client_request_id,),
                ).fetchone()
            )

    def mark_client_request_claimed(
        self,
        client_request_id: str,
        *,
        note_key: str,
        request_reference_id: str,
        head_generation: int,
        attempt_no: int,
    ) -> dict[str, Any]:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(client_request_id)
            if row["head_generation"] is not None and row["head_generation"] != head_generation:
                raise ValueError("client request head generation is immutable")
            existing_attempt = (
                row["note_key"],
                row["request_reference_id"],
                row["attempt_no"],
            )
            desired_attempt = (note_key, request_reference_id, attempt_no)
            if any(value is not None for value in existing_attempt):
                if existing_attempt == desired_attempt:
                    return dict(row)
                raise ValueError("client request attempt binding is immutable")
            if row["state"] in _REQUEST_TERMINAL_STATUSES or row["state"] == "SUPERSEDED":
                return dict(row)
            connection.execute(
                """
                UPDATE client_requests
                SET state='CLAIMED', display_status='CLAIMED', display_reason=NULL,
                    waiting_reason=NULL, note_key=?, request_reference_id=?,
                    head_generation=?, attempt_no=?, updated_at=?
                WHERE client_request_id=?
                """,
                (note_key, request_reference_id, head_generation, attempt_no, now, client_request_id),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
                ).fetchone()
            )

    def mark_client_request_head_claimed(
        self, client_request_id: str, *, head_generation: int, session_provider_page_id: str,
    ) -> dict[str, Any]:
        """Record the head claim before evidence/note_key are known.

        state stays PENDING (not yet CLAIMED with a note_key) until
        `mark_client_request_claimed` runs after evidence assembly succeeds;
        this only records the generation and clears any stale waiting_reason.
        session_provider_page_id is persisted immutably on first claim; a
        later call with a DIFFERENT physical id for the same request is a
        graph-identity drift and is rejected rather than silently accepted --
        the handler resolves the physical id once and must not re-resolve a
        new one per dispatch.
        """
        now = self.clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT session_provider_page_id FROM client_requests WHERE client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(client_request_id)
            if (
                existing["session_provider_page_id"] is not None
                and existing["session_provider_page_id"] != session_provider_page_id
            ):
                raise ValueError(
                    "session_provider_page_id drift: a different physical Session id "
                    "was already recorded for this request"
                )
            connection.execute(
                "UPDATE client_requests"
                " SET head_generation=?, session_provider_page_id=?, waiting_reason=NULL, updated_at=?"
                " WHERE client_request_id=?",
                (head_generation, session_provider_page_id, now, client_request_id),
            )
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(client_request_id)
            return dict(row)

    def mark_client_request_waiting(self, client_request_id: str, *, reason: str) -> dict[str, Any]:
        now = self.clock()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE client_requests SET waiting_reason=?, updated_at=? WHERE client_request_id=?",
                (reason, now, client_request_id),
            )
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(client_request_id)
            return dict(row)

    def list_claimed_awaiting_evidence(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM client_requests
                WHERE head_generation IS NOT NULL AND note_key IS NULL AND state='PENDING'
                ORDER BY created_time ASC, client_request_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def request_cancel(self, client_request_id: str, *, caller_context: str) -> dict[str, Any]:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(client_request_id)
            if row["caller_context"] != caller_context:
                raise PermissionError("cancel target belongs to a different caller")
            if row["cancel_requested_at"] is None:
                connection.execute(
                    "UPDATE client_requests SET cancel_requested_at=?, updated_at=? WHERE client_request_id=?",
                    (now, now, client_request_id),
                )
            return dict(
                connection.execute(
                    "SELECT * FROM client_requests WHERE client_request_id=?", (client_request_id,)
                ).fetchone()
            )

    # -- prepared evidence context -----------------------------------------

    def save_prepared_context(
        self, *, note_key: str, attempt_no: int, evidence_manifest_hash: str, context: Any,
    ) -> None:
        now = self.clock()
        context_json = _json(context)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM note_prepared_context WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            if existing is not None:
                if (
                    existing["evidence_manifest_hash"] != evidence_manifest_hash
                    or existing["context_json"] != context_json
                ):
                    raise DifferentPayloadReplayError(
                        "prepared evidence context is immutable for an attempt"
                    )
                return
            connection.execute(
                """
                INSERT INTO note_prepared_context(
                    note_key, attempt_no, evidence_manifest_hash, context_json, prepared_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (note_key, attempt_no, evidence_manifest_hash, context_json, now),
            )

    def get_prepared_context(self, note_key: str, attempt_no: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_prepared_context WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["context"] = json.loads(result.pop("context_json"))
            return result

    # -- grants ---------------------------------------------------------------

    def issue_grant(
        self,
        *,
        note_key: str,
        attempt_no: int,
        evidence_manifest_hash: str,
        head_generation: int,
        caller_context: str,
        ttl_seconds: int,
        client_request_id: str | None = None,
        session_provider_page_id: str | None = None,
    ) -> dict[str, Any]:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat(timespec="microseconds")
        expires = now_dt.timestamp() + ttl_seconds
        from datetime import datetime as _dt
        expires_at = _dt.fromtimestamp(expires, UTC).isoformat(timespec="microseconds")
        grant_id = new_uuid4()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO note_submission_grants(
                    grant_id, client_request_id, session_provider_page_id, note_key, attempt_no,
                    evidence_manifest_hash, head_generation, caller_context, issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (grant_id, client_request_id, session_provider_page_id, note_key, attempt_no,
                 evidence_manifest_hash, head_generation, caller_context, now, expires_at),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_submission_grants WHERE grant_id=?", (grant_id,)
                ).fetchone()
            )

    def get_grant(self, grant_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_submission_grants WHERE grant_id=?", (grant_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def get_open_grant_for_attempt(self, note_key: str, attempt_no: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM note_submission_grants WHERE note_key=? AND attempt_no=?
                  AND revoked_at IS NULL AND consumed_at IS NULL
                ORDER BY issued_at DESC LIMIT 1
                """,
                (note_key, attempt_no),
            ).fetchone()
            return None if row is None else dict(row)

    def get_open_grant_for_request(self, client_request_id: str) -> dict[str, Any] | None:
        """The caller/request-bound grant lookup used by status projection.

        Filters on client_request_id, not only note_key/attempt_no --
        get_open_grant_for_attempt alone can return a DIFFERENT request's
        (or a different caller's) grant when several requests share one
        note_key/attempt_no; this method never leaks across requests.
        """
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM note_submission_grants WHERE client_request_id=?
                  AND revoked_at IS NULL AND consumed_at IS NULL
                ORDER BY issued_at DESC LIMIT 1
                """,
                (client_request_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def get_request_for_grant(self, grant_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT client_requests.* FROM client_requests
                JOIN note_submission_grants ON note_submission_grants.client_request_id = client_requests.client_request_id
                WHERE note_submission_grants.grant_id=?
                """,
                (grant_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def revoke_attempt_grants(self, note_key: str, attempt_no: int, *, reason: str) -> int:
        now = self.clock()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE note_submission_grants SET revoked_at=?
                WHERE note_key=? AND attempt_no=? AND revoked_at IS NULL AND consumed_at IS NULL
                """,
                (now, note_key, attempt_no),
            )
            return cursor.rowcount

    def revoke_grant(self, grant_id: str, *, reason: str) -> None:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_submission_grants WHERE grant_id=?", (grant_id,)
            ).fetchone()
            if row is None:
                raise KeyError(grant_id)
            if row["revoked_at"] is not None or row["consumed_at"] is not None:
                return
            connection.execute(
                "UPDATE note_submission_grants SET revoked_at=? WHERE grant_id=?", (now, grant_id)
            )

    def consume_grant_and_insert_draft(
        self, *, grant_id: str, draft_text: str, client_model_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomic consume+insert (plan section 4/7).

        Returns ``(draft_row, created)``.  ``created`` is False when this call
        is an exact-payload idempotent replay of an already-consumed grant.
        A different draft body on an already-consumed grant raises
        :class:`DifferentPayloadReplayError` -- no overwrite.
        """
        _require_text(grant_id, "grant_id")
        if not isinstance(draft_text, str) or not draft_text:
            raise ValueError("draft_text must be a non-empty string")
        import hashlib
        draft_hash = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()
        now = self.clock()
        with self._transaction() as connection:
            grant = connection.execute(
                "SELECT * FROM note_submission_grants WHERE grant_id=?", (grant_id,)
            ).fetchone()
            if grant is None:
                raise GrantUnavailableError("unknown grant_id")
            if grant["revoked_at"] is not None:
                raise GrantUnavailableError("grant was revoked")
            if grant["expires_at"] < now and grant["consumed_at"] is None:
                raise GrantUnavailableError("grant has expired")
            if grant["client_request_id"] is not None and grant["consumed_at"] is None:
                # Exact cancelled/stale rejection: the underlying local
                # request state is checked inside this same atomic
                # transaction, not left to a separate later step -- a grant
                # can still be technically open/unconsumed after its request
                # was cancelled.
                owning_request = connection.execute(
                    "SELECT state, cancel_requested_at FROM client_requests WHERE client_request_id=?",
                    (grant["client_request_id"],),
                ).fetchone()
                if owning_request is not None and (
                    owning_request["state"] == "CANCELLED"
                    or owning_request["cancel_requested_at"] is not None
                ):
                    raise GrantUnavailableError(
                        "the request owning this grant is cancelled or has a pending cancel"
                    )
            if grant["consumed_at"] is not None:
                if grant["consumed_draft_hash"] == draft_hash:
                    existing = connection.execute(
                        "SELECT * FROM note_draft_submissions WHERE grant_id=? AND draft_hash=?",
                        (grant_id, draft_hash),
                    ).fetchone()
                    if existing is not None:
                        return dict(existing), False
                raise DifferentPayloadReplayError(
                    "grant already consumed with a different draft body"
                )
            draft_id = new_uuid4()
            metadata_json = None if client_model_metadata is None else _json(dict(client_model_metadata))
            connection.execute(
                """
                INSERT INTO note_draft_submissions(
                    draft_id, grant_id, note_key, attempt_no, draft_text, draft_hash,
                    client_model_metadata_json, submitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (draft_id, grant_id, grant["note_key"], grant["attempt_no"], draft_text,
                 draft_hash, metadata_json, now),
            )
            connection.execute(
                "UPDATE note_submission_grants SET consumed_at=?, consumed_draft_hash=? WHERE grant_id=?",
                (now, draft_hash, grant_id),
            )
            row = connection.execute(
                "SELECT * FROM note_draft_submissions WHERE draft_id=?", (draft_id,)
            ).fetchone()
            return dict(row), True

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_draft_submissions WHERE draft_id=?", (draft_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def list_unvalidated_drafts(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM note_draft_submissions WHERE validated_at IS NULL
                ORDER BY submitted_at ASC, draft_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_draft_validated(self, draft_id: str, *, outcome: str, reason: str | None = None) -> None:
        now = self.clock()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE note_draft_submissions
                SET validated_at=?, validation_outcome=?, validation_reason=?
                WHERE draft_id=?
                """,
                (now, outcome, reason, draft_id),
            )

    def list_accepted_undispatched_drafts(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM note_draft_submissions
                WHERE validation_outcome='ACCEPTED' AND dispatched_at IS NULL
                ORDER BY submitted_at ASC, draft_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_draft_dispatched(self, draft_id: str) -> None:
        now = self.clock()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE note_draft_submissions SET dispatched_at=? WHERE draft_id=?",
                (now, draft_id),
            )

    # -- reject budget (plan section 8, distinct from pipeline retry) --------

    def increment_reject_counter(self, *, note_key: str, attempt_no: int) -> int:
        """Return the new rejected-submission count for this attempt.

        Capped enforcement (max 3, third rejection is terminal) lives in the
        handler, not here -- this method only counts.
        """
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO note_reject_counters(note_key, attempt_no, rejected_count)
                VALUES (?, ?, 1)
                ON CONFLICT(note_key, attempt_no) DO UPDATE SET
                    rejected_count = rejected_count + 1
                """,
                (note_key, attempt_no),
            )
            row = connection.execute(
                "SELECT rejected_count FROM note_reject_counters WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            return int(row["rejected_count"])

    def reject_draft_once(self, draft_id: str, reason: str) -> int:
        """Reject one draft and consume exactly one attempt rejection budget."""
        _require_text(reason, "reason")
        now = self.clock()
        with self._transaction() as connection:
            draft = connection.execute(
                "SELECT * FROM note_draft_submissions WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if draft is None:
                raise KeyError(draft_id)
            if draft["validated_at"] is not None:
                if draft["validation_outcome"] != "REJECTED":
                    raise ValueError("draft already has a different validation outcome")
                row = connection.execute(
                    """
                    SELECT rejected_count FROM note_reject_counters
                    WHERE note_key=? AND attempt_no=?
                    """,
                    (draft["note_key"], draft["attempt_no"]),
                ).fetchone()
                return 0 if row is None else int(row["rejected_count"])
            connection.execute(
                """
                UPDATE note_draft_submissions
                SET validated_at=?, validation_outcome='REJECTED', validation_reason=?
                WHERE draft_id=? AND validated_at IS NULL
                """,
                (now, reason, draft_id),
            )
            connection.execute(
                """
                INSERT INTO note_reject_counters(note_key, attempt_no, rejected_count)
                VALUES (?, ?, 1)
                ON CONFLICT(note_key, attempt_no) DO UPDATE SET
                    rejected_count = rejected_count + 1
                """,
                (draft["note_key"], draft["attempt_no"]),
            )
            row = connection.execute(
                """
                SELECT rejected_count FROM note_reject_counters
                WHERE note_key=? AND attempt_no=?
                """,
                (draft["note_key"], draft["attempt_no"]),
            ).fetchone()
            return int(row["rejected_count"])

    def get_reject_count(self, *, note_key: str, attempt_no: int) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT rejected_count FROM note_reject_counters WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            return 0 if row is None else int(row["rejected_count"])

    # -- cross-store operation journal (plan section 7) -----------------------

    def journal_get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_operation_journal WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def journal_intent(
        self,
        *,
        operation_id: str,
        client_request_id: str | None,
        note_key: str | None,
        head_generation: int | None,
        attempt_no: int | None,
        boundary: str,
        payload_hash: str,
    ) -> dict[str, Any]:
        """Persist INTENT before calling the main state store.

        Idempotent: an existing row for this exact operation_id is returned
        unchanged rather than re-inserted or overwritten.
        """
        now = self.clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM note_operation_journal WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute(
                """
                INSERT INTO note_operation_journal(
                    operation_id, client_request_id, note_key, head_generation, attempt_no,
                    boundary, payload_hash, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'INTENT', ?)
                """,
                (operation_id, client_request_id, note_key, head_generation, attempt_no,
                 boundary, payload_hash, now),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_operation_journal WHERE operation_id=?", (operation_id,)
                ).fetchone()
            )

    def journal_transition(self, operation_id: str, state: str) -> dict[str, Any]:
        if state not in ("APPLIED", "DONE", "BLOCKED"):
            raise ValueError("unsupported journal state")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_operation_journal WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(operation_id)
            connection.execute(
                """
                UPDATE note_operation_journal SET state=?, applied_at=COALESCE(applied_at, ?)
                WHERE operation_id=?
                """,
                (state, now, operation_id),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_operation_journal WHERE operation_id=?", (operation_id,)
                ).fetchone()
            )



    # -- Drive stage receipts: first-create response-loss recovery ----------
    # (plan section 7: persist marker before create; on response loss, search
    # that exact marker; adopt only an exact match; never a second create on
    # timeout alone.)

    def persist_stage_marker(
        self, *, note_key: str, attempt_no: int, marker: Mapping[str, str],
        manifest_hash: str, content_hash: str,
    ) -> dict[str, Any]:
        now = self.clock()
        marker_json = _json(dict(marker))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM note_drive_stage_receipts WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            if existing is not None:
                if (
                    existing["marker_json"] != marker_json
                    or existing["manifest_hash"] != manifest_hash
                    or existing["content_hash"] != content_hash
                ):
                    raise DifferentPayloadReplayError(
                        "Drive stage intent is immutable for a note attempt"
                    )
                return dict(existing)
            connection.execute(
                """
                INSERT INTO note_drive_stage_receipts(
                    note_key, attempt_no, marker_json, state, content_hash,
                    manifest_hash, created_at
                ) VALUES (?, ?, ?, 'MARKER_PERSISTED', ?, ?, ?)
                """,
                (note_key, attempt_no, marker_json, content_hash, manifest_hash, now),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_drive_stage_receipts WHERE note_key=? AND attempt_no=?",
                    (note_key, attempt_no),
                ).fetchone()
            )

    def get_stage_receipt(self, note_key: str, attempt_no: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_drive_stage_receipts WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            return None if row is None else dict(row)

    def get_verified_stage_receipt_by_output(
        self, note_key: str, output_identity: str,
    ) -> dict[str, Any] | None:
        """Find any VERIFIED receipt for note_key with this exact file_id,
        regardless of which attempt_no originally staged it -- used to seed
        a new attempt from a reusable artifact without re-staging."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM note_drive_stage_receipts
                WHERE note_key=? AND file_id=? AND state='VERIFIED'
                ORDER BY verified_at DESC LIMIT 1
                """,
                (note_key, output_identity),
            ).fetchone()
            return None if row is None else dict(row)

    def mark_stage_state(
        self, *, note_key: str, attempt_no: int, state: str,
        file_id: str | None = None, content_hash: str | None = None,
    ) -> dict[str, Any]:
        if state not in ("MARKER_PERSISTED", "UNKNOWN", "VERIFIED", "BLOCKED"):
            raise ValueError("unsupported stage receipt state")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_drive_stage_receipts WHERE note_key=? AND attempt_no=?",
                (note_key, attempt_no),
            ).fetchone()
            if row is None:
                raise KeyError((note_key, attempt_no))
            if (
                content_hash is not None
                and row["content_hash"] is not None
                and row["content_hash"] != content_hash
            ):
                raise DifferentPayloadReplayError(
                    "Drive stage content hash cannot change after intent persistence"
                )
            verified_at = row["verified_at"]
            if state == "VERIFIED" and verified_at is None:
                verified_at = now
            connection.execute(
                """
                UPDATE note_drive_stage_receipts
                SET state=?, file_id=COALESCE(?, file_id), content_hash=COALESCE(?, content_hash),
                    verified_at=?
                WHERE note_key=? AND attempt_no=?
                """,
                (state, file_id, content_hash, verified_at, note_key, attempt_no),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_drive_stage_receipts WHERE note_key=? AND attempt_no=?",
                    (note_key, attempt_no),
                ).fetchone()
            )

    # -- AI block: session-level guard against a duplicate first append -----
    # (plan section 10: reconcile UNKNOWN at the physical Session region
    # level so a NEW request cannot bypass an unresolved prior append and
    # emit a second block.  Keyed by session_id, not by note_key/attempt --
    # a session has at most one AI block region regardless of how many notes
    # or attempts have targeted it.)


    # -- folder creation durability (parent-flagged fix: same UNKNOWN guard
    # discipline as file staging / AI block, applied to nested folder create) --

    def get_folder_receipt(self, folder_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_folder_receipts WHERE folder_key=?", (folder_key,)
            ).fetchone()
            return None if row is None else dict(row)

    def persist_folder_marker(self, *, folder_key: str, marker: Mapping[str, str]) -> dict[str, Any]:
        now = self.clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM note_folder_receipts WHERE folder_key=?", (folder_key,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute(
                """
                INSERT INTO note_folder_receipts(folder_key, marker_json, state, created_at)
                VALUES (?, ?, 'MARKER_PERSISTED', ?)
                """,
                (folder_key, _json(dict(marker)), now),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_folder_receipts WHERE folder_key=?", (folder_key,)
                ).fetchone()
            )

    def mark_folder_state(
        self, *, folder_key: str, state: str, folder_id: str | None = None,
    ) -> dict[str, Any]:
        if state not in ("MARKER_PERSISTED", "UNKNOWN", "VERIFIED", "BLOCKED"):
            raise ValueError("unsupported folder receipt state")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_folder_receipts WHERE folder_key=?", (folder_key,)
            ).fetchone()
            if row is None:
                raise KeyError(folder_key)
            verified_at = row["verified_at"]
            if state == "VERIFIED" and verified_at is None:
                verified_at = now
            connection.execute(
                """
                UPDATE note_folder_receipts SET state=?, folder_id=COALESCE(?, folder_id), verified_at=?
                WHERE folder_key=?
                """,
                (state, folder_id, verified_at, folder_key),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_folder_receipts WHERE folder_key=?", (folder_key,)
                ).fetchone()
            )

    def get_ai_block(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM note_ai_block WHERE session_id=?", (session_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def begin_block_create_intent(
        self, *, session_id: str, note_key: str, attempt_no: int,
    ) -> dict[str, Any]:
        """Claim the session-level right to attempt the FIRST block create.

        Raises :class:`PermissionError` if the session is already KNOWN (a
        block exists -- use an update, not another create), UNKNOWN (a prior
        create's result is unresolved -- must reconcile before any new
        create), or BLOCKED.
        """
        return self._begin_block_create_intent(
            session_id=session_id, note_key=note_key, attempt_no=attempt_no, payload_hash=None,
        )

    def begin_block_create_intent_with_payload(
        self, *, session_id: str, note_key: str, attempt_no: int, payload_hash: str,
    ) -> dict[str, Any]:
        """Same as begin_block_create_intent but also records the exact intended
        payload hash, used later by reconcile-by-exact-content-match on UNKNOWN.
        """
        return self._begin_block_create_intent(
            session_id=session_id, note_key=note_key, attempt_no=attempt_no, payload_hash=payload_hash,
        )

    def _begin_block_create_intent(
        self, *, session_id: str, note_key: str, attempt_no: int, payload_hash: str | None,
    ) -> dict[str, Any]:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_ai_block WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is not None and row["state"] not in ("NONE",):
                raise PermissionError(
                    f"session {session_id} AI block state is {row['state']!r};"
                    " a new create is not permitted until it is KNOWN or reconciled"
                )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO note_ai_block(
                        session_id, state, intent_note_key, intent_attempt_no,
                        intent_payload_hash, created_at, updated_at
                    ) VALUES (?, 'INTENT', ?, ?, ?, ?, ?)
                    """,
                    (session_id, note_key, attempt_no, payload_hash, now, now),
                )
            else:
                prior_identity = (
                    row["intent_note_key"], row["intent_attempt_no"], row["intent_payload_hash"],
                )
                desired_identity = (note_key, attempt_no, payload_hash)
                if prior_identity != desired_identity:
                    raise DifferentPayloadReplayError(
                        "Notion block create intent is immutable after first persistence"
                    )
                connection.execute(
                    """
                    UPDATE note_ai_block
                    SET state='INTENT', reason=NULL, updated_at=?
                    WHERE session_id=?
                    """,
                    (now, session_id),
                )
            return dict(
                connection.execute(
                    "SELECT * FROM note_ai_block WHERE session_id=?", (session_id,)
                ).fetchone()
            )

    def set_block_state(
        self, *, session_id: str, state: str,
        block_id: str | None = None, content_hash: str | None = None, reason: str | None = None,
    ) -> dict[str, Any]:
        if state not in ("INTENT", "UNKNOWN", "KNOWN", "BLOCKED", "NONE"):
            raise ValueError("unsupported AI block state")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM note_ai_block WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            connection.execute(
                """
                UPDATE note_ai_block
                SET state=?, block_id=COALESCE(?, block_id),
                    last_worker_hash=COALESCE(?, last_worker_hash), reason=?, updated_at=?
                WHERE session_id=?
                """,
                (state, block_id, content_hash, reason, now, session_id),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM note_ai_block WHERE session_id=?", (session_id,)
                ).fetchone()
            )


__all__ = [
    "DifferentPayloadReplayError",
    "GrantUnavailableError",
    "StudyNoteStore",
]
