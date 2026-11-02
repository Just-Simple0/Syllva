"""Normalize a locally stored source version into v2 derivatives (spec §3.2, §3.7, §11.2).

Reads the original bytes from the blob store (hash-verified), runs the existing
deterministic normalizers (PDF, transcript), and stores two derivatives:

* ``normalized`` — the canonical Markdown derivative (front matter + body with
  page/timestamp markers);
* ``chunks`` — JSON list of locator-addressed chunks used by retrieval
  (``normalization.chunks``; schema ``syllva.chunks.v2`` with parts, errata E14).

Completeness follows the normalizer result exactly (design §2 I3): only a
``ready`` result is ``COMPLETE``; ``partial`` and ``needs_review`` are stored as
``PARTIAL``; a failed/unavailable extraction, or one that yields no chunk at
all (nothing to cite), stores nothing. Normalization is never summarisation:
no model is involved here.

Re-running is safe (spec §11.2): the derivative key is
``(source_version, kind, schema_version, processor_version)``; when both
derivatives exist they are returned without recomputation. ``normalized_at``
defaults to the source version's ``created_at`` so the bytes are deterministic.
A different ``entity_id``/``course_key`` for an already-normalized version needs
a new ``processor_version`` (or schema version) to produce new derivatives.

The caller owns the material state machine; ``NormalizeResult.status`` is the
``ProcessingStatus`` to transition to.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from syllva.domain.source_ref import v2_source_ref
from syllva.library.blobs import BlobStore
from syllva.normalization.chunks import DEFAULT_MAX_CHARS, Chunk, pdf_chunks, transcript_chunks
from syllva.normalization.pdf import extract_pdf
from syllva.normalization.transcript import normalize_transcript
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

SourceKind = Literal["pdf", "transcript"]
CHUNKS_SCHEMA = "syllva.chunks.v2"  # v2: rows carry part/parts (errata E14)
_STATUS = {"ready": "READY", "partial": "PARTIAL", "needs_review": "NEEDS_REVIEW", "failed": "FAILED"}


class NormalizeError(RuntimeError):
    code = "NORMALIZE_FAILED"


@dataclass(frozen=True)
class NormalizeResult:
    status: str  # ProcessingStatus value for the material
    completeness: str | None  # COMPLETE / PARTIAL / None when nothing was stored
    normalized_derivative_id: int | None
    chunks_derivative_id: int | None
    chunk_count: int  # -1 when the derivatives already existed and were not recomputed
    reason: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def chunks_payload(chunks: Sequence[Chunk]) -> bytes:
    rows = [chunk.as_row() for chunk in chunks]
    return json.dumps({"schema": CHUNKS_SCHEMA, "chunks": rows}, ensure_ascii=False, sort_keys=True).encode("utf-8")


_INVISIBLE = "\ufeff\u200b\u200c\u200d\u2060\x00"


def _has_text(content: str) -> bool:
    return bool(content.translate({ord(c): None for c in _INVISIBLE}).strip())


def _existing(writer: StateWriter, source_version_id: int, schema_version: str, processor_version: str) -> NormalizeResult | None:
    rows = writer.query(
        "SELECT id, kind, completeness, blob_sha256 FROM derivatives "
        "WHERE source_version_id = ? AND schema_version = ? AND processor_version = ?",
        (source_version_id, schema_version, processor_version),
    )
    by_kind = {str(r["kind"]): r for r in rows}
    if set(by_kind) != {"normalized", "chunks"}:
        if by_kind:
            raise NormalizeError("incomplete derivative set for this processor version; bump processor_version")
        return None
    completeness = str(by_kind["normalized"]["completeness"])
    # Only completeness is stored, so a result first reported as NEEDS_REVIEW (with some
    # citable text) is reported as PARTIAL on re-run; both mean "not complete".
    status = "READY" if completeness == "COMPLETE" else "PARTIAL"
    return NormalizeResult(status, completeness, int(by_kind["normalized"]["id"]), int(by_kind["chunks"]["id"]), -1, "ALREADY_NORMALIZED")


def normalize_source_version(
    writer: StateWriter,
    blobs: BlobStore,
    source_version_id: int,
    *,
    kind: SourceKind,
    entity_id: str,
    course_key: str,
    processor_version: str,
    schema_version: str = "2",
    max_chunk_chars: int = DEFAULT_MAX_CHARS,
    now: str | None = None,
) -> NormalizeResult:
    row = writer.query_one(
        "SELECT v.source_version, v.source_hash, v.blob_sha256, v.created_at, f.source_id, f.provider "
        "FROM source_versions v JOIN source_files f ON f.id = v.source_file_id WHERE v.id = ?",
        (source_version_id,),
    )
    if row is None:
        raise NormalizeError(f"unknown source version {source_version_id}")
    existing = _existing(writer, source_version_id, schema_version, processor_version)
    if existing is not None:
        return existing
    stamp = now or str(row["created_at"])  # deterministic: same inputs -> same derivative bytes
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
        chunks = pdf_chunks(pdf, entity_id, max_chars=max_chunk_chars) if derivative_status != "failed" else []
    elif kind == "transcript":
        try:
            transcript = normalize_transcript(raw, **common)  # type: ignore[arg-type]
        except UnicodeDecodeError:
            return NormalizeResult("FAILED", None, None, None, 0, "TRANSCRIPT_NOT_UTF8")
        derivative_status = str(transcript.front_matter.status)
        markdown = transcript.to_markdown()
        chunks = transcript_chunks(transcript, entity_id, max_chars=max_chunk_chars)
    else:  # pragma: no cover - Literal guards this
        raise NormalizeError(f"unsupported kind {kind!r}")

    status = _STATUS.get(derivative_status.lower(), "FAILED")
    if status == "FAILED":
        return NormalizeResult(status, None, None, None, 0, reason)
    if not any(_has_text(chunk.content) for chunk in chunks):
        if derivative_status in ("ready", "partial") and kind == "pdf":
            # Text was extracted but cannot be attributed to pages (multi-page PDF without
            # per-page text). Chunks come from per-page text since R1b-5 (errata E12 resolved),
            # so a "Page N" footer line no longer lands here.
            return NormalizeResult("NEEDS_REVIEW", None, None, None, 0, "NO_PAGE_INDEX")
        # Nothing citable was extracted (marker-only pages carry no SOURCE text) (e.g. unreadable or image-only PDF): store no
        # derivative rather than an empty PARTIAL one; a person has to look at it.
        return NormalizeResult("NEEDS_REVIEW", None, None, None, 0, reason or "NO_EXTRACTABLE_TEXT")
    completeness = "COMPLETE" if status == "READY" else "PARTIAL"

    normalized_blob = blobs.put_bytes(markdown.encode("utf-8"))
    chunks_blob = blobs.put_bytes(chunks_payload(chunks))
    with writer.transaction():
        concurrent = _existing(writer, source_version_id, schema_version, processor_version)
        if concurrent is not None:  # another caller finished first; stay idempotent
            return concurrent
        derivative_ids: list[int] = []
        for derivative_kind, stored in (("normalized", normalized_blob), ("chunks", chunks_blob)):
            if writer.query_one("SELECT 1 FROM blobs WHERE sha256 = ?", (stored.sha256,)) is None:
                writer.insert(Owner.SOURCE, Actor.INGESTER, "blobs", {"sha256": stored.sha256, "size_bytes": stored.size_bytes, "created_at": stamp})
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
