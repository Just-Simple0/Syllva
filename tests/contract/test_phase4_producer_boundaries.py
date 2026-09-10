"""Permanent regressions for Phase4 producer trust and proposer boundaries."""

from __future__ import annotations

import math
import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import material_derivative, ready_phase4

from uls.adapters.drive.binding import SourceBindingRecord
from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.errors import SourcePartialError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.proposal.material_usage import MaterialUsageProposalProducer


def _two_material_phase4():
    reader, writer, drive, resolver = ready_phase4()
    material = deepcopy(reader.materials["COMP319-M03"])
    material["ID"] = "COMP319-M04"
    material["Name"] = "Second material"
    material["Normalized Source"] = "material-m04"
    material["Original Filename"] = "COMP319-M04.pdf"
    reader.materials["COMP319-M04"] = material
    drive.derived["material-m04"] = material_derivative(
        material_id="COMP319-M04",
        source_hash="material-hash-m04",
    ).replace("file_id: material-m03", "file_id: material-m04")
    drive.fingerprints["material-m04"] = SourceFingerprint(1, "material-hash-m04")
    drive.register_source_binding(
        SourceBindingRecord(
            "COMP319-M04",
            "material-m04",
            SourceRef("google_drive", "material-m04"),
            SourceRef("google_drive", "material-m04"),
        )
    )
    return reader, writer, drive, resolver


def _producer(reader, writer, drive, resolver, proposer, **limits):
    return MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        proposer,
        source_binding_resolver=resolver,
        config=UlsConfig(),
        **limits,
    )


def test_request_budget_is_shared_fairly_across_session_and_materials() -> None:
    reader, writer, drive, resolver = _two_material_phase4()

    class Proposer:
        payload = None

        def propose_material_usage(self, **kwargs):
            self.payload = kwargs
            return []

    proposer = Proposer()
    _producer(
        reader,
        writer,
        drive,
        resolver,
        proposer,
        max_candidate_chunks=3,
        max_chars_per_item=20,
        max_total_chars=30,
    ).propose("COMP319-S05", materials=list(reader.materials.values()))

    assert proposer.payload is not None
    source_chunks = list(proposer.payload["session_chunks"])
    source_chunks.extend(
        chunk
        for material in proposer.payload["materials"]
        for chunk in material["chunks"]
    )
    assert len(source_chunks) <= 3
    assert sum(len(chunk.content) for chunk in source_chunks) <= 30
    assert proposer.payload["session_chunks"]
    assert {item["material_id"] for item in proposer.payload["materials"]} == {
        "COMP319-M03",
        "COMP319-M04",
    }


def test_huge_graph_metadata_and_usage_rows_are_bounded_before_proposer() -> None:
    reader, writer, drive, resolver = ready_phase4()
    reader.sessions["COMP319-S05"]["Name"] = "session-name-" + "x" * 10_000
    reader.materials["COMP319-M03"]["Name"] = "material-name-" + "y" * 10_000
    usage = reader.material_usage["COMP319-S05"][0]
    usage["ID"] = "usage-id-" + "z" * 10_000
    usage["Role"] = "Primary"
    usage["Session"] = {"relation": [{"id": "COMP319-S05", "label": "q" * 10_000}]}

    class Proposer:
        payload = None

        def propose_material_usage(self, **kwargs):
            self.payload = kwargs
            return []

    proposer = Proposer()
    _producer(
        reader,
        writer,
        drive,
        resolver,
        proposer,
        max_chars_per_item=20,
        max_total_chars=30,
    ).propose("COMP319-S05")

    assert proposer.payload is not None
    assert len(proposer.payload["session"]["Name"]) <= 256
    material_payload = proposer.payload["materials"][0]["material"]
    assert len(material_payload["Name"]) <= 256
    usage_payload = proposer.payload["existing_usages"][0]
    assert len(usage_payload["ID"]) <= 128
    assert len(usage_payload["Role"]) <= 256
    assert usage_payload["Session"] == "COMP319-S05"


@pytest.mark.parametrize(
    ("candidate_range", "evidence", "max_candidate_chunks", "expected_reason"),
    [
        (
            (1, 1),
            {"locator": "COMP319-M03:p2", "quote": "Round robin examples"},
            4,
            "proposed Material range",
        ),
        (
            (2, 2),
            {"locator": "COMP319-M03:p2", "quote": "Round robin examples"},
            2,
            "supplied Material slice",
        ),
    ],
)
def test_material_evidence_must_be_in_candidate_range_and_sent_slice(
    candidate_range,
    evidence,
    max_candidate_chunks,
    expected_reason,
) -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Primary",
                    "start_page": candidate_range[0],
                    "end_page": candidate_range[1],
                    "evidence": [evidence],
                }
            ]

    result = _producer(
        reader,
        writer,
        drive,
        resolver,
        Proposer(),
        max_candidate_chunks=max_candidate_chunks,
        max_chars_per_item=200,
        max_total_chars=1_000,
    ).propose("COMP319-S05")

    assert result.proposals == ()
    assert result.skipped
    assert expected_reason in result.skipped[0]["reason"]
    assert writer.queue == {}
    assert len(reader.get_material_usage("COMP319-S05")) == 1


