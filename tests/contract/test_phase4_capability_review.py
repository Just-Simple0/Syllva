"""Focused regressions for the Phase 4 capability review findings."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.errors import LocatorNotAllowedError
from uls.domain.models import PageLocator
from uls.domain.page_range import PageRange
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.capabilities import CapabilityManager, authorize_locator
from uls.retrieval.schemas import CapabilityBinding

_DEFAULT_USAGE_RANGE = PageRange(1, 10)


def _usage_binding(
    *,
    usage_id: str = "MU:one",
    locator: tuple[int, int] = (1, 10),
    usage_range: PageRange = _DEFAULT_USAGE_RANGE,
    role: str = "Primary",
) -> CapabilityBinding:
    return CapabilityBinding(
        entity_id="COMP319-M03",
        locator=PageLocator("COMP319-M03", *locator),
        source_hash="material-hash-v1",
        source_version=1,
        source_class="professor_material",
        source_ref=SourceRef("google_drive", "material-m03"),
        session_id="COMP319-S05",
        material_id="COMP319-M03",
        relation_required=True,
        usage_id=usage_id,
        usage_role=role,
        material_type="Lecture Slides",
        course_relation_page_id="course-page-1",
        course_key="2026-1_COMP319-002",
        usage_range=usage_range,
    )


def _authorize(manager: CapabilityManager, context_id: str, locator: str) -> CapabilityBinding:
    return manager.authorize(
        context_id,
        locator,
        candidate_validator=lambda _: SourceFingerprint(1, "material-hash-v1"),
    )


def test_bare_material_is_denied_at_issuance_and_by_compatibility_helper() -> None:
    store = MemoryEphemeralStore()
    manager = CapabilityManager(store)
    stripped = CapabilityBinding(
        entity_id="COMP319-M03",
        locator="COMP319-M03:p1",
        source_hash="h",
        source_version=1,
        source_class="professor_material",
    )
    with pytest.raises(LocatorNotAllowedError):
        manager.issue([stripped])

    capability = store.create_context_capability(
        ["COMP319-M03:p1"], None, 30, source_hash="h", source_version=1
    )
    allowed = capability.allowed_locators[0]
    object.__setattr__(allowed, "source_class", "professor_material")
    with pytest.raises(LocatorNotAllowedError):
        authorize_locator(
            store,
            capability.context_id,
            "COMP319-M03:p1",
            None,
            SourceFingerprint(1, "h"),
            role_validator=lambda _: True,
        )


@pytest.mark.parametrize("bad_range", [None, []])
def test_usage_capability_requires_a_real_full_page_range(bad_range) -> None:
    binding = _usage_binding()
    binding = CapabilityBinding(**{**binding.__dict__, "usage_range": bad_range})
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue([binding])


def test_complete_direct_material_basis_can_be_issued() -> None:
    binding = CapabilityBinding(
        entity_id="COMP319-M03",
        locator="COMP319-M03:p1-p2",
        source_hash="material-hash-v1",
        source_version=1,
        source_class="supplemental_reference",
        source_ref=SourceRef("google_drive", "material-m03"),
        material_id="COMP319-M03",
        material_type="Textbook",
        course_relation_page_id="course-page-1",
        course_key="2026-1_COMP319-002",
    )
    manager = CapabilityManager(MemoryEphemeralStore())
    capability = manager.issue([binding])
    selected = _authorize(manager, capability.context_id, "COMP319-M03:p1")
    assert selected.material_type == "Textbook"
    assert selected.usage_range is None


def test_same_usage_basis_can_bind_multiple_chunks_but_contradictory_basis_is_rejected() -> None:
    manager = CapabilityManager(MemoryEphemeralStore())
    same_basis = [
        _usage_binding(locator=(1, 10), usage_range=PageRange(1, 10)),
        _usage_binding(locator=(5, 15), usage_range=PageRange(1, 10)),
    ]
    capability = manager.issue(same_basis)
    assert _authorize(manager, capability.context_id, "COMP319-M03:p7").usage_id == "MU:one"

    contradictory = [
        _usage_binding(locator=(1, 10), usage_range=PageRange(1, 10)),
        _usage_binding(locator=(5, 15), usage_range=PageRange(5, 15)),
    ]
    capability = manager.issue(contradictory)
    with pytest.raises(LocatorNotAllowedError):
        _authorize(manager, capability.context_id, "COMP319-M03:p7")


def test_identical_relation_tuples_with_distinct_usage_ids_are_ambiguous_but_unrelated_sibling_survives() -> None:
    manager = CapabilityManager(MemoryEphemeralStore())
    ambiguous = [
        _usage_binding(usage_id="MU:a"),
        _usage_binding(usage_id="MU:b"),
    ]
    capability = manager.issue(ambiguous)
    with pytest.raises(LocatorNotAllowedError):
        _authorize(manager, capability.context_id, "COMP319-M03:p7")

    values = [
        _usage_binding(usage_id="MU:dup", locator=(1, 10), usage_range=PageRange(1, 10)),
        _usage_binding(usage_id="MU:dup", locator=(5, 15), usage_range=PageRange(5, 15)),
        _usage_binding(
            usage_id="MU:good",
            locator=(1, 20),
            usage_range=PageRange(1, 20),
            role="Supporting",
        ),
    ]
    capability = manager.issue(values)
    assert _authorize(manager, capability.context_id, "COMP319-M03:p7").usage_id == "MU:good"
