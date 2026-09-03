"""Small dependency-inversion contracts for the ULS domain boundary.

These protocols deliberately describe capabilities rather than provider or
client implementations.  Concrete adapters may live in higher layers.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeAlias, runtime_checkable

from .models import ContextPackage, PageLocator, TimeLocator
from .source_ref import SourceRef


Locator: TypeAlias = PageLocator | TimeLocator
SourceIdentity: TypeAlias = tuple[str, str]


@runtime_checkable
class SourceReader(Protocol):
    """Read a source body through an injected provider adapter."""

    def read(self, source_ref: SourceRef) -> str:
        ...


@runtime_checkable
class StateStore(Protocol):
    """Minimal durable orchestration store surface from implementation §7."""

    def create_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def get_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def claim_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def transition_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def complete_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def fail_job(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def find_processed_source(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def register_source_file(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def register_source_version(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def find_source_versions(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def get_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def set_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def acquire_local_worker_lock(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def release_local_worker_lock(self, *args: Any, **kwargs: Any) -> Any:
        ...


@runtime_checkable
class EphemeralStore(Protocol):
    """Minimal process-local capability/resolution store surface."""

    def create_context_capability(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def get_context_capability(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def create_resolution(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def get_resolution(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def consume_resolution_choice(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def authorize_locator(
        self,
        context_id: str,
        locator: Locator | str,
        caller_scope: str | None = None,
    ) -> bool:
        ...

    def purge_expired(self) -> int:
        ...


@runtime_checkable
class RetrievalEngine(Protocol):
    """Model-neutral retrieval service callable without an MCP server."""

    def get_context(self, *args: Any, **kwargs: Any) -> ContextPackage:
        ...


__all__ = [
    "EphemeralStore",
    "Locator",
    "RetrievalEngine",
    "SourceIdentity",
    "SourceReader",
    "StateStore",
]
