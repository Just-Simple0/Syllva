"""Material capabilities retain only their current, exact graph basis."""

import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from uls.adapters.drive.binding import SourceBindingRecord
from uls.config.schema import UlsConfig
from uls.domain.errors import LocatorNotAllowedError, LocatorStaleError
from uls.domain.source_ref import SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine


def _ready_context():
    fixture_path = str(Path(__file__).resolve().parents[1] / "fixtures")
    if fixture_path not in sys.path:
        sys.path.insert(0, fixture_path)
    from phase4 import ready_phase4

    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = RetrievalEngine(
        reader, drive, None, MemoryEphemeralStore(), UlsConfig(),
        source_binding_resolver=resolver,
    )
    return reader, drive, engine


@pytest.mark.parametrize(
    "change",
    ["verified", "role", "type", "range", "course_key", "course_page", "source", "duplicate"],
)
def test_changed_material_basis_revokes_an_already_issued_page(change: str) -> None:
    reader, drive, engine = _ready_context()
    package = engine.get_session_context("COMP319-S05")
    assert any(str(item.locator) == "COMP319-M03:p1" for item in package.sources)
    usage = reader.material_usage["COMP319-S05"][0]
    material = reader.materials["COMP319-M03"]
    if change == "verified":
        usage["Verified"] = False
    elif change == "role":
        usage["Role"] = "Supporting"
    elif change == "type":
        # Same authority mapping, different raw Type must still revoke.
        material["Type"] = "Professor Notes"
    elif change == "range":
        # p1 still fits; the entire issued Usage range nevertheless changed.
        usage["End Page"] = 1
    elif change == "course_key":
        course = next(iter(reader.courses.values()))
        course["Course Key"] = "2026-2_COMP319-002"
        course["Semester"] = "2026-2"
    elif change == "course_page":
        course = deepcopy(next(iter(reader.courses.values())))
        course["id"] = "different-course-page"
        reader.courses[course["id"]] = course
        reader.sessions["COMP319-S05"]["Course"] = {
            "relation": [{"id": course["id"]}]
        }
    elif change == "source":
        material["Normalized Source"] = "different-derivative"
        drive.register_source_binding(SourceBindingRecord(
            "COMP319-M03", "different-derivative",
            SourceRef("google_drive", "different-derivative"),
            SourceRef("google_drive", "different-original"),
        ))
    else:
        duplicate = deepcopy(usage)
        duplicate["Role"] = "invalid-role"
        reader.material_usage["COMP319-S05"].append(duplicate)

    with pytest.raises((LocatorNotAllowedError, LocatorStaleError)):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p1")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("invalidate", ["verified", "fingerprint"])
def test_valid_overlapping_material_basis_survives_in_both_orders(
    reverse: bool, invalidate: str,
) -> None:
    reader, _, engine = _ready_context()
    first = reader.material_usage["COMP319-S05"][0]
    second = deepcopy(first)
    second.update({"ID": "MU:overlap", "Start Page": 1, "End Page": 1})
    reader.material_usage["COMP319-S05"].append(second)
    package = engine.get_session_context("COMP319-S05")
    bindings = [
        binding for binding in engine.capabilities.bindings_for(package.context_id)
        if str(binding.locator) == "COMP319-M03:p1"
    ]
    assert len(bindings) == 2
    if invalidate == "verified":
        first["Verified"] = False
    else:
        bindings = [
            replace(binding, source_hash="obsolete-hash")
            if binding.usage_id == first["ID"] else binding
            for binding in bindings
        ]
    if reverse:
        bindings.reverse()
    capability = engine.capabilities.issue(bindings)
    chunk = engine.get_source_chunk(capability.context_id, "COMP319-M03:p1")
    assert str(chunk.locator) == "COMP319-M03:p1"
    assert chunk.fingerprint.source_hash == "material-hash-v1"
    assert "Master theorem" in chunk.content
