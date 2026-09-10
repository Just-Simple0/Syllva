"""Reusable strict Phase4 graph/source/writer fixtures."""

from __future__ import annotations

from typing import Any

from fake_drive import FakeDriveReader
from fake_notion import COURSE_KEY, COURSE_PAGE_ID, FakeNotionAdapter, FakeNotionReader

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.domain.approval_identity import (
    build_material_usage_semantics,
    canonical_action_json,
    derive_proposal_id,
)
from uls.domain.enums import AutomationActor
from uls.domain.page_range import PageRange
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.normalization.transcript import normalize_transcript
from uls.retrieval.scope import material_usage_scopes


def transcript_derivative(
    *,
    source_hash: str = "transcript-hash-v1",
    source_version: int = 1,
) -> Any:
    return normalize_transcript(
        "[00:00:01] CPU scheduling overview\n[00:00:10] Round robin examples",
        entity_id="COMP319-S05",
        course_key=COURSE_KEY,
        source_ref=SourceRef("google_drive", "transcript-05"),
        source_hash=source_hash,
        source_version=source_version,
        processor_version="1.2.0",
        now="2026-09-04T00:00:00+09:00",
    )


def material_derivative(
    *,
    source_hash: str = "material-hash-v1",
    source_version: int = 1,
    material_id: str = "COMP319-M03",
) -> str:
    return f"""---
schema: uls.material.v1
entity_id: {material_id}
course_key: {COURSE_KEY}
source_ref:
  provider: google_drive
  file_id: material-m03
source_hash: {source_hash}
source_version: {source_version}
processor_version: 1.2.0
normalized_at: '2026-09-04T00:00:00+09:00'
status: ready
---
Page 1
Master theorem material
Page 2
Round robin examples
"""


def ready_phase4(
    *,
    usage: dict[str, Any] | None = None,
    material_type: str = "Lecture Slides",
    material_source_ref: str = "material-m03",
    drive: FakeDriveReader | None = None,
) -> tuple[FakeNotionReader, FakeNotionAdapter, FakeDriveReader, ValidatedSourceBindingResolver]:
    usage_value = usage or {
        "ID": "MU:existing",
        "Session": "COMP319-S05",
        "Material ID": "COMP319-M03",
        "Role": "Primary",
        "Start Page": 1,
        "End Page": 2,
        "Verified": False,
    }
    material = {
        "ID": "COMP319-M03",
        "Name": "Algorithmic Analysis II",
        "Aliases": "Lec3 | Algorithmic Analysis II",
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Type": material_type,
        "Source Folder": "https://drive.google.com/drive/folders/materials",
        "Original Filename": "COMP319-M03.pdf",
        "Normalized Source": material_source_ref,
        "Current Source Version": 1,
        "Text Source": "PDF Extract",
        "Visual Dependency": "None",
        "AI Priority": "Normal",
        "Page Count": 2,
        "Text Status": "Ready",
    }
    reader = FakeNotionReader(
        material_usage={"COMP319-S05": [usage_value]},
        materials=[material],
    )
    drive_value = drive or FakeDriveReader(
        derived={
            "transcript-05": transcript_derivative(),
            "material-m03": material_derivative(),
        },
        fingerprints={
            "transcript-05": SourceFingerprint(1, "transcript-hash-v1"),
            "material-m03": SourceFingerprint(1, "material-hash-v1"),
        },
    )
    writer = FakeNotionAdapter(reader)
    resolver = ValidatedSourceBindingResolver(drive_value)
    return reader, writer, drive_value, resolver


_DEFAULT_RANGE = PageRange(1, 2)

def phase4_proposal(
    reader: FakeNotionReader,
    *,
    operation: str = "create_usage",
    target_id: str = "MU:existing",
    desired_range: PageRange = _DEFAULT_RANGE,
    role: str = "Primary",
    material_type: str = "Lecture Slides",
    source_class: str = "professor_material",
    decision: str = "Pending",
    state: str = "PENDING_REVIEW",
    include_decision_by: str | None = None,
) -> dict[str, Any]:
    usages = reader.get_material_usage("COMP319-S05")
    scopes = material_usage_scopes(usages, session_id="COMP319-S05")
    target = next(scope for scope in scopes if scope.usage_id == target_id)
    old_snapshot = {
        "usage_id": target.usage_id,
        "session_id": target.session_id,
        "material_id": target.material_id,
        "role": target.role,
        "start_page": target.start_page,
        "end_page": target.end_page,
        "verified": target.verified,
    }
    semantics = build_material_usage_semantics(
        operation=operation,
        target_entity_id=target_id,
        session_id="COMP319-S05",
        material_id="COMP319-M03",
        course_relation_page_id=COURSE_PAGE_ID,
        course_key=COURSE_KEY,
        usage_role=role,
        material_type=material_type,
        source_class=source_class,
        old_snapshot=old_snapshot,
        desired_range=desired_range,
        session_dependency={
            "source_ref": {"provider": "google_drive", "file_id": "transcript-05"},
            "source_hash": "transcript-hash-v1",
            "source_version": 1,
        },
        material_dependency={
            "source_ref": {"provider": "google_drive", "file_id": "material-m03"},
            "source_hash": "material-hash-v1",
            "source_version": 1,
        },
        evidence=None,
        review_reason="candidate requires human verification",
        processor_version="1.2.0",
    )
    proposal_id = derive_proposal_id(
        "MATERIAL_USAGE" if operation == "create_usage" else "PAGE_RANGE",
        semantics,
    )
    properties: dict[str, Any] = {
        "Name": "Phase4 Material Usage review",
        "Proposal ID": proposal_id,
        "Proposal Type": "MATERIAL_USAGE" if operation == "create_usage" else "PAGE_RANGE",
        "State": state,
        "Course": {"relation": [{"id": COURSE_PAGE_ID}]},
        "Target Entity ID": target_id,
        "Source Ref": {"provider": "google_drive", "file_id": "material-m03"},
        "Source Hash": "material-hash-v1",
        "Source Version": 1,
        "Proposed Action": canonical_action_json(semantics),
        "Confidence": "High",
        "Evidence": None,
        "Review Reason": "candidate requires human verification",
        "Decision": decision,
    }
    if include_decision_by is not None:
        properties["Decision By"] = include_decision_by
    return properties


def phase4_applier_kwargs(reader, drive, resolver) -> dict[str, Any]:
    from uls.config.schema import UlsConfig

    return {
        "graph_reader": reader,
        "source_reader": drive,
        "source_binding_resolver": resolver,
        "config": UlsConfig(),
    }


__all__ = [
    "COURSE_KEY",
    "COURSE_PAGE_ID",
    "AutomationActor",
    "FakeNotionAdapter",
    "FakeNotionReader",
    "material_derivative",
    "phase4_applier_kwargs",
    "phase4_proposal",
    "ready_phase4",
    "transcript_derivative",
]
