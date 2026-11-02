"""Local ingestion and normalization into v2 state (spec §3.2, §4.4, §11; design I3, I5).

R1b-3: originals become content-addressed blobs + source rows; revisions are new
versions; normalization stores deterministic derivatives whose completeness never
claims more than the normalizer produced.
"""

from __future__ import annotations

import io
import json
import unicodedata

import pytest
from conftest import make_pdf_bytes

from syllva.domain.source_ref import V2_PROVIDERS, v2_source_ref
from syllva.ingestion.local import IngestError, add_version, ingest_new
from syllva.library.blobs import BlobCorrupt, BlobStore
from syllva.normalization.local import NormalizeError, normalize_source_version
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

TRANSCRIPT = "[00:00] intro\nwelcome\n[01:30] TCP handshake\nSYN, SYN-ACK, ACK\n"


@pytest.fixture
def blobs(v2_db: Database) -> BlobStore:
    return BlobStore(v2_db.layout.blobs_dir, v2_db.layout.tmp_dir)


def _ingest(v2_db: Database, blobs: BlobStore, data: bytes, name: str = "ch04.pdf"):  # type: ignore[no-untyped-def]
    return ingest_new(v2_db.writer(), blobs, io.BytesIO(data), display_name=name, mime_type="application/pdf")


def test_v2_source_refs_reject_legacy_providers() -> None:
    assert V2_PROVIDERS == {"local", "canvas", "github"}
    assert v2_source_ref("local", "SRC-1").canonical_key == "local:SRC-1"
    for provider, file_id in (("google_drive", "x"), ("local", "../etc"), ("local", "a\\b"), ("local", " ")):
        with pytest.raises(ValueError):
            v2_source_ref(provider, file_id)


def test_new_file_becomes_blob_source_and_first_version(v2_db: Database, blobs: BlobStore) -> None:
    decomposed = unicodedata.normalize("NFD", "알고리즘 4주차.pdf")
    result = _ingest(v2_db, blobs, b"%PDF-fake", decomposed)
    assert result.created and result.source_version == 1 and result.source_id.startswith("SRC-")
    assert blobs.read_bytes(result.blob_sha256, verify=True) == b"%PDF-fake"
    conn = v2_db.connection
    name, provider, origin = conn.execute("SELECT display_name, provider, origin FROM source_files").fetchone()
    assert name == unicodedata.normalize("NFC", decomposed) and provider == "local" and origin == "USER_DROP"
    assert conn.execute("SELECT source_hash FROM source_versions").fetchone()[0] == "sha256:" + result.blob_sha256
    assert [tuple(r) for r in conn.execute("SELECT owner_kind, owner_id FROM blob_refs")] == [("source_version", result.source_version_id)]


def test_identical_bytes_are_reported_as_duplicate(v2_db: Database, blobs: BlobStore) -> None:
    first = _ingest(v2_db, blobs, b"same bytes", "a.pdf")
    second = _ingest(v2_db, blobs, b"same bytes", "copy of a.pdf")
    assert not second.created and second.duplicate_of == first.source_id
    assert v2_db.connection.execute("SELECT count(*) FROM source_files").fetchone()[0] == 1


def test_revision_is_a_new_version_that_supersedes_the_previous(v2_db: Database, blobs: BlobStore) -> None:
    writer = v2_db.writer()
    first = _ingest(v2_db, blobs, b"v1 bytes")
    second = add_version(writer, blobs, first.source_id, io.BytesIO(b"v2 bytes"))
    assert second.created and second.source_version == 2
    supersedes = v2_db.connection.execute("SELECT supersedes_id FROM source_versions WHERE id = ?", (second.source_version_id,)).fetchone()[0]
    assert supersedes == first.source_version_id
    unchanged = add_version(writer, blobs, first.source_id, io.BytesIO(b"v2 bytes"))
    assert not unchanged.created and unchanged.source_version_id == second.source_version_id
    with pytest.raises(IngestError, match="equals version 1"):
        add_version(writer, blobs, first.source_id, io.BytesIO(b"v1 bytes"))
    with pytest.raises(IngestError, match="unknown source"):
        add_version(writer, blobs, "SRC-missing", io.BytesIO(b"x"))


@pytest.mark.parametrize("name", ["", "a\nb", "a\x00b"])
def test_display_names_are_single_line(v2_db: Database, blobs: BlobStore, name: str) -> None:
    with pytest.raises(IngestError):
        _ingest(v2_db, blobs, b"x", name)


def test_provider_must_be_v2(v2_db: Database, blobs: BlobStore) -> None:
    with pytest.raises(IngestError):
        ingest_new(v2_db.writer(), blobs, io.BytesIO(b"x"), display_name="a", provider="google_drive")


def _normalize(v2_db: Database, blobs: BlobStore, version_id: int, kind: str, processor: str = "p1"):  # type: ignore[no-untyped-def]
    return normalize_source_version(
        v2_db.writer(), blobs, version_id, kind=kind, entity_id="LMS1-S01", course_key="LMS1", processor_version=processor  # type: ignore[arg-type]
    )


