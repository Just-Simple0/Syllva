"""P-A: HUMAN FILE_DETAILS Kind contract per Notion schema profile (plan §5, r3 R4, r4 M5)."""
from __future__ import annotations

import pytest

from uls.intake.models import RequestInput
from uls.intake.requests import validate_request_input

pytestmark = pytest.mark.unit

KEY = "a" * 64


def _request(**overrides) -> RequestInput:
    base = {
        "request_key": KEY, "request_type": "FILE_DETAILS", "intake_ids": ("intake-1",),
        "course_key": "2026-2_LMS67535-001", "submitted": True, "cancelled": False,
    }
    base.update(overrides)
    return RequestInput(**base)


def test_legacy_profiles_still_accept_only_the_two_legacy_kinds() -> None:
    assert validate_request_input(_request(kind="MATERIAL_PDF", material_role="Textbook")) == ()
    errors = validate_request_input(_request(kind="LECTURE_SLIDES", material_role="Lecture Slides"))
    assert "FILE_DETAILS Kind must be TRANSCRIPT or MATERIAL_PDF" in errors
    errors = validate_request_input(_request(kind="LECTURE_SLIDES", material_role="Lecture Slides"),
                                    schema_profile="c5-range-v1")
    assert "FILE_DETAILS Kind must be TRANSCRIPT or MATERIAL_PDF" in errors


@pytest.mark.parametrize("profile", ["legacy5-cls", "c5-range-v2"])
def test_v2_profiles_accept_the_exact_kind_set_with_the_role_matrix(profile: str) -> None:
    ok = _request(kind="LAB_MATERIAL", material_role="Professor Notes")
    assert validate_request_input(ok, schema_profile=profile) == ()
    # MATERIAL_PDF takes the full six-role matrix under v2 (P-A review R7).
    assert validate_request_input(_request(kind="MATERIAL_PDF", material_role="Textbook"), schema_profile=profile) == ()
    assert validate_request_input(_request(kind="MATERIAL_PDF", material_role="Reference"), schema_profile=profile) == ()
    errors = validate_request_input(_request(kind="MATERIAL_PDF", material_role="Nope"), schema_profile=profile)
    assert any("Material Role from" in e for e in errors)
    # Any v2 Material Kind requires a USER role from the Materials.Type set.
    errors = validate_request_input(_request(kind="PROVIDED_CODE"), schema_profile=profile)
    assert any("requires a Material Role from" in e for e in errors)
    errors = validate_request_input(_request(kind="EXAM", material_role="Reference", actual_date="2026-10-01"),
                                    schema_profile=profile)
    assert any("Actual Date must be empty" in e for e in errors)
    # The automatic-only Kinds are never a HUMAN choice.
    for kind in ("ANNOUNCEMENT", "RECORDING", "UNSUPPORTED", "UNKNOWN"):
        errors = validate_request_input(_request(kind=kind, material_role="Reference"), schema_profile=profile)
        assert any("Kind must be one of" in e for e in errors), kind
    # TRANSCRIPT keeps forbidding a role and requiring the session fields.
    errors = validate_request_input(_request(kind="TRANSCRIPT", material_role="Reference"), schema_profile=profile)
    assert errors
