"""Gate service: begin (HTTP), view/confirm/deny (stdio) — implementation spec §6.3-§6.4.

Nonce records live in this process' memory only (a Core restart forgets them).
One PENDING nonce per UI session, a 2 s cooldown after a refusal, 60 s TTL on a
monotonic clock, terminal records kept 5 minutes for replay answers (C-17).

Confirm (plan rev 2, item 1 and condition D): the service lock is held from the
first check to the final in-memory transition; the record moves PENDING ->
COMMITTING before the database work; ``StateWriter.commit_gate`` re-reads the
target, regenerates the summary and recomputes ``request_hash`` *inside* its
``BEGIN IMMEDIATE`` and writes only if they still match. A changed target ends
as ``REJECTED(TARGET_CHANGED)`` with ``superseded=True`` (the v1.2 proposal
objects were removed in R1c; errata E27).
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from syllva.core.logging import log_event
from syllva.core.secret import Secret
from syllva.gate import canonical, fingerprints, mac, summaries
from syllva.state.v2.writer import StateWriter

TTL_S = 60.0
COOLDOWN_S = 2.0
KEEP_TERMINAL_S = 300.0
ENTITY_ID = re.compile(r"[A-Z][A-Z0-9]*-[A-Z][0-9]{2}")
ACTIONS = ("VERIFY_MATERIAL", "CONFIRM_EXAM_SCOPE", "SET_SUBMISSION_STATUS", "ADOPT_AI_ARTIFACT")
WIRED = {"VERIFY_MATERIAL"}  # errata E24: the other three are wired in R3/R5
TERMINAL = {"COMMITTED", "REJECTED", "DENIED", "EXPIRED"}


class GateError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class _Record:
    nonce_id: str
    session_id: str
    window_label: str
    action: str
    target_id: str
    params: dict[str, Any]
    request_hash: str
    summary: str
    target_label: str
    source_ids: list[str]
    source_prefixes: list[str]
    deadline: float
    state: str = "PENDING"
    reason: str | None = None
    superseded: bool = False
    finished: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def request_hash(action: str, target_id: str, params: Mapping[str, Any], summary: str, fingerprint: str) -> str:
    obj = {"v": 1, "action": action, "target_id": target_id, "params": dict(params), "summary": summary,
           "target_fingerprint": fingerprint}
    return hashlib.sha256(canonical.dumps(obj).encode("utf-8")).hexdigest()


class GateService:
    def __init__(
        self,
        writer: StateWriter,
        session_alive: Callable[[str], bool],
        *,
        secret: Secret | None,
        shell_version: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._writer = writer
        self._alive = session_alive
        self._secret = secret
        self._shell_version = shell_version
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}
        self._cooldown: dict[str, float] = {}

    # -- begin (HTTP, after session + CSRF + Origin) -------------------------------------

    def begin(self, session_id: str, window_label: str, action: str, target_id: str, params: Mapping[str, Any]) -> str:
        if self._secret is None:
            raise GateError("GATE_UNAVAILABLE", 409)  # headless Core: no Shell, no Human-Proof (§6.7)
        if action not in ACTIONS:
            raise GateError("BAD_REQUEST", 400)
        if action not in WIRED:
            raise GateError("ACTION_NOT_AVAILABLE", 400)
        if (not isinstance(params, Mapping) or set(params) != {"material_id"}
                or not isinstance(params["material_id"], str) or not ENTITY_ID.fullmatch(params["material_id"])
                or target_id != params["material_id"]):
            raise GateError("BAD_REQUEST", 400)
        target = fingerprints.material_target(self._writer.query_one, target_id)
        if target is None:
            raise GateError("TARGET_UNKNOWN", 404)
        if target.verified:
            raise GateError("ALREADY_DONE", 409)
        clean = {"material_id": target_id}
        summary = summaries.summarize(action, clean, {"title": target.title, "course": target.course})
        digest = request_hash(action, target_id, clean, summary, target.fingerprint)
        with self._lock:
            now = self._clock()
            self._expire(now)
            if any(r.session_id == session_id and r.state == "PENDING" for r in self._records.values()):
                raise GateError("GATE_BUSY", 409)
            if now < self._cooldown.get(session_id, 0.0):
                raise GateError("GATE_COOLDOWN", 409)
            nonce_id = secrets.token_hex(16)
            self._records[nonce_id] = _Record(
                nonce_id, session_id, window_label, action, target_id, clean, digest, summary,
                summaries.label(target.title), [target.source_id], [target.source_hash.removeprefix("sha256:")[:12]],
                now + TTL_S,
            )
        log_event("GATE_BEGIN", action=action, target=target_id)
        return nonce_id

    # -- stdio ---------------------------------------------------------------------------------

    def view(self, nonce_id: str, window_label: str) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._expire(now)
            record = self._records.get(nonce_id)
            reason = self._unusable(record, window_label)
            if reason is not None:
                return {"t": "gate.none", "nonce_id": nonce_id, "reason": reason}
            assert record is not None
            return {
                "t": "gate.view", "nonce_id": nonce_id, "summary": record.summary, "target_id": record.target_id,
                "target_label": record.target_label, "source_ids": list(record.source_ids),
                "source_hash_prefixes": list(record.source_prefixes), "request_hash": record.request_hash,
                "expires_in_ms": max(0, int((record.deadline - now) * 1000)),
            }

    def deny(self, nonce_id: str) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._expire(now)
            record = self._records.get(nonce_id)
            if record is None:
                return self._result(nonce_id, "REJECTED", "UNKNOWN")
            if record.state == "PENDING":
                self._finish(record, "DENIED", None, now)
            return self._answer(record)

    def confirm(self, nonce_id: str, window_label: str, mac_b64: str) -> dict[str, Any]:
        with self._lock:  # held to the end: one confirm at a time decides a nonce (§6.4-5)
            now = self._clock()
            self._expire(now)
            record = self._records.get(nonce_id)
            if record is None:
                return self._result(nonce_id, "REJECTED", "UNKNOWN")
            presented = _b64(mac_b64)
            mac_ok = self._secret is not None and presented is not None and mac.verify(
                self._secret.reveal(), nonce_id, record.request_hash, presented)
            if record.state in TERMINAL:
                if record.state == "COMMITTED" and mac_ok and record.window_label == window_label:
                    return self._answer(record)  # idempotent replay (C-17)
                return self._answer(record)
            if record.window_label != window_label:
                return self._result(nonce_id, "REJECTED", "WRONG_WINDOW")  # does not end the nonce
            if not self._alive(record.session_id):
                self._finish(record, "REJECTED", "SESSION_GONE", now)
                return self._answer(record)
            if not mac_ok:
                self._finish(record, "REJECTED", "BAD_MAC", now)
                return self._answer(record)
            record.state = "COMMITTING"
            try:
                refusal = self._writer.commit_gate(
                    action=record.action, target_kind="material", target_id=record.target_id,
                    request_hash=record.request_hash, nonce_id=nonce_id,
                    confirmed_at=datetime.now(UTC).isoformat(timespec="seconds"), shell_version=self._shell_version,
                    verify=lambda w: self._recheck(w, record),
                )
            except Exception as exc:  # noqa: BLE001 - nothing was written (the transaction rolled back)
                log_event("GATE_COMMIT_FAILED", error=type(exc).__name__)
                self._finish(record, "REJECTED", "COMMIT_FAILED", now)
                return self._answer(record)
            if refusal is None:
                self._finish(record, "COMMITTED", None, now)
                log_event("GATE_COMMITTED", action=record.action, target=record.target_id)
            else:
                self._finish(record, "REJECTED", refusal, now)
                record.superseded = refusal == "TARGET_CHANGED"
            return self._answer(record)

    # -- internals ------------------------------------------------------------------------------

    def _recheck(self, writer: StateWriter, record: _Record) -> str | None:
        """Runs inside commit_gate's BEGIN IMMEDIATE: the target as it is now, recomputed."""

        target = fingerprints.material_target(writer.query_one, record.target_id)
        if target is None or target.verified:
            return "TARGET_CHANGED"
        summary = summaries.summarize(record.action, record.params, {"title": target.title, "course": target.course})
        if request_hash(record.action, record.target_id, record.params, summary, target.fingerprint) != record.request_hash:
            return "TARGET_CHANGED"
        return None

    def _unusable(self, record: _Record | None, window_label: str) -> str | None:
        if record is None:
            return "UNKNOWN"
        if record.state == "EXPIRED":
            return "EXPIRED"
        if record.state != "PENDING":
            return "USED"
        if record.window_label != window_label:
            return "WRONG_WINDOW"
        if not self._alive(record.session_id):
            return "SESSION_GONE"
        return None

    def _expire(self, now: float) -> None:
        for nonce_id, record in list(self._records.items()):
            if record.state == "PENDING" and now >= record.deadline:
                self._finish(record, "EXPIRED", None, now)
            elif record.finished is not None and now - record.finished > KEEP_TERMINAL_S:
                del self._records[nonce_id]

    def _finish(self, record: _Record, state: str, reason: str | None, now: float) -> None:
        record.state, record.reason, record.finished = state, reason, now
        if state in ("REJECTED", "DENIED", "EXPIRED"):
            self._cooldown[record.session_id] = now + COOLDOWN_S

    def _answer(self, record: _Record) -> dict[str, Any]:
        result = self._result(record.nonce_id, record.state, record.reason)
        if record.superseded:
            result["superseded"] = True
        return result

    @staticmethod
    def _result(nonce_id: str, status: str, reason: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {"t": "gate.result", "nonce_id": nonce_id, "status": status}
        if reason is not None:
            out["reason"] = reason
        return out


def _b64(text: object) -> bytes | None:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", text):
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return None


__all__ = ["ACTIONS", "GateError", "GateService", "request_hash"]
