"""P-B1: the pure S0/S1/S3 outcome for Drive uploads against the observed fixture."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from uls.intake.classification import (
    CourseAliasIndex,
    HandlingMode,
    Kind,
    MatchStatus,
    Origin,
    RecordingEntry,
    SemesterRange,
    build_calendar,
)
from uls.intake.classification.pipeline import (
    NOTE_AUTO_UNAVAILABLE,
    NOTE_CALENDAR_MISMATCH,
    NOTE_CLASSIFIER_DISABLED,
    NOTE_COURSE_UNRESOLVED,
    NOTE_FORMAT_KIND_MISMATCH,
    NOTE_NO_CALENDAR,
    NOTE_NO_SINGLE_RULE,
    NOTE_SOURCE_TOO_LARGE,
    NOTE_SOURCE_UNAVAILABLE,
    NOTE_UNSUPPORTED,
    SourceProbe,
    classify_upload_item,
)

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parents[2] / "docs/plans/intake-classification-fixtures-20261009.json"
DRIVE_ITEMS = [i for i in json.loads(FIXTURE.read_text(encoding="utf-8"))["items"] if i["origin"] == "drive_upload"]
SEMESTER = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
INDEX = CourseAliasIndex.build([
    ("2026-2_LMS67535-001", ["알고리즘2", "LMS67535"]),
    ("2026-2_LMS66469-001", ["네트워크프로그래밍", "LMS66469"]),
    ("2026-2_LMS66290-002", ["데이타베이스", "LMS66290"]),
])
TEXT = SourceProbe(b"# [00:10] transcript line\n", True)


def _classify(name: str, **overrides):
    kwargs = {
        "name": name, "mime_type": "text/markdown", "from_upload_folder": True, "explicit_course_key": None,
        "origin": None, "alias_index": INDEX, "semester": SEMESTER, "calendar": None, "probe": TEXT,
    }
    kwargs.update(overrides)
    return classify_upload_item(**kwargs)


@pytest.mark.parametrize("item", DRIVE_ITEMS, ids=[i["title"] for i in DRIVE_ITEMS])
def test_every_observed_transcript_is_decided_but_falls_back_on_the_calendar(item: dict[str, object]) -> None:
    outcome = _classify(str(item["title"]), mime_type=str(item["mime_type"]))
    assert outcome.kind is Kind(str(item["expected_kind"])) and outcome.rule_id == item["expected_decision"]
    assert outcome.origin is Origin(str(item["expected_origin"]))
    assert outcome.course_key == item["expected_course_key"] and outcome.course_basis == "config_alias"
    assert outcome.week == item["expected_week"]
    assert outcome.recorded_date == date.fromisoformat(str(item["filename_date"]))
    assert outcome.calendar is not None and outcome.calendar.status is MatchStatus(str(item["expected_calendar_match"]))
    assert outcome.handling is HandlingMode.NORMALIZE
    assert item["expected_fallback_reason"] in outcome.notes and NOTE_AUTO_UNAVAILABLE in outcome.notes
    assert outcome.needs_human and outcome.suggestion_source == "rule:P3:transcript_filename"
    fields = outcome.suggestion_fields()
    assert fields["suggested_kind"] == "TRANSCRIPT" and fields["suggested_week"] == item["expected_week"]
    assert fields["suggested_course_key"] == item["expected_course_key"]
    assert fields["suggested_date"] == item["filename_date"]
    assert NOTE_CALENDAR_MISMATCH in str(fields["suggestion_note"])


def test_undecided_items_go_to_s3_with_the_classifier_disabled_note() -> None:
    outcome = _classify("lecture.md")
    assert outcome.kind is None and outcome.decision_type is None and outcome.handling is None
    assert outcome.notes == (NOTE_NO_SINGLE_RULE, NOTE_CLASSIFIER_DISABLED, NOTE_AUTO_UNAVAILABLE)
    assert outcome.suggestion_source is None and outcome.origin is Origin.UNKNOWN
    assert outcome.suggestion_fields()["suggested_kind"] is None
    # With S2 available the disabled note disappears but the item is still undecided here.
    assert NOTE_CLASSIFIER_DISABLED not in _classify("lecture.md", s2_available=True).notes


def test_explicit_upload_folder_course_wins_and_calendar_states_map_to_notes() -> None:
    name = "2026.09.10_알고리즘2_2주차.md"
    explicit = _classify(name, explicit_course_key="2026-2_LMS66469-001")
    assert (explicit.course_key, explicit.course_basis) == ("2026-2_LMS66469-001", "upload_folder")
    assert explicit.calendar is not None and explicit.calendar.status is MatchStatus.NO_CALENDAR
    assert NOTE_NO_CALENDAR in explicit.notes
    entries = [RecordingEntry(67535, f"r{w}", 1, w, date(2026, 9, 3) + __import__("datetime").timedelta(days=7 * (w - 1)))
               for w in (1, 2, 3)]
    calendar = build_calendar("2026-2_LMS67535-001", entries, semester=SEMESTER, collection_complete=True)
    matched = _classify(name, calendar=calendar)
    assert matched.calendar is not None and matched.calendar.status is MatchStatus.MATCHED and matched.week == 2
    assert NOTE_CALENDAR_MISMATCH not in matched.notes and NOTE_NO_CALENDAR not in matched.notes
    wrong_week = _classify("2026.09.10_알고리즘2_3주차.md", calendar=calendar)
    assert wrong_week.calendar is not None and wrong_week.calendar.reason == "WEEK_MISMATCH"
    assert NOTE_CALENDAR_MISMATCH in wrong_week.notes
    unknown_course = _classify("2026.09.10_미지과목_2주차.md")
    assert unknown_course.kind is Kind.TRANSCRIPT and unknown_course.course_key is None
    assert NOTE_COURSE_UNRESOLVED in unknown_course.notes and unknown_course.calendar is None


def test_probe_outcomes_drive_the_handling_mode() -> None:
    name = "2026.09.10_알고리즘2_2주차.md"
    assert _classify(name, probe=None).handling is HandlingMode.S3
    too_large = _classify(name, probe=SourceProbe(None, False, too_large=True))
    assert too_large.handling is HandlingMode.S3 and NOTE_SOURCE_TOO_LARGE in too_large.notes
    unavailable = _classify(name, probe=SourceProbe(None, False, unavailable=True))
    assert unavailable.handling is HandlingMode.S3 and NOTE_SOURCE_UNAVAILABLE in unavailable.notes
    disguised = _classify(name, probe=SourceProbe(b"%PDF-1.7 not a transcript", True))
    assert disguised.handling is HandlingMode.S3 and NOTE_FORMAT_KIND_MISMATCH in disguised.notes
    assert TEXT.byte_sha256 is not None and len(TEXT.byte_sha256) == 64 and len(TEXT.byte_md5 or "") == 32
    assert SourceProbe(b"x", False).byte_sha256 is None  # an incomplete payload proves nothing


def test_verified_canvas_attachment_signal_reaches_p4() -> None:
    csv = SourceProbe(b"name,score\nA,1\n", True)
    plain = _classify("mbti.csv", mime_type="text/csv", probe=csv)
    assert plain.kind is None and plain.origin is Origin.UNKNOWN
    bound = _classify("mbti.csv", mime_type="text/csv", probe=csv, origin=Origin.PROFESSOR_SOURCE,
                      canvas_attachment_of="assignment")
    assert bound.kind is Kind.ASSIGNMENT_RESOURCE and bound.rule_id == "P4:tabular_assignment_attachment"
    assert bound.origin is Origin.PROFESSOR_SOURCE and bound.handling is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    # An announcement attachment is not an assignment signal.
    announced = _classify("mbti.csv", mime_type="text/csv", probe=csv, origin=Origin.PROFESSOR_SOURCE,
                          canvas_attachment_of="announcement")
    assert announced.kind is None and announced.origin is Origin.PROFESSOR_SOURCE


def test_forbidden_formats_are_terminal_without_a_probe() -> None:
    outcome = _classify("VMware_installer.exe", mime_type="application/x-msdownload", probe=None)
    assert outcome.kind is Kind.UNSUPPORTED and NOTE_UNSUPPORTED in outcome.notes
    assert outcome.handling is HandlingMode.S3 and outcome.course_key is None
    assert NOTE_COURSE_UNRESOLVED not in outcome.notes
