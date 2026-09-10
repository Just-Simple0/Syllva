from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_notion import FakeNotionReader
from phase4 import COURSE_KEY, COURSE_PAGE_ID, exam_record

from uls.adapters.drive.binding import SourceBindingRecord, ValidatedSourceBindingResolver
from uls.config.schema import UlsConfig
from uls.domain.academic import ExamRecord
from uls.domain.errors import LocatorNotAllowedError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.normalization.transcript import normalize_transcript
from uls.retrieval.engine import RetrievalEngine


def _session(session_id: str, number: int) -> dict[str, object]:
    return {
        "ID": session_id,
        "Name": session_id,
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Session No": number,
        "Normalized Transcript": f"transcript-{number:02d}",
    }


def _transcript(session_id: str, source_file: str, text: str):
    return normalize_transcript(
        f"[00:00:01] {text}",
        entity_id=session_id,
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", source_file),
        source_hash=f"hash-{session_id}",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )


def _engine(*, included_sessions=None, confirmed=True, omit_included=False):
    sessions = [_session(f"COMP319-S{number:02d}", number) for number in (1, 2, 3)]
    exam = exam_record(
        included_sessions=None if omit_included else (included_sessions or []),
        scope_confirmed=confirmed,
    )
    if omit_included:
        exam.pop("Included Sessions", None)
    reader = FakeNotionReader(sessions=sessions, exams=[exam], material_usage={})
    derived = {}
    fingerprints = {}
    bindings = []
    for number in (1, 2, 3):
        session_id = f"COMP319-S{number:02d}"
        source_file = f"source-{number:02d}"
        pointer = f"transcript-{number:02d}"
        derived[f"transcript-{number:02d}"] = _transcript(
            session_id, source_file, f"Evidence for {session_id}"
        )
        fingerprints[source_file] = SourceFingerprint(1, f"hash-{session_id}")
        bindings.append(
            SourceBindingRecord(
                session_id,
                pointer,
                SourceRef("google_drive", f"transcript-{number:02d}"),
                SourceRef("google_drive", source_file),
            )
        )
    drive = FakeDriveReader(
        derived=derived,
        fingerprints=fingerprints,
        bindings=bindings,
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=ValidatedSourceBindingResolver(drive),
    )
    return engine, reader, drive


def test_exam_context_includes_only_current_listed_sessions_and_authorizes_their_chunks() -> None:
    engine, _, _ = _engine(included_sessions=["COMP319-S01", "COMP319-S02"])

    package = engine.get_exam_context("COMP319-E01", caller_scope="study")
    ids = {item.entity_id for item in package.sources}

    assert ids == {"COMP319-S01", "COMP319-S02"}
    assert "COMP319-S03" not in ids
    assert package.scope["status"] == "confirmed"
    assert package.scope["hard_boundary"] is True
    locator = str(package.sources[0].locator)
    chunk = engine.get_source_chunk(package.context_id, locator, caller_scope="study")
    assert chunk.entity_id in {"COMP319-S01", "COMP319-S02"}
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, "COMP319-S03:t00:00:01", caller_scope="study")


def test_unconfirmed_scope_is_provisional_but_current_listed_evidence_remains_verified_only() -> None:
    engine, _, _ = _engine(included_sessions=["COMP319-S01"], confirmed=False)

    package = engine.get_exam_context("COMP319-E01")

    assert package.scope["status"] == "provisional"
    assert package.scope["hard_boundary"] is False
    assert package.scope["included_sessions"] == ["COMP319-S01"]
    assert any(item.get("code") == "EXAM_SCOPE_UNCONFIRMED" for item in package.warnings)
    assert all(not getattr(item, "provisional", False) for item in package.sources)


def test_absent_and_explicit_empty_exam_relations_are_distinct() -> None:
    empty_engine, _, _ = _engine(included_sessions=[])
    absent_engine, _, _ = _engine(omit_included=True)

    empty = empty_engine.get_exam_context("COMP319-E01")
    absent = absent_engine.get_exam_context("COMP319-E01")

    assert empty.scope["included_sessions"] == []
    assert absent.scope["included_sessions"] is None
    assert empty.scope["status"] == "confirmed"
    assert empty.scope["hard_boundary"] is True
    assert not empty.sources
    assert not absent.sources
    assert any(item.get("code") == "EXAM_SCOPE_EMPTY" for item in empty.warnings)
    assert any(item.get("code") == "EXAM_SCOPE_EMPTY" for item in absent.warnings)


def test_malformed_exam_course_fails_closed() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    reader.courses[COURSE_PAGE_ID]["Section"] = "not-the-course"

    with pytest.raises(SourceUnavailableError):
        engine.get_exam_context("COMP319-E01")


def test_typed_exam_record_must_match_requested_logical_id() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    course = engine._course_by_relation_id(COURSE_PAGE_ID, "COMP319-E01")
    typed = ExamRecord("COMP319-E99", course, ("COMP319-S01",), True)
    reader.get_exam = lambda _exam_id: typed

    with pytest.raises(SourceUnavailableError, match="does not match"):
        engine.get_exam_context("COMP319-E01")


