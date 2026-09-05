from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.adapters.llm.structured import LLMEnrichmentResult
from uls.domain.enums import Explicitness
from uls.domain.models import PageLocator, TimeLocator
from uls.domain.source_ref import SourceFingerprint
from uls.enrichment.schemas import (
    EnrichmentRecord,
    EnrichmentSignal,
    EvidenceLocator,
    MaterialEnrichmentPayload,
    SessionEnrichmentPayload,
)


def _signal(kind: str = "topics", locator: str = "COMP319-M03:p1") -> EnrichmentSignal:
    return EnrichmentSignal(
        kind=kind,
        content="Master theorem",
        explicitness=Explicitness.EXPLICIT,
        evidence=(EvidenceLocator(locator, "Master theorem"),),
        confidence=0.8,
        symbolic_hint="Master theorem",
    )


def test_evidence_locator_is_parsed_and_canonical() -> None:
    evidence = EvidenceLocator("COMP319-M03:p1-p2", "quote")

    assert isinstance(evidence.locator, PageLocator)
    assert str(evidence.locator) == "COMP319-M03:p1-p2"
    assert evidence.as_dict() == {"locator": "COMP319-M03:p1-p2", "quote": "quote"}


def test_evidence_locator_normalizes_whitespace_only_quotes_to_none() -> None:
    evidence = EvidenceLocator("COMP319-M03:p1", " \n\t ")

    assert evidence.quote is None
    assert evidence.as_dict()["quote"] is None


def test_explicit_signal_rejects_a_trivial_quote() -> None:
    with pytest.raises(ValueError):
        EnrichmentSignal(
            "topics",
            "topic",
            Explicitness.EXPLICIT,
            (EvidenceLocator("COMP319-M03:p1", "이"),),
            0.8,
            "topic",
        )


def test_typed_session_payload_matches_consumer_keys_and_projects_hints() -> None:
    payload = SessionEnrichmentPayload(
        summary=(_signal("summary", "COMP319-S05:t00:00:01"),),
        topics=(_signal("topics", "COMP319-S05:t00:00:01"),),
        professor_emphasis=(_signal("professor_emphasis", "COMP319-S05:t00:00:01"),),
        professor_examples=(_signal("professor_examples", "COMP319-S05:t00:00:01"),),
        exam_signals=(_signal("exam_signals", "COMP319-S05:t00:00:01"),),
        likely_confusions=(_signal("likely_confusions", "COMP319-S05:t00:00:01"),),
    )
    serialized = payload.as_dict()

    assert set(serialized) == {
        "summary",
        "topics",
        "professor_emphasis",
        "professor_examples",
        "exam_signals",
        "likely_confusions",
        "symbolic_hints",
    }
    assert len(serialized["symbolic_hints"]) == 6
    assert serialized["symbolic_hints"][0]["topic"] == "Master theorem"
    assert serialized["symbolic_hints"][0]["locator"] == "COMP319-S05:t00:00:01"
    assert all(
        "source_class" not in item and "freshness" not in item
        for kind in (
            "summary",
            "topics",
            "professor_emphasis",
            "professor_examples",
            "exam_signals",
            "likely_confusions",
        )
        for item in serialized[kind]
    )


def test_material_payload_round_trips_through_record() -> None:
    payload = MaterialEnrichmentPayload(
        content_index=(_signal("content_index"),),
        topics=(_signal("topics"),),
    )
    record = EnrichmentRecord(
        payload=payload,
        based_on_source_version=2,
        based_on_source_hash="material-v2",
        processor_version="1.2.0",
        provider_provenance={"provider": "fake", "model": "unit"},
    )

    restored = EnrichmentRecord.from_mapping(record.as_dict())

    assert isinstance(restored.payload, MaterialEnrichmentPayload)
    assert restored.payload.as_dict() == payload.as_dict()
    assert restored.source_fingerprint == SourceFingerprint(2, "material-v2")
    assert restored.provider_provenance == {"provider": "fake", "model": "unit"}


def test_proposal_fact_and_explicitness_are_independent_axes() -> None:
    proposal_inferred = LLMEnrichmentResult(
        output={},
        confidence=0.5,
        classification="proposal",
        explicitness=Explicitness.INFERRED,
    )
    fact_explicit = LLMEnrichmentResult(
        output={},
        confidence=0.5,
        classification="fact",
        explicitness=Explicitness.EXPLICIT,
    )

    assert proposal_inferred.classification == "proposal"
    assert proposal_inferred.explicitness is Explicitness.INFERRED
    assert fact_explicit.classification == "fact"
    assert fact_explicit.explicitness is Explicitness.EXPLICIT


def test_signal_requires_evidence_and_valid_confidence() -> None:
    with pytest.raises(ValueError):
        EnrichmentSignal(
            "topics",
            "topic",
            Explicitness.INFERRED,
            (),
            0.5,
            "topic",
        )
    with pytest.raises(ValueError):
        EnrichmentSignal(
            "topics",
            "topic",
            Explicitness.INFERRED,
            (EvidenceLocator("COMP319-M03:p1"),),
            1.1,
            "topic",
        )


def test_time_locator_is_typed_not_a_string_prefix() -> None:
    evidence = EvidenceLocator(TimeLocator("COMP319-S05", 10, 20))

    assert evidence.as_dict()["locator"] == "COMP319-S05:t00:00:10-00:00:20"
