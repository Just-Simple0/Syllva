import pathlib
import sys
from dataclasses import fields

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.source_ref import SourceRef
from uls.normalization.schemas import NormalizedTranscript, TimestampMark
from uls.normalization.transcript import normalize_transcript
from uls.normalization.validators import parse_transcript_derivative, validate_normalized_transcript
from uls.retrieval.chunking import timestamp_chunks


def _normalize(raw: str) -> NormalizedTranscript:
    return normalize_transcript(
        raw,
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="sha256:source-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )


def test_body_is_verbatim_except_for_lf_and_marks_use_codepoint_offsets() -> None:
    raw = "도입😀\r\n[00:01:02] 첫 문장\r[00:02:03] 둘째"
    normalized = _normalize(raw)

    assert normalized.body == "도입😀\n[00:01:02] 첫 문장\n[00:02:03] 둘째"
    assert normalized.marks == (
        TimestampMark(62, normalized.body.index("[00:01:02]")),
        TimestampMark(123, normalized.body.index("[00:02:03]")),
    )
    assert normalized.status.value == "ready"
    assert normalized.body[normalized.marks[0].char_offset :] == "[00:01:02] 첫 문장\n[00:02:03] 둘째"


def test_front_matter_is_typed_and_status_has_one_source_of_truth() -> None:
    normalized = _normalize("[00:00:01] 내용")
    assert normalized.front_matter.schema == "uls.transcript.v1"
    assert normalized.front_matter.source_ref == SourceRef("google_drive", "transcript-05")
    assert "status" not in {item.name for item in fields(NormalizedTranscript)}
    rendered = normalized.to_markdown()
    assert rendered.count("status:") == 1
    reparsed = parse_transcript_derivative(rendered)
    assert reparsed.body == normalized.body
    assert reparsed.marks == normalized.marks
    assert validate_normalized_transcript(reparsed) is True


def test_timestamp_parse_failure_keeps_body_and_downgrades_to_partial() -> None:
    raw = "[00:61:02] 원문은 그대로 남아야 함\r\n[00:00:03] 정상"
    normalized = _normalize(raw)

    assert normalized.body == raw.replace("\r\n", "\n")
    assert normalized.status.value == "partial"
    assert normalized.marks == (TimestampMark(3, normalized.body.index("[00:00:03]")),)


def test_export_switching_from_minutes_to_hours_keeps_all_evidence() -> None:
    raw = "[0:01] 도입😀\r\n[59:59] 한 시간 직전\n[1:00:00] 이후"
    normalized = _normalize(raw)
    assert normalized.body == raw.replace("\r\n", "\n")
    assert normalized.status.value == "ready"
    assert normalized.marks == tuple(
        TimestampMark(seconds, normalized.body.index(stamp))
        for stamp, seconds in (("[0:01]", 1), ("[59:59]", 3599), ("[1:00:00]", 3600))
    )
    reparsed = parse_transcript_derivative(normalized.to_markdown())
    assert reparsed.marks == normalized.marks
    chunks = timestamp_chunks(reparsed)
    assert [str(chunk.locator) for chunk in chunks] == [
        "COMP319-S05:t00:00:01-00:59:58",
        "COMP319-S05:t00:59:59",
        "COMP319-S05:t01:00:00",
    ]
    assert "".join(chunk.content for chunk in chunks) == normalized.body


@pytest.mark.parametrize("marker", ["[1:60]", "[1:2]", "[1:xx]", "[1:02", "[1:02)"])
def test_malformed_short_timestamp_is_partial(marker: str) -> None:
    normalized = _normalize(marker)
    assert normalized.body == marker
    assert normalized.status.value == "partial"
    assert normalized.marks == ()


def test_short_timestamps_support_provider_brackets_and_elapsed_minutes() -> None:
    raw = "[참고: 예제] (알림: 중간 퀴즈)\n【01:02】 첫째\n(65:13) 둘째"
    normalized = _normalize(raw)
    assert normalized.body == raw
    assert normalized.status.value == "ready"
    assert normalized.marks == (
        TimestampMark(62, raw.index("【01:02】")),
        TimestampMark(3913, raw.index("(65:13)")),
    )
