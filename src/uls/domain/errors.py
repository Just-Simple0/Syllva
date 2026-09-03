"""Structured errors shared by ULS domain and adapter boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class UlsError(Exception):
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


class ParseError(UlsError):
    """Base class for domain text parsing failures."""

    code = "PARSE_ERROR"


class CourseKeyParseError(ParseError):
    code = "INVALID_COURSE_KEY"


class EntityIdParseError(ParseError):
    code = "INVALID_ENTITY_ID"


class LocatorParseError(UlsError):
    code = "LOCATOR_PARSE_ERROR"


class EntityNotFoundError(UlsError):
    code = "ENTITY_NOT_FOUND"


class EntityAmbiguousError(UlsError):
    code = "ENTITY_AMBIGUOUS"


class ResolutionExpiredError(UlsError):
    code = "RESOLUTION_EXPIRED"


class InvalidCandidateError(UlsError):
    code = "INVALID_CANDIDATE"


class ContextExpiredError(UlsError):
    code = "CONTEXT_EXPIRED"


class LocatorNotAllowedError(UlsError):
    code = "LOCATOR_NOT_ALLOWED"


class LocatorStaleError(UlsError):
    code = "LOCATOR_STALE"


class PolicyDeniedError(UlsError):
    code = "POLICY_DENIED"


class PolicyViolation(UlsError):
    """Raised when an actor attempts a policy-forbidden domain mutation."""

    code = "POLICY_DENIED"


class SourceUnavailableError(UlsError):
    code = "SOURCE_UNAVAILABLE"


class SourcePartialError(UlsError):
    code = "SOURCE_PARTIAL"


class StaleEnrichmentError(UlsError):
    code = "STALE_ENRICHMENT"


class InvalidSubmissionRefError(UlsError):
    code = "INVALID_SUBMISSION_REF"


class ProviderRateLimitedError(UlsError):
    code = "PROVIDER_RATE_LIMITED"


class ProviderUnavailableError(UlsError):
    code = "PROVIDER_UNAVAILABLE"


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
    "EntityNotFound",
    "EntityNotFoundError",
    "EntityIdParseError",
    "InvalidCandidate",
    "InvalidCandidateError",
    "InvalidCourseKey",
    "InvalidCourseKeyError",
    "InvalidEntityId",
    "InvalidEntityIdError",
    "IdParseError",
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
    "ProviderRateLimited",
    "ProviderRateLimitedError",
    "ProviderUnavailable",
    "ProviderUnavailableError",
    "ResolutionExpired",
    "ResolutionExpiredError",
    "SourcePartial",
    "SourcePartialError",
    "SourceUnavailable",
    "SourceUnavailableError",
    "StaleEnrichment",
    "StaleEnrichmentError",
    "UlsError",
]
