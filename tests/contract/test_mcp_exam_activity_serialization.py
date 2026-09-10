from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.academic import ActivityConstraintMetadata
from uls.domain.enums import FreshnessStatus, SourceAuthority
from uls.domain.errors import PolicyViolation
from uls.domain.models import ContextPackage, EvidenceItem, PageLocator
from uls.domain.provenance import Provenance
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.mcp.schemas import (
    ACTIVITY_CONTEXT_REQUEST_SCHEMA,
    CONTEXT_PACKAGE_RESPONSE_SCHEMA,
    EXAM_CONTEXT_REQUEST_SCHEMA,
    ActivityContextRequest,
    ExamContextRequest,
)
from uls.mcp.tools.activity import TOOL_NAME as ACTIVITY_TOOL_NAME
from uls.mcp.tools.activity import ActivityContextTool
from uls.mcp.tools.exam import TOOL_NAME as EXAM_TOOL_NAME
from uls.mcp.tools.exam import ExamContextTool


class _ReadOnlyEngine:
    def __init__(self, package: ContextPackage) -> None:
        self.package = package
        self.calls: list[tuple[str, str, str | None, str | None]] = []

    def get_exam_context(self, entity_id: str, *, query=None, caller_scope=None):
        self.calls.append(("exam", entity_id, query, caller_scope))
        return self.package

    def get_activity_context(self, entity_id: str, *, query=None, caller_scope=None):
        self.calls.append(("activity", entity_id, query, caller_scope))
        return self.package


def _package() -> ContextPackage:
    source_ref = SourceRef("google_drive", "activity-source-01")
    item = EvidenceItem(
        source_class="official_activity",
        entity_id="COMP319-A01",
        locator=PageLocator("COMP319-A01", 1, 1),
        fingerprint=SourceFingerprint(1, "activity-source-v1"),
        authority=SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
        content="Official: do not use X.",
        provenance=Provenance(
            "uls.activity.v1",
            "COMP319-A01",
            "2026-1_COMP319-002",
            source_ref,
            "activity-source-v1",
            1,
            "1.2.0",
            "2026-09-04T00:00:00+09:00",
            "ready",
        ),
        freshness=FreshnessStatus.FRESH,
    )
    object.__setattr__(
        item,
        "constraint_metadata",
        ActivityConstraintMetadata(("COMP319-A01:p1",), ("COMP319-A01:p1",)),
    )
    constraint = ActivityConstraintMetadata(("COMP319-A01:p1",), ("COMP319-A01:p1",))
    return ContextPackage(
        entity={
            "type": "activity",
            "id": "COMP319-A01",
            "result": {"submission_ref": "refs/heads/assignment-1"},
        },
        scope={
            "official_constraint": constraint,
            "instruction_coverage": {
                "expected_locators": ["COMP319-A01:p1"],
                "returned_locators": ["COMP319-A01:p1"],
                "missing_locators": [],
                "truncated": False,
                "complete": True,
            },
        },
        sources=(item,),
        context_id="ctx-phase5",
    )


def test_wrappers_expose_exact_read_only_names_and_typed_schemas() -> None:
    assert EXAM_TOOL_NAME == "uls.get_exam_context"
    assert ACTIVITY_TOOL_NAME == "uls.get_activity_context"
    assert EXAM_CONTEXT_REQUEST_SCHEMA["required"] == ["exam_id"]
    assert ACTIVITY_CONTEXT_REQUEST_SCHEMA["required"] == ["activity_id"]
    assert CONTEXT_PACKAGE_RESPONSE_SCHEMA["required"] == [
        "protocol_version",
        "context_id",
        "entity",
        "scope",
        "sources",
        "professor_signals",
        "user_context",
        "warnings",
    ]


def test_request_serialization_rejects_unknown_or_wrong_entity_fields() -> None:
    assert ExamContextRequest.from_mapping({"exam_id": "COMP319-E01"}).as_dict() == {
        "exam_id": "COMP319-E01"
    }
    assert ActivityContextRequest("COMP319-A01", query="requirements").as_dict() == {
        "activity_id": "COMP319-A01",
        "query": "requirements",
    }
    with pytest.raises(PolicyViolation):
        ExamContextRequest.from_mapping({"exam_id": "COMP319-E01", "write": True})
    with pytest.raises(PolicyViolation):
        ActivityContextRequest("COMP319-E01")


@pytest.mark.parametrize(
    ("request_cls", "schema", "id_field", "entity_id"),
    [
        (ExamContextRequest, EXAM_CONTEXT_REQUEST_SCHEMA, "exam_id", "COMP319-E01"),
        (ActivityContextRequest, ACTIVITY_CONTEXT_REQUEST_SCHEMA, "activity_id", "COMP319-A01"),
    ],
)
def test_mapping_requests_accept_only_the_declared_entity_key(
    request_cls,
    schema,
    id_field: str,
    entity_id: str,
) -> None:
    assert set(schema["properties"]) == {id_field, "query", "caller_scope"}
    assert request_cls.from_mapping({id_field: entity_id}).as_dict() == {id_field: entity_id}

    with pytest.raises(PolicyViolation, match="unknown fields"):
        request_cls.from_mapping({"entity_id": entity_id})
    with pytest.raises(PolicyViolation, match="unknown fields"):
        request_cls.from_mapping({id_field: entity_id, "entity_id": entity_id})


def test_exam_and_activity_wrappers_call_only_direct_engine_and_preserve_wire_metadata() -> None:
    engine = _ReadOnlyEngine(_package())

    exam_wire = ExamContextTool(engine).invoke(
        {"exam_id": "COMP319-E01", "caller_scope": "study"}
    )
    activity_wire = ActivityContextTool(engine).invoke(
        "COMP319-A01", query="requirements", caller_scope="study"
    )

    assert engine.calls == [
        ("exam", "COMP319-E01", None, "study"),
        ("activity", "COMP319-A01", "requirements", "study"),
    ]
    for wire in (exam_wire, activity_wire):
        assert wire["context_id"] == "ctx-phase5"
        assert wire["sources"][0]["source_class"] == "official_activity"
        assert wire["sources"][0]["constraint_metadata"]["priority"] == "hard"
        assert wire["scope"]["instruction_coverage"]["complete"] is True
        assert wire["scope"]["official_constraint"]["official_locator_set"] == [
            "COMP319-A01:p1"
        ]
