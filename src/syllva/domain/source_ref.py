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


# v2 providers (implementation spec §4.1). v1.2 code still constructs
# ``google_drive`` references until it is removed (R1b-6); v2 code must use
# ``v2_source_ref`` so a legacy provider can never enter v2 state.
V2_PROVIDERS = frozenset({"local", "canvas", "github"})


def v2_source_ref(provider: str, file_id: str) -> SourceRef:
    if provider not in V2_PROVIDERS:
        raise ValueError(f"unsupported v2 provider: {provider!r}")
    if not isinstance(file_id, str) or not file_id.strip() or any(c in file_id for c in "/\\\x00"):
        raise ValueError("file_id must be a non-empty identifier without path separators")
    return SourceRef(provider, file_id.strip())


__all__ = ["V2_PROVIDERS", "GitHubRef", "SourceFingerprint", "SourceRef", "v2_source_ref"]
