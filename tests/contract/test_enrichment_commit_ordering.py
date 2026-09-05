from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_llm import FakeLLMAdapter, LLMPermanentError, session_result
from fake_notion import FakeNotionReader, FakeNotionWriter
from uls.domain.enums import ProcessingStatus
from uls.domain.errors import SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment._common import EnrichmentSafetyError, EnrichmentTerminalizationError
from uls.enrichment.schemas import EnrichmentRecord
from uls.enrichment.writer import EnrichmentWriter
from uls.normalization.transcript import normalize_transcript
from uls.orchestration.retry import ErrorClass
from uls.state.sqlite import SQLiteStateStore


FP = SourceFingerprint(1, "h")


def _transcript(*, source_hash: str = "h", source_version: int = 1):
    return normalize_transcript(
        "[00:00:01] 첫 주제\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=source_hash,
        source_version=source_version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )


class TraceState:
    def __init__(self, events: list[str]) -> None:
        self.store = SQLiteStateStore(":memory:")
        self.events = events

    def create_job(self, **kwargs):
        self.events.append("job_processing")
        return self.store.create_job(**kwargs)

    def create_processing_record(self, **kwargs):
        self.events.append("processing_record")
        return self.store.create_processing_record(**kwargs)

    def complete_job(self, *args, **kwargs):
        self.events.append("job_terminal")
        return self.store.complete_job(*args, **kwargs)

    def fail_job(self, *args, **kwargs):
        self.events.append("job_failed")
        return self.store.fail_job(*args, **kwargs)


class NullProcessingRecordState(TraceState):
    def create_processing_record(self, **kwargs):
        self.events.append("processing_record")
        return None


class NullCompletionState(TraceState):
    def complete_job(self, *args, **kwargs):
        self.events.append("job_terminal")
        return None


class FailingCompletionState(TraceState):
    def complete_job(self, *args, **kwargs):
        self.events.append("job_terminal")
        raise RuntimeError("completion failed after publication")


class FailingTerminalizationState(TraceState):
    def fail_job(self, *args, **kwargs):
        self.events.append("job_failed")
        raise RuntimeError("failure terminalization unavailable")


class TraceDrive(FakeDriveReader):
    def __init__(self, events: list[str], **kwargs):
        super().__init__(**kwargs)
        self.trace = events

    def get_current_fingerprint(self, entity_id_or_source_ref):
        self.trace.append("fingerprint")
        return super().get_current_fingerprint(entity_id_or_source_ref)


class FlippingDrive(TraceDrive):
    def __init__(self, events: list[str], **kwargs):
        super().__init__(events, **kwargs)
        self.lookups = 0

    def get_current_fingerprint(self, entity_id_or_source_ref):
        self.lookups += 1
        self.trace.append("fingerprint")
        return FP if self.lookups == 1 else SourceFingerprint(2, "h-v2")


class TraceLLM(FakeLLMAdapter):
    def __init__(self, events: list[str], **kwargs):
        super().__init__(**kwargs)
        self.trace = events

    def enrich_session(self, derivative, *, chunks=()):
        self.trace.append("generation")
        return super().enrich_session(derivative, chunks=chunks)


class TraceNotion(FakeNotionWriter):
    def __init__(self, reader, trace):
        super().__init__(reader, events=[])
        self.trace = trace

    def write_ai_region(self, target_db, entity_id, patch, *, actor=None):
        self.trace.append("ai_region")
        if actor is None:
            return super().write_ai_region(target_db, entity_id, patch)
        return super().write_ai_region(target_db, entity_id, patch, actor=actor)


class NoopRollbackNotion(TraceNotion):
    def restore_ai_region(self, *args, **kwargs):
        self.trace.append("ai_region_rollback_noop")
        return None


class CountingEnrichmentReader(FakeNotionReader):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.enrichment_reads = 0

    def get_session_enrichment(self, entity_id):
        self.enrichment_reads += 1
        return super().get_session_enrichment(entity_id)


