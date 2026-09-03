"""Structured references to canonical external and GitHub sources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRef:
    """Provider source reference.

    ``provider`` and ``file_id`` form the canonical identity.  ``web_url`` is
    intentionally navigational metadata and is never part of that identity.
    """

    provider: str
    file_id: str
    web_url: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider, self.file_id)

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.identity

    @property
    def canonical_identity(self) -> tuple[str, str]:
        return self.identity

    @property
    def canonical_key(self) -> str:
        """Stable display/storage key that excludes navigational metadata."""

        return f"{self.provider}:{self.file_id}"


@dataclass(frozen=True)
class SourceFingerprint:
    """Immutable source version/hash pair used for freshness checks."""

    source_version: int
    source_hash: str


@dataclass(frozen=True)
class GitHubRef:
    """Exact GitHub source reference, including the immutable ref selector."""

    repository: str
    repository_path: str
    ref: str


__all__ = ["GitHubRef", "SourceFingerprint", "SourceRef"]
