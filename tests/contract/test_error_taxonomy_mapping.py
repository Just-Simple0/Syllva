from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_llm import (
    FakeLLMAdapter,
    LLMAmbiguousError,
    LLMPermanentError,
    LLMPolicyDeniedError,
    LLMRateLimitError,
    LLMTransientError,
    session_result,
)
from fake_notion import FakeNotionReader, FakeNotionWriter
from uls.domain.enums import ProcessingStatus
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.writer import EnrichmentWriter, map_error_class
from uls.normalization.transcript import normalize_transcript
from uls.orchestration.retry import ErrorClass
from uls.state.sqlite import SQLiteStateStore


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("LLM_TRANSIENT", ErrorClass.TRANSIENT),
        ("LLM_RATE_LIMITED", ErrorClass.RATE_LIMITED),
        ("LLM_PERMANENT", ErrorClass.PERMANENT),
        ("LLM_AMBIGUOUS", ErrorClass.AMBIGUOUS),
        ("LLM_POLICY_DENIED", ErrorClass.POLICY_DENIED),
        (LLMTransientError("x"), ErrorClass.TRANSIENT),
        (LLMRateLimitError("x"), ErrorClass.RATE_LIMITED),
        (LLMPermanentError("x"), ErrorClass.PERMANENT),
        (LLMAmbiguousError("x"), ErrorClass.AMBIGUOUS),
        (LLMPolicyDeniedError("x"), ErrorClass.POLICY_DENIED),
    ],
)
def test_internal_llm_names_are_mapped_to_the_frozen_taxonomy(value, expected) -> None:
    assert map_error_class(value) is expected


def _transcript():
    return normalize_transcript(
        "[00:00:01] 첫 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash="h",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )


def _writer(adapter):
    reader = FakeNotionReader()
    notion = FakeNotionWriter(reader)
    drive = FakeDriveReader(fingerprints={"COMP319-S05": SourceFingerprint(1, "h"), "transcript-05": SourceFingerprint(1, "h")})
    state = SQLiteStateStore(":memory:")
    return EnrichmentWriter(adapter, notion, drive, state, sleep=lambda _: None), state


def test_transient_provider_failure_is_bounded_by_retry_policy() -> None:
    adapter = FakeLLMAdapter(
        session_outputs=[LLMTransientError("temporary") for _ in range(3)]
    )
    writer, state = _writer(adapter)

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is ProcessingStatus.FAILED
    assert outcome.error_class is ErrorClass.TRANSIENT
    assert len(adapter.calls) == 3
    assert outcome.job.status is ProcessingStatus.FAILED
    assert state.get_job(job_id=outcome.job.id).error_class == ErrorClass.TRANSIENT.value


@pytest.mark.parametrize(
    ("exception", "status", "error_class"),
    [
        (LLMAmbiguousError("ambiguous"), ProcessingStatus.NEEDS_REVIEW, ErrorClass.AMBIGUOUS),
        (LLMPermanentError("bad request"), ProcessingStatus.FAILED, ErrorClass.PERMANENT),
        (LLMPolicyDeniedError("blocked"), ProcessingStatus.FAILED, ErrorClass.POLICY_DENIED),
        (LLMRateLimitError("slow down"), ProcessingStatus.FAILED, ErrorClass.RATE_LIMITED),
    ],
)
def test_non_retryable_classification_controls_job_outcome(exception, status, error_class) -> None:
    writer, _ = _writer(FakeLLMAdapter(session_outputs=[exception]))

    outcome = writer.process_session("COMP319-S05", _transcript())

    assert outcome.status is status
    assert outcome.error_class is error_class
