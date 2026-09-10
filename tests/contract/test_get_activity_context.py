from __future__ import annotations

import pathlib
import sys
from dataclasses import replace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveReader
from fake_notion import FakeNotionReader
from phase4 import (
    ACTIVITY_NORMALIZED_POINTER,
    ACTIVITY_SOURCE_POINTER,
    COURSE_KEY,
    activity_binding,
    activity_derivative,
    activity_record,
    transcript_derivative,
)

from uls.adapters.drive.binding import SourceBindingRecord, ValidatedSourceBindingResolver
from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.enums import DerivativeStatus
from uls.domain.errors import LocatorNotAllowedError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.normalization.activity import normalize_activity_instructions
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.schemas import context_package_to_dict


def _material_conflict() -> str:
    return f"""---
schema: uls.material.v1
entity_id: COMP319-M03
course_key: {COURSE_KEY}
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: material-hash-v1
source_version: 1
processor_version: 1.2.0
normalized_at: '2026-09-04T00:00:00+09:00'
status: ready
---
Page 1
Professor recommends X for the implementation.
Page 2
Background material.
"""


def _engine(*, record=None, activity_value=None, derivative=None, config=None, material_usage=None):
    record = record or activity_record(
        related_sessions=["COMP319-S05"],
        related_materials=["COMP319-M03"],
    )
    reader = FakeNotionReader(activities=[record], material_usage=material_usage)
    activity_value = derivative or activity_derivative()
    drive = FakeDriveReader(
        derived={
            "activity-normalized-01": activity_value,
            "transcript-05": transcript_derivative(),
            "material-m03": _material_conflict(),
        },
        fingerprints={
            "activity-source-01": SourceFingerprint(1, "activity-source-v1"),
            "transcript-05": SourceFingerprint(1, "transcript-hash-v1"),
            "material-m03": SourceFingerprint(1, "material-hash-v1"),
        },
        bindings=[
            activity_binding(),
            SourceBindingRecord(
                "COMP319-S05",
                "transcript-05",
                SourceRef("google_drive", "transcript-05"),
                SourceRef("google_drive", "transcript-05"),
            ),
            SourceBindingRecord(
                "COMP319-M03",
                "material-m03",
                SourceRef("google_drive", "material-m03"),
                SourceRef("google_drive", "material-m03"),
            ),
        ],
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config or UlsConfig(),
        source_binding_resolver=ValidatedSourceBindingResolver(drive),
    )
    return engine, reader, drive


def test_activity_context_places_official_evidence_first_and_keeps_related_sources_distinct() -> None:
    engine, _, _ = _engine()

    package = engine.get_activity_context("COMP319-A01")
    serialized = context_package_to_dict(package)

    assert package.sources[0].source_class == "official_activity"
    assert any(item.source_class == "professor_transcript" for item in package.sources[1:])
    assert any(item.source_class == "professor_material" for item in package.sources[1:])
    assert "do not use x" in package.sources[0].content.casefold()
    assert "professor recommends x" in " ".join(
        item.content for item in package.sources if item.source_class == "professor_material"
    ).casefold()
    assert package.scope["constraint_conflict_rule"].startswith("official_activity")
    assert package.scope["instruction_coverage"]["complete"] is True
    assert serialized["sources"][0]["source_class"] == "official_activity"
    assert serialized["sources"][0]["constraint_metadata"]["priority"] == "hard"
    assert serialized["scope"]["official_constraint"]["priority"] == "hard"


def test_activity_constraint_metadata_is_typed_and_separate_from_global_provenance() -> None:
    engine, _, _ = _engine()
    package = engine.get_activity_context("COMP319-A01")
    official = package.sources[0]
    constraint = getattr(official, "constraint_metadata")  # noqa: B009

    assert constraint.priority == "hard"
    assert constraint.official_locator_set == ("COMP319-A01:p1", "COMP319-A01:p2")
    assert official.source_class == "official_activity"
    assert official.provenance.source_ref.identity == ("google_drive", "activity-source-01")
    wire = context_package_to_dict(package)
    assert wire["sources"][0]["authority"] == "official_activity_or_exam"
    assert wire["sources"][0]["constraint_metadata"]["official_evidence_set"] == [
        "COMP319-A01:p1",
        "COMP319-A01:p2",
    ]


