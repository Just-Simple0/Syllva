"""Provenance and freshness value objects."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import DerivativeStatus, FreshnessStatus, ProcessingStatus
from .source_ref import SourceFingerprint, SourceRef


Freshness = FreshnessStatus


@dataclass(frozen=True)
class Provenance:
    """Trace metadata carried by a normalized derivative or factual item."""

    schema: str
    entity_id: str
    course_key: str
    source_ref: SourceRef
    source_hash: str
    source_version: int
    processor_version: str
    normalized_at: str
    status: DerivativeStatus | str

    def __post_init__(self) -> None:
        # Accept the common upper-case processing enum at the boundary while
        # storing the canonical lower-case derivative spelling.
        if isinstance(self.status, ProcessingStatus):
            object.__setattr__(self, "status", DerivativeStatus(self.status.value.lower()))
        elif isinstance(self.status, str) and not isinstance(self.status, DerivativeStatus):
            try:
                object.__setattr__(self, "status", DerivativeStatus(self.status.lower()))
            except ValueError:
                # Unknown future statuses remain representable as raw metadata;
                # validation of a particular schema belongs to its validator.
                pass


@dataclass(frozen=True)
class FreshnessInfo:
    """Fingerprint on which a persistent enrichment was based."""

    based_on_source_version: int
    based_on_source_hash: str
    processor_version: str

    def compare(self, current: SourceFingerprint) -> FreshnessStatus:
        """Compare this enrichment's source fingerprint with ``current``."""

        if not isinstance(current, SourceFingerprint):
            raise TypeError(f"Expected SourceFingerprint, got {type(current).__name__}")
        if (
            self.based_on_source_version == current.source_version
            and self.based_on_source_hash == current.source_hash
        ):
            return FreshnessStatus.FRESH
        return FreshnessStatus.STALE

    def check_freshness(self, current: SourceFingerprint) -> FreshnessStatus:
        return self.compare(current)

    def is_fresh(self, current: SourceFingerprint) -> bool:
        return self.compare(current) is FreshnessStatus.FRESH

    def is_stale(self, current: SourceFingerprint) -> bool:
        return self.compare(current) is FreshnessStatus.STALE


def check_freshness(info: FreshnessInfo, current: SourceFingerprint) -> FreshnessStatus:
    """Return ``FRESH`` when both source version and hash match."""

    return info.compare(current)


assess_freshness = check_freshness
compare_freshness = check_freshness


def is_fresh(info: FreshnessInfo, current: SourceFingerprint) -> bool:
    return info.is_fresh(current)


def is_stale(info: FreshnessInfo, current: SourceFingerprint) -> bool:
    return info.is_stale(current)


__all__ = [
    "FreshnessInfo",
    "Freshness",
    "FreshnessStatus",
    "Provenance",
    "assess_freshness",
    "check_freshness",
    "compare_freshness",
    "is_fresh",
    "is_stale",
]
