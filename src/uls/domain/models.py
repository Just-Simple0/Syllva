"""Core typed domain models, including the normative Locator AST."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .enums import FreshnessStatus, SourceAuthority
from .errors import LocatorParseError, UlsError
from .ids import parse_entity_id
from .provenance import Provenance
from .source_ref import SourceFingerprint


_TIMESTAMP_PATTERN = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}\Z")
_LOCATOR_PATTERN = re.compile(
    r"(?P<entity_id>[^:]+):"
    r"(?:(?P<page_target>p[1-9][0-9]*(?:-p?[1-9][0-9]*)?)|"
    r"(?P<time_target>t[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:-[0-9]{2}:[0-9]{2}:[0-9]{2})?))"
    r"(?::(?P<subtype>source|user))?\Z"
)
_MAX_TIMESTAMP_SECONDS = 99 * 60 * 60 + 59 * 60 + 59


def _invalid_locator(message: str, value: object | None = None) -> LocatorParseError:
    if value is None:
        return LocatorParseError(message)
    return LocatorParseError(f"{message}: {value!r}")


def _validate_entity_id(entity_id: object) -> None:
    if not isinstance(entity_id, str):
        raise _invalid_locator("Locator entity_id must be a string", entity_id)
    try:
        parse_entity_id(entity_id)
    except UlsError as exc:
        raise _invalid_locator("Locator contains an invalid entity_id", entity_id) from exc


def _validate_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_locator(f"Locator {field_name} must be an integer", value)


def _validate_subtype(subtype: object) -> None:
    if subtype is not None and (
        not isinstance(subtype, str) or subtype not in {"source", "user"}
    ):
        raise _invalid_locator("Locator subtype must be 'source' or 'user'", subtype)


@dataclass(frozen=True)
class PageLocator:
    """Inclusive page range attached to an entity."""

    entity_id: str
    start_page: int
    end_page: int
    subtype: str | None = None

    def __post_init__(self) -> None:
        _validate_entity_id(self.entity_id)
        _validate_int(self.start_page, "start_page")
        _validate_int(self.end_page, "end_page")
        _validate_subtype(self.subtype)
        if self.start_page < 1 or self.end_page < 1:
            raise _invalid_locator("Page numbers must be at least 1")
        if self.start_page > self.end_page:
            raise _invalid_locator("Page locator start must not exceed end")

    @property
    def kind(self) -> str:
        return "page"

    def __str__(self) -> str:
        return serialize_locator(self)


@dataclass(frozen=True)
class TimeLocator:
    """Inclusive timestamp range attached to an entity."""

    entity_id: str
    start_seconds: int
    end_seconds: int
    subtype: str | None = None

    def __post_init__(self) -> None:
        _validate_entity_id(self.entity_id)
        _validate_int(self.start_seconds, "start_seconds")
        _validate_int(self.end_seconds, "end_seconds")
        _validate_subtype(self.subtype)
        if self.start_seconds < 0 or self.end_seconds < 0:
            raise _invalid_locator("Timestamp values must not be negative")
        if self.start_seconds > _MAX_TIMESTAMP_SECONDS:
            raise _invalid_locator("Timestamp hour must be representable by two digits")
        if self.end_seconds > _MAX_TIMESTAMP_SECONDS:
            raise _invalid_locator("Timestamp hour must be representable by two digits")
        if self.start_seconds > self.end_seconds:
            raise _invalid_locator("Time locator start must not exceed end")

    @property
    def kind(self) -> str:
        return "time"

    def __str__(self) -> str:
        return serialize_locator(self)


def _parse_timestamp(value: str) -> int:
    if _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise _invalid_locator("Invalid timestamp", value)
    hour_text, minute_text, second_text = value.split(":")
    hour = int(hour_text)
    minute = int(minute_text)
    second = int(second_text)
    if minute > 59 or second > 59:
        raise _invalid_locator("Timestamp minute and second must be between 00 and 59", value)
    return hour * 60 * 60 + minute * 60 + second


def _parse_time_target(target: str) -> tuple[int, int]:
    parts = target[1:].split("-")
    if len(parts) not in {1, 2}:
        raise _invalid_locator("Invalid time target", target)
    start = _parse_timestamp(parts[0])
    end = start if len(parts) == 1 else _parse_timestamp(parts[1])
    if start > end:
        raise _invalid_locator("Time locator start must not exceed end", target)
    return start, end


def _parse_page_target(target: str) -> tuple[int, int]:
    parts = target[1:].split("-")
    if len(parts) not in {1, 2}:
        raise _invalid_locator("Invalid page target", target)
    first = int(parts[0])
    second_text = parts[0] if len(parts) == 1 else parts[1]
    if second_text.startswith("p"):
        second_text = second_text[1:]
    second = int(second_text)
    if first < 1 or second < 1:
        raise _invalid_locator("Page numbers must be at least 1", target)
    if first > second:
        raise _invalid_locator("Page locator start must not exceed end", target)
    return first, second


def parse_locator(text: str) -> PageLocator | TimeLocator:
    """Parse and normalize a v1.2 page or time locator.

    The full-match grammar intentionally excludes paths, URLs, unknown
    subtypes, malformed timestamps, and entity IDs containing a colon.
    """

    if not isinstance(text, str):
        raise _invalid_locator("Locator must be a string", text)
    match = _LOCATOR_PATTERN.fullmatch(text)
    if match is None:
        raise _invalid_locator("Invalid locator", text)

    entity_id = match.group("entity_id")
    subtype = match.group("subtype")
    try:
        parse_entity_id(entity_id)
    except UlsError as exc:
        raise _invalid_locator("Locator contains an invalid entity_id", entity_id) from exc

    page_target = match.group("page_target")
    if page_target is not None:
        start_page, end_page = _parse_page_target(page_target)
        return PageLocator(entity_id, start_page, end_page, subtype)

    time_target = match.group("time_target")
    if time_target is not None:
        start_seconds, end_seconds = _parse_time_target(time_target)
        return TimeLocator(entity_id, start_seconds, end_seconds, subtype)

    # The regular expression is exhaustive, but retaining a structured error
    # here makes the invariant explicit if the grammar is edited later.
    raise _invalid_locator("Locator has no recognized target", text)


def _format_timestamp(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    if hours > 99:
        raise _invalid_locator("Timestamp hour must be representable by two digits", total_seconds)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def serialize_locator(loc: PageLocator | TimeLocator | str) -> str:
    """Serialize a locator into its deterministic canonical spelling."""

    if isinstance(loc, str):
        loc = parse_locator(loc)
    if isinstance(loc, PageLocator):
        target = f"p{loc.start_page}"
        if loc.start_page != loc.end_page:
            target = f"{target}-p{loc.end_page}"
    elif isinstance(loc, TimeLocator):
        target = f"t{_format_timestamp(loc.start_seconds)}"
        if loc.start_seconds != loc.end_seconds:
            target = f"{target}-{_format_timestamp(loc.end_seconds)}"
    else:
        raise _invalid_locator("Expected PageLocator or TimeLocator", loc)
    if loc.subtype is not None:
        target = f"{target}:{loc.subtype}"
    return f"{loc.entity_id}:{target}"


def _coerce_locator(value: PageLocator | TimeLocator | str) -> PageLocator | TimeLocator:
    if isinstance(value, (PageLocator, TimeLocator)):
        return value
    if isinstance(value, str):
        return parse_locator(value)
    raise _invalid_locator("Expected a parsed locator or locator string", value)


def is_contained(
    requested: PageLocator | TimeLocator | str,
    allowed: PageLocator | TimeLocator | str,
) -> bool:
    """Return whether ``requested`` is safely contained by ``allowed``.

    Comparison is typed and numeric.  In particular, no string prefix or
    substring check can authorize a locator.
    """

    requested_loc = _coerce_locator(requested)
    allowed_loc = _coerce_locator(allowed)

    if type(requested_loc) is not type(allowed_loc):
        return False
    if requested_loc.entity_id != allowed_loc.entity_id:
        return False
    if requested_loc.subtype != allowed_loc.subtype:
        return False

    if isinstance(requested_loc, PageLocator) and isinstance(allowed_loc, PageLocator):
        return (
            requested_loc.start_page >= allowed_loc.start_page
            and requested_loc.end_page <= allowed_loc.end_page
        )
    if isinstance(requested_loc, TimeLocator) and isinstance(allowed_loc, TimeLocator):
        return (
            requested_loc.start_seconds >= allowed_loc.start_seconds
            and requested_loc.end_seconds <= allowed_loc.end_seconds
        )
    return False


@dataclass(frozen=True)
class EvidenceItem:
    """Factual context item with authority, fingerprint, and provenance."""

    source_class: str
    entity_id: str
    locator: PageLocator | TimeLocator
    fingerprint: SourceFingerprint
    authority: SourceAuthority | str
    content: str
    provenance: Provenance
    freshness: FreshnessStatus | str

    def __post_init__(self) -> None:
        if isinstance(self.locator, str):
            object.__setattr__(self, "locator", parse_locator(self.locator))
        if isinstance(self.authority, str):
            try:
                object.__setattr__(self, "authority", SourceAuthority(self.authority.lower()))
            except ValueError:
                # The source-class vocabulary can be extended independently
                # of this initial authority enum; preserve unknown metadata.
                pass
        if isinstance(self.freshness, str):
            try:
                object.__setattr__(self, "freshness", FreshnessStatus(self.freshness.upper()))
            except ValueError:
                pass


@dataclass(frozen=True)
class ContextPackage:
    """Versioned structured context returned by retrieval domain tools."""

    entity: Mapping[str, Any] = field(default_factory=dict)
    scope: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    professor_signals: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    user_context: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    warnings: tuple[Any, ...] = field(default_factory=tuple)
    protocol_version: str = "1.2"
    context_id: str | None = None

    def __post_init__(self) -> None:
        # A frozen package should also prevent callers from accidentally
        # appending to the top-level collections after construction.
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "professor_signals", tuple(self.professor_signals))
        object.__setattr__(self, "user_context", tuple(self.user_context))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    @property
    def version(self) -> str:
        """Short alias for the protocol version field."""

        return self.protocol_version

    @property
    def schema_version(self) -> str:
        return self.protocol_version


__all__ = [
    "ContextPackage",
    "EvidenceItem",
    "PageLocator",
    "TimeLocator",
    "is_contained",
    "parse_locator",
    "serialize_locator",
]
