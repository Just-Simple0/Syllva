"""R1c-2: revision replacement (implementation spec §11.4, design §4.4, I5, C-10).

Replacing a material's file adds a NEW source version (the old one is kept),
processes it, and from then on only the new version is evidence: a locator
handed out for the old version is refused. A stale queued run of the old
version can never make the material READY again.
"""

from __future__ import annotations

import io
import sqlite3

import pytest
from conftest import NOW
from test_v2_intake_local import TRANSCRIPT, _drop, intake  # noqa: F401  (fixture)

from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.intake.v2 import IntakeError, LocalIntake
from syllva.retrieval.service import LocatorNotAuthorized, RetrievalService
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

REVISED = "[00:00] 오늘은 UDP\n[01:00] datagram, no handshake\n"


def _ready(intake: LocalIntake) -> str:  # noqa: F811
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "s1.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    [done] = intake.run_pending(now=NOW)
    assert done.state == "READY" and done.material_id
    return done.material_id


def _replace(intake: LocalIntake, material_id: str, text: str):  # type: ignore[no-untyped-def]  # noqa: F811
    return intake.replace(material_id, io.BytesIO(text.encode()), display_name="s1 (rev).txt", now=NOW)


def test_replacement_becomes_the_only_evidence(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    service = RetrievalService(v2_db.reader(), intake.blobs, MemoryEphemeralStore())
    before = service.get_material_context(material, caller_scope="ui")
    old_locator = str(before.sources[0].locator)
    replacement = _replace(intake, material, REVISED)
    assert (replacement.state, replacement.material_id) == ("CONFIRMED", material)
    [done] = intake.run_pending(now=NOW)
    assert done.state == "READY"
    after = service.get_material_context(material, caller_scope="ui")
    assert "UDP" in after.sources[0].content and all("TCP" not in i.content for i in after.sources)
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(before.context_id or "", old_locator, caller_scope="ui")
    versions = v2_db.connection.execute("SELECT source_version, supersedes_id IS NOT NULL FROM source_versions ORDER BY id").fetchall()
    assert [tuple(v) for v in versions] == [(1, 0), (2, 1)]  # the old version is kept


def test_identical_or_older_bytes_are_refused(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    with pytest.raises(IntakeError):
        _replace(intake, material, TRANSCRIPT)
    _replace(intake, material, REVISED)
    intake.run_pending(now=NOW)
    with pytest.raises(IntakeError):
        _replace(intake, material, TRANSCRIPT)  # going back to v1 bytes needs an explicit restore
    assert v2_db.connection.execute("SELECT count(*) FROM intake_candidates WHERE replaces_material IS NOT NULL").fetchone()[0] == 1


def test_replace_waits_for_running_work(intake: LocalIntake) -> None:  # noqa: F811
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "w.txt").candidate_id, now=NOW)
    confirmed = intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    with pytest.raises(IntakeError):
        _replace(intake, confirmed.material_id or "", REVISED)


def test_a_stale_run_of_the_old_version_never_wins(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    first = v2_db.connection.execute("SELECT candidate_id FROM intake_candidates ORDER BY id").fetchone()[0]
    _replace(intake, material, REVISED)
    intake.run_pending(now=NOW)
    intake.retry(first, now=NOW)  # someone re-queues the ORIGINAL candidate (old version)
    stale = intake.process(first, now=NOW)
    assert (stale.state, stale.reason_code) == ("FAILED", "SUPERSEDED")
    status = v2_db.connection.execute("SELECT processing_status FROM materials").fetchone()[0]
    assert status == "READY"  # the material still reflects the current version


def test_shortcut_confirmation_is_only_for_replacements(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    fresh = _drop(intake, b"[00:00] new\n", "n.txt")
    row = v2_db.connection.execute("SELECT id FROM materials WHERE material_id = ?", (material,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="REPLACEMENT_ONLY"):
        v2_db.connection.execute(
            "UPDATE intake_candidates SET state = 'CONFIRMED', material_id = ? WHERE candidate_id = ?", (row, fresh.candidate_id)
        )
    assert intake.writer.query_one("SELECT 1 FROM intake_candidates WHERE replaces_material IS NOT NULL") is None
    rep = _replace(intake, material, REVISED)
    with pytest.raises(sqlite3.IntegrityError, match="IDENTITY_IMMUTABLE"):
        v2_db.connection.execute("UPDATE intake_candidates SET replaces_material = NULL WHERE candidate_id = ?", (rep.candidate_id,))


def test_material_is_pending_and_serves_no_old_evidence_until_the_revision_is_processed(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    service = RetrievalService(v2_db.reader(), intake.blobs, MemoryEphemeralStore())
    before = service.get_material_context(material, caller_scope="ui")
    _replace(intake, material, REVISED)
    status = v2_db.connection.execute("SELECT processing_status FROM materials").fetchone()[0]
    assert status == "PENDING"
    window = service.get_material_context(material, caller_scope="ui")
    assert not window.sources and {"code": "STALE_DERIVATIVE", "material_id": material} in window.warnings
    with pytest.raises(LocatorNotAuthorized):
        service.get_source_chunk(before.context_id or "", str(before.sources[0].locator), caller_scope="ui")


def test_replacement_cannot_be_redirected_or_classified(intake: LocalIntake, v2_db: Database) -> None:  # noqa: F811
    material = _ready(intake)
    c = intake.classify(_drop(intake, b"[00:00] other\n", "o.txt").candidate_id, now=NOW)
    other = intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-02", now=NOW)
    intake.run_pending(now=NOW)
    other_row = v2_db.connection.execute("SELECT id FROM materials WHERE material_id = ?", (other.material_id,)).fetchone()[0]
    rep = _replace(intake, material, REVISED)
    conn = v2_db.connection
    with pytest.raises(sqlite3.IntegrityError):  # the material a candidate became never changes
        conn.execute("UPDATE intake_candidates SET material_id = ? WHERE candidate_id = ?", (other_row, rep.candidate_id))
    with pytest.raises(sqlite3.IntegrityError, match="REPLACEMENT_ONLY|INTAKE_TRANSITION"):
        conn.execute("UPDATE OR REPLACE intake_candidates SET state = 'CLASSIFYING' WHERE candidate_id = ?", (rep.candidate_id,))
