"""Focused regressions for the Phase 4 retrieval/domain review findings."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from phase4 import ready_phase4

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.config.schema import UlsConfig
from uls.config.validation import validate_config
from uls.domain.course_identity import course_key_of, validate_course_record
from uls.domain.errors import LocatorNotAllowedError, SourcePartialError, SourceUnavailableError
from uls.domain.models import PageLocator
from uls.domain.page_range import PageRangeStatus, parse_page_range
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.chunking import page_marker_index
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.freshness import revalidate_locator
from uls.retrieval.resolver import SessionResolver
from uls.retrieval.scope import material_usage_scopes, usage_app_id


def _engine(reader, drive, *, config: UlsConfig | None = None) -> RetrievalEngine:
    return RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config or UlsConfig(),
        source_binding_resolver=ValidatedSourceBindingResolver(drive),
    )


def test_source_binding_resolver_is_explicit_and_never_made_from_drive() -> None:
    reader, _, drive, _ = ready_phase4()
    engine = RetrievalEngine(reader, drive, None, MemoryEphemeralStore(), UlsConfig())

    with pytest.raises(SourceUnavailableError):
        engine.get_session_context("COMP319-S05")
    assert drive.events == []


def test_page_range_domain_is_strict_but_stored_integer_float_is_boundary_normalized() -> None:
    assert parse_page_range(1.0, 2.0).status is PageRangeStatus.INVALID
    stored = {
        "ID": "MU:stored-float",
        "Session": "COMP319-S05",
        "Material": "COMP319-M03",
        "Role": "Primary",
        "Verified": True,
        "Start Page": 1.0,
        "End Page": 2.0,
    }
    scopes = material_usage_scopes([stored])
    assert len(scopes) == 1
    assert scopes[0].range.start_page == 1
    assert scopes[0].range.end_page == 2


def test_usage_app_id_requires_the_frozen_property_and_preserves_physical_duplicates() -> None:
    missing = {
        "id": "provider-page-1",
        "properties": {
            "Session": "COMP319-S05",
            "Material": "COMP319-M03",
            "Role": "Primary",
            "Verified": True,
        },
    }
    assert usage_app_id(missing) is None
    assert material_usage_scopes([missing]) == []

    first = {
        "id": "provider-page-1",
        "ID": "MU:same",
        "Session": "COMP319-S05",
        "Material": "COMP319-M03",
        "Role": "Primary",
        "Verified": True,
    }
    second = {**first, "id": "provider-page-2"}
    assert [scope.usage_id for scope in material_usage_scopes([first, second])] == [
        "MU:same",
        "MU:same",
    ]


def test_course_identity_preserves_raw_key_and_component_text() -> None:
    padded = {
        "id": "course-page-1",
        "Course Key": " 2026-1_COMP319-002 ",
        "Code": "COMP319",
        "Section": "002",
        "Semester": "2026-1",
    }
    assert course_key_of(padded) == " 2026-1_COMP319-002 "
    assert validate_course_record(padded, "course-page-1") is None

    padded_component = {**padded, "Course Key": "2026-1_COMP319-002", "Code": " COMP319"}
    assert validate_course_record(padded_component, "course-page-1") is None


def test_material_stale_revalidation_never_uses_timestamp_text_as_page_evidence() -> None:
    unmarked = {
        "front_matter": {"schema": "uls.material.v1", "entity_id": "COMP319-M03"},
        "body": "[00:00:01] target topic",
    }
    assert revalidate_locator({"topic": "target topic"}, unmarked) is None
    with_explicit_marks = {**unmarked, "marks": [{"start_seconds": 1, "char_offset": 0}]}
    assert revalidate_locator({"topic": "target topic"}, with_explicit_marks) is None

    marked = {
        "front_matter": {"schema": "uls.material.v1", "entity_id": "COMP319-M03"},
        "body": "Page 1\n[00:00:01] target topic\nPage 2\nnext",
    }
    locator = revalidate_locator({"topic": "target topic"}, marked)
    assert locator == PageLocator("COMP319-M03", 1, 1)
    assert page_marker_index("Page 1\nA\nPage 1\nB") is None
    assert page_marker_index("Page 1\nA\nPage 3\nB") is None


def test_each_invalid_usage_is_warned_and_out_of_index_usage_is_not_provisional_included() -> None:
    reader, _, drive, _ = ready_phase4()
    reader.material_usage["COMP319-S05"].extend(
        [
            {
                "id": "physical-only",
                "Session": "COMP319-S05",
                "Material ID": "COMP319-M03",
                "Role": "Primary",
                "Verified": True,
            },
            {
                "ID": "MU:bad-role",
                "Session": "COMP319-S05",
                "Material ID": "COMP319-M03",
                "Role": "Unknown",
                "Verified": True,
            },
            {
                "ID": "MU:one-sided",
                "Session": "COMP319-S05",
                "Material ID": "COMP319-M03",
                "Role": "Primary",
                "Verified": True,
                "Start Page": 1,
            },
        ]
    )
    package = _engine(reader, drive).get_session_context("COMP319-S05")
    messages = [warning["message"] for warning in package.warnings if warning["code"] == "SOURCE_UNAVAILABLE"]
    assert any("physical-only" not in message and "unknown" in message.lower() for message in messages)
    assert any("MU:bad-role" in message for message in messages)
    assert any("MU:one-sided" in message for message in messages)

    reader, _, drive, _ = ready_phase4()
    usage = reader.material_usage["COMP319-S05"][0]
    usage.update({"Start Page": 3, "End Page": 3, "Verified": False})
    package = _engine(reader, drive).get_session_context(
        "COMP319-S05", include_provisional=True
    )
    assert not any(item.entity_id == "COMP319-M03" for item in package.sources)
    assert package.scope["provisional"] is False
    assert any(
        warning["code"] == "SOURCE_PARTIAL" and "justified page evidence" in warning["message"]
        for warning in package.warnings
    )


def test_role_and_material_type_are_raw_exact_values() -> None:
    reader, _, drive, _ = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Role"] = " Primary "
    package = _engine(reader, drive).get_session_context("COMP319-S05")
    assert not any(item.entity_id == "COMP319-M03" for item in package.sources)
    assert any(warning["code"] == "SOURCE_UNAVAILABLE" for warning in package.warnings)

    reader, _, drive, _ = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = _engine(reader, drive)
    package = engine.get_session_context("COMP319-S05")
    material_item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
    reader.materials["COMP319-M03"]["Type"] = " Lecture Slides "
    with pytest.raises((LocatorNotAllowedError, SourcePartialError, SourceUnavailableError)):
        engine.get_source_chunk(package.context_id, str(material_item.locator))


@pytest.mark.parametrize("direct", [True, False])
def test_final_derivative_read_rechecks_the_issued_course_key(direct: bool) -> None:
    reader, _, drive, _ = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = _engine(reader, drive)
    package = (
        engine.get_material_context("COMP319-M03")
        if direct
        else engine.get_session_context("COMP319-S05")
    )
    material_item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
    drive.derived["material-m03"] = drive.derived["material-m03"].replace(
        "course_key: 2026-1_COMP319-002", "course_key: 2026-2_COMP319-002"
    )
    with pytest.raises(SourcePartialError):
        engine.get_source_chunk(package.context_id, str(material_item.locator))


def test_followup_revalidates_the_usage_session_relation_exactly() -> None:
    reader, _, drive, _ = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = _engine(reader, drive)
    package = engine.get_session_context("COMP319-S05")
    material_item = next(item for item in package.sources if item.entity_id == "COMP319-M03")
    reader.material_usage["COMP319-S05"][0]["Session"] = {
        "relation": [{"id": "COMP319-S06"}]
    }
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(material_item.locator))


def test_material_type_mapping_labels_direct_supplemental_material_without_promoting_session_usage() -> None:
    reader, _, drive, _ = ready_phase4(material_type="Textbook")
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = _engine(reader, drive)
    direct = engine.get_material_context("COMP319-M03")
    assert direct.sources
    assert all(item.source_class == "supplemental_reference" for item in direct.sources)

    session = engine.get_session_context("COMP319-S05")
    assert not any(item.entity_id == "COMP319-M03" for item in session.sources)
    assert any(
        warning["code"] == "SOURCE_UNAVAILABLE" and "allowed Session source class" in warning["message"]
        for warning in session.warnings
    )


def test_malformed_material_type_mapping_is_a_structured_config_problem() -> None:
    config = UlsConfig()
    config.retrieval.material_type_source_class = {"Lecture Slides": []}  # type: ignore[assignment]
    problems = validate_config(config)
    assert any("material_type_source_class values" in problem for problem in problems)


def test_session_resolver_rejects_multiple_or_malformed_course_relations() -> None:
    reader, _, _, _ = ready_phase4()
    resolver = SessionResolver(reader, MemoryEphemeralStore())
    session = reader.sessions["COMP319-S05"]
    course = reader.get_course_by_relation_id("course-page-1")

    session["Course"] = {"relation": [{"id": "course-page-1"}, {"id": "other"}]}
    assert resolver._course_matches(session, course) is False
    session["Course"] = {"relation": [{"id": "course-page-1"}, {}]}
    assert resolver._course_matches(session, course) is False
    session["Course"] = {"relation": [{"id": "missing-course"}]}
    assert resolver._course_matches(session, course) is False
