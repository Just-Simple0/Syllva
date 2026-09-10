"""Rev4 regressions for producer final-read freshness and graph stability."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import material_derivative, ready_phase4, transcript_derivative

from uls.adapters.drive.binding import SourceBindingRecord
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.proposal.material_usage import MaterialUsageProposalProducer


class Proposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_material_usage(self, **_kwargs):
        self.calls += 1
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


def _producer(fixture, proposer: Proposer) -> MaterialUsageProposalProducer:
    reader, writer, drive, resolver = fixture
    return MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        proposer,
        source_binding_resolver=resolver,
    )


def _wrap_second_read(drive, source_file_id: str, mutation):
    original = drive.read_derived
    state = {"reads": 0, "mutated": False}

    def read(source_ref):
        result = original(source_ref)
        key = source_ref.file_id if isinstance(source_ref, SourceRef) else str(source_ref)
        if key == source_file_id:
            state["reads"] += 1
            if state["reads"] == 2:
                mutation()
                state["mutated"] = True
        return result

    drive.read_derived = read
    return state


def _replace_binding(
    drive,
    entity_id: str,
    pointer: str,
    derivative_file_id: str,
    source_file_id: str,
    *,
    derivative_url: str | None = None,
) -> None:
    drive.source_bindings.records = [
        record for record in drive.source_bindings.records if record.entity_id != entity_id
    ]
    drive.register_source_binding(
        SourceBindingRecord(
            entity_id,
            pointer,
            SourceRef("google_drive", derivative_file_id, derivative_url),
            SourceRef("google_drive", source_file_id),
        )
    )


def _advance_session_source(drive) -> None:
    drive.fingerprints["transcript-05"] = SourceFingerprint(2, "transcript-hash-v2")
    drive.derived["transcript-05"] = transcript_derivative(
        source_hash="transcript-hash-v2", source_version=2
    )


def _advance_material_source(drive) -> None:
    drive.fingerprints["material-m03"] = SourceFingerprint(2, "material-hash-v2")
    drive.derived["material-m03"] = material_derivative(
        source_hash="material-hash-v2"
    )


@pytest.mark.parametrize(
    "source_file_id",
    [
        pytest.param("transcript-05", id="session-fingerprint-during-final-session-read"),
        pytest.param("material-m03", id="material-fingerprint-during-final-material-read"),
    ],
)
def test_final_body_read_fingerprint_change_prevents_all_writes(source_file_id: str) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    proposer = Proposer()
    if source_file_id == "transcript-05":
        mutation = lambda: _advance_session_source(drive)
    else:
        mutation = lambda: _advance_material_source(drive)
    # Keep the mutation on the corresponding final reread.  The initial read
    # is count one; count two is the body read immediately before persistence.
    read_state = _wrap_second_read(drive, source_file_id, mutation)

    result = _producer(fixture, proposer).propose("COMP319-S05")

    assert read_state == {"reads": 2, "mutated": True}
    assert proposer.calls == 1
    assert not result.proposals
    assert not result.created_usage_ids
    assert writer.create_calls == 0
    assert writer.update_calls == 0
    assert writer.queue_rows == []
    assert len(reader.get_material_usage("COMP319-S05")) == 1


@pytest.mark.parametrize(
    "mutation_name",
    [
        "session graph source pointer",
        "material graph source pointer",
        "course identity",
        "material type",
        "trusted material source rebind",
        "session fingerprint while reading material",
    ],
)
def test_graph_or_dependency_change_during_final_material_read_prevents_writes(
    mutation_name: str,
) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture

    def mutate() -> None:
        if mutation_name == "session graph source pointer":
            pointer = "transcript-rebound"
            reader.sessions["COMP319-S05"]["Normalized Transcript"] = pointer
            drive.fingerprints["transcript-rebound-origin"] = SourceFingerprint(
                1, "transcript-hash-v1"
            )
            drive.derived[pointer] = transcript_derivative().to_markdown().replace(
                "file_id: transcript-05", "file_id: transcript-rebound-origin"
            )
            _replace_binding(
                drive,
                "COMP319-S05",
                pointer,
                pointer,
                "transcript-rebound-origin",
            )
        elif mutation_name == "material graph source pointer":
            pointer = "material-rebound"
            reader.materials["COMP319-M03"]["Normalized Source"] = pointer
            drive.fingerprints["material-rebound-origin"] = SourceFingerprint(
                1, "material-hash-v1"
            )
            drive.derived[pointer] = material_derivative().replace(
                "file_id: material-m03", "file_id: material-rebound-origin"
            )
            _replace_binding(
                drive,
                "COMP319-M03",
                pointer,
                pointer,
                "material-rebound-origin",
            )
        elif mutation_name == "course identity":
            reader.courses["course-page-1"]["Course Key"] = "2026-2_COMP319-002"
            reader.courses["course-page-1"]["Semester"] = "2026-2"
        elif mutation_name == "material type":
            reader.materials["COMP319-M03"]["Type"] = "Textbook"
        elif mutation_name == "trusted material source rebind":
            drive.fingerprints["material-origin-v2"] = SourceFingerprint(
                1, "material-hash-v1"
            )
            drive.derived["material-m03"] = material_derivative().replace(
                "file_id: material-m03", "file_id: material-origin-v2"
            )
            _replace_binding(
                drive,
                "COMP319-M03",
                "material-m03",
                "material-m03",
                "material-origin-v2",
            )
        else:
            _advance_session_source(drive)

    # Material's second read is the final material body read.  Session
    # mutation here proves the post-read check covers the other dependency.
    read_state = _wrap_second_read(drive, "material-m03", mutate)
    proposer = Proposer()
    result = _producer(fixture, proposer).propose("COMP319-S05")

    assert read_state == {"reads": 2, "mutated": True}
    assert proposer.calls == 1
    assert not result.proposals
    assert not result.created_usage_ids
    assert writer.create_calls == 0
    assert writer.update_calls == 0
    assert writer.queue_rows == []
    assert len(reader.get_material_usage("COMP319-S05")) == 1


@pytest.mark.parametrize("entity", ["session", "material"])
def test_navigation_only_pointer_change_during_final_body_read_succeeds(entity: str) -> None:
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    if entity == "session":
        source_file_id = "transcript-05"
        pointer = "https://drive.google.com/file/d/transcript-05/preview?usp=sharing"
        entity_id = "COMP319-S05"

        def navigate_after_final_read() -> None:
            reader.sessions[entity_id]["Normalized Transcript"] = pointer
            _replace_binding(
                drive,
                entity_id,
                pointer,
                source_file_id,
                source_file_id,
                derivative_url="https://drive.google.com/file/d/transcript-05/preview",
            )
    else:
        source_file_id = "material-m03"
        pointer = "https://drive.google.com/file/d/material-m03/view?usp=sharing"
        entity_id = "COMP319-M03"

        def navigate_after_final_read() -> None:
            reader.materials[entity_id]["Normalized Source"] = pointer
            _replace_binding(
                drive,
                entity_id,
                pointer,
                source_file_id,
                source_file_id,
                derivative_url="https://drive.google.com/file/d/material-m03/view",
            )

    read_state = _wrap_second_read(drive, source_file_id, navigate_after_final_read)
    proposer = Proposer()
    result = _producer(fixture, proposer).propose("COMP319-S05")

    assert read_state == {"reads": 2, "mutated": True}
    assert len(result.proposals) == 1
    assert len(result.created_usage_ids) == 1
    assert writer.create_calls == 2
