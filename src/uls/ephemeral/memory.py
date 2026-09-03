"""Process-local, TTL-bounded ephemeral state (implementation spec §9)."""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Mapping

from uls.domain.errors import ContextExpiredError, InvalidCandidateError, ResolutionExpiredError
from uls.domain.models import PageLocator, TimeLocator, is_contained, parse_locator

from .models import (
    ContextCapability,
    ResolutionCandidate,
    ResolutionHandle,
    ResolvedEntity,
)


DEFAULT_RESOLUTION_TTL_SECONDS = 15 * 60
DEFAULT_CONTEXT_TTL_SECONDS = 15 * 60
# Capabilities are intentionally finite.  The exact upper bound is an
# implementation safety limit; callers can always issue a fresh capability.
MAX_TTL_SECONDS = 24 * 60 * 60


class MemoryEphemeralStore:
    """Thread-safe in-memory implementation of :class:`EphemeralStore`.

    No state is shared between instances.  Consequently constructing a new
    store models a server restart and invalidates every prior opaque handle.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resolutions: dict[str, ResolutionHandle] = {}
        self._contexts: dict[str, ContextCapability] = {}
        self._candidate_ids: set[str] = set()

    def create_resolution(
        self,
        candidates: list[ResolutionCandidate],
        ttl_seconds: int = DEFAULT_RESOLUTION_TTL_SECONDS,
    ) -> ResolutionHandle:
        ttl = _bounded_ttl(ttl_seconds)
        with self._lock:
            # A resolver may already have supplied an opaque ``cand_`` ID.
            # Preserve it for the first handle so the store composes cleanly
            # with that resolver, but never let one ID belong to two handles.
            scoped_candidates: list[ResolutionCandidate] = []
            for value in candidates:
                candidate = _coerce_candidate(value)
                candidate_id = candidate.candidate_id
                if not candidate_id.startswith("cand_") or candidate_id in self._candidate_ids:
                    candidate_id = _new_id("cand_")
                self._candidate_ids.add(candidate_id)
                scoped_candidates.append(
                    ResolutionCandidate(
                        candidate_id=candidate_id,
                        entity_type=candidate.entity_type,
                        entity_id=candidate.entity_id,
                        label=candidate.label,
                        reason=candidate.reason,
                    )
                )
            handle = ResolutionHandle(
                resolution_id=_new_id("res_"),
                expires_at=time.monotonic() + ttl,
                candidates=tuple(scoped_candidates),
            )
            self._resolutions[handle.resolution_id] = handle
        return handle

    def get_resolution(self, resolution_id: str) -> ResolutionHandle | None:
        with self._lock:
            handle = self._resolutions.get(resolution_id)
            if handle is None:
                return None
            if handle.is_expired(time.monotonic()):
                del self._resolutions[resolution_id]
                return None
            return handle

    def consume_resolution_choice(
        self,
        resolution_id: str,
        candidate_id: str,
    ) -> ResolvedEntity:
        with self._lock:
            handle = self._resolutions.get(resolution_id)
            if handle is None or handle.is_expired(time.monotonic()):
                if handle is not None:
                    del self._resolutions[resolution_id]
                raise ResolutionExpiredError(
                    "Resolution handle is missing or expired",
                    details={"resolution_id": resolution_id},
                )
            selected = next(
                (candidate for candidate in handle.candidates if candidate.candidate_id == candidate_id),
                None,
            )
            if selected is None:
                raise InvalidCandidateError(
                    "Candidate does not belong to the resolution",
                    details={
                        "resolution_id": resolution_id,
                        "candidate_id": candidate_id,
                    },
                )
            return ResolvedEntity(
                entity_type=selected.entity_type,
                entity_id=selected.entity_id,
                label=selected.label,
                candidate_id=selected.candidate_id,
                reason=selected.reason,
            )

    def create_context_capability(
        self,
        allowed_locators: list[str],
        caller_scope: str | None = None,
        ttl_seconds: int = DEFAULT_CONTEXT_TTL_SECONDS,
    ) -> ContextCapability:
        ttl = _bounded_ttl(ttl_seconds)
        # Parsing happens before the capability is stored, so an invalid
        # allowlist can never result in a partially-created capability.
        parsed_locators = tuple(parse_locator(locator) for locator in allowed_locators)
        capability = ContextCapability(
            context_id=_new_id("ctx_"),
            allowed_locators=parsed_locators,
            caller_scope=caller_scope,
            expires_at=time.monotonic() + ttl,
        )
        with self._lock:
            self._contexts[capability.context_id] = capability
        return capability

    def get_context_capability(self, context_id: str) -> ContextCapability | None:
        with self._lock:
            capability = self._contexts.get(context_id)
            if capability is None:
                return None
            if capability.is_expired(time.monotonic()):
                del self._contexts[context_id]
                return None
            return capability

    def authorize_locator(
        self,
        context_id: str,
        locator: str | PageLocator | TimeLocator,
        caller_scope: str | None = None,
    ) -> bool:
        with self._lock:
            capability = self._contexts.get(context_id)
            if capability is None or capability.is_expired(time.monotonic()):
                if capability is not None:
                    del self._contexts[context_id]
                raise ContextExpiredError(
                    "Context capability is missing or expired",
                    details={"context_id": context_id},
                )
            if capability.caller_scope is not None and capability.caller_scope != caller_scope:
                return False
            parsed_locator = parse_locator(locator) if isinstance(locator, str) else locator
            return any(is_contained(parsed_locator, allowed) for allowed in capability.allowed_locators)

    def purge_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired_resolutions = [
                key for key, handle in self._resolutions.items() if handle.is_expired(now)
            ]
            expired_contexts = [
                key for key, capability in self._contexts.items() if capability.is_expired(now)
            ]
            for key in expired_resolutions:
                del self._resolutions[key]
            for key in expired_contexts:
                del self._contexts[key]
            return len(expired_resolutions) + len(expired_contexts)


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(24)


def _bounded_ttl(ttl_seconds: int) -> float:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
        raise TypeError("ttl_seconds must be a number")
    if not math.isfinite(float(ttl_seconds)) or ttl_seconds < 0:
        raise ValueError("ttl_seconds must be finite and non-negative")
    return min(float(ttl_seconds), float(MAX_TTL_SECONDS))


def _coerce_candidate(value: ResolutionCandidate | Mapping[str, object]) -> ResolutionCandidate:
    if isinstance(value, ResolutionCandidate):
        return value
    if isinstance(value, Mapping):
        return ResolutionCandidate(
            candidate_id=str(value.get("candidate_id", "")),
            entity_type=str(value["entity_type"]),
            entity_id=str(value["entity_id"]),
            label=str(value["label"]),
            reason=(None if value.get("reason") is None else str(value["reason"])),
        )
    raise TypeError("candidates must contain ResolutionCandidate values or mappings")


__all__ = [
    "DEFAULT_CONTEXT_TTL_SECONDS",
    "DEFAULT_RESOLUTION_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "MemoryEphemeralStore",
]
