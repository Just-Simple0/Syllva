"""P-A: v2 Notion schema profiles keep the legacy shapes intact and verify the real 2025-09-03 shape."""
from __future__ import annotations

import copy
from typing import Any

import pytest
from tests.integration.test_intake_worker_preview import SEMESTER, _NotionClient

from uls.adapters.notion.intake import (
    AUTO_RESOLVED_STATUS,
    FILE_KINDS,
    INTAKE_SCHEMAS,
    MATERIAL_ROLES,
    STATUS_GROUPS,
    NotionAPIWorker,
    NotionIntakeWriter,
    intake_schemas,
    intake_status_groups,
)
from uls.intake.classification import FILE_KINDS_V2, MATERIAL_ROLES_V2, Kind, Origin

pytestmark = pytest.mark.integration

_DISPLAY = {"to_do": "To-do", "in_progress": "In progress", "complete": "Complete"}


def _payloads(profile: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Build live-shaped (2025-09-03) data-source readbacks for one profile."""

    schemas = intake_schemas(profile)
    groups_by_logical = intake_status_groups(profile)
    ids = {logical: f"notion-{logical}" for logical in schemas}
    payloads: dict[str, dict[str, Any]] = {}
    for logical, schema in schemas.items():
        properties: dict[str, dict[str, Any]] = {}
        for name, spec in schema.items():
            kind = str(spec["type"])
            prop: dict[str, Any] = {"name": name, "type": kind, kind: {}}
            if kind in {"select", "status"}:
                options = list(spec.get("options", ()))
                if logical == "academic_courses" and name == "Semester":
                    options = [SEMESTER]
                option_rows = [{"id": f"opt-{logical}-{name}-{i}", "name": o, "color": "default"}
                               for i, o in enumerate(options)]
                prop[kind] = {"options": option_rows}
                if kind == "status" and logical != "automation_queue":
                    by_name = {row["name"]: row["id"] for row in option_rows}
                    groups = [
                        {"id": f"grp-{logical}-{g}", "name": _DISPLAY[g], "color": "default",
                         "option_ids": [by_name[n] for n in names]}
                        for g, names in groups_by_logical[logical].items() if g in _DISPLAY
                    ]
                    prop[kind]["groups"] = groups
                elif kind == "status":
                    prop[kind]["groups"] = []
            elif kind == "relation":
                prop[kind] = {"data_source_id": ids[str(spec["relation"])]}
            properties[name] = prop
        payloads[logical] = {
            "id": ids[logical],
            "parent": {"type": "database_id", "database_id": f"db-{logical}"},
            "database_parent": {"type": "page_id", "page_id": "notion-parent"},
            "properties": properties,
        }
    return ids, payloads


def _writer(profile: str, payloads: dict[str, dict[str, Any]], ids: dict[str, str]) -> NotionIntakeWriter:
    return NotionIntakeWriter(NotionAPIWorker(_NotionClient(payloads)), ids,
                              parent_page_id="notion-parent", semester=SEMESTER,
                              schema_profile=profile)


def test_legacy_profiles_are_byte_identical_to_before() -> None:
    assert intake_schemas("legacy5") == INTAKE_SCHEMAS
    assert intake_schemas("legacy5")["input_request"]["Kind"]["options"] == tuple(FILE_KINDS)
    assert intake_schemas("c5-range-v1")["input_request"]["Material Role"]["options"] == tuple(MATERIAL_ROLES)
    assert intake_status_groups("legacy5") == STATUS_GROUPS
    assert intake_status_groups("c5-range-v1") == STATUS_GROUPS
    assert "Suggested Kind" not in intake_schemas("c5-range-v1")["input_request"]
    with pytest.raises(ValueError):
        intake_schemas("legacy6")


@pytest.mark.parametrize("profile", ["legacy5-cls", "c5-range-v2"])
def test_classification_profiles_declare_the_exact_v2_contract(profile: str) -> None:
    schemas = intake_schemas(profile)
    request = schemas["input_request"]
    assert request["Kind"]["options"] == FILE_KINDS_V2 and request["Kind"]["ownership"] == "USER"
    assert request["Material Role"]["options"] == MATERIAL_ROLES_V2
    assert AUTO_RESOLVED_STATUS in request["Request Status"]["options"]
    assert intake_status_groups(profile)["input_request"]["complete"] == ("Cancelled", "Applied", AUTO_RESOLVED_STATUS)
    for name in ("Suggested Course", "Suggested Kind", "Suggested Date", "Suggested Week",
                 "Suggestion Source", "Suggestion Note"):
        assert request[name]["ownership"] == "SYSTEM_DERIVED"
    assert set(request["Suggested Kind"]["options"]) == {k.value for k in Kind}
    intake = schemas["file_intake"]
    assert set(intake["Origin"]["options"]) == {o.value for o in Origin}
    assert intake["Observed Kind"]["options"] == INTAKE_SCHEMAS["file_intake"]["Observed Kind"]["options"]
    materials = schemas["materials"]
    assert materials["Type"] == INTAKE_SCHEMAS["materials"]["Type"]  # USER Type untouched
    assert materials["Week"]["ownership"] == "SYSTEM_INITIAL_USER_PRESERVE"
    if profile == "c5-range-v2":
        assert "material_usage" in schemas and "USAGE_RANGE" in schemas["input_request"]["Request Type"]["options"]
    else:
        assert "material_usage" not in schemas


@pytest.mark.parametrize("profile", ["legacy5", "legacy5-cls", "c5-range-v1", "c5-range-v2"])
def test_live_shaped_readback_verifies_for_each_profile(profile: str) -> None:
    ids, payloads = _payloads(profile)
    result = _writer(profile, payloads, ids).validate_workspace()
    assert result["status"] == "VERIFIED", result
    assert result["data_sources"] == len(intake_schemas(profile))


def test_profiles_do_not_accept_each_other_s_shape() -> None:
    ids_v2, payloads_v2 = _payloads("legacy5-cls")
    ids_v1, payloads_v1 = _payloads("legacy5")
    # A v2 workspace fails the legacy exact readback (extra properties / options).
    assert _writer("legacy5", payloads_v2, ids_v2).validate_workspace()["status"] == "NOT_VERIFIED"
    # A legacy workspace fails the v2 readback (missing properties).
    assert _writer("legacy5-cls", payloads_v1, ids_v1).validate_workspace()["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize("mutate, reason", [
    (lambda p: p["input_request"]["properties"]["Request Status"]["status"]["options"].pop(),
     "option set mismatch: input_request.Request Status"),
    (lambda p: p["input_request"]["properties"]["Kind"]["select"]["options"].pop(),
     "option set mismatch: input_request.Kind"),
    (lambda p: p["file_intake"]["properties"].pop("AI Kind"), "property set mismatch: file_intake"),
    (lambda p: p["materials"]["properties"]["Week"].update(type="rich_text", rich_text={}),
     "property type mismatch: materials.Week"),
])
def test_v2_readback_fails_closed_on_each_missing_piece(mutate, reason: str) -> None:
    ids, payloads = _payloads("legacy5-cls")
    payloads = copy.deepcopy(payloads)
    mutate(payloads)
    result = _writer("legacy5-cls", payloads, ids).validate_workspace()
    assert result["status"] == "NOT_VERIFIED" and result["reason"].endswith(reason), result


def test_auto_resolved_must_sit_in_the_complete_group() -> None:
    ids, payloads = _payloads("legacy5-cls")
    status = payloads["input_request"]["properties"]["Request Status"]["status"]
    auto = next(o["id"] for o in status["options"] if o["name"] == AUTO_RESOLVED_STATUS)
    complete = next(g for g in status["groups"] if g["name"] == "Complete")
    todo = next(g for g in status["groups"] if g["name"] == "To-do")
    complete["option_ids"].remove(auto)
    todo["option_ids"].append(auto)
    result = _writer("legacy5-cls", payloads, ids).validate_workspace()
    assert result["reason"].endswith("status groups mismatch: input_request.Request Status")


def test_initial_draft_may_carry_suggestions_but_no_user_values() -> None:
    ids, payloads = _payloads("legacy5-cls")
    writer = _writer("legacy5-cls", payloads, ids)
    base = {
        "Name": "req", "Request Key": "k", "Request Revision Hash": "h", "Request Type": "ASSIGN_COURSE",
        "Intake Items": ["page-1"], "Submitted": False, "Cancelled": False,
        "Request Status": "Draft", "Workspace Fingerprint": "wf",
    }
    writer._validate("input_request", {**base, "Suggested Kind": "LECTURE_SLIDES", "Suggested Week": 3,
                                        "Suggestion Source": "rule:P3:lecture"},
                     is_create=True, allow_user_defaults=False)
    from uls.domain.errors import PolicyDeniedError
    with pytest.raises(PolicyDeniedError):
        writer._validate("input_request", {**base, "Kind": "LECTURE_SLIDES"},
                         is_create=True, allow_user_defaults=False)


# --- P-A review O1: independent frozen snapshot of the pre-v2 profiles ---------------

def _canonical_hash(value: Any) -> str:
    import hashlib
    import json

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=list)
    return hashlib.sha256(encoded.encode()).hexdigest()


# Computed from the committed HEAD 0c1fe7f export (before any v2 change), not from the
# current module; see handoff.md "P-A O1".
LEGACY_SNAPSHOT_HASHES = {
    "legacy5": "76d3cc46198e3dd36517c7f98ce82be1007aa88a94c30a3d7966cd48b5b9af13",
    "c5-range-v1": "8fcc0e694ff066377e09351424675c70580448b37e915a36d6f964b459170101",
    "status_groups": "8f61a47579fae6e85a53ce707207a44bf8694db3b704a6e155d5b6788e2b88a0",
}


def test_legacy_profiles_match_the_frozen_pre_v2_snapshot() -> None:
    assert _canonical_hash(intake_schemas("legacy5")) == LEGACY_SNAPSHOT_HASHES["legacy5"]
    assert _canonical_hash(intake_schemas("c5-range-v1")) == LEGACY_SNAPSHOT_HASHES["c5-range-v1"]
    assert _canonical_hash(STATUS_GROUPS) == LEGACY_SNAPSHOT_HASHES["status_groups"]
    assert _canonical_hash(intake_status_groups("legacy5")) == LEGACY_SNAPSHOT_HASHES["status_groups"]
    assert _canonical_hash(intake_status_groups("c5-range-v1")) == LEGACY_SNAPSHOT_HASHES["status_groups"]
    # And the v2 profiles are genuinely different objects, not aliases.
    assert _canonical_hash(intake_schemas("legacy5-cls")) != LEGACY_SNAPSHOT_HASHES["legacy5"]
