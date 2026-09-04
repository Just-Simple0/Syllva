"""Provenance assembly for factual evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from uls.domain.enums import DerivativeStatus
from uls.domain.provenance import Provenance
from uls.domain.source_ref import SourceRef

from ._compat import coerce_source_ref


def make_provenance(
    front_matter: Mapping[str, Any] | Any,
    *,
    entity_id: str | None = None,
    course_key: str | None = None,
    source_ref: SourceRef | str | None = None,
    source_hash: str | None = None,
    source_version: int | None = None,
    processor_version: str | None = None,
    normalized_at: str | None = None,
    status: str | DerivativeStatus | None = None,
) -> Provenance:
    """Build a domain ``Provenance`` from a derivative front matter mapping."""

    def value(name: str, default: Any = None) -> Any:
        if isinstance(front_matter, Mapping):
            return front_matter.get(name, default)
        return getattr(front_matter, name, default)

    actual_entity = entity_id if entity_id is not None else value("entity_id")
    actual_course = course_key if course_key is not None else value("course_key")
    actual_ref = source_ref if source_ref is not None else value("source_ref")
    actual_hash = source_hash if source_hash is not None else value("source_hash")
    actual_version = source_version if source_version is not None else value("source_version")
    actual_processor = (
        processor_version if processor_version is not None else value("processor_version")
    )
    actual_normalized = normalized_at if normalized_at is not None else value("normalized_at")
    actual_status = status if status is not None else value("status")
    actual_schema = value("schema")
    ref = actual_ref if isinstance(actual_ref, SourceRef) else coerce_source_ref(actual_ref)
    if ref is None:
        raise ValueError("provenance requires source_ref")
    if not isinstance(actual_entity, str) or not actual_entity:
        raise ValueError("provenance requires entity_id")
    if not isinstance(actual_schema, str) or not actual_schema:
        raise ValueError("provenance requires schema")
    if not isinstance(actual_course, str) or not actual_course:
        raise ValueError("provenance requires course_key")
    if not isinstance(actual_hash, str) or not actual_hash:
        raise ValueError("provenance requires source_hash")
    if isinstance(actual_version, bool) or not isinstance(actual_version, int) or actual_version < 1:
        raise ValueError("provenance requires a positive source_version")
    if not isinstance(actual_processor, str) or not actual_processor:
        raise ValueError("provenance requires processor_version")
    if not isinstance(actual_normalized, str) or not actual_normalized:
        raise ValueError("provenance requires normalized_at")
    if actual_status is None or (
        isinstance(actual_status, str) and not actual_status.strip()
    ):
        raise ValueError("provenance requires status")
    return Provenance(
        schema=actual_schema,
        entity_id=actual_entity,
        course_key=actual_course,
        source_ref=ref,
        source_hash=actual_hash,
        source_version=actual_version,
        processor_version=actual_processor,
        normalized_at=actual_normalized,
        status=actual_status,
    )


provenance_from_front_matter = make_provenance


__all__ = ["make_provenance", "provenance_from_front_matter"]
