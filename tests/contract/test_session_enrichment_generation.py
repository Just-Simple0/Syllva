from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_llm import (
    FakeLLMAdapter,
    cross_locator_quote_result,
    session_result,
    ungrounded_result,
)
from uls.adapters.llm.base import LLMEnrichmentResult
from uls.domain.enums import Explicitness, ProcessingStatus
from uls.domain.errors import SourcePartialError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.session import generate_session_enrichment
from uls.normalization.transcript import normalize_transcript


FP = SourceFingerprint(1, "transcript-v1")


def _transcript():
    return normalize_transcript(
        "[00:00:01] 첫 주제\n[00:00:10] 둘째 주제",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=FP.source_hash,
        source_version=FP.source_version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )


def _generate(result):
    return generate_session_enrichment(
        _transcript(),
        FakeLLMAdapter(session_result=result),
        FP,
        entity_id="COMP319-S05",
    )


def _generate_attribution_case(*, claim: str, quote: str):
    derivative = normalize_transcript(
        f"[00:00:01] {quote}",
        entity_id="COMP319-S05",
        course_key="2026-1_COMP319-002",
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=FP.source_hash,
        source_version=FP.source_version,
        processor_version="1.2.0",
        now="2026-09-05T00:00:00+09:00",
    )
    kinds = (
        "summary",
        "topics",
        "professor_emphasis",
        "professor_examples",
        "exam_signals",
        "likely_confusions",
    )
    result = LLMEnrichmentResult(
        output={
            kind: [{"content": claim, "symbolic_hint": claim}]
            for kind in kinds
        },
        evidence={
            kind: [{"locator": "COMP319-S05:t00:00:01", "quote": quote}]
            for kind in kinds
        },
        confidence=0.9,
        classification="proposal",
    )
    return generate_session_enrichment(
        derivative,
        FakeLLMAdapter(session_result=result),
        FP,
        entity_id="COMP319-S05",
    )


def test_grounded_candidates_are_retained_with_symbolic_hints() -> None:
    result = _generate(session_result())

    assert result.status is ProcessingStatus.READY
    assert result.produced_count == 6
    assert all(result.completeness.values())
    assert all(signal.evidence for signal in result.payload.all_signals())
    assert all(signal.symbolic_hint == "첫 주제" for signal in result.payload.all_signals())


def test_summary_and_likely_confusions_can_never_be_explicit() -> None:
    result = _generate(session_result())

    assert result.payload.summary[0].explicitness is Explicitness.INFERRED
    assert result.payload.likely_confusions[0].explicitness is Explicitness.INFERRED
    assert result.payload.topics[0].explicitness is Explicitness.EXPLICIT


def test_quote_must_be_inside_the_same_locator_slice() -> None:
    result = _generate(cross_locator_quote_result())

    # The quote exists in the current transcript, but not in the p/t slice
    # attached to this candidate.  It is therefore not an EXPLICIT signal.
    assert result.payload.professor_emphasis[0].explicitness is Explicitness.INFERRED


