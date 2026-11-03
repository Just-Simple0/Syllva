"""R1c: local intake worker (implementation spec §11, design §4.4, §2 I3).

The whole local path runs here with no provider: drop -> classify -> (confirm)
-> normalize -> search. The state machine is enforced by the schema (a reference
table + trigger) and mirrored in ``ownership.INTAKE_TRANSITIONS``; every
failure keeps its cause code; PARTIAL never becomes READY; only the user
confirms or dismisses.
"""

from __future__ import annotations

import io
import sqlite3

import pytest
from conftest import NOW, make_pdf_bytes

from syllva.domain.errors import PolicyViolation
from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.intake.v2 import IntakeConfig, IntakeError, LocalIntake
from syllva.library.blobs import BlobStore
from syllva.retrieval.service import RetrievalService
from syllva.state.v2.database import Database
from syllva.state.v2.ownership import INTAKE_TRANSITIONS, Actor, Owner

pytestmark = pytest.mark.contract

TRANSCRIPT = "[00:00] 오늘은 TCP\n[01:00] 3-way handshake SYN ACK\n"


@pytest.fixture
def intake(v2_db: Database) -> LocalIntake:
    conn = v2_db.connection
    conn.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET', '컴퓨터네트워크', 'NET', '2026-2', ?)",
        (NOW,),
    )
    conn.execute("INSERT INTO course_aliases(course_id, alias_nfc) VALUES (1, '네트워크')")
    blobs = BlobStore(v2_db.layout.blobs_dir, v2_db.layout.tmp_dir)
    return LocalIntake(v2_db.writer(), blobs, config=IntakeConfig(max_bytes=64 * 1024))


def _drop(intake: LocalIntake, data: bytes, name: str, **kw: object):  # type: ignore[no-untyped-def]
    return intake.receive(io.BytesIO(data), display_name=name, now=NOW, **kw)  # type: ignore[arg-type]


def _service(v2_db: Database, intake: LocalIntake) -> RetrievalService:
    return RetrievalService(v2_db.reader(), intake.blobs, MemoryEphemeralStore())


# --- the state machine is a schema invariant ---------------------------------------


def test_schema_transitions_equal_the_python_table(v2_db: Database) -> None:
    rows = v2_db.connection.execute("SELECT from_state, to_state FROM intake_state_transitions").fetchall()
    in_db = {(str(a), str(b)) for a, b in rows}
    in_py = {(a, b) for a, targets in INTAKE_TRANSITIONS.items() for b in targets}
    assert in_db == in_py
    assert ("PARTIAL", "READY") not in in_db  # design I3


def test_illegal_transition_is_refused_by_the_schema(intake: LocalIntake, v2_db: Database) -> None:
    candidate = _drop(intake, TRANSCRIPT.encode(), "note.txt")
    with pytest.raises(sqlite3.IntegrityError, match="INTAKE_TRANSITION"):
        v2_db.connection.execute("UPDATE intake_candidates SET state = 'READY' WHERE candidate_id = ?", (candidate.candidate_id,))
    with pytest.raises(sqlite3.IntegrityError, match="READ_ONLY_TABLE"):
        v2_db.connection.execute("INSERT INTO intake_state_transitions VALUES ('PARTIAL', 'READY')")
    with pytest.raises(sqlite3.IntegrityError, match="INTAKE_APPEND_ONLY"):
        v2_db.connection.execute("DELETE FROM intake_candidates")


def test_failure_states_need_a_cause(v2_db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="reason_code"):
        v2_db.connection.execute(
            "INSERT INTO intake_candidates(candidate_id, channel, display_name, state, created_at, updated_at) "
            "VALUES ('INT-x', 'USER_DROP', 'a.exe', 'UNSUPPORTED', ?, ?)",
            (NOW, NOW),
        )


def test_only_the_user_confirms_and_only_processing_processes(intake: LocalIntake) -> None:
    candidate = intake.classify(_drop(intake, TRANSCRIPT.encode(), "note.txt").candidate_id, now=NOW)
    assert candidate.state == "NEEDS_REVIEW"
    writer = intake.writer
    with pytest.raises(PolicyViolation):
        writer.transition_intake_state(Actor.INGESTER, candidate.candidate_id, "DISMISSED")
    with pytest.raises(PolicyViolation):
        writer.transition_intake_state(Actor.CORE_AGENT_OUTPUT, candidate.candidate_id, "DISMISSED")
    with pytest.raises(PolicyViolation):
        writer.update(Owner.CATALOG, Actor.UI_USER, "intake_candidates", {"candidate_id": candidate.candidate_id}, {"state": "CONFIRMED"})
    with pytest.raises(sqlite3.IntegrityError, match="material_id"):  # confirm() creates the material; a bare move is refused
        writer.transition_intake_state(Actor.UI_USER, candidate.candidate_id, "CONFIRMED")


