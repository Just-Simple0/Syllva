"""Process-local, TTL-bounded ephemeral state (implementation spec §9)."""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Mapping, Sequence

from uls.domain.errors import (
    ContextExpiredError,
    InvalidCandidateError,
    LocatorParseError,
    LocatorStaleError,
    ResolutionExpiredError,
)
from uls.domain.models import PageLocator, TimeLocator, is_contained, parse_locator
from uls.domain.source_ref import SourceFingerprint

from .models import (
    AllowedLocator,
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
            reserved_ids: set[str] = set()
            try:
                for value in candidates:
                    candidate = _coerce_candidate(value)
                    candidate_id = candidate.candidate_id
                    if (
                        not candidate_id.startswith("cand_")
                        or candidate_id in self._candidate_ids
                        or candidate_id in reserved_ids
                    ):
                        candidate_id = _new_id("cand_")
                        while (
                            candidate_id in self._candidate_ids
                            or candidate_id in reserved_ids
                        ):
                            candidate_id = _new_id("cand_")
                    reserved_ids.add(candidate_id)
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
                self._candidate_ids.update(reserved_ids)
                self._resolutions[handle.resolution_id] = handle
            except BaseException:
                # Reservation is transactional: a malformed later candidate
                # (or any failure while building the handle) must not leak IDs
                # that were allocated by this call.
                self._candidate_ids.difference_update(reserved_ids)
                raise
        return handle

    def get_resolution(self, resolution_id: str) -> ResolutionHandle | None:
        with self._lock:
            handle = self._resolutions.get(resolution_id)
            if handle is None:
                return None
            if handle.is_expired(time.monotonic()):
                self._drop_resolution(resolution_id)
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
                    self._drop_resolution(resolution_id)
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
            resolved = ResolvedEntity(
                entity_type=selected.entity_type,
                entity_id=selected.entity_id,
                label=selected.label,
                candidate_id=selected.candidate_id,
                reason=selected.reason,
            )
            # A resolution handle is a one-shot capability.  Remove both the
            # handle and its candidate IDs atomically before returning it.
            self._drop_resolution(resolution_id)
            return resolved

    def create_context_capability(
        self,
        allowed_locators: list[str | AllowedLocator | Mapping[str, object] | Sequence[object]],
        caller_scope: str | None = None,
        ttl_seconds: int = DEFAULT_CONTEXT_TTL_SECONDS,
        source_hash: str | None = None,
        source_version: int | None = None,
        *,
        source_fingerprint: SourceFingerprint | Mapping[str, object] | Sequence[object] | None = None,
        fingerprints: Mapping[str, SourceFingerprint | Mapping[str, object] | Sequence[object]]
        | None = None,
    ) -> ContextCapability:
        ttl = _bounded_ttl(ttl_seconds)
        # Parsing happens before the capability is stored, so an invalid
        # allowlist can never result in a partially-created capability.
        if isinstance(source_hash, SourceFingerprint) and source_version is None:
            source_fingerprint = source_hash
            source_hash = None
        global_fingerprint = _coerce_fingerprint(
            source_fingerprint,
            source_hash=source_hash,
            source_version=source_version,
        )
        bound_locators = tuple(
            _coerce_allowed_locator(value, global_fingerprint, fingerprints)
            for value in allowed_locators
        )
        capability = ContextCapability(
            context_id=_new_id("ctx_"),
            allowed_locators=bound_locators,
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
        *,
        current_fingerprint: SourceFingerprint | None = None,
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
            try:
                parsed_locator = parse_locator(locator) if isinstance(locator, str) else locator
            except LocatorParseError:
                # Malformed caller input is an authorization miss, not a
                # provider/domain failure.  Expiry/staleness errors above and
                # below remain structured policy errors.
                return False
            try:
                matches = [
                    allowed
                    for allowed in capability.allowed_locators
                    if is_contained(parsed_locator, allowed.locator)
                ]
            except LocatorParseError:
                return False
            if not matches:
                return False
            if current_fingerprint is None:
                # Every allowed locator is source-version bound.  Without a
                # current fingerprint the required freshness check cannot be
                # performed, so authorization must fail closed.
                return False
            if not isinstance(current_fingerprint, SourceFingerprint):
                raise TypeError("current_fingerprint must be a SourceFingerprint or None")
            if all(
                allowed.source_hash == current_fingerprint.source_hash
                and allowed.source_version == current_fingerprint.source_version
                for allowed in matches
            ):
                return True
            raise LocatorStaleError(
                "Locator capability fingerprint is stale",
                details={"context_id": context_id, "locator": str(parsed_locator)},
            )

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
                self._drop_resolution(key)
            for key in expired_contexts:
                del self._contexts[key]
            return len(expired_resolutions) + len(expired_contexts)

    def _drop_resolution(self, resolution_id: str) -> None:
        """Delete a resolution and release all candidate IDs (lock held)."""

        handle = self._resolutions.pop(resolution_id, None)
        if handle is not None:
            self._candidate_ids.difference_update(
                candidate.candidate_id for candidate in handle.candidates
            )


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


def _coerce_fingerprint(
    value: SourceFingerprint | Mapping[str, object] | Sequence[object] | None,
    *,
    source_hash: str | None = None,
    source_version: int | None = None,
) -> SourceFingerprint | None:
    if value is not None:
        if source_hash is not None or source_version is not None:
            raise TypeError("pass either source_fingerprint or source_hash/source_version")
        if isinstance(value, SourceFingerprint):
            return value
        if isinstance(value, Mapping):
            source_hash = value.get("source_hash")
            source_version = value.get("source_version")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
            source_hash, source_version = value[0], value[1]  # type: ignore[assignment]
        else:
            raise TypeError("source_fingerprint must be a SourceFingerprint or a pair")
    if source_hash is None and source_version is None:
        return None
    if not isinstance(source_hash, str) or not source_hash.strip():
        raise ValueError("source_hash must be a non-empty string")
    if (
        isinstance(source_version, bool)
        or not isinstance(source_version, int)
        or source_version < 1
    ):
        raise ValueError("source_version must be a positive integer")
    return SourceFingerprint(source_version, source_hash.strip())


def _coerce_allowed_locator(
    value: str | AllowedLocator | Mapping[str, object] | Sequence[object],
    global_fingerprint: SourceFingerprint | None,
    fingerprints: Mapping[str, SourceFingerprint | Mapping[str, object] | Sequence[object]]
    | None,
) -> AllowedLocator:
    if isinstance(value, AllowedLocator):
        return value
    if isinstance(value, str):
        fingerprint = None
        if fingerprints is not None:
            parsed = parse_locator(value)
            fingerprint = fingerprints.get(value) or fingerprints.get(str(parsed))
        fingerprint = fingerprint or global_fingerprint
        if fingerprint is None:
            raise ValueError("every allowed locator must include a source fingerprint")
        actual = _coerce_fingerprint(fingerprint)
        assert actual is not None
        return AllowedLocator(value, actual.source_hash, actual.source_version)
    if isinstance(value, Mapping):
        locator = value.get("locator", value.get("locator_range"))
        item_fingerprint = value.get("source_fingerprint")
        item_hash = value.get("source_hash")
        item_version = value.get("source_version")
        if item_fingerprint is not None:
            actual = _coerce_fingerprint(item_fingerprint)  # type: ignore[arg-type]
        elif item_hash is not None or item_version is not None:
            actual = _coerce_fingerprint(None, source_hash=item_hash, source_version=item_version)  # type: ignore[arg-type]
        else:
            actual = global_fingerprint
        if actual is None:
            raise ValueError("every allowed locator must include a source fingerprint")
        return AllowedLocator(locator, actual.source_hash, actual.source_version)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 3:
        if isinstance(value[1], (SourceFingerprint, Mapping)) or (
            isinstance(value[1], Sequence) and not isinstance(value[1], (str, bytes))
        ):
            item_fingerprint = _coerce_fingerprint(value[1])  # type: ignore[arg-type]
        else:
            item_fingerprint = _coerce_fingerprint(
                None,
                source_hash=value[1],  # type: ignore[arg-type]
                source_version=value[2],  # type: ignore[arg-type]
            )
        assert item_fingerprint is not None
        return AllowedLocator(value[0], item_fingerprint.source_hash, item_fingerprint.source_version)
    raise TypeError("invalid allowed locator value")


__all__ = [
    "AllowedLocator",
    "DEFAULT_CONTEXT_TTL_SECONDS",
    "DEFAULT_RESOLUTION_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "MemoryEphemeralStore",
]
