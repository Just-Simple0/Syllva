"""Bounded GitHub REST reads, pinned commits, no redirects or write endpoints."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from uls.domain.errors import (
    InvalidSubmissionRefError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
)

from .base import CodeFile, ResolvedRef, TreeEntry

_SHA = re.compile(r"[0-9a-fA-F]{40}")
_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+")


def repository_name(value: str) -> str:
    if not isinstance(value, str):
        raise SourceUnavailableError("invalid GitHub repository")
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if parsed.netloc != "github.com" or parsed.query or parsed.fragment:
            raise SourceUnavailableError("repository must be a canonical github.com URL")
        value = parsed.path.removeprefix("/").removesuffix("/")
    value = value.removesuffix(".git")
    if not _REPOSITORY.fullmatch(value) or value.split("/")[1] in {".", ".."}:
        raise SourceUnavailableError("invalid GitHub repository")
    return value


def repository_path(value: str, *, allow_empty: bool = False) -> str:
    if value == "" and allow_empty:
        return value
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 for c in value) or "\\" in value
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise SourceUnavailableError("invalid repository path")
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class GitHubAPIReader:
    """Fine-grained token needs Contents:read and Metadata:read only.

    A supplied JSON transport is for deterministic adapter tests. The production
    transport only sends GET requests to a fixed HTTPS origin, never follows
    redirects, bounds response bytes and does not surface response bodies/errors.
    """

    def __init__(self, token: str = "", *, timeout: float = 20,
                 max_file_bytes: int = 256_000, max_tree_entries: int = 10_000,
                 get_json: Callable[[str], Mapping[str, Any]] | None = None) -> None:
        self._token = token
        self.timeout = timeout
        self.max_file_bytes = max_file_bytes
        self.max_tree_entries = max_tree_entries
        self._transport = get_json or self._get_json

    def _get_json(self, route: str) -> Mapping[str, Any]:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "uls/1.2"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = Request("https://api.github.com" + route, headers=headers, method="GET")
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise SourcePartialError("GitHub response exceeds retrieval budget")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise SourceUnavailableError("invalid GitHub response")
            return data
        except HTTPError as exc:
            if exc.code == 429 or (exc.code == 403 and (
                exc.headers.get("X-RateLimit-Remaining") == "0"
                or exc.headers.get("Retry-After") is not None
            )):
                raise ProviderRateLimitedError("GitHub rate limited") from None
            if exc.code in {400, 404, 409, 422}:
                raise SourceUnavailableError("GitHub object is unavailable") from None
            raise ProviderUnavailableError("GitHub request failed") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderUnavailableError("GitHub response unavailable") from None

    def validate_repository(self, repository: str) -> str:
        name = repository_name(repository)
        data = self._transport("/repos/" + name)
        if str(data.get("full_name", "")).casefold() != name.casefold():
            raise SourceUnavailableError("GitHub repository identity mismatch")
        return name

    def validate_ref(self, repository: str, ref: str) -> ResolvedRef:
        name = self.validate_repository(repository)
        if (not isinstance(ref, str) or not ref or ref != ref.strip() or len(ref) > 256
                or any(ord(c) < 33 for c in ref)):
            raise InvalidSubmissionRefError("Submission Ref must name a commit or tag")
        selector = ref
        try:
            if not _COMMIT.fullmatch(ref):
                tag = ref.removeprefix("refs/tags/")
                if (ref.startswith("refs/") and not ref.startswith("refs/tags/")):
                    raise InvalidSubmissionRefError("branches are not submission evidence")
                repository_path(tag)
                data = self._transport(f"/repos/{name}/git/ref/tags/{quote(tag, safe='')}")
                if data.get("ref") != "refs/tags/" + tag:
                    raise InvalidSubmissionRefError("Submission Ref is not the requested tag")
                obj = data.get("object", {})
                for _ in range(8):
                    sha = obj.get("sha", "")
                    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                        raise InvalidSubmissionRefError("invalid tag target")
                    if obj.get("type") == "commit":
                        selector = sha
                        break
                    if obj.get("type") != "tag":
                        raise InvalidSubmissionRefError("tag does not identify a commit")
                    obj = self._transport(f"/repos/{name}/git/tags/{sha}").get("object", {})
                else:
                    raise InvalidSubmissionRefError("tag chain exceeds limit")
            commit = self._transport(f"/repos/{name}/commits/{quote(selector, safe='')}")
            sha = commit.get("sha", "")
            tree = commit.get("commit", {}).get("tree", {}).get("sha", "")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise InvalidSubmissionRefError("invalid commit identity")
            if not sha.casefold().startswith(selector.casefold()):
                raise InvalidSubmissionRefError("commit identity differs from Submission Ref")
            if not isinstance(tree, str) or not _SHA.fullmatch(tree):
                raise InvalidSubmissionRefError("invalid commit tree")
            return ResolvedRef(name, ref, sha.lower(), tree.lower())
        except SourceUnavailableError:
            raise InvalidSubmissionRefError("stored Submission Ref could not be resolved") from None

    def list_tree(self, ref: ResolvedRef) -> tuple[TreeEntry, ...]:
        name = repository_name(ref.repository)
        if not _SHA.fullmatch(ref.tree_sha) or not _SHA.fullmatch(ref.commit_sha):
            raise InvalidSubmissionRefError("tree read requires a resolved commit")
        data = self._transport(f"/repos/{name}/git/trees/{ref.tree_sha}?recursive=1")
        if data.get("sha") != ref.tree_sha:
            raise SourceUnavailableError("GitHub tree identity mismatch")
        entries = data.get("tree")
        if data.get("truncated") is not False or not isinstance(entries, list):
            raise SourcePartialError("GitHub tree is incomplete")
        if len(entries) > self.max_tree_entries:
            raise SourcePartialError("GitHub tree exceeds candidate budget")
        result: list[TreeEntry] = []
        seen: set[str] = set()
        for entry in entries:
            # Symlinks and submodules are not code files and are never followed.
            if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                continue
            path = repository_path(entry.get("path"))
            sha, size = entry.get("sha"), entry.get("size")
            if path in seen or not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise SourceUnavailableError("invalid or duplicate GitHub tree entry")
            if type(size) is not int or size < 0:
                raise SourceUnavailableError("invalid GitHub file size")
            seen.add(path)
            result.append(TreeEntry(path, sha, size))
        return tuple(sorted(result, key=lambda entry: entry.path))

    def read_file(self, ref: ResolvedRef, path: str) -> CodeFile:
        path = repository_path(path)
        entry = next((e for e in self.list_tree(ref) if e.path == path), None)
        if entry is None:
            raise SourceUnavailableError("file is absent from the submitted tree")
        if entry.size > self.max_file_bytes:
            raise SourcePartialError("code file exceeds retrieval byte budget")
        data = self._transport(f"/repos/{ref.repository}/git/blobs/{entry.sha}")
        if data.get("sha") != entry.sha or data.get("encoding") != "base64":
            raise SourceUnavailableError("GitHub blob identity or encoding mismatch")
        try:
            content = base64.b64decode("".join(data["content"].split()), validate=True)
            if len(content) != entry.size or len(content) > self.max_file_bytes:
                raise SourceUnavailableError("GitHub blob size mismatch")
            digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if digest != entry.sha:
                raise SourceUnavailableError("GitHub blob checksum mismatch")
            text = content.decode("utf-8")
            if "\0" in text:
                raise SourceUnavailableError("binary submission file")
        except (KeyError, ValueError, UnicodeError):
            raise SourceUnavailableError("submission file is not UTF-8 text") from None
        return CodeFile(ref, path, entry.sha, text)

    def read_at_ref(self, repository: str, path: str, ref: str) -> CodeFile:
        return self.read_file(self.validate_ref(repository, ref), path)
