"""Session scope boundaries and relationship verification helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ._compat import field, is_strict_true, record_id, relation_id, relation_records, text


@dataclass(frozen=True)
class MaterialUsageScope:
    usage: Any
    material_id: str
    verified: bool
    start_page: int | None = None
    end_page: int | None = None
    role: str | None = None

    @property
    def provisional(self) -> bool:
        return not self.verified

    @property
    def usage_id(self) -> str | None:
        return record_id(self.usage)


def material_usage_scopes(usages: Iterable[Any]) -> list[MaterialUsageScope]:
    result: list[MaterialUsageScope] = []
    for usage in usages:
        material_value = field(
            usage,
            "Material ID",
            "material_id",
            "Material",
            "material",
            default=None,
        )
        material_id = relation_id(material_value)
        if not material_id:
            # Some fakes expose the relation under a nested snapshot.
            material_id = text(field(usage, "Target Material", default=None), default=None)
        if not material_id:
            continue
        verified = is_strict_true(field(usage, "Verified", "verified", default=False))
        start = _positive_page(field(usage, "Start Page", "start_page", default=None))
        end = _positive_page(field(usage, "End Page", "end_page", default=None))
        if start is not None and end is not None and start > end:
            # A malformed relation is not silently widened to the whole
            # material; leave it unavailable to the bounded retrieval path.
            continue
        role = text(field(usage, "Role", "role", default=None), default=None)
        result.append(MaterialUsageScope(usage, material_id, verified, start, end, role))
    return result


def allowed_material_usages(
    usages: Iterable[Any],
    *,
    include_provisional: bool = False,
) -> list[MaterialUsageScope]:
    return [
        scope
        for scope in material_usage_scopes(usages)
        if scope.verified or include_provisional
    ]


def relation_is_currently_allowed(
    usages: Iterable[Any],
    material_id: str,
    *,
    include_provisional: bool,
    usage_id: str | None = None,
    locator: Any | None = None,
) -> bool:
    for scope in material_usage_scopes(usages):
        if scope.material_id != material_id:
            continue
        if usage_id is not None and scope.usage_id != usage_id:
            continue
        if not (scope.verified or include_provisional):
            continue
        if locator is not None and getattr(locator, "kind", None) == "page":
            start = getattr(locator, "start_page", None)
            end = getattr(locator, "end_page", None)
            if scope.start_page is not None and (start is None or start < scope.start_page):
                continue
            if scope.end_page is not None and (end is None or end > scope.end_page):
                continue
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
    # Never copy Content/Text/Body/Note fields into the returned reference.
    return result


def _positive_page(value: Any) -> int | None:
    value = value
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 1:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) >= 1:
        return int(value.strip())
    return None


__all__ = [
    "MaterialUsageScope",
    "allowed_material_usages",
    "material_usage_scopes",
    "relation_is_currently_allowed",
    "user_reference",
]
