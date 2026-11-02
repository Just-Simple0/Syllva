"""C-02 and C-10: the v2 Retrieval Service is read-only and serves only allowlisted, current locators.

Spec §8.6, §9.1-§9.2; design I1, I5, I6. The data goes through the real v2
pipeline: ingest -> normalize -> RetrievalService.
"""

from __future__ import annotations

import io
import sqlite3

import pytest
from conftest import NOW

from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.ingestion.local import add_version, ingest_new
from syllva.library.blobs import BlobStore
from syllva.normalization.local import normalize_source_version
from syllva.retrieval.schemas import RetrievalBudget
from syllva.retrieval.service import EntityUnknown, LocatorNotAuthorized, RetrievalService
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

TRANSCRIPT = "[00:00] intro\nwelcome\n[01:30] TCP handshake\nSYN, SYN-ACK, ACK\n"
SCOPE = "ui-session-1"


class Library:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.blobs = BlobStore(db.layout.blobs_dir, db.layout.tmp_dir)
        conn = db.connection
        self.course = conn.execute(
            "INSERT INTO courses(course_key, name, semester, created_at) VALUES ('LMS1', '네프', '2026-2', ?)", (NOW,)
        ).lastrowid
        self.session = conn.execute(
            "INSERT INTO sessions(session_id, course_id, number, created_at) VALUES ('LMS1-S02', ?, 2, ?)", (self.course, NOW)
        ).lastrowid

    def material(self, material_id: str, text: str, *, normalize: bool = True) -> str:
        ingested = ingest_new(self.db.writer(), self.blobs, io.BytesIO(text.encode()), display_name=f"{material_id}.txt")
        self.db.connection.execute(
            "INSERT INTO materials(material_id, course_id, session_id, source_file_id, kind, role, created_at) "
            "VALUES (?, ?, ?, ?, 'TRANSCRIPT', 'PRIMARY', ?)",
            (material_id, self.course, self.session, ingested.source_file_id, NOW),
        )
        if normalize:
            self.normalize(ingested.source_version_id, material_id)
        return ingested.source_id

    def normalize(self, version_id: int, material_id: str) -> None:
        normalize_source_version(
            self.db.writer(), self.blobs, version_id, kind="transcript", entity_id=material_id, course_key="LMS1", processor_version="p1"
        )

    def service(self, **kwargs: object) -> RetrievalService:
        return RetrievalService(self.db.reader(), self.blobs, MemoryEphemeralStore(), **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def lib(v2_db: Database) -> Library:
    return Library(v2_db)


# --- C-02: read-only service ---------------------------------------------------

def test_service_reader_cannot_write(lib: Library) -> None:
    service = lib.service()
    assert not hasattr(service, "writer")
    with pytest.raises(sqlite3.OperationalError):
        service.reader.fetch_all("DELETE FROM materials")


def test_service_module_never_imports_the_writer() -> None:
    """Direct check in addition to the v2-retrieval-clean import contract."""

    import ast
    from pathlib import Path

    import syllva.retrieval.service as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m.startswith(("syllva.state.v2.writer", "syllva.state.v2.database"))}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "StateWriter" not in names


# --- context assembly ----------------------------------------------------------

