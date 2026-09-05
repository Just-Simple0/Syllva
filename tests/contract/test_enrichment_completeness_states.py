from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_llm import FakeLLMAdapter, session_result
from fake_notion import FakeNotionReader, FakeNotionWriter
from uls.adapters.llm.base import LLMEnrichmentResult
from uls.domain.enums import ProcessingStatus
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.session import generate_session_enrichment
from uls.enrichment.writer import EnrichmentWriter
from uls.normalization.transcript import normalize_transcript
from uls.state.sqlite import SQLiteStateStore


FP = SourceFingerprint(1, "transcript-v1")
KINDS = (
    "summary",
    "topics",
    "professor_emphasis",
    "professor_examples",
    "exam_signals",
    "likely_confusions",
)


def _transcript():
    return normalize_transcript(
        "[00:00:01] 첫 주제\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="transcript-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )


def _writer(adapter):
    reader = FakeNotionReader()
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(fingerprints={"COMP319-S05": FP, "transcript-05": FP})
    state = SQLiteStateStore(":memory:")
    return EnrichmentWriter(adapter, notion, drive, state), reader, notion


def _result(output, evidence):
    return LLMEnrichmentResult(
        output=output,
        evidence=evidence,
        confidence=0.8,
        classification="proposal",
        provider_provenance={"provider": "fake"},
    )


def test_all_dropped_candidates_are_never_an_empty_ready_result() -> None:
    base = session_result(locator="COMP319-S05:t99:59:59", quote="missing")
    writer, reader, notion = _writer(FakeLLMAdapter(session_result=base))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert outcome.generation is not None
    assert "ENRICHMENT_NO_EVIDENCE" in outcome.generation.drop_reasons
    assert outcome.generation.produced_count == 0
    assert notion.ai_region_writes == []
    assert reader.get_session_enrichment("COMP319-S05") is None


def test_partial_drop_is_recorded_while_valid_siblings_can_publish_ready() -> None:
    base = session_result()
    output = {kind: list(base.output[kind]) for kind in KINDS}
    evidence = {kind: list(base.evidence[kind]) for kind in KINDS}
    output["topics"].append({"content": "bad topic", "symbolic_hint": "bad topic"})
    evidence["topics"].append({"locator": "COMP319-S05:t99:59:59", "quote": "bad topic"})
    writer, _, notion = _writer(FakeLLMAdapter(session_result=_result(output, evidence)))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.READY
    assert outcome.generation is not None
    assert outcome.generation.completeness["topics"] == "produced"
    assert outcome.generation.dropped_count == 1
    assert outcome.generation.produced_count == 6
    assert len(notion.ai_region_writes) == 1


def test_required_kind_omission_is_not_silent_success() -> None:
    base = session_result(include=("summary",))
    writer, _, notion = _writer(FakeLLMAdapter(session_result=base))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert outcome.completeness["summary"] == "produced"
    assert outcome.completeness["topics"] == "omitted_or_failed"
    assert notion.ai_region_writes == []


def test_empty_required_kinds_cannot_self_certify_ready() -> None:
    base = session_result(include=("summary",))
    output = {kind: [] for kind in KINDS}
    output["summary"] = base.output["summary"]
    evidence = {"summary": base.evidence["summary"]}
    writer, _, notion = _writer(FakeLLMAdapter(session_result=_result(output, evidence)))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert outcome.completeness["exam_signals"] == "omitted_or_failed"
    assert outcome.completeness["likely_confusions"] == "omitted_or_failed"
    assert notion.ai_region_writes == []


def test_generation_never_reports_all_empty_provider_output_as_ready() -> None:
    output = {kind: [] for kind in KINDS}
    result = generate_session_enrichment(
        _transcript(),
        FakeLLMAdapter(session_result=_result(output, {})),
        FP,
        entity_id="COMP319-S05",
    )

    assert result.status is ProcessingStatus.NEEDS_REVIEW
    assert set(result.completeness.values()) == {"omitted_or_failed"}
    assert "ENRICHMENT_NO_EVIDENCE" in result.drop_reasons


def test_model_completeness_metadata_cannot_certify_zero_signals() -> None:
    output = {
        "completeness": {kind: "legitimately_empty" for kind in KINDS},
    }
    result = generate_session_enrichment(
        _transcript(),
        FakeLLMAdapter(session_result=_result(output, {})),
        FP,
        entity_id="COMP319-S05",
    )

    assert result.status is ProcessingStatus.NEEDS_REVIEW
    assert result.produced_count == 0
    assert set(result.completeness.values()) == {"omitted_or_failed"}
    assert "ENRICHMENT_NO_EVIDENCE" in result.drop_reasons
