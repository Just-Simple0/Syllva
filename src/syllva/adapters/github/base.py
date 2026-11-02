"""Read-only GitHub contract; branches are not submission evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResolvedRef:
    repository: str
    submission_ref: str
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class TreeEntry:
    path: str
    sha: str
    size: int


@dataclass(frozen=True)
class CodeFile:
    ref: ResolvedRef
    path: str
    blob_sha: str
    content: str


class GitHubReader(Protocol):
    def validate_repository(self, repository: str) -> str: ...
    def validate_ref(self, repository: str, ref: str) -> ResolvedRef: ...
    def list_tree(self, ref: ResolvedRef) -> tuple[TreeEntry, ...]: ...
    def read_file(self, ref: ResolvedRef, path: str) -> CodeFile: ...
    def read_at_ref(self, repository: str, path: str, ref: str) -> CodeFile: ...