# --- the local path end to end ------------------------------------------------------


def test_pdf_dropped_into_a_course_is_auto_confirmed_processed_and_searchable(intake: LocalIntake, v2_db: Database) -> None:
    pytest.importorskip("pypdf")
    pdf = make_pdf_bytes(["TCP handshake: SYN, SYN-ACK, ACK", "Flow control and windows"])
    candidate = _drop(intake, pdf, "Lec.3 TCP.pdf", mime_type="application/pdf", course_hint="NET")
    assert candidate.state == "RECEIVED"
    classified = intake.classify(candidate.candidate_id, now=NOW)
    assert classified.state == "AUTO_CONFIRMED", classified
    assert classified.material_id == "NET-M01"
    done = intake.process(candidate.candidate_id, now=NOW)
    assert (done.state, done.reason_code) == ("READY", None)
    status = v2_db.connection.execute("SELECT processing_status, kind FROM materials WHERE material_id = 'NET-M01'").fetchone()
    assert tuple(status) == ("READY", "LECTURE_SLIDES")
    package = _service(v2_db, intake).get_material_context("NET-M01", caller_scope="ui", query="handshake")
    assert [str(i.locator) for i in package.sources] == ["NET-M01:p1"]
    job = v2_db.connection.execute("SELECT status, target_entity_id FROM jobs").fetchone()
    assert tuple(job) == ("READY", "NET-M01")


def test_unclassified_transcript_waits_for_the_user_then_becomes_a_new_session(intake: LocalIntake, v2_db: Database) -> None:
    candidate = intake.classify(_drop(intake, TRANSCRIPT.encode(), "녹음 정리.txt").candidate_id, now=NOW)
    assert candidate.state == "NEEDS_REVIEW" and candidate.material_id is None
    assert candidate.suggestion is not None and "suggested_kind" in candidate.suggestion
    assert intake.run_pending(now=NOW) == []  # nothing is processed before the user confirms
    confirmed = intake.confirm(candidate.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-08", now=NOW)
    assert (confirmed.state, confirmed.material_id) == ("CONFIRMED", "NET-M01")
    session = v2_db.connection.execute("SELECT session_id, number, held_on FROM sessions").fetchone()
    assert tuple(session) == ("NET-S01", 1, "2026-09-08")
    [done] = intake.run_pending(now=NOW)
    assert done.state == "READY"
    package = _service(v2_db, intake).get_session_context("NET-S01", caller_scope="ui")
    assert package.sources and {i.entity_id for i in package.sources} == {"NET-M01"}
    assert all(str(i.locator).startswith("NET-M01:t") for i in package.sources)


def test_confirmation_is_validated_and_leaves_the_candidate_unchanged(intake: LocalIntake) -> None:
    candidate = intake.classify(_drop(intake, TRANSCRIPT.encode(), "a.txt").candidate_id, now=NOW)
    with pytest.raises(IntakeError):
        intake.confirm(candidate.candidate_id, course_key="NOPE", kind="TRANSCRIPT", now=NOW)
    with pytest.raises(IntakeError):
        intake.confirm(candidate.candidate_id, course_key="NET", kind="UNSUPPORTED", now=NOW)
    with pytest.raises(IntakeError):
        intake.confirm(candidate.candidate_id, course_key="NET", kind="TRANSCRIPT", session_id="NET-S99", now=NOW)
    assert intake.get(candidate.candidate_id).state == "NEEDS_REVIEW"


def test_ids_are_allocated_in_order(intake: LocalIntake) -> None:
    ids = []
    for n in range(3):
        c = intake.classify(_drop(intake, f"[00:00] lecture {n}\n".encode(), f"{n}.txt").candidate_id, now=NOW)
        ids.append(intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on=f"2026-09-0{n + 1}", now=NOW))
    assert [c.material_id for c in ids] == ["NET-M01", "NET-M02", "NET-M03"]


# --- duplicates, unsupported input, failures ------------------------------------------


def test_same_bytes_again_is_a_duplicate_that_can_be_dismissed(intake: LocalIntake) -> None:
    first = _drop(intake, TRANSCRIPT.encode(), "a.txt")
    again = _drop(intake, TRANSCRIPT.encode(), "copy of a.txt")
    assert (again.state, again.reason_code, again.duplicate_of) == ("DUPLICATE", "DUPLICATE_CONTENT", first.source_id)
    assert intake.dismiss(again.candidate_id, now=NOW).state == "DISMISSED"
    assert intake.get(first.candidate_id).state == "RECEIVED"


