"""Strict page-range value objects used by Material Usage.

The distinction between an omitted/null bound and a malformed bound is
security relevant.  A malformed value must never be normalised to ``None``
because ``None/None`` means whole-document scope.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

_MISSING = object()


class PageRangeStatus(str, Enum):
    """Validation result for a pair of Material Usage bounds."""

    WHOLE_SOURCE = "whole_source"
    BOUNDED = "bounded"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True)
class PageRange:
    """An inclusive, validated page range.

    ``None/None`` is the explicit whole-source range.  A one-sided range is
    not representable as a valid :class:`PageRange`; callers should use
    :func:`parse_page_range` to receive an ``INCOMPLETE`` result instead.
    """

    start_page: int | None = None
    end_page: int | None = None

    def __post_init__(self) -> None:
        _validate_bound(self.start_page, "start_page", allow_none=True)
        _validate_bound(self.end_page, "end_page", allow_none=True)
        if (self.start_page is None) != (self.end_page is None):
            raise ValueError("page range must have both bounds or neither bound")
        if (
            self.start_page is not None
            and self.end_page is not None
            and self.start_page > self.end_page
        ):
            raise ValueError("page range start must not exceed end")

    @property
    def start(self) -> int | None:
        return self.start_page

    @property
    def end(self) -> int | None:
        return self.end_page

    @property
    def is_whole_source(self) -> bool:
        return self.start_page is None and self.end_page is None

    @property
    def is_bounded(self) -> bool:
        return not self.is_whole_source

    def contains_page(self, page: int) -> bool:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return False
        if self.is_whole_source:
            return True
        assert self.start_page is not None and self.end_page is not None
        return self.start_page <= page <= self.end_page

    def as_dict(self) -> dict[str, int | None]:
        return {"start_page": self.start_page, "end_page": self.end_page}

    to_dict = as_dict


@dataclass(frozen=True)
class PageRangeResult:
    """Non-throwing result returned by strict boundary parsers."""

    status: PageRangeStatus
    value: PageRange | None = None
    reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.value is not None and self.status in {
            PageRangeStatus.WHOLE_SOURCE,
            PageRangeStatus.BOUNDED,
        }

    @property
    def page_range(self) -> PageRange | None:
        return self.value

    @property
    def range(self) -> PageRange | None:
        return self.value


def parse_page_range(
    start_page: Any = _MISSING,
    end_page: Any = _MISSING,
) -> PageRangeResult:
    """Validate two raw bounds without widening malformed input.

    Missing and explicit ``None`` are equivalent only when both bounds are
    missing/null.  Strings, booleans, fractional/non-finite floats, invalid
    integers, reversed ranges and one-sided bounds are rejected.
    """

    start_missing = start_page is _MISSING
    end_missing = end_page is _MISSING
    start_value = None if start_missing else start_page
    end_value = None if end_missing else end_page

    if (start_missing or start_value is None) and (end_missing or end_value is None):
        return PageRangeResult(PageRangeStatus.WHOLE_SOURCE, PageRange())
    if (start_missing or start_value is None) != (end_missing or end_value is None):
        return PageRangeResult(
            PageRangeStatus.INCOMPLETE,
            reason="one-sided page bounds are unsupported",
        )

    try:
        start = _coerce_bound(start_value, "start_page")
        end = _coerce_bound(end_value, "end_page")
        value = PageRange(start, end)
    except (TypeError, ValueError) as exc:
        return PageRangeResult(PageRangeStatus.INVALID, reason=str(exc))
    return PageRangeResult(PageRangeStatus.BOUNDED, value)


def page_range_from_bounds(start_page: Any = _MISSING, end_page: Any = _MISSING) -> PageRangeResult:
    """Descriptive alias for :func:`parse_page_range`."""

    return parse_page_range(start_page, end_page)


def require_page_range(start_page: Any = _MISSING, end_page: Any = _MISSING) -> PageRange:
    """Return a valid range or raise ``ValueError`` with the boundary reason."""

    result = parse_page_range(start_page, end_page)
    if not result.is_valid or result.value is None:
        raise ValueError(result.reason or "invalid page range")
    return result.value


def _coerce_bound(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must not be boolean")
    if isinstance(value, int):
        normalized = value
    else:
        raise ValueError(f"{field_name} must be an integer")
    _validate_bound(normalized, field_name, allow_none=False)
    return normalized


def _validate_bound(value: Any, field_name: str, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a positive integer or None")
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    # Keep the value within the range accepted by common provider/JSON page
    # indexes.  This also gives deterministic overflow behaviour at the
    # boundary instead of relying on a provider integer implementation.
    if value > sys.maxsize:
        raise ValueError(f"{field_name} is too large")


__all__ = [
    "PageRange",
    "PageRangeResult",
    "PageRangeStatus",
    "page_range_from_bounds",
    "parse_page_range",
    "require_page_range",
]
