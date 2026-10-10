"""Durable intake classification v2 ledgers (plan §3.4–3.6, §5, §6.3).

Installed additively by :class:`uls.state.sqlite.SQLiteStateStore` next to the
intake preview tables.  Every table here is either an immutable record
(classification records, decisions), a projection rebuilt from complete
observations (recording calendar), or an idempotency ledger whose primary key
makes a repeated step a no-op (request generations, auto-resolve intents,
terminal changes, handovers).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from uls.intake.classification.calendar import (
    CalendarEntry,
    CourseCalendar,
    EntryStatus,
    RecordingEntry,
    build_calendar,
)

CLASSIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS classification_records (
    record_id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL,
    provider_file_id TEXT NOT NULL,
    byte_sha256 TEXT,
    byte_md5 TEXT,
    snapshot_sha256 TEXT,
    observation_id TEXT,
    source_version INTEGER NOT NULL,
    workspace_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    rule_table_version TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    course_key TEXT,
    week INTEGER,
    decided_date TEXT,
    session_mode TEXT,
    session_id TEXT,
    course_basis_json TEXT NOT NULL DEFAULT '{}',
    calendar_projection_revision_hash TEXT,
    sessions_inventory_hash TEXT,
    semester_range_basis_json TEXT,
    classification_revision_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_records_intake
    ON classification_records(intake_id, created_at);
CREATE TABLE IF NOT EXISTS intake_suggestions (
    intake_id TEXT PRIMARY KEY,
    suggested_course_key TEXT,
    suggested_kind TEXT,
    suggested_date TEXT,
    suggested_week INTEGER,
    suggestion_source TEXT,
    suggestion_note TEXT,
    written_to_notion INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_observations (
    origin TEXT NOT NULL,
    canvas_course_id INTEGER NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    observation_revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    module_name TEXT,
    module_week INTEGER,
    item_type TEXT,
    updated_at TEXT,
    observed_at TEXT NOT NULL,
    collection_complete INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (origin, canvas_course_id, resource_kind, resource_id, observation_revision)
);
CREATE TABLE IF NOT EXISTS canvas_classifications (
    observation_fingerprint TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    canvas_course_id INTEGER NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    observation_revision INTEGER NOT NULL,
    kind TEXT,
    decision_json TEXT NOT NULL,
    rule_table_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_drive_bindings (
    drive_file_id TEXT PRIMARY KEY,
    canvas_course_id INTEGER NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    observation_revision INTEGER NOT NULL,
    attachment_id TEXT NOT NULL,
    attachment_filename TEXT NOT NULL,
    attachment_size INTEGER,
    byte_sha256 TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    UNIQUE (canvas_course_id, resource_kind, resource_id, observation_revision, attachment_id)
);
CREATE TABLE IF NOT EXISTS recording_calendar (
    canvas_course_id INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    observation_revision INTEGER NOT NULL,
    course_key TEXT NOT NULL,
    recorded_on TEXT NOT NULL,
    week INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (canvas_course_id, resource_id, observation_revision)
);
CREATE TABLE IF NOT EXISTS recording_calendar_current (
    canvas_course_id INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    course_key TEXT NOT NULL,
    recorded_on TEXT NOT NULL,
    week INTEGER NOT NULL,
    observation_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    collection_complete INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (canvas_course_id, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_recording_calendar_current_course_date
    ON recording_calendar_current(course_key, recorded_on);
CREATE TABLE IF NOT EXISTS chunk_tag_decisions (
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    source TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    source_fingerprint TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    question_set_version TEXT NOT NULL,
    tag_rule_version TEXT NOT NULL,
    s4_payload_policy_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (
        doc_id, chunk_id, question_id, source_fingerprint, normalization_version,
        tag_vocab_version, question_set_version, tag_rule_version,
        s4_payload_policy_version, prompt_version, model
    )
);
CREATE TABLE IF NOT EXISTS chunk_tag_attempts (
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    source TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    source_fingerprint TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    question_set_version TEXT NOT NULL,
    tag_rule_version TEXT NOT NULL,
    s4_payload_policy_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_tag_manifests (
    doc_id TEXT PRIMARY KEY,
    source_fingerprint TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    question_set_version TEXT NOT NULL,
    tag_rule_version TEXT NOT NULL,
    s4_payload_policy_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    chunk_ids_hash TEXT NOT NULL,
    chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    question_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_tag_bases (
    doc_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    tag_vocab_version TEXT NOT NULL,
    question_set_version TEXT NOT NULL,
    tag_rule_version TEXT NOT NULL,
    s4_payload_policy_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    chunk_ids_json TEXT NOT NULL,
    question_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        doc_id, source_fingerprint, normalization_version, tag_vocab_version,
        question_set_version, tag_rule_version, s4_payload_policy_version, prompt_version, model
    )
);
CREATE TABLE IF NOT EXISTS ledger_markers (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_ai_kind_backfill (
    page_id TEXT PRIMARY KEY,
    material_type TEXT,
    applied_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_request_generations (
    intake_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    generation INTEGER NOT NULL,
    request_key TEXT,
    superseded_request_key TEXT,
    derivation_version TEXT NOT NULL DEFAULT 'V2',
    created_at TEXT NOT NULL,
    PRIMARY KEY (intake_id, request_type, generation)
);
CREATE INDEX IF NOT EXISTS idx_intake_request_generations_key
    ON intake_request_generations(request_key);
CREATE TABLE IF NOT EXISTS auto_resolve_intents (
    intent_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    expected_user_snapshot_hash TEXT NOT NULL,
    pre_close_snapshot_hash TEXT,
    terminal_snapshot_hash TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (request_key, record_id)
);
CREATE TABLE IF NOT EXISTS auto_resolve_rollbacks (
    intent_id TEXT PRIMARY KEY REFERENCES auto_resolve_intents(intent_id),
    target_status TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recording_calendar_courses (
    course_key TEXT PRIMARY KEY,
    canvas_course_id INTEGER NOT NULL,
    collection_complete INTEGER NOT NULL DEFAULT 0,
    projection_revision_hash TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminal_change_ledger (
    original_receipt_id TEXT NOT NULL,
    changed_user_snapshot_hash TEXT NOT NULL,
    created_generation INTEGER,
    created_request_key TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (original_receipt_id, changed_user_snapshot_hash)
);
CREATE TABLE IF NOT EXISTS handover_records (
    from_plan_revision TEXT NOT NULL,
    to_plan_revision TEXT NOT NULL,
    adopted_effects_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_plan_revision, to_plan_revision)
);
"""

AUTO_RESOLVE_STATES = frozenset({"PENDING", "DONE", "ABORTED", "RECONCILE"})
CHUNK_TAG_OPTIONS = ("yes", "no")
CHUNK_TAG_DECISIONS = frozenset({*CHUNK_TAG_OPTIONS, "unresolved"})
CHUNK_TAG_SOURCES = frozenset({"rule", "model"})
REQUIRED_TAG_QUESTION = "assignment"
MATERIAL_BACKFILL_MARKER = "material_ai_kind_backfill_snapshot"
AUTO_RESOLVABLE_REQUEST_TYPES = frozenset({"ASSIGN_COURSE", "FILE_DETAILS"})
# The File Intake error code owned by an unresolved automatic closure (cleared with its terminal commit).
AUTO_CLOSE_RECONCILE_CODE = "AUTO_RESOLVE_RECONCILE"
# Approved S2/S4 thresholds (user decision 2026-10-10; ClassificationCfg defaults).  A
# model yes/no below them is never a resolved decision (plan §3.2 / §3.6).
MODEL_MIN_CONFIDENCE = 0.80
MODEL_MIN_TOP_PROBABILITY = 0.70


RECORDING_RESOURCE_KIND = "module_item"
_RECORDING_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _recording_observation(item_type: Any, title: Any, resource_kind: Any = RECORDING_RESOURCE_KIND) -> bool:
    """A Canvas *module item* of type ExternalTool whose title is a plain ISO date is a
    recording (plan §2.3/§6.3); any other resource kind never is (r12 R3)."""

    if resource_kind != RECORDING_RESOURCE_KIND or item_type != "ExternalTool" or not isinstance(title, str):
        return False
    stripped = title.strip()
    # Exactly YYYY-MM-DD (plan §2.3): compact "20260917" or ISO-week "2026-W38-4" forms,
    # which date.fromisoformat() would accept, are not recordings (r16 R1).
    if _RECORDING_DATE.fullmatch(stripped) is None:
        return False
    try:
        date.fromisoformat(stripped)
    except ValueError:
        return False
    return True


