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
from uls.enrichment.schemas import EnrichmentRecord
from uls.enrichment.writer import EnrichmentWriter
from uls.normalization.transcript import normalize_transcript
from uls.state.sqlite import SQLiteStateStore


FP = SourceFingerprint(1, "transcript-v1")


def _transcript(*, source_hash: str = "transcript-v1", source_version: int = 1, status: str = "ready"):
    value = normalize_transcript(
        "[00:00:01] 첫 주제\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=source_hash,
        source_version=source_version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )
    if status == "ready":
        return value
    return value.to_markdown().replace("status: ready", f"status: {status}")


def _writer(adapter, *, drive=None, reader=None, state=None):
    reader = reader or FakeNotionReader()
    notion = FakeNotionWriter(reader)
    drive = drive or FakeDriveReader(
        fingerprints={"COMP319-S05": FP, "transcript-05": FP}
    )
    state = state or SQLiteStateStore(":memory:")
    return EnrichmentWriter(adapter, notion, drive, state), reader, notion, drive, state


def _forbidden_keys(value):
    forbidden = {
        "source_class",
        "freshness",
        "factual",
        "based_on",
        "verified",
        "scope confirmed",
        "decision",
        "state",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in forbidden:
                yield str(key)
            yield from _forbidden_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _forbidden_keys(item)


def test_model_control_metadata_is_stripped_and_server_metadata_is_forced() -> None:
    result = session_result(malicious=True)
    result_with_bad_provenance = LLMEnrichmentResult(
        output=result.output,
        evidence=result.evidence,
        confidence=result.confidence,
        classification=result.classification,
        provider_provenance={
            "provider": "fake",
            "model": "deterministic",
            "source_class": "professor_transcript",
            "freshness": "FRESH",
            "Verified": True,
            "Decision": "Approve",
        },
        based_on={"source_version": 999, "source_hash": "spoof"},
        explicitness=result.explicitness,
    )
    writer, reader, notion, _, _ = _writer(FakeLLMAdapter(session_result=result_with_bad_provenance))

    outcome = writer.process_session("COMP319-S05", _transcript())
    stored = reader.get_session_enrichment("COMP319-S05")

    assert outcome.status is ProcessingStatus.READY
    assert stored is not None
    assert stored.source_fingerprint == FP
    assert stored.provider_provenance == {"provider": "fake", "model": "deterministic"}
    assert not list(_forbidden_keys(stored.as_dict()))
    assert all(signal.ownership.value == "AI" for signal in stored.payload.all_signals())
    assert notion.ai_region_writes[0]["patch"]["ownership"] == "AI"
    assert "Topics" not in reader.sessions["COMP319-S05"]


def test_model_fingerprint_cannot_override_verified_record_fingerprint() -> None:
    writer, reader, _, _, _ = _writer(FakeLLMAdapter(session_result=session_result(based_on={"source_version": 99, "source_hash": "fake"})))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.record is not None
    assert outcome.record.based_on_source_version == 1
    assert outcome.record.based_on_source_hash == "transcript-v1"
    assert reader.get_session_enrichment("COMP319-S05").based_on == FP


def test_input_mismatch_keeps_the_previous_enrichment_and_does_not_call_llm() -> None:
    old = EnrichmentRecord(
        payload={"summary": "old enrichment"},
        based_on_source_version=2,
        based_on_source_hash="old",
        processor_version="1.2.0",
    )
    reader = FakeNotionReader(enrichments={"COMP319-S05": old})
    drive = FakeDriveReader(
        fingerprints={"COMP319-S05": SourceFingerprint(3, "transcript-v3"), "transcript-05": SourceFingerprint(3, "transcript-v3")}
    )
    adapter = FakeLLMAdapter(session_result=session_result())
    writer, reader, notion, _, _ = _writer(adapter, drive=drive, reader=reader)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert adapter.calls == []
    assert reader.get_session_enrichment("COMP319-S05") is old
    assert notion.ai_region_writes == []


def test_partial_input_cannot_be_published_as_ready() -> None:
    adapter = FakeLLMAdapter(session_result=session_result())
    writer, reader, notion, _, _ = _writer(adapter)

    outcome = writer.process_session("COMP319-S05", _transcript(status="partial"))

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert adapter.calls == []
    assert reader.get_session_enrichment("COMP319-S05") is None
    assert notion.ai_region_writes == []


def test_caller_fingerprint_is_not_an_authority_fallback() -> None:
    class NoFingerprintDrive:
        pass

    reader = FakeNotionReader()
    notion = FakeNotionWriter(reader)
    adapter = FakeLLMAdapter(session_result=session_result())
    writer = EnrichmentWriter(
        adapter,
        notion,
        NoFingerprintDrive(),
        SQLiteStateStore(":memory:"),
    )

    outcome = writer.process_session(
        "COMP319-S05",
        _transcript(),
        current_fingerprint=FP,
    )

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.job is None
    assert adapter.calls == []
    assert notion.ai_region_writes == []


def test_identical_terminal_job_is_reused_without_regeneration_or_republication() -> None:
    adapter = FakeLLMAdapter(session_result=session_result())
    writer, reader, notion, _, state = _writer(adapter)

    first = writer.process_session("COMP319-S05", _transcript())
    second = writer.process_session("COMP319-S05", _transcript())

    assert first.status is ProcessingStatus.READY
    assert second.status is ProcessingStatus.READY
    assert second.job.id == first.job.id
    assert second.generation is None
    assert second.published is False
    assert len(adapter.calls) == 1
    assert len(notion.ai_region_writes) == 1
    assert reader.get_session_enrichment("COMP319-S05") is not None
    assert state.get_job(job_id=second.job.id).status is ProcessingStatus.READY