def test_unsupported_type_and_size_are_reported_and_not_stored(intake: LocalIntake, v2_db: Database) -> None:
    exe = _drop(intake, b"MZ\x90\x00", "setup.exe")
    assert (exe.state, exe.reason_code) == ("UNSUPPORTED", "UNSUPPORTED_TYPE")
    big = _drop(intake, b"x" * (64 * 1024 + 1), "big.txt")
    assert (big.state, big.reason_code) == ("UNSUPPORTED", "UNSUPPORTED_SIZE")
    assert v2_db.connection.execute("SELECT count(*) FROM source_files").fetchone()[0] == 0
    assert not [p for p in v2_db.layout.tmp_dir.rglob("*") if p.is_file()]


def test_textless_pdf_fails_with_its_cause_and_can_be_retried(intake: LocalIntake, v2_db: Database) -> None:
    pytest.importorskip("pypdf")
    candidate = _drop(intake, make_pdf_bytes([None, None]), "scan.pdf")
    assert intake.classify(candidate.candidate_id, now=NOW).state == "NEEDS_REVIEW"  # no course: the user decides
    intake.confirm(candidate.candidate_id, course_key="NET", kind="LECTURE_SLIDES", now=NOW)
    done = intake.process(candidate.candidate_id, now=NOW)
    assert done.state == "FAILED" and done.reason_code == "NO_EXTRACTABLE_TEXT"
    material = v2_db.connection.execute("SELECT processing_status FROM materials").fetchone()[0]
    assert material == "NEEDS_REVIEW"
    assert intake.retry(candidate.candidate_id, now=NOW).state == "QUEUED"


def test_partial_never_becomes_ready_without_reprocessing(intake: LocalIntake, v2_db: Database) -> None:
    c = intake.classify(_drop(intake, b"[00:00] a\n[99:99:99:99] broken\n", "p.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    assert intake.process(c.candidate_id, now=NOW).state == "PARTIAL"
    with pytest.raises(sqlite3.IntegrityError):
        v2_db.connection.execute("UPDATE intake_candidates SET state = 'READY' WHERE candidate_id = ?", (c.candidate_id,))
    assert intake.retry(c.candidate_id, now=NOW).state == "QUEUED"
    assert intake.process(c.candidate_id, now=NOW).state == "PARTIAL"  # same bytes, same verdict


def test_crash_during_processing_is_recovered(intake: LocalIntake) -> None:
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "r.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    intake.writer.transition_intake_state(Actor.INGESTER, c.candidate_id, "PROCESSING", now=NOW)  # then the process dies
    assert intake.recover(now=NOW) == 1
    assert intake.get(c.candidate_id).state == "QUEUED"
    [done] = intake.run_pending(now=NOW)
    assert done.state == "READY"


def test_processing_error_is_stored_with_a_code(intake: LocalIntake, monkeypatch: pytest.MonkeyPatch) -> None:
    from syllva.intake.v2 import pipeline

    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "e.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)

    def boom(*a: object, **k: object) -> object:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pipeline, "normalize_source_version", boom)
    done = intake.process(c.candidate_id, now=NOW)
    assert done.state == "FAILED" and done.reason_code == "PROCESSING_ERROR" and "RuntimeError" in (done.reason_message or "")


def test_receive_is_atomic(intake: LocalIntake, v2_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    original = intake.writer.insert

    def failing(owner, actor, table, values):  # type: ignore[no-untyped-def]
        if table == "intake_candidates":
            raise RuntimeError("crash before the candidate row")
        return original(owner, actor, table, values)

    monkeypatch.setattr(intake.writer, "insert", failing)
    with pytest.raises(RuntimeError):
        _drop(intake, TRANSCRIPT.encode(), "a.txt")
    monkeypatch.undo()
    assert v2_db.connection.execute("SELECT count(*) FROM source_files").fetchone()[0] == 0
    assert _drop(intake, TRANSCRIPT.encode(), "a.txt").state == "RECEIVED"  # not a DUPLICATE of an orphan


# --- review of PR #46 ------------------------------------------------------------------


def test_a_course_that_cannot_form_ids_sends_the_file_to_review_and_the_worker_goes_on(intake: LocalIntake, v2_db: Database) -> None:
    pytest.importorskip("pypdf")
    v2_db.connection.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('2026-2_cs-101', 'x', NULL, '2026-2', ?)", (NOW,)
    )
    bad = _drop(intake, make_pdf_bytes(["Lec text"]), "Lec.1 intro.pdf", course_hint="2026-2_cs-101", mime_type="application/pdf")
    good = _drop(intake, TRANSCRIPT.encode(), "b.txt")
    intake.run_pending(now=NOW)
    first = intake.get(bad.candidate_id)
    assert first.state == "NEEDS_REVIEW" and (first.suggestion or {})["blockers"] == ["COURSE_CODE_INVALID"]
    assert intake.get(good.candidate_id).state == "NEEDS_REVIEW"


def test_an_unreadable_blob_goes_to_review_with_a_cause(intake: LocalIntake) -> None:
    candidate = _drop(intake, TRANSCRIPT.encode(), "gone.txt")
    sha = intake.writer.query_one(
        "SELECT v.blob_sha256 FROM intake_candidates c JOIN source_versions v ON v.id = c.source_version_id WHERE c.candidate_id = ?",
        (candidate.candidate_id,),
    )["blob_sha256"]  # type: ignore[index]
    path = intake.blobs.path_for(str(sha))
    path.chmod(0o600)
    path.unlink()
    result = intake.classify(candidate.candidate_id, now=NOW)
    assert result.state == "NEEDS_REVIEW" and (result.suggestion or {})["blockers"] == ["CLASSIFY_ERROR"]


def test_unknown_course_hint_is_said(intake: LocalIntake) -> None:
    result = intake.classify(_drop(intake, TRANSCRIPT.encode(), "h.txt", course_hint="NOPE").candidate_id, now=NOW)
    assert "COURSE_HINT_UNKNOWN" in (result.suggestion or {})["blockers"]


def test_dismissed_bytes_can_be_dropped_again(intake: LocalIntake) -> None:
    first = intake.classify(_drop(intake, TRANSCRIPT.encode(), "a.txt").candidate_id, now=NOW)
    intake.dismiss(first.candidate_id, now=NOW)
    again = _drop(intake, TRANSCRIPT.encode(), "a.txt")
    assert again.state == "RECEIVED" and again.source_id == first.source_id
    third = _drop(intake, TRANSCRIPT.encode(), "a again.txt")
    assert third.state == "DUPLICATE"  # one live candidate per stored version


def test_kind_must_fit_the_file(intake: LocalIntake) -> None:
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "k.txt").candidate_id, now=NOW)
    with pytest.raises(IntakeError):
        intake.confirm(c.candidate_id, course_key="NET", kind="RECORDING", now=NOW)
    assert intake.get(c.candidate_id).state == "NEEDS_REVIEW"