def _unit_interval(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def _validated_decision_detail(source: str, decision: str, detail: Any) -> dict[str, Any]:
    """Evidence a stored decision must carry (plan §2.2): rule id, or model scores.

    A model decision needs ``confidence``, ``top_probability`` and the full
    ``probabilities`` distribution whose maximum is the top probability.  A
    ``yes``/``no`` is only resolved when both scores reach the approved thresholds;
    below them the only storable decision is ``unresolved`` (P-A r3 #1).
    """

    if source not in CHUNK_TAG_SOURCES:
        raise ValueError("chunk tag decision source must be rule or model")
    payload = dict(detail or {})
    if source == "rule":
        if decision == "unresolved":
            raise ValueError("a rule decision is never unresolved")
        if not isinstance(payload.get("rule_id"), str) or not payload["rule_id"]:
            raise ValueError("a rule decision requires rule_id evidence")
        return payload
    for name in ("confidence", "top_probability"):
        if not _unit_interval(payload.get(name)):
            raise ValueError(f"a model decision requires {name} in [0, 1]")
    probabilities = payload.get("probabilities")
    if (
        not isinstance(probabilities, Mapping)
        or set(probabilities) != set(CHUNK_TAG_OPTIONS)
        or not all(_unit_interval(v) for v in probabilities.values())
    ):
        raise ValueError("a model decision requires probabilities for exactly yes and no")
    if abs(sum(float(v) for v in probabilities.values()) - 1.0) > 1e-6:
        raise ValueError("model probabilities must sum to 1")
    ranked = sorted(probabilities.items(), key=lambda item: float(item[1]), reverse=True)
    if abs(float(ranked[0][1]) - float(payload["top_probability"])) > 1e-9:
        raise ValueError("top_probability must equal the maximum probability")
    unique_top = float(ranked[0][1]) - float(ranked[1][1]) > 1e-9
    # The thresholds are the approved constants; a caller can never lower them
    # (P-A r5 #1).  A configuration change needs a code revision of these values.
    resolved = (
        unique_top
        and payload["confidence"] >= MODEL_MIN_CONFIDENCE
        and payload["top_probability"] >= MODEL_MIN_TOP_PROBABILITY
    )
    if decision != "unresolved":
        if not resolved:
            raise ValueError("a model decision below the approved thresholds must be unresolved")
        if ranked[0][0] != decision:
            # The stored answer must be the model's own top choice (P-A r4 #1).
            raise ValueError("a model decision must equal its top-probability option")
    elif resolved:
        raise ValueError("a model decision above the thresholds is resolved, not unresolved")
    payload["thresholds"] = {
        "min_confidence": MODEL_MIN_CONFIDENCE, "min_top_probability": MODEL_MIN_TOP_PROBABILITY,
    }
    return payload
_AUTO_RESOLVE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"DONE", "ABORTED", "RECONCILE"}),
    "RECONCILE": frozenset({"DONE", "ABORTED"}),
    "DONE": frozenset(),
    "ABORTED": frozenset(),
}
PLAN_AUTHORITIES = ("HUMAN_REQUEST", "AUTO_CLASSIFICATION")
CLASSIFICATION_STATES = ("NONE", "DEFERRED", "CLASSIFIED", "HUMAN")
CALENDAR_ENTRY_STATES = ("CALENDAR", "ANOMALY", "AMBIGUOUS")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_revision_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClassificationRecord:
    record_id: str
    intake_id: str
    provider_file_id: str
    byte_sha256: str | None
    byte_md5: str | None
    snapshot_sha256: str | None
    observation_id: str | None
    source_version: int
    workspace_fingerprint: str
    config_fingerprint: str
    rule_table_version: str
    decision_json: str
    kind: str
    origin: str
    course_key: str | None
    week: int | None
    decided_date: str | None
    session_mode: str | None
    session_id: str | None
    course_basis_json: str
    calendar_projection_revision_hash: str | None
    sessions_inventory_hash: str | None
    semester_range_basis_json: str | None
    classification_revision_hash: str
    created_at: str


@dataclass(frozen=True)
class RecordingCalendarRow:
    canvas_course_id: int
    resource_id: str
    course_key: str
    recorded_on: str
    week: int
    observation_revision: int
    status: str
    collection_complete: bool


@dataclass(frozen=True)
class AutoResolveIntent:
    intent_id: str
    record_id: str
    request_key: str
    expected_user_snapshot_hash: str
    state: str
    created_at: str
    updated_at: str
    pre_close_snapshot_hash: str | None = None
    terminal_snapshot_hash: str | None = None


