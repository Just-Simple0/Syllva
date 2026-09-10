"""Strict Material Usage scope and relationship validation helpers."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from uls.domain.course_identity import resolve_course_relation
from uls.domain.errors import PolicyDeniedError
from uls.domain.page_range import PageRange, PageRangeResult, parse_page_range

from ._compat import field, is_strict_true, properties, raw_field, strict_text, text, unwrap

VALID_USAGE_ROLES = frozenset({"Primary", "Supporting", "Reference"})
_MISSING = object()
MaterialUsageIdentity = tuple[str, str, str, PageRange]


@dataclass(frozen=True)
class MaterialUsageScope:
    """One valid Usage relation, including its immutable full range."""

    usage: Any
    material_id: str
    verified: bool
    start_page: int | None = None
    end_page: int | None = None
    role: str | None = None
    session_id: str | None = None
    page_range: PageRange | None = None

    def __post_init__(self) -> None:
        if self.page_range is None:
            object.__setattr__(self, "page_range", PageRange(self.start_page, self.end_page))
        else:
            object.__setattr__(self, "start_page", self.page_range.start_page)
            object.__setattr__(self, "end_page", self.page_range.end_page)

    @property
    def provisional(self) -> bool:
        return not self.verified

    @property
    def usage_id(self) -> str | None:
        return usage_app_id(self.usage)

    @property
    def range(self) -> PageRange:
        assert self.page_range is not None
        return self.page_range


@dataclass(frozen=True)
class MaterialUsageScopeResult:
    """Strict parsing result with a safe diagnostic for invalid graph rows."""

    scope: MaterialUsageScope | None
    reason: str | None = None


def material_usage_scopes(
    usages: Iterable[Any],
    *,
    session_id: str | None = None,
) -> list[MaterialUsageScope]:
    """Return only schema-valid Usage rows, preserving physical duplicates."""

    result: list[MaterialUsageScope] = []
    for usage in usages:
        scope = material_usage_scope_result(usage).scope
        if scope is None:
            continue
        if session_id is not None and scope.session_id != session_id:
            continue
        result.append(scope)
    return result


def material_usage_identity(usage: Any) -> MaterialUsageIdentity | None:
    """Extract a Usage's exact semantic sibling key.

    This is intentionally independent of :func:`material_usage_scope_result`:
    ``Verified`` is an eligibility field, not part of the identity of a
    relation.  A malformed checkbox must therefore not make an otherwise
    exact sibling disappear from duplicate detection.  The identity still
    requires the same strict relation cardinality, allowed Role, and complete
    full page range used by valid Usage scopes.
    """

    material_id = _single_relation_id(
        raw_field(usage, "Material", "material", "Material ID", "material_id")
    )
    session_id = _single_relation_id(
        raw_field(usage, "Session", "session", "Session ID", "session_id")
    )
    role = strict_text(raw_field(usage, "Role", "role", default=None), default=None)
    if not material_id or not session_id or role not in VALID_USAGE_ROLES:
        return None
    _, _, range_result = _range_from_record(usage)
    if not range_result.is_valid or range_result.value is None:
        return None
    return session_id, material_id, role, range_result.value


def material_usage_identity_counts(
    usages: Iterable[Any],
    *,
    session_id: str | None = None,
) -> Counter[MaterialUsageIdentity]:
    """Count exact Usage semantic keys, preserving physical duplicates.

    Rows with malformed ``Verified`` values are counted when their relation,
    Role, and complete range are otherwise valid.  Rows with malformed
    identity fields are ignored because they do not establish an exact
    semantic tuple.
    """

    counts: Counter[MaterialUsageIdentity] = Counter()
    for usage in usages:
        identity = material_usage_identity(usage)
        if identity is None or (session_id is not None and identity[0] != session_id):
            continue
        counts[identity] += 1
    return counts


def allowed_material_usages(
    usages: Iterable[Any],
    *,
    include_provisional: bool = False,
    session_id: str | None = None,
) -> list[MaterialUsageScope]:
    if type(include_provisional) is not bool:
        raise PolicyDeniedError("include_provisional must be a boolean")
    return [
        scope
        for scope in material_usage_scopes(usages, session_id=session_id)
        if scope.verified or include_provisional
    ]


def relation_is_currently_allowed(
    usages: Iterable[Any],
    material_id: str,
    *,
    include_provisional: bool,
    usage_id: str | None = None,
    locator: Any | None = None,
    issued_range: PageRange | None = None,
    expected_role: str | None = None,
) -> bool:
    """Check one current Usage basis without widening its range."""

    if type(include_provisional) is not bool:
        return False
    scopes = material_usage_scopes(usages)
    candidates = [
        scope
        for scope in scopes
        if scope.material_id == material_id
        and (usage_id is None or scope.usage_id == usage_id)
        and (expected_role is None or scope.role == expected_role)
        and (issued_range is None or scope.range == issued_range)
        and (scope.verified or include_provisional)
    ]
    if locator is None:
        return bool(candidates)
    for scope in candidates:
        if getattr(locator, "kind", None) != "page":
            return True
        start = getattr(locator, "start_page", None)
        end = getattr(locator, "end_page", None)
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if scope.range.is_whole_source:
            return True
        if (
            scope.range.start_page is not None
            and scope.range.end_page is not None
            and start >= scope.range.start_page
            and end <= scope.range.end_page
        ):
            return True
    return False


def user_reference(annotation: Any) -> dict[str, Any]:
    """Return metadata for a USER annotation without exposing its body."""

    result: dict[str, Any] = {"kind": "user_annotation", "source_class": "user_source"}
    for name, output_name in (
        ("ID", "entity_id"),
        ("Entity ID", "entity_id"),
        ("Locator", "locator"),
        ("Page", "page"),
        ("Topic", "topic"),
        ("Title", "title"),
        ("Name", "name"),
    ):
        value = text(field(annotation, name, default=None), default=None)
        if value is not None and output_name not in result:
            result[output_name] = value
    return result


def usage_app_id(usage: Any) -> str | None:
    """Return a Usage's frozen app ID, never its provider record ID.

    ``record_id`` intentionally has broad compatibility fallbacks for generic
    entities.  Material Usage is different: its logical ID is a required
    frozen ``ID`` property, while the provider's top-level ``id`` is only a
    physical record identity and cannot authorize or disambiguate a Usage.
    """

    source = properties(usage)
    if isinstance(source, Mapping):
        raw_id = source.get("ID", _MISSING)
    elif hasattr(source, "ID"):
        raw_id = source.ID
    else:
        raw_id = _MISSING
    if raw_id is _MISSING:
        return None
    value = unwrap(raw_id)
    if isinstance(value, str) and value.strip():
        return value
    return None


def material_usage_scope_result(usage: Any) -> MaterialUsageScopeResult:
    """Parse one stored Usage row and retain the exclusion reason."""

    usage_id = usage_app_id(usage)
    if not usage_id:
        return MaterialUsageScopeResult(None, "required Usage ID property is missing or blank")
    material_id = _single_relation_id(
        raw_field(usage, "Material", "material", "Material ID", "material_id")
    )
    session_value = raw_field(usage, "Session", "session", "Session ID", "session_id")
    session_value_id = _single_relation_id(session_value)
    if not material_id or not session_value_id:
        return MaterialUsageScopeResult(
            None,
            "Usage must have exactly one Session and exactly one Material relation",
        )
    verified_value = unwrap(raw_field(usage, "Verified", "verified", default=_MISSING))
    if verified_value is _MISSING or not isinstance(verified_value, bool):
        return MaterialUsageScopeResult(None, "Usage Verified must be a boolean")
    role_value = strict_text(raw_field(usage, "Role", "role", default=None), default=None)
    if role_value not in VALID_USAGE_ROLES:
        return MaterialUsageScopeResult(None, "Usage Role is missing or unknown")
    start, end, range_result = _range_from_record(usage)
    if not range_result.is_valid or range_result.value is None:
        return MaterialUsageScopeResult(
            None,
            range_result.reason or "Usage page range is invalid or incomplete",
        )
    return MaterialUsageScopeResult(
        MaterialUsageScope(
            usage=usage,
            material_id=material_id,
            verified=is_strict_true(verified_value),
            start_page=start,
            end_page=end,
            role=role_value,
            session_id=session_value_id,
            page_range=range_result.value,
        )
    )


def _scope_from_usage(usage: Any) -> MaterialUsageScope | None:
    """Compatibility wrapper for callers that only need valid scopes."""

    return material_usage_scope_result(usage).scope


def _range_from_record(usage: Any) -> tuple[int | None, int | None, PageRangeResult]:
    start = raw_field(usage, "Start Page", "start_page", default=_MISSING)
    end = raw_field(usage, "End Page", "end_page", default=_MISSING)
    # ``parse_page_range`` owns its omission sentinel.  Normalize an omitted
    # provider property to explicit null at this adapter boundary so omitted
    # and null/null both mean whole-source, while malformed values remain
    # invalid rather than being widened.
    if start is _MISSING:
        start = None
    if end is _MISSING:
        end = None
    # Notion's number boundary can expose an integer-valued JSON float.  This
    # is the one explicit storage normalization permitted by the contract;
    # the pure domain parser remains strict for model/output values.
    start = _normalize_stored_bound(start)
    end = _normalize_stored_bound(end)
    result = parse_page_range(start, end)
    if result.value is None:
        return None, None, result
    return result.value.start_page, result.value.end_page, result


def _normalize_stored_bound(value: Any) -> Any:
    value = unwrap(value)
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _single_relation_id(value: Any) -> str | None:
    return resolve_course_relation(value)


def _positive_page(value: Any) -> int | None:
    """Legacy helper retained as a strict single-page parser."""

    result = parse_page_range(value, value)
    if result.is_valid and result.value is not None:
        return result.value.start_page
    return None


__all__ = [
    "VALID_USAGE_ROLES",
    "MaterialUsageIdentity",
    "MaterialUsageScope",
    "MaterialUsageScopeResult",
    "allowed_material_usages",
    "material_usage_identity",
    "material_usage_identity_counts",
    "material_usage_scope_result",
    "material_usage_scopes",
    "relation_is_currently_allowed",
    "usage_app_id",
    "user_reference",
]
