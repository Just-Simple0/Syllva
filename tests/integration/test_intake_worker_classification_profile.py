"""P-A: worker-level contract for the v2 Notion profiles (review R6 readiness, R7 HUMAN dispatch)."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_intake_worker_preview import (
    SEMESTER,
    _assign_request,
    _details_request,
    _pdf_bytes,
    _system,
)

from uls.domain.errors import SourceUnavailableError
from uls.intake.classification import FILE_KINDS_V2
from uls.intake.worker import INTAKE_MATERIAL_OPERATION

pytestmark = pytest.mark.integration


def _rewire(system, profile: str):
    """Re-select the Notion writer after the test changed the requested profile."""

    worker = system["worker"]
    system["config"].intake.classification.schema_profile = profile
    worker._classification_profile_mismatch = None
    worker.notion = worker._wrap_notion(system["notion"], SEMESTER)
    return worker


def test_profile_mismatch_fails_readiness_closed_without_silent_downgrade(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        # c5-range-v2 needs the material_usage/automation_queue sources, which this
        # workspace does not configure: readiness is NOT_VERIFIED with the fixed reason
        # and no mutation runs; the writer is not quietly built on another profile.
        worker = _rewire(system, "c5-range-v2")
        assert worker.notion.schema_profile == "legacy5"
        readiness = worker._notion_workspace_readiness()
        assert readiness["status"] == "NOT_VERIFIED"
        assert "c5-range-v2" in readiness["reason"] and "does not match" in readiness["reason"]
        with pytest.raises(SourceUnavailableError):
            worker._require_mutation_capability()
        # The matching profile for this workspace verifies and is used as-is.
        worker = _rewire(system, "legacy5-cls")
        assert worker.notion.schema_profile == "legacy5-cls"
        assert worker._notion_workspace_readiness()["status"] == "VERIFIED"
        # No request: the default selection stays legacy5.
        worker = _rewire(system, "")
        assert worker.notion.schema_profile == "legacy5"
        assert worker._notion_workspace_readiness()["status"] == "VERIFIED"


def test_human_v2_material_kind_is_preserved_through_claim_and_dispatch(tmp_path: Path) -> None:
    with _system(tmp_path, raw=_pdf_bytes(), name="week3_lab.pdf", mime_type="application/pdf") as system:
        worker = _rewire(system, "legacy5-cls")
        notion = system["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        assert tuple(notion_kind_options(system)) == FILE_KINDS_V2
        details.update({"Course": ["synthetic-course-page-0"], "Kind": "LAB_MATERIAL",
                        "Material Role": "Professor Notes", "Submitted": True})
        result = worker.run_once()
        assert result["status"] == "ok" and result["processed"] == 1
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert item.selected_kind == "LAB_MATERIAL"
        plan = system["state"].get_intake_plan(item.plan_revision)
        assert plan.plan_authority == "HUMAN_REQUEST"
        job = system["state"].list_jobs()[0]
        assert job.operation == INTAKE_MATERIAL_OPERATION and job.plan_authority == "HUMAN_REQUEST"
        material = notion.data_sources["synthetic-materials"][0]
        assert material["Type"] == "Professor Notes" and material["Text Status"] == "Ready"
        assert system["state"].get_request_receipt(details_key(system)).state == "Applied"
        # The pre-v2 Material set was fixed when the v2 profile first verified, before this
        # HUMAN write: a later backfill never touches the row created here (r10 R5).
        assert system["state"].material_backfill_targets() == []
        material["Type"] = "Lecture Slides"
        assert worker.backfill_material_ai_kind() == {"scanned": 0, "updated": 0}
        assert notion.data_sources["synthetic-materials"][0].get("AI Kind") is None


def test_human_v2_kind_on_an_opaque_source_registers_without_retrieval(tmp_path: Path) -> None:
    # P-B2b E10/E11: the §6.1 matrix registers PROVIDED_CODE x .c opaquely (no extraction, no
    # derivative, no provenance); the HUMAN Role stays the Materials.Type.
    with _system(tmp_path, raw=b"#include <stdio.h>\n", name="tcp_server.c", mime_type="text/x-csrc") as system:
        worker = _rewire(system, "legacy5-cls")
        notion = system["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        details.update({"Course": ["synthetic-course-page-0"], "Kind": "PROVIDED_CODE",
                        "Material Role": "Reference", "Submitted": True})
        worker.run_once()
        materials = notion.data_sources["synthetic-materials"]
        assert len(materials) == 1
        assert materials[0]["Type"] == "Reference" and materials[0]["Text Status"] == "Needs Review"
        assert materials[0]["Text Source"] == "Unavailable" and not materials[0].get("Normalized Source")
        assert materials[0].get("AI Kind") is None  # a HUMAN-chosen Kind is not relabelled as AI
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        assert item.status == "ORGANIZED" and item.exposure_block_reason == "CODE_LOCATOR_CONTRACT"
        assert item.canonical_entity_id or item.canonical_source_json
        assert system["state"].list_processing_records() == [] if hasattr(system["state"], "list_processing_records") else True


def test_human_v2_kind_that_never_takes_pdf_is_rejected_before_the_pdf_path(tmp_path: Path) -> None:
    with _system(tmp_path, raw=_pdf_bytes(), name="server.pdf", mime_type="application/pdf") as system:
        worker = _rewire(system, "legacy5-cls")
        notion = system["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        details.update({"Course": ["synthetic-course-page-0"], "Kind": "PROVIDED_CODE",
                        "Material Role": "Reference", "Submitted": True})
        worker.run_once()
        assert notion.data_sources["synthetic-materials"] == []
        item = system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])
        # P-B1: the §6.1 matrix is checked at claim time, before any plan, job or
        # status write; the request goes back to Needs Input with the fixed code.
        assert item.last_error == "FORMAT_KIND_MISMATCH" and item.plan_revision is None
        assert system["state"].list_jobs() == []
        page = next(r for r in notion.data_sources["synthetic-requests"] if r["Request Type"] == "FILE_DETAILS")
        assert page["Request Status"] == "Needs Input" and "FORMAT_KIND_MISMATCH" in page["Error"]
        assert system["state"].get_request_receipt(details_key(system)).state == "Draft"


def test_material_ai_kind_backfill_is_limited_and_idempotent(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        notion = system["notion"]
        rows = notion.data_sources["synthetic-materials"]
        rows.extend([
            {"id": "mat-1", "_parent_data_source_id": "synthetic-materials", "ID": "M01",
             "Type": "Lecture Slides", "Material Role": "Lecture Slides", "AI Kind": None},
            {"id": "mat-2", "_parent_data_source_id": "synthetic-materials", "ID": "M02",
             "Type": "Textbook", "Material Role": "Textbook", "AI Kind": None},
            {"id": "mat-3", "_parent_data_source_id": "synthetic-materials", "ID": "M03",
             "Type": "Lecture Slides", "Material Role": "Lecture Slides", "AI Kind": "LAB_MATERIAL"},
        ])
        worker = system["worker"]
        with pytest.raises(SourceUnavailableError):
            worker.backfill_material_ai_kind()  # legacy profile: never
        assert system["state"].material_backfill_targets() is None
        worker = _rewire(system, "legacy5-cls")
        assert worker._notion_workspace_readiness()["status"] == "VERIFIED"
        assert [t["page_id"] for t in system["state"].material_backfill_targets()] == ["mat-1", "mat-2", "mat-3"]
        assert worker.backfill_material_ai_kind() == {"scanned": 3, "updated": 1}
        by_id = {row["id"]: row for row in notion.data_sources["synthetic-materials"]}
        assert by_id["mat-1"]["AI Kind"] == "LECTURE_SLIDES" and by_id["mat-1"]["Type"] == "Lecture Slides"
        assert by_id["mat-2"]["AI Kind"] is None  # Textbook stays NULL (plan §5 O1)
        assert by_id["mat-3"]["AI Kind"] == "LAB_MATERIAL"  # an existing Kind is never overwritten
        assert worker.backfill_material_ai_kind() == {"scanned": 3, "updated": 0}
        # A Material created after the transition (HUMAN MATERIAL_PDF compatibility input,
        # Type=Lecture Slides) is never a backfill target on a re-run (r8 R4).
        rows.append({"id": "mat-4", "_parent_data_source_id": "synthetic-materials", "ID": "M04",
                     "Type": "Lecture Slides", "Material Role": "Lecture Slides", "AI Kind": None})
        assert worker.backfill_material_ai_kind() == {"scanned": 3, "updated": 0}
        by_id = {row["id"]: row for row in notion.data_sources["synthetic-materials"]}
        assert by_id["mat-4"]["AI Kind"] is None and by_id["mat-4"]["Type"] == "Lecture Slides"
        updates = [e for e in notion.events if e[0] == "update" and e[1] == "synthetic-materials"]
        assert len(updates) == 1 and set(updates[0][3]) == {"AI Kind"}
        # Remote success with a lost local mark: the readback completes the ledger without a
        # second Notion write (r13 O1).
        system["state"]._connection.execute(
            "UPDATE material_ai_kind_backfill SET applied_at = NULL WHERE page_id = 'mat-1'")
        system["state"]._connection.commit()
        assert worker.backfill_material_ai_kind() == {"scanned": 3, "updated": 0}
        assert all(t["applied_at"] for t in system["state"].material_backfill_targets() if t["page_id"] == "mat-1")
        assert len([e for e in notion.events if e[0] == "update" and e[1] == "synthetic-materials"]) == 1


def test_material_ai_kind_backfill_with_no_pre_v2_materials_never_adopts_later_rows(tmp_path: Path) -> None:
    with _system(tmp_path) as system:
        notion = system["notion"]
        worker = _rewire(system, "legacy5-cls")
        assert worker.backfill_material_ai_kind() == {"scanned": 0, "updated": 0}
        # A Material created after the (empty) snapshot is never a target (r9 R3).
        notion.data_sources["synthetic-materials"].append(
            {"id": "mat-new", "_parent_data_source_id": "synthetic-materials", "ID": "M09",
             "Type": "Lecture Slides", "Material Role": "Lecture Slides", "AI Kind": None})
        assert worker.backfill_material_ai_kind() == {"scanned": 0, "updated": 0}
        assert notion.data_sources["synthetic-materials"][0]["AI Kind"] is None
        assert not [e for e in notion.events if e[0] == "update" and e[1] == "synthetic-materials"]


def notion_kind_options(system) -> tuple[str, ...]:
    return system["worker"].notion.schemas["input_request"]["Kind"]["options"]


def details_key(system) -> str:
    receipts = [r for r in system["state"].list_request_receipts() if r.request_type == "FILE_DETAILS"]
    assert len(receipts) == 1
    return receipts[0].request_key
