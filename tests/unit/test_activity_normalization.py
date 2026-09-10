from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from phase4 import COURSE_KEY

from uls.domain.enums import DerivativeStatus
from uls.domain.errors import SourcePartialError
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.normalization.activity import (
    ACTIVITY_SCHEMA,
    ActivityFrontMatter,
    NormalizedActivity,
    normalize_activity_instructions,
)
from uls.normalization.validators import (
    parse_activity_derivative,
    validate_normalized_activity,
)
from uls.retrieval.chunking import page_chunks


def _activity(*, status: DerivativeStatus | str = DerivativeStatus.READY) -> NormalizedActivity:
    return normalize_activity_instructions(
        "Page 1\r\nDo not use X.\rPage 2\nSubmit the exact ref.",
        entity_id="COMP319-A01",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "activity-source-01"),
        source_hash="activity-hash-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
        status=status,
    )


def test_activity_normalization_preserves_body_and_typed_fingerprint() -> None:
    activity = _activity()

    assert isinstance(activity.front_matter, ActivityFrontMatter)
    assert activity.schema == ACTIVITY_SCHEMA
    assert activity.body == "Page 1\nDo not use X.\nPage 2\nSubmit the exact ref."
    assert activity.source_ref.identity == ("google_drive", "activity-source-01")
    assert activity.fingerprint == SourceFingerprint(1, "activity-hash-v1")
    assert activity.status is DerivativeStatus.READY
    assert validate_normalized_activity(
        activity, current_fingerprint=SourceFingerprint(1, "activity-hash-v1"), require_ready=True
    )


def test_activity_markdown_round_trip_requires_complete_front_matter() -> None:
    activity = _activity()
    parsed = parse_activity_derivative(activity.to_markdown())

    assert parsed == activity
    assert "source_ref:" in activity.to_markdown()
    with pytest.raises(ValueError):
        ActivityFrontMatter.from_mapping({"schema": ACTIVITY_SCHEMA})


def test_partial_activity_is_valid_but_rejected_by_ready_gate() -> None:
    activity = _activity(status=DerivativeStatus.PARTIAL)

    assert validate_normalized_activity(activity)
    with pytest.raises(SourcePartialError):
        validate_normalized_activity(activity, require_ready=True)


def test_unmarked_activity_text_never_receives_a_fabricated_page_locator() -> None:
    activity = normalize_activity_instructions(
        "The provider returned text without page markers.",
        entity_id="COMP319-A01",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "activity-source-01"),
        source_hash="activity-hash-v1",
        source_version=1,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )

    assert page_chunks(activity, entity_id="COMP319-A01") == []
