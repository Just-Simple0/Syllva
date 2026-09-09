"""Permanent Phase 4 rev3 regressions for retrieval and capability boundaries."""

from __future__ import annotations

import pathlib
import sys
from copy import deepcopy
from dataclasses import replace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from phase4 import material_derivative, ready_phase4

from uls.config.schema import RetrievalCfg, UlsConfig
from uls.domain.errors import (
    LocatorNotAllowedError,
    LocatorStaleError,
    SourceUnavailableError,
)
from uls.domain.models import PageLocator, TimeLocator
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.capabilities import CapabilityManager
from uls.retrieval.engine import RetrievalEngine
from uls.retrieval.schemas import CapabilityBinding


def _expanded_material_snapshot(reader) -> dict[str, object]:
    return {
        "id": "COMP319-M03",
        "properties": deepcopy(reader.materials["COMP319-M03"]),
    }


@pytest.mark.parametrize("mutation", ["type", "course", "delete"])
def test_usage_material_snapshot_never_bypasses_current_material_lookup(mutation: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    # The relation is deliberately expanded with a stale copy.  It is an
    # identity hint only; the graph reader remains authoritative.
    reader.material_usage["COMP319-S05"][0]["Material"] = _expanded_material_snapshot(reader)
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")
    assert any(item.entity_id == "COMP319-M03" for item in package.sources)

    if mutation == "type":
        # Same authority class, but a changed raw Type must revoke the old
        # binding rather than being hidden by the expanded relation snapshot.
        reader.materials["COMP319-M03"]["Type"] = "Professor Notes"
    elif mutation == "course":
        current_course = deepcopy(reader.courses["course-page-1"])
        current_course["id"] = "course-page-2"
        reader.courses["course-page-2"] = current_course
        reader.materials["COMP319-M03"]["Course"] = {
            "relation": [{"id": "course-page-2"}]
        }
    else:
        del reader.materials["COMP319-M03"]

    with pytest.raises((LocatorNotAllowedError, LocatorStaleError, SourceUnavailableError)):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p1")


def test_expanded_material_snapshot_without_current_material_is_excluded() -> None:
    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Material"] = _expanded_material_snapshot(reader)
    del reader.materials["COMP319-M03"]
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    package = engine.get_session_context("COMP319-S05", include_provisional=True)

    assert all(item.entity_id != "COMP319-M03" for item in package.sources)
    assert all(
        binding.material_id != "COMP319-M03"
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
    )


def test_provisional_reporting_uses_retained_bindings_after_budget_replacement() -> None:
    reader, _, drive, resolver = ready_phase4()
    config = UlsConfig(
        retrieval=RetrievalCfg(
            max_evidence_items=12,
            max_chars_per_item=4,
            max_total_chars=24_000,
            max_followup_chunks=8,
        )
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config,
        source_binding_resolver=resolver,
    )

    package = engine.get_session_context("COMP319-S05", include_provisional=True)
    material = [item for item in package.sources if item.entity_id == "COMP319-M03"]
    bindings = engine.capabilities.bindings_for(package.context_id) or ()

    assert material
    assert all(len(item.content) <= 4 for item in material)
    assert all(getattr(item, "provisional", False) for item in material)
    assert package.scope["provisional"] is True
    assert any(
        warning["code"] == "PROVISIONAL_SOURCE"
        and "MU:existing" in warning["message"]
        for warning in package.warnings
    )
    assert any(
        binding.usage_id == "MU:existing" and binding.provisional
        for binding in bindings
    )


def test_provisional_and_verified_overlapping_bases_are_reported_independently() -> None:
    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"].append(
        {
            **deepcopy(reader.material_usage["COMP319-S05"][0]),
            "ID": "MU:verified-overlap",
            "Start Page": 1,
            "End Page": 1,
            "Verified": True,
        }
    )
    config = UlsConfig(
        retrieval=RetrievalCfg(
            max_evidence_items=12,
            max_chars_per_item=4,
            max_total_chars=24_000,
            max_followup_chunks=8,
        )
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        config,
        source_binding_resolver=resolver,
    )

    package = engine.get_session_context("COMP319-S05", include_provisional=True)
    p1_bindings = [
        binding
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
        if str(binding.locator) == "COMP319-M03:p1"
    ]

    assert {binding.usage_id for binding in p1_bindings} >= {
        "MU:existing",
        "MU:verified-overlap",
    }
    assert package.scope["provisional"] is True
    assert sum(
        warning["code"] == "PROVISIONAL_SOURCE" for warning in package.warnings
    ) == 1


def test_followup_rejects_source_advance_after_selected_binding_is_authorized() -> None:
    reader, _, drive, resolver = ready_phase4()
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )
    package = engine.get_session_context("COMP319-S05")

    real_authorize = engine.capabilities.authorize

    def authorize_then_advance(*args, **kwargs):
        binding = real_authorize(*args, **kwargs)
        drive.fingerprints["material-m03"] = SourceFingerprint(2, "material-hash-v2")
        drive.derived["material-m03"] = material_derivative(
            source_hash="material-hash-v2",
            source_version=2,
        )
        return binding

    engine.capabilities.authorize = authorize_then_advance

    with pytest.raises(LocatorStaleError):
        engine.get_source_chunk(package.context_id, "COMP319-M03:p1")


