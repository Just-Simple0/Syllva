"""Enumerations used by the model-independent ULS domain layer.

The domain uses two different status representations on purpose:

* :class:`ProcessingStatus` is the common job/outcome representation and is
  serialized with upper-case values.
* :class:`DerivativeStatus` is the lower-case representation used in
  normalized derivative front matter.

Keeping the conversion here makes the boundary explicit and avoids having
individual adapters invent their own spelling.
"""

from __future__ import annotations

from enum import Enum


class _ValueEnum(str, Enum):
    """A string enum whose string form is its wire value."""

    def __str__(self) -> str:
        return self.value


class ProcessingStatus(_ValueEnum):
    """Common job and processing outcome states from the v1.2 contract."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


# Both names are useful at the application boundary.  They intentionally
# refer to one enum so a job status and a processing status cannot diverge.
JobStatus = ProcessingStatus
Status = ProcessingStatus


class DerivativeStatus(_ValueEnum):
    """Status spelling used by normalized derivative metadata."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    PARTIAL = "partial"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


NormalizationStatus = DerivativeStatus


_PROCESSING_TO_DERIVATIVE: dict[ProcessingStatus, DerivativeStatus] = {
    ProcessingStatus.PENDING: DerivativeStatus.PENDING,
    ProcessingStatus.PROCESSING: DerivativeStatus.PROCESSING,
    ProcessingStatus.READY: DerivativeStatus.READY,
    ProcessingStatus.PARTIAL: DerivativeStatus.PARTIAL,
    ProcessingStatus.NEEDS_REVIEW: DerivativeStatus.NEEDS_REVIEW,
    ProcessingStatus.FAILED: DerivativeStatus.FAILED,
}
_DERIVATIVE_TO_PROCESSING: dict[DerivativeStatus, ProcessingStatus] = {
    derivative: processing
    for processing, derivative in _PROCESSING_TO_DERIVATIVE.items()
}


def _coerce_processing_status(
    status: ProcessingStatus | DerivativeStatus | str,
) -> ProcessingStatus:
    if isinstance(status, ProcessingStatus):
        return status
    if isinstance(status, DerivativeStatus):
        return _DERIVATIVE_TO_PROCESSING[status]
    if isinstance(status, str):
        try:
            return ProcessingStatus(status)
        except ValueError:
            try:
                return ProcessingStatus(status.upper())
            except ValueError:
                try:
                    return _DERIVATIVE_TO_PROCESSING[DerivativeStatus(status.lower())]
                except (KeyError, ValueError) as exc:
                    raise ValueError(f"Unknown processing status: {status!r}") from exc
    raise TypeError(f"Expected a processing/derivative status, got {type(status).__name__}")


def _coerce_derivative_status(
    status: ProcessingStatus | DerivativeStatus | str,
) -> DerivativeStatus:
    if isinstance(status, DerivativeStatus):
        return status
    if isinstance(status, ProcessingStatus):
        return _PROCESSING_TO_DERIVATIVE[status]
    if isinstance(status, str):
        try:
            return DerivativeStatus(status)
        except ValueError:
            try:
                return _PROCESSING_TO_DERIVATIVE[ProcessingStatus(status.upper())]
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Unknown derivative status: {status!r}") from exc
    raise TypeError(f"Expected a processing/derivative status, got {type(status).__name__}")


def to_derivative_status(
    status: ProcessingStatus | DerivativeStatus | str,
) -> DerivativeStatus:
    """Convert a job/processing status to lower-case derivative status."""

    return _coerce_derivative_status(status)


def to_processing_status(
    status: ProcessingStatus | DerivativeStatus | str,
) -> ProcessingStatus:
    """Convert a derivative status to the common upper-case status."""

    return _coerce_processing_status(status)


# Descriptive aliases keep the conversion discoverable for callers that use
# the two domain terms instead of the shorter ``to_*`` names.
processing_status_to_derivative_status = to_derivative_status
job_status_to_derivative_status = to_derivative_status
processing_to_derivative_status = to_derivative_status
job_to_derivative_status = to_derivative_status
derivative_status_to_processing_status = to_processing_status
derivative_status_to_job_status = to_processing_status
derivative_to_processing_status = to_processing_status
derivative_to_job_status = to_processing_status
processing_to_derivative = to_derivative_status
derivative_to_processing = to_processing_status
to_normalization_status = to_derivative_status
to_job_status = to_processing_status


class OwnershipZone(_ValueEnum):
    """Ownership labels that must not be silently crossed."""

    SOURCE = "SOURCE"
    AI = "AI"
    USER = "USER"


class Explicitness(_ValueEnum):
    """Whether an AI enrichment signal is explicitly stated in its source.

    This is deliberately separate from the LLM adapter's ``proposal`` /
    ``fact`` classification.  Enrichment remains an AI proposal even when a
    source-backed quote proves that the underlying point is explicit.
    """

    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


class RetrievalIntent(_ValueEnum):
    """Primary retrieval modes defined by the v1.2 retrieval contract."""

    SESSION = "SESSION"
    CONCEPT = "CONCEPT"
    EXAM = "EXAM"
    ACTIVITY = "ACTIVITY"
    USER_NOTE = "USER_NOTE"
    VERIFY = "VERIFY"


