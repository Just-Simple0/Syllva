from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import (
    ACTIVITY_NORMALIZED_POINTER,
    ACTIVITY_SOURCE_POINTER,
    COURSE_KEY,
    COURSE_PAGE_ID,
)

from uls.domain.errors import LocatorNotAllowedError
from uls.domain.models import PageLocator
from uls.domain.page_range import PageRange
from uls.domain.source_ref import SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.capabilities import CapabilityManager
from uls.retrieval.schemas import CapabilityBinding


def _exam_binding(**changes) -> CapabilityBinding:
    values = {
        "entity_id": "COMP319-S01",
        "locator": PageLocator("COMP319-S01", 1, 1),
        "source_hash": "transcript-hash-v1",
        "source_version": 1,
        "source_class": "professor_transcript",
        "source_ref": SourceRef("google_drive", "transcript-01"),
        "session_id": "COMP319-S01",
        "course_relation_page_id": COURSE_PAGE_ID,
        "course_key": COURSE_KEY,
        "parent_entity_id": "COMP319-E01",
        "parent_entity_type": "exam",
        "parent_course_relation_page_id": COURSE_PAGE_ID,
        "parent_course_key": COURSE_KEY,
        "parent_scope_confirmed": True,
        "parent_included_session_ids": ("COMP319-S01",),
        "parent_path_leaf": "session",
    }
    values.update(changes)
    return CapabilityBinding(**values)


def _activity_binding(**changes) -> CapabilityBinding:
    values = {
        "entity_id": "COMP319-A01",
        "locator": PageLocator("COMP319-A01", 1, 1),
        "source_hash": "activity-source-v1",
        "source_version": 1,
        "source_class": "official_activity",
        "source_ref": SourceRef("google_drive", "activity-source-01"),
        "parent_entity_id": "COMP319-A01",
        "parent_entity_type": "activity",
        "parent_course_relation_page_id": COURSE_PAGE_ID,
        "parent_course_key": COURSE_KEY,
        "parent_related_session_ids": (),
        "parent_related_material_ids": (),
        "parent_path_leaf": "activity_instructions",
        "activity_instructions_source_url": ACTIVITY_SOURCE_POINTER,
        "activity_normalized_instructions_url": ACTIVITY_NORMALIZED_POINTER,
        "activity_binding_identity": ("google_drive", "activity-source-01"),
    }
    values.update(changes)
    return CapabilityBinding(**values)


def test_issue_rejects_incomplete_parent_and_duplicate_relation_snapshots() -> None:
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue(
            [_exam_binding(parent_included_session_ids=None)]
        )
    with pytest.raises(ValueError):
        _exam_binding(parent_included_session_ids=("COMP319-S01", "COMP319-S01"))


def test_activity_instruction_binding_requires_both_pointers_and_source_identity() -> None:
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue(
            [_activity_binding(activity_binding_identity=None)]
        )
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue(
            [_activity_binding(activity_normalized_instructions_url=None)]
        )


def test_decorated_capability_preserves_parent_snapshot_and_phase4_leaf_identity() -> None:
    manager = CapabilityManager(MemoryEphemeralStore())
    capability = manager.issue([_exam_binding()])
    allowed = capability.allowed_locators[0]

    assert allowed.parent_entity_id == "COMP319-E01"
    assert allowed.parent_scope_confirmed is True
    assert allowed.parent_included_session_ids == ("COMP319-S01",)
    assert allowed.course_key == COURSE_KEY
    assert allowed.session_id == "COMP319-S01"


def test_phase4_usage_binding_still_requires_complete_leaf_identity_under_parent_metadata() -> None:
    values = {
        "entity_id": "COMP319-M03",
        "locator": PageLocator("COMP319-M03", 1, 1),
        "source_hash": "material-hash-v1",
        "source_version": 1,
        "source_class": "professor_material",
        "source_ref": None,
        "material_id": "COMP319-M03",
        "usage_id": "MU:one",
        "relation_required": True,
        "usage_role": "Primary",
        "material_type": "Lecture Slides",
        "course_relation_page_id": COURSE_PAGE_ID,
        "course_key": COURSE_KEY,
        "usage_range": PageRange(1, 1),
        "parent_entity_id": "COMP319-E01",
        "parent_entity_type": "exam",
        "parent_course_relation_page_id": COURSE_PAGE_ID,
        "parent_course_key": COURSE_KEY,
        "parent_scope_confirmed": True,
        "parent_included_session_ids": ("COMP319-S01",),
        "parent_path_leaf": "material_usage",
    }
    with pytest.raises(LocatorNotAllowedError):
        CapabilityManager(MemoryEphemeralStore()).issue([CapabilityBinding(**values)])
