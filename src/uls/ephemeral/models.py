"""Ephemeral resolution and context-capability value objects.

``expires_at`` is a monotonic deadline rather than wall-clock time.  That is
intentional: these objects are process-local and TTL checks must not be
affected by a system clock adjustment.  API layers can format the deadline
separately when they need a presentation timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uls.domain.models import PageLocator, TimeLocator, parse_locator, serialize_locator


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
class ContextCapability:
    """A bounded, caller-scoped set of locator ranges."""

    context_id: str
    allowed_locators: tuple[PageLocator | TimeLocator, ...] = field(default_factory=tuple)
    caller_scope: str | None = None
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        normalized: list[PageLocator | TimeLocator] = []
        seen: set[str] = set()
        for locator in self.allowed_locators:
            parsed = parse_locator(locator) if isinstance(locator, str) else locator
            canonical = serialize_locator(parsed)
            if canonical not in seen:
                normalized.append(parsed)
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


__all__ = [
    "ContextCapability",
    "ResolutionCandidate",
    "ResolutionHandle",
    "ResolvedEntity",
]