def _setup(adapter, drive, events):
    reader = FakeNotionReader()
    notion = TraceNotion(reader, events)
    state = TraceState(events)
    writer = EnrichmentWriter(adapter, notion, drive, state)
    return writer, reader, notion, state


def test_commit_ordering_places_ready_last_and_uses_ai_region_only() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_result=session_result())
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    writer, reader, notion, state = _setup(adapter, drive, events)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.READY
    assert events.index("job_processing") < events.index("generation")
    assert events.index("generation") < events.index("processing_record")
    assert events.index("ai_region") < events.index("processing_record")
    assert events.index("processing_record") < events.index("job_terminal")
    assert events.index("ai_region") < events.index("job_terminal")
    assert len(notion.ai_region_writes) == 1
    assert reader.sessions["COMP319-S05"].get("Topics") is None
    assert state.store.get_job(job_id=outcome.job.id).status is ProcessingStatus.READY


def test_publish_rechecks_fingerprint_and_aborts_on_toctou_change() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_result=session_result())
    drive = FlippingDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    writer, reader, notion, _ = _setup(adapter, drive, events)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.NEEDS_REVIEW
    assert drive.lookups >= 2
    assert notion.ai_region_writes == []
    assert reader.get_session_enrichment("COMP319-S05") is None


def test_llm_failure_does_not_replace_a_previous_record() -> None:
    old = EnrichmentRecord(
        payload={"summary": "previous"},
        based_on_source_version=1,
        based_on_source_hash="h",
        processor_version="1.1.0",
    )
    reader = FakeNotionReader(enrichments={"COMP319-S05": old})
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(fingerprints={"COMP319-S05": FP, "transcript-05": FP})
    state = SQLiteStateStore(":memory:")
    writer = EnrichmentWriter(
        FakeLLMAdapter(session_outputs=[LLMPermanentError("bad output")]),
        notion,
        drive,
        state,
    )

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.error_class is ErrorClass.PERMANENT
    assert reader.get_session_enrichment("COMP319-S05") is old
    assert notion.ai_region_writes == []


def test_missing_durable_processing_record_rolls_back_after_publication() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_result=session_result())
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    reader = FakeNotionReader()
    notion = TraceNotion(reader, events)
    state = NullProcessingRecordState(events)
    writer = EnrichmentWriter(adapter, notion, drive, state)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert state.store.get_job(job_id=outcome.job.id).status is ProcessingStatus.FAILED
    assert reader.get_session_enrichment("COMP319-S05") is None
    assert len(notion.ai_region_writes) == 1


class LookupFailingEnrichmentReader(FakeNotionReader):
    def get_session_enrichment(self, entity_id):
        raise LookupError(f"transient enrichment lookup failed: {entity_id}")


def test_unreadable_prewrite_snapshot_aborts_before_ai_region_publication() -> None:
    reader = LookupFailingEnrichmentReader()
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(fingerprints={"COMP319-S05": FP, "transcript-05": FP})
    state = SQLiteStateStore(":memory:")
    adapter = FakeLLMAdapter(session_result=session_result())
    writer = EnrichmentWriter(adapter, notion, drive, state)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.job is not None
    assert notion.ai_region_writes == []


def test_unreported_ready_transition_rolls_back_consumer_visibility() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_result=session_result())
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    reader = FakeNotionReader()
    notion = TraceNotion(reader, events)
    state = NullCompletionState(events)
    writer = EnrichmentWriter(adapter, notion, drive, state)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.published is False
    assert state.store.get_job(job_id=outcome.job.id).status is ProcessingStatus.FAILED
    assert reader.get_session_enrichment("COMP319-S05") is None


