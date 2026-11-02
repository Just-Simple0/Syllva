"""StudyNoteSubmissionCore -- transport-neutral core (plan section 2).

Owns exactly the five request/status/evidence/submit/cancel operations
against its own StudyNoteStore. Takes an opaque authenticated caller_context
on every call; never assumes 'the configured local channel'. No Notion/
Drive credentials, no RetrievalEngine access, no provider write capability --
those are StudyNoteHandler's (worker-side) concerns, not this core's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .config import StudyNoteConfig
from .identity import (
    CONFIRMED_LECTURE,
    PROVISIONAL_SELECTED,
    TRANSCRIPT_ONLY,
    normalize_evidence_mode,
    request_payload_hash,
)
from .store import DifferentPayloadReplayError, GrantUnavailableError, StudyNoteStore


class CallerMismatchError(PermissionError):
    """caller_context does not own the referenced request/grant."""


class UnknownRequestError(KeyError):
    """client_request_id/grant_id is not known to this store."""


class DraftTooLargeError(ValueError):
    """draft_text exceeds config.max_draft_chars."""


class StudyNoteSubmissionCore:
    def __init__(self, store: StudyNoteStore, *, config: StudyNoteConfig) -> None:
        self.store = store
        self.config = config

    def request_study_note(
        self,
        *,
        idempotency_key: str,
        caller_context: str,
        session_id: str,
        evidence_mode: str,
        selected_materials: Sequence[str] | None = None,
        learner_request: str | None = None,
    ) -> dict[str, Any]:
        canonical_mode = normalize_evidence_mode(evidence_mode)
        selected = tuple(selected_materials or ())
        if selected_materials is not None and (
            isinstance(selected_materials, (str, bytes))
            or any(not isinstance(value, str) or not value.strip() for value in selected)
        ):
            raise ValueError("selected_materials must contain non-empty material ids")
        if canonical_mode in {TRANSCRIPT_ONLY, CONFIRMED_LECTURE} and selected:
            raise ValueError(f"{canonical_mode} must not include selected_materials")
        if canonical_mode == PROVISIONAL_SELECTED and not selected:
            raise ValueError("provisional-selected.v1 requires at least one selected material")
        payload_hash = request_payload_hash(
            session_id=session_id, evidence_mode=canonical_mode,
            selected_materials=selected, learner_request=learner_request,
        )
        try:
            row = self.store.create_or_replay_client_request(
                idempotency_key=idempotency_key, caller_context=caller_context,
                payload_hash=payload_hash, session_id=session_id,
                evidence_mode=canonical_mode, selected_materials=selected,
                learner_request=learner_request,
            )
        except DifferentPayloadReplayError as exc:
            raise ValueError(str(exc)) from exc
        return {"client_request_id": row["client_request_id"], "status": row["state"]}

    def _owned_client_request(self, client_request_id: str, caller_context: str) -> dict[str, Any]:
        row = self.store.get_client_request(client_request_id)
        if row is None:
            raise UnknownRequestError(client_request_id)
        if row["caller_context"] != caller_context:
            raise CallerMismatchError("client_request_id belongs to a different caller")
        return row

    def get_study_note_status(self, client_request_id: str, *, caller_context: str) -> dict[str, Any]:
        row = self._owned_client_request(client_request_id, caller_context)
        cancel_requested = row["cancel_requested_at"] is not None
        status = row["display_status"] or row["state"]
        note_key = row["note_key"]
        attempt_no = row["attempt_no"]
        result: dict[str, Any] = {
            "status": status,
            "client_request_id": client_request_id,
            "cancel_requested": cancel_requested,
        }
        if row["display_reason"]:
            result["reason"] = row["display_reason"]
        if row["artifact_link"]:
            result["artifact_link"] = row["artifact_link"]
        if not note_key or attempt_no is None:
            if row["head_generation"] is not None:
                result["head_generation"] = row["head_generation"]
            return result
        result.update(
            note_key=note_key,
            head_generation=row["head_generation"],
            attempt_no=attempt_no,
            evidence_mode=row["evidence_mode"],
        )
        prepared = self.store.get_prepared_context(note_key, attempt_no)
        if prepared is not None:
            result["coverage"] = prepared["context"].get("coverage")
        # Request/caller-bound lookup only -- never the most recent grant
        # for this note_key/attempt_no in general, which could belong to a
        # different request or caller sharing the same attempt.
        grant_row = self.store.get_open_grant_for_request(client_request_id)
        if grant_row is not None:
            result["grant_id"] = grant_row["grant_id"]
            result["grant_expires_at"] = grant_row["expires_at"]
        return result

    def get_study_note_evidence(self, client_request_id: str, *, caller_context: str) -> dict[str, Any]:
        row = self._owned_client_request(client_request_id, caller_context)
        note_key = row["note_key"]
        if not note_key:
            raise UnknownRequestError("evidence is not prepared yet for this request")
        prepared = self.store.get_prepared_context(note_key, row["attempt_no"])
        if prepared is None:
            raise UnknownRequestError("evidence is not prepared yet for this request")
        return prepared

    def submit_study_note_draft(
        self, *, grant_id: str, draft_text: str, caller_context: str,
        client_model_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if len(draft_text) > self.config.max_draft_chars:
            raise DraftTooLargeError(
                f"draft_text exceeds max_draft_chars ({self.config.max_draft_chars})"
            )
        grant = self.store.get_grant(grant_id)
        if grant is None:
            raise UnknownRequestError(grant_id)
        if grant["caller_context"] != caller_context:
            raise CallerMismatchError("grant_id belongs to a different caller")
        try:
            draft, created = self.store.consume_grant_and_insert_draft(
                grant_id=grant_id, draft_text=draft_text,
                client_model_metadata=client_model_metadata,
            )
        except GrantUnavailableError as exc:
            raise ValueError(str(exc)) from exc
        except DifferentPayloadReplayError as exc:
            raise ValueError(str(exc)) from exc
        return {"draft_id": draft["draft_id"], "status": "received", "created": created}

    def cancel_study_note_request(self, client_request_id: str, *, caller_context: str) -> dict[str, Any]:
        try:
            row = self.store.request_cancel(client_request_id, caller_context=caller_context)
        except KeyError as exc:
            raise UnknownRequestError(client_request_id) from exc
        except PermissionError as exc:
            raise CallerMismatchError(str(exc)) from exc
        return {"client_request_id": client_request_id, "cancel_requested": True,
                "state": row["state"]}


__all__ = [
    "CallerMismatchError",
    "DraftTooLargeError",
    "StudyNoteSubmissionCore",
    "UnknownRequestError",
]