class ClassificationStateMixin:
    """Methods mixed into :class:`SQLiteStateStore`; relies on its transaction helper."""

    def _transaction(self, *, immediate: bool = False) -> AbstractContextManager[sqlite3.Connection]:
        raise NotImplementedError  # provided by SQLiteStateStore

    # --- classification records ------------------------------------------------

    def create_classification_record(self, **values: Any) -> ClassificationRecord:
        """Insert an immutable record; the same revision hash returns the existing row."""

        required = ("intake_id", "provider_file_id", "source_version", "workspace_fingerprint",
                    "config_fingerprint", "rule_table_version", "decision", "kind", "origin")
        for name in required:
            if values.get(name) in (None, ""):
                raise ValueError(f"classification record requires {name}")
        # Normalize to the exact stored form first so the hash is a function of
        # the row that will be persisted (R15).
        def _opt_text(name: str) -> str | None:
            value = values.get(name)
            return None if value is None else str(value)

        source_version = int(values["source_version"])
        week_value = values.get("week")
        week = None if week_value is None else int(week_value)
        course_basis = dict(values.get("course_basis") or {})
        semester_basis = values.get("semester_range_basis")
        payload: dict[str, Any] = {
            "intake_id": str(values["intake_id"]),
            "provider_file_id": str(values["provider_file_id"]),
            "byte_sha256": _opt_text("byte_sha256"),
            "byte_md5": _opt_text("byte_md5"),
            "snapshot_sha256": _opt_text("snapshot_sha256"),
            "observation_id": _opt_text("observation_id"),
            "source_version": source_version,
            "workspace_fingerprint": str(values["workspace_fingerprint"]),
            "config_fingerprint": str(values["config_fingerprint"]),
            "rule_table_version": str(values["rule_table_version"]),
            "decision": values["decision"],
            "kind": str(values["kind"]),
            "origin": str(values["origin"]),
            "course_key": _opt_text("course_key"),
            "week": week,
            "decided_date": _opt_text("decided_date"),
            "session_mode": _opt_text("session_mode"),
            "session_id": _opt_text("session_id"),
            "course_basis": course_basis,
            "calendar_projection_revision_hash": _opt_text("calendar_projection_revision_hash"),
            "sessions_inventory_hash": _opt_text("sessions_inventory_hash"),
            "semester_range_basis": None if semester_basis is None else dict(semester_basis),
        }
        revision = canonical_revision_hash(payload)
        record_id = values.get("record_id") or f"cls_{uuid4().hex}"
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM classification_records WHERE classification_revision_hash = ?",
                (revision,),
            ).fetchone()
            if existing is not None:
                return ClassificationRecord(**dict(existing))
            connection.execute(
                """
                INSERT INTO classification_records(
                    record_id, intake_id, provider_file_id, byte_sha256, byte_md5, snapshot_sha256,
                    observation_id, source_version, workspace_fingerprint, config_fingerprint,
                    rule_table_version, decision_json, kind, origin, course_key, week,
                    decided_date, session_mode, session_id, course_basis_json,
                    calendar_projection_revision_hash, sessions_inventory_hash,
                    semester_range_basis_json, classification_revision_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id, payload["intake_id"], payload["provider_file_id"],
                    payload["byte_sha256"], payload["byte_md5"], payload["snapshot_sha256"],
                    payload["observation_id"], source_version,
                    payload["workspace_fingerprint"], payload["config_fingerprint"],
                    payload["rule_table_version"], _json(payload["decision"]), payload["kind"],
                    payload["origin"], payload["course_key"], payload["week"],
                    payload["decided_date"], payload["session_mode"], payload["session_id"],
                    _json(payload["course_basis"]),
                    payload["calendar_projection_revision_hash"], payload["sessions_inventory_hash"],
                    None if payload["semester_range_basis"] is None
                    else _json(payload["semester_range_basis"]),
                    revision, _now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM classification_records WHERE record_id = ?", (record_id,)
            ).fetchone()
            return ClassificationRecord(**dict(row))

    def get_classification_record(self, record_id: str) -> ClassificationRecord | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM classification_records WHERE record_id = ?", (record_id,)
            ).fetchone()
            return None if row is None else ClassificationRecord(**dict(row))

    def list_classification_records_by_bytes(self, byte_sha256: str) -> list[ClassificationRecord]:
        """Every record holding these exact bytes (duplicate-content gate, plan §3.4 r6 R2)."""

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM classification_records WHERE byte_sha256 = ? ORDER BY rowid",
                (byte_sha256,),
            ).fetchall()
        return [ClassificationRecord(**dict(row)) for row in rows]

    def list_intake_plans(self, *, plan_authority: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM intake_plans", []
        clauses = []
        if plan_authority is not None:
            clauses.append("plan_authority = ?"); params.append(plan_authority)
        if status is not None:
            clauses.append("status = ?"); params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._transaction() as connection:
            return [dict(r) for r in connection.execute(query + " ORDER BY created_at, plan_id", params).fetchall()]

    def supersede_intake_plan(self, plan_revision: str, reason: str) -> int:
        """Close an AUTO plan a human overtook: status SUPERSEDED and its jobs VOID (plan §3.4).

        The status change and the job VOID transition commit in one transaction, and a
        plan that is already closed still voids any job left behind, so a crash between
        the two can never leave a runnable job of a dead plan (P-B2a r1 R8).
        """

        return self._close_auto_plan(plan_revision, "SUPERSEDED", reason)

    def reconcile_intake_plan(self, plan_revision: str, reason: str) -> int:
        """Park an AUTO plan whose preflight failed (plan §3.5): RECONCILE_REQUIRED, jobs VOID."""

        return self._close_auto_plan(plan_revision, "RECONCILE_REQUIRED", reason)

    def _close_auto_plan(self, plan_revision: str, status: str, reason: str) -> int:
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE intake_plans SET status = ? WHERE plan_revision = ? "
                "AND plan_authority = 'AUTO_CLASSIFICATION' AND status IN ('AUTO_PENDING', 'PLANNED')",
                (status, plan_revision),
            )
            changed = cursor.rowcount
            row = connection.execute(
                "SELECT status FROM intake_plans WHERE plan_revision = ? "
                "AND plan_authority = 'AUTO_CLASSIFICATION' ORDER BY created_at DESC LIMIT 1",
                (plan_revision,),
            ).fetchone()
            if row is not None and row["status"] in ("SUPERSEDED", "RECONCILE_REQUIRED"):
                self._void_plan_jobs(connection, plan_revision, reason)
        return int(changed)

    def get_auto_resolve_rollback(self, intent_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM auto_resolve_rollbacks WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def duplicate_content_candidates(
        self, *, intake_id: str, provider: str, byte_sha256: str, byte_md5: str | None, size: int | None
    ) -> dict[str, list[str]]:
        """Plan §3.4 duplicate-content gate, complete over the whole store (P-B2a r1 R1, r2 R1).

        ``proven`` lists other provider file IDs whose bytes are known to equal these
        (an md5 observation, or a classification record proven for the item's *current*
        source version).  ``unproven`` lists every other item whose bytes cannot be
        proven different: no current-version byte proof and no md5, and a declared size
        that is unknown or equal to this payload.  Terminal items (ORGANIZED with a
        canonical binding, UNSUPPORTED) are judged like any other; the caller fails
        closed on anything unproven.
        """

        proven: list[str] = []
        unproven: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT i.intake_id, i.provider_file_id, i.source_hash, i.status, "
                "(SELECT c.byte_sha256 FROM classification_records c WHERE c.intake_id = i.intake_id "
                " AND c.byte_sha256 IS NOT NULL AND c.source_version = i.source_version "
                " AND c.provider_file_id = i.provider_file_id ORDER BY c.rowid DESC LIMIT 1) AS proven_sha, "
                "(SELECT CASE WHEN json_type(o.metadata_json, '$.size') = 'integer' "
                " THEN json_extract(o.metadata_json, '$.size') END FROM intake_observations o "
                " WHERE o.intake_id = i.intake_id AND o.source_version = i.source_version "
                " ORDER BY o.observed_at DESC LIMIT 1) AS size "
                "FROM intake_items i WHERE i.provider = ? AND i.intake_id != ?",
                (provider, intake_id),
            ).fetchall()
        for row in rows:
            file_id = str(row["provider_file_id"])
            source_hash = str(row["source_hash"] or "")
            if row["proven_sha"] == byte_sha256 or (byte_md5 and source_hash == "md5:" + byte_md5):
                proven.append(file_id)
                continue
            if row["proven_sha"] is not None or source_hash.startswith("md5:"):
                continue  # bytes known for the current version and different
            other_size = row["size"]
            if size is None or other_size is None or type(other_size) is not int or other_size < 0 or other_size == size:
                unproven.append(file_id)  # size unknown or equal, bytes unknown: cannot rule out
        return {"proven": sorted(proven), "unproven": sorted(unproven)}

    def list_auto_resolve_intents(self, *, states: Iterable[str] = ("PENDING", "RECONCILE")) -> list[AutoResolveIntent]:
        wanted = tuple(states)
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE state IN (" + ",".join("?" for _ in wanted) + ") "
                "ORDER BY rowid",
                wanted,
            ).fetchall()
        return [AutoResolveIntent(**dict(r)) for r in rows]

    def latest_classification_record(self, intake_id: str) -> ClassificationRecord | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM classification_records WHERE intake_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (intake_id,),
            ).fetchone()
            return None if row is None else ClassificationRecord(**dict(row))

    # --- suggestions (local until the v2 Notion profile is verified) ---------------

    def upsert_intake_suggestion(self, intake_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"suggested_course_key", "suggested_kind", "suggested_date", "suggested_week",
                   "suggestion_source", "suggestion_note", "written_to_notion"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError("unknown suggestion fields: " + ", ".join(sorted(unknown)))
        with self._transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO intake_suggestions(intake_id, updated_at) VALUES (?, ?) "
                "ON CONFLICT(intake_id) DO NOTHING",
                (intake_id, _now()),
            )
            if fields:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                connection.execute(
                    f"UPDATE intake_suggestions SET {assignments}, updated_at = ? WHERE intake_id = ?",
                    [*fields.values(), _now(), intake_id],
                )
            row = connection.execute(
                "SELECT * FROM intake_suggestions WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            return dict(row)

    def classification_counts(self) -> dict[str, int]:
        """Readiness counters (plan §4 O2): items per classification_state and tag manifests."""

        with self._transaction() as connection:
            states = {
                str(row["classification_state"]): int(row["n"])
                for row in connection.execute(
                    "SELECT classification_state, COUNT(*) AS n FROM intake_items "
                    "GROUP BY classification_state"
                ).fetchall()
            }
            manifests = {
                str(row["status"]): int(row["n"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS n FROM document_tag_manifests GROUP BY status"
                ).fetchall()
            }
        return {
            "deferred_items": states.get("DEFERRED", 0),
            "classified_items": states.get("CLASSIFIED", 0),
            "human_fallback_items": states.get("HUMAN", 0),
            "partial_tag_documents": manifests.get("PARTIAL", 0),
            "stale_tag_documents": manifests.get("STALE", 0),
        }

    def get_intake_suggestion(self, intake_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM intake_suggestions WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            return None if row is None else dict(row)

    # --- canvas observations / calendar --------------------------------------------

    def record_canvas_observation(self, **values: Any) -> dict[str, Any]:
        for name in ("origin", "canvas_course_id", "resource_kind", "resource_id",
                     "observation_revision", "title"):
            if values.get(name) in (None, ""):
                raise ValueError(f"canvas observation requires {name}")
        identity = (values["origin"], int(values["canvas_course_id"]), values["resource_kind"],
                    str(values["resource_id"]))
        revision = int(values["observation_revision"])
        complete_flag = values.get("collection_complete", False)
        if type(complete_flag) is not bool:
            # The completeness proof is never derived from truthiness (r11 R3).
            raise ValueError("collection_complete must be a bool")
        payload = (values["title"], values.get("module_name"), values.get("module_week"),
                   values.get("item_type"), values.get("updated_at"))
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM canvas_observations WHERE origin = ? AND canvas_course_id = ? "
                "AND resource_kind = ? AND resource_id = ? AND observation_revision = ?",
                (*identity, revision),
            ).fetchone()
            if existing is not None:
                stored = (existing["title"], existing["module_name"], existing["module_week"],
                          existing["item_type"], existing["updated_at"])
                if stored != payload:
                    raise ValueError("canvas observation revision is immutable")
                # Content and metadata of a revision are immutable, but a verified complete
                # collection may upgrade its completeness proof, and the newest revision of a
                # resource that was deactivated by an earlier complete collection is
                # reactivated when a complete collection lists it again (r11 R2).  An older
                # revision never revives.
                newest_known = connection.execute(
                    "SELECT COALESCE(MAX(observation_revision), 0) AS r FROM canvas_observations "
                    "WHERE origin = ? AND canvas_course_id = ? AND resource_kind = ? AND resource_id = ?",
                    identity,
                ).fetchone()
                changed = False
                if complete_flag and not existing["collection_complete"]:
                    connection.execute(
                        "UPDATE canvas_observations SET collection_complete = 1, observed_at = ? "
                        "WHERE origin = ? AND canvas_course_id = ? AND resource_kind = ? AND resource_id = ? "
                        "AND observation_revision = ?",
                        (values.get("observed_at") or _now(), *identity, revision),
                    )
                    changed = True
                if complete_flag and not existing["active"] and revision == int(newest_known["r"]):
                    connection.execute(
                        "UPDATE canvas_observations SET active = 1 WHERE origin = ? AND canvas_course_id = ? "
                        "AND resource_kind = ? AND resource_id = ? AND observation_revision = ?",
                        (*identity, revision),
                    )
                    changed = True
                    if _recording_observation(values.get("item_type"), values["title"], values["resource_kind"]):
                        connection.execute(
                            "UPDATE recording_calendar_courses SET collection_complete = 0, "
                            "projection_revision_hash = NULL, updated_at = ? WHERE canvas_course_id = ?",
                            (_now(), int(values["canvas_course_id"])),
                        )
                if not changed:
                    return dict(existing)  # idempotent re-observation of a known revision
                refreshed = connection.execute(
                    "SELECT * FROM canvas_observations WHERE origin = ? AND canvas_course_id = ? "
                    "AND resource_kind = ? AND resource_id = ? AND observation_revision = ?",
                    (*identity, revision),
                ).fetchone()
                return dict(refreshed)
            newest = connection.execute(
                "SELECT COALESCE(MAX(observation_revision), 0) AS r FROM canvas_observations "
                "WHERE origin = ? AND canvas_course_id = ? AND resource_kind = ? AND resource_id = ?",
                identity,
            ).fetchone()
            is_latest = revision > int(newest["r"])
            previous = connection.execute(
                "SELECT item_type, title FROM canvas_observations WHERE origin = ? AND canvas_course_id = ? "
                "AND resource_kind = ? AND resource_id = ? AND active = 1",
                identity,
            ).fetchone()
            was_recording = previous is not None and _recording_observation(
                previous["item_type"], previous["title"], values["resource_kind"]
            )
            connection.execute(
                """
                INSERT INTO canvas_observations(
                    origin, canvas_course_id, resource_kind, resource_id, observation_revision,
                    title, module_name, module_week, item_type, updated_at, observed_at,
                    collection_complete, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *identity, revision, *payload,
                    values.get("observed_at") or _now(),
                    1 if complete_flag else 0, 1 if is_latest else 0,
                ),
            )
            if not is_latest:
                # A delayed older revision never revives: the newest stays active.
                row = connection.execute(
                    "SELECT * FROM canvas_observations WHERE origin = ? AND canvas_course_id = ? "
                    "AND resource_kind = ? AND resource_id = ? AND observation_revision = ?",
                    (*identity, revision),
                ).fetchone()
                return dict(row)
            # Older revisions of the same resource are no longer active.
            connection.execute(
                """
                UPDATE canvas_observations SET active = 0
                WHERE origin = ? AND canvas_course_id = ? AND resource_kind = ? AND resource_id = ?
                  AND observation_revision < ?
                """,
                (values["origin"], int(values["canvas_course_id"]), values["resource_kind"],
                 str(values["resource_id"]), int(values["observation_revision"])),
            )
            if was_recording or _recording_observation(values.get("item_type"), values["title"], values["resource_kind"]):
                # A new or changed active recording observation, or a recording that just
                # stopped being one, invalidates the course's completed calendar projection
                # until it is rebuilt (r8 R2 A, r9 R1 A).
                connection.execute(
                    "UPDATE recording_calendar_courses SET collection_complete = 0, "
                    "projection_revision_hash = NULL, updated_at = ? WHERE canvas_course_id = ?",
                    (_now(), int(values["canvas_course_id"])),
                )
            row = connection.execute(
                "SELECT * FROM canvas_observations WHERE origin = ? AND canvas_course_id = ? "
                "AND resource_kind = ? AND resource_id = ? AND observation_revision = ?",
                (values["origin"], int(values["canvas_course_id"]), values["resource_kind"],
                 str(values["resource_id"]), int(values["observation_revision"])),
            ).fetchone()
            return dict(row)

    def reconcile_canvas_collection(
        self, *, origin: str, canvas_course_id: int, resource_kind: str,
        seen_resource_ids: Iterable[str], collection_complete: bool,
    ) -> list[str]:
        """Deactivate active observations that a *complete* collection no longer lists.

        History rows stay; only the active set shrinks, and a deactivated recording
        invalidates the course's completed projection.  An incomplete collection
        never deactivates anything (plan §2.3; P-A r10 R2).  Returns the resource ids
        that were deactivated.
        """

        if type(collection_complete) is not bool:
            raise ValueError("collection_complete must be a bool")
        seen = {str(r) for r in seen_resource_ids if isinstance(r, str) and r}
        if not collection_complete:
            # Nothing is deactivated, but an incomplete collection of a course that has
            # recording observations closes its calendar until a complete one re-verifies
            # it (plan §2.3; r11 R1).
            if resource_kind == RECORDING_RESOURCE_KIND:
                # The absence of recordings proves nothing about the new collection: any
                # completeness proof of the course is withdrawn (r11 R1, r12 R5).
                with self._transaction(immediate=True) as connection:
                    connection.execute(
                        "UPDATE recording_calendar_courses SET collection_complete = 0, "
                        "projection_revision_hash = NULL, updated_at = ? WHERE canvas_course_id = ?",
                        (_now(), int(canvas_course_id)),
                    )
            return []
        deactivated: list[str] = []
        with self._transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT resource_id, item_type, title FROM canvas_observations "
                "WHERE origin = ? AND canvas_course_id = ? AND resource_kind = ? AND active = 1",
                (origin, int(canvas_course_id), resource_kind),
            ).fetchall()
            recording_gone = False
            for row in rows:
                if str(row["resource_id"]) in seen:
                    continue
                connection.execute(
                    "UPDATE canvas_observations SET active = 0 WHERE origin = ? AND canvas_course_id = ? "
                    "AND resource_kind = ? AND resource_id = ? AND active = 1",
                    (origin, int(canvas_course_id), resource_kind, str(row["resource_id"])),
                )
                deactivated.append(str(row["resource_id"]))
                recording_gone = recording_gone or _recording_observation(row["item_type"], row["title"], resource_kind)
            if recording_gone:
                connection.execute(
                    "UPDATE recording_calendar_courses SET collection_complete = 0, "
                    "projection_revision_hash = NULL, updated_at = ? WHERE canvas_course_id = ?",
                    (_now(), int(canvas_course_id)),
                )
        return deactivated

    def list_active_canvas_observations(
        self, canvas_course_id: int, resource_kind: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM canvas_observations WHERE canvas_course_id = ? AND active = 1"
        params: list[Any] = [int(canvas_course_id)]
        if resource_kind is not None:
            query += " AND resource_kind = ?"
            params.append(resource_kind)
        query += " ORDER BY resource_kind, resource_id, observation_revision"
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def replace_recording_calendar_current(
        self,
        course_key: str,
        canvas_course_id: int,
        calendar: CourseCalendar,
        *,
        collection_complete: bool,
    ) -> list[RecordingCalendarRow]:
        """Rebuild the active projection of one course atomically (plan §2.3, r6 R1).

        The projection is only ever the verdict of :func:`build_calendar`: the given
        ``calendar`` is recomputed from its own entries and semester basis and must
        hash identically, so a CALENDAR/ANOMALY/AMBIGUOUS status can never be
        asserted by a caller (r7 R3).  ``collection_complete`` must be a real bool.
        """

        if type(collection_complete) is not bool:
            raise ValueError("collection_complete must be a bool")
        if not isinstance(calendar, CourseCalendar):
            raise ValueError("calendar must be a CourseCalendar")  # noqa: TRY004
        if calendar.course_key != course_key:
            raise ValueError("calendar belongs to another course key")
        if collection_complete and calendar.reason == "COLLECTION_INCOMPLETE":
            raise ValueError("an incomplete calendar cannot be recorded as a complete collection")
        # Validate every entry before touching anything, then apply the batch in one
        # transaction so a bad row never leaves a half-replaced projection (r2 #9).
        normalized: list[tuple[str, str, int, int, str]] = []
        for projected in calendar.entries:
            if not isinstance(projected, CalendarEntry) or not isinstance(projected.status, EntryStatus):
                raise ValueError("calendar entry status is invalid")  # noqa: TRY004
            entry = projected.entry
            if not isinstance(entry, RecordingEntry):
                raise ValueError("calendar entry is malformed")  # noqa: TRY004
            if int(entry.canvas_course_id) != int(canvas_course_id):
                raise ValueError("calendar entry belongs to another Canvas course")
            if not isinstance(entry.resource_id, str) or not entry.resource_id.strip():
                raise ValueError("calendar entry needs a non-empty resource_id")
            if not isinstance(entry.recorded_on, date) or isinstance(entry.recorded_on, datetime):
                raise ValueError("calendar entry needs a date recorded_on")  # noqa: TRY004
            for name, value in (("week", entry.week), ("observation_revision", entry.observation_revision)):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"calendar entry needs a positive integer {name}")
            normalized.append((entry.resource_id, entry.recorded_on.isoformat(), entry.week,
                               entry.observation_revision, projected.status.value))
        if len({r[0] for r in normalized}) != len(normalized):
            raise ValueError("calendar entry rows repeat a resource_id")
        # The statuses must be exactly what the pure projection yields for these entries.
        verified = build_calendar(
            course_key, [p.entry for p in calendar.entries],
            semester=calendar.semester, collection_complete=collection_complete,
        )
        projection_hash = calendar.revision_hash()
        if verified.revision_hash() != projection_hash:
            raise ValueError("calendar statuses do not match the verified projection")
        with self._transaction(immediate=True) as connection:
            bound = connection.execute(
                "SELECT course_key, canvas_course_id FROM recording_calendar_courses "
                "WHERE course_key = ? OR canvas_course_id = ?",
                (course_key, int(canvas_course_id)),
            ).fetchall()
            if any(
                (b["course_key"], int(b["canvas_course_id"])) != (course_key, int(canvas_course_id))
                for b in bound
            ):
                # A course key and a Canvas course id bind once; a different pairing is
                # refused instead of mixing projections (P-A r4 #4).
                raise ValueError("course_key / canvas_course_id binding differs from the recorded one")
            connection.execute(
                "INSERT INTO recording_calendar_courses(course_key, canvas_course_id, "
                "collection_complete, projection_revision_hash, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(course_key) DO UPDATE SET "
                "collection_complete = excluded.collection_complete, "
                "projection_revision_hash = excluded.projection_revision_hash, "
                "updated_at = excluded.updated_at",
                (course_key, int(canvas_course_id), 1 if collection_complete else 0,
                 projection_hash if collection_complete else None, _now()),
            )
            if collection_complete:
                # The completed projection must agree with every persisted active
                # recording observation of the course: each one present with the same
                # revision, date and week, none contradicted (r8 R2 B).
                by_resource = {r[0]: r for r in normalized}
                active_recordings: set[str] = set()
                for observed in connection.execute(
                    "SELECT resource_id, observation_revision, title, module_week, item_type, resource_kind, "
                    "collection_complete FROM canvas_observations WHERE canvas_course_id = ? AND active = 1",
                    (int(canvas_course_id),),
                ).fetchall():
                    if not _recording_observation(observed["item_type"], observed["title"], observed["resource_kind"]):
                        continue
                    active_recordings.add(str(observed["resource_id"]))
                    projected_row = by_resource.get(str(observed["resource_id"]))
                    if projected_row is None:
                        raise ValueError("completed projection misses an active recording observation")
                    if projected_row[3] != int(observed["observation_revision"]):
                        raise ValueError("calendar entry revision conflicts with the active observation")
                    if projected_row[1] != str(observed["title"]).strip():
                        raise ValueError("calendar entry date contradicts the active observation")
                    week_evidence = observed["module_week"]
                    if (
                        isinstance(week_evidence, bool) or not isinstance(week_evidence, int)
                        or week_evidence < 1
                    ):
                        # No explicit week on the observation: the week can never be
                        # guessed into a completed projection (r10 R1).
                        raise ValueError("active recording observation has no verified week")
                    if projected_row[2] != int(week_evidence):
                        raise ValueError("calendar entry week contradicts the active observation")
                    if not observed["collection_complete"]:
                        # Only observations proven by a complete collection back a
                        # completed projection (r10 R3).
                        raise ValueError("active recording observation came from an incomplete collection")
                unobserved = set(by_resource) - active_recordings
                if unobserved:
                    # The projection is built from active recording observations only: a
                    # row without one has no evidence (r9 R1 B).
                    raise ValueError("projection rows without an active recording observation: "
                                     + ", ".join(sorted(unobserved)))
                # An incomplete collection never replaces the active projection; the
                # course is only marked incomplete so matching stays AMBIGUOUS (R13).
                connection.execute(
                    "DELETE FROM recording_calendar_current WHERE canvas_course_id = ?",
                    (int(canvas_course_id),),
                )
                for resource_id, recorded_on, week, revision, status in normalized:
                    # The projection must carry the newest known revision of each resource:
                    # an older or unknown-but-stale revision is refused (r6 R3).
                    newest = connection.execute(
                        "SELECT MAX(r) AS r FROM ("
                        "SELECT MAX(observation_revision) AS r FROM canvas_observations "
                        "WHERE canvas_course_id = ? AND resource_id = ? AND active = 1 "
                        "UNION ALL SELECT MAX(observation_revision) FROM recording_calendar "
                        "WHERE canvas_course_id = ? AND resource_id = ?)",
                        (int(canvas_course_id), resource_id, int(canvas_course_id), resource_id),
                    ).fetchone()
                    if newest is not None and newest["r"] is not None and revision < int(newest["r"]):
                        raise ValueError("calendar entry revision is older than the active observation")
                    active = connection.execute(
                        "SELECT observation_revision FROM canvas_observations "
                        "WHERE canvas_course_id = ? AND resource_id = ? AND active = 1",
                        (int(canvas_course_id), resource_id),
                    ).fetchall()
                    if active and all(int(a["observation_revision"]) != revision for a in active):
                        raise ValueError("calendar entry revision conflicts with the active observation")
                    history = connection.execute(
                        "SELECT course_key, recorded_on, week FROM recording_calendar "
                        "WHERE canvas_course_id = ? AND resource_id = ? AND observation_revision = ?",
                        (int(canvas_course_id), resource_id, revision),
                    ).fetchone()
                    if history is not None and (
                        history["course_key"], history["recorded_on"], history["week"]
                    ) != (course_key, recorded_on, week):
                        # The immutable history already fixed this observation revision
                        # differently; the whole replacement rolls back (r3 #5).
                        raise ValueError("calendar history conflict for an observation revision")
                    connection.execute(
                        """
                        INSERT INTO recording_calendar_current(
                            canvas_course_id, resource_id, course_key, recorded_on, week,
                            observation_revision, status, collection_complete
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (int(canvas_course_id), resource_id, course_key, recorded_on, week,
                         revision, status),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO recording_calendar(
                            canvas_course_id, resource_id, observation_revision, course_key,
                            recorded_on, week, observed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (int(canvas_course_id), resource_id, revision, course_key, recorded_on,
                         week, _now()),
                    )
        return self.list_recording_calendar_current(course_key)

    def recording_calendar_course(self, course_key: str) -> dict[str, Any] | None:
        """The course's collection row (None when no collection was ever recorded)."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM recording_calendar_courses WHERE course_key = ?", (course_key,)
            ).fetchone()
            return None if row is None else dict(row)

    def recording_calendar_projection_hash(self, course_key: str) -> str | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT projection_revision_hash FROM recording_calendar_courses WHERE course_key = ?",
                (course_key,),
            ).fetchone()
            return None if row is None else row["projection_revision_hash"]

    def recording_calendar_complete(self, course_key: str) -> bool:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT collection_complete FROM recording_calendar_courses WHERE course_key = ?",
                (course_key,),
            ).fetchone()
            return bool(row and row["collection_complete"])

    def list_recording_calendar_current(self, course_key: str) -> list[RecordingCalendarRow]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM recording_calendar_current WHERE course_key = ? "
                "ORDER BY week, recorded_on, resource_id",
                (course_key,),
            ).fetchall()
            course = connection.execute(
                "SELECT collection_complete FROM recording_calendar_courses WHERE course_key = ?",
                (course_key,),
            ).fetchone()
        # Row-level completeness mirrors the authoritative course flag (r7 O1).
        complete = bool(course and course["collection_complete"])
        return [
            RecordingCalendarRow(
                int(r["canvas_course_id"]), str(r["resource_id"]), str(r["course_key"]),
                str(r["recorded_on"]), int(r["week"]), int(r["observation_revision"]),
                str(r["status"]), complete,
            )
            for r in rows
        ]

    # --- canvas → drive provenance ----------------------------------------------

    def record_canvas_drive_binding(self, **values: Any) -> dict[str, Any]:
        for name in ("drive_file_id", "canvas_course_id", "resource_kind", "resource_id",
                     "observation_revision", "attachment_id", "attachment_filename", "byte_sha256"):
            if values.get(name) in (None, ""):
                raise ValueError(f"canvas drive binding requires {name}")
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM canvas_drive_bindings WHERE drive_file_id = ?",
                (values["drive_file_id"],),
            ).fetchone()
            if existing is not None:
                same = all(
                    str(existing[name]) == str(values[name]) for name in (
                        "canvas_course_id", "resource_kind", "resource_id", "observation_revision",
                        "attachment_id", "byte_sha256", "attachment_filename",
                    )
                ) and (existing["attachment_size"] == values.get("attachment_size"))
                if not same:
                    raise ValueError("drive file is already bound to another Canvas attachment")
                return dict(existing)
            connection.execute(
                """
                INSERT INTO canvas_drive_bindings(
                    drive_file_id, canvas_course_id, resource_kind, resource_id,
                    observation_revision, attachment_id, attachment_filename, attachment_size,
                    byte_sha256, bound_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["drive_file_id"], int(values["canvas_course_id"]), values["resource_kind"],
                    str(values["resource_id"]), int(values["observation_revision"]),
                    str(values["attachment_id"]), values["attachment_filename"],
                    values.get("attachment_size"), values["byte_sha256"], _now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM canvas_drive_bindings WHERE drive_file_id = ?",
                (values["drive_file_id"],),
            ).fetchone()
            return dict(row)

    def session_source_binding_for(self, course_key: str, session_id: str) -> dict[str, Any] | None:
        """The canonical source binding of a Session, when one exists (occupancy proof)."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM session_source_bindings WHERE course_key = ? AND session_id = ?",
                (course_key, session_id),
            ).fetchone()
            return None if row is None else dict(row)

    def get_canvas_drive_binding(self, drive_file_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM canvas_drive_bindings WHERE drive_file_id = ?", (drive_file_id,)
            ).fetchone()
            return None if row is None else dict(row)

    # --- request generations ------------------------------------------------------

    def reserve_request_generation(
        self, intake_id: str, request_type: str, *, superseded_request_key: str | None = None
    ) -> int:
        """Persist the next generation number before any provider create (r8 R1)."""

        with self._transaction(immediate=True) as connection:
            # One supersession basis owns exactly one generation: a retry after the
            # key was bound (lost provider response) and a retry before it both come
            # back to the same number (R9, P-A r2 #5).
            same_basis = connection.execute(
                "SELECT generation FROM intake_request_generations "
                "WHERE intake_id = ? AND request_type = ? AND superseded_request_key IS ? ",
                (intake_id, request_type, superseded_request_key),
            ).fetchone()
            if same_basis is not None:
                return int(same_basis["generation"])
            pending = connection.execute(
                "SELECT generation, superseded_request_key FROM intake_request_generations "
                "WHERE intake_id = ? AND request_type = ? AND request_key IS NULL "
                "ORDER BY generation DESC LIMIT 1",
                (intake_id, request_type),
            ).fetchone()
            if pending is not None:
                # A reservation whose create never completed blocks a new basis: the
                # caller must bind or reconcile it first, never skip a number.
                raise ValueError("pending request generation has a different supersession basis")
            if superseded_request_key is not None:
                latest = connection.execute(
                    "SELECT generation, request_key FROM intake_request_generations "
                    "WHERE intake_id = ? AND request_type = ? ORDER BY generation DESC LIMIT 1",
                    (intake_id, request_type),
                ).fetchone()
                if latest is None or latest["request_key"] != superseded_request_key:
                    raise ValueError("a new generation may only supersede the latest bound request key")
            row = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) AS g FROM intake_request_generations "
                "WHERE intake_id = ? AND request_type = ?",
                (intake_id, request_type),
            ).fetchone()
            generation = int(row["g"]) + 1
            if superseded_request_key is None and generation > 1:
                raise ValueError("a superseding generation must name the superseded request key")
            connection.execute(
                "INSERT INTO intake_request_generations(intake_id, request_type, generation, "
                "request_key, superseded_request_key, derivation_version, created_at) "
                "VALUES (?, ?, ?, NULL, ?, 'V2', ?)",
                (intake_id, request_type, generation, superseded_request_key, _now()),
            )
            return generation

    def bind_request_generation_key(
        self, intake_id: str, request_type: str, generation: int, request_key: str
    ) -> None:
        with self._transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT request_key, superseded_request_key FROM intake_request_generations "
                "WHERE intake_id = ? AND request_type = ? AND generation = ?",
                (intake_id, request_type, int(generation)),
            ).fetchone()
            if current is None:
                raise KeyError((intake_id, request_type, generation))
            if current["request_key"] not in (None, request_key):
                raise ValueError("request generation is already bound to another key")
            if current["request_key"] == request_key:
                return  # idempotent re-bind
            # Every generation carries a new key: never the superseded one nor any key
            # of another generation of this (intake, type).  FILE_DETAILS keys are
            # single-intake; ASSIGN_COURSE keys may legitimately span intakes (r3 #4).
            others = connection.execute(
                "SELECT intake_id, request_type, generation FROM intake_request_generations "
                "WHERE request_key = ? AND NOT (intake_id = ? AND request_type = ? AND generation = ?)",
                (request_key, intake_id, request_type, int(generation)),
            ).fetchall()
            if any(
                (o["intake_id"], o["request_type"]) == (intake_id, request_type) for o in others
            ):
                raise ValueError("request key is already bound to another generation of this intake")
            if others and request_type != "ASSIGN_COURSE":
                raise ValueError(f"{request_type} request keys are single-intake")
            if any(o["request_type"] != request_type for o in others):
                raise ValueError("request key is bound to another request type")
            if current["superseded_request_key"] == request_key:
                raise ValueError("a superseding generation needs a new request key")
            connection.execute(
                "UPDATE intake_request_generations SET request_key = ? "
                "WHERE intake_id = ? AND request_type = ? AND generation = ?",
                (request_key, intake_id, request_type, int(generation)),
            )

    def get_request_generation(self, request_key: str, intake_id: str | None = None) -> dict[str, Any] | None:
        """One HUMAN request may cover several intakes (ASSIGN_COURSE); pass ``intake_id`` to pick one."""

        with self._transaction() as connection:
            if intake_id is None:
                row = connection.execute(
                    "SELECT * FROM intake_request_generations WHERE request_key = ? "
                    "ORDER BY intake_id LIMIT 1",
                    (request_key,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM intake_request_generations WHERE request_key = ? AND intake_id = ?",
                    (request_key, intake_id),
                ).fetchone()
            return None if row is None else dict(row)

    def list_request_generations(self, request_key: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM intake_request_generations WHERE request_key = ? ORDER BY intake_id",
                (request_key,),
            ).fetchall()]

    def pending_request_generation(self, intake_id: str, request_type: str) -> int | None:
        """A reserved generation whose key was never bound (create response lost)."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT generation FROM intake_request_generations WHERE intake_id = ? "
                "AND request_type = ? AND request_key IS NULL ORDER BY generation DESC LIMIT 1",
                (intake_id, request_type),
            ).fetchone()
            return None if row is None else int(row["generation"])

    # --- auto resolve intents -------------------------------------------------------

    @staticmethod
    def _require_closure_binding(
        connection: sqlite3.Connection, *, record_id: str, request_key: str, receipt_required: bool
    ) -> None:
        """The record must exist and the receipt (when present) must be a single-intake
        Draft of that record's intake (P-A r5 #3, r6 R1).  Re-checked inside the DONE
        transaction so a receipt that appeared later cannot bypass the create-time check."""

        record = connection.execute(
            "SELECT intake_id FROM classification_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        if record is None:
            raise ValueError("auto resolve needs an existing classification record")
        receipt = connection.execute(
            "SELECT state, request_type, intake_ids_json FROM request_receipts WHERE request_key = ?",
            (request_key,),
        ).fetchone()
        if receipt is None:
            if receipt_required:
                raise KeyError(request_key)
            return
        if receipt["request_type"] not in AUTO_RESOLVABLE_REQUEST_TYPES:
            # Only the upload-intake request kinds of plan §3.5 ever close automatically;
            # USAGE_RANGE or unknown HUMAN requests are never touched (r17 R1).
            raise ValueError(f"auto resolve never closes a {receipt['request_type']!r} request")
        if receipt["state"] != "Draft":
            raise ValueError(f"auto resolve needs a Draft receipt, not {receipt['state']}")
        try:
            intake_ids = json.loads(receipt["intake_ids_json"] or "[]")
        except ValueError:
            intake_ids = None
        if not isinstance(intake_ids, list) or len(intake_ids) != 1:
            raise ValueError("auto resolve covers single-intake requests only")
        if record["intake_id"] != intake_ids[0]:
            raise ValueError("classification record belongs to another intake than the request")

    @staticmethod
    def _clear_closure_mark(connection: sqlite3.Connection, record_id: str) -> None:
        """Inside the terminal transaction: drop the closure-owned reconcile mark of the
        record's intake unless another live intent of that intake still needs it.  An
        independent reconcile cause (any other error code) is never touched."""

        record = connection.execute(
            "SELECT intake_id FROM classification_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        if record is None:
            return
        live = connection.execute(
            "SELECT 1 FROM auto_resolve_intents i JOIN classification_records c ON c.record_id = i.record_id "
            "WHERE c.intake_id = ? AND i.state IN ('PENDING', 'RECONCILE')",
            (record["intake_id"],),
        ).fetchone()
        if live is None:
            connection.execute(
                "UPDATE intake_items SET status = 'NEEDS_INPUT', last_error_code = NULL, last_error = NULL "
                "WHERE intake_id = ? AND status = 'RECONCILE_REQUIRED' AND last_error_code = ?",
                (record["intake_id"], AUTO_CLOSE_RECONCILE_CODE),
            )

    def abort_auto_resolve_intent_superseding(self, intent_id: str, reason: str) -> AutoResolveIntent:
        """§3.5 (ii): plan SUPERSEDED + its jobs VOID + item HUMAN + intent ABORTED + the
        closure's reconcile mark, all in ONE transaction, so no crash can leave a closed
        intent next to a live AUTO plan (r8 R1).  Idempotent for an already ABORTED intent."""

        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            if row is None:
                raise KeyError(intent_id)
            if row["state"] not in ("PENDING", "RECONCILE", "ABORTED"):
                raise ValueError(f"auto resolve intent cannot be aborted from {row['state']}")
            record = connection.execute(
                "SELECT intake_id, classification_revision_hash FROM classification_records WHERE record_id = ?",
                (row["record_id"],),
            ).fetchone()
            if record is not None:
                plans = connection.execute(
                    "SELECT plan_revision FROM intake_plans WHERE plan_authority = 'AUTO_CLASSIFICATION' "
                    "AND classification_revision_hash = ? AND status IN ('AUTO_PENDING', 'PLANNED')",
                    (record["classification_revision_hash"],),
                ).fetchall()
                for plan in plans:
                    connection.execute(
                        "UPDATE intake_plans SET status = 'SUPERSEDED' WHERE plan_revision = ?",
                        (plan["plan_revision"],),
                    )
                    self._void_plan_jobs(connection, plan["plan_revision"], reason)
                connection.execute(
                    "UPDATE intake_items SET classification_state = 'HUMAN' WHERE intake_id = ?",
                    (record["intake_id"],),
                )
            if row["state"] != "ABORTED":
                connection.execute(
                    "UPDATE auto_resolve_intents SET state = 'ABORTED', updated_at = ? WHERE intent_id = ?",
                    (_now(), intent_id),
                )
            self._clear_closure_mark(connection, row["record_id"])
            refreshed = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return AutoResolveIntent(**dict(refreshed))

    def create_auto_resolve_intent(
        self, *, record_id: str, request_key: str, expected_user_snapshot_hash: str,
        pre_close_snapshot_hash: str | None = None,
    ) -> AutoResolveIntent:
        """Durable PENDING intent; the pre-close snapshot (when given) is written in the
        same transaction so no crash window separates the two (P-B2a r2 O1)."""

        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE request_key = ? AND record_id = ?",
                (request_key, record_id),
            ).fetchone()
            if existing is not None:
                if existing["expected_user_snapshot_hash"] != expected_user_snapshot_hash:
                    raise ValueError("auto resolve intent inputs are immutable")
                if pre_close_snapshot_hash is not None and existing["pre_close_snapshot_hash"] not in (None, pre_close_snapshot_hash):
                    raise ValueError("pre_close_snapshot_hash is immutable once recorded")
                if existing["state"] in ("PENDING", "RECONCILE"):
                    # A live intent is only re-read while its binding still holds: a
                    # receipt a human moved to Submitted/Claimed or a terminal state is
                    # never a normal re-entry (r4 #3, r7 R1).
                    self._require_closure_binding(
                        connection, record_id=record_id, request_key=request_key, receipt_required=False
                    )
                return AutoResolveIntent(**dict(existing))
            active = connection.execute(
                "SELECT intent_id FROM auto_resolve_intents WHERE request_key = ? "
                "AND state IN ('PENDING', 'RECONCILE', 'DONE')",
                (request_key,),
            ).fetchone()
            if active is not None:
                # One request has at most one live closure intent, and a closed request
                # never gets another (P-A r2 #7, r3 #3).
                raise ValueError("a live or finished auto resolve intent already exists for this request")
            self._require_closure_binding(
                connection, record_id=record_id, request_key=request_key, receipt_required=False
            )
            intent_id = f"ari_{uuid4().hex}"
            now = _now()
            connection.execute(
                "INSERT INTO auto_resolve_intents(intent_id, record_id, request_key, "
                "expected_user_snapshot_hash, pre_close_snapshot_hash, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (intent_id, record_id, request_key, expected_user_snapshot_hash, pre_close_snapshot_hash, now, now),
            )
            row = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return AutoResolveIntent(**dict(row))

    def get_auto_resolve_intent(self, request_key: str) -> AutoResolveIntent | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE request_key = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (request_key,),
            ).fetchone()
            return None if row is None else AutoResolveIntent(**dict(row))

    def record_auto_resolve_snapshots(
        self, intent_id: str, *, pre_close_snapshot_hash: str | None = None,
        terminal_snapshot_hash: str | None = None,
    ) -> AutoResolveIntent:
        """Persist the Draft-time and the post-readback snapshots (r10 R1: distinct baselines)."""

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            for column, value in (("pre_close_snapshot_hash", pre_close_snapshot_hash),
                                  ("terminal_snapshot_hash", terminal_snapshot_hash)):
                if value is None:
                    continue
                if row[column] not in (None, value):
                    raise ValueError(f"{column} is immutable once recorded")
                connection.execute(
                    f"UPDATE auto_resolve_intents SET {column} = ?, updated_at = ? WHERE intent_id = ?",
                    (value, _now(), intent_id),
                )
            refreshed = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return AutoResolveIntent(**dict(refreshed))

    def transition_auto_resolve_intent(
        self, intent_id: str, state: str, *, terminal_snapshot_hash: str | None = None
    ) -> AutoResolveIntent:
        """Move an intent.  ``DONE`` is only reachable together with the receipt flip
        Draft → AutoResolved in the same conditional transaction (r9 R2, P-A R11)."""

        if state not in AUTO_RESOLVE_STATES:
            raise ValueError("unknown auto resolve state")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = str(row["state"])
            if state != current and state not in _AUTO_RESOLVE_TRANSITIONS[current]:
                raise ValueError(f"auto resolve intent cannot move {current} -> {state}")
            if state == "DONE" and current == "DONE":
                # Idempotent only for the same terminal readback and a receipt that is
                # still AutoResolved; a contradicting readback is a fixed conflict (r5 #2).
                if terminal_snapshot_hash is not None and terminal_snapshot_hash != row["terminal_snapshot_hash"]:
                    raise ValueError("terminal_snapshot_hash conflicts with the recorded closure")
                receipt = connection.execute(
                    "SELECT state FROM request_receipts WHERE request_key = ?", (row["request_key"],)
                ).fetchone()
                if receipt is None or receipt["state"] != "AutoResolved":
                    raise ValueError("closed intent re-read without an AutoResolved receipt")
                return AutoResolveIntent(**dict(row))
            if state == "DONE" and current != "DONE":
                if terminal_snapshot_hash is None and row["terminal_snapshot_hash"] is None:
                    raise ValueError("DONE requires the terminal readback snapshot")
                if (
                    terminal_snapshot_hash is not None
                    and row["terminal_snapshot_hash"] not in (None, terminal_snapshot_hash)
                ):
                    raise ValueError("terminal_snapshot_hash is immutable once recorded")
                self._require_closure_binding(
                    connection, record_id=row["record_id"], request_key=row["request_key"],
                    receipt_required=True,
                )
                updated = connection.execute(
                    "UPDATE request_receipts SET state = 'AutoResolved' "
                    "WHERE request_key = ? AND state = 'Draft'",
                    (row["request_key"],),
                )
                if updated.rowcount != 1:
                    raise KeyError(f"receipt {row['request_key']} is not a Draft")
                if terminal_snapshot_hash is not None:
                    connection.execute(
                        "UPDATE auto_resolve_intents SET terminal_snapshot_hash = ? WHERE intent_id = ?",
                        (terminal_snapshot_hash, intent_id),
                    )
            connection.execute(
                "UPDATE auto_resolve_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (state, _now(), intent_id),
            )
            if state in ("DONE", "ABORTED"):
                self._clear_closure_mark(connection, row["record_id"])
            refreshed = connection.execute(
                "SELECT * FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return AutoResolveIntent(**dict(refreshed))

    def create_auto_resolve_rollback(self, intent_id: str, target_status: str) -> dict[str, Any]:
        """Idempotent rollback intent for §3.5 (iii): Notion already Auto Resolved, human changed."""

        if target_status not in ("Draft", "Submitted"):
            raise ValueError("rollback target must be Draft or Submitted")
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM auto_resolve_rollbacks WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if existing is not None:
                if existing["target_status"] != target_status:
                    raise ValueError("rollback target is immutable for this intent")
                return dict(existing)
            self._require_rollback_eligible(connection, intent_id)
            connection.execute(
                "INSERT INTO auto_resolve_rollbacks(intent_id, target_status, state, "
                "created_at, updated_at) VALUES (?, ?, 'PENDING', ?, ?)",
                (intent_id, target_status, _now(), _now()),
            )
            row = connection.execute(
                "SELECT * FROM auto_resolve_rollbacks WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row)

    def complete_auto_resolve_rollback(self, intent_id: str) -> dict[str, Any]:
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM auto_resolve_rollbacks WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(intent_id)
            if existing["state"] == "DONE":
                return dict(existing)  # idempotent: the confirmed completion
            self._require_rollback_eligible(connection, intent_id)
            connection.execute(
                "UPDATE auto_resolve_rollbacks SET state = 'DONE', updated_at = ? "
                "WHERE intent_id = ? AND state = 'PENDING'",
                (_now(), intent_id),
            )
            connection.execute(
                "UPDATE auto_resolve_intents SET state = 'ABORTED', updated_at = ? "
                "WHERE intent_id = ? AND state IN ('PENDING', 'RECONCILE')",
                (_now(), intent_id),
            )
            intent_row = connection.execute(
                "SELECT record_id FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if intent_row is not None:
                self._clear_closure_mark(connection, intent_row["record_id"])
            row = connection.execute(
                "SELECT * FROM auto_resolve_rollbacks WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row)

    @staticmethod
    def _require_rollback_eligible(connection: sqlite3.Connection, intent_id: str) -> None:
        """A rollback only exists for a live intent whose local receipt is still a Draft (r3 #3)."""

        intent = connection.execute(
            "SELECT state, request_key FROM auto_resolve_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if intent is None:
            raise KeyError(intent_id)
        if intent["state"] not in ("PENDING", "RECONCILE"):
            raise ValueError("rollback needs a live (PENDING/RECONCILE) auto resolve intent")
        receipt = connection.execute(
            "SELECT state FROM request_receipts WHERE request_key = ?", (intent["request_key"],)
        ).fetchone()
        if receipt is None or receipt["state"] != "Draft":
            # No receipt means the Draft observation was never reconciled locally;
            # a rollback has nothing verified to restore (P-A r4 #3).
            raise ValueError("rollback needs an existing local Draft receipt")

    # --- Materials AI Kind backfill target set (plan §5 migration; r8 R4) ------------

    def material_backfill_targets(self) -> list[dict[str, Any]] | None:
        """The fixed pre-v2 Material set, or None when it was never snapshotted."""

        with self._transaction() as connection:
            marker = connection.execute(
                "SELECT 1 FROM ledger_markers WHERE name = ?", (MATERIAL_BACKFILL_MARKER,)
            ).fetchone()
            if marker is None:
                return None  # never initialised: distinct from an empty target set (r9 R3)
            rows = connection.execute(
                "SELECT * FROM material_ai_kind_backfill ORDER BY page_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def snapshot_material_backfill_targets(self, materials: Iterable[tuple[str, str | None]]) -> int:
        """Record the backfill target set exactly once; later rows are never added."""

        with self._transaction(immediate=True) as connection:
            if connection.execute(
                "SELECT 1 FROM ledger_markers WHERE name = ?", (MATERIAL_BACKFILL_MARKER,)
            ).fetchone():
                raise ValueError("material backfill targets are already recorded")
            connection.execute(
                "INSERT INTO ledger_markers(name, value, created_at) VALUES (?, 'snapshot', ?)",
                (MATERIAL_BACKFILL_MARKER, _now()),
            )
            count = 0
            for page_id, material_type in materials:
                if not isinstance(page_id, str) or not page_id:
                    continue
                connection.execute(
                    "INSERT INTO material_ai_kind_backfill(page_id, material_type, applied_at, "
                    "created_at) VALUES (?, ?, NULL, ?)",
                    (page_id, material_type, _now()),
                )
                count += 1
            return count

    def mark_material_backfill_applied(self, page_id: str) -> None:
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE material_ai_kind_backfill SET applied_at = ? WHERE page_id = ? "
                "AND applied_at IS NULL",
                (_now(), page_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(page_id)

    # --- terminal change ledger / handover -----------------------------------------

    def record_terminal_change(
        self, original_receipt_id: str, changed_user_snapshot_hash: str, *,
        created_generation: int | None, created_request_key: str | None, state: str,
    ) -> dict[str, Any]:
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM terminal_change_ledger WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ?",
                (original_receipt_id, changed_user_snapshot_hash),
            ).fetchone()
            if existing is not None:
                if (existing["created_generation"], existing["created_request_key"]) != (
                    created_generation, created_request_key
                ):
                    raise ValueError("terminal change ledger entry is immutable")
                # The same event re-observed after the entry progressed returns the row as
                # it is now; state only ever moves through transition_terminal_change (r13 R1).
                return dict(existing)
            connection.execute(
                "INSERT INTO terminal_change_ledger(original_receipt_id, "
                "changed_user_snapshot_hash, created_generation, created_request_key, state, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (original_receipt_id, changed_user_snapshot_hash, created_generation,
                 created_request_key, state, _now()),
            )
            row = connection.execute(
                "SELECT * FROM terminal_change_ledger WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ?",
                (original_receipt_id, changed_user_snapshot_hash),
            ).fetchone()
            return dict(row)

    def transition_terminal_change(
        self, original_receipt_id: str, changed_user_snapshot_hash: str, *,
        expected_state: str, state: str,
    ) -> dict[str, Any]:
        """Explicit conditional progress of one ledger entry (r3 #7); same-state is idempotent."""

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM terminal_change_ledger WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ?",
                (original_receipt_id, changed_user_snapshot_hash),
            ).fetchone()
            if row is None:
                raise KeyError((original_receipt_id, changed_user_snapshot_hash))
            if row["state"] == state:
                return dict(row)
            if row["state"] != expected_state:
                raise ValueError(f"terminal change ledger entry is {row['state']}, not {expected_state}")
            connection.execute(
                "UPDATE terminal_change_ledger SET state = ? WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ? AND state = ?",
                (state, original_receipt_id, changed_user_snapshot_hash, expected_state),
            )
            refreshed = connection.execute(
                "SELECT * FROM terminal_change_ledger WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ?",
                (original_receipt_id, changed_user_snapshot_hash),
            ).fetchone()
            return dict(refreshed)

    def get_terminal_change(
        self, original_receipt_id: str, changed_user_snapshot_hash: str
    ) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM terminal_change_ledger WHERE original_receipt_id = ? "
                "AND changed_user_snapshot_hash = ?",
                (original_receipt_id, changed_user_snapshot_hash),
            ).fetchone()
            return None if row is None else dict(row)

    def record_handover(
        self, from_plan_revision: str, to_plan_revision: str, adopted_effects: Mapping[str, Any]
    ) -> dict[str, Any]:
        encoded = _json(adopted_effects)
        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM handover_records WHERE from_plan_revision = ? AND to_plan_revision = ?",
                (from_plan_revision, to_plan_revision),
            ).fetchone()
            if existing is not None:
                if existing["adopted_effects_json"] != encoded:
                    raise ValueError("handover record is immutable")
                return dict(existing)
            connection.execute(
                "INSERT INTO handover_records(from_plan_revision, to_plan_revision, "
                "adopted_effects_json, created_at) VALUES (?, ?, ?, ?)",
                (from_plan_revision, to_plan_revision, encoded, _now()),
            )
            row = connection.execute(
                "SELECT * FROM handover_records WHERE from_plan_revision = ? AND to_plan_revision = ?",
                (from_plan_revision, to_plan_revision),
            ).fetchone()
            return dict(row)

    # --- chunk tags ----------------------------------------------------------------

    def upsert_chunk_tag_decision(self, **values: Any) -> None:
        keys = ("doc_id", "chunk_id", "question_id", "decision", "source", "source_fingerprint",
                "normalization_version", "tag_vocab_version", "question_set_version",
                "tag_rule_version", "s4_payload_policy_version", "prompt_version", "model")
        for name in keys:
            if values.get(name) in (None, ""):
                raise ValueError(f"chunk tag decision requires {name}")
        if values["decision"] not in CHUNK_TAG_DECISIONS:
            raise ValueError("chunk tag decision must be yes, no or unresolved")
        unknown = set(values) - set(keys) - {"detail"}
        if unknown:
            raise ValueError(f"chunk tag decision does not accept {sorted(unknown)}")
        detail = _validated_decision_detail(values["source"], values["decision"], values.get("detail"))
        detail_json = _json(detail)
        key_columns = keys[:3] + keys[5:]
        with self._transaction(immediate=True) as connection:
            if values["decision"] == "unresolved":
                # A low-confidence answer is an attempt, never a decision: it is appended
                # to its own ledger and leaves the question open for a retry (r9 R4).
                connection.execute(
                    """
                    INSERT INTO chunk_tag_attempts(
                        doc_id, chunk_id, question_id, source, detail_json,
                        source_fingerprint, normalization_version, tag_vocab_version,
                        question_set_version, tag_rule_version, s4_payload_policy_version,
                        prompt_version, model, attempted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*(values[k] for k in keys[:3]), values["source"], detail_json,
                     *(values[k] for k in keys[5:]), _now()),
                )
                return
            existing = connection.execute(
                "SELECT decision, source, detail_json FROM chunk_tag_decisions WHERE "
                + " AND ".join(f"{column} = ?" for column in key_columns),
                tuple(values[k] for k in key_columns),
            ).fetchone()
            if existing is not None:
                stored = (existing["decision"], existing["source"], existing["detail_json"])
                if stored != (values["decision"], values["source"], detail_json):
                    raise ValueError("chunk tag decision is immutable for this version tuple")
                return  # idempotent re-record of the same decision
            connection.execute(
                """
                INSERT INTO chunk_tag_decisions(
                    doc_id, chunk_id, question_id, decision, source, detail_json,
                    source_fingerprint, normalization_version, tag_vocab_version,
                    question_set_version, tag_rule_version, s4_payload_policy_version,
                    prompt_version, model, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*(values[k] for k in keys[:5]), detail_json,
                 *(values[k] for k in keys[5:]), _now()),
            )

    def list_chunk_tag_attempts(self, doc_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM chunk_tag_attempts WHERE doc_id = ? ORDER BY rowid", (doc_id,)
            ).fetchall()]

    def list_chunk_tag_decisions(self, doc_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM chunk_tag_decisions WHERE doc_id = ? ORDER BY chunk_id, question_id",
                (doc_id,),
            ).fetchall()]

    def upsert_document_tag_manifest(
        self, doc_id: str, *, chunk_ids: Iterable[str] = (), question_ids: Iterable[str] = (),
        **values: Any,
    ) -> dict[str, Any]:
        """Record the document manifest; ``COMPLETE`` is only accepted with proven coverage (R16).

        ``chunk_ids`` × ``question_ids`` must each have a decision under exactly the
        manifest's version tuple; ``chunk_ids_hash`` is derived from ``chunk_ids``.
        """

        required = ("source_fingerprint", "normalization_version", "tag_vocab_version",
                    "question_set_version", "tag_rule_version", "s4_payload_policy_version",
                    "prompt_version", "model", "status")
        for name in required:
            if values.get(name) in (None, ""):
                raise ValueError(f"document tag manifest requires {name}")
        if values["status"] not in ("COMPLETE", "PARTIAL", "STALE"):
            raise ValueError("document tag manifest status is invalid")
        chunks = sorted(set(chunk_ids))
        questions = sorted(set(question_ids))
        values["chunk_ids_hash"] = hashlib.sha256(_json(chunks).encode()).hexdigest()
        values["chunk_ids_json"] = _json(chunks)
        values["question_ids_json"] = _json(questions)
        keys = (*required[:-1], "chunk_ids_hash", "chunk_ids_json", "question_ids_json", "status")
        version_columns = ("source_fingerprint", "normalization_version", "tag_vocab_version",
                           "question_set_version", "tag_rule_version",
                           "s4_payload_policy_version", "prompt_version", "model")
        with self._transaction(immediate=True) as connection:
            # The chunk set and the applied question set of one (document, version
            # tuple) are authoritative once recorded, in every status including STALE
            # and across pointer changes to other tuples: the basis lives in its own
            # table, so a round trip X -> Y -> X cannot shrink it (r2 #11, r3 #2, r4 #2).
            basis = connection.execute(
                "SELECT chunk_ids_json, question_ids_json FROM document_tag_bases "
                "WHERE doc_id = ? AND " + " AND ".join(f"{c} = ?" for c in version_columns),
                (doc_id, *(values[c] for c in version_columns)),
            ).fetchone()
            if basis is None:
                connection.execute(
                    "INSERT INTO document_tag_bases(doc_id, "
                    + ", ".join(version_columns)
                    + ", chunk_ids_json, question_ids_json, created_at) VALUES (?, "
                    + ", ".join("?" for _ in version_columns) + ", ?, ?, ?)",
                    (doc_id, *(values[c] for c in version_columns), values["chunk_ids_json"],
                     values["question_ids_json"], _now()),
                )
            else:
                if basis["chunk_ids_json"] != values["chunk_ids_json"]:
                    raise ValueError("chunk set is immutable for this version tuple")
                if basis["question_ids_json"] != values["question_ids_json"]:
                    raise ValueError("question set is immutable for this version tuple")
            if values["status"] == "COMPLETE":
                if not chunks or not questions:
                    raise ValueError("COMPLETE requires chunk and question sets")
                if REQUIRED_TAG_QUESTION not in questions:
                    raise ValueError(f"COMPLETE requires the {REQUIRED_TAG_QUESTION} question")
                rows = connection.execute(
                    "SELECT chunk_id, question_id FROM chunk_tag_decisions WHERE doc_id = ? AND "
                    "decision IN ('yes', 'no') AND "
                    + " AND ".join(f"{column} = ?" for column in version_columns),
                    (doc_id, *(values[column] for column in version_columns)),
                ).fetchall()
                covered = {(r["chunk_id"], r["question_id"]) for r in rows}
                missing = [(c, q) for c in chunks for q in questions if (c, q) not in covered]
                if missing:
                    raise ValueError(
                        f"COMPLETE coverage is missing {len(missing)} resolved chunk/question decisions"
                    )
            connection.execute(
                f"INSERT OR REPLACE INTO document_tag_manifests(doc_id, {', '.join(keys)}, "
                f"updated_at) VALUES (?, {', '.join('?' for _ in keys)}, ?)",
                (doc_id, *(values[k] for k in keys), _now()),
            )
            row = connection.execute(
                "SELECT * FROM document_tag_manifests WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            return dict(row)

    def get_document_tag_manifest(self, doc_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM document_tag_manifests WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            return None if row is None else dict(row)

    # --- jobs ------------------------------------------------------------------------

    def void_jobs_for_plan(self, plan_revision: str, reason: str) -> int:
        """Persistently retire every unfinished job of a superseded plan (r8 R2, r9 O1)."""

        with self._transaction(immediate=True) as connection:
            return self._void_plan_jobs(connection, plan_revision, reason)

    @staticmethod
    def _void_plan_jobs(connection: sqlite3.Connection, plan_revision: str, reason: str) -> int:
        cursor = connection.execute(
            "UPDATE jobs SET voided_at = ?, void_reason = ?, updated_at = ? "
            "WHERE plan_revision = ? AND voided_at IS NULL AND completed_at IS NULL",
            (_now(), reason, _now(), plan_revision),
        )
        return int(cursor.rowcount)


__all__ = [
    "AUTO_RESOLVE_STATES",
    "CALENDAR_ENTRY_STATES",
    "CLASSIFICATION_SCHEMA",
    "CLASSIFICATION_STATES",
    "PLAN_AUTHORITIES",
    "AutoResolveIntent",
    "ClassificationRecord",
    "ClassificationStateMixin",
    "RecordingCalendarRow",
    "canonical_revision_hash",
]