def test_completion_failure_restores_previous_record_and_readback_blocks_fresh_visibility() -> None:
    events: list[str] = []
    old = EnrichmentRecord(
        payload={"summary": "previous"},
        based_on_source_version=1,
        based_on_source_hash="h",
        processor_version="1.1.0",
    )
    reader = CountingEnrichmentReader(enrichments={"COMP319-S05": old})
    notion = TraceNotion(reader, events)
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    state = FailingCompletionState(events)
    writer = EnrichmentWriter(TraceLLM(events, session_result=session_result()), notion, drive, state)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.published is False
    assert state.store.get_job(job_id=outcome.job.id).status is ProcessingStatus.FAILED
    reads_before_assertions = reader.enrichment_reads
    assert reader.get_session_enrichment("COMP319-S05") == old
    assert reader.get_session_enrichment("COMP319-S05") != outcome.record
    assert reads_before_assertions >= 2  # initial read plus strict rollback read-back


def test_failed_compensation_is_a_hard_visible_safety_error() -> None:
    events: list[str] = []
    reader = FakeNotionReader()
    notion = NoopRollbackNotion(reader, events)
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    state = FailingCompletionState(events)
    writer = EnrichmentWriter(TraceLLM(events, session_result=session_result()), notion, drive, state)

    with pytest.raises(EnrichmentSafetyError, match="consumer-visible|rollback"):
        writer.process_session("COMP319-S05", _transcript())

    stored_job = state.store.connection.execute(
        "SELECT status, last_error FROM jobs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert stored_job is not None
    assert stored_job["status"] == ProcessingStatus.FAILED.value
    assert "ENRICHMENT_SAFETY_QUARANTINED" in (stored_job["last_error"] or "")
    assert "consumer-visible" in (stored_job["last_error"] or "")
    leaked = reader.get_session_enrichment("COMP319-S05")
    assert leaked is not None

    # A deterministic retry must not turn a quarantined, still-visible
    # enrichment into an apparently clean FAILED result.
    with pytest.raises(EnrichmentSafetyError, match="consumer-visible|quarantined"):
        writer.process_session("COMP319-S05", _transcript())
    assert reader.get_session_enrichment("COMP319-S05") == leaked


def test_failure_terminalization_error_is_not_returned_as_plain_failed() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_outputs=[LLMPermanentError("bad output")])
    drive = TraceDrive(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    reader = FakeNotionReader()
    notion = TraceNotion(reader, events)
    state = FailingTerminalizationState(events)
    writer = EnrichmentWriter(adapter, notion, drive, state)

    with pytest.raises(EnrichmentTerminalizationError, match="terminalized"):
        writer.process_session("COMP319-S05", _transcript())

    stored_job = state.store.connection.execute(
        "SELECT status FROM jobs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert stored_job is not None
    assert stored_job["status"] == ProcessingStatus.PROCESSING.value


def test_no_state_store_never_publishes_or_calls_the_provider() -> None:
    reader = FakeNotionReader()
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(fingerprints={"COMP319-S05": FP, "transcript-05": FP})
    adapter = TraceLLM([], session_result=session_result())
    writer = EnrichmentWriter(adapter, notion, drive, None)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert adapter.calls == []
    assert notion.ai_region_writes == []
    assert reader.get_session_enrichment("COMP319-S05") is None


class AuthorityFailsAfterGeneration(TraceDrive):
    def __init__(self, events: list[str], **kwargs):
        super().__init__(events, **kwargs)
        self.lookups = 0

    def get_current_fingerprint(self, entity_id_or_source_ref):
        self.lookups += 1
        self.trace.append("fingerprint")
        if self.lookups > 1:
            raise SourceUnavailableError("fingerprint authority unavailable")
        return FP


def test_publish_authority_failure_terminalizes_the_processing_job() -> None:
    events: list[str] = []
    adapter = TraceLLM(events, session_result=session_result())
    drive = AuthorityFailsAfterGeneration(
        events,
        fingerprints={"COMP319-S05": FP, "transcript-05": FP},
    )
    writer, reader, notion, state = _setup(adapter, drive, events)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert state.store.get_job(job_id=outcome.job.id).status is ProcessingStatus.FAILED
    assert reader.get_session_enrichment("COMP319-S05") is None
    assert notion.ai_region_writes == []
