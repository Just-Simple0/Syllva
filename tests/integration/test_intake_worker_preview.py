from __future__ import annotations

import hashlib
from argparse import Namespace
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import uls.cli.main as cli_main
from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, InMemoryDriveWorker
from uls.adapters.notion.intake import (
    INTAKE_SCHEMAS,
    STATUS_GROUPS,
    InMemoryNotionWorker,
    NotionAPIWorker,
    NotionIntakeWriter,
)
from uls.config.schema import (
    CourseCfg,
    CourseStaticFolderCfg,
    SemesterRegistryCfg,
    SemesterWorkspaceCfg,
    UlsConfig,
)
from uls.domain.errors import PolicyDeniedError, ProviderUnavailableError
from uls.intake.identity import provider_binding_id
from uls.intake.models import RequestType
from uls.intake.worker import IntakeReconcileRequired
from uls.retrieval.chunking import derivative_parts, page_chunks
from uls.runtime import build_intake_worker
from uls.state.reader import ReadOnlyState
from uls.state.sqlite import SQLiteStateStore

SEMESTER = "2026-2"
COURSE_KEYS = tuple(f"{SEMESTER}_TEST{101 + index}-001" for index in range(5))


def _privacy() -> dict[str, Any]:
    return {
        "owned_by_me": True,
        "permission_count": 1,
        "permission_roles": (("user", "owner"),),
        "permission_types": ("user",),
        "owner_only": True,
        "is_publicly_shared": False,
        "can_edit": True,
        "can_move": True,
    }


def _folder(file_id: str, parent: str | None = None) -> DriveMetadata:
    return DriveMetadata(
        file_id=file_id,
        name=file_id,
        mime_type=DRIVE_FOLDER_MIME,
        parents=(parent,) if parent else (),
        **_privacy(),
    )