class SourceAuthority(_ValueEnum):
    """Source authority classes in descending academic authority order.

    ``rank`` follows the usual ranking convention: 1 is the strongest
    authority and larger numbers are progressively weaker.  The string value
    is the stable lower-snake-case wire label; enum member names remain the
    frozen upper-case contract names.
    """

    PROFESSOR_MATERIAL = "professor_material"
    PROFESSOR_TRANSCRIPT = "professor_transcript"
    OFFICIAL_ACTIVITY_OR_EXAM = "official_activity_or_exam"
    USER_SOURCE = "user_source"
    SUPPLEMENTAL_REFERENCE = "supplemental_reference"
    AI_ENRICHMENT = "ai_enrichment"
    EXTERNAL = "external"

    @classmethod
    def _missing_(cls, value: object) -> SourceAuthority | None:
        if isinstance(value, str):
            normalized = value.lower()
            for member in cls:
                if member.value == normalized or member.name.lower() == normalized:
                    return member
        return None

    @property
    def rank(self) -> int:
        return _SOURCE_AUTHORITY_RANK[self]

    @property
    def priority(self) -> int:
        """Alias for callers that use the policy term ``priority``."""

        return self.rank

    def __int__(self) -> int:
        return self.rank


_SOURCE_AUTHORITY_ORDER = (
    SourceAuthority.PROFESSOR_MATERIAL,
    SourceAuthority.PROFESSOR_TRANSCRIPT,
    SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    SourceAuthority.USER_SOURCE,
    SourceAuthority.SUPPLEMENTAL_REFERENCE,
    SourceAuthority.AI_ENRICHMENT,
    SourceAuthority.EXTERNAL,
)
_SOURCE_AUTHORITY_RANK: dict[SourceAuthority, int] = {
    authority: index for index, authority in enumerate(_SOURCE_AUTHORITY_ORDER, start=1)
}
SOURCE_AUTHORITY_RANK = dict(_SOURCE_AUTHORITY_RANK)
AUTHORITY_RANK = SOURCE_AUTHORITY_RANK


def _coerce_source_authority(authority: SourceAuthority | str) -> SourceAuthority:
    if isinstance(authority, SourceAuthority):
        return authority
    if isinstance(authority, str):
        try:
            return SourceAuthority(authority)
        except ValueError:
            try:
                return SourceAuthority[authority.upper()]
            except KeyError as exc:
                raise ValueError(f"Unknown source authority: {authority!r}") from exc
    raise TypeError(f"Expected a source authority, got {type(authority).__name__}")


def authority_rank(authority: SourceAuthority | str) -> int:
    """Return the comparable integer rank for a source authority."""

    return _coerce_source_authority(authority).rank


source_authority_rank = authority_rank


def is_at_least_as_authoritative(
    candidate: SourceAuthority | str,
    baseline: SourceAuthority | str,
) -> bool:
    """Return whether ``candidate`` is no weaker than ``baseline``."""

    return authority_rank(candidate) <= authority_rank(baseline)


def compare_authority(
    left: SourceAuthority | str,
    right: SourceAuthority | str,
) -> int:
    """Compare authorities by strength.

    The return value is positive when ``left`` is stronger, zero when equal,
    and negative when ``left`` is weaker.
    """

    left_rank = authority_rank(left)
    right_rank = authority_rank(right)
    return (right_rank > left_rank) - (right_rank < left_rank)


class AutomationActor(_ValueEnum):
    """Actor/capability contexts for guarded automation paths."""

    AUTOMATION = "AUTOMATION"
    APPROVAL_READER = "APPROVAL_READER"
    HUMAN_APPROVAL_APPLIER = "HUMAN_APPROVAL_APPLIER"


class FreshnessStatus(_ValueEnum):
    """Result of comparing enrichment provenance with a current fingerprint."""

    FRESH = "FRESH"
    STALE = "STALE"


Freshness = FreshnessStatus


__all__ = [
    "AUTHORITY_RANK",
    "AutomationActor",
    "DerivativeStatus",
    "Explicitness",
    "Freshness",
    "FreshnessStatus",
    "JobStatus",
    "NormalizationStatus",
    "OwnershipZone",
    "ProcessingStatus",
    "RetrievalIntent",
    "SOURCE_AUTHORITY_RANK",
    "SourceAuthority",
    "Status",
    "authority_rank",
    "compare_authority",
    "derivative_status_to_job_status",
    "derivative_status_to_processing_status",
    "derivative_to_job_status",
    "derivative_to_processing_status",
    "derivative_to_processing",
    "is_at_least_as_authoritative",
    "job_to_derivative_status",
    "job_status_to_derivative_status",
    "processing_to_derivative_status",
    "processing_to_derivative",
    "processing_status_to_derivative_status",
    "source_authority_rank",
    "to_derivative_status",
    "to_job_status",
    "to_normalization_status",
    "to_processing_status",
]