def test_typed_exam_record_rechecks_the_current_course_identity() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    course = engine._course_by_relation_id(COURSE_PAGE_ID, "COMP319-E01")
    typed = ExamRecord("COMP319-E01", course, ("COMP319-S01",), True)
    reader.get_exam = lambda _exam_id: typed
    reader.courses[COURSE_PAGE_ID]["Course Key"] = "2026-1_COMP319-003"
    reader.courses[COURSE_PAGE_ID]["Section"] = "003"

    with pytest.raises(SourceUnavailableError, match="stale"):
        engine.get_exam_context("COMP319-E01")


def test_exam_scope_accepts_the_documented_typed_relation_property_shape() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    reader.exams["COMP319-E01"]["Included Sessions"] = {
        "id": "included-sessions-property",
        "type": "relation",
        "relation": [{"id": "COMP319-S01"}],
        "has_more": False,
    }

    package = engine.get_exam_context("COMP319-E01")

    assert package.scope["included_sessions"] == ["COMP319-S01"]


def test_exam_course_accepts_the_documented_typed_relation_property_shape() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    reader.exams["COMP319-E01"]["Course"] = {
        "id": "course-property",
        "type": "relation",
        "relation": [{"id": COURSE_PAGE_ID}],
        "has_more": False,
    }

    package = engine.get_exam_context("COMP319-E01")

    assert package.scope["hard_boundary"] is True


@pytest.mark.parametrize(
    "course_relation",
    [
        {"not_a_relation": True},
        {"relation": []},
        {"relation": [{"id": COURSE_PAGE_ID}, {"id": "course-page-2"}]},
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": COURSE_PAGE_ID}],
            "has_more": True,
        },
        {
            "id": "course-property",
            "type": "checkbox",
            "relation": [{"id": COURSE_PAGE_ID}],
            "has_more": False,
        },
        {
            "id": 42,
            "type": "relation",
            "relation": [{"id": COURSE_PAGE_ID}],
            "has_more": False,
        },
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": COURSE_PAGE_ID, "name": "unexpected"}],
            "has_more": False,
        },
    ],
)
def test_exam_course_rejects_malformed_or_truncated_relation_properties(course_relation) -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    reader.exams["COMP319-E01"]["Course"] = course_relation

    with pytest.raises(SourceUnavailableError, match="Course relation"):
        engine.get_exam_context("COMP319-E01")


@pytest.mark.parametrize(
    "relation",
    [
        {"not_a_relation": True},
        {"relation": [{"id": "COMP319-S01"}, {"id": "COMP319-S01"}]},
        {"relation": [{"id": "COMP319-M03"}]},
        {
            "id": "included-sessions-property",
            "type": "relation",
            "relation": [{"id": "COMP319-S01"}],
            "has_more": True,
        },
    ],
)
def test_exam_scope_rejects_malformed_or_incomplete_relation_properties(relation) -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    reader.exams["COMP319-E01"]["Included Sessions"] = relation

    with pytest.raises(SourceUnavailableError):
        engine.get_exam_context("COMP319-E01")


def test_exam_parent_uses_one_aggregate_budget_and_one_capability() -> None:
    engine, _, _ = _engine(included_sessions=["COMP319-S01", "COMP319-S02"])
    budget_calls: list[tuple[int, int]] = []
    issue_calls: list[int] = []
    real_budget = engine._budget_evidence_bindings
    real_issue = engine.capabilities.issue

    def budget(evidence, bindings):
        budget_calls.append((len(evidence), len(bindings)))
        return real_budget(evidence, bindings)

    def issue(bindings, *, caller_scope=None):
        issue_calls.append(len(bindings))
        return real_issue(bindings, caller_scope=caller_scope)

    engine._budget_evidence_bindings = budget
    engine.capabilities.issue = issue

    package = engine.get_exam_context("COMP319-E01")

    assert package.sources
    assert len(budget_calls) == 1
    assert len(issue_calls) == 1


@pytest.mark.parametrize(
    "new_sessions",
    [["COMP319-S01"], ["COMP319-S01", "COMP319-S03"]],
)
def test_parent_scope_subset_or_expansion_revokes_existing_locator_capability(new_sessions) -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01", "COMP319-S02"])
    package = engine.get_exam_context("COMP319-E01")
    locator = str(package.sources[0].locator)
    reader.exams["COMP319-E01"]["Included Sessions"] = {
        "relation": [{"id": value} for value in new_sessions]
    }

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, locator)


def test_parent_confirmation_change_revokes_existing_locator_capability() -> None:
    engine, reader, _ = _engine(included_sessions=["COMP319-S01"])
    package = engine.get_exam_context("COMP319-E01")
    locator = str(package.sources[0].locator)
    reader.exams["COMP319-E01"]["Scope Confirmed"] = False

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, locator)
