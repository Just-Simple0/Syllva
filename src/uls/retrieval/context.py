"""Bounded ContextPackage assembly."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from uls.domain.enums import FreshnessStatus, SourceAuthority
from uls.domain.models import ContextPackage, EvidenceItem
from uls.domain.source_ref import SourceFingerprint

from .authority import authority_for
from .schemas import RetrievalBudget


def budget_from_config(config: Any) -> RetrievalBudget:
    if isinstance(config, Mapping):
        retrieval = config.get("retrieval", config)
    else:
        retrieval = getattr(config, "retrieval", config)

    def integer(name: str, default: int) -> int:
        value = getattr(retrieval, name, default)
        if isinstance(retrieval, Mapping):
            value = retrieval.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return default
        return value

    return RetrievalBudget(
        max_evidence_items=integer("max_evidence_items", 12),
        max_chars_per_item=integer("max_chars_per_item", 4000),
        max_total_chars=integer("max_total_chars", 24000),
        max_followup_chunks=integer("max_followup_chunks", 8),
    )


def bounded_evidence(
    items: Iterable[EvidenceItem],
    budget: RetrievalBudget,
) -> tuple[EvidenceItem, ...]:
    """Apply item/count/total budgets while retaining all provenance fields."""

    selected: list[EvidenceItem] = []
    total = 0
    for item in items:
        if len(selected) >= budget.max_evidence_items or total >= budget.max_total_chars:
            break
        content = item.content[: budget.max_chars_per_item]
        remaining = budget.max_total_chars - total
        content = content[:remaining]
        if not content:
            continue
        if content != item.content:
            provisional = bool(getattr(item, "provisional", False))
            source_ref = getattr(item, "source_ref", None)
            # Domain EvidenceItem is frozen but its shape is canonical.  A
            # replacement retains locator/fingerprint/authority/provenance/
            # freshness, which is the important truncation invariant.
            item = replace(item, content=content)
            if provisional:
                object.__setattr__(item, "provisional", True)
            if source_ref is not None:
                object.__setattr__(item, "source_ref", source_ref)
        selected.append(item)
        total += len(content)
    return tuple(selected)


def make_evidence_item(
    *,
    source_class: str,
    entity_id: str,
    locator: Any,
    fingerprint: SourceFingerprint,
    content: str,
    provenance: Any,
    freshness: FreshnessStatus | str = FreshnessStatus.FRESH,
    provisional: bool = False,
) -> EvidenceItem:
    item = EvidenceItem(
        source_class=source_class,
        entity_id=entity_id,
        locator=locator,
        fingerprint=fingerprint,
        authority=authority_for(source_class),
        content=content,
        provenance=provenance,
        freshness=freshness,
    )
    if provisional:
        # The frozen domain shape intentionally stays compact.  Preserve the
        # provisional marker as an additive, non-authorizing metadata flag.
        object.__setattr__(item, "provisional", True)
    return item


def assemble_context_package(
    *,
    entity: Mapping[str, Any],
    sources: Iterable[EvidenceItem],
    scope: Mapping[str, Any] | None = None,
    professor_signals: Iterable[Mapping[str, Any]] = (),
    user_context: Iterable[Mapping[str, Any]] = (),
    warnings: Iterable[Any] = (),
    context_id: str | None = None,
    budget: RetrievalBudget | None = None,
) -> ContextPackage:
    effective_budget = budget or RetrievalBudget()
    bounded = bounded_evidence(sources, effective_budget)
    return ContextPackage(
        entity=dict(entity),
        scope=dict(scope or {}),
        sources=bounded,
        professor_signals=tuple(dict(item) for item in professor_signals),
        user_context=tuple(dict(item) for item in user_context),
        warnings=tuple(warnings),
        context_id=context_id,
    )


__all__ = [
    "assemble_context_package",
    "bounded_evidence",
    "budget_from_config",
    "make_evidence_item",
]
