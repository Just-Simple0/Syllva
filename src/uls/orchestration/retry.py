"""Bounded retry classification and backoff policy (spec §36)."""

from __future__ import annotations

import math
import random
from enum import Enum


class ErrorClass(str, Enum):
    TRANSIENT = "TRANSIENT"
    RATE_LIMITED = "RATE_LIMITED"
    PERMANENT = "PERMANENT"
    AMBIGUOUS = "AMBIGUOUS"
    POLICY_DENIED = "POLICY_DENIED"

    def __str__(self) -> str:
        return self.value


DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS = DEFAULT_MAX_ATTEMPTS
MAX_RETRY_ATTEMPTS = DEFAULT_MAX_ATTEMPTS
DEFAULT_RETRY_LIMIT = DEFAULT_MAX_ATTEMPTS
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_CAP_SECONDS = 60.0
RETRYABLE_ERROR_CLASSES = frozenset({ErrorClass.TRANSIENT, ErrorClass.RATE_LIMITED})
NON_RETRYABLE_ERROR_CLASSES = frozenset(
    {ErrorClass.PERMANENT, ErrorClass.AMBIGUOUS, ErrorClass.POLICY_DENIED}
)


def next_backoff(
    attempt: int,
    base: float = DEFAULT_BACKOFF_BASE_SECONDS,
    cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
    retry_after: float | None = None,
) -> float:
    """Return a bounded exponential delay with additive jitter.

    ``attempt`` is one-based (the delay after attempt 1 uses ``base``).  A
    provider-supplied ``Retry-After`` wins over the exponential calculation,
    while still respecting the configured cap.  This function never sleeps;
    callers decide how to schedule the returned delay.
    """

    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise TypeError("attempt must be an integer")
    if attempt < 1:
        attempt = 1
    if not math.isfinite(base) or base < 0:
        raise ValueError("base must be a finite non-negative number")
    if not math.isfinite(cap) or cap < 0:
        raise ValueError("cap must be a finite non-negative number")
    if cap < base and retry_after is None:
        # A cap lower than the base is still meaningful; it simply caps the
        # first delay.  Do not reject a useful caller configuration.
        pass

    if retry_after is not None:
        if not math.isfinite(retry_after):
            raise ValueError("retry_after must be finite")
        return min(cap, max(0.0, float(retry_after)))

    exponential = min(cap, float(base) * (2 ** (attempt - 1)))
    # A small positive additive jitter preserves a useful lower bound for
    # tests and operators while preventing synchronized worker retries.
    jitter = random.uniform(0.0, exponential * 0.1)
    return min(cap, exponential + jitter)


def coerce_error_class(error_class: ErrorClass | str) -> ErrorClass:
    if isinstance(error_class, ErrorClass):
        return error_class
    if isinstance(error_class, str):
        try:
            return ErrorClass(error_class.upper())
        except ValueError as exc:
            raise ValueError(f"unknown error class: {error_class!r}") from exc
    raise TypeError("error_class must be an ErrorClass or string")


def is_retryable(error_class: ErrorClass | str) -> bool:
    """Return whether the error class permits an automatic retry."""

    return coerce_error_class(error_class) in RETRYABLE_ERROR_CLASSES


def should_retry(
    error_class: ErrorClass | str,
    attempt: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """Return whether another automatic attempt is allowed.

    Permanent, policy-denied, and ambiguous outcomes never retry.  Retryable
    outcomes stop once the bounded maximum number of attempts is reached.
    """

    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise TypeError("attempt must be an integer")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts must be an integer")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")
    return is_retryable(error_class) and attempt < max_attempts


def no_retry(error_class: ErrorClass | str) -> bool:
    """Return whether the policy explicitly forbids retrying this class."""

    return not is_retryable(error_class)


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_BACKOFF_CAP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_LIMIT",
    "ErrorClass",
    "MAX_ATTEMPTS",
    "MAX_RETRY_ATTEMPTS",
    "NON_RETRYABLE_ERROR_CLASSES",
    "RETRYABLE_ERROR_CLASSES",
    "coerce_error_class",
    "is_retryable",
    "next_backoff",
    "no_retry",
    "should_retry",
]
