from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from uls.adapters.llm.base import LLMEnrichmentResult
from fake_llm import FakeLLMAdapter, material_result
from uls.domain.enums import Explicitness, ProcessingStatus
from uls.domain.source_ref import SourceFingerprint
from uls.domain.errors import SourcePartialError
from uls.enrichment.material import generate_material_enrichment


FP = SourceFingerprint(1, "material-v1")


def _material() -> str:
    return """---
schema: uls.material.v1
entity_id: COMP319-M03
course_key: 2026-1_COMP319-002
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: material-v1
source_version: 1
processor_version: 1.2.0
normalized_at: '2026-09-05T00:00:00+09:00'
status: ready
---
Page 1
Master theorem
Page 2
Dynamic programming
"""


def test_content_index_requires_and_keeps_typed_page_evidence() -> None:
    result = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=material_result()),
        FP,
        entity_id="COMP319-M03",
    )

    assert result.status is ProcessingStatus.READY
    assert result.payload.content_index[0].evidence[0].locator.kind == "page"
    assert result.payload.content_index[0].explicitness is Explicitness.EXPLICIT
    assert result.record.based_on == FP


def test_content_index_drops_a_page_locator_outside_the_material() -> None:
    bad = material_result(locator="COMP319-M03:p99", quote="missing")
    result = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=bad),
        FP,
        entity_id="COMP319-M03",
    )

    assert result.status is ProcessingStatus.NEEDS_REVIEW
    assert result.produced_count == 0
    assert result.dropped_count == 2
    assert all(state == "omitted_or_failed" for state in result.completeness.values())


def test_material_rejects_transcript_time_locators() -> None:
    bad = material_result(locator="COMP319-M03:t00:00:01", quote="Master theorem")
    result = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=bad),
        FP,
        entity_id="COMP319-M03",
    )

    assert result.status is ProcessingStatus.NEEDS_REVIEW
    assert result.produced_count == 0


def test_material_partial_derivative_is_not_enriched() -> None:
    partial = _material().replace("status: ready", "status: partial")
    adapter = FakeLLMAdapter(material_result=material_result())

    try:
        generate_material_enrichment(partial, adapter, FP, entity_id="COMP319-M03")
    except SourcePartialError:
        pass
    else:
        raise AssertionError("partial material unexpectedly produced enrichment")
    assert adapter.calls == []


def _custom_material_result(*, quote: str) -> LLMEnrichmentResult:
    return LLMEnrichmentResult(
        output={
            "content_index": [
                {"content": "Master theorem", "symbolic_hint": "Master theorem"}
            ],
            "topics": [
                {"content": "Master theorem topic", "symbolic_hint": "Master theorem"}
            ],
        },
        evidence={
            "content_index": [{"locator": "COMP319-M03:p1", "quote": quote}],
            "topics": [{"locator": "COMP319-M03:p1", "quote": quote}],
        },
        confidence=0.9,
        classification="proposal",
    )


def test_evidence_is_bound_to_each_candidate_slot_without_sibling_inheritance() -> None:
    result = LLMEnrichmentResult(
        output={
            "content_index": [
                {"content": "Master theorem", "symbolic_hint": "Master theorem"}
            ],
            "topics": [
                {"content": "Master theorem topic", "symbolic_hint": "Master theorem"},
                {"content": "Dynamic programming topic", "symbolic_hint": "Dynamic programming"},
            ],
        },
        evidence={
            "content_index": [{"locator": "COMP319-M03:p1", "quote": "Master theorem"}],
            "topics": [{"locator": "COMP319-M03:p1", "quote": "Master theorem"}],
        },
        confidence=0.9,
        classification="proposal",
    )

    generated = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=result),
        FP,
        entity_id="COMP319-M03",
    )

    assert generated.status is ProcessingStatus.READY
    assert len(generated.payload.topics) == 1
    assert generated.payload.topics[0].content == "Master theorem topic"
    assert generated.dropped_count == 1
    assert "MISSING_EVIDENCE" in generated.drop_reasons[0]


@pytest.mark.parametrize("quote", ["   ", "Page"])
def test_whitespace_or_trivial_in_slice_quote_cannot_make_signal_explicit(quote: str) -> None:
    generated = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=_custom_material_result(quote=quote)),
        FP,
        entity_id="COMP319-M03",
    )

    signal = generated.payload.content_index[0]
    assert generated.status is ProcessingStatus.READY
    assert signal.explicitness is Explicitness.INFERRED
    assert signal.evidence[0].quote is None


def test_genuine_in_slice_quote_is_explicit_and_is_persisted() -> None:
    generated = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=_custom_material_result(quote="Master theorem")),
        FP,
        entity_id="COMP319-M03",
    )

    signal = generated.payload.content_index[0]
    assert signal.explicitness is Explicitness.EXPLICIT
    assert signal.evidence[0].quote == "Master theorem"


def test_out_of_slice_quote_is_removed_from_persisted_evidence() -> None:
    generated = generate_material_enrichment(
        _material(),
        FakeLLMAdapter(material_result=_custom_material_result(quote="Dynamic programming")),
        FP,
        entity_id="COMP319-M03",
    )

    signal = generated.payload.content_index[0]
    assert signal.explicitness is Explicitness.INFERRED
    assert signal.evidence[0].quote is None