def test_disjoint_candidates_persist_independently_after_owned_usage_creation() -> None:
    reader, writer, drive, resolver = _two_material_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Primary",
                    "start_page": 1,
                    "end_page": 1,
                    "review_reason": "review first range",
                },
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M04",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                    "review_reason": "review second material",
                },
            ]

    result = _producer(reader, writer, drive, resolver, Proposer()).propose(
        "COMP319-S05",
        materials=list(reader.materials.values()),
    )

    assert len(result.proposals) == 2
    assert len(result.created_usage_ids) == 2
    assert len(writer.queue_rows) == 2
    assert len(reader.get_material_usage("COMP319-S05")) == 3


def test_external_usage_mutation_during_proposer_is_not_adopted_as_new_baseline() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class MutatingProposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            reader.material_usage["COMP319-S05"][0]["End Page"] = 1
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": 1,
                    "end_page": 1,
                    "review_reason": "human review required",
                }
            ]

    result = _producer(reader, writer, drive, resolver, MutatingProposer()).propose(
        "COMP319-S05"
    )

    assert result.proposals == ()
    assert result.skipped
    assert "Material Usage rows changed" in result.skipped[0]["reason"]
    assert writer.queue == {}
    assert len(reader.get_material_usage("COMP319-S05")) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("confidence", 2.0), ("review_reason", 123)],
)
def test_new_usage_validates_queue_semantics_before_usage_creation(field, value) -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                    field: value,
                }
            ]

    proposer = Proposer()
    result = _producer(reader, writer, drive, resolver, proposer).propose("COMP319-S05")

    assert proposer.calls == 1
    assert result.proposals == ()
    assert result.skipped
    assert writer.queue_rows == []
    assert len(reader.get_material_usage("COMP319-S05")) == 1


@pytest.mark.parametrize(
    ("start_page", "end_page"),
    [
        (2.0, 2.0),
        (True, 2),
        ("2", "2"),
        (1.5, 2),
        (math.inf, 2),
        (math.nan, 2),
    ],
)
def test_model_page_bounds_are_strict_and_never_create_usage(start_page, end_page) -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": start_page,
                    "end_page": end_page,
                    "review_reason": "human review required",
                }
            ]

    result = _producer(reader, writer, drive, resolver, Proposer()).propose("COMP319-S05")

    assert result.proposals == ()
    assert result.skipped
    assert writer.queue_rows == []
    assert len(reader.get_material_usage("COMP319-S05")) == 1


def test_retrieval_budgets_are_producer_defaults_when_not_overridden() -> None:
    reader, writer, drive, resolver = _two_material_phase4()
    config = UlsConfig(
        retrieval=RetrievalCfg(
            max_candidate_entities=1,
            max_candidate_chunks=2,
            max_chars_per_item=3,
            max_total_chars=4,
        )
    )

    class Proposer:
        payload = None

        def propose_material_usage(self, **kwargs):
            self.payload = kwargs
            return []

    proposer = Proposer()
    MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        proposer,
        source_binding_resolver=resolver,
        config=config,
    ).propose("COMP319-S05", materials=list(reader.materials.values()))

    assert proposer.payload is not None
    chunks = list(proposer.payload["session_chunks"])
    chunks.extend(
        chunk
        for material in proposer.payload["materials"]
        for chunk in material["chunks"]
    )
    assert len(proposer.payload["materials"]) == 1
    assert len(chunks) <= 2
    assert all(len(chunk.content) <= 3 for chunk in chunks)
    assert sum(len(chunk.content) for chunk in chunks) <= 4


def _rebind_material_pointer(
    reader,
    drive,
    *,
    pointer: str,
    source_ref: SourceRef,
    source_hash: str,
) -> None:
    reader.materials["COMP319-M03"]["Normalized Source"] = pointer
    drive.derived[pointer] = material_derivative(
        material_id="COMP319-M03",
        source_hash=source_hash,
    ).replace("file_id: material-m03", f"file_id: {source_ref.file_id}")
    drive.fingerprints[source_ref.file_id] = SourceFingerprint(1, source_hash)
    drive.register_source_binding(
        SourceBindingRecord(
            "COMP319-M03",
            pointer,
            SourceRef(source_ref.provider, source_ref.file_id, source_ref.web_url),
            source_ref,
        )
    )


