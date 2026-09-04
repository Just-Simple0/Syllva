"""Provider-neutral Drive contracts.

The worker adapter may implement the broader write contract from the frozen
specification.  RetrievalEngine only imports :class:`DriveReader`, whose two
methods are intentionally read-only.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from uls.domain.source_ref import SourceFingerprint, SourceRef


@runtime_checkable
class DriveReader(Protocol):
    """Read-only canonical derivative/fingerprint surface for retrieval."""

    def read_derived(self, source_ref: SourceRef | str | Any) -> str:
        ...

    def get_current_fingerprint(
        self,
        entity_id_or_source_ref: str | SourceRef | Any,
    ) -> SourceFingerprint:
        ...


__all__ = ["DriveReader"]