def test_activity_constraint_metadata_matches_final_query_coverage_per_item() -> None:
    engine, _, _ = _engine()

    package = engine.get_activity_context("COMP319-A01", query="repository")
    coverage = package.scope["instruction_coverage"]
    official = [item for item in package.sources if item.source_class == "official_activity"]

    assert coverage["expected_locators"] == ["COMP319-A01:p1", "COMP319-A01:p2"]
    assert coverage["returned_locators"] == ["COMP319-A01:p2"]
    assert len(official) == 1
    constraint = official[0].constraint_metadata
    assert constraint.official_locator_set == tuple(coverage["expected_locators"])
    assert constraint.official_evidence_set == tuple(coverage["returned_locators"])
    assert "COMP319-A01:p1" not in constraint.official_evidence_set
    serialized = context_package_to_dict(package)
    assert serialized["sources"][0]["constraint_metadata"]["official_evidence_set"] == [
        "COMP319-A01:p2"
    ]


def test_activity_result_metadata_preserves_exact_submission_ref() -> None:
    engine, _, _ = _engine()

    package = engine.get_activity_context("COMP319-A01")

    assert package.entity["result"].submission_ref == "refs/heads/assignment-1"
    assert context_package_to_dict(package)["entity"]["result"]["submission_ref"] == (
        "refs/heads/assignment-1"
    )


def test_missing_pointer_pair_is_incomplete_and_never_official_evidence() -> None:
    record = activity_record(
        instructions_source_url=None,
        normalized_instructions_url=ACTIVITY_NORMALIZED_POINTER,
    )
    engine, _, _ = _engine(record=record)

    package = engine.get_activity_context("COMP319-A01")

    assert all(item.source_class != "official_activity" for item in package.sources)
    assert any(item.get("code") == "INSTRUCTIONS_INCOMPLETE" for item in package.warnings)
    assert package.scope["instruction_coverage"]["complete"] is False


@pytest.mark.parametrize(
    ("source_pointer", "normalized_pointer"),
    [
        (None, ACTIVITY_NORMALIZED_POINTER),
        ("https://drive.google.com/file/d/activity-source-01/view", None),
        (None, None),
    ],
)
def test_incomplete_instruction_pointers_preserve_independent_related_evidence(
    source_pointer: str | None,
    normalized_pointer: str | None,
) -> None:
    record = activity_record(
        related_sessions=["COMP319-S05"],
        related_materials=["COMP319-M03"],
        instructions_source_url=source_pointer,
        normalized_instructions_url=normalized_pointer,
    )
    engine, _, _ = _engine(record=record)

    package = engine.get_activity_context("COMP319-A01")
    source_classes = {item.source_class for item in package.sources}

    assert "official_activity" not in source_classes
    assert "professor_transcript" in source_classes
    assert "professor_material" in source_classes
    assert package.scope["instruction_coverage"]["complete"] is False
    assert any(item.get("code") == "INSTRUCTIONS_INCOMPLETE" for item in package.warnings)


@pytest.mark.parametrize("ref_kind", ["source", "derivative"])
def test_activity_pointer_pair_requires_registered_origin_and_derivative_identity(ref_kind: str) -> None:
    engine, _, drive = _engine()
    original = drive.source_bindings.activity_records[0]
    if ref_kind == "source":
        replacement = activity_binding(
            source_ref=SourceRef("google_drive", "rewired-origin", ACTIVITY_SOURCE_POINTER),
            derivative_ref=original.derivative_ref,
        )
    else:
        replacement = activity_binding(
            source_ref=original.source_ref,
            derivative_ref=SourceRef(
                "google_drive", "rewired-derivative", ACTIVITY_NORMALIZED_POINTER
            ),
        )
    drive.source_bindings.activity_records[0] = replacement

    package = engine.get_activity_context("COMP319-A01")

    assert all(item.source_class != "official_activity" for item in package.sources)
    assert any(item.get("code") == "INSTRUCTIONS_INCOMPLETE" for item in package.warnings)


def test_activity_binding_accepts_registered_provider_file_id_pointers() -> None:
    record = activity_record(
        related_sessions=None,
        related_materials=None,
        instructions_source_url="activity-source-01",
        normalized_instructions_url="activity-normalized-01",
    )
    engine, _, drive = _engine(record=record)
    drive.source_bindings.activity_records[0] = activity_binding(
        instructions_source_url="activity-source-01",
        normalized_instructions_url="activity-normalized-01",
        source_ref=SourceRef("google_drive", "activity-source-01"),
        derivative_ref=SourceRef("google_drive", "activity-normalized-01"),
    )

    package = engine.get_activity_context("COMP319-A01")

    assert package.sources[0].source_class == "official_activity"


