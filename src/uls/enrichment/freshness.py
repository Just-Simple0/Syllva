"""Enrichment freshness helpers shared by enrichment and retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from uls.domain.enums import FreshnessStatus
from uls.domain.source_ref import SourceFingerprint

from .schemas import EnrichmentRecord, coerce_enrichment


def assess_freshness(
    record: EnrichmentRecord | Mapping[str, Any],
    current: SourceFingerprint,
) -> FreshnessStatus:
    enrichment = coerce_enrichment(record)
    if not isinstance(current, SourceFingerprint):
        raise TypeError("current must be a SourceFingerprint")
    return FreshnessStatus.FRESH if enrichment.source_fingerprint == current else FreshnessStatus.STALE


check_freshness = assess_freshness


def is_fresh(record: EnrichmentRecord | Mapping[str, Any], current: SourceFingerprint) -> bool:
    return assess_freshness(record, current) is FreshnessStatus.FRESH


def is_stale(record: EnrichmentRecord | Mapping[str, Any], current: SourceFingerprint) -> bool:
    return assess_freshness(record, current) is FreshnessStatus.STALE


__all__ = ["EnrichmentRecord", "assess_freshness", "check_freshness", "is_fresh", "is_stale"]