def _chunks(v2_db: Database, blobs: BlobStore, derivative_id: int) -> list[dict[str, object]]:
    sha = v2_db.connection.execute("SELECT blob_sha256 FROM derivatives WHERE id = ?", (derivative_id,)).fetchone()[0]
    payload = json.loads(blobs.read_bytes(sha))
    assert payload["schema"] == "syllva.chunks.v1"
    return list(payload["chunks"])


def test_transcript_normalizes_complete_with_time_locators(v2_db: Database, blobs: BlobStore) -> None:
    ingested = _ingest(v2_db, blobs, TRANSCRIPT.encode(), "S01.txt")
    result = _normalize(v2_db, blobs, ingested.source_version_id, "transcript")
    assert result.status == "READY" and result.completeness == "COMPLETE" and result.chunk_count == 2
    chunks = _chunks(v2_db, blobs, result.chunks_derivative_id)  # type: ignore[arg-type]
    assert [c["content"].splitlines()[0] for c in chunks] == ["[00:00] intro", "[01:30] TCP handshake"]  # type: ignore[union-attr]
    sha = v2_db.connection.execute("SELECT blob_sha256 FROM derivatives WHERE id = ?", (result.normalized_derivative_id,)).fetchone()[0]
    normalized = blobs.read_bytes(sha).decode()
    assert "provider: local" in normalized and "google_drive" not in normalized
    assert ingested.source_hash in normalized  # fingerprint travels with the derivative (I5)


def test_broken_timestamps_are_partial_never_complete(v2_db: Database, blobs: BlobStore) -> None:
    ingested = _ingest(v2_db, blobs, b"[00:00] a\n[99:99:99:99] broken\n", "S02.txt")
    result = _normalize(v2_db, blobs, ingested.source_version_id, "transcript")
    assert result.status == "PARTIAL" and result.completeness == "PARTIAL"
    rows = v2_db.connection.execute("SELECT completeness FROM derivatives").fetchall()
    assert {r[0] for r in rows} == {"PARTIAL"}


def test_pdf_pages_become_page_chunks(v2_db: Database, blobs: BlobStore) -> None:
    pytest.importorskip("pypdf")
    ingested = _ingest(v2_db, blobs, make_pdf_bytes(["TCP three-way handshake", "Flow control"]))
    result = _normalize(v2_db, blobs, ingested.source_version_id, "pdf")
    assert result.status == "READY" and result.completeness == "COMPLETE"
    chunks = _chunks(v2_db, blobs, result.chunks_derivative_id)  # type: ignore[arg-type]
    assert len(chunks) == 2 and "TCP three-way handshake" in str(chunks[0]["content"])
    assert all("p" in str(c["locator"]) for c in chunks)


def test_pdf_with_a_textless_page_is_partial(v2_db: Database, blobs: BlobStore) -> None:
    pytest.importorskip("pypdf")
    ingested = _ingest(v2_db, blobs, make_pdf_bytes(["page one", None]))
    result = _normalize(v2_db, blobs, ingested.source_version_id, "pdf")
    assert result.completeness == "PARTIAL" and result.status in {"PARTIAL", "NEEDS_REVIEW"}


def test_unreadable_pdf_stores_nothing(v2_db: Database, blobs: BlobStore) -> None:
    ingested = _ingest(v2_db, blobs, b"%PDF-1.4\nnot a pdf\n%%EOF")
    result = _normalize(v2_db, blobs, ingested.source_version_id, "pdf")
    assert result.status in {"FAILED", "NEEDS_REVIEW"}
    if result.status == "FAILED":
        assert result.completeness is None
        assert v2_db.connection.execute("SELECT count(*) FROM derivatives").fetchone()[0] == 0


def test_normalization_is_idempotent_per_processor_version(v2_db: Database, blobs: BlobStore) -> None:
    ingested = _ingest(v2_db, blobs, TRANSCRIPT.encode(), "S01.txt")
    first = _normalize(v2_db, blobs, ingested.source_version_id, "transcript")
    again = _normalize(v2_db, blobs, ingested.source_version_id, "transcript")
    assert (again.normalized_derivative_id, again.chunks_derivative_id) == (first.normalized_derivative_id, first.chunks_derivative_id)
    newer = _normalize(v2_db, blobs, ingested.source_version_id, "transcript", processor="p2")
    assert newer.normalized_derivative_id != first.normalized_derivative_id


def test_tampered_original_is_refused(v2_db: Database, blobs: BlobStore) -> None:
    import os
    import stat

    ingested = _ingest(v2_db, blobs, TRANSCRIPT.encode(), "S01.txt")
    path = blobs.path_for(ingested.blob_sha256)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    path.write_bytes(b"[00:00] changed")
    with pytest.raises(BlobCorrupt):
        _normalize(v2_db, blobs, ingested.source_version_id, "transcript")


def test_unknown_version(v2_db: Database, blobs: BlobStore) -> None:
    with pytest.raises(NormalizeError):
        _normalize(v2_db, blobs, 999, "transcript")