@pytest.mark.parametrize("ref_kind", ["origin", "derivative"])
def test_activity_strict_identity_does_not_use_navigational_web_url_fallback(ref_kind: str) -> None:
    arbitrary_pointer = f"https://example.edu/activity/{ref_kind}-navigation"
    if ref_kind == "origin":
        record = activity_record(
            related_sessions=None,
            related_materials=None,
            instructions_source_url=arbitrary_pointer,
            normalized_instructions_url=ACTIVITY_NORMALIZED_POINTER,
        )
        source_ref = SourceRef("google_drive", "unrelated-origin-id", arbitrary_pointer)
        derivative_ref = SourceRef("google_drive", "activity-normalized-01")
    else:
        record = activity_record(
            related_sessions=None,
            related_materials=None,
            instructions_source_url=ACTIVITY_SOURCE_POINTER,
            normalized_instructions_url=arbitrary_pointer,
        )
        source_ref = SourceRef("google_drive", "activity-source-01", ACTIVITY_SOURCE_POINTER)
        derivative_ref = SourceRef("google_drive", "unrelated-derivative-id", arbitrary_pointer)
    engine, _, drive = _engine(record=record)
    drive.source_bindings.activity_records[0] = activity_binding(
        instructions_source_url=record["Instructions Source"],
        normalized_instructions_url=record["Normalized Instructions"],
        source_ref=source_ref,
        derivative_ref=derivative_ref,
    )

    with pytest.raises(SourceUnavailableError, match="not bound"):
        ValidatedSourceBindingResolver(drive).resolve_activity_instructions(
            "COMP319-A01",
            record["Instructions Source"],
            record["Normalized Instructions"],
        )

    package = engine.get_activity_context("COMP319-A01")

    assert all(item.source_class != "official_activity" for item in package.sources)
    assert any(item.get("code") == "INSTRUCTIONS_INCOMPLETE" for item in package.warnings)
    assert not engine.capabilities.bindings_for(package.context_id)


def test_typed_activity_record_must_match_requested_logical_id() -> None:
    engine, reader, _ = _engine()
    typed = replace(engine._get_activity_record("COMP319-A01"), entity_id="COMP319-A99")
    reader.get_activity = lambda _activity_id: typed

    with pytest.raises(SourceUnavailableError, match="does not match"):
        engine.get_activity_context("COMP319-A01")


def test_typed_activity_record_rechecks_the_current_course_identity() -> None:
    engine, reader, _ = _engine()
    typed = engine._get_activity_record("COMP319-A01")
    reader.get_activity = lambda _activity_id: typed
    reader.courses["course-page-1"]["Course Key"] = "2026-1_COMP319-003"
    reader.courses["course-page-1"]["Section"] = "003"

    with pytest.raises(SourceUnavailableError, match="stale"):
        engine.get_activity_context("COMP319-A01")


def test_activity_accepts_documented_provider_url_and_relation_shapes() -> None:
    record = activity_record(
        related_sessions=None,
        related_materials=None,
    )
    record["Instructions Source"] = {
        "id": "instructions-source-property",
        "type": "url",
        "url": "https://drive.google.com/file/d/activity-source-01/view",
    }
    record["Normalized Instructions"] = {
        "id": "normalized-instructions-property",
        "type": "url",
        "url": ACTIVITY_NORMALIZED_POINTER,
    }
    record["Related Sessions"] = {
        "id": "related-sessions-property",
        "type": "relation",
        "relation": [{"id": "COMP319-S05"}],
        "has_more": False,
    }
    record["Related Materials"] = {
        "id": "related-materials-property",
        "type": "relation",
        "relation": [{"id": "COMP319-M03"}],
        "has_more": False,
    }
    engine, _, _ = _engine(record=record)

    parsed = engine._get_activity_record("COMP319-A01")
    assert parsed is not None
    assert parsed.instructions_source_url == record["Instructions Source"]["url"]
    assert parsed.normalized_instructions_url == ACTIVITY_NORMALIZED_POINTER
    assert parsed.related_session_ids == ("COMP319-S05",)
    assert parsed.related_material_ids == ("COMP319-M03",)
    assert engine.get_activity_context("COMP319-A01").sources[0].source_class == "official_activity"


