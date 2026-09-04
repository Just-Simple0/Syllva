"""Ephemeral resolution and context-capability value objects.

``expires_at`` is a monotonic deadline rather than wall-clock time.  That is
intentional: these objects are process-local and TTL checks must not be
affected by a system clock adjustment.  API layers can format the deadline
separately when they need a presentation timestamp.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from uls.domain.models import PageLocator, TimeLocator, parse_locator, serialize_locator
from uls.domain.source_ref import SourceFingerprint


@dataclass(frozen=True)
class ResolutionCandidate:
    """One candidate returned by an ambiguous entity resolution."""

    candidate_id: str
    entity_type: str
    entity_id: str
    label: str
    reason: str | None = None


@dataclass(frozen=True)
class ResolutionHandle:
    """Short-lived handle and candidate set for a multi-turn resolution."""

    resolution_id: str
    expires_at: float
    candidates: tuple[ResolutionCandidate, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidates",
            tuple(_coerce_candidate(candidate) for candidate in self.candidates),
        )

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True)
class ResolvedEntity:
    """The trusted entity selected by a resolution handle."""

    entity_type: str
    entity_id: str
    label: str
    candidate_id: str | None = None
    reason: str | None = None

    @property
    def name(self) -> str:
        """Alias used by the successful-resolution JSON example."""

        return self.label


@dataclass(frozen=True)
class AllowedLocator:
    """One parsed locator range bound to the source version that authorized it."""

    locator: PageLocator | TimeLocator | str
    source_hash: str
    source_version: int

    def __post_init__(self) -> None:
        parsed = parse_locator(self.locator) if isinstance(self.locator, str) else self.locator
        if not isinstance(parsed, (PageLocator, TimeLocator)):
            raise TypeError("locator must be a PageLocator or TimeLocator")
        if not isinstance(self.source_hash, str) or not self.source_hash.strip():
            raise ValueError("source_hash must be a non-empty string")
        if (
            isinstance(self.source_version, bool)
            or not isinstance(self.source_version, int)
            or self.source_version < 1
        ):
            raise ValueError("source_version must be a positive integer")
        object.__setattr__(self, "locator", parsed)
        object.__setattr__(self, "source_hash", self.source_hash.strip())

    @property
    def source_fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.source_version, self.source_hash)


@dataclass(frozen=True)
class ContextCapability:
    """A bounded, caller-scoped set of locator ranges."""

    context_id: str
    allowed_locators: tuple[AllowedLocator, ...] = field(default_factory=tuple)
    caller_scope: str | None = None
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        normalized: list[AllowedLocator] = []
        seen: set[str] = set()
        for locator in self.allowed_locators:
            allowed = _coerce_allowed_locator(locator)
            canonical = (
                f"{serialize_locator(allowed.locator)}\x1f"
                f"{allowed.source_hash}\x1f{allowed.source_version}"
            )
            if canonical not in seen:
                normalized.append(allowed)
                seen.add(canonical)
        object.__setattr__(self, "allowed_locators", tuple(normalized))

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


def _coerce_candidate(candidate: ResolutionCandidate | dict[str, Any]) -> ResolutionCandidate:
    if isinstance(candidate, ResolutionCandidate):
        return candidate
    if isinstance(candidate, dict):
        return ResolutionCandidate(**candidate)
    raise TypeError("candidates must contain ResolutionCandidate values or mappings")


def _coerce_allowed_locator(value: Any) -> AllowedLocator:
    if isinstance(value, AllowedLocator):
        return value
    if isinstance(value, Mapping):
        locator = value.get("locator", value.get("locator_range"))
        source_hash = value.get("source_hash")
        source_version = value.get("source_version")
        return AllowedLocator(locator, source_hash, source_version)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 3:
        return AllowedLocator(value[0], value[1], value[2])
    raise TypeError(
        "allowed_locators must contain AllowedLocator values or "
        "(locator, source_hash, source_version) mappings"
    )


__all__ = [
    "AllowedLocator",
    "ContextCapability",
    "ResolutionCandidate",
    "ResolutionHandle",
    "ResolvedEntity",
]
