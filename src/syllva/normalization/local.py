"""Normalize a locally stored source version into v2 derivatives (spec §3.2, §3.7, §11.2).

Reads the original bytes from the blob store (hash-verified), runs the existing
deterministic normalizers (PDF, transcript), and stores two derivatives:

* ``normalized`` — the canonical Markdown derivative (front matter + body with
  page/timestamp markers);
* ``chunks`` — JSON list of locator-addressed chunks used by retrieval.

Completeness follows the normalizer result exactly (design §2 I3): only a
``ready`` result is ``COMPLETE``; ``partial`` and ``needs_review`` are stored as
``PARTIAL``; a failed/unavailable extraction stores nothing. Normalization is
never summarisation: no model is involved here.

The caller owns the material state machine; ``NormalizeResult.status`` is the
``ProcessingStatus`` to transition to.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from syllva.domain.models import serialize_locator
from syllva.domain.source_ref import v2_source_ref
from syllva.library.blobs import BlobStore
from syllva.normalization.pdf import extract_pdf
from syllva.normalization.transcript import normalize_transcript
from syllva.retrieval.chunking import DerivativeChunk, page_chunks, timestamp_chunks
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

SourceKind = Literal["pdf", "transcript"]
CHUNKS_SCHEMA = "syllva.chunks.v1"
_STATUS = {"ready": "READY", "partial": "PARTIAL", "needs_review": "NEEDS_REVIEW", "failed": "FAILED"}


class NormalizeError(RuntimeError):
    code = "NORMALIZE_FAILED"


@dataclass(frozen=True)
class NormalizeResult:
    status: str  # ProcessingStatus value for the material
    completeness: str | None  # COMPLETE / PARTIAL / None when nothing was stored
    normalized_derivative_id: int | None
    chunks_derivative_id: int | None
    chunk_count: int
    reason: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def chunks_payload(chunks: Sequence[DerivativeChunk]) -> bytes:
    rows = [
        {
            "locator": serialize_locator(chunk.locator),
            "content": chunk.content,
            "start_offset": chunk.start_offset,
            "end_offset": chunk.end_offset,
        }
        for chunk in chunks
    ]
    return json.dumps({"schema": CHUNKS_SCHEMA, "chunks": rows}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def normalize_source_version(
    writer: StateWriter,
    blobs: BlobStore,
    source_version_id: int,
    *,
    kind: SourceKind,
    entity_id: str,
    course_key: str,
    processor_version: str,
    schema_version: str = "1",
    now: str | None = None,
) -> NormalizeResult:
    stamp = now or _now()
    row = writer.query_one(
        "SELECT v.source_version, v.source_hash, v.blob_sha256, f.source_id, f.provider "
        "FROM source_versions v JOIN source_files f ON f.id = v.source_file_id WHERE v.id = ?",
        (source_version_id,),
    )
    if row is None:
        raise NormalizeError(f"unknown source version {source_version_id}")
    raw = blobs.read_bytes(str(row["blob_sha256"]), verify=True)
    source_ref = v2_source_ref(str(row["provider"]), str(row["source_id"]))
    common = {
        "entity_id": entity_id,
        "course_key": course_key,
        "source_ref": source_ref,
        "source_hash": str(row["source_hash"]),
        "source_version": int(row["source_version"]),
        "processor_version": processor_version,
        "now": stamp,
    }

    reason: str | None = None
    if kind == "pdf":
        pdf = extract_pdf(raw, **common)  # type: ignore[arg-type]
        derivative_status = pdf.derivative_status
        reason = pdf.reason
        markdown = pdf.to_markdown()
        chunks = page_chunks(markdown, entity_id=entity_id) if derivative_status in ("ready", "partial") else []
    elif kind == "transcript":
        transcript = normalize_transcript(raw, **common)  # type: ignore[arg-type]
        derivative_status = str(transcript.front_matter.status)
        markdown = transcript.to_markdown()
        chunks = timestamp_chunks(transcript, entity_id=entity_id)
    else:  # pragma: no cover - Literal guards this
        raise NormalizeError(f"unsupported kind {kind!r}")

    status = _STATUS.get(derivative_status.lower(), "FAILED")
    if status == "FAILED":
        return NormalizeResult(status, None, None, None, 0, reason)
    completeness = "COMPLETE" if status == "READY" else "PARTIAL"

    normalized_blob = blobs.put_bytes(markdown.encode("utf-8"))
    chunks_blob = blobs.put_bytes(chunks_payload(chunks))
    with writer.transaction():
        derivative_ids: list[int] = []
        for derivative_kind, stored in (("normalized", normalized_blob), ("chunks", chunks_blob)):
            if writer.query_one("SELECT 1 FROM blobs WHERE sha256 = ?", (stored.sha256,)) is None:
                writer.insert(Owner.SOURCE, Actor.INGESTER, "blobs", {"sha256": stored.sha256, "size_bytes": stored.size_bytes, "created_at": stamp})
            existing = writer.query_one(
                "SELECT id, blob_sha256, completeness FROM derivatives "
                "WHERE source_version_id = ? AND kind = ? AND schema_version = ? AND processor_version = ?",
                (source_version_id, derivative_kind, schema_version, processor_version),
            )
            if existing is not None:
                if existing["blob_sha256"] != stored.sha256 or existing["completeness"] != completeness:
                    raise NormalizeError(
                        "a derivative for this source version and processor version already exists with different "
                        "content; bump processor_version for a new normalizer"
                    )
                derivative_ids.append(int(existing["id"]))
                continue
            derivative_id = writer.insert(
                Owner.SOURCE,
                Actor.INGESTER,
                "derivatives",
                {
                    "source_version_id": source_version_id,
                    "kind": derivative_kind,
                    "schema_version": schema_version,
                    "processor_version": processor_version,
                    "blob_sha256": stored.sha256,
                    "completeness": completeness,
                    "created_at": stamp,
                },
            )
            writer.insert(Owner.SYSTEM, Actor.INGESTER, "blob_refs", {"sha256": stored.sha256, "owner_kind": "derivative", "owner_id": derivative_id})
            derivative_ids.append(derivative_id)
    return NormalizeResult(status, completeness, derivative_ids[0], derivative_ids[1], len(chunks), reason)