def _pdf_bytes(page_texts: tuple[str, ...] = ("Synthetic PDF material",)) -> bytes:
    writer = PdfWriter()
    for page_text in page_texts:
        page = writer.add_blank_page(width=200, height=200)
        if not page_text:
            continue
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({page_text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@contextmanager
def _system(
    tmp_path: Path,
    *,
    raw: bytes = b"# Synthetic lecture\n\n[00:10] Binary search halves the range.\n",
    name: str = "lecture.md",
    mime_type: str = "text/markdown",
    duplicate: bool = False,
) -> Iterator[dict[str, Any]]:
    config = UlsConfig()
    config.system.workspace_dir = str(tmp_path)
    config.behavior_contract.path = str(Path("contracts/study-behavior.md").resolve())
    config.google_drive.university_root_id = "synthetic-school"
    for index, course_key in enumerate(COURSE_KEYS):
        config.courses.append(
            CourseCfg(
                course_key=course_key,
                name=f"Synthetic Course {index}",
                code=f"TEST{101 + index}",
                section="001",
                semester=SEMESTER,
            )
        )
    drive_config = SemesterRegistryCfg(
        semester=SEMESTER,
        folder_id="synthetic-semester",
        upload_folder_id="synthetic-upload",
    )
    for index, course_key in enumerate(COURSE_KEYS):
        drive_config.course_folder_ids[course_key] = f"synthetic-course-{index}"
        drive_config.course_static_folder_ids[course_key] = CourseStaticFolderCfg(
            recordings_folder_id=f"synthetic-recordings-{index}",
            materials_folder_id=f"synthetic-materials-{index}",
        )
    config.google_drive.semester_registries = [drive_config]
    notion_config = SemesterWorkspaceCfg(
        semester=SEMESTER,
        connection_settings_files_parent_id="synthetic-parent",
        academic_courses_data_source_id="synthetic-courses",
        sessions_data_source_id="synthetic-sessions",
        materials_data_source_id="synthetic-materials",
        file_intake_data_source_id="synthetic-intake",
        input_requests_data_source_id="synthetic-requests",
    )
    config.notion.semester_workspaces = [notion_config]

    files = [
        _folder("synthetic-school"),
        _folder("synthetic-semester", "synthetic-school"),
        _folder("synthetic-upload", "synthetic-semester"),
    ]
    for index in range(5):
        course_folder = f"synthetic-course-{index}"
        files.extend(
            (
                _folder(course_folder, "synthetic-semester"),
                _folder(f"synthetic-recordings-{index}", course_folder),
                _folder(f"synthetic-materials-{index}", course_folder),
            )
        )
    source_ids = ["synthetic-source"]
    source_metadata = DriveMetadata(
        file_id=source_ids[0],
        name=name,
        mime_type=mime_type,
        parents=("synthetic-upload",),
        modified_time="2026-09-13T10:00:00Z",
        size=len(raw),
        md5_checksum=hashlib.md5(raw).hexdigest(),
        **_privacy(),
    )
    files.append(source_metadata)
    contents = {source_ids[0]: raw}
    if duplicate:
        source_ids.append("synthetic-source-copy")
        files.append(
            replace(
                source_metadata,
                file_id=source_ids[1],
                name=f"copy-{name}",
            )
        )
        contents[source_ids[1]] = raw
    drive = InMemoryDriveWorker(files, contents)
    notion = InMemoryNotionWorker(
        {
            "synthetic-courses": [
                {
                    "id": f"synthetic-course-page-{index}",
                    "Course Key": course_key,
                    "Name": f"Synthetic Course {index}",
                    "Status": "Current",
                    "_parent_data_source_id": "synthetic-courses",
                }
                for index, course_key in enumerate(COURSE_KEYS)
            ],
            "synthetic-sessions": [],
            "synthetic-materials": [],
            "synthetic-intake": [],
            "synthetic-requests": [],
        }
    )
    with SQLiteStateStore(tmp_path / "state.sqlite3") as state:
        worker = build_intake_worker(
            config,
            state=state,
            drive=drive,
            notion=notion,
            provider_account_binding_id=provider_binding_id(
                "google_drive", "synthetic-owner", "synthetic-oauth"
            ),
            semester=SEMESTER,
        )
        yield {
            "config": config,
            "state": state,
            "worker": worker,
            "drive": drive,
            "notion": notion,
            "source_id": source_ids[0],
            "source_ids": source_ids,
        }


def _assign_request(notion: InMemoryNotionWorker) -> dict[str, Any]:
    return next(row for row in notion.data_sources["synthetic-requests"] if row["Request Type"] == "ASSIGN_COURSE")


def _details_request(notion: InMemoryNotionWorker) -> dict[str, Any]:
    return next(row for row in notion.data_sources["synthetic-requests"] if row["Request Type"] == "FILE_DETAILS")


def _complete_transcript_route(worker: Any, notion: InMemoryNotionWorker) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    first = worker.run_once()
    assign = _assign_request(notion)
    assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
    second = worker.run_once()
    details = _details_request(notion)
    details.update(
        {
            "Course": ["synthetic-course-page-0"],
            "Kind": "TRANSCRIPT",
            "Actual Date": {"start": "2026-09-01", "end": None},
            "Session Mode": "NEW",
            "Submitted": True,
        }
    )
    third = worker.run_once()
    return first, second, third


def _job_status(job: Any) -> str:
    value = getattr(job.status, "value", job.status)
    return str(value)


def test_three_tick_transcript_route_is_ordered_partial_safe_and_replayable(tmp_path: Path) -> None:
    with _system(tmp_path, raw=b"# [00:99]\nPartial timestamp evidence\n") as harness:
        first, second, third = _complete_transcript_route(harness["worker"], harness["notion"])
        assert first["status"] == "ok" and first["processed"] == 0
        assert first["readiness"]["retrieval_readiness"]["status"] == "NOT_PROVEN_BY_INTAKE_PREVIEW"
        assert second["status"] == "ok" and second["processed"] == 0
        assert third["status"] == "ok" and third["processed"] == 1

        state = harness["state"]
        notion = harness["notion"]
        item = state.list_intake_items()[0]
        assert item.status == "ORGANIZED"
        assert item.content_status == "Partial"
        session = notion.data_sources["synthetic-sessions"][0]
        assert session["Recording Status"] == "Partial"
        file_intake = notion.data_sources["synthetic-intake"][0]
        assert file_intake["Content Status"] == "Partial"
        job = state.list_jobs()[0]
        assert _job_status(job) == "PARTIAL"
        processing = state.get_processing_record(job.id, operation=job.operation)
        assert processing is not None and _job_status(processing) == "PARTIAL"

        stages = [event["stage"] for event in state.list_intake_stage_events(item.intake_id)]
        required = [
            "create_session_readback",
            "normalize_complete",
            "stage_validate_readback",
            "pointer_write_readback",
            "full_tuple_readback",
            "APPLIED",
            "REGISTERED",
            "move_readback",
            "provenance_readback",
        ]
        positions = [stages.index(stage) for stage in required]
        assert positions == sorted(positions)
        assert positions[5] < positions[7]
        assert harness["drive"].files[harness["source_id"]].file_id == harness["source_id"]
        assert harness["drive"].files[harness["source_id"]].parents != ("synthetic-upload",)

        readonly = ReadOnlyState(tmp_path / "state.sqlite3")
        binding = readonly.binding(session["ID"])
        resolved = ValidatedSourceBindingResolver(readonly).resolve_derivative_ref(
            session["ID"], session["Normalized Transcript"]
        )
        assert binding.source_ref.identity == ("google_drive", harness["source_id"])
        assert resolved.identity == binding.source_ref.identity

        drive_mutations = [event for event in harness["drive"].events if event[0] in {"create", "move"}]
        notion_mutations = [event for event in notion.events if event[0] in {"create", "update"}]
        replay = harness["worker"].run_once()
        assert replay["status"] == "ok" and replay["processed"] == 0
        assert drive_mutations == [event for event in harness["drive"].events if event[0] in {"create", "move"}]
        assert notion_mutations == [event for event in notion.events if event[0] in {"create", "update"}]


def test_user_checkboxes_drive_claim_and_system_status_is_not_approval(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign["Request Status"] = "Submitted"
        untouched = worker.run_once()
        assert untouched["processed"] == 0
        assert assign["Request Status"] == "Submitted"

        assign.update({"Request Status": "Draft", "Course": ["synthetic-course-page-0"], "Submitted": True})
        claimed = worker.run_once()
        assert claimed["status"] == "ok"
        assert assign["Request Status"] == "Applied"
        assert any(row["Request Type"] == "FILE_DETAILS" for row in notion.data_sources["synthetic-requests"])


def test_source_generation_change_rejects_old_submitted_receipt(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        drive = harness["drive"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        changed = b"# Changed source generation\n[00:20] Different bytes\n"
        drive.contents[harness["source_id"]] = changed
        drive.files[harness["source_id"]] = replace(
            drive.files[harness["source_id"]],
            size=len(changed),
            md5_checksum=hashlib.md5(changed).hexdigest(),
            modified_time="2026-09-13T10:01:00Z",
        )
        result = worker.run_once()
        item = harness["state"].list_intake_items()[0]
        assert result["failed"] == 1
        assert item.source_version == 2
        assert assign["Request Status"] == "Reconcile Required"
        assert harness["state"].list_jobs() == []
        assert drive.files[harness["source_id"]].parents == ("synthetic-upload",)


def test_same_hash_different_file_ids_are_duplicate_candidates(tmp_path: Path) -> None:
    with _system(tmp_path, duplicate=True) as harness:
        result = harness["worker"].run_once(process=False)
        assert result["status"] == "ok"
        items = harness["state"].list_intake_items()
        assert {item.provider_file_id for item in items} == set(harness["source_ids"])
        assert all(item.last_error_code == "DUPLICATE_CANDIDATE" for item in items)
        assert all(item.status == "NEEDS_INPUT" for item in items)
        assert len(harness["notion"].data_sources["synthetic-requests"]) == 2


def test_cancel_after_claim_blocks_subsequent_mutation(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        drive = harness["drive"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        details.update(
            {
                "Course": ["synthetic-course-page-0"],
                "Kind": "TRANSCRIPT",
                "Actual Date": {"start": "2026-09-01", "end": None},
                "Session Mode": "NEW",
                "Submitted": True,
            }
        )
        original_read_record = notion.read_record
        calls = {"count": 0}

        def flipping_read_record(data_source_id: str, page_id: str) -> dict[str, Any] | None:
            if data_source_id == "synthetic-requests":
                calls["count"] += 1
                if calls["count"] > 1:
                    # Simulate the user cancelling in Notion partway through
                    # this same processing tick, after the initial claim
                    # gate already observed Submitted=True/Cancelled=False.
                    details["Cancelled"] = True
            return original_read_record(data_source_id, page_id)

        notion.read_record = flipping_read_record  # type: ignore[method-assign]
        result = worker.run_once()
        assert result["failed"] == 1
        assert notion.data_sources["synthetic-sessions"] == []
        assert drive.files[harness["source_id"]].parents == ("synthetic-upload",)
        assert harness["state"].list_intake_items()[0].status != "ORGANIZED"


def test_response_loss_then_source_drift_recovers_pending_request_without_duplicate(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        drive = harness["drive"]
        state = harness["state"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        # The FILE_DETAILS create actually reaches the provider (the page is
        # appended before the simulated failure), but the worker never sees
        # a successful response.  This mirrors Root's drop_next_create_response
        # recipe for an indeterminate outcome.
        notion.drop_next_create_response = True
        worker.run_once()
        item = state.list_intake_items()[0]
        assert item.pending_request_key, "durable intent must be committed before the first create attempt"
        pending_receipt = state.get_request_receipt(item.pending_request_key)
        assert pending_receipt is not None and pending_receipt.provider_page_id is None
        details_before = [
            row for row in notion.data_sources["synthetic-requests"] if row["Request Type"] == "FILE_DETAILS"
        ]
        assert len(details_before) == 1, "the provider create must have actually succeeded once"
        # Source drift happens before anything resolves the lost response,
        # exactly the ordering the plan's R3 fix was meant to survive.
        changed = b"# Changed source generation\n[00:20] Different bytes\n"
        drive.contents[harness["source_id"]] = changed
        drive.files[harness["source_id"]] = replace(
            drive.files[harness["source_id"]],
            size=len(changed),
            md5_checksum=hashlib.md5(changed).hexdigest(),
            modified_time="2026-09-13T10:01:00Z",
        )
        worker.run_once()  # discovery observes the drifted bytes as a new source_version
        item_drifted = state.get_intake_item(item.intake_id)
        assert item_drifted.source_version == 2
        receipts_before_retry = len(state.list_request_receipts(provider=worker.provider))
        # A retry after drift must resolve the pending generation first and
        # then STOP: it must never fall through to computing a fresh
        # generation from the now-drifted item, which would silently
        # replace the just-recovered receipt and durably create a second,
        # never-attempted pending generation for the same item.
        result_receipt = worker.create_input_request(
            item_drifted.intake_id, request_type=RequestType.FILE_DETAILS.value
        )
        details_after = [
            row for row in notion.data_sources["synthetic-requests"] if row["Request Type"] == "FILE_DETAILS"
        ]
        assert len(details_after) == 1, "drift must not create a second Input Request"
        assert details_after[0]["id"] == details_before[0]["id"]
        assert result_receipt.request_key == item.pending_request_key, (
            "the original pending key must still be authoritative, not a drift-derived one"
        )
        assert result_receipt.provider_page_id == details_before[0]["id"], (
            "the original response-loss generation must recover to its real page"
        )
        item_after = state.get_intake_item(item.intake_id)
        assert item_after.pending_request_key == item.pending_request_key, (
            "pending_request_key must not be silently overwritten by a drift-derived key"
        )
        receipts_after_retry = len(state.list_request_receipts(provider=worker.provider))
        assert receipts_after_retry == receipts_before_retry, (
            "recovery must not durably create an orphaned second RequestReceipt"
        )


def test_pending_request_with_no_recoverable_page_blocks_a_new_generation(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        state = harness["state"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        notion.drop_next_create_response = True
        worker.run_once()
        item = state.list_intake_items()[0]
        pending_key = item.pending_request_key
        assert pending_key
        # The page the prior attempt actually created is now permanently gone
        # (for example a Notion-side deletion).  Its outcome is still
        # indeterminate from the worker's point of view, so a retry must
        # never fall through to a fresh generation.
        notion.data_sources["synthetic-requests"] = [
            row for row in notion.data_sources["synthetic-requests"] if row.get("Request Key") != pending_key
        ]
        with pytest.raises(IntakeReconcileRequired):
            worker.create_input_request(item.intake_id, request_type=RequestType.FILE_DETAILS.value)
        assert not any(
            row["Request Type"] == "FILE_DETAILS" for row in notion.data_sources["synthetic-requests"]
        )


def test_initial_system_draft_rejects_nonblank_user_choices(tmp_path: Path) -> None:
    backend = InMemoryNotionWorker({"requests": []}, parent_page_id="parent")
    writer = NotionIntakeWriter(
        backend,
        {"input_request": "requests"},
        parent_page_id="parent",
        semester=SEMESTER,
    )
    base = {
        "Name": "Input Request",
        "Request Key": "request-key",
        "Request Revision Hash": "revision",
        "Request Type": "FILE_DETAILS",
        "Intake Items": ["intake-page"],
        "Submitted": False,
        "Cancelled": False,
        "Request Status": "Draft",
        "Workspace Fingerprint": "workspace",
    }
    for field, value in {
        "Course": ["course-page"],
        "Actual Date": "2026-09-01",
        "Session Mode": "NEW",
        "Kind": "TRANSCRIPT",
        "Session No": 2,
    }.items():
        with pytest.raises(PolicyDeniedError):
            writer.create_record("input_request", {**base, field: value})
    assert backend.events == []


def test_pdf_material_route_preserves_file_id_and_publishes_provenance(tmp_path: Path) -> None:
    with _system(tmp_path, raw=_pdf_bytes(), name="slides.pdf", mime_type="application/pdf") as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        details.update(
            {
                "Course": ["synthetic-course-page-0"],
                "Kind": "MATERIAL_PDF",
                "Material Role": "Textbook",
                "Submitted": True,
            }
        )
        result = worker.run_once()
        assert result["status"] == "ok" and result["processed"] == 1
        material = notion.data_sources["synthetic-materials"][0]
        assert material["Text Status"] == "Ready"
        assert material["Text Source"] == "PDF Extract"
        assert harness["drive"].files[harness["source_id"]].file_id == harness["source_id"]
        assert harness["drive"].files[harness["source_id"]].parents != ("synthetic-upload",)
        job = harness["state"].list_jobs()[0]
        assert _job_status(job) == "READY"
        assert harness["state"].get_processing_record(job.id, operation=job.operation) is not None
        binding = ReadOnlyState(tmp_path / "state.sqlite3").binding(material["ID"])
        derivative = harness["drive"].contents[binding.derivative_ref.file_id].decode()
        assert len(page_chunks(derivative, entity_id=material["ID"])) == 1


def test_partial_pdf_keeps_empty_page_locator_and_retrieves_later_page(tmp_path: Path) -> None:
    raw = _pdf_bytes(("Page one source text", "", "Page three source text"))
    with _system(tmp_path, raw=raw, name="partial.pdf", mime_type="application/pdf") as harness:
        worker = harness["worker"]
        notion = harness["notion"]
        worker.run_once()
        assign = _assign_request(notion)
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        worker.run_once()
        details = _details_request(notion)
        details.update(
            {
                "Course": ["synthetic-course-page-0"],
                "Kind": "MATERIAL_PDF",
                "Material Role": "Textbook",
                "Submitted": True,
            }
        )
        result = worker.run_once()
        assert result["status"] == "ok" and result["processed"] == 1

        material = notion.data_sources["synthetic-materials"][0]
        assert material["Text Status"] == "Partial"
        job = harness["state"].list_jobs()[0]
        assert _job_status(job) == "PARTIAL"
        binding = ReadOnlyState(tmp_path / "state.sqlite3").binding(material["ID"])
        derivative = harness["drive"].contents[binding.derivative_ref.file_id].decode()
        _, _, _, front = derivative_parts(derivative)
        assert front["status"] == "partial"
        assert front["missing_pages"] == [2]

        chunks = page_chunks(derivative, entity_id=material["ID"])
        assert [chunk.locator.start_page for chunk in chunks] == [1, 2, 3]
        assert chunks[1].content == ""
        from uls.retrieval.chunking import select_chunks

        page_three = select_chunks(chunks, "Page three source text")
        assert [chunk.locator.start_page for chunk in page_three] == [3]
        assert page_three[0].content == "Page three source text"


def _provider_schema_payloads() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    ids = {logical: f"notion-{logical}" for logical in INTAKE_SCHEMAS}
    payloads: dict[str, dict[str, Any]] = {}
    for logical, schema in INTAKE_SCHEMAS.items():
        properties: dict[str, dict[str, Any]] = {}
        for name, spec in schema.items():
            kind = str(spec["type"])
            prop: dict[str, Any] = {"name": name, "type": kind, kind: {}}
            if kind in {"select", "status"}:
                options = list(spec.get("options", ()))
                if logical == "academic_courses" and name == "Semester":
                    options = [SEMESTER]
                prop[kind] = {"options": [{"name": option} for option in options]}
                if kind == "status":
                    prop[kind]["groups"] = {
                        group: [{"name": option} for option in values]
                        for group, values in STATUS_GROUPS[logical].items()
                    }
            elif kind == "relation":
                prop[kind] = {"data_source_id": ids[str(spec["relation"])]}
            properties[name] = prop
        payloads[logical] = {
            "id": ids[logical],
            "parent": {"type": "page_id", "page_id": "notion-parent"},
            "properties": properties,
        }
    return ids, payloads


class _DataSources:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads

    def retrieve(self, *, data_source_id: str) -> dict[str, Any]:
        return next(value for value in self.payloads.values() if value["id"] == data_source_id)


class _NotionClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.data_sources = _DataSources(payloads)


def test_notion_readiness_requires_exact_current_parent_schema_and_relations() -> None:
    ids, payloads = _provider_schema_payloads()
    writer = NotionIntakeWriter(
        NotionAPIWorker(_NotionClient(payloads)),
        ids,
        parent_page_id="notion-parent",
        semester=SEMESTER,
    )
    verified = writer.validate_workspace()
    assert verified["status"] == "VERIFIED"
    assert verified["data_sources"] == 5
    assert verified["properties"] == 76

    payloads["sessions"]["parent"] = {"type": "page_id", "page_id": "wrong-parent"}
    not_verified = writer.validate_workspace()
    assert not_verified["status"] == "NOT_VERIFIED"


def test_public_claim_and_process_entrypoints_refuse_an_existing_worker_lock(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        state = harness["state"]
        worker = harness["worker"]
        assert state.acquire_local_worker_lock()
        try:
            with pytest.raises(ProviderUnavailableError, match="already running"):
                worker.claim_request("missing")
            with pytest.raises(ProviderUnavailableError, match="already running"):
                worker.process_item("missing")
        finally:
            state.release_local_worker_lock()


@pytest.mark.parametrize(
    ("command", "sync", "process"),
    (("sync", True, False), ("process", False, True), ("run", True, True)),
)
def test_cli_routes_preview_commands_to_one_bounded_run(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    sync: bool,
    process: bool,
) -> None:
    calls: list[dict[str, Any]] = []

    class _Runner:
        def run_once(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"status": "ok", "processed": 0}

    class _Worker:
        runner = _Runner()
        state = SimpleNamespace(close=lambda: calls.append({"closed": True}))

    config = SimpleNamespace(worker=SimpleNamespace(enabled=True))
    monkeypatch.setattr(cli_main, "_config", lambda _: config)
    monkeypatch.setattr("uls.worker.build_worker", lambda _: _Worker())
    result = cli_main.dispatch(Namespace(command=command, config=Path("config.yaml"), max_jobs=7))
    assert result["status"] == "ok"
    assert calls == [{"sync": sync, "process": process, "max_jobs": 7}, {"closed": True}]
