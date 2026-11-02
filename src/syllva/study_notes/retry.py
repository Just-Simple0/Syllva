"""Two independent, non-fungible counters (plan sections 8-9).

Pipeline retry: the worker’s own Drive/Notion dispatch call failing
transiently.  Exactly initial + 3 retries at 1/5/15 minutes, four dispatch
attempts total, then terminal FAILED.

Draft-reject budget: a submitted draft failing *structural* validation.
Capped at 3 rejections per attempt; the third rejected draft is terminal.
Never shares state with the pipeline-retry counter.
"""

from __future__ import annotations

MAX_PIPELINE_RETRIES = 3
PIPELINE_RETRY_DELAYS_SECONDS = (60, 300, 900)
MAX_DRAFT_REJECTIONS = 3


def pipeline_retry_delay_seconds(retry_number: int) -> int:
    """``retry_number`` is 1-based: 1st retry -> 60s, 2nd -> 300s, 3rd -> 900s.

    Raises for ``retry_number`` outside 1..3; the caller is responsible for
    treating attempt 4 as terminal FAILED, never a 4th retry.
    """
    if retry_number < 1 or retry_number > MAX_PIPELINE_RETRIES:
        raise ValueError("retry_number must be 1, 2, or 3")
    return PIPELINE_RETRY_DELAYS_SECONDS[retry_number - 1]


def pipeline_dispatch_exhausted(attempt_count: int) -> bool:
    """``attempt_count`` counts dispatch calls already made (initial = 1).

    True once 4 dispatch attempts (initial + 3 retries) have been made --
    the next failure is terminal, not a 4th retry.
    """
    return attempt_count >= MAX_PIPELINE_RETRIES + 1


def draft_rejection_exhausted(rejected_count: int) -> bool:
    """True on the THIRD rejected draft for one attempt (terminal FAILED).

    Distinct counter from pipeline dispatch attempts; a structurally
    rejected draft never consumes pipeline-retry budget.
    """
    return rejected_count >= MAX_DRAFT_REJECTIONS


__all__ = [
    "MAX_DRAFT_REJECTIONS",
    "MAX_PIPELINE_RETRIES",
    "PIPELINE_RETRY_DELAYS_SECONDS",
    "draft_rejection_exhausted",
    "pipeline_dispatch_exhausted",
    "pipeline_retry_delay_seconds",
]
