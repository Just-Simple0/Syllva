"""Range-request transactions, separated from study-note state ownership."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from uls.domain.approval_identity import (
    UsageSlotIdentity,
    build_usage_proposal_envelope,
    c5_usage_snapshot_json,
    canonical_action_json,
    canonical_semantics_from_queue,
    canonical_usage_proposal_envelope_json,
    derive_proposal_id_for_create,
    parse_c5_usage_snapshot,
)
from uls.domain.errors import ProviderWriteNotAppliedError
from uls.intake.usage_range import ValidatedRangeRequest

RANGE_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS range_request_receipts (
 request_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE,
 source_id TEXT NOT NULL, workspace TEXT NOT NULL, usage_slot_key TEXT NOT NULL,
 generation INTEGER NOT NULL, user_hash TEXT NOT NULL, user_json TEXT NOT NULL,
 identity_json TEXT NOT NULL, observation_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS range_producer_intents (
 usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL, receipt_id TEXT NOT NULL,
 mode TEXT NOT NULL, candidate_id TEXT, expected_json TEXT, status TEXT NOT NULL,
 PRIMARY KEY (usage_slot_key,generation)
);
CREATE TABLE IF NOT EXISTS range_producer_attempts (
 token TEXT PRIMARY KEY, usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL,
 baseline_json TEXT NOT NULL, phase TEXT NOT NULL, outcome TEXT, readback_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS range_one_producer_owner ON range_producer_attempts(usage_slot_key)
 WHERE phase != 'RELEASED';
CREATE TABLE IF NOT EXISTS range_outbox_invalidations (
 proposal_id TEXT PRIMARY KEY, usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL,
 reason TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS range_producer_actions (
 usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL, action_json TEXT NOT NULL,
 PRIMARY KEY(usage_slot_key,generation)
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slot_rows(rows: list[dict[str, Any]], head: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [row for row in rows if row["session_app_id"] == head["session_app_id"]
            and row["material_app_id"] == head["material_app_id"] and row["usage_role"] == head["usage_role"]]


def _no_live_owner(connection: sqlite3.Connection, slot: str) -> None:
    for table, phase_column in (("usage_apply_guards", "phase"), ("range_producer_attempts", "phase")):
        if connection.execute(f"SELECT 1 FROM {table} WHERE usage_slot_key=? AND {phase_column}!='RELEASED'", (slot,)).fetchone():
            raise ValueError("slot has unresolved mutation ownership")


class UsageRangeStateMixin:
    """Methods execute on the host store's same immediate-transaction boundary."""

    def _transaction(self, *, immediate: bool = False) -> AbstractContextManager[sqlite3.Connection]:
        raise NotImplementedError

    def list_usage_request_receipts(self, *, workspace: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM range_request_receipts WHERE workspace=? ORDER BY created_at,request_id", (workspace,),
            )]

    def get_usage_request_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM range_request_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            return dict(row) if row else None

    def claim_usage_request(
        self, request: ValidatedRangeRequest, *, workspace: str,
        candidate_id: str | None, baseline_json: str, expected_json: str | None,
        action_json: str | None = None,
    ) -> dict[str, Any]:
        baseline = parse_c5_usage_snapshot(baseline_json)
        slot = request.slot.key
        operation = "create_usage" if request.input.operation == "CREATE" else "update_range"
        receipt_id = hashlib.sha256(_json(["uls.range-receipt.v1", workspace, request.request_id, request.input.user_hash]).encode()).hexdigest()
        now = _now()
        with self._transaction(immediate=True) as connection:
            previous = connection.execute("SELECT * FROM range_request_receipts WHERE request_id=?", (request.request_id,)).fetchone()
            if previous is not None:
                if (previous["user_hash"] != request.input.user_hash or previous["workspace"] != workspace
                    or previous["usage_slot_key"] != slot or previous["identity_json"] != request.identity_json):
                    raise ValueError("immutable request receipt conflicts; submit a new request")
                return dict(previous)
            current = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (slot,)).fetchone()
            # An old app-only slot is not silently migrated around a live owner.
            for old in connection.execute(
                "SELECT * FROM range_intent_heads WHERE session_app_id=? AND material_app_id=? AND usage_role=?",
                (request.session_app_id, request.material_app_id, request.slot.role),
            ):
                _no_live_owner(connection, old["usage_slot_key"])
                if old["reservation_state"] != "NONE" or old["apply_lease_expires_at"] is not None:
                    raise ValueError("legacy reservation requires explicit recovery")
            occupants = _slot_rows(baseline, {"session_app_id": request.session_app_id,
                                            "material_app_id": request.material_app_id, "usage_role": request.slot.role})
            if len(occupants) > 1:
                raise ValueError("duplicate physical slot occupants")
            if operation == "create_usage" and request.input.mode != "UNKNOWN" and (occupants or (current and current["current_usage_app_id"])):
                raise ValueError("CREATE requires an empty slot; open an UPDATE request")
            if operation == "update_range":
                if (request.input.usage_page_id is None or len(occupants) != 1
                    or occupants[0]["provider_row_id"] != request.input.usage_page_id.replace("-", "")):
                    raise ValueError("UPDATE requires its exact selected physical Usage")
                candidate_id = occupants[0]["usage_app_id"]
            if request.input.mode != "UNKNOWN" and (not candidate_id or expected_json is None or action_json is None):
                raise ValueError("proposal intent requires exact candidate, expected identity and source-bound action")
            if request.input.mode == "UNKNOWN" and (action_json is not None or expected_json is not None):
                raise ValueError("UNKNOWN cannot contain proposal semantics")
            expected = json.loads(expected_json) if expected_json is not None else None
            if expected is not None and (
                not isinstance(expected, dict)
                or set(expected) != {"usage_app_id", "session_app_id", "material_app_id", "usage_role", "start_page", "end_page", "verified"}
                or expected.get("usage_app_id") != candidate_id
                or expected.get("session_app_id") != request.session_app_id
                or expected.get("material_app_id") != request.material_app_id
                or expected.get("usage_role") != request.slot.role
                or (operation == "create_usage" and expected.get("verified") is not False)
                or type(expected.get("verified")) is not bool
                or expected.get("start_page") != request.input.start_page
                or expected.get("end_page") != request.input.end_page
                or (operation == "update_range" and expected.get("verified") != occupants[0]["verified"])
            ):
                raise ValueError("producer expected identity differs from request")
            if (current and current["current_usage_app_id"] is not None
                and (not occupants or current["current_usage_app_id"] != occupants[0]["usage_app_id"]
                     or current["current_usage_provider"] != occupants[0]["provider"]
                     or current["current_usage_provider_row_id"] != occupants[0]["provider_row_id"])):
                raise ValueError("permanent Usage binding changed")
            generation = 1 if current is None else current["intent_generation"] + 1
            if current and current["current_proposal_id"]:
                connection.execute("INSERT OR IGNORE INTO range_outbox_invalidations VALUES (?,?,?,?,0)",
                                   (current["current_proposal_id"], slot, current["intent_generation"], "new range request"))
            binding = occupants[0] if occupants else None
            if current is None:
                connection.execute(
                    "INSERT INTO range_intent_heads (usage_slot_key,session_app_id,material_app_id,usage_role,"
                    "current_request_id,current_receipt_id,receipt_hash,current_operation,intent_generation,"
                    "current_usage_app_id,current_usage_provider,current_usage_provider_row_id,slot_identity_json,active,"
                    "input_mode,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                    (slot, request.session_app_id, request.material_app_id, request.slot.role, request.request_id,
                     receipt_id, request.input.user_hash, operation, generation,
                     binding["usage_app_id"] if binding else None, binding["provider"] if binding else None,
                     binding["provider_row_id"] if binding else None, request.slot.canonical_json, request.input.mode, now, now),
                )
            else:
                connection.execute(
                    "UPDATE range_intent_heads SET current_request_id=?,current_receipt_id=?,receipt_hash=?,"
                    "current_operation=?,intent_generation=?,current_target_entity_id=NULL,current_proposal_id=NULL,"
                    "active=1,inactive_reason=NULL,input_mode=?,updated_at=? WHERE usage_slot_key=?",
                    (request.request_id, receipt_id, request.input.user_hash, operation, generation, request.input.mode, now, slot),
                )
                if binding is not None and current["current_usage_app_id"] is None:
                    connection.execute(
                        "UPDATE range_intent_heads SET current_usage_app_id=?,current_usage_provider=?,current_usage_provider_row_id=? WHERE usage_slot_key=?",
                        (binding["usage_app_id"], binding["provider"], binding["provider_row_id"], slot),
                    )
            observation = _json({"created_time": request.created_time.isoformat(), "first_read_at": request.first_read_at.isoformat(),
                                 "second_read_at": request.second_read_at.isoformat(), "created_by": request.created_by,
                                 "last_edited_by": request.last_edited_by})
            connection.execute("INSERT INTO range_request_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (request.request_id, receipt_id, request.source_id, workspace, slot, generation,
                                request.input.user_hash, request.input.user_json, request.identity_json, observation, now))
            connection.execute("INSERT INTO range_intent_claims VALUES (?,?,?,?,?)",
                               (slot, request.request_id, receipt_id, generation, _json({"receipt": receipt_id, "identity": request.identity_json})))
            connection.execute("INSERT INTO range_producer_intents VALUES (?,?,?,?,?,?,?)",
                               (slot, generation, receipt_id, request.input.mode,
                                None if request.input.mode == "UNKNOWN" else candidate_id, expected_json,
                                "UNKNOWN" if request.input.mode == "UNKNOWN" else "PREPARED"))
            if action_json is not None:
                kind = "MATERIAL_USAGE" if operation == "create_usage" else "PAGE_RANGE"
                semantics = canonical_semantics_from_queue({"Proposal Type": kind, "Proposed Action": action_json,
                                                            "Target Entity ID": candidate_id})
                if (canonical_action_json(semantics) != action_json or semantics["target_entity_id"] != candidate_id
                    or semantics["session_id"] != request.session_app_id or semantics["material_id"] != request.material_app_id
                    or semantics["usage_role"] != request.slot.role or semantics["operation"] != operation
                    or semantics["desired_range"] != {"start_page": request.input.start_page, "end_page": request.input.end_page}
                    or semantics["course_relation_page_id"].replace("-", "") != request.slot.course_page_id.replace("-", "")):
                    raise ValueError("immutable producer action differs from request")
                old = semantics["old_snapshot"]
                basis = occupants[0] if operation == "update_range" else expected
                if basis is None or old != {"usage_id": candidate_id, "session_id": request.session_app_id,
                                          "material_id": request.material_app_id, "role": request.slot.role,
                                          "start_page": basis["start_page"], "end_page": basis["end_page"],
                                          "verified": basis["verified"]}:
                    raise ValueError("immutable producer old snapshot differs from acquired slot")
                connection.execute("INSERT INTO range_producer_actions VALUES (?,?,?)", (slot, generation, action_json))
            return dict(connection.execute("SELECT * FROM range_request_receipts WHERE receipt_id=?", (receipt_id,)).fetchone())

    def invalidate_usage_request(self, *, receipt_id: str, slot: str, generation: int, reason: str) -> bool:
        if not reason:
            raise ValueError("invalidation requires a reason")
        with self._transaction(immediate=True) as connection:
            head = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (slot,)).fetchone()
            if head is None or head["current_receipt_id"] != receipt_id or head["intent_generation"] != generation:
                return False
            connection.execute("UPDATE range_intent_heads SET active=0,inactive_reason=?,updated_at=? WHERE usage_slot_key=?",
                               (reason, _now(), slot))
            if head["current_proposal_id"]:
                connection.execute("INSERT OR IGNORE INTO range_outbox_invalidations VALUES (?,?,?,?,0)",
                                   (head["current_proposal_id"], slot, generation, reason))
            return True

    def get_usage_producer_intent(self, slot: str, generation: int) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM range_producer_intents WHERE usage_slot_key=? AND generation=?", (slot, generation)).fetchone()
            return dict(row) if row else None

    def get_usage_producer_action(self, slot: str, generation: int) -> str | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT action_json FROM range_producer_actions WHERE usage_slot_key=? AND generation=?",
                                     (slot, generation)).fetchone()
            return row[0] if row else None

    def pending_usage_invalidations(self, workspace: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT i.* FROM range_outbox_invalidations i JOIN range_request_receipts r "
                "ON r.usage_slot_key=i.usage_slot_key AND r.generation=i.generation "
                "WHERE r.workspace=? AND i.projected=0", (workspace,),
            )]

    def acknowledge_usage_invalidation(self, proposal_id: str) -> None:
        with self._transaction(immediate=True) as connection:
            connection.execute("UPDATE range_outbox_invalidations SET projected=1 WHERE proposal_id=?", (proposal_id,))

    def finalize_usage_producer_proofs(self, workspace: str) -> None:
        """Resume only durable RESOLVED outcomes; HELD/MUTATING are never unlocked."""
        with self._transaction() as connection:
            owners = [dict(row) for row in connection.execute(
                "SELECT a.*,r.user_hash FROM range_producer_attempts a JOIN range_request_receipts r "
                "ON r.usage_slot_key=a.usage_slot_key AND r.generation=a.generation "
                "WHERE r.workspace=? AND a.phase='RESOLVED'", (workspace,),
            )]
        for owner in owners:
            if self.release_usage_creation(owner["token"]):
                continue
            if owner["outcome"] == "created":
                action = self.get_usage_producer_action(owner["usage_slot_key"], owner["generation"])
                if action is not None:
                    self.seal_usage_proposal(slot=owner["usage_slot_key"], generation=owner["generation"],
                                             receipt_hash=owner["user_hash"], action_json=action)

    def acquire_usage_creation(self, *, slot: str, generation: int, receipt_hash: str, baseline_json: str) -> str:
        baseline = parse_c5_usage_snapshot(baseline_json)
        with self._transaction(immediate=True) as connection:
            head = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (slot,)).fetchone()
            intent = connection.execute("SELECT * FROM range_producer_intents WHERE usage_slot_key=? AND generation=?", (slot, generation)).fetchone()
            if (head is None or intent is None or head["intent_generation"] != generation or not head["active"]
                or head["receipt_hash"] != receipt_hash or head["current_operation"] != "create_usage"
                or head["current_proposal_id"] is not None or intent["status"] != "PREPARED" or intent["mode"] == "UNKNOWN"
                or head["current_usage_app_id"] is not None or _slot_rows(baseline, head)):
                raise ValueError("creation requires current active unsealed intent and empty physical slot")
            _no_live_owner(connection, slot)
            token = str(uuid4())
            connection.execute("INSERT INTO range_producer_attempts VALUES (?,?,?,?, 'HELD',NULL,NULL)",
                               (token, slot, generation, c5_usage_snapshot_json(baseline)))
            return token

    def mark_usage_creation_mutating(self, token: str, *, baseline_json: str) -> None:
        readback = c5_usage_snapshot_json(parse_c5_usage_snapshot(baseline_json))
        with self._transaction(immediate=True) as connection:
            owner = connection.execute("SELECT * FROM range_producer_attempts WHERE token=?", (token,)).fetchone()
            if owner is None or owner["phase"] != "HELD" or owner["baseline_json"] != readback:
                raise ValueError("creation baseline changed before dispatch")
            head = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (owner["usage_slot_key"],)).fetchone()
            if not head["active"] or head["intent_generation"] != owner["generation"]:
                raise ValueError("creation intent is inactive/stale")
            connection.execute("UPDATE range_producer_attempts SET phase='MUTATING' WHERE token=?", (token,))

    def record_usage_creation_outcome(
        self, token: str, *, readback_json: str, not_applied_error: ProviderWriteNotAppliedError | None = None,
    ) -> None:
        readback = parse_c5_usage_snapshot(readback_json)
        if not_applied_error is not None and not isinstance(not_applied_error, ProviderWriteNotAppliedError):
            raise ValueError("typed provider no-effect guarantee required")
        with self._transaction(immediate=True) as connection:
            owner = connection.execute("SELECT * FROM range_producer_attempts WHERE token=?", (token,)).fetchone()
            if owner is None or owner["phase"] != "MUTATING":
                raise ValueError("exact mutating producer owner required")
            baseline = parse_c5_usage_snapshot(owner["baseline_json"])
            head = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (owner["usage_slot_key"],)).fetchone()
            if head is None or head["intent_generation"] != owner["generation"]:
                raise ValueError("producer owner is stale")
            intent = connection.execute("SELECT * FROM range_producer_intents WHERE usage_slot_key=? AND generation=?",
                                        (owner["usage_slot_key"], owner["generation"])).fetchone()
            if not_applied_error is not None:
                if c5_usage_snapshot_json(baseline) != c5_usage_snapshot_json(readback):
                    raise ValueError("physical baseline changed; no-effect unproven")
                outcome = "not_applied"
            else:
                expected = json.loads(intent["expected_json"])
                added = [row for row in readback if row not in baseline]
                if (len(readback) != len(baseline) + 1 or any(row not in readback for row in baseline)
                    or len(added) != 1 or len(_slot_rows(readback, head)) != 1
                    or any(added[0].get(key) != value for key, value in expected.items())
                    or added[0]["usage_app_id"] != intent["candidate_id"] or added[0]["verified"] is not False):
                    raise ValueError("exact deterministic Usage creation is unproven")
                row = added[0]
                connection.execute("UPDATE range_intent_heads SET current_usage_app_id=?,current_usage_provider=?,current_usage_provider_row_id=? WHERE usage_slot_key=?",
                                   (row["usage_app_id"], row["provider"], row["provider_row_id"], owner["usage_slot_key"]))
                connection.execute("UPDATE range_producer_intents SET status='CREATED' WHERE usage_slot_key=? AND generation=?",
                                   (owner["usage_slot_key"], owner["generation"]))
                outcome = "created"
            connection.execute("UPDATE range_producer_attempts SET phase='RESOLVED',outcome=?,readback_json=? WHERE token=?",
                               (outcome, c5_usage_snapshot_json(readback), token))

    def release_usage_creation(self, token: str) -> bool:
        with self._transaction(immediate=True) as connection:
            # Creation success remains reserved until seal or inactive partial-effect finalization.
            return connection.execute(
                "UPDATE range_producer_attempts SET phase='RELEASED' WHERE token=? AND (phase='HELD' OR "
                "(phase='RESOLVED' AND (outcome='not_applied' OR EXISTS (SELECT 1 FROM range_intent_heads h "
                "WHERE h.usage_slot_key=range_producer_attempts.usage_slot_key AND h.intent_generation=range_producer_attempts.generation AND h.active=0))))",
                (token,),
            ).rowcount == 1

    def seal_usage_proposal(self, *, slot: str, generation: int, receipt_hash: str, action_json: str) -> str:
        with self._transaction(immediate=True) as connection:
            head = connection.execute("SELECT * FROM range_intent_heads WHERE usage_slot_key=?", (slot,)).fetchone()
            intent = connection.execute("SELECT * FROM range_producer_intents WHERE usage_slot_key=? AND generation=?", (slot, generation)).fetchone()
            if (head is None or intent is None or not head["active"] or head["intent_generation"] != generation
                or head["receipt_hash"] != receipt_hash or intent["mode"] == "UNKNOWN"):
                raise ValueError("seal requires current active range intent")
            kind = "MATERIAL_USAGE" if head["current_operation"] == "create_usage" else "PAGE_RANGE"
            immutable = connection.execute("SELECT action_json FROM range_producer_actions WHERE usage_slot_key=? AND generation=?",
                                           (slot, generation)).fetchone()
            if immutable is None or immutable[0] != action_json:
                raise ValueError("seal requires exact action persisted at claim")
            semantics = canonical_semantics_from_queue({"Proposal Type": kind, "Proposed Action": action_json,
                                                        "Target Entity ID": intent["candidate_id"]})
            if (canonical_action_json(semantics) != action_json or semantics["operation"] != head["current_operation"]
                or semantics["target_entity_id"] != intent["candidate_id"] or semantics["target_entity_id"] != head["current_usage_app_id"]
                or semantics["session_id"] != head["session_app_id"] or semantics["material_id"] != head["material_app_id"]
                or semantics["usage_role"] != head["usage_role"]):
                raise ValueError("sealed proposal differs from immutable intent/binding")
            identity = UsageSlotIdentity.from_json(head["slot_identity_json"])
            if identity.key != slot or semantics["course_relation_page_id"].replace("-", "") != identity.course_page_id.replace("-", ""):
                raise ValueError("proposal Course differs from physical slot")
            envelope = build_usage_proposal_envelope(slot, head["current_request_id"], generation)
            proposal_id = derive_proposal_id_for_create(kind, envelope, semantics)
            previous = connection.execute("SELECT * FROM usage_proposal_outbox WHERE usage_slot_key=? AND intent_generation=?", (slot, generation)).fetchone()
            if previous is not None:
                if previous["proposal_id"] != proposal_id or previous["action_json"] != action_json:
                    raise ValueError("committed proposal cannot be rebound")
                return proposal_id
            if head["current_proposal_id"] is not None:
                raise ValueError("bound proposal lacks its immutable outbox")
            if head["current_operation"] == "create_usage" and (intent["status"] != "CREATED" or not connection.execute(
                "SELECT 1 FROM range_producer_attempts WHERE usage_slot_key=? AND generation=? AND phase='RESOLVED' AND outcome='created'",
                (slot, generation),
            ).fetchone()):
                raise ValueError("CREATE seal requires durable physical creation proof")
            now = _now()
            connection.execute("UPDATE range_intent_heads SET current_target_entity_id=?,current_proposal_id=?,updated_at=? WHERE usage_slot_key=?",
                               (intent["candidate_id"], proposal_id, now, slot))
            connection.execute("INSERT INTO usage_proposal_outbox VALUES (?,?,?,?,?,?,'PREPARED',NULL,?,?)",
                               (proposal_id, slot, head["current_request_id"], generation, action_json,
                                canonical_usage_proposal_envelope_json(envelope), now, now))
            connection.execute("UPDATE range_producer_intents SET status='SEALED' WHERE usage_slot_key=? AND generation=?", (slot, generation))
            connection.execute("UPDATE range_producer_attempts SET phase='RELEASED' WHERE usage_slot_key=? AND generation=? AND phase='RESOLVED' AND outcome='created'", (slot, generation))
            return proposal_id
