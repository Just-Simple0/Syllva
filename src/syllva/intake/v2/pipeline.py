"""Local intake worker (implementation spec §11, design §4.4, §2 I3).

One ``intake_candidates`` row per received file walks the §11.2 state machine::

    RECEIVED -> CLASSIFYING -> NEEDS_REVIEW -> CONFIRMED ----> PROCESSING -> READY
                     |                                   ^                  \\-> PARTIAL / FAILED
                     \\-> AUTO_CONFIRMED -----------------/
    RECEIVED-time verdicts: DUPLICATE (same bytes already stored), UNSUPPORTED (type/size)

* every transition is one transaction and goes through
  ``StateWriter.transition_intake_state`` (the schema enforces the same table);
* classification reuses the pure S0/S1/S3 pipeline (``intake.classification``);
  S2 (external LLM) is not used (§11.2). AUTO_CONFIRMED needs only what still
  means something locally: a decided, normalizable Kind, an existing course, a
  calendar-matched transcript, and no conflicting week evidence; anything else
  waits for the user (NEEDS_REVIEW);
* only the user confirms or dismisses (``Actor.UI_USER``); processing is the
  ingester. Confirming creates the material (and, on request, a new session);
* processing normalizes the current source version and maps the result exactly:
  READY/PARTIAL as reported, no citable text -> FAILED with the normalizer's cause
  (the material goes to NEEDS_REVIEW), any error -> FAILED(PROCESSING_ERROR) with
  the exception class. A FAILED or PARTIAL candidate is only re-run through
  QUEUED (never PARTIAL -> READY directly);
* ``recover()`` puts orphans back after a crash (PROCESSING -> QUEUED,
  CLASSIFYING -> RECEIVED); every step is safe to re-run.

Single active worker: the caller owns the database (``open_database`` holds an
exclusive OS lock), so no two workers run these steps concurrently.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePath
from typing import Any, BinaryIO, cast

from syllva.domain.errors import PolicyViolation, SyllvaError
from syllva.ingestion.local import IngestError, add_version, ingest_new, nfc
from syllva.intake.classification.calendar import MatchStatus
from syllva.intake.classification.pipeline import (
    NOTE_CANVAS_WEEK_CONFLICT,
    ClassificationOutcome,
    SourceProbe,
    classify_upload_item,
)
from syllva.intake.classification.rules import TRANSCRIPT_RULE_MIME, CourseAliasIndex
from syllva.intake.classification.taxonomy import HandlingMode, Kind, handling_mode
from syllva.library.blobs import BlobStore, BlobTooLarge
from syllva.normalization.local import normalize_source_version
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

# extension -> normalizer kind (§11.3: anything else is UNSUPPORTED_TYPE for now)
NORMALIZER_BY_EXTENSION = {".pdf": "pdf", ".txt": "transcript", ".md": "transcript"}
PROCESSOR_VERSION = "local-intake-1"
_PROBE_HEAD = 4096
_CODE_RE = re.compile(r"[A-Z][A-Z0-9]*")
_MAX_SEQUENCE = 99  # entity ids are <CODE>-<T><NN> (domain/ids.py)


class IntakeError(SyllvaError):
    code = "INTAKE_INVALID"


class IdSpaceExhausted(IntakeError):
    code = "ID_SPACE_EXHAUSTED"


@dataclass(frozen=True)
class IntakeConfig:
    max_bytes: int = 200 * 1024 * 1024
    processor_version: str = PROCESSOR_VERSION


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    state: str
    display_name: str
    reason_code: str | None
    reason_message: str | None
    source_id: str | None
    duplicate_of: str | None
    material_id: str | None
    suggestion: dict[str, Any] | None


class _TooLarge(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bounded(stream: BinaryIO, limit: int) -> Iterator[bytes]:
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise _TooLarge
        yield chunk


class LocalIntake:
    def __init__(self, writer: StateWriter, blobs: BlobStore, *, config: IntakeConfig | None = None) -> None:
        self.writer = writer
        self.blobs = blobs
        self.config = config or IntakeConfig()

    # -- queries --------------------------------------------------------------------

    def get(self, candidate_id: str) -> Candidate:
        row = self.writer.query_one(
            "SELECT c.*, f.source_id, m.material_id AS material_key FROM intake_candidates c "
            "LEFT JOIN source_versions v ON v.id = c.source_version_id "
            "LEFT JOIN source_files f ON f.id = v.source_file_id "
            "LEFT JOIN materials m ON m.id = c.material_id WHERE c.candidate_id = ?",
            (candidate_id,),
        )
        if row is None:
            raise IntakeError("unknown intake candidate", details={"candidate_id": candidate_id})
        suggestion = json.loads(row["suggestion_json"]) if row["suggestion_json"] else None
        return Candidate(
            str(row["candidate_id"]), str(row["state"]), str(row["display_name"]), row["reason_code"],
            row["reason_message"], row["source_id"], row["duplicate_of_source_id"], row["material_key"], suggestion,
        )

    def _ids(self, *states: str) -> list[str]:
        marks = ", ".join("?" for _ in states)
        rows = self.writer.query(f"SELECT candidate_id FROM intake_candidates WHERE state IN ({marks}) ORDER BY id", states)
        return [str(r["candidate_id"]) for r in rows]

    # -- RECEIVED ---------------------------------------------------------------------

    def receive(
        self,
        stream: BinaryIO,
        *,
        display_name: str,
        mime_type: str | None = None,
        channel: str = "USER_DROP",
        course_hint: str | None = None,
        now: str | None = None,
    ) -> Candidate:
        """Store the bytes and open a candidate (or record why they were not stored)."""

        stamp = now or _now()
        name = nfc(display_name)
        candidate_id = "INT-" + secrets.token_hex(6)
        base = {
            "candidate_id": candidate_id, "channel": channel, "display_name": name, "mime_type": mime_type,
            "created_at": stamp, "updated_at": stamp,
        }
        if course_hint is not None:
            base["suggestion_json"] = json.dumps({"course_hint": course_hint}, ensure_ascii=False, sort_keys=True)
        if PurePath(name).suffix.lower() not in NORMALIZER_BY_EXTENSION:
            return self._unsupported(base, "UNSUPPORTED_TYPE", f"{PurePath(name).suffix or '(none)'} is not supported yet")
        try:
            # one transaction: a crash never leaves a stored source without its candidate row
            with self.writer.transaction():
                ingested = ingest_new(
                    self.writer, self.blobs, cast(BinaryIO, _ChunkReader(_bounded(stream, self.config.max_bytes))),
                    display_name=name, mime_type=mime_type, now=stamp,
                )
                values = {**base, "source_version_id": ingested.source_version_id, "size_bytes": ingested.size_bytes}
                live = None if ingested.created else self.writer.query_one(
                    "SELECT candidate_id FROM intake_candidates WHERE source_version_id = ? "
                    "AND state NOT IN ('DUPLICATE','DISMISSED')",
                    (ingested.source_version_id,),
                )
                if not ingested.created and live is not None:
                    values.update(
                        state="DUPLICATE", reason_code="DUPLICATE_CONTENT", duplicate_of_source_id=ingested.duplicate_of,
                        reason_message="the same bytes are already in the library",
                    )
                else:  # new bytes, or bytes whose earlier candidate was dismissed: open a fresh one
                    values["state"] = "RECEIVED"
                self.writer.insert(Owner.CATALOG, Actor.INGESTER, "intake_candidates", values)
        except (_TooLarge, BlobTooLarge):
            return self._unsupported(base, "UNSUPPORTED_SIZE", f"larger than {self.config.max_bytes} bytes")
        return self.get(candidate_id)

    def _unsupported(self, base: dict[str, Any], code: str, message: str) -> Candidate:
        self.writer.insert(
            Owner.CATALOG, Actor.INGESTER, "intake_candidates",
            {**base, "state": "UNSUPPORTED", "reason_code": code, "reason_message": message},
        )
        return self.get(str(base["candidate_id"]))

    # -- CLASSIFYING ----------------------------------------------------------------------

    def classify(self, candidate_id: str, *, now: str | None = None) -> Candidate:
        self._own_transactions()
        stamp = now or _now()
        current = self.get(candidate_id)
        if current.state != "RECEIVED":
            return current  # already classified (re-run safe)
        self.writer.transition_intake_state(Actor.INGESTER, candidate_id, "CLASSIFYING", now=stamp)
        row = self.writer.query_one(
            "SELECT c.display_name, c.mime_type, c.size_bytes, v.blob_sha256 FROM intake_candidates c "
            "JOIN source_versions v ON v.id = c.source_version_id WHERE c.candidate_id = ?",
            (candidate_id,),
        )
        assert row is not None
        hint = (current.suggestion or {}).get("course_hint")
        try:
            with self.blobs.open(str(row["blob_sha256"])) as handle:
                head = handle.read(_PROBE_HEAD)
            outcome = classify_upload_item(
                name=str(row["display_name"]),
                mime_type=row["mime_type"],
                from_upload_folder=True,
                explicit_course_key=hint if isinstance(hint, str) and self._course(hint) is not None else None,
                origin=None,
                alias_index=self._alias_index(),
                semester=None,
                calendar=None,  # recording calendar import is a later R1c slice; transcripts wait for the user
                probe=SourceProbe(head, complete=int(row["size_bytes"] or 0) <= _PROBE_HEAD),
            )
            suggestion: dict[str, Any] = {**outcome.suggestion_fields(), **({"course_hint": hint} if hint else {})}
            suggestion["blockers"] = list(self._auto_blockers(outcome, hint))
        except (PolicyViolation, sqlite3.Error):
            raise  # an invariant breach or a broken database is not a property of the file
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stall the worker (review of PR #46)
            outcome = None
            suggestion = {"blockers": ["CLASSIFY_ERROR"], "error": f"{type(exc).__name__}: {exc}"[:500],
                          **({"course_hint": hint} if hint else {})}
        # Structure (review of PR #46, third finding of one class): once CLASSIFYING is committed the
        # candidate ALWAYS ends in AUTO_CONFIRMED or NEEDS_REVIEW. The automatic confirmation is its
        # own transaction; if anything in it refuses (id space, course, a future allocation rule) it
        # rolls back completely and the file goes to the user with that refusal as the blocker.
        # Only invariant breaches and database errors propagate (recover() then requeues).
        if outcome is not None and not suggestion["blockers"]:
            assert outcome.kind is not None and outcome.course_key is not None
            payload = json.dumps(suggestion, ensure_ascii=False, sort_keys=True, default=str)
            try:
                with self.writer.transaction():
                    material = self._create_material(Actor.INGESTER, candidate_id, outcome.course_key, outcome.kind.value, None, stamp)
                    self.writer.transition_intake_state(
                        Actor.INGESTER, candidate_id, "AUTO_CONFIRMED",
                        values={"suggestion_json": payload, "material_id": material}, now=stamp,
                    )
                return self.get(candidate_id)
            except PolicyViolation:
                raise
            except SyllvaError as exc:
                suggestion["blockers"] = [exc.code if _REASON_CODE.fullmatch(exc.code) else "AUTO_CONFIRM_REFUSED"]
                suggestion["error"] = str(exc)[:500]
        payload = json.dumps(suggestion, ensure_ascii=False, sort_keys=True, default=str)
        self.writer.transition_intake_state(
            Actor.INGESTER, candidate_id, "NEEDS_REVIEW", values={"suggestion_json": payload}, now=stamp
        )
        return self.get(candidate_id)

    def _auto_blockers(self, outcome: ClassificationOutcome, hint: object = None) -> tuple[str, ...]:
        blockers: list[str] = []
        if isinstance(hint, str) and self._course(hint) is None:
            blockers.append("COURSE_HINT_UNKNOWN")
        if outcome.kind is None:
            return (*blockers, "KIND_UNDECIDED")
        if outcome.kind is Kind.UNSUPPORTED:
            return (*blockers, "KIND_UNSUPPORTED")
        if outcome.course_key is None or self._course(outcome.course_key) is None:
            blockers.append("COURSE_UNRESOLVED")
        elif not self._code_ok(outcome.course_key):
            blockers.append("COURSE_CODE_INVALID")  # ids cannot be formed; the user fixes the course first
        if outcome.handling is not HandlingMode.NORMALIZE:
            blockers.append("NOT_NORMALIZABLE")
        if outcome.kind is Kind.TRANSCRIPT and (outcome.calendar is None or outcome.calendar.status is not MatchStatus.MATCHED):
            blockers.append("CALENDAR_UNMATCHED")
        if NOTE_CANVAS_WEEK_CONFLICT in outcome.notes:
            blockers.append("WEEK_CONFLICT")
        return tuple(blockers)

    # -- user decisions ---------------------------------------------------------------------

    def confirm(
        self,
        candidate_id: str,
        *,
        course_key: str,
        kind: str,
        role: str = "PRIMARY",
        session_id: str | None = None,
        new_session_held_on: str | None = None,
        now: str | None = None,
    ) -> Candidate:
        """The user's answer on the review screen (§11.2): course, kind, role, session."""

        stamp = now or _now()
        if self._course(course_key) is None:
            raise IntakeError("unknown course", details={"course_key": course_key})
        try:
            parsed = Kind(kind)
        except ValueError:
            raise IntakeError("unknown kind", details={"kind": kind}) from None
        if parsed is Kind.UNSUPPORTED:
            raise IntakeError("an unsupported file cannot be confirmed as a material")
        if not self._normalizable(candidate_id, parsed):
            raise IntakeError("this file cannot be processed as that kind", details={"kind": kind})
        if session_id is not None and new_session_held_on is not None:
            raise IntakeError("choose an existing session or a new one, not both")
        if new_session_held_on is not None:
            try:
                date.fromisoformat(new_session_held_on)
            except ValueError:
                raise IntakeError("held_on must be YYYY-MM-DD") from None
        with self.writer.transaction():
            if self.get(candidate_id).state != "NEEDS_REVIEW":
                raise IntakeError("only a candidate waiting for review can be confirmed")
            session_row: int | None = None
            if session_id is not None:
                found = self.writer.query_one(
                    "SELECT s.id FROM sessions s JOIN courses c ON c.id = s.course_id WHERE s.session_id = ? AND c.course_key = ?",
                    (session_id, course_key),
                )
                if found is None:
                    raise IntakeError("unknown session for this course", details={"session_id": session_id})
                session_row = int(found["id"])
            elif new_session_held_on is not None:
                session_row = self._create_session(course_key, new_session_held_on, stamp)
            material = self._create_material(Actor.UI_USER, candidate_id, course_key, parsed.value, session_row, stamp, role=role)
            self.writer.transition_intake_state(Actor.UI_USER, candidate_id, "CONFIRMED", values={"material_id": material}, now=stamp)
        return self.get(candidate_id)

    def dismiss(self, candidate_id: str, *, now: str | None = None) -> Candidate:
        self.writer.transition_intake_state(Actor.UI_USER, candidate_id, "DISMISSED", now=now or _now())
        return self.get(candidate_id)

    def replace(self, material_id: str, stream: BinaryIO, *, display_name: str, mime_type: str | None = None,
                now: str | None = None) -> Candidate:
        """The user replaces a material's file with a revision (§11.4); the action is the confirmation.

        Adds a NEW source version (``supersedes`` the current one; the old version is kept), opens a
        replacement candidate already CONFIRMED onto that material, and leaves processing to the
        worker. Older locators and AI artifacts become stale by fingerprint (I5) — nothing is
        rewritten. Refused while earlier work on the material is still pending, for identical bytes,
        and for bytes of an older version (restoring is an explicit, separate action).
        """

        self._own_transactions()
        stamp = now or _now()
        name = nfc(display_name)
        if PurePath(name).suffix.lower() not in NORMALIZER_BY_EXTENSION:
            raise IntakeError("this file type is not supported yet", details={"name": name})
        target = self.writer.query_one(
            "SELECT m.id, f.source_id, f.display_name FROM materials m JOIN source_files f ON f.id = m.source_file_id "
            "WHERE m.material_id = ? AND f.deleted_at IS NULL",
            (material_id,),
        )
        if target is None:
            raise IntakeError("unknown material", details={"material_id": material_id})
        if PurePath(str(target["display_name"])).suffix.lower() != PurePath(name).suffix.lower():
            raise IntakeError("a revision must keep the file type (it is normalized the same way)")
        busy = self.writer.query_one(
            "SELECT 1 FROM intake_candidates WHERE material_id = ? AND state IN ('AUTO_CONFIRMED','CONFIRMED','QUEUED','PROCESSING')",
            (int(target["id"]),),
        )
        if busy is not None:
            raise IntakeError("earlier work on this material is still pending", details={"material_id": material_id})
        candidate_id = "INT-" + secrets.token_hex(6)
        try:
            with self.writer.transaction():
                version = add_version(
                    self.writer, self.blobs, str(target["source_id"]),
                    cast(BinaryIO, _ChunkReader(_bounded(stream, self.config.max_bytes))), now=stamp,
                )
                if not version.created:
                    raise IntakeError("the file is identical to the current version")
                self.writer.insert(
                    Owner.CATALOG, Actor.INGESTER, "intake_candidates",
                    {
                        "candidate_id": candidate_id, "channel": "USER_DROP", "display_name": name, "mime_type": mime_type,
                        "size_bytes": version.size_bytes, "source_version_id": version.source_version_id, "state": "RECEIVED",
                        "replaces_material": int(target["id"]), "created_at": stamp, "updated_at": stamp,
                    },
                )
                self.writer.transition_intake_state(
                    Actor.UI_USER, candidate_id, "CONFIRMED", values={"material_id": int(target["id"])}, now=stamp
                )
                # the material has no current evidence until the new version is processed: say so (I3/I5)
                current = self.writer.query_one("SELECT processing_status FROM materials WHERE id = ?", (int(target["id"]),))
                if current is not None and current["processing_status"] != "PENDING":
                    self.writer.transition_material_status(Actor.INGESTER, material_id, "PENDING")
        except (_TooLarge, BlobTooLarge):
            raise IntakeError("the file is too large", details={"max_bytes": self.config.max_bytes}) from None
        except IngestError as exc:
            raise IntakeError(str(exc)) from exc
        return self.get(candidate_id)

    def retry(self, candidate_id: str, *, now: str | None = None) -> Candidate:
        """Queue a FAILED/PARTIAL/READY candidate for another processing run."""

        self.writer.transition_intake_state(Actor.INGESTER, candidate_id, "QUEUED", now=now or _now())
        return self.get(candidate_id)

    # -- PROCESSING -------------------------------------------------------------------------

    def process(self, candidate_id: str, *, now: str | None = None) -> Candidate:
        self._own_transactions()
        stamp = now or _now()
        row = self.writer.query_one(
            "SELECT c.state, c.display_name, c.source_version_id, c.attempt_count, m.material_id, m.processing_status, "
            "co.course_key, f.source_id, v.source_hash FROM intake_candidates c "
            "JOIN materials m ON m.id = c.material_id JOIN courses co ON co.id = m.course_id "
            "JOIN source_versions v ON v.id = c.source_version_id JOIN source_files f ON f.id = v.source_file_id "
            "WHERE c.candidate_id = ?",
            (candidate_id,),
        )
        if row is None or row["state"] not in ("AUTO_CONFIRMED", "CONFIRMED", "QUEUED"):
            raise IntakeError("candidate is not ready for processing", details={"candidate_id": candidate_id})
        material_id = str(row["material_id"])
        newer = self.writer.query_one(
            "SELECT 1 FROM source_versions v JOIN source_versions mine ON mine.source_file_id = v.source_file_id "
            "WHERE mine.id = ? AND v.source_version > mine.source_version",
            (int(row["source_version_id"]),),
        )
        if newer is not None:
            # §11.4 / I5: a run of a superseded version must never decide the material's state
            with self.writer.transaction():
                self.writer.transition_intake_state(Actor.INGESTER, candidate_id, "PROCESSING", now=stamp)
                self.writer.transition_intake_state(
                    Actor.INGESTER, candidate_id, "FAILED",
                    values={"reason_code": "SUPERSEDED", "reason_message": "a newer version of this file replaced it"}, now=stamp,
                )
            return self.get(candidate_id)
        job_id = self._job_start(row, material_id, stamp)
        with self.writer.transaction():
            self.writer.transition_intake_state(
                Actor.INGESTER, candidate_id, "PROCESSING",
                values={"attempt_count": int(row["attempt_count"]) + 1, "reason_code": None, "reason_message": None}, now=stamp,
            )
            self._material_to(material_id, "PROCESSING")
        kind = NORMALIZER_BY_EXTENSION[PurePath(str(row["display_name"])).suffix.lower()]
        try:
            result = normalize_source_version(
                self.writer, self.blobs, int(row["source_version_id"]), kind=kind,  # type: ignore[arg-type]
                entity_id=material_id, course_key=str(row["course_key"]), processor_version=self.config.processor_version,
            )
            status, reason, message = result.status, result.reason, None
        except (PolicyViolation, sqlite3.Error):
            raise  # invariant breach / broken database: loud, and recover() requeues the candidate
        except Exception as exc:  # noqa: BLE001 - recorded with its class; the worker keeps going (§11.2: always a cause)
            status, reason, message = "FAILED", "PROCESSING_ERROR", f"{type(exc).__name__}: {exc}"[:500]
        if status in ("READY", "PARTIAL"):
            final, material_status = status, status
        elif status == "NEEDS_REVIEW":  # nothing citable: the normalizer says why (NO_EXTRACTABLE_TEXT, ...)
            final, material_status = "FAILED", "NEEDS_REVIEW"
            reason, message = _as_code(reason, message, "NO_EXTRACTABLE_TEXT")
        else:
            final, material_status = "FAILED", "FAILED"
            reason, message = _as_code(reason, message, "NORMALIZE_FAILED")
        with self.writer.transaction():
            self._material_to(material_id, material_status)
            values: dict[str, Any] = {}
            if final == "FAILED" or reason:
                values = {"reason_code": reason, "reason_message": message}
            self.writer.transition_intake_state(Actor.INGESTER, candidate_id, final, values=values, now=stamp)
            self._job_finish(job_id, final, reason, stamp)
        return self.get(candidate_id)

    def run_pending(self, *, now: str | None = None) -> list[Candidate]:
        """One pass: classify what was received, process what was confirmed or queued."""

        done: list[Candidate] = []
        for candidate_id in self._ids("RECEIVED"):
            if self.writer.query_one(
                "SELECT 1 FROM intake_candidates WHERE candidate_id = ? AND replaces_material IS NOT NULL", (candidate_id,)
            ) is None:  # replacements are confirmed by the replace action, never classified
                self.classify(candidate_id, now=now)
        for candidate_id in self._ids("AUTO_CONFIRMED", "CONFIRMED", "QUEUED"):
            done.append(self.process(candidate_id, now=now))
        return done

    def recover(self, *, now: str | None = None) -> int:
        """After a crash: PROCESSING -> QUEUED, CLASSIFYING -> RECEIVED (§11.2)."""

        stamp = now or _now()
        count = 0
        for candidate_id in self._ids("PROCESSING"):
            self.writer.transition_intake_state(Actor.INGESTER, candidate_id, "QUEUED", now=stamp)
            count += 1
        for candidate_id in self._ids("CLASSIFYING"):
            self.writer.transition_intake_state(Actor.INGESTER, candidate_id, "RECEIVED", now=stamp)
            count += 1
        return count

    # -- internals --------------------------------------------------------------------------

    def _own_transactions(self) -> None:
        # classify/process commit step by step and roll a refused automatic confirmation back on
        # its own; inside a caller's transaction (nesting joins it) that guarantee would not hold.
        if self.writer.in_transaction:
            raise RuntimeError("intake steps own their transactions; do not call them inside writer.transaction()")

    def _normalizable(self, candidate_id: str, kind: Kind) -> bool:
        row = self.writer.query_one(
            "SELECT c.display_name, c.mime_type, c.size_bytes, v.blob_sha256 FROM intake_candidates c "
            "JOIN source_versions v ON v.id = c.source_version_id WHERE c.candidate_id = ?",
            (candidate_id,),
        )
        if row is None:
            raise IntakeError("unknown intake candidate", details={"candidate_id": candidate_id})
        with self.blobs.open(str(row["blob_sha256"])) as handle:
            head = handle.read(_PROBE_HEAD)
        name = str(row["display_name"])
        mime = row["mime_type"] or ("application/pdf" if name.lower().endswith(".pdf") else TRANSCRIPT_RULE_MIME)
        extension = name.rsplit(".", 1)[1].lower() if "." in name else None
        return handling_mode(
            kind, mime_type=mime, extension=extension, head=head, payload_complete=int(row["size_bytes"] or 0) <= _PROBE_HEAD
        ) is HandlingMode.NORMALIZE

    def _course(self, course_key: str) -> Any:
        return self.writer.query_one("SELECT id, course_key, code FROM courses WHERE course_key = ?", (course_key,))

    def _alias_index(self) -> CourseAliasIndex:
        entries: dict[str, set[str]] = {}
        for row in self.writer.query("SELECT id, course_key, name, code FROM courses"):
            entries.setdefault(str(row["course_key"]), set()).update(
                str(v) for v in (row["course_key"], row["name"], row["code"]) if v
            )
        for row in self.writer.query("SELECT c.course_key, a.alias_nfc FROM course_aliases a JOIN courses c ON c.id = a.course_id"):
            entries.setdefault(str(row["course_key"]), set()).add(str(row["alias_nfc"]))
        return CourseAliasIndex.build(entries.items())

    def _code_ok(self, course_key: str) -> bool:
        course = self._course(course_key)
        return course is not None and bool(_CODE_RE.fullmatch(str(course["code"] or course["course_key"])))

    def _code(self, course_key: str) -> tuple[int, str]:
        course = self._course(course_key)
        if course is None:
            raise IntakeError("unknown course", details={"course_key": course_key})
        code = str(course["code"] or course["course_key"])
        if not _CODE_RE.fullmatch(code):
            raise IntakeError("course code cannot form entity ids", details={"course_key": course_key, "code": code})
        return int(course["id"]), code

    def _next_id(self, table: str, column: str, course_row: int, prefix: str) -> tuple[str, int]:
        rows = self.writer.query(f"SELECT {column} FROM {table} WHERE course_id = ?", (course_row,))  # fixed identifiers
        used = [int(str(r[0])[len(prefix):]) for r in rows if str(r[0]).startswith(prefix) and str(r[0])[len(prefix):].isdigit()]
        number = max(used, default=0) + 1
        if number > _MAX_SEQUENCE:
            raise IdSpaceExhausted("entity id space for this course is exhausted", details={"prefix": prefix})
        return f"{prefix}{number:02d}", number

    def _create_session(self, course_key: str, held_on: str, stamp: str) -> int:
        course_row, code = self._code(course_key)
        session_id, number = self._next_id("sessions", "session_id", course_row, f"{code}-S")  # §11.5: new sessions get max+1
        return self.writer.insert(
            Owner.CATALOG, Actor.UI_USER, "sessions",
            {"session_id": session_id, "course_id": course_row, "number": number, "held_on": held_on, "created_at": stamp},
        )

    def _create_material(
        self, actor: Actor, candidate_id: str, course_key: str, kind: str, session_row: int | None, stamp: str, *, role: str = "PRIMARY"
    ) -> int:
        course_row, code = self._code(course_key)
        material_id, _ = self._next_id("materials", "material_id", course_row, f"{code}-M")
        source = self.writer.query_one(
            "SELECT v.source_file_id FROM intake_candidates c JOIN source_versions v ON v.id = c.source_version_id WHERE c.candidate_id = ?",
            (candidate_id,),
        )
        assert source is not None
        return self.writer.insert(
            Owner.CATALOG, actor, "materials",
            {
                "material_id": material_id, "course_id": course_row, "session_id": session_row,
                "source_file_id": int(source["source_file_id"]), "kind": kind, "role": role, "created_at": stamp,
            },
        )

    def _material_to(self, material_id: str, status: str) -> None:
        current = self.writer.query_one("SELECT processing_status FROM materials WHERE material_id = ?", (material_id,))
        assert current is not None
        old = str(current["processing_status"])
        if old == status:
            return
        if status == "PROCESSING" and old in ("READY", "FAILED", "NEEDS_REVIEW"):
            self.writer.transition_material_status(Actor.INGESTER, material_id, "PENDING")  # reprocess goes back through PENDING
        self.writer.transition_material_status(Actor.INGESTER, material_id, status)

    def _job_start(self, row: Any, material_id: str, stamp: str) -> str:
        key = f"normalize:{int(row['source_version_id'])}:{self.config.processor_version}"
        existing = self.writer.query_one("SELECT id, attempt_count FROM jobs WHERE job_key = ?", (key,))
        if existing is not None:
            self.writer.update(
                Owner.SYSTEM, Actor.INGESTER, "jobs", {"id": existing["id"]},
                {"status": "PROCESSING", "stage": "NORMALIZE", "attempt_count": int(existing["attempt_count"]) + 1,
                 "error_code": None, "error_class": None, "updated_at": stamp, "completed_at": None},
            )
            return str(existing["id"])
        job_id = "JOB-" + secrets.token_hex(6)
        self.writer.insert(
            Owner.SYSTEM, Actor.INGESTER, "jobs",
            {
                "id": job_id, "job_key": key, "operation": "NORMALIZE", "stage": "NORMALIZE", "status": "PROCESSING",
                "course_key": row["course_key"], "source_id": row["source_id"], "source_hash": row["source_hash"],
                "target_entity_id": material_id, "attempt_count": 1, "created_at": stamp, "updated_at": stamp,
            },
        )
        return job_id

    def _job_finish(self, job_id: str, status: str, reason: str | None, stamp: str) -> None:
        self.writer.update(
            Owner.SYSTEM, Actor.INGESTER, "jobs", {"id": job_id},
            {"status": status, "error_code": reason if status == "FAILED" else None, "updated_at": stamp, "completed_at": stamp},
        )


_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]*")


def _as_code(reason: str | None, message: str | None, default: str) -> tuple[str, str | None]:
    """Normalizers return either a code or a human sentence; the candidate stores both apart."""

    if reason and _REASON_CODE.fullmatch(reason):
        return reason, message
    return default, reason or message


class _ChunkReader:
    """Adapts a chunk iterator to the ``read()`` interface ``BlobStore.put_stream`` uses."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")


__all__ = ["NORMALIZER_BY_EXTENSION", "PROCESSOR_VERSION", "Candidate", "IntakeConfig", "IntakeError", "LocalIntake"]
