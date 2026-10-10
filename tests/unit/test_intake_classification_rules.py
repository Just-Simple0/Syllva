"""P-A: deterministic rules, taxonomy matrix and recording calendar against the observed fixture."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from uls.intake.classification import (
    FILE_KINDS_V2,
    MATERIAL_TYPE_INITIAL,
    CourseAliasIndex,
    EntryStatus,
    HandlingMode,
    ItemSignals,
    Kind,
    MatchStatus,
    Origin,
    RecordingEntry,
    SemesterRange,
    build_calendar,
    classify_by_rules,
    course_aliases_from_config,
    date_from_text,
    handling_mode,
    match_transcript,
    transcript_signals,
    week_from_filename,
    week_from_module_name,
)
from uls.intake.classification.rules import (
    CANVAS_ANNOUNCEMENT,
    CANVAS_ASSIGNMENT,
    CANVAS_MODULE_ITEM,
)
from uls.intake.classification.taxonomy import XLSX_MAX_ENTRY_BYTES

pytestmark = pytest.mark.unit

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FIXTURE = Path(__file__).resolve().parents[2] / "docs/plans/intake-classification-fixtures-20261009.json"
_FIXTURE_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
ITEMS = _FIXTURE_DOC["items"]
FIXTURE_VERSION = _FIXTURE_DOC["rule_table_version"]


def _signals(item: dict[str, object]) -> ItemSignals:
    origin = item["origin"]
    mime = item.get("mime_type")
    if origin == "canvas_module":
        return ItemSignals(str(item["title"]), canvas_resource_kind=CANVAS_MODULE_ITEM,
                           canvas_item_type=str(item.get("item_type") or ""),
                           origin=Origin.PROFESSOR_SOURCE)
    if origin == "canvas_assignment":
        return ItemSignals(str(item["title"]), canvas_resource_kind=CANVAS_ASSIGNMENT,
                           origin=Origin.PROFESSOR_SOURCE)
    if origin == "canvas_announcement":
        return ItemSignals(str(item["title"]), canvas_resource_kind=CANVAS_ANNOUNCEMENT,
                           origin=Origin.PROFESSOR_SOURCE)
    return ItemSignals(str(item["title"]), mime_type=None if mime is None else str(mime),
                       from_upload_folder=True, origin=Origin.USER_AUTHORED_TRANSCRIPT)


@pytest.mark.parametrize("item", ITEMS, ids=[f"{i['origin']}:{i['title']}" for i in ITEMS])
def test_every_observed_item_matches_the_fixture_expectation(item: dict[str, object]) -> None:
    """Exact (kind, rule_id, version) comparison per observed item (P-A review O2)."""

    decision = classify_by_rules(_signals(item))
    expected_kind = item["expected_kind"]
    assert decision.rule_id == item["expected_decision"]
    assert decision.rule_table_version == FIXTURE_VERSION == "icv2-r14"
    if expected_kind is None:
        assert decision.kind is None and item["expected_classification_stage"] == "S2"
        assert decision.rule_id == "S2:no_single_rule"
    else:
        assert decision.kind is Kind(str(expected_kind)), decision
        assert item["expected_classification_stage"] == "S1"
        assert decision.priority == str(item["expected_decision"]).split(":")[0]


def test_fixture_counts_match_the_plan() -> None:
    assert len(ITEMS) == 80
    assert sum(1 for i in ITEMS if i["expected_final_intake_state"] == "CANVAS_METADATA_ONLY") == 72
    assert sum(1 for i in ITEMS if i["expected_final_intake_state"] == "NEEDS_INPUT") == 8


def test_priority_levels_stop_at_the_first_decisive_level() -> None:
    # P2 course info beats the P3 lecture/lab collision in the same title.
    d = classify_by_rules(ItemSignals("Lec.00_강의소개및실습안내", canvas_resource_kind=CANVAS_MODULE_ITEM))
    assert d.kind is Kind.COURSE_INFO and d.priority == "P2"
    # P0 wins over everything, including an Assignment item type.
    # English "installer" guides are ordinary setup documents, not P0 (r15 R2).
    for name in ("VMware installer guide.pdf", "installer_notes.pdf", "uninstaller_guide.pdf"):
        decision = classify_by_rules(ItemSignals(name))
        assert decision.kind is Kind.SETUP_GUIDE and decision.priority == "P3", name
    assert classify_by_rules(ItemSignals("VMware 설치 가이드.pdf")).kind is Kind.SETUP_GUIDE
    assert classify_by_rules(ItemSignals("VMware_Workstation_installer.exe")).rule_id == "P0:forbidden_extension"
    d = classify_by_rules(ItemSignals("Windows용 VMware Workstation 설치 프로그램",
                                      canvas_item_type="Assignment"))
    assert d.terminal_unsupported
    # Two P3 hits defer to S2 with both candidates recorded and no probability.
    d = classify_by_rules(ItemSignals("week3_lab Appendix"))
    assert d.kind is None and set(d.candidates) == {Kind.LAB_MATERIAL, Kind.SUPPLEMENT}
    # Assignment attachment titled "실습과제" is ambiguous and never the generic lab rule.
    d = classify_by_rules(ItemSignals("실습과제 01.pdf", canvas_attachment_of=CANVAS_ASSIGNMENT))
    assert d.kind is None
    # Plain .csv without assignment provenance stays undecided (r3 O1).
    assert classify_by_rules(ItemSignals("data.csv")).kind is None
    assert classify_by_rules(ItemSignals("data.csv", canvas_attachment_of=CANVAS_ASSIGNMENT)).kind \
        is Kind.ASSIGNMENT_RESOURCE
    assert classify_by_rules(ItemSignals("과제 data.csv")).rule_id == "P4:tabular_title_signal"
    # P1 never applies to attachments (they are Drive files, not Canvas resources) and
    # the date-only rule needs an ExternalTool item with a real calendar date (P-A R1).
    assert classify_by_rules(ItemSignals("과제1.pdf", canvas_resource_kind=CANVAS_ASSIGNMENT,
                                         canvas_attachment_of=CANVAS_ASSIGNMENT)).kind is None
    assert classify_by_rules(ItemSignals("2026-09-03", canvas_resource_kind=CANVAS_MODULE_ITEM,
                                         canvas_item_type="ExternalTool")).rule_id == "P1:date_only_title"
    assert classify_by_rules(ItemSignals("2026-02-30", canvas_resource_kind=CANVAS_MODULE_ITEM,
                                         canvas_item_type="ExternalTool")).kind is None
    assert classify_by_rules(ItemSignals("2026-09-03", canvas_resource_kind=CANVAS_MODULE_ITEM,
                                         canvas_item_type="Page")).kind is None
    # The exact P4 extension list: nothing outside it (P-A R2).
    assert classify_by_rules(ItemSignals("main.rs")).kind is None
    assert classify_by_rules(ItemSignals("main.c")).rule_id == "P4:code_extension"
    assert classify_by_rules(ItemSignals("notes.ipynb")).kind is Kind.PROVIDED_CODE
    # A transcript-named PDF is not the transcript rule (needs a text MIME or none).
    assert classify_by_rules(ItemSignals("2029.09.17_알고리즘2_2주차.md", mime_type="application/pdf",
                                         from_upload_folder=True)).kind is None


def test_transcript_filename_rule_and_double_md_extension() -> None:
    index = CourseAliasIndex.build([
        ("2026-2_LMS67535-001", course_aliases_from_config("2026-2_LMS67535-001", "알고리즘2 (001)", "LMS67535")),
        ("2026-2_LMS66469-001", course_aliases_from_config("2026-2_LMS66469-001", "네트워크프로그래밍 (001)", "LMS66469")),
    ])
    for name in ("2029.09.17_알고리즘2_2주차.md", "2029.09.21_네트워크프로그래밍_3주차.md.md"):
        assert classify_by_rules(ItemSignals(name, mime_type="text/markdown", from_upload_folder=True)).kind \
            is Kind.TRANSCRIPT
        # MIME unknown or mismatched is not evidence: the item stays for S2/S3 (P-A r2 #1).
        assert classify_by_rules(ItemSignals(name, from_upload_folder=True)).kind is None
        assert classify_by_rules(ItemSignals(name, mime_type="application/octet-stream",
                                             from_upload_folder=True)).kind is None
        # text/plain is processable (§6.1) but never decides in S1 (§3.1 P3; r14 R3).
        assert classify_by_rules(ItemSignals(name, mime_type="text/plain", from_upload_folder=True)).kind is None
        signals = transcript_signals(name, index)
        assert signals["course_key"] in {"2026-2_LMS67535-001", "2026-2_LMS66469-001"}
        assert signals["date"] == date(2029, 9, 17) or signals["date"] == date(2029, 9, 21)
    # Not from the upload folder → the filename rule does not apply.
    assert classify_by_rules(ItemSignals("2029.09.17_알고리즘2_2주차.md", mime_type="text/markdown")).kind is None
    assert transcript_signals("lecture.md", index) == {}


def test_duplicate_aliases_are_disabled_across_courses() -> None:
    index = CourseAliasIndex.build([("A", ["캡스톤", "ALPHA"]), ("B", ["캡스톤", "BETA"])])
    assert index.resolve("캡스톤") is None and "캡스톤" in index.disabled
    assert index.resolve("alpha") == "A" and index.resolve(" Beta ") == "B"


def test_week_only_from_explicit_notation() -> None:
    assert week_from_module_name("3주차") == 3
    assert week_from_module_name("Week 3") is None and week_from_module_name(None) is None
    assert week_from_filename("2029.09.17_알고리즘2_12주차.md") == 12
    assert week_from_filename("lecture.md") is None
    # Conflicting explicit values never pick the first one (r7 R4); repeats are fine.
    assert week_from_filename("slides_2주차_3주차.pdf") is None
    assert week_from_filename("slides_2주차_2주차.pdf") == 2
    assert date_from_text("2026-09-03_2026-09-10.pdf") is None
    assert date_from_text("2026-09-03_2026.09.03.pdf") == date(2026, 9, 3)
    # Mixed separators are not a plan date format (r14 R2).
    assert date_from_text("2026.09-03") is None and date_from_text("2026-09.03") is None
    assert date_from_text("x 2026.09-03 y 2026-09-03") == date(2026, 9, 3)
    assert date_from_text("2026-02-30 memo") is None and date_from_text("x 2026.09.03") == date(2026, 9, 3)


@pytest.mark.parametrize("kind, mime, ext, head, expected", [
    (Kind.TRANSCRIPT, "text/markdown", "md", b"# hi", HandlingMode.NORMALIZE),
    (Kind.TRANSCRIPT, "application/pdf", "pdf", b"%PDF-1.4", HandlingMode.S3),
    (Kind.LECTURE_SLIDES, "application/pdf", "pdf", b"%PDF-1.7", HandlingMode.NORMALIZE),
    (Kind.LECTURE_SLIDES, "application/pdf", "pdf", b"PK\x03\x04", HandlingMode.S3),  # disguised
    (Kind.LECTURE_SLIDES, "application/vnd.openxmlformats-officedocument.presentationml.presentation",
     "pptx", b"PK\x03\x04", HandlingMode.S3),
    (Kind.PROVIDED_CODE, "text/x-csrc", "c", b"#include <stdio.h>", HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL),
    (Kind.PROVIDED_CODE, "text/x-unrecognized-payload", "c", b"#include <stdio.h>", HandlingMode.S3),  # r2 #3
    (Kind.ASSIGNMENT_RESOURCE, XLSX_MIME, "xlsx", b"PK\x03\x04NOT-XLSX", HandlingMode.S3),  # r2 #3
    (Kind.PROVIDED_CODE, "application/pdf", "pdf", b"%PDF", HandlingMode.S3),
    (Kind.ASSIGNMENT_RESOURCE, "text/csv", "csv", b"a,b", HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL),
    (Kind.ASSIGNMENT_RESOURCE, "application/pdf", "pdf", b"%PDF", HandlingMode.NORMALIZE),
    (Kind.LAB_MATERIAL, "text/csv", "csv", b"a,b", HandlingMode.S3),  # Kind×format mismatch
    (Kind.RECORDING, None, None, None, HandlingMode.METADATA_ONLY),
    (Kind.ANNOUNCEMENT, None, None, None, HandlingMode.METADATA_ONLY),
    (Kind.UNSUPPORTED, "application/x-msdownload", "exe", b"MZ", HandlingMode.METADATA_ONLY),
    (Kind.LECTURE_SLIDES, "application/pdf", "pdf", None, HandlingMode.S3),  # bytes not proven
    (None, "application/pdf", "pdf", b"%PDF", HandlingMode.S3),
])
def test_handling_mode_matrix(kind, mime, ext, head, expected) -> None:
    assert handling_mode(kind, mime_type=mime, extension=ext, head=head) is expected


def _zip_with(names: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in names.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_xlsx_registration_needs_the_real_ooxml_container() -> None:
    good = _zip_with({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=good, payload_complete=True) \
        is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    # Without proof that the bytes are the whole payload nothing is registered (r5 #4/#5).
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=good) is HandlingMode.S3
    # The mandatory parts must be real OOXML, not just present (r5 #5).
    not_xml = _zip_with({"[Content_Types].xml": b"NOT XML", "xl/workbook.xml": b"NOT XML"})
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=not_xml, payload_complete=True) \
        is HandlingMode.S3
    wrong_root = _zip_with({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<sheet/>"})
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=wrong_root, payload_complete=True) \
        is HandlingMode.S3
    # The parts must be well-formed to the end: truncated tags, trailing garbage, duplicates (r6 R2).
    for parts in ({"[Content_Types].xml": b"<Types><broken", "xl/workbook.xml": b"<workbook><broken"},
                  {"[Content_Types].xml": b"<Types></Types>junk", "xl/workbook.xml": b"<workbook/>"},
                  {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook></workbook><x>"}):
        assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx",
                             head=_zip_with(parts), payload_complete=True) is HandlingMode.S3
    import io
    import zipfile
    duplicated = io.BytesIO()
    with zipfile.ZipFile(duplicated, "w") as archive:
        archive.writestr("[Content_Types].xml", b"NOT XML")
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", b"<workbook/>")
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx",
                         head=duplicated.getvalue(), payload_complete=True) is HandlingMode.S3
    doctype = _zip_with({"[Content_Types].xml": b"<!DOCTYPE x [<!ENTITY a 'b'>]><Types/>", "xl/workbook.xml": b"<workbook/>"})
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=doctype, payload_complete=True) \
        is HandlingMode.S3
    # Declared decompressed sizes are bounded before inflating anything.
    bomb = _zip_with({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>",
                      "xl/big.bin": b"\0" * 1024})
    import zipfile
    from unittest.mock import patch
    real_infolist = zipfile.ZipFile.infolist

    def inflated(self):
        infos = real_infolist(self)
        for info in infos:
            if info.filename == "xl/big.bin":
                info.file_size = XLSX_MAX_ENTRY_BYTES + 1
        return infos

    with patch.object(zipfile.ZipFile, "infolist", inflated):
        assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=bomb, payload_complete=True) \
            is HandlingMode.S3
    # A ZIP without the workbook parts (e.g. a DOCX or a plain archive) is not an XLSX.
    docx_like = _zip_with({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=docx_like, payload_complete=True) \
        is HandlingMode.S3
    # A truncated payload cannot prove its structure.
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=good[:40], payload_complete=True) \
        is HandlingMode.S3
    # A CRC-corrupted member with an intact central directory is not a valid XLSX (r3 #6).
    corrupted = bytearray(good)
    offset = good.index(b"<workbook/>")
    corrupted[offset:offset + 11] = b"<broken!!/>"
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type=XLSX_MIME, extension="xlsx", head=bytes(corrupted)) \
        is HandlingMode.S3


def test_code_mime_must_match_the_extension() -> None:
    src = b"#include <stdio.h>"
    assert handling_mode(Kind.PROVIDED_CODE, mime_type="text/plain", extension="c", head=src, payload_complete=True) \
        is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    for mime in ("application/json", "application/x-ipynb+json", "text/x-python"):
        assert handling_mode(Kind.PROVIDED_CODE, mime_type=mime, extension="c", head=src, payload_complete=True) is HandlingMode.S3
    notebook = b'{"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {}}'
    assert handling_mode(Kind.PROVIDED_CODE, mime_type="application/x-ipynb+json", extension="ipynb",
                         head=notebook, payload_complete=True) is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    assert handling_mode(Kind.PROVIDED_CODE, mime_type="text/plain", extension="ipynb",
                         head=notebook, payload_complete=True) is HandlingMode.S3


def test_binary_signatures_never_pass_as_text_opaque_material() -> None:
    # Text MIME + text extension but a PDF/ZIP/OLE/PNG body is a disguise: S3 (r12 R2).
    for body in (b"%PDF-1.7 x", b"PK\x03\x04junk", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\x89PNG\r\n", b"\x1f\x8bzz",
                 b"int x;\x01\x02", b"a,b\x1bc"):
        assert handling_mode(Kind.PROVIDED_CODE, mime_type="text/x-csrc", extension="c", head=body) is HandlingMode.S3
        assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="text/csv", extension="csv", head=body) is HandlingMode.S3
    assert handling_mode(Kind.PROVIDED_CODE, mime_type="text/x-csrc", extension="c", head=b"int main(void) {\n\treturn 0;\r\n}\n") \
        is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="text/csv", extension="csv", head="이름,점수\n가,1\n".encode()) \
        is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL


def test_json_formats_must_parse_as_their_declared_format() -> None:
    # The declared JSON/ipynb format must hold for the whole payload (r4 #5).
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="application/json", extension="json",
                         head=b"NOT JSON", payload_complete=True) is HandlingMode.S3
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="application/json", extension="json",
                         head=b'{"rows": [1, 2]}', payload_complete=True) is HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="application/json", extension="json",
                         head=b'{"rows": [1, 2]', payload_complete=True) is HandlingMode.S3  # truncated
    assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="application/json", extension="json",
                         head=b'{"rows": [1, 2]}') is HandlingMode.S3  # completeness not proven
    for payload in (b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}'):  # r5 #4
        assert handling_mode(Kind.ASSIGNMENT_RESOURCE, mime_type="application/json", extension="json",
                             head=payload, payload_complete=True) is HandlingMode.S3
    for payload in (b"NOT JSON", b'{"cells": []}', b'[]', b'{"cells": {}, "nbformat": 4}',
                    b'{"cells": [], "nbformat": true}', b'{"cells": [], "nbformat": NaN}'):
        assert handling_mode(Kind.PROVIDED_CODE, mime_type="application/x-ipynb+json", extension="ipynb",
                             head=payload, payload_complete=True) is HandlingMode.S3


def test_human_kind_set_and_type_initial_table() -> None:
    assert FILE_KINDS_V2 == ("TRANSCRIPT", "LECTURE_SLIDES", "LAB_MATERIAL", "PROVIDED_CODE",
                             "ASSIGNMENT_BRIEF", "ASSIGNMENT_RESOURCE", "SETUP_GUIDE", "COURSE_INFO",
                             "SUPPLEMENT", "EXAM", "MATERIAL_PDF")
    assert MATERIAL_TYPE_INITIAL[Kind.LECTURE_SLIDES] == "Lecture Slides"
    assert MATERIAL_TYPE_INITIAL[Kind.ASSIGNMENT_BRIEF] == "Professor Notes"
    assert MATERIAL_TYPE_INITIAL[Kind.EXAM] == "Reference"
    assert Kind.TRANSCRIPT not in MATERIAL_TYPE_INITIAL and Kind.ANNOUNCEMENT not in MATERIAL_TYPE_INITIAL


# --- recording calendar ---------------------------------------------------------

SEMESTER = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")


def _entries() -> list[RecordingEntry]:
    rows = [(1, "2026-09-03"), (2, "2026-09-10"), (3, "2026-09-17"), (4, "2026-12-10"), (5, "2026-10-01")]
    return [RecordingEntry(67535, f"res-{w}", 1, w, date.fromisoformat(d)) for w, d in rows]


def _calendar(entries=None, complete=True, semester=SEMESTER):
    return build_calendar("2026-2_LMS67535-001", entries if entries is not None else _entries(),
                          semester=semester, collection_complete=complete)


def test_observed_algorithms2_calendar_marks_week4_as_anomaly() -> None:
    calendar = _calendar()
    statuses = {e.entry.week: e.status for e in calendar.entries}
    assert statuses == {1: EntryStatus.CALENDAR, 2: EntryStatus.CALENDAR, 3: EntryStatus.CALENDAR,
                        4: EntryStatus.ANOMALY, 5: EntryStatus.CALENDAR}
    assert not calendar.ambiguous
    # Matching the observed 2029 transcripts: outside the semester → MISMATCH first.
    outside = match_transcript(calendar, transcript_date=date(2029, 9, 17), transcript_week=2)
    assert (outside.status, outside.reason) == (MatchStatus.MISMATCH, "OUTSIDE_SEMESTER")
    good = match_transcript(calendar, transcript_date=date(2026, 9, 17), transcript_week=3)
    assert good.status is MatchStatus.MATCHED and good.week == 3
    assert match_transcript(calendar, transcript_date=date(2026, 9, 17), transcript_week=2).reason \
        == "WEEK_MISMATCH"
    assert match_transcript(calendar, transcript_date=date(2026, 12, 10), transcript_week=4).reason \
        == "DATE_ANOMALY"
    assert match_transcript(calendar, transcript_date=date(2026, 9, 18), transcript_week=3).reason \
        == "DATE_NOT_IN_CALENDAR"
    assert match_transcript(calendar, transcript_date=None, transcript_week=3).reason == "NO_DATE"
    # Without a week the date alone matches (week comes from the calendar).
    assert match_transcript(calendar, transcript_date=date(2026, 9, 10), transcript_week=None).week == 2


def test_calendar_ambiguity_rules() -> None:
    assert _calendar(complete=False).reason == "COLLECTION_INCOMPLETE"
    assert _calendar(semester=None).reason == "SEMESTER_RANGE_UNKNOWN"
    two_anchors = _entries() + [RecordingEntry(67535, "res-1b", 1, 1, date(2026, 9, 4))]
    assert _calendar(two_anchors).reason == "ANCHOR_NOT_UNIQUE"
    early = [RecordingEntry(67535, f"res-{w}", 1, w, date(2026, 8, 1) + timedelta(days=7 * w))
             for w in range(1, 4)]
    assert _calendar(early).reason == "ANCHOR_OUTSIDE_SEMESTER"
    dup = _entries() + [RecordingEntry(67535, "res-3b", 1, 3, date(2026, 9, 17))]
    calendar = _calendar(dup)
    assert not calendar.ambiguous
    assert match_transcript(calendar, transcript_date=date(2026, 9, 17), transcript_week=3).reason \
        == "DUPLICATE_DATE"
    # A duplicate date is ambiguous even when the other entry is off cadence (P-A r2 #2):
    # week 5 recorded on the week-3 date makes both entries AMBIGUOUS, never MATCHED.
    off_cadence_dup = [RecordingEntry(67535, "res-1", 1, 1, date(2026, 9, 3)),
                       RecordingEntry(67535, "res-2", 1, 2, date(2026, 9, 10)),
                       RecordingEntry(67535, "res-3", 1, 3, date(2026, 9, 17)),
                       RecordingEntry(67535, "res-5", 1, 5, date(2026, 9, 17))]
    shared = _calendar(off_cadence_dup)
    assert {e.entry.week: e.status for e in shared.entries}[3] is EntryStatus.AMBIGUOUS
    assert {e.entry.week: e.status for e in shared.entries}[5] is EntryStatus.AMBIGUOUS
    assert match_transcript(shared, transcript_date=date(2026, 9, 17), transcript_week=3).status \
        is MatchStatus.AMBIGUOUS
    # Week order must be date order (P-A R3): week 2 recorded after week 3 conflicts both.
    swapped = [RecordingEntry(67535, "res-1", 1, 1, date(2026, 9, 3)),
               RecordingEntry(67535, "res-2", 1, 2, date(2026, 9, 15)),
               RecordingEntry(67535, "res-3", 1, 3, date(2026, 9, 14)),
               RecordingEntry(67535, "res-4", 1, 4, date(2026, 9, 24)),
               RecordingEntry(67535, "res-5", 1, 5, date(2026, 10, 1))]
    conflicted = _calendar(swapped)
    assert {e.entry.week: e.status for e in conflicted.entries}[2] is EntryStatus.AMBIGUOUS
    assert {e.entry.week: e.status for e in conflicted.entries}[3] is EntryStatus.AMBIGUOUS
    assert match_transcript(conflicted, transcript_date=date(2026, 9, 14), transcript_week=3).reason \
        == "DUPLICATE_DATE"
    assert match_transcript(None, transcript_date=date(2026, 9, 17), transcript_week=3).status \
        is MatchStatus.NO_CALENDAR
    # An empty but ambiguous calendar keeps its reason; only a complete empty one is NO_CALENDAR (r10 R6).
    empty_incomplete = _calendar((), complete=False)
    assert match_transcript(empty_incomplete, transcript_date=date(2026, 9, 17), transcript_week=3).reason \
        == "COLLECTION_INCOMPLETE"
    assert match_transcript(_calendar((), semester=None), transcript_date=date(2026, 9, 17), transcript_week=3).reason \
        == "SEMESTER_RANGE_UNKNOWN"
    assert match_transcript(_calendar(()), transcript_date=date(2026, 9, 17), transcript_week=3).status \
        is MatchStatus.NO_CALENDAR
    assert match_transcript(empty_incomplete, transcript_date=date(2029, 9, 17), transcript_week=3).reason \
        == "OUTSIDE_SEMESTER"
    # An ambiguous calendar never matches, and the range check still comes first.
    incomplete = _calendar(complete=False)
    assert match_transcript(incomplete, transcript_date=date(2026, 9, 17), transcript_week=3).status \
        is MatchStatus.AMBIGUOUS
    assert match_transcript(incomplete, transcript_date=date(2029, 9, 17), transcript_week=3).reason \
        == "OUTSIDE_SEMESTER"
    assert build_calendar("x", _entries()[:1], semester=SEMESTER, collection_complete=True).reason \
        == "TOO_FEW_VALID_ENTRIES"
    # The basis hash changes when any entry or the semester basis changes.
    assert _calendar().revision_hash() != _calendar(_entries()[:4]).revision_hash()
    assert _calendar().revision_hash() == _calendar().revision_hash()
    assert _calendar().revision_hash() != _calendar(
        semester=SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "canvas_term")).revision_hash()
