"""Persistent StateStore protocol (spec §7).

StateStore holds durable orchestration state (jobs, source files/versions, processing
records, checkpoints, entity allocations, worker lock). It must NOT store canonical
academic source bodies — those live in Drive/GitHub. The initial implementation is
SQLite (see ``sqlite.py``); the protocol keeps the core independent of the backend.

This module defines the interface only. Implementation is delegated to Codex.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StateStore(Protocol):
    """Durable orchestration state (spec §7).

    Method bodies are implemented by concrete backends. Signatures use ``Any`` where
    the concrete row/dataclass types (``state/models.py``) are still being finalized by
    the implementation role; they should be tightened as those types land.
    """

    # --- Job lifecycle (spec §8.1, §32) ---
    def create_job(self, *args: Any, **kwargs: Any) -> Any: ...
    def get_job(self, job_id: str) -> Any: ...
    def claim_job(self, *args: Any, **kwargs: Any) -> Any: ...
    def transition_job(self, *args: Any, **kwargs: Any) -> Any: ...
    def complete_job(self, *args: Any, **kwargs: Any) -> Any: ...
    def fail_job(self, *args: Any, **kwargs: Any) -> Any: ...

    # --- Source identity / versioning (spec §8.2, §8.3, §11) ---
    def find_processed_source(self, *args: Any, **kwargs: Any) -> Any: ...
    def register_source_file(self, *args: Any, **kwargs: Any) -> Any: ...
    def register_source_version(self, *args: Any, **kwargs: Any) -> Any: ...
    def find_source_versions(self, *args: Any, **kwargs: Any) -> Any: ...

    # --- Checkpoints (spec §8.5) ---
    def get_checkpoint(self, provider: str, scope: str) -> Any: ...
    def set_checkpoint(self, provider: str, scope: str, value: str) -> Any: ...

    # --- Local single-worker lock (spec §40) ---
    def acquire_local_worker_lock(self, *args: Any, **kwargs: Any) -> Any: ...
    def release_local_worker_lock(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["StateStore"]