def test_crash_during_classification_is_recovered(intake: LocalIntake) -> None:
    c = _drop(intake, TRANSCRIPT.encode(), "c.txt")
    intake.writer.transition_intake_state(Actor.INGESTER, c.candidate_id, "CLASSIFYING", now=NOW)  # then the process dies
    assert intake.recover(now=NOW) == 1 and intake.get(c.candidate_id).state == "RECEIVED"
    assert intake.classify(c.candidate_id, now=NOW).state == "NEEDS_REVIEW"


def test_user_cannot_drive_processing(intake: LocalIntake) -> None:
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "u.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    with pytest.raises(PolicyViolation):
        intake.writer.transition_intake_state(Actor.UI_USER, c.candidate_id, "PROCESSING")


@pytest.mark.parametrize("verb", ["UPDATE OR REPLACE", "UPDATE OR IGNORE"])
def test_conflict_clauses_do_not_bypass_the_state_machine(intake: LocalIntake, v2_db: Database, verb: str) -> None:
    """Errata E7 regression class: conflict clauses must not skip the trigger."""

    c = _drop(intake, TRANSCRIPT.encode(), "r.txt")
    with pytest.raises(sqlite3.IntegrityError, match="INTAKE_TRANSITION"):
        v2_db.connection.execute(f"{verb} intake_candidates SET state = 'READY' WHERE candidate_id = ?", (c.candidate_id,))
    with pytest.raises(sqlite3.IntegrityError):
        v2_db.connection.execute(
            "INSERT OR REPLACE INTO intake_candidates(candidate_id, channel, display_name, state, source_version_id, "
            "created_at, updated_at) SELECT candidate_id, channel, display_name, 'READY', source_version_id, created_at, "
            "updated_at FROM intake_candidates WHERE candidate_id = ?",
            (c.candidate_id,),
        )
    assert intake.get(c.candidate_id).state == "RECEIVED"