def test_material_context_uses_current_version_and_binds_a_capability(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert package.protocol_version == "2.0" and package.context_id
    current = lib.db.connection.execute(
        "SELECT source_version, source_hash FROM source_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert len(package.sources) == 2 and all(i.entity_id == "LMS1-M01" for i in package.sources)
    assert {(i.fingerprint.source_version, i.fingerprint.source_hash) for i in package.sources} == {tuple(current)}
    assert all(i.provenance.source_ref.provider == "local" for i in package.sources)
    assert package.warnings == ()


def test_stale_derivative_is_not_evidence(lib: Library) -> None:
    source_id = lib.material("LMS1-M01", TRANSCRIPT)
    add_version(lib.db.writer(), lib.blobs, source_id, io.BytesIO(b"[00:00] revised"))
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert package.sources == ()
    assert {"code": "STALE_DERIVATIVE", "material_id": "LMS1-M01"} in package.warnings


def test_partial_source_is_flagged(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] a\n[99:99:99:99] broken\n")
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert package.sources and {"code": "PARTIAL_SOURCE", "material_id": "LMS1-M01"} in package.warnings


def test_budget_drops_are_reported_and_not_allowlisted(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service(budget=RetrievalBudget(max_evidence_items=1))
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert len(package.sources) == 1
    assert {"code": "PARTIAL_CONTEXT", "used": 1, "total": 2} in package.warnings
    full = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    dropped = next(str(i.locator) for i in full.sources if str(i.locator) != str(package.sources[0].locator))
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(package.context_id or "", dropped, caller_scope=SCOPE)


def test_session_context_and_unknown_entities(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    lib.material("LMS1-M02", "[00:00] second\n")
    package = lib.service().get_session_context("LMS1-S02", caller_scope=SCOPE)
    assert {i.entity_id for i in package.sources} == {"LMS1-M01", "LMS1-M02"}
    with pytest.raises(EntityUnknown):
        lib.service().get_session_context("LMS1-S99", caller_scope=SCOPE)
    with pytest.raises(EntityUnknown):
        lib.service().get_material_context("LMS1-M99", caller_scope=SCOPE)


# --- C-10: allowlisted, current locators only ------------------------------------

def test_allowlisted_locator_is_served(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    first = package.sources[0]
    served = service.get_source_chunk(package.context_id or "", str(first.locator), caller_scope=SCOPE)
    assert served.content == first.content and served.fingerprint == first.fingerprint


def test_locator_outside_the_package_is_refused(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    lib.material("LMS1-M02", "[00:00] other material\n")
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    other = lib.service().get_material_context("LMS1-M02", caller_scope=SCOPE)
    for locator in (str(other.sources[0].locator), "LMS1-M01:t00:59:00-01:00:00", "not a locator", ""):
        with pytest.raises(LocatorNotAuthorized):
            service.get_source_chunk(package.context_id or "", locator, caller_scope=SCOPE)


def test_another_ui_session_cannot_use_the_context(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(package.context_id or "", str(package.sources[0].locator), caller_scope="ui-session-2")
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk("ctx_unknown", str(package.sources[0].locator), caller_scope=SCOPE)


def test_locator_of_a_superseded_version_is_refused(lib: Library) -> None:
    source_id = lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    locator = str(package.sources[0].locator)
    revised = add_version(lib.db.writer(), lib.blobs, source_id, io.BytesIO(TRANSCRIPT.replace("welcome", "hello").encode()))
    lib.normalize(revised.source_version_id, "LMS1-M01")
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(package.context_id or "", locator, caller_scope=SCOPE)


def test_expired_context_is_refused(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    from syllva.ephemeral import memory

    lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    real = memory.time.monotonic
    monkeypatch.setattr(memory.time, "monotonic", lambda: real() + 10_000)
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(package.context_id or "", str(package.sources[0].locator), caller_scope=SCOPE)


def test_caller_scope_is_required(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    from syllva.domain.errors import PolicyViolation

    with pytest.raises(PolicyViolation):
        lib.service().get_material_context("LMS1-M01", caller_scope="")


def test_oversize_chunk_is_reported_and_context_has_no_holes(lib: Library) -> None:
    """Spec errata E14: a chunk over max_chars_per_item is reported per locator until R1b-5 splits it."""

    big = "x" * 5000
    lib.material("LMS1-M01", f"[00:00] a\n[01:00] {big}\n[02:00] c\n")
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    codes = [w["code"] for w in package.warnings]
    assert "OVERSIZE_CHUNK" in codes
    oversize = next(w for w in package.warnings if w["code"] == "OVERSIZE_CHUNK")
    assert oversize["locator"].startswith("LMS1-M01:t00:01:00")
    assert [str(i.locator).split(":t")[1][:8] for i in package.sources] == ["00:00:00", "00:02:00"]


def test_budget_stops_at_the_first_chunk_that_does_not_fit(lib: Library) -> None:
    lib.material("LMS1-M01", "[00:00] " + "a" * 30 + "\n[01:00] " + "b" * 300 + "\n[02:00] c\n")
    package = lib.service(budget=RetrievalBudget(max_total_chars=100)).get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert [str(i.locator).split(":t")[1][:8] for i in package.sources] == ["00:00:00"]  # c is NOT added after the gap
    assert {"code": "PARTIAL_CONTEXT", "used": 1, "total": 3} in package.warnings


def test_served_text_is_the_packaged_text_even_after_renormalization(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    service = lib.service()
    package = service.get_material_context("LMS1-M01", caller_scope=SCOPE)
    first = package.sources[0]
    version_id = lib.db.connection.execute("SELECT id FROM source_versions").fetchone()[0]
    normalize_source_version(
        lib.db.writer(), lib.blobs, version_id, kind="transcript", entity_id="LMS1-M01", course_key="LMS1", processor_version="p2"
    )
    served = service.get_source_chunk(package.context_id or "", str(first.locator), caller_scope=SCOPE)
    assert served.content == first.content and served.provenance.processor_version == "p1"


def test_missing_blob_drops_only_that_material(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT)
    lib.material("LMS1-M02", "[00:00] second\n")
    sha = lib.db.connection.execute(
        "SELECT d.blob_sha256 FROM derivatives d JOIN source_versions v ON v.id = d.source_version_id "
        "JOIN materials m ON m.source_file_id = v.source_file_id WHERE m.material_id = 'LMS1-M02' AND d.kind = 'chunks'"
    ).fetchone()[0]
    lib.blobs.delete(sha)
    package = lib.service().get_session_context("LMS1-S02", caller_scope=SCOPE)
    assert {i.entity_id for i in package.sources} == {"LMS1-M01"}
    assert {"code": "BLOB_UNAVAILABLE", "material_id": "LMS1-M02"} in package.warnings


def test_derivative_for_another_entity_is_not_evidence(lib: Library) -> None:
    lib.material("LMS1-M01", TRANSCRIPT, normalize=False)
    version_id = lib.db.connection.execute("SELECT id FROM source_versions").fetchone()[0]
    lib.normalize(version_id, "LMS1-M09")  # normalized under the wrong entity id
    package = lib.service().get_material_context("LMS1-M01", caller_scope=SCOPE)
    assert package.sources == () and {"code": "LOCATOR_ENTITY_MISMATCH", "material_id": "LMS1-M01"} in package.warnings
