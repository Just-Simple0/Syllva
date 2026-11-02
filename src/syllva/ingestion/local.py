"""Local ingestion into v2 state (implementation spec §3.2, §4.4, §11.1, design §4).

A file the user drops (or Canvas delivers) becomes:

1. a content-addressed blob (``library.blobs``);
2. ``blobs`` + ``blob_refs`` rows;
3. a ``source_files`` row (logical ``source_id``, NFC display name) and its first
   ``source_versions`` row — or, for a revision, a new version of an existing
   source that ``supersedes`` the previous one.

Identical bytes already stored as any source version are reported as a
duplicate instead of creating a second source (spec §11.3); the caller decides
what to show. All rows are written through ``StateWriter`` as ``INGESTER``.
"""

from __future__ import annotations

import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from syllva.domain.source_ref import V2_PROVIDERS
from syllva.library.blobs import BlobStore, StoredBlob
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

ORIGINS = {"local": "USER_DROP", "canvas": "CANVAS", "github": "GITHUB"}


class IngestError(RuntimeError):
    code = "INGEST_FAILED"


@dataclass(frozen=True)
class IngestResult:
    source_id: str
    source_file_id: int
    source_version_id: int
    source_version: int
    source_hash: str
    blob_sha256: str
    size_bytes: int
    created: bool  # False: identical content already stored (duplicate or unchanged revision)
    duplicate_of: str | None = None  # source_id holding the same bytes, for new-file ingests


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def nfc(name: str) -> str:
    cleaned = unicodedata.normalize("NFC", name).strip()
    if not cleaned or any(ch in cleaned for ch in "\x00\r\n"):
        raise IngestError("display name must be a non-empty single line")
    return cleaned


def _record_blob(writer: StateWriter, stored: StoredBlob, now: str) -> None:
    if writer.query_one("SELECT 1 FROM blobs WHERE sha256 = ?", (stored.sha256,)) is None:
        writer.insert(Owner.SOURCE, Actor.INGESTER, "blobs", {"sha256": stored.sha256, "size_bytes": stored.size_bytes, "created_at": now})


def _ref(writer: StateWriter, sha256: str, owner_kind: str, owner_id: int) -> None:
    if writer.query_one(
        "SELECT 1 FROM blob_refs WHERE sha256 = ? AND owner_kind = ? AND owner_id = ?", (sha256, owner_kind, owner_id)
    ) is None:
        writer.insert(Owner.SYSTEM, Actor.INGESTER, "blob_refs", {"sha256": sha256, "owner_kind": owner_kind, "owner_id": owner_id})


def ingest_new(
    writer: StateWriter,
    blobs: BlobStore,
    stream: BinaryIO,
    *,
    display_name: str,
    mime_type: str | None = None,
    provider: str = "local",
    now: str | None = None,
) -> IngestResult:
    """Store a new file as a new source (or report the existing source with the same bytes)."""

    if provider not in V2_PROVIDERS:
        raise IngestError(f"unsupported provider {provider!r}")
    name = nfc(display_name)
    stamp = now or _now()
    stored = blobs.put_stream(stream)  # outside the DB transaction; unreferenced blobs are GC'd later
    source_hash = "sha256:" + stored.sha256
    with writer.transaction():
        existing = writer.query_one(
            "SELECT f.source_id, f.id AS file_id, v.id AS version_id, v.source_version "
            "FROM source_versions v JOIN source_files f ON f.id = v.source_file_id "
            "WHERE v.source_hash = ? AND f.deleted_at IS NULL ORDER BY v.id LIMIT 1",
            (source_hash,),
        )
        if existing is not None:
            return IngestResult(
                source_id=str(existing["source_id"]),
                source_file_id=int(existing["file_id"]),
                source_version_id=int(existing["version_id"]),
                source_version=int(existing["source_version"]),
                source_hash=source_hash,
                blob_sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                created=False,
                duplicate_of=str(existing["source_id"]),
            )
        _record_blob(writer, stored, stamp)
        source_id = "SRC-" + secrets.token_hex(6)
        file_id = writer.insert(
            Owner.SOURCE,
            Actor.INGESTER,
            "source_files",
            {
                "source_id": source_id,
                "provider": provider,
                "display_name": name,
                "mime_type": mime_type,
                "origin": ORIGINS[provider],
                "created_at": stamp,
            },
        )
        version_id = writer.insert(
            Owner.SOURCE,
            Actor.INGESTER,
            "source_versions",
            {
                "source_file_id": file_id,
                "source_version": 1,
                "source_hash": source_hash,
                "blob_sha256": stored.sha256,
                "created_at": stamp,
            },
        )
        _ref(writer, stored.sha256, "source_version", version_id)
        return IngestResult(source_id, file_id, version_id, 1, source_hash, stored.sha256, stored.size_bytes, created=True)


def add_version(
    writer: StateWriter,
    blobs: BlobStore,
    source_id: str,
    stream: BinaryIO,
    *,
    now: str | None = None,
) -> IngestResult:
    """Record a revision of ``source_id`` as a new version that supersedes the latest one (design §4.4).

    Identical bytes to the latest version are a no-op (``created=False``).
    Older versions are kept; their locators and AI artifacts become stale by fingerprint.
    """

    stamp = now or _now()
    stored = blobs.put_stream(stream)
    source_hash = "sha256:" + stored.sha256
    with writer.transaction():
        latest = writer.query_one(
            "SELECT f.id AS file_id, v.id AS version_id, v.source_version, v.source_hash "
            "FROM source_files f JOIN source_versions v ON v.source_file_id = f.id "
            "WHERE f.source_id = ? AND f.deleted_at IS NULL ORDER BY v.source_version DESC LIMIT 1",
            (source_id,),
        )
        if latest is None:
            raise IngestError(f"unknown source {source_id}")
        if latest["source_hash"] == source_hash:
            return IngestResult(
                source_id, int(latest["file_id"]), int(latest["version_id"]), int(latest["source_version"]),
                source_hash, stored.sha256, stored.size_bytes, created=False,
            )
        earlier = writer.query_one(
            "SELECT id, source_version FROM source_versions WHERE source_file_id = ? AND source_hash = ?",
            (int(latest["file_id"]), source_hash),
        )
        if earlier is not None:
            # Going back to bytes of an older version is still a NEW version: history is linear
            # and older locators must not silently become current again. The UNIQUE constraint
            # (source_file_id, source_hash) forbids that, so report it instead of guessing.
            raise IngestError(
                f"content equals version {int(earlier['source_version'])} of {source_id}; restore it explicitly"
            )
        _record_blob(writer, stored, stamp)
        number = int(latest["source_version"]) + 1
        version_id = writer.insert(
            Owner.SOURCE,
            Actor.INGESTER,
            "source_versions",
            {
                "source_file_id": int(latest["file_id"]),
                "source_version": number,
                "source_hash": source_hash,
                "blob_sha256": stored.sha256,
                "supersedes_id": int(latest["version_id"]),
                "created_at": stamp,
            },
        )
        _ref(writer, stored.sha256, "source_version", version_id)
        return IngestResult(source_id, int(latest["file_id"]), version_id, number, source_hash, stored.sha256, stored.size_bytes, created=True)