def test_exhausted_id_space_on_the_automatic_path_goes_to_review(intake: LocalIntake, v2_db: Database) -> None:
    pytest.importorskip("pypdf")
    conn = v2_db.connection
    for n in range(1, 100):
        conn.execute("INSERT INTO source_files(source_id, provider, display_name, origin, created_at) VALUES (?, 'local', 'f', 'USER_DROP', ?)",
                     (f"SRC-pad{n}", NOW))
        conn.execute("INSERT INTO materials(material_id, course_id, source_file_id, kind, role, created_at) "
                     "VALUES (?, 1, last_insert_rowid(), 'LECTURE_SLIDES', 'PRIMARY', ?)", (f"NET-M{n:02d}", NOW))
    bad = _drop(intake, make_pdf_bytes(["Lec text"]), "Lec.9 full.pdf", course_hint="NET", mime_type="application/pdf")
    good = _drop(intake, TRANSCRIPT.encode(), "b.txt")
    intake.run_pending(now=NOW)
    first = intake.get(bad.candidate_id)
    assert first.state == "NEEDS_REVIEW" and (first.suggestion or {})["blockers"] == ["ID_SPACE_EXHAUSTED"]
    assert first.material_id is None
    assert intake.get(good.candidate_id).state == "NEEDS_REVIEW"


def test_any_refusal_during_automatic_confirmation_rolls_back_to_review(intake: LocalIntake, v2_db: Database,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural guarantee: after CLASSIFYING the candidate always ends in AUTO_CONFIRMED or NEEDS_REVIEW."""

    pytest.importorskip("pypdf")
    original = intake._create_material

    def half_then_refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        original(*args, **kwargs)  # the material row is written ...
        raise IntakeError("some future allocation rule says no")  # ... then refused

    monkeypatch.setattr(intake, "_create_material", half_then_refuse)
    c = intake.classify(_drop(intake, make_pdf_bytes(["Lec text"]), "Lec.2 x.pdf", course_hint="NET", mime_type="application/pdf").candidate_id, now=NOW)
    assert c.state == "NEEDS_REVIEW" and (c.suggestion or {})["blockers"] == ["INTAKE_INVALID"]
    assert v2_db.connection.execute("SELECT count(*) FROM materials").fetchone()[0] == 0  # rolled back


def test_steps_refuse_to_run_inside_a_callers_transaction(intake: LocalIntake) -> None:
    c = _drop(intake, TRANSCRIPT.encode(), "t.txt")
    with intake.writer.transaction(), pytest.raises(RuntimeError, match="own their transactions"):
        intake.classify(c.candidate_id, now=NOW)
    assert intake.get(c.candidate_id).state == "RECEIVED"


def test_intake_steps_wait_for_another_threads_transaction(intake: LocalIntake, v2_db: Database) -> None:
    """The nesting guard counts only the calling thread's own transaction (review of PR #50 class)."""

    import threading

    c = _drop(intake, TRANSCRIPT.encode(), "t.txt")
    held, release, result = threading.Event(), threading.Event(), {}

    def hold() -> None:
        with intake.writer.transaction():
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    worker = threading.Thread(target=lambda: result.update(state=intake.classify(c.candidate_id, now=NOW).state))
    worker.start()
    worker.join(0.3)
    assert worker.is_alive()
    release.set()
    holder.join()
    worker.join(5)
    assert result["state"] == "NEEDS_REVIEW"


def test_two_threads_never_race_one_candidate(intake: LocalIntake) -> None:
    """Single active worker (spec §11.2): steps are serialized whatever thread calls them (review of PR #51)."""

    import threading

    c = _drop(intake, TRANSCRIPT.encode(), "race.txt")
    barrier, errors, states = threading.Barrier(4), [], []

    def go() -> None:
        barrier.wait()
        try:
            states.append(intake.classify(c.candidate_id, now=NOW).state)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and set(states) == {"NEEDS_REVIEW"}


def test_material_deleted_before_processing_is_not_processed(intake: LocalIntake, v2_db: Database) -> None:
    c = intake.classify(_drop(intake, TRANSCRIPT.encode(), "d.txt").candidate_id, now=NOW)
    intake.confirm(c.candidate_id, course_key="NET", kind="TRANSCRIPT", new_session_held_on="2026-09-01", now=NOW)
    v2_db.connection.execute("UPDATE source_files SET deleted_at = ?", (NOW,))
    done = intake.process(c.candidate_id, now=NOW)
    assert (done.state, done.reason_code) == ("FAILED", "SOURCE_DELETED")
    assert v2_db.connection.execute("SELECT count(*) FROM derivatives").fetchone()[0] == 0
