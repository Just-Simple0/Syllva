"""Evidence assembly for the three canonical modes (plan section 5).

This module depends only on a small injected SessionEvidenceEngine Protocol
-- it never assumes C5's Queue/write profile is active, and never assumes a
specific concrete graph reader. A semester with no configured Material Usage
mapping (C5 not activated for it) must still support transcript-only.v1
normally; confirmed-lecture.v1 and provisional-selected.v1 are blocked with
an honest WAITING_CONTEXT reason in that case, never silently returned as an
empty 'ready' result.

Mode-scoped invariant: transcript-only.v1 calls ONLY the transcript entry
point below, never any Usage/Material method -- unrelated Usage read/
permission errors for other materials must never break a transcript-only
request or enter its manifest. confirmed-lecture.v1 snapshots the full
eligible Verified-Usage membership before any budgeting.
provisional-selected.v1 reads only the explicit selected set, nothing more.
The concrete graph adapter (owned outside this package) supplies raw exact
dependencies per mode-scoped call; deciding which calls a mode makes, and
in what order, is this module's responsibility, not the adapter's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .identity import CONFIRMED_LECTURE, PROVISIONAL_SELECTED, TRANSCRIPT_ONLY, sha256_hex


@dataclass(frozen=True)
class TranscriptEvidence:
    source_version: int
    source_hash: str
    chunks: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    partial: bool = False


@dataclass(frozen=True)
class MaterialUsageEvidence:
    """One Material Usage as evidence for confirmed/provisional modes."""

    material_app_id: str
    usage_app_id: str
    provider_page_id: str
    usage_role: str
    verified: bool
    material_type: str
    source_hash: str | None = None
    source_version: int | None = None
    chunks: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    read: bool = True
    partial: bool = False
    start_page: int | None = None
    end_page: int | None = None


@runtime_checkable
class SessionEvidenceEngine(Protocol):
    """Minimal injected capability this module needs, one entry point per mode.

    Composition (a real RetrievalEngine bound to C5's SemesterGraphView, or a
    reduced reader for a C5-inactive semester) is owned outside this package.
    Each method below must read only what its own mode needs -- never a
    shared 'collect everything then filter' implementation, so an unrelated
    material's read/permission error cannot break a transcript-only or a
    narrowly-scoped provisional request."""

    def get_transcript_evidence(self, session_id: str) -> TranscriptEvidence | None: ...

    def usage_mapping_available(self, session_id: str) -> bool:
        """False when Material Usage cannot be read for this semester/session
        (e.g. C5's material_usage_data_source_id is not configured). This is a
        distinct, honestly-reported state from 'available but empty'."""
        ...

    def list_verified_lecture_usages(self, session_id: str) -> Sequence[MaterialUsageEvidence]:
        """Every currently-Verified Usage whose Material.Type is Lecture Slides
        or Professor Notes, all Usage Roles, full pre-budget membership
        (confirmed-lecture.v1)."""
        ...

    def get_selected_usage(
        self, session_id: str, material_id: str,
    ) -> MaterialUsageEvidence | None:
        """One explicitly user-selected Material/Usage for this Session
        (provisional-selected.v1); None if invalid or not part of this Session."""
        ...


class EvidenceWaitingError(RuntimeError):
    """Evidence for this mode is not currently available.

    Maps to durable state WAITING_CONTEXT (never silent fallback to
    transcript-only or a degraded 'ready' result)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class InvalidSelectionError(ValueError):
    """A provisional-selected.v1 request named an invalid/non-Session material."""


@dataclass(frozen=True)
class EvidenceManifest:
    evidence_mode: str
    session_id: str
    transcript: TranscriptEvidence | None
    usages: tuple[MaterialUsageEvidence, ...]
    coverage: str  # "FULL" or "PARTIAL"
    missing_dependencies: tuple[str, ...] = field(default_factory=tuple)

    @property
    def manifest_hash(self) -> str:
        """Bound into note_key per plan section 5: mode + exact membership.

        Sorted (Material app ID, Usage app ID, provider page ID) -- role
        priority is never inferred (contract 6.1)."""

        def chunk_digest(chunks: tuple[Mapping[str, Any], ...]) -> tuple[tuple[str, str], ...]:
            # A digest of each chunk's locator/content, not the raw text
            # itself -- the manifest hash identifies exactly which evidence
            # was authorized, without embedding full document bodies in it.
            return tuple(
                sorted(
                    (str(chunk.get("locator", "")), sha256_hex(chunk.get("content", "")))
                    for chunk in chunks
                )
            )

        usage_tuples = sorted(
            (u.material_app_id, u.usage_app_id, u.provider_page_id, u.usage_role,
             u.verified, u.source_hash, u.source_version, u.partial, u.start_page, u.end_page,
             chunk_digest(u.chunks))
            for u in self.usages
        )
        transcript_tuple = (
            None
            if self.transcript is None
            else (
                self.transcript.source_hash, self.transcript.source_version,
                self.transcript.partial, chunk_digest(self.transcript.chunks),
            )
        )
        return sha256_hex(
            ["study-note-evidence.v1", self.evidence_mode, self.session_id,
             transcript_tuple, usage_tuples]
        )


def assemble_evidence(
    engine: SessionEvidenceEngine,
    *,
    session_id: str,
    evidence_mode: str,
    selected_materials: Sequence[str] | None = None,
) -> EvidenceManifest:
    """Assemble evidence for exactly one canonical mode.

    Raises EvidenceWaitingError when the mode cannot currently be satisfied
    (missing transcript, no configured Usage mapping, or no eligible
    Verified Usage) -- callers must transition to WAITING_CONTEXT with the
    exact reason text, never substitute a different mode."""
    if evidence_mode == TRANSCRIPT_ONLY:
        if selected_materials:
            raise ValueError("transcript-only.v1 must not include selected_materials")
        transcript = engine.get_transcript_evidence(session_id)
        if transcript is None:
            raise EvidenceWaitingError("입력·연결 필요: transcript is not available")
        return EvidenceManifest(
            evidence_mode=evidence_mode, session_id=session_id, transcript=transcript,
            usages=(), coverage="PARTIAL" if transcript.partial else "FULL",
        )

    if evidence_mode == CONFIRMED_LECTURE:
        transcript = engine.get_transcript_evidence(session_id)
        if not engine.usage_mapping_available(session_id):
            raise EvidenceWaitingError(
                "자료 사용 확인 필요: Material Usage mapping is not configured for this semester"
            )
        usages = tuple(engine.list_verified_lecture_usages(session_id))
        if not usages:
            raise EvidenceWaitingError(
                "자료 사용 확인 필요: no eligible Verified Lecture Slides/Professor Notes usage"
            )
        missing = tuple(u.usage_app_id for u in usages if not u.read)
        any_partial = (transcript is not None and transcript.partial) or any(u.partial for u in usages)
        coverage = "PARTIAL" if (missing or any_partial) else "FULL"
        return EvidenceManifest(
            evidence_mode=evidence_mode, session_id=session_id, transcript=transcript,
            usages=usages, coverage=coverage, missing_dependencies=missing,
        )

    if evidence_mode == PROVISIONAL_SELECTED:
        if not selected_materials:
            raise ValueError("provisional-selected.v1 requires at least one selected material")
        if not engine.usage_mapping_available(session_id):
            raise EvidenceWaitingError(
                "자료 사용 확인 필요: Material Usage mapping is not configured for this semester"
            )
        transcript = engine.get_transcript_evidence(session_id)
        provisional_usages: list[MaterialUsageEvidence] = []
        provisional_missing: list[str] = []
        for material_id in selected_materials:
            usage = engine.get_selected_usage(session_id, material_id)
            if usage is None:
                raise InvalidSelectionError(
                    f"material {material_id!r} is not a valid Session selection"
                )
            provisional_usages.append(usage)
            if not usage.read:
                provisional_missing.append(usage.usage_app_id)
        any_partial = (transcript is not None and transcript.partial) or any(
            u.partial for u in provisional_usages
        )
        coverage = "PARTIAL" if (provisional_missing or any_partial) else "FULL"
        return EvidenceManifest(
            evidence_mode=evidence_mode, session_id=session_id, transcript=transcript,
            usages=tuple(provisional_usages), coverage=coverage,
            missing_dependencies=tuple(provisional_missing),
        )

    raise ValueError(f"unsupported canonical evidence_mode: {evidence_mode!r}")


__all__ = [
    "EvidenceManifest",
    "EvidenceWaitingError",
    "InvalidSelectionError",
    "MaterialUsageEvidence",
    "SessionEvidenceEngine",
    "TranscriptEvidence",
    "assemble_evidence",
]