def test_activity_accepts_documented_title_rich_text_select_and_status_values() -> None:
    record = activity_record(related_sessions=None, related_materials=None)
    record["Name"] = {
        "id": "title-property",
        "type": "title",
        "title": [{"type": "text", "text": {"content": "HW1"}, "plain_text": "HW1"}],
    }
    record["Submission Ref"] = {
        "id": "submission-property",
        "type": "rich_text",
        "rich_text": [
            {
                "type": "text",
                "text": {"content": "refs/heads/assignment-1"},
                "plain_text": "refs/heads/assignment-1",
            }
        ],
    }
    record["Result Type"] = {
        "id": "result-type-property",
        "type": "select",
        "select": {"id": "select-github", "name": "GitHub", "color": "blue"},
    }
    record["Status"] = {
        "id": "status-property",
        "type": "status",
        "status": {"id": "status-submitted", "name": "Submitted", "color": "green"},
    }
    engine, _, _ = _engine(record=record)

    parsed = engine._get_activity_record("COMP319-A01")

    assert parsed is not None
    assert parsed.title == "HW1"
    assert parsed.result.result_type == "GitHub"
    assert parsed.result.submission_ref == "refs/heads/assignment-1"
    assert parsed.result.status == "Submitted"


def test_activity_accepts_documented_date_due_without_suppressing_evidence() -> None:
    record = activity_record(
        related_sessions=["COMP319-S05"],
        related_materials=["COMP319-M03"],
    )
    record["Due"] = {
        "id": "due-property",
        "type": "date",
        "date": {
            "start": "2026-09-30",
            "end": None,
            "time_zone": None,
        },
    }
    engine, _, _ = _engine(record=record)

    parsed = engine._get_activity_record("COMP319-A01")
    package = engine.get_activity_context("COMP319-A01")

    assert parsed is not None
    assert parsed.due_at == "2026-09-30"
    assert package.sources[0].source_class == "official_activity"
    assert any(item.source_class == "professor_transcript" for item in package.sources)
    assert any(item.source_class == "professor_material" for item in package.sources)


def test_activity_accepts_date_range_and_preserves_start_in_scalar_due_field() -> None:
    record = activity_record(related_sessions=None, related_materials=None)
    record["Due"] = {
        "id": "due-property",
        "type": "date",
        "date": {
            "start": "2026-09-30T09:00:00+09:00",
            "end": "2026-09-30T10:00:00+09:00",
            "time_zone": "Asia/Seoul",
        },
    }
    engine, _, _ = _engine(record=record)

    parsed = engine._get_activity_record("COMP319-A01")

    assert parsed is not None
    assert parsed.due_at == "2026-09-30T09:00:00+09:00"


def test_activity_accepts_explicit_null_date_due() -> None:
    record = activity_record(related_sessions=None, related_materials=None)
    record["Due"] = {"id": "due-property", "type": "date", "date": None}
    engine, _, _ = _engine(record=record)

    parsed = engine._get_activity_record("COMP319-A01")

    assert parsed is not None
    assert parsed.due_at is None


def test_activity_material_usage_leaf_requires_session_membership_not_direct_material_membership() -> None:
    record = activity_record(
        related_sessions=["COMP319-S05"],
        related_materials=None,
    )
    usage = {
        "ID": "MU:activity-leaf",
        "Session": "COMP319-S05",
        "Material ID": "COMP319-M03",
        "Role": "Primary",
        "Start Page": 1,
        "End Page": 2,
        "Verified": True,
    }
    engine, _, _ = _engine(
        record=record,
        material_usage={"COMP319-S05": [usage]},
    )

    package = engine.get_activity_context("COMP319-A01")
    material_item = next(
        item for item in package.sources if item.source_class == "professor_material"
    )

    assert material_item.entity_id == "COMP319-M03"
    assert engine.get_source_chunk(package.context_id, str(material_item.locator)).entity_id == (
        "COMP319-M03"
    )


