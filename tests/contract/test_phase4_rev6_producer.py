"""Initial body reads must finish with a current batch before model disclosure."""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import ready_phase4

from uls.adapters.drive.binding import SourceBindingRecord
from uls.domain.errors import SourcePartialError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.proposal.material_usage import MaterialUsageProposalProducer


class Proposer:
    def __init__(self):
        self.calls = 0

    def propose_material_usage(self, **kwargs):
        self.calls += 1
        return []


def _run(fixture, proposer, material_ids=None):
    reader, writer, drive, resolver = fixture
    return MaterialUsageProposalProducer(
        reader,
        drive,
        writer,
        proposer,
        source_binding_resolver=resolver,
    ).propose("COMP319-S05", material_ids=material_ids)


def _during_read(drive, file_id, mutation):
    original = drive.read_derived
    reads = []

    def read(ref):
        value = original(ref)
        reads.append(ref.file_id)
        if ref.file_id == file_id:
            mutation()
        return value

    drive.read_derived = read
    return reads


@pytest.mark.parametrize(
    "entity,change",
    [
        ("session", "fingerprint"),
        ("session", "source"),
        ("session", "course"),
        ("session", "id"),
        ("session", "course_relation"),
        ("session", "binding"),
        ("material", "fingerprint"),
        ("material", "source"),
        ("material", "type"),
        ("material", "id"),
        ("material", "course_relation"),
        ("material", "binding"),
    ],
)
def test_initial_material_read_drift_denies_before_model(entity, change):
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    proposer = Proposer()
    record = (
        reader.sessions["COMP319-S05"] if entity == "session" else reader.materials["COMP319-M03"]
    )

    def mutate():
        if change == "fingerprint":
            file_id = "transcript-05" if entity == "session" else "material-m03"
            drive.fingerprints[file_id] = SourceFingerprint(2, "changed")
        elif change == "source":
            record["Normalized Transcript" if entity == "session" else "Normalized Source"] = (
                "unregistered"
            )
        elif change == "course":
            reader.courses["course-page-1"]["Course Key"] = "2026-2_COMP319-002"
        elif change == "course_relation":
            other_course = deepcopy(reader.courses["course-page-1"])
            other_course["id"] = "course-page-2"
            reader.courses["course-page-2"] = other_course
            record["Course"] = {"relation": [{"id": "course-page-2"}]}
        elif change == "binding":
            entity_id = "COMP319-S05" if entity == "session" else "COMP319-M03"
            file_id = "transcript-05" if entity == "session" else "material-m03"
            drive.source_bindings.records = [
                row for row in drive.source_bindings.records if row.entity_id != entity_id
            ]
            drive.register_source_binding(
                SourceBindingRecord(
                    entity_id,
                    file_id,
                    SourceRef("google_drive", file_id),
                    SourceRef("google_drive", "rebound-origin"),
                )
            )
            drive.fingerprints["rebound-origin"] = drive.fingerprints[file_id]
        elif change == "type":
            record["Type"] = "Professor Notes"
        else:
            record["ID"] = "COMP319-S06" if entity == "session" else "COMP319-M04"

    _during_read(drive, "material-m03", mutate)
    try:
        _run(fixture, proposer)
    except (SourcePartialError, SourceUnavailableError):
        pass
    assert proposer.calls == 0
    assert writer.create_calls == writer.update_calls == 0


@pytest.mark.parametrize(
    "changed_material,trigger",
    [
        ("material-m03", "material-m04"),
        ("material-m04", "material-m03"),
    ],
)
def test_cross_candidate_read_drift_denies_complete_batch(changed_material, trigger):
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    other = deepcopy(reader.materials["COMP319-M03"])
    other.update({"ID": "COMP319-M04", "Normalized Source": "material-m04"})
    reader.materials["COMP319-M04"] = other
    drive.derived["material-m04"] = (
        drive.derived["material-m03"]
        .replace("COMP319-M03", "COMP319-M04")
        .replace("material-m03", "material-m04")
    )
    drive.fingerprints["material-m04"] = SourceFingerprint(1, "material-hash-v1")
    drive.register_source_binding(
        SourceBindingRecord(
            "COMP319-M04",
            "material-m04",
            SourceRef("google_drive", "material-m04"),
            SourceRef("google_drive", "material-m04"),
        )
    )
    proposer = Proposer()
    _during_read(
        drive,
        trigger,
        lambda: drive.fingerprints.update({changed_material: SourceFingerprint(2, "changed")}),
    )
    try:
        _run(fixture, proposer, ["COMP319-M03", "COMP319-M04"])
    except (SourcePartialError, SourceUnavailableError):
        pass
    assert proposer.calls == 0
    assert writer.create_calls == writer.update_calls == 0


@pytest.mark.parametrize("entity", ["session", "material"])
def test_navigation_only_change_reaches_model_without_extra_body_reads(entity):
    fixture = ready_phase4()
    reader, writer, drive, _ = fixture
    proposer = Proposer()
    record = (
        reader.sessions["COMP319-S05"] if entity == "session" else reader.materials["COMP319-M03"]
    )
    key = "Normalized Transcript" if entity == "session" else "Normalized Source"
    file_id = "transcript-05" if entity == "session" else "material-m03"
    reads = _during_read(
        drive,
        "material-m03",
        lambda: record.update({key: f"https://drive.google.com/file/d/{file_id}/view"}),
    )
    _run(fixture, proposer)
    assert proposer.calls == 1
    assert reads == ["transcript-05", "material-m03"]
    assert writer.create_calls == writer.update_calls == 0
