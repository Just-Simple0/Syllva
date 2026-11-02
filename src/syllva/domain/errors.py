"""Structured errors shared by ULS domain and adapter boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class SyllvaError(Exception):
    """Base class for safe, structured ULS failures."""

    code = "ULS_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = type(self).code
        self.message = message or self.code
        self.details = dict(details) if details is not None else {}
        super().__init__(self.message)


class ParseError(SyllvaError):
    """Base class for domain text parsing failures."""

    code = "PARSE_ERROR"


class CourseKeyParseError(ParseError):
    code = "INVALID_COURSE_KEY"


class EntityIdParseError(ParseError):
    code = "INVALID_ENTITY_ID"


class LocatorParseError(SyllvaError):
    code = "LOCATOR_PARSE_ERROR"


class EntityNotFoundError(SyllvaError):
    code = "ENTITY_NOT_FOUND"


class EntityAmbiguousError(SyllvaError):
    code = "ENTITY_AMBIGUOUS"


class ResolutionExpiredError(SyllvaError):
    code = "RESOLUTION_EXPIRED"


class InvalidCandidateError(SyllvaError):
    code = "INVALID_CANDIDATE"


class ContextExpiredError(SyllvaError):
    code = "CONTEXT_EXPIRED"


class LocatorNotAllowedError(SyllvaError):
    code = "LOCATOR_NOT_ALLOWED"


class LocatorStaleError(SyllvaError):
    code = "LOCATOR_STALE"


class PolicyDeniedError(SyllvaError):
    code = "POLICY_DENIED"


class PolicyViolation(SyllvaError):
    """Raised when an actor attempts a policy-forbidden domain mutation."""

    code = "POLICY_DENIED"


class SourceUnavailableError(SyllvaError):
    code = "SOURCE_UNAVAILABLE"


class SourcePartialError(SyllvaError):
    code = "SOURCE_PARTIAL"


class StaleEnrichmentError(SyllvaError):
    code = "STALE_ENRICHMENT"


class InvalidSubmissionRefError(SyllvaError):
    code = "INVALID_SUBMISSION_REF"


class ProviderRateLimitedError(SyllvaError):
    code = "PROVIDER_RATE_LIMITED"


class ProviderUnavailableError(SyllvaError):
    code = "PROVIDER_UNAVAILABLE"


class ProviderWriteNotAppliedError(ProviderUnavailableError):
    """Trusted adapter guarantee that this write did not apply any mutation.

    Emit only when the adapter can prove non-application (for example, failure
    before dispatch). Timeouts, retryability, error text, and a later snapshot
    are not such proof. This internal distinction retains the existing wire
    error code; callers must not reconstruct it from provider error payloads.
    """


class ProposalConflictError(SyllvaError):
    """A same-generation retry produced different producer output bytes.

    Raised by the range-intent outbox when a caller attempts to persist a
    different action/envelope for a (usage_slot_key, intent_generation) pair
    that already has a durably stored entry. This must never be silently
    caught by broad producer error handling -- it is a fail-closed identity
    conflict, not a skippable candidate warning.
    """

    code = "PROPOSAL_CONFLICT"


# Short aliases mirror the taxonomy names while the *Error forms remain the
# explicit exception names used by most Python callers.
ParsingError = ParseError
InvalidCourseKey = CourseKeyParseError
InvalidEntityId = EntityIdParseError
InvalidCourseKeyError = CourseKeyParseError
InvalidEntityIdError = EntityIdParseError
IdParseError = ParseError
EntityNotFound = EntityNotFoundError
EntityAmbiguous = EntityAmbiguousError
ResolutionExpired = ResolutionExpiredError
InvalidCandidate = InvalidCandidateError
ContextExpired = ContextExpiredError
LocatorNotAllowed = LocatorNotAllowedError
LocatorStale = LocatorStaleError
PolicyDenied = PolicyDeniedError
SourceUnavailable = SourceUnavailableError
SourcePartial = SourcePartialError
StaleEnrichment = StaleEnrichmentError
InvalidSubmissionRef = InvalidSubmissionRefError
ProviderRateLimited = ProviderRateLimitedError
ProviderUnavailable = ProviderUnavailableError


__all__ = [
    "ContextExpired",
    "ContextExpiredError",
    "CourseKeyParseError",
    "EntityAmbiguous",
    "EntityAmbiguousError",
    "EntityIdParseError",
    "EntityNotFound",
    "EntityNotFoundError",
    "IdParseError",
    "InvalidCandidate",
    "InvalidCandidateError",
    "InvalidCourseKey",
    "InvalidCourseKeyError",
    "InvalidEntityId",
    "InvalidEntityIdError",
    "InvalidSubmissionRef",
    "InvalidSubmissionRefError",
    "LocatorNotAllowed",
    "LocatorNotAllowedError",
    "LocatorParseError",
    "LocatorStale",
    "LocatorStaleError",
    "ParseError",
    "ParsingError",
    "PolicyDenied",
    "PolicyDeniedError",
    "PolicyViolation",
    "ProposalConflictError",
    "ProviderRateLimited",
    "ProviderRateLimitedError",
    "ProviderUnavailable",
    "ProviderUnavailableError",
    "ProviderWriteNotAppliedError",
    "ResolutionExpired",
    "ResolutionExpiredError",
    "SourcePartial",
    "SourcePartialError",
    "SourceUnavailable",
    "SourceUnavailableError",
    "StaleEnrichment",
    "StaleEnrichmentError",
    "SyllvaError",
]