@pytest.mark.parametrize("status", ["pending", "processing", "needs_review", "failed"])
def test_non_ready_material_status_is_not_factual_initially_or_on_followup(status: str) -> None:
    reader, _, drive, resolver = ready_phase4()
    drive.derived["material-m03"] = drive.derived["material-m03"].replace(
        "status: ready", f"status: {status}"
    )
    reader.material_usage["COMP319-S05"][0]["Verified"] = True
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    with pytest.raises(SourceUnavailableError):
        engine.get_material_context("COMP319-M03")

    package = engine.get_session_context("COMP319-S05")
    assert all(item.entity_id != "COMP319-M03" for item in package.sources)
    assert any(warning["code"] == "SOURCE_UNAVAILABLE" for warning in package.warnings)
    assert all(
        binding.material_id != "COMP319-M03"
        for binding in engine.capabilities.bindings_for(package.context_id) or ()
    )

    ready_reader, _, ready_drive, ready_resolver = ready_phase4()
    ready_reader.material_usage["COMP319-S05"][0]["Verified"] = True
    ready_engine = RetrievalEngine(
        ready_reader,
        ready_drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=ready_resolver,
    )
    ready_package = ready_engine.get_session_context("COMP319-S05")
    ready_drive.derived["material-m03"] = ready_drive.derived["material-m03"].replace(
        "status: ready", f"status: {status}"
    )
    with pytest.raises(SourceUnavailableError):
        ready_engine.get_source_chunk(ready_package.context_id, "COMP319-M03:p1")


def test_partial_material_status_keeps_partial_warning_and_valid_pages() -> None:
    reader, _, drive, resolver = ready_phase4()
    drive.derived["material-m03"] = drive.derived["material-m03"].replace(
        "status: ready", "status: partial"
    )
    engine = RetrievalEngine(
        reader,
        drive,
        None,
        MemoryEphemeralStore(),
        UlsConfig(),
        source_binding_resolver=resolver,
    )

    package = engine.get_material_context("COMP319-M03")

    assert package.sources
    assert any(warning["code"] == "SOURCE_PARTIAL" for warning in package.warnings)


def _material_binding(**overrides) -> CapabilityBinding:
    binding = CapabilityBinding(
        entity_id="COMP319-M03",
        locator=PageLocator("COMP319-M03", 1, 1),
        source_hash="material-hash-v1",
        source_version=1,
        source_class="professor_material",
        source_ref=SourceRef("google_drive", "material-m03"),
        material_id="COMP319-M03",
        material_type="Lecture Slides",
        course_relation_page_id="course-page-1",
        course_key="2026-1_COMP319-002",
    )
    return replace(binding, **overrides)


@pytest.mark.parametrize(
    ("source_class", "material_type"),
    [
        ("professor_material", "Lecture Slides"),
        ("supplemental_reference", "Textbook"),
    ],
)
def test_exact_supported_material_source_classes_remain_issuable(
    source_class: str,
    material_type: str,
) -> None:
    manager = CapabilityManager(MemoryEphemeralStore())
    capability = manager.issue(
        [_material_binding(source_class=source_class, material_type=material_type)]
    )
    assert capability.context_id


def test_legacy_material_source_class_is_rejected_at_issuance() -> None:
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue(
            [_material_binding(source_class="material")]
        )


def test_managed_material_requires_self_consistent_page_locator_and_entity() -> None:
    manager = CapabilityManager(MemoryEphemeralStore())
    with pytest.raises(LocatorNotAllowedError):
        manager.issue(
            [_material_binding(locator=PageLocator("COMP319-M04", 1, 1))]
        )
    with pytest.raises(LocatorNotAllowedError):
        manager.issue(
            [_material_binding(locator=TimeLocator("COMP319-M03", 1, 1))]
        )


@pytest.mark.parametrize("relation_required", [1, "yes"])
def test_relation_required_is_a_real_bool_for_material_capabilities(relation_required) -> None:
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue(
            [_material_binding(relation_required=relation_required)]
        )


def test_partial_material_metadata_cannot_hide_on_a_generic_binding() -> None:
    hidden = CapabilityBinding(
        entity_id="COMP319-S05",
        locator=TimeLocator("COMP319-S05", 1, 1),
        source_hash="transcript-hash-v1",
        source_version=1,
        source_class="external",
        source_ref=SourceRef("google_drive", "transcript-05"),
        material_id="COMP319-M03",
    )

    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue([hidden])

    course_only = replace(
        hidden,
        material_id=None,
        course_relation_page_id="course-page-1",
        course_key="2026-1_COMP319-002",
    )
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue([course_only])


__all__ = []