@pytest.mark.parametrize(
    "due",
    [
        {"id": "due-property", "type": "date", "date": {}},
        {"id": "due-property", "type": "date", "date": {"start": 42}},
        {
            "id": "due-property",
            "type": "date",
            "date": {"start": "2026-09-30", "end": 42},
        },
        {
            "id": "due-property",
            "type": "date",
            "date": {"start": "2026-09-30", "time_zone": 42},
        },
        {
            "id": "due-property",
            "type": "date",
            "date": {"start": "2026-09-30", "unexpected": True},
        },
        {
            "id": "due-property",
            "type": "date",
            "date": {"start": "not-a-date"},
        },
    ],
)
def test_malformed_due_date_shape_is_denied(due) -> None:
    record = activity_record()
    record["Due"] = due
    engine, _, _ = _engine(record=record)

    with pytest.raises(SourceUnavailableError):
        engine.get_activity_context("COMP319-A01")


@pytest.mark.parametrize(
    "course_relation",
    [
        {"not_a_relation": True},
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": "course-page-1"}],
            "has_more": True,
        },
        {
            "id": "course-property",
            "type": "relation",
            "relation": [{"id": "course-page-1"}, {"id": "course-page-2"}],
            "has_more": False,
        },
    ],
)
def test_activity_course_rejects_malformed_or_truncated_relation(course_relation) -> None:
    record = activity_record()
    record["Course"] = course_relation
    engine, _, _ = _engine(record=record)

    with pytest.raises(SourceUnavailableError, match="Course relation"):
        engine.get_activity_context("COMP319-A01")


@pytest.mark.parametrize(
    "property_value",
    [
        {"id": "instructions-source-property", "type": "url"},
        {"id": "instructions-source-property", "type": "url", "url": 42},
        {"id": "instructions-source-property", "type": "url", "url": ""},
    ],
)
def test_malformed_supplied_provider_url_shape_is_denied(property_value) -> None:
    record = activity_record()
    record["Instructions Source"] = property_value
    engine, _, _ = _engine(record=record)

    with pytest.raises(SourceUnavailableError):
        engine.get_activity_context("COMP319-A01")


def test_truncated_provider_relation_cannot_authorize_activity_scope() -> None:
    record = activity_record(related_sessions=["COMP319-S05"])
    record["Related Sessions"] = {
        "id": "related-sessions-property",
        "type": "relation",
        "relation": [{"id": "COMP319-S05"}],
        "has_more": True,
    }
    engine, _, _ = _engine(record=record)

    with pytest.raises(SourceUnavailableError):
        engine.get_activity_context("COMP319-A01")


def test_partial_derivative_keeps_official_priority_but_discloses_incomplete_coverage() -> None:
    partial = normalize_activity_instructions(
        "Page 1\nOfficial: do not use X.",
        entity_id="COMP319-A01",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "activity-source-01"),
        source_hash="activity-source-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
        status=DerivativeStatus.PARTIAL,
    )
    engine, _, _ = _engine(derivative=partial)

    package = engine.get_activity_context("COMP319-A01")

    assert package.sources[0].source_class == "official_activity"
    assert getattr(package.sources[0], "constraint_metadata").priority == "hard"  # noqa: B009
    assert package.scope["instruction_coverage"]["complete"] is False
    assert any(item.get("code") == "SOURCE_PARTIAL" for item in package.warnings)
    assert any(item.get("code") == "INSTRUCTIONS_PARTIAL" for item in package.warnings)


def test_budget_truncation_survives_with_official_constraint_metadata() -> None:
    config = UlsConfig(
        retrieval=RetrievalCfg(
            max_evidence_items=1,
            max_chars_per_item=8,
            max_total_chars=8,
        )
    )
    engine, _, _ = _engine(config=config)

    package = engine.get_activity_context("COMP319-A01")
    coverage = package.scope["instruction_coverage"]

    assert coverage["truncated"] is True
    assert coverage["complete"] is False
    assert any(item.get("code") == "INSTRUCTIONS_PARTIAL" for item in package.warnings)
    for item in package.sources:
        if item.source_class != "official_activity":
            continue
        constraint = item.constraint_metadata
        assert constraint.priority == "hard"
        assert constraint.official_locator_set == tuple(coverage["expected_locators"])
        assert constraint.official_evidence_set == tuple(coverage["returned_locators"])


def test_pointer_rewire_revokes_existing_activity_instruction_locator() -> None:
    engine, reader, _ = _engine()
    package = engine.get_activity_context("COMP319-A01")
    locator = str(package.sources[0].locator)
    assert engine.get_source_chunk(package.context_id, locator).entity_id == "COMP319-A01"
    reader.activities["COMP319-A01"]["Normalized Instructions"] = (
        "https://drive.google.com/file/d/activity-normalized-02/view"
    )

    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, locator)
