"""Identity helpers: canonical evidence-mode strings, UUID4 ids, payload hashing.

Canonical mode identities are fixed wire/storage/hash values per the accepted
plan (.review/c6-plan.md section 5).  Internal short aliases are accepted at
the API boundary and normalized one-way before any persistence or hashing.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from uls.intake.identity import canonical_json

TRANSCRIPT_ONLY = "transcript-only.v1"
CONFIRMED_LECTURE = "confirmed-lecture.v1"
PROVISIONAL_SELECTED = "provisional-selected.v1"

CANONICAL_EVIDENCE_MODES = (TRANSCRIPT_ONLY, CONFIRMED_LECTURE, PROVISIONAL_SELECTED)

# Internal/short aliases accepted at the request_study_note boundary.  Every
# alias maps one-way to exactly one canonical value; canonical values also
# map to themselves so callers may pass either spelling.
_MODE_ALIASES: dict[str, str] = {
    "transcript_only": TRANSCRIPT_ONLY,
    "transcript-only": TRANSCRIPT_ONLY,
    TRANSCRIPT_ONLY: TRANSCRIPT_ONLY,
    "confirmed_lecture": CONFIRMED_LECTURE,
    "confirmed-lecture": CONFIRMED_LECTURE,
    CONFIRMED_LECTURE: CONFIRMED_LECTURE,
    "provisional": PROVISIONAL_SELECTED,
    "provisional_selected": PROVISIONAL_SELECTED,
    "provisional-selected": PROVISIONAL_SELECTED,
    PROVISIONAL_SELECTED: PROVISIONAL_SELECTED,
}


class UnknownEvidenceModeError(ValueError):
    """An evidence_mode string does not map to a canonical identity."""


def normalize_evidence_mode(value: Any) -> str:
    """Return the canonical wire/storage/hash mode string for ``value``.

    This is the single one-way normalization point.  Callers must call this
    before persisting, comparing, or hashing an evidence_mode -- never store
    or hash an alias directly.
    """

    if not isinstance(value, str) or not value.strip():
        raise UnknownEvidenceModeError("evidence_mode must be a non-empty string")
    canonical = _MODE_ALIASES.get(value.strip())
    if canonical is None:
        raise UnknownEvidenceModeError(f"unknown evidence_mode: {value!r}")
    return canonical


def new_uuid4() -> str:
    """Return a fresh UUID4 string suitable for a RequestRow.page_id.

    RequestCoordinator.RequestRow.__post_init__ requires ``str(UUID(page_id))``
    to succeed (coordinator.py:30); every client_request_id and grant_id is
    generated here so that constraint always holds.
    """

    return str(uuid.uuid4())


def is_uuid4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return str(parsed) == value and parsed.version == 4


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def request_payload_hash(
    *,
    session_id: str,
    evidence_mode: str,
    selected_materials: Sequence[str] | None,
    learner_request: str | None,
) -> str:
    """Hash the exact request payload identity for idempotency dedup.

    ``evidence_mode`` must already be canonical (call
    :func:`normalize_evidence_mode` first) so an alias and its canonical
    spelling never hash to different values.
    """

    if evidence_mode not in CANONICAL_EVIDENCE_MODES:
        raise UnknownEvidenceModeError(
            f"request_payload_hash requires an already-canonical evidence_mode, got {evidence_mode!r}"
        )
    return sha256_hex(
        [
            "study-note-client-request.v1",
            session_id,
            evidence_mode,
            sorted(selected_materials or ()),
            learner_request or "",
        ]
    )


def operation_id(
    *,
    client_request_id: str | None,
    note_key: str | None,
    head_generation: int | None,
    attempt_no: int | None,
    boundary: str,
    payload_hash: str,
) -> str:
    """Deterministic cross-store journal operation identity (plan section 7).

    Unknown ``note_key``/``head_generation``/``attempt_no`` at pre-claim
    boundaries are passed as ``None`` and included literally -- never guessed
    or substituted with a placeholder that could collide with a real value.
    """

    if not isinstance(boundary, str) or not boundary.strip():
        raise ValueError("boundary must be a non-empty string")
    if not isinstance(payload_hash, str) or not payload_hash.strip():
        raise ValueError("payload_hash must be a non-empty string")
    return sha256_hex(
        [
            "study-note-operation.v1",
            client_request_id,
            note_key,
            head_generation,
            attempt_no,
            boundary,
            payload_hash,
        ]
    )


__all__ = [
    "CANONICAL_EVIDENCE_MODES",
    "CONFIRMED_LECTURE",
    "PROVISIONAL_SELECTED",
    "TRANSCRIPT_ONLY",
    "UnknownEvidenceModeError",
    "is_uuid4",
    "new_uuid4",
    "normalize_evidence_mode",
    "operation_id",
    "request_payload_hash",
    "sha256_hex",
]
