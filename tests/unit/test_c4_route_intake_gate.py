"""C4 regression: the real production course/kind fail-closed gate.

Earlier drafts of this test targeted uls.intake.planner.route_intake(), but an
independent insane-review found that function has zero production callers.
The actual production fail-closed gate for "no source registration before
Course/kind are explicitly confirmed" is validate_request_input()
(src/uls/intake/requests.py), invoked from
IntakeWorker._claim_request_unlocked() (src/uls/intake/worker.py) before a
job is ever enqueued for _reserve_session/_reserve_material.

This test also locks a structural invariant: RequestInput (the type
validate_request_input operates on) has no observed_kind field at all, so a
classifier confidence hint can never reach this gate even in principle -- it
is not merely filtered by logic, it does not exist on the type. observed_kind
only exists on IntakeItem and is written to a separate read-only "Observed
Kind" Notion property (src/uls/intake/worker.py) as a user-facing suggestion;
it never flows into the submitted request's "kind" field that
validate_request_input checks.
"""

from __future__ import annotations

import dataclasses

import pytest

from uls.intake.models import RequestInput, RequestType
from uls.intake.requests import validate_request_input

pytestmark = pytest.mark.unit


def test_request_input_has_no_observed_kind_field() -> None:
    """Structural guarantee: a classifier confidence hint cannot reach the
    claim-time validation gate because the type it operates on has no such
    field, not merely because current logic happens to ignore it."""

    field_names = {f.name for f in dataclasses.fields(RequestInput)}
    assert "observed_kind" not in field_names
    assert "confidence" not in field_names


def _file_details(**overrides: object) -> RequestInput:
    base: dict[str, object] = {
        "request_key": "a" * 64,
        "request_type": RequestType.FILE_DETAILS.value,
        "intake_ids": ("intake-1",),
        "course_key": "2026-1_COMP319-002",
        "kind": None,
        "actual_date": None,
        "session_mode": None,
        "session_id": None,
        "session_no": None,
        "material_role": None,
        "submitted": True,
        "cancelled": False,
    }
    base.update(overrides)
    return RequestInput(**base)


def test_missing_course_key_is_rejected() -> None:
    request = _file_details(course_key=None, kind="TRANSCRIPT", actual_date="2026-03-06", session_mode="NEW")
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert "FILE_DETAILS requires exactly one Course" in errors


def test_course_key_outside_registry_is_rejected() -> None:
    request = _file_details(kind="TRANSCRIPT", actual_date="2026-03-06", session_mode="NEW")
    errors = validate_request_input(request, course_exists=lambda value: False)
    assert "Course is not a current canonical course" in errors


def test_missing_kind_is_rejected_even_with_confirmed_course() -> None:
    request = _file_details(kind=None)
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert "FILE_DETAILS Kind must be TRANSCRIPT or MATERIAL_PDF" in errors


def test_transcript_missing_date_and_session_mode_is_rejected() -> None:
    request = _file_details(kind="TRANSCRIPT")
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert "transcript FILE_DETAILS requires an actual lecture date" in errors
    assert "transcript FILE_DETAILS requires NEW or EXISTING Session Mode" in errors


def test_full_transcript_new_session_has_no_errors() -> None:
    request = _file_details(kind="TRANSCRIPT", actual_date="2026-03-06", session_mode="NEW")
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert errors == ()


def test_full_material_pdf_has_no_errors() -> None:
    request = _file_details(kind="MATERIAL_PDF", material_role="Lecture Slides")
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert errors == ()


def test_material_pdf_with_session_fields_is_rejected() -> None:
    """PDF material intake must never accept Session-only fields, regardless
    of what any classifier candidate suggested."""

    request = _file_details(kind="MATERIAL_PDF", material_role="Lecture Slides", actual_date="2026-03-06")
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert "Actual Date must be empty for PDF FILE_DETAILS" in errors


def test_existing_session_must_belong_to_selected_course() -> None:
    request = _file_details(kind="TRANSCRIPT", actual_date="2026-03-06", session_mode="EXISTING", session_id="S05")
    errors = validate_request_input(
        request,
        course_exists=lambda value: True,
        session_course=lambda value: "2026-1_OTHER-999",
    )
    assert "selected Session does not belong to selected Course" in errors


def test_assign_course_forbids_any_file_details_field() -> None:
    """ASSIGN_COURSE (the request type that confirms Course before any kind
    selection is even possible) must carry no kind/date/session fields."""

    request = _file_details(
        request_type=RequestType.ASSIGN_COURSE.value,
        kind="TRANSCRIPT",
        actual_date="2026-03-06",
        session_mode="NEW",
    )
    errors = validate_request_input(request, course_exists=lambda value: True)
    assert any("must be empty for ASSIGN_COURSE" in error for error in errors)
