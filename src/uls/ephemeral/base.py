"""EphemeralStore protocol (spec §9).

Holds short-lived, process-local, TTL-bounded state for safe multi-turn retrieval:
resolution sessions, context capabilities, locator allowlists, and expiry metadata.

This state is NOT academic authority and NOT durable canonical state. A server restart
invalidates all entries (spec §9.5), and the client must repeat the parent resolve/
context call. Default backend is in-memory (see ``memory.py``).

This module defines the interface only. Implementation is delegated to Codex.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EphemeralStore(Protocol):
    """Short-lived multi-turn retrieval state (spec §9.2).

    Concrete return types (``ResolutionHandle``, ``ResolvedEntity``,
    ``ContextCapability``, ``ResolutionCandidate``) are defined in ``ephemeral/models.py``
    by the implementation role; ``Any`` is used here until those types are finalized.
    """

    # --- Resolution sessions (spec §10, §15.1) ---
    def create_resolution(
        self, candidates: list[Any], ttl_seconds: int
    ) -> Any: ...
    def get_resolution(self, resolution_id: str) -> Any | None: ...
    def consume_resolution_choice(
        self, resolution_id: str, candidate_id: str
    ) -> Any: ...

    # --- Context capabilities (spec §20, §25) ---
    def create_context_capability(
        self,
        allowed_locators: list[str],
        caller_scope: str | None,
        ttl_seconds: int,
    ) -> Any: ...
    def get_context_capability(self, context_id: str) -> Any | None: ...
    def authorize_locator(
        self, context_id: str, locator: str, caller_scope: str | None
    ) -> bool: ...

    # --- Maintenance (spec §9.4) ---
    def purge_expired(self) -> int: ...


__all__ = ["EphemeralStore"]