def test_contradictory_in_slice_quote_is_not_explicit_on_partial_lexical_overlap() -> None:
    result = _generate_attribution_case(
        claim="시험에 나온다",
        quote="시험에 나오지 않는다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_inserted_korean_negation_cannot_make_a_quote_explicit() -> None:
    result = _generate_attribution_case(
        claim="시험에 나온다",
        quote="시험에 안 나온다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_attached_korean_negation_cannot_make_a_quote_explicit() -> None:
    result = _generate_attribution_case(
        claim="이 내용은 중간고사 시험에 나온다",
        quote="이 내용은 중간고사 시험에 안나온다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_attached_mot_negation_cannot_make_a_quote_explicit() -> None:
    result = _generate_attribution_case(
        claim="이 작업은 수업에서 직접 한다",
        quote="이 작업은 수업에서 직접 못한다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_attached_polite_korean_negation_cannot_make_a_quote_explicit() -> None:
    # Polite (non-다) conjugations must be caught too: stripping 안 from
    # "안해요" leaves "해요", which appears positively in the claim.
    result = _generate_attribution_case(
        claim="이 작업은 수업에서 직접 해요",
        quote="이 작업은 수업에서 직접 안해요",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_attached_polite_mot_negation_cannot_make_a_quote_explicit() -> None:
    result = _generate_attribution_case(
        claim="이 내용은 중간고사 시험에 나와요",
        quote="이 내용은 중간고사 시험에 못나와요",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_an_morpheme_noun_does_not_downgrade_positive_covering_quote() -> None:
    result = _generate_attribution_case(
        claim="안전 수칙은 중간고사 시험에 나온다",
        quote="수칙은 중간고사 시험에 나온다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.EXPLICIT


def test_matching_korean_negation_can_still_be_explicit() -> None:
    result = _generate_attribution_case(
        claim="시험에 나오지 않는다",
        quote="시험에 나오지 않는다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.EXPLICIT


def test_inserted_english_negation_cannot_make_a_quote_explicit() -> None:
    result = _generate_attribution_case(
        claim="will be on the exam",
        quote="will not be on the exam",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_unrelated_in_slice_quote_sharing_one_word_is_not_explicit() -> None:
    result = _generate_attribution_case(
        claim="시험 나온다",
        quote="시험 관련 범위",
    )

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED


def test_verbatim_quote_covering_claim_content_tokens_is_explicit() -> None:
    result = _generate_attribution_case(
        claim="시험에 나온다",
        quote="교수님은 시험에 나온다",
    )

    assert result.payload.topics[0].explicitness is Explicitness.EXPLICIT


def test_ungrounded_candidates_are_dropped_and_never_rescued_as_inferred() -> None:
    result = _generate(ungrounded_result())

    assert result.status is ProcessingStatus.NEEDS_REVIEW
    assert result.produced_count == 0
    assert result.dropped_count == 1
    assert "MISSING_EVIDENCE" in result.drop_reasons[0]
    assert result.completeness["summary"] == "omitted_or_failed"


def test_false_explicit_claim_without_quote_is_downgraded() -> None:
    bad = session_result(explicitness="EXPLICIT")
    # The frozen result is immutable, so construct a replacement explicitly.
    from uls.adapters.llm.base import LLMEnrichmentResult

    replacement = LLMEnrichmentResult(
        output=bad.output,
        evidence={
            kind: [{"locator": "COMP319-S05:t00:00:01"}]
            for kind in (
                "summary",
                "topics",
                "professor_emphasis",
                "professor_examples",
                "exam_signals",
                "likely_confusions",
            )
        },
        confidence=bad.confidence,
        classification=bad.classification,
        provider_provenance=bad.provider_provenance,
        based_on=bad.based_on,
        explicitness="EXPLICIT",
    )
    result = _generate(replacement)

    assert result.payload.topics[0].explicitness is Explicitness.INFERRED
    assert result.payload.professor_emphasis[0].explicitness is Explicitness.INFERRED


def test_one_invalid_candidate_does_not_drop_a_valid_sibling() -> None:
    good = session_result(include=("summary", "topics", "professor_emphasis", "professor_examples", "exam_signals", "likely_confusions"))
    good_output = dict(good.output)
    good_evidence = dict(good.evidence)
    good_output["topics"] = [
        good_output["topics"][0],
        {"content": "unsupported topic", "symbolic_hint": "unsupported"},
    ]
    good_evidence["topics"] = [
        good_evidence["topics"][0],
        {"locator": "COMP319-S05:t99:59:59", "quote": "unsupported"},
    ]
    from uls.adapters.llm.base import LLMEnrichmentResult

    result = _generate(
        LLMEnrichmentResult(
            output=good_output,
            evidence=good_evidence,
            confidence=good.confidence,
            classification=good.classification,
            provider_provenance=good.provider_provenance,
            based_on=good.based_on,
        )
    )

    assert result.payload.topics and result.produced_count == 6
    assert result.dropped_count == 1
    assert result.status is ProcessingStatus.READY


def test_input_fingerprint_gate_runs_before_the_adapter() -> None:
    adapter = FakeLLMAdapter(session_result=session_result())
    with pytest.raises(SourcePartialError):
        generate_session_enrichment(
            _transcript(),
            adapter,
            SourceFingerprint(2, "transcript-v2"),
            entity_id="COMP319-S05",
        )

    assert adapter.calls == []
