from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_llm import FakeLLMAdapter, session_result
from fake_notion import FakeNotionReader, FakeNotionWriter
from uls.domain.enums import ProcessingStatus
from uls.domain.errors import SourcePartialError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.session import generate_session_enrichment
from uls.enrichment.writer import EnrichmentWriter
from uls.normalization.transcript import normalize_transcript
from uls.state.sqlite import SQLiteStateStore


FP = SourceFingerprint(1, "v1")


def _transcript(*, version: int = 1, source_hash: str = "v1", status: str = "ready"):
    value = normalize_transcript(
        "[00:00:01] grounded topic",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=source_hash,
        source_version=version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )
    return value if status == "ready" else value.to_markdown().replace("status: ready", f"status: {status}")


def test_old_derivative_against_authoritative_new_fingerprint_fails_closed() -> None:
    adapter = FakeLLMAdapter(session_result=session_result())

    with pytest.raises(SourcePartialError):
        generate_session_enrichment(
            _transcript(),
            adapter,
            SourceFingerprint(2, "v2"),
            entity_id="COMP319-S05",
        )

    assert adapter.calls == []


@pytest.mark.parametrize("status", ["partial", "needs_review", "failed", "pending", "processing"])
def test_every_non_ready_derivative_status_blocks_normal_ready_enrichment(status: str) -> None:
    adapter = FakeLLMAdapter(session_result=session_result())

    with pytest.raises(SourcePartialError):
        generate_session_enrichment(
            _transcript(status=status),
            adapter,
            SourceFingerprint(1, "v1"),
            entity_id="COMP319-S05",
        )

    assert adapter.calls == []


def test_writer_keeps_old_record_when_input_gate_blocks_generation() -> None:
    reader = FakeNotionReader(
        enrichments={
            "COMP319-S05": {
                "payload": {"summary": "old"},
                "based_on_source_version": 1,
                "based_on_source_hash": "old",
                "processor_version": "1.1.0",
            }
        }
    )
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(
        fingerprints={
            "COMP319-S05": SourceFingerprint(2, "v2"),
            "transcript-05": SourceFingerprint(2, "v2"),
        }
    )
    adapter = FakeLLMAdapter(session_result=session_result())
    writer = EnrichmentWriter(adapter, notion, drive, SQLiteStateStore(":memory:"))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert adapter.calls == []
    assert notion.ai_region_writes == []
    assert reader.get_session_enrichment("COMP319-S05")["payload"] == {"summary": "old"}


@pytest.mark.parametrize(
    "field",
    ["course_key", "source_ref", "processor_version", "normalized_at"],
)
def test_producer_rejects_derivatives_missing_required_section_16_fields(field: str) -> None:
    derivative = _transcript()
    front = derivative.as_front_matter()
    del front[field]
    malformed = {
        "front_matter": front,
        "body": derivative.body,
        "marks": derivative.marks,
    }
    adapter = FakeLLMAdapter(session_result=session_result())

    with pytest.raises(SourcePartialError):
        generate_session_enrichment(
            malformed,
            adapter,
            SourceFingerprint(1, "v1"),
            entity_id="COMP319-S05",
        )

    assert adapter.calls == []


def test_producer_rejects_a_source_ref_that_is_not_the_requested_identity() -> None:
    adapter = FakeLLMAdapter(session_result=session_result())

    with pytest.raises(SourcePartialError):
        generate_session_enrichment(
            _transcript(),
            adapter,
            SourceFingerprint(1, "v1"),
            entity_id="COMP319-S05",
            source_ref=SourceRef("google_drive", "different-transcript"),
        )

    assert adapter.calls == []


def test_writer_uses_graph_source_identity_when_caller_omits_source_ref() -> None:
    graph_source = "transcript-05"
    other_source = "other-file"
    mismatched = normalize_transcript(
        "[00:00:01] 첫 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", other_source),
        source_hash=FP.source_hash,
        source_version=FP.source_version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )
    reader = FakeNotionReader(
        sessions=[
            {
                "ID": "COMP319-S05",
                "Name": "05 · CPU Scheduling",
                "Course": "2026-1_COMP319-002",
                "Session No": 5,
                "Normalized Transcript": graph_source,
            }
        ]
    )
    notion = FakeNotionWriter(reader)
    adapter = FakeLLMAdapter(session_result=session_result())
    drive = FakeDriveReader(
        fingerprints={
            "COMP319-S05": FP,
            graph_source: FP,
            other_source: FP,
        }
    )
    state = SQLiteStateStore(":memory:")
    writer = EnrichmentWriter(adapter, notion, drive, state)

    outcome = writer.process_session("COMP319-S05", mismatched)

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert outcome.error_class is not None
    assert "graph source" in (outcome.error or "")
    assert adapter.calls == []
    assert notion.ai_region_writes == []