def test_supplied_stale_material_fails_before_any_source_read_or_proposer_call() -> None:
    reader, writer, drive, resolver = ready_phase4()
    stale = deepcopy(reader.materials["COMP319-M03"])
    _rebind_material_pointer(
        reader,
        drive,
        pointer="material-current-b",
        source_ref=SourceRef("google_drive", "material-current-b"),
        source_hash="material-hash-b",
    )

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            return []

    proposer = Proposer()
    with pytest.raises(SourcePartialError):
        _producer(reader, writer, drive, resolver, proposer).propose(
            "COMP319-S05", materials=[stale]
        )

    assert proposer.calls == 0
    assert drive.events == []
    assert writer.queue_rows == []


def test_supplied_current_material_is_accepted_after_identity_validation() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            return []

    proposer = Proposer()
    _producer(reader, writer, drive, resolver, proposer).propose(
        "COMP319-S05", materials=[deepcopy(reader.materials["COMP319-M03"])]
    )

    assert proposer.calls == 1


def test_navigational_source_pointer_change_with_same_canonical_identity_survives() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            _rebind_material_pointer(
                reader,
                drive,
                pointer="https://drive.example/material-b",
                source_ref=SourceRef(
                    "google_drive",
                    "material-m03",
                    "https://drive.example/material-b",
                ),
                source_hash="material-hash-v1",
            )
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                    "review_reason": "human review required",
                }
            ]

    result = _producer(reader, writer, drive, resolver, Proposer()).propose("COMP319-S05")

    assert len(result.proposals) == 1
    assert len(reader.get_material_usage("COMP319-S05")) == 2


def test_different_canonical_source_pointer_fails_even_when_old_source_is_readable() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            _rebind_material_pointer(
                reader,
                drive,
                pointer="material-different-b",
                source_ref=SourceRef("google_drive", "material-different-b"),
                source_hash="material-hash-different",
            )
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                    "review_reason": "human review required",
                }
            ]

    proposer = Proposer()
    result = _producer(reader, writer, drive, resolver, proposer).propose("COMP319-S05")

    assert proposer.calls == 1
    assert result.proposals == ()
    assert result.skipped
    assert "trusted Material inputs changed" in result.skipped[0]["reason"]
    assert writer.queue_rows == []
    assert len(reader.get_material_usage("COMP319-S05")) == 1


def test_unrelated_graph_metadata_does_not_cancel_trusted_producer_inputs() -> None:
    reader, writer, drive, resolver = ready_phase4()

    class Proposer:
        def propose_material_usage(self, **kwargs):
            del kwargs
            reader.sessions["COMP319-S05"].update(
                {
                    "Name": "renamed session",
                    "Aliases": "new alias",
                    "Provider Metadata": {"display": "changed"},
                }
            )
            reader.materials["COMP319-M03"].update(
                {
                    "Name": "renamed material",
                    "Aliases": "new material alias",
                    "Original Filename": "renamed.pdf",
                    "Source Folder": "https://drive.example/new-folder",
                }
            )
            return [
                {
                    "operation": "create_usage",
                    "material_id": "COMP319-M03",
                    "role": "Supporting",
                    "start_page": 2,
                    "end_page": 2,
                    "review_reason": "human review required",
                }
            ]

    result = _producer(reader, writer, drive, resolver, Proposer()).propose("COMP319-S05")

    assert len(result.proposals) == 1
    assert len(reader.get_material_usage("COMP319-S05")) == 2


def test_usage_provider_id_never_substitutes_for_missing_frozen_app_id() -> None:
    reader, writer, drive, resolver = ready_phase4()
    usage = reader.material_usage["COMP319-S05"][0]
    usage["id"] = "notion-physical-usage"
    del usage["ID"]

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            return []

    proposer = Proposer()
    result = _producer(reader, writer, drive, resolver, proposer).propose("COMP319-S05")

    assert proposer.calls == 0
    assert result.proposals == ()
    assert result.warnings == ("no valid Material candidates",)
    assert writer.queue_rows == []


def test_material_type_whitespace_is_not_trimmed_into_a_configured_option() -> None:
    reader, writer, drive, resolver = ready_phase4()
    reader.materials["COMP319-M03"]["Type"] = " Lecture Slides "

    class Proposer:
        calls = 0

        def propose_material_usage(self, **kwargs):
            self.calls += 1
            del kwargs
            return []

    proposer = Proposer()
    with pytest.raises(SourceUnavailableError):
        _producer(reader, writer, drive, resolver, proposer).propose("COMP319-S05")

    assert proposer.calls == 0
    assert writer.queue_rows == []
