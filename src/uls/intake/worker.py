"""Executable local semester intake worker.

The worker is deliberately provider-neutral above the two worker ports.  It
commits observations and write attempts before external calls, validates full
provider readbacks, and only moves an original Drive file after a canonical
binding is registered.  It never calls an AI provider or the read-only MCP
surface.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    DriveMetadata,
    DriveWorkerPort,
    ensure_marked_folder,
)
from uls.adapters.notion.intake import NotionIntakeWriter, NotionWorkerPort
from uls.config.intake import (
    IntakeConfigurationError,
    ResolvedSemesterWorkspace,
    resolve_configured_semester,
)
from uls.domain.errors import (
    PolicyDeniedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
    UlsError,
)
from uls.domain.ids import parse_course_key, parse_entity_id
from uls.domain.source_ref import SourceRef
from uls.ingestion.discovery import discover_intake
from uls.intake.identity import (
    derivative_marker,
    derive_operation_key,
    derive_request_key,
    derive_request_revision,
    derive_status_revision,
    folder_marker_key,
    sha256_hex,
)
from uls.intake.models import FileKind, IntakeStatus, RequestInput, RequestType, SessionMode
from uls.intake.planner import make_plan_revision, make_request_generation
from uls.intake.registry import validate_registered_drive_layout
from uls.intake.requests import (
    RequestGeneration,
    RequestValidationError,
    normalized_user_hash,
    request_from_mapping,
    validate_request_input,
)
from uls.normalization.pdf import PDFContentStatus, extract_pdf
from uls.normalization.transcript import normalize_transcript
from uls.normalization.validators import validate_normalized_transcript
from uls.state.models import EntityReservation, IntakeItem, IntakePlan, RequestReceipt

INTAKE_DISCOVER_OPERATION = "INTAKE_DISCOVER_V1"
INTAKE_REQUEST_SYNC_OPERATION = "INTAKE_REQUEST_SYNC_V1"
INTAKE_FOLDER_OPERATION = "INTAKE_ENTITY_FOLDER_PREPARE_V1"
INTAKE_DERIVATIVE_OPERATION = "INTAKE_DERIVATIVE_PUBLISH_V1"
INTAKE_SESSION_OPERATION = "INTAKE_SESSION_REGISTER_V1"
INTAKE_MATERIAL_OPERATION = "INTAKE_MATERIAL_REGISTER_V1"
INTAKE_MOVE_OPERATION = "INTAKE_MOVE_V1"
INTAKE_STATUS_OPERATION = "INTAKE_STATUS_PROJECT_V1"
INTAKE_OPERATIONS = frozenset(
    {
        INTAKE_DISCOVER_OPERATION,
        INTAKE_REQUEST_SYNC_OPERATION,
        INTAKE_FOLDER_OPERATION,
        INTAKE_DERIVATIVE_OPERATION,
        INTAKE_SESSION_OPERATION,
        INTAKE_MATERIAL_OPERATION,
        INTAKE_MOVE_OPERATION,
        INTAKE_STATUS_OPERATION,
    }
)


class IntakeReconcileRequired(UlsError):
    code = "RECONCILE_REQUIRED"


class IntakeWorker:
    """One local, bounded, single-active worker for configured semesters."""

    def __init__(
        self,
        config: Any,
        state: Any,
        drive: DriveWorkerPort,
        notion: NotionWorkerPort | NotionIntakeWriter | None = None,
        *,
        provider_account_binding_id: str = "",
        semester: str | None = None,
        max_files: int = 10_000,
    ) -> None:
        self.config = config
        self.state = state
        self.drive = drive
        self.provider_account_binding_id = provider_account_binding_id
        self.max_files = max_files
        self.workspaces = self._resolve_workspaces(semester)
        selected_semester = semester or self.workspaces[0].semester
        self.notion = (
            notion
            if isinstance(notion, NotionIntakeWriter)
            else self._wrap_notion(notion, selected_semester)
        )
        self._course_rows: dict[str, dict[str, Any]] = {}
        # The existing CLI expects every worker composition to expose a
        # ``runner`` with ``run_once``.  Keeping this alias makes the preview
        # worker a drop-in local runner without importing the legacy pipeline.
        self.runner = self

    @property
    def provider(self) -> str:
        return "google_drive"

    @property
    def intake_ready(self) -> bool:
        return (
            bool(self.provider_account_binding_id)
            and self.drive.capabilities.full_intake
            and self.notion is not None
            and self._notion_workspace_readiness().get("status") == "VERIFIED"
        )

    def readiness(self) -> dict[str, Any]:
        current = self.workspaces[0] if self.workspaces else None
        notion_readiness = self._notion_workspace_readiness()
        configured = bool(current and self.notion and self.provider_account_binding_id)
        if not configured:
            preview_status = "CONFIGURATION_INVALID"
            full_status = "CONFIGURATION_INVALID"
        elif notion_readiness.get("status") != "VERIFIED":
            preview_status = "NOT_VERIFIED"
            full_status = "NOT_VERIFIED"
        else:
            preview_status = "READY"
            full_status = "READY" if self.drive.capabilities.full_intake else "UNSUPPORTED"
        return {
            "intake_preview_readiness": preview_status,
            "full_intake_activation": full_status,
            # The intake writer's five-data-source registration and SQLite
            # provenance do not attest the separate read-only MCP composition
            # that still uses the legacy seven global data-source IDs.
            "retrieval_readiness": {
                "status": "NOT_PROVEN_BY_INTAKE_PREVIEW",
                "legacy_global_registry": "SEPARATE",
                "custom_mcp_client": "NOT_PROVEN",
            },
            "notion_workspace": notion_readiness,
            "provider_capability": asdict(self.drive.capabilities),
            "semester_count": len(self.workspaces),
            "sources_json_optional": True,
        }

    def sync(self) -> int:
        """Discover current uploads under the single-active worker lock."""

        if not self.state.acquire_local_worker_lock():
            return 0
        try:
            return self._sync_unlocked()
        finally:
            self.state.release_local_worker_lock()

    def _sync_unlocked(self) -> int:
        """Discover current registered upload folders after the lock is held."""

        if self.provider_account_binding_id and self.notion is not None:
            self._require_notion_workspace_verified()
        total = 0
        for semester, workspaces in self._group_workspaces().items():
            workspace = workspaces[0]
            validate_registered_drive_layout(self.drive, workspaces)
            config_fingerprint = self._config_fingerprint(workspaces)
            workspace_fingerprint = self._workspace_fingerprint(workspaces)
            self.state.register_semester_registration(
                semester=semester,
                config_fingerprint=config_fingerprint,
                workspace_fingerprint=workspace_fingerprint,
                drive_static_ids_json={item.course_key: item.as_dict()["drive"] for item in workspaces},
                notion_resolved_ids_json={item.course_key: item.as_dict()["notion"] for item in workspaces},
                provider_account_binding_id=self.provider_account_binding_id or "unbound",
            )
            optional = {
                item.course_key: item.optional_upload_folder_id
                for item in workspaces
                if item.optional_upload_folder_id
            }
            # The semester upload folder is shared.  Discover it once per
            # semester and pass all explicitly registered course-upload
            # folders; calling discovery once per course would re-observe the
            # shared root files five times and create false routing history.
            discovery = discover_intake(
                self.drive,
                workspace,
                self.state,
                config_fingerprint=config_fingerprint,
                provider=self.provider,
                max_files=self.max_files,
                optional_course_uploads=optional,
            )
            total += discovery.count
            for item in discovery.items:
                if self.provider_account_binding_id:
                    self._project_file_intake_safe(item, workspace, workspace_fingerprint)
                if item.status == IntakeStatus.NEEDS_INPUT.value and self.provider_account_binding_id:
                    request_type = self._initial_request_type(item)
                    try:
                        self.create_input_request(item.intake_id, request_type=request_type)
                    except (
                        IntakeReconcileRequired,
                        ProviderUnavailableError,
                        SourceUnavailableError,
                        NotImplementedError,
                    ) as exc:
                        self._record_item_error(
                            item,
                            workspace,
                            exc,
                            default_status=IntakeStatus.NEEDS_INPUT,
                        )
        return total

    def run_once(self, *, sync: bool = True, process: bool = True, max_jobs: int = 100) -> dict[str, Any]:
        """Run one bounded local tick under the existing StateStore lock."""

        if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
            raise ValueError("max_jobs must be 1–1000")
        if not self.state.acquire_local_worker_lock():
            return {"status": "already_running", "discovered": 0, "processed": 0}
        try:
            discovered = self._sync_unlocked() if sync else 0
            processed = failed = needs_input = 0
            if process:
                for request_key in self._submitted_request_keys()[:max_jobs]:
                    try:
                        self._claim_request_unlocked(request_key)
                    except RequestValidationError as exc:
                        self._record_request_error(request_key, exc)
                        needs_input += 1
                    except (
                        IntakeReconcileRequired,
                        ProviderUnavailableError,
                        SourceUnavailableError,
                        NotImplementedError,
                    ) as exc:
                        self._record_request_error(request_key, exc)
                        failed += 1
                jobs = [
                    job
                    for job in self.state.list_jobs(limit=1000)
                    if job.operation in {INTAKE_SESSION_OPERATION, INTAKE_MATERIAL_OPERATION}
                    and str(job.status) == "PENDING"
                ][:max_jobs]
                for pending in jobs:
                    job = self.state.claim_job(pending.id)
                    if job is None:
                        continue
                    try:
                        result = self._process_item_unlocked(job.target_entity_id)
                        status = result.get("status", IntakeStatus.ORGANIZED.value)
                        content_status = result.get("content_status")
                        self.state.complete_job(
                            job.id,
                            status=_job_status(status, content_status),
                        )
                        self._mark_request_applied(job.target_entity_id, result)
                        processed += 1
                    except RequestValidationError as exc:
                        self._record_job_error(job, exc, default_status=IntakeStatus.NEEDS_INPUT)
                        needs_input += 1
                    except Exception as exc:  # noqa: BLE001 - worker records a safe durable failure
                        self._record_job_error(job, exc)
                        failed += 1
            return {
                "status": "failed" if failed else "needs_input" if needs_input else "ok",
                "discovered": discovered,
                "processed": processed,
                "failed": failed,
                "needs_input": needs_input,
                "readiness": self.readiness(),
            }
        finally:
            self.state.release_local_worker_lock()

    def _submitted_request_keys(self) -> list[str]:
        """Return current receipts whose USER submit checkboxes are ready.

        ``Request Status`` is deliberately absent from this predicate.  It is
        a worker-owned projection and therefore cannot be used as a USER
        approval signal.  The receipt and page identity checks still prevent
        arbitrary pages from entering the worker queue.
        """

        if self.notion is None:
            return []
        receipts = {
            receipt.request_key: receipt
            for receipt in self.state.list_request_receipts(provider=self.provider)
        }
        eligible: list[str] = []
        for page in self.notion.list_records("input_request"):
            page_id = _page_id(page)
            request_key = page.get("Request Key")
            if (
                page_id is None
                or not isinstance(request_key, str)
                or type(page.get("Submitted")) is not bool
                or type(page.get("Cancelled")) is not bool
                or page["Submitted"] is not True
                or page["Cancelled"] is not False
            ):
                continue
            receipt = receipts.get(request_key)
            if receipt is None or receipt.provider_page_id != page_id:
                continue
            if receipt.state in {"Applied", "Cancelled"}:
                continue
            if receipt.state == "Claimed":
                # A claimed receipt has a durable plan/job.  Re-entering it
                # here would duplicate the provider work while a prior tick
                # is still recoverable from SQLite.
                continue
            eligible.append(request_key)
        return sorted(set(eligible))

    def _record_item_error(
        self,
        item: IntakeItem,
        workspace: ResolvedSemesterWorkspace,
        error: BaseException,
        *,
        default_status: IntakeStatus | str = IntakeStatus.RETRYABLE_ERROR,
    ) -> IntakeItem:
        """Persist an actionable intake failure and project File Intake.

        Provider details are intentionally reduced to the stable ULS error
        class and a short safe message.  The local state is updated before a
        best-effort Notion projection so a projection failure cannot erase the
        actionable result of discovery or processing.
        """

        status, error_code = _intake_error_state(error, default_status)
        item = self.state.update_intake_item(
            item.intake_id,
            status=status,
            last_error_code=error_code,
            last_error=_safe_error_text(error),
        )
        if isinstance(error, SourcePartialError) and item.content_status == "Pending":
            item = self.state.update_intake_item(
                item.intake_id,
                content_status="Partial",
            )
        if self.notion is not None:
            try:
                self._project_file_intake(item, workspace, self._semester_workspace_fingerprint(workspace.semester))
            except (IntakeReconcileRequired, PolicyDeniedError, ProviderUnavailableError, SourceUnavailableError):
                # The durable item/error is the actionable local state.  A
                # later tick can retry the system-only projection.
                pass
        return item

    def _record_request_error(self, request_key: str, error: BaseException) -> None:
        """Record a request failure without touching USER input fields."""

        receipt = self.state.get_request_receipt(request_key)
        if receipt is None:
            return
        try:
            item = self._item_for_receipt(receipt)
            workspace = self._workspace_for_item(item)
        except (IntakeConfigurationError, SourceUnavailableError):
            return
        self._record_item_error(item, workspace, error, default_status=IntakeStatus.NEEDS_INPUT)
        if self.notion is None or not receipt.provider_page_id:
            return
        page = self.notion.read_record("input_request", receipt.provider_page_id)
        if page is None:
            return
        request = self._request_from_page(page, workspace)
        if request.request_key != receipt.request_key:
            return
        if isinstance(error, RequestValidationError):
            status = "Needs Input"
        elif isinstance(error, IntakeReconcileRequired):
            status = "Reconcile Required"
        elif isinstance(error, (ProviderUnavailableError, SourceUnavailableError)):
            status = "Failed"
        else:
            status = "Failed"
        try:
            self._guarded_notion_update(
                receipt,
                request,
                workspace,
                "input_request",
                receipt.provider_page_id,
                {"Request Status": status, "Error": _safe_error_text(error)},
                operation_key=self._operation_key(
                    INTAKE_REQUEST_SYNC_OPERATION,
                    self.provider,
                    request_key,
                    receipt.request_revision_hash,
                    "error",
                    getattr(error, "code", type(error).__name__),
                ),
                attempt_operation=INTAKE_REQUEST_SYNC_OPERATION,
            )
        except (IntakeReconcileRequired, PolicyDeniedError, ProviderUnavailableError, SourceUnavailableError):
            # _record_item_error already left a durable actionable File Intake
            # state.  A changed USER request must never be rewritten here.
            return

    def _record_job_error(
        self,
        job: Any,
        error: BaseException,
        *,
        default_status: IntakeStatus | str = IntakeStatus.RETRYABLE_ERROR,
    ) -> None:
        """Persist a safe job failure and its File Intake/request projection."""

        item = None
        workspace = None
        if isinstance(getattr(job, "target_entity_id", None), str):
            item = self.state.get_intake_item(job.target_entity_id)
            if item is not None:
                try:
                    workspace = self._workspace_for_item(item)
                except IntakeConfigurationError:
                    workspace = None
        if item is not None and workspace is not None:
            self._record_item_error(item, workspace, error, default_status=default_status)

        error_class = _error_class(error)
        message = _safe_error_text(error)
        if isinstance(error, RequestValidationError):
            self.state.complete_job(
                job.id,
                status="NEEDS_REVIEW",
                error_class="POLICY_DENIED",
                last_error=message,
            )
        elif isinstance(error, SourcePartialError):
            self.state.complete_job(
                job.id,
                status="PARTIAL",
                error_class="PERMANENT",
                last_error=message,
            )
        elif isinstance(error, (IntakeReconcileRequired, PolicyDeniedError)):
            self.state.complete_job(
                job.id,
                status="NEEDS_REVIEW",
                error_class=error_class,
                last_error=message,
            )
        else:
            self.state.fail_job(job.id, error_class=error_class, last_error=message)
            if error_class in {"TRANSIENT", "RATE_LIMITED"}:
                self.state.requeue_job(job.id, error_class, last_error=message)

        if item is not None and item.plan_revision:
            receipt = self._receipt_for_plan(self.state.get_intake_plan(item.plan_revision)) if self.state.get_intake_plan(item.plan_revision) else None
            if receipt is not None:
                self._record_request_error(receipt.request_key, error)

    def _mark_request_applied(self, intake_id: str, result: Mapping[str, Any]) -> None:
        """Close the claimed request only after the intake path succeeds."""

        item = self._require_item(intake_id)
        if not item.plan_revision:
            return
        plan = self.state.get_intake_plan(item.plan_revision)
        if plan is None:
            return
        receipt = self._receipt_for_plan(plan)
        if receipt is None or not receipt.provider_page_id:
            return
        workspace = self._workspace_for_item(item)
        request = self._request_from_receipt(receipt, workspace)
        content_status = result.get("content_status") or "Ready"
        if content_status not in {"Ready", "Partial", "Needs Review", "Unavailable", "Failed", "Pending"}:
            content_status = "Ready"
        reference = result.get("entity_id") or result.get("file_id") or intake_id
        self._guarded_notion_update(
            receipt,
            request,
            workspace,
            "input_request",
            receipt.provider_page_id,
            {
                "Request Status": "Applied",
                "Result Status": content_status,
                "Result Reference": str(reference),
            },
            operation_key=self._operation_key(
                INTAKE_REQUEST_SYNC_OPERATION,
                self.provider,
                receipt.request_key,
                receipt.request_revision_hash,
                plan.plan_revision,
                "result",
            ),
            attempt_operation=INTAKE_REQUEST_SYNC_OPERATION,
        )
        self.state.update_request_receipt(receipt.request_key, state="Applied")

    def create_input_request(
        self,
        intake_id: str,
        *,
        request_type: str = RequestType.FILE_DETAILS.value,
        target_snapshot: Mapping[str, Any] | None = None,
    ) -> RequestReceipt:
        """Create or recover one initial SYSTEM request projection."""

        item = self._require_item(intake_id)
        workspace = self._workspace_for_item(item)
        self._require_binding()
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        config_fingerprint = self._semester_config_fingerprint(workspace.semester)
        workspace_fingerprint = self._semester_workspace_fingerprint(workspace.semester)
        if item.pending_request_key:
            pending_receipt = self.state.get_request_receipt(item.pending_request_key)
            if pending_receipt is not None and pending_receipt.provider_page_id is None:
                recovered_receipt = self._recover_or_block_pending_request(item, workspace)
                if recovered_receipt is not None:
                    # A successful recovery of the pending generation is
                    # authoritative for this call.  Falling through to
                    # compute a fresh generation here would silently
                    # replace a just-recovered, correctly bound receipt
                    # with a new one derived from whatever the item's
                    # current (possibly drifted) state now is -- exactly
                    # the duplicate-generation defect this guard exists
                    # to close.  A genuinely new request for this item
                    # only happens on a later call for a different
                    # request_type once this one is resolved.
                    return recovered_receipt
        generation = make_request_generation(
            item=item,
            request_type=request_type,
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            config_fingerprint=config_fingerprint,
            target_snapshot=target_snapshot,
        )
        # This is intentionally committed before the first create attempt.
        # Durable intent uses the item's stable identity: once persisted,
        # this key does not change even if item.source_hash/version drift
        # before the next tick, so a lost response can always be recovered
        # or explicitly reconciled under its original key.
        if item.pending_request_key != generation.request_key:
            item = self.state.update_intake_item(item.intake_id, pending_request_key=generation.request_key)
        receipt = self.state.create_request_receipt(
            provider=self.provider,
            input_requests_data_source_id=workspace.input_requests_data_source_id,
            request_key=generation.request_key,
            request_revision_hash=generation.request_revision_hash,
            target_snapshot_hash=generation.target_snapshot_hash,
            state="Draft",
            workspace_fingerprint=workspace_fingerprint,
            request_type=generation.request_type,
            intake_ids_json=json.dumps(list(generation.intake_ids)),
        )
        existing = self._find_request_by_key(generation.request_key)
        if len(existing) > 1:
            raise IntakeReconcileRequired("multiple Input Request pages share one Request Key")
        attempt_key = derive_operation_key(
            INTAKE_REQUEST_SYNC_OPERATION,
            self.provider,
            workspace.input_requests_data_source_id,
            generation.request_key,
            generation.request_revision_hash,
        )
        prior = self.state.get_provider_write_attempt(attempt_key)
        if existing:
            page = existing[0]
            self._validate_request_page_tuple(page, generation, workspace)
            page_id = _page_id(page)
            if page_id is None:
                raise SourceUnavailableError("Input Request page has no provider ID")
            if receipt.provider_page_id not in (None, page_id):
                raise IntakeReconcileRequired("Request Key points to a different provider page")
            if prior is not None and prior.response_state != "READBACK_OK":
                self.state.update_provider_write_attempt(
                    attempt_key,
                    target_id=page_id,
                    response_state="READBACK_OK",
                    readback_json=page,
                )
            self.state.bind_request_page(generation.request_key, page_id)
        else:
            if prior is not None:
                raise IntakeReconcileRequired("Input Request create outcome is indeterminate")
            properties = {
                "Name": f"입력 필요 · {item.original_name}",
                "Request Key": generation.request_key,
                "Request Revision Hash": generation.request_revision_hash,
                "Request Type": request_type,
                "Intake Items": [self._ensure_file_intake_page(item, workspace, workspace_fingerprint)],
                "Submitted": False,
                "Cancelled": False,
                "Request Status": "Draft",
                "Workspace Fingerprint": workspace_fingerprint,
            }
            self.state.record_provider_write_attempt(
                operation=INTAKE_REQUEST_SYNC_OPERATION,
                operation_key=attempt_key,
                provider=self.provider,
                target_id=None,
                response_state="PREPARED",
            )
            self.state.record_intake_stage_event("request_write_attempt_started", intake_id=item.intake_id, operation_key=attempt_key)
            try:
                page = self.notion.create_record("input_request", properties)
            except Exception:
                self.state.update_provider_write_attempt(attempt_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
                raise
            page_id = _page_id(page)
            if page_id is None:
                self.state.update_provider_write_attempt(attempt_key, response_state="UNKNOWN")
                raise SourceUnavailableError("Input Request create returned no provider ID")
            self.state.update_provider_write_attempt(
                attempt_key,
                target_id=page_id,
                dispatched_at=_utc_now(),
                response_state="DISPATCHED",
            )
            readback = self.notion.read_record("input_request", page_id)
            if readback is None:
                self.state.update_provider_write_attempt(attempt_key, response_state="UNKNOWN")
                raise SourceUnavailableError("Input Request page create readback is missing")
            self._validate_request_page_tuple(readback, generation, workspace)
            self.state.update_provider_write_attempt(
                attempt_key,
                response_state="READBACK_OK",
                readback_json=readback,
            )
            self.state.bind_request_page(generation.request_key, page_id)
        receipt = self.state.get_request_receipt(generation.request_key)
        if receipt is None:
            raise SourceUnavailableError("request receipt disappeared after provider readback")
        self.state.update_intake_item(
            item.intake_id,
            request_revision_hash=generation.request_revision_hash,
            input_request_page_id=receipt.provider_page_id,
        )
        self._project_file_intake(
            self._require_item(item.intake_id),
            workspace,
            workspace_fingerprint,
            input_request_link=_notion_link(receipt.provider_page_id),
        )
        return receipt

    def claim_request(self, request_key: str) -> dict[str, Any]:
        """Claim one submitted request under the single-active-worker lock."""

        self._acquire_public_worker_lock()
        try:
            return self._claim_request_unlocked(request_key)
        finally:
            self.state.release_local_worker_lock()

    def _claim_request_unlocked(self, request_key: str) -> dict[str, Any]:
        """Validate one USER-submitted request and enqueue its immutable plan."""

        receipt = self.state.get_request_receipt(request_key)
        if receipt is None or not receipt.provider_page_id:
            raise SourceUnavailableError("Request Key is not bound to a provider page")
        item_hint = self._item_for_receipt(receipt)
        workspace = self._workspace_for_item(item_hint)
        if receipt.input_requests_data_source_id != workspace.input_requests_data_source_id:
            raise IntakeReconcileRequired("request belongs to a different current workspace")
        page = self.notion.read_record("input_request", receipt.provider_page_id) if self.notion else None
        if page is None:
            raise SourceUnavailableError("submitted Input Request page is unavailable")
        request = self._request_from_page(page, workspace)
        if request.request_key != receipt.request_key:
            raise IntakeReconcileRequired("Request Key was tampered or changed")
        self._assert_request_generation_current(receipt, request, workspace)
        errors = validate_request_input(
            request,
            intake_exists=lambda value: self.state.get_intake_item(value) is not None,
            course_exists=lambda value: value in self._course_page_map(workspace),
            session_course=lambda value: self._session_course_key(value, workspace),
        )
        if errors:
            if self.notion:
                self._guarded_notion_update(
                    receipt,
                    request,
                    workspace,
                    "input_request",
                    receipt.provider_page_id,
                    {"Request Status": "Needs Input", "Error": "; ".join(errors)},
                    operation_key=self._operation_key(
                        INTAKE_REQUEST_SYNC_OPERATION,
                        self.provider,
                        receipt.request_key,
                        receipt.request_revision_hash,
                        "validation",
                    ),
                    attempt_operation=INTAKE_REQUEST_SYNC_OPERATION,
                )
            raise RequestValidationError(errors)
        if request.request_revision_hash != receipt.request_revision_hash:
            raise IntakeReconcileRequired("request revision hash was tampered or changed")
        user_hash = normalized_user_hash(request)
        if request.input_hash and request.input_hash != user_hash:
            raise IntakeReconcileRequired("Input Hash does not match USER fields")
        target_snapshot = self._request_target_snapshot(request, workspace)
        plan_revision = make_plan_revision(
            request_key=request.request_key,
            request_revision_hash=receipt.request_revision_hash,
            normalized_user_hash=user_hash,
            target_snapshot=target_snapshot,
            workspace_fingerprint=self._semester_workspace_fingerprint(workspace.semester),
            provider=self.provider,
        )
        # The receipt check occurs before this system status write.  It is the
        # first post-claim mutation and does not rewrite any USER property.
        self._assert_receipt_unchanged(receipt, request, workspace)
        self._guarded_notion_update(
            receipt,
            request,
            workspace,
            "input_request",
            receipt.provider_page_id,
            {"Input Hash": user_hash, "Plan Revision": plan_revision, "Request Status": "Claimed"},
            operation_key=self._operation_key(
                INTAKE_REQUEST_SYNC_OPERATION,
                self.provider,
                receipt.request_key,
                receipt.request_revision_hash,
                "claim",
            ),
            attempt_operation=INTAKE_REQUEST_SYNC_OPERATION,
        )
        post = self.notion.read_record("input_request", receipt.provider_page_id) if self.notion else None
        if post is None:
            raise SourceUnavailableError("request status readback is missing")
        post_request = self._request_from_page(post, workspace)
        if normalized_user_hash(post_request) != user_hash or not post_request.submitted or post_request.cancelled:
            raise IntakeReconcileRequired("USER request changed during claim")
        self.state.update_request_receipt(
            request_key,
            normalized_user_hash=user_hash,
            plan_revision=plan_revision,
            state="Claimed",
        )
        plans: list[dict[str, Any]] = []
        for intake_id in request.intake_ids:
            item = self._require_item(intake_id)
            if request.request_type == RequestType.ASSIGN_COURSE.value:
                self.state.update_intake_item(item.intake_id, selected_course_key=request.course_key, status=IntakeStatus.NEEDS_INPUT.value)
                self._project_file_intake(
                    self._require_item(item.intake_id),
                    workspace,
                    self._semester_workspace_fingerprint(workspace.semester),
                    receipt=receipt,
                )
                self._guarded_notion_update(
                    receipt,
                    request,
                    workspace,
                    "input_request",
                    receipt.provider_page_id,
                    {
                        "Request Status": "Applied",
                        "Result Status": "Ready",
                        "Result Reference": request.course_key,
                    },
                    operation_key=self._operation_key(
                        INTAKE_REQUEST_SYNC_OPERATION,
                        self.provider,
                        receipt.request_key,
                        receipt.request_revision_hash,
                        "assignment",
                    ),
                    attempt_operation=INTAKE_REQUEST_SYNC_OPERATION,
                )
                self.state.update_request_receipt(request_key, state="Applied")
                next_receipt = self.create_input_request(
                    item.intake_id,
                    request_type=RequestType.FILE_DETAILS.value,
                )
                plans.append({"intake_id": item.intake_id, "next_request_key": next_receipt.request_key})
                continue
            target = dict(target_snapshot)
            target["intake_id"] = intake_id
            plan_hash = sha256_hex(["intake.plan-record.v1", plan_revision, intake_id, target])
            plan = self.state.create_intake_plan(
                intake_id=intake_id,
                request_revision_hash=receipt.request_revision_hash,
                plan_revision=plan_revision,
                resolved_workspace_fingerprint=self._semester_workspace_fingerprint(workspace.semester),
                target_snapshot_json=target,
                plan_hash=plan_hash,
                status=IntakeStatus.PLANNED.value,
            )
            self.state.update_intake_item(
                intake_id,
                selected_course_key=request.course_key,
                selected_kind=request.kind,
                plan_revision=plan_revision,
                status=IntakeStatus.PLANNED.value,
            )
            operation = INTAKE_SESSION_OPERATION if request.kind == FileKind.TRANSCRIPT.value else INTAKE_MATERIAL_OPERATION
            operation_key = self._plan_operation_key(operation, item, plan, workspace, request)
            self._enqueue_plan_job(operation, operation_key, item, plan)
            plans.append({"intake_id": intake_id, "plan_revision": plan_revision, "operation": operation})
        return {"status": "claimed", "request_key": request_key, "plans": plans}

    def process_item(self, intake_id: str) -> dict[str, Any]:
        """Process one claimed item under the single-active-worker lock."""

        self._acquire_public_worker_lock()
        try:
            return self._process_item_unlocked(intake_id)
        finally:
            self.state.release_local_worker_lock()

    def _process_item_unlocked(self, intake_id: str) -> dict[str, Any]:
        """Execute a previously claimed plan, preserving the ordered gates."""

        item = self._require_item(intake_id)
        if not item.plan_revision:
            raise RequestValidationError(("intake item has no claimed plan",))
        plan = self.state.get_intake_plan(item.plan_revision)
        if plan is None:
            raise SourceUnavailableError("durable intake plan is missing")
        receipt = self._receipt_for_plan(plan)
        if receipt is None or not receipt.provider_page_id:
            raise SourceUnavailableError("plan has no submitted request receipt")
        workspace = self._workspace_for_item(item)
        request_page = self.notion.read_record("input_request", receipt.provider_page_id) if self.notion else None
        if request_page is None:
            raise SourceUnavailableError("request receipt page is unavailable")
        request = self._request_from_page(request_page, workspace)
        self._assert_receipt_unchanged(receipt, request, workspace)
        if request.cancelled or not request.submitted:
            raise RequestValidationError(("request is cancelled or no longer submitted",))
        if request.kind == FileKind.TRANSCRIPT.value:
            return self._process_transcript(item, plan, receipt, request, workspace)
        if request.kind == FileKind.MATERIAL_PDF.value:
            return self._process_material(item, plan, receipt, request, workspace)
        raise RequestValidationError(("unsupported plan kind",))

    # ------------------------------------------------------------------
    # Ordered transcript/material paths
    # ------------------------------------------------------------------
    def _process_transcript(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
    ) -> dict[str, Any]:
        self._require_mutation_capability()
        course_id = self._course_id(request.course_key or item.selected_course_key or "", workspace)
        reservation, existing_page = self._reserve_session(item, plan, request, workspace, course_id)
        self.state.record_intake_stage_event("create_session_started", intake_id=item.intake_id)
        session_page = self._ensure_session_page(
            item,
            plan,
            request,
            workspace,
            course_id,
            reservation,
            existing_page,
            receipt,
        )
        self.state.record_intake_stage_event("create_session_readback", intake_id=item.intake_id)

        # The source content is read only after the target session create or
        # exact EXISTING validation.  Actual lecture date comes only from USER.
        raw = self._read_source_bytes(item)
        source_hash = _sha256_source(raw)
        if not _source_hash_matches(item.source_hash, raw):
            raise SourceUnavailableError("source changed after the immutable observation")
        self.state.record_intake_stage_event("normalize_started", intake_id=item.intake_id)
        source_ref = SourceRef(self.provider, item.provider_file_id, _drive_link(item.provider_file_id))
        transcript = normalize_transcript(
            raw,
            entity_id=reservation.entity_app_id,
            course_key=request.course_key or item.selected_course_key or "",
            source_ref=source_ref,
            source_hash=source_hash,
            source_version=item.source_version,
            processor_version=self.config.normalization.processor_version,
        )
        validate_normalized_transcript(transcript)
        self.state.record_intake_stage_event("normalize_complete", intake_id=item.intake_id)
        folders = self._prepare_entity_folders(item, plan, reservation, workspace, role="recording", receipt=receipt)
        derivative_content = transcript.to_markdown().encode("utf-8")
        derivative = self._stage_derivative(
            item=item,
            plan=plan,
            reservation=reservation,
            workspace=workspace,
            entity_id=reservation.entity_app_id,
            source_hash=source_hash,
            source_version=item.source_version,
            schema="uls.transcript.v1",
            processor_version=self.config.normalization.processor_version,
            artifact_role="normalized_transcript",
            parent_id=folders["derived"].file_id,
            name=f"{reservation.entity_app_id}.md",
            content=derivative_content,
            receipt=receipt,
        )
        self.state.record_intake_stage_event("pointer_write_started", intake_id=item.intake_id)
        status = "Ready" if transcript.status.value == "ready" else "Partial"
        self._guarded_notion_update(
            receipt,
            request,
            workspace,
            "sessions",
            _page_id(session_page),
            {"Normalized Transcript": _drive_link(derivative.file_id), "Recording Status": status},
            operation_key=self._operation_key(
                INTAKE_DERIVATIVE_OPERATION,
                self.provider,
                item.provider_file_id,
                source_hash,
                item.source_version,
                reservation.entity_app_id,
                "uls.transcript.v1",
                self.config.normalization.processor_version,
                "source_pointer",
            ),
        )
        self.state.record_intake_stage_event("pointer_write_readback", intake_id=item.intake_id)
        full = self._read_session_tuple(session_page_id=_page_id(session_page), workspace=workspace, expected={
            "ID": reservation.entity_app_id,
            "Course": course_id,
            "Date": request.actual_date,
            "Normalized Transcript": _drive_link(derivative.file_id),
            "Recording Status": status,
        })
        del full
        self.state.record_intake_stage_event("full_tuple_readback", intake_id=item.intake_id)
        self._apply_binding(item, plan, reservation, source_ref, source_hash, "transcript", receipt, request, workspace)
        self.state.record_intake_stage_event("APPLIED", intake_id=item.intake_id)
        self.state.update_intake_item(item.intake_id, status=IntakeStatus.REGISTERED.value, last_successful_stage="APPLIED", content_status=status)
        self._project_file_intake(
            self._require_item(item.intake_id),
            workspace,
            self._semester_workspace_fingerprint(workspace.semester),
            receipt=receipt,
        )
        self.state.record_intake_stage_event("REGISTERED", intake_id=item.intake_id)
        moved = self._move_after_freshness(item, plan, reservation, folders["source"], receipt, request, workspace)
        self.state.record_intake_stage_event("move_readback", intake_id=item.intake_id)
        self.state.update_intake_item(item.intake_id, status=IntakeStatus.ORGANIZED.value, last_successful_stage="ORGANIZED", canonical_source_json={"provider": self.provider, "file_id": item.provider_file_id, "parent_id": moved.parent_id})
        self._project_file_intake(
            self._require_item(item.intake_id),
            workspace,
            self._semester_workspace_fingerprint(workspace.semester),
            receipt=receipt,
        )
        self._publish_processing_provenance(
            item=item,
            operation=INTAKE_SESSION_OPERATION,
            source_ref=source_ref,
            derivative_ref=SourceRef(
                self.provider,
                derivative.file_id,
                derivative.web_view_link or _drive_link(derivative.file_id),
            ),
            source_hash=source_hash,
            status=_job_status(IntakeStatus.ORGANIZED.value, status),
        )
        return {
            "status": IntakeStatus.ORGANIZED.value,
            "content_status": status,
            "entity_id": reservation.entity_app_id,
            "file_id": item.provider_file_id,
        }

    def _process_material(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
    ) -> dict[str, Any]:
        self._require_mutation_capability()
        course_id = self._course_id(request.course_key or item.selected_course_key or "", workspace)
        reservation = self._reserve_material(item, plan, request, workspace, course_id)
        folders = self._prepare_entity_folders(item, plan, reservation, workspace, role="material", receipt=receipt)
        self.state.record_intake_stage_event("create_material_started", intake_id=item.intake_id)
        material_page = self._ensure_material_page(item, plan, request, workspace, course_id, reservation, folders, receipt)
        self.state.record_intake_stage_event("create_material_readback", intake_id=item.intake_id)
        raw = self._read_source_bytes(item)
        if not _source_hash_matches(item.source_hash, raw):
            raise SourceUnavailableError("source changed after the immutable observation")
        source_hash = _sha256_source(raw)
        normalized = extract_pdf(
            raw,
            entity_id=reservation.entity_app_id,
            course_key=request.course_key or item.selected_course_key or "",
            source_ref=SourceRef(self.provider, item.provider_file_id, _drive_link(item.provider_file_id)),
            source_hash=source_hash,
            source_version=item.source_version,
            processor_version=self.config.normalization.processor_version,
        )
        if normalized.status in {PDFContentStatus.UNAVAILABLE, PDFContentStatus.NEEDS_REVIEW, PDFContentStatus.FAILED} and not normalized.text:
            self.state.update_intake_item(item.intake_id, content_status=normalized.content_status, last_error_code="PDF_NOT_EXTRACTABLE", last_error=normalized.reason, last_successful_stage="MATERIAL_CREATED")
            self._project_file_intake(
                self._require_item(item.intake_id),
                workspace,
                self._semester_workspace_fingerprint(workspace.semester),
                receipt=receipt,
            )
            raise SourcePartialError(normalized.reason or "PDF is not deterministically extractable")
        derivative = self._stage_derivative(
            item=item,
            plan=plan,
            reservation=reservation,
            workspace=workspace,
            entity_id=reservation.entity_app_id,
            source_hash=source_hash,
            source_version=item.source_version,
            schema="uls.material.v1",
            processor_version=self.config.normalization.processor_version,
            artifact_role="normalized_material",
            parent_id=folders["derived"].file_id,
            name=f"{reservation.entity_app_id}.md",
            content=normalized.to_markdown().encode("utf-8"),
            receipt=receipt,
        )
        text_status = normalized.content_status
        self._guarded_notion_update(
            receipt,
            request,
            workspace,
            "materials",
            _page_id(material_page),
            {
                "Normalized Source": _drive_link(derivative.file_id),
                "Text Status": text_status,
                "Text Source": normalized.text_source,
                "Page Count": normalized.page_count,
            },
            operation_key=self._operation_key(
                INTAKE_DERIVATIVE_OPERATION,
                self.provider,
                item.provider_file_id,
                source_hash,
                item.source_version,
                reservation.entity_app_id,
                "uls.material.v1",
                self.config.normalization.processor_version,
                "material_pointer",
            ),
        )
        self.state.record_intake_stage_event("full_tuple_readback", intake_id=item.intake_id)
        self._read_material_tuple(
            page_id=_page_id(material_page),
            workspace=workspace,
            expected={
                "ID": reservation.entity_app_id,
                "Course": course_id,
                "Type": request.material_role,
                "Original Filename": item.original_name,
                "Normalized Source": _drive_link(derivative.file_id),
                "Text Status": text_status,
                "Text Source": normalized.text_source,
                "Current Source Version": item.source_version,
            },
        )
        source_ref = SourceRef(self.provider, item.provider_file_id, _drive_link(item.provider_file_id))
        self._apply_binding(item, plan, reservation, source_ref, source_hash, "material", receipt, request, workspace)
        self.state.record_intake_stage_event("APPLIED", intake_id=item.intake_id)
        self.state.update_intake_item(item.intake_id, status=IntakeStatus.REGISTERED.value, last_successful_stage="APPLIED", content_status=text_status)
        self._project_file_intake(
            self._require_item(item.intake_id),
            workspace,
            self._semester_workspace_fingerprint(workspace.semester),
            receipt=receipt,
        )
        self.state.record_intake_stage_event("REGISTERED", intake_id=item.intake_id)
        moved = self._move_after_freshness(item, plan, reservation, folders["source"], receipt, request, workspace)
        self.state.record_intake_stage_event("move_readback", intake_id=item.intake_id)
        self.state.update_intake_item(item.intake_id, status=IntakeStatus.ORGANIZED.value, last_successful_stage="ORGANIZED", canonical_source_json={"provider": self.provider, "file_id": item.provider_file_id, "parent_id": moved.parent_id})
        self._project_file_intake(
            self._require_item(item.intake_id),
            workspace,
            self._semester_workspace_fingerprint(workspace.semester),
            receipt=receipt,
        )
        self._publish_processing_provenance(
            item=item,
            operation=INTAKE_MATERIAL_OPERATION,
            source_ref=source_ref,
            derivative_ref=SourceRef(
                self.provider,
                derivative.file_id,
                derivative.web_view_link or _drive_link(derivative.file_id),
            ),
            source_hash=source_hash,
            status=_job_status(IntakeStatus.ORGANIZED.value, text_status),
        )
        return {"status": IntakeStatus.ORGANIZED.value, "entity_id": reservation.entity_app_id, "file_id": item.provider_file_id, "content_status": text_status}

    # ------------------------------------------------------------------
    # Provider and state gates
    # ------------------------------------------------------------------
    def _publish_processing_provenance(
        self,
        *,
        item: IntakeItem,
        operation: str,
        source_ref: SourceRef,
        derivative_ref: SourceRef,
        source_hash: str,
        status: str,
    ) -> None:
        """Publish the existing source-to-derivative retrieval contract.

        Intake's plan job is created before bytes are read, so it is first
        bound to the verified SHA-256 generation here.  The processing record
        is written only after the full tuple, APPLIED/REGISTERED, freshness,
        and file-ID-preserving move gates have succeeded.  ReadOnlyState can
        therefore resolve the same SOURCE binding used by the legacy worker.
        """

        bind_job = getattr(self.state, "bind_job_source_identity", None)
        get_record = getattr(self.state, "get_processing_record", None)
        create_record = getattr(self.state, "create_processing_record", None)
        if not callable(bind_job) or not callable(get_record) or not callable(create_record):
            raise SourceUnavailableError("StateStore lacks intake provenance methods")
        jobs = self.state.list_jobs(entity_id=item.intake_id, limit=1000)
        candidates = [
            job
            for job in jobs
            if job.operation == operation
            and job.target_entity_id == item.intake_id
            and str(job.status) in {"PROCESSING", "READY", "PARTIAL"}
            and job.source_file_id in (None, item.provider_file_id)
            and job.source_hash in (None, source_hash)
        ]
        active = [job for job in candidates if str(job.status) == "PROCESSING"]
        if active:
            candidates = active
        if len(candidates) != 1:
            raise IntakeReconcileRequired("successful intake has no unique durable plan job")
        job = bind_job(
            candidates[0].id,
            source_file_id=item.provider_file_id,
            source_hash=source_hash,
            course_key=item.selected_course_key,
        )
        existing = get_record(job.id, operation=operation)
        if existing is None:
            existing = create_record(
                job_id=job.id,
                operation=operation,
                processor_version=self.config.normalization.processor_version,
                input_hash=source_hash,
                output_ref_json={
                    "provider": derivative_ref.provider,
                    "file_id": derivative_ref.file_id,
                    "web_url": derivative_ref.web_url,
                    "source_ref": {
                        "provider": source_ref.provider,
                        "file_id": source_ref.file_id,
                        "web_url": source_ref.web_url,
                    },
                    "source_hash": source_hash,
                    "source_version": item.source_version,
                    "status": status,
                },
                finished_at=_utc_now(),
                status=status,
            )
        if existing is None:
            raise SourceUnavailableError("processing provenance readback is missing")
        self.state.record_intake_stage_event(
            "provenance_readback",
            intake_id=item.intake_id,
            detail={"operation": operation, "derivative_file_id": derivative_ref.file_id},
        )

    def _reserve_session(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
        course_id: str,
    ) -> tuple[EntityReservation, dict[str, Any] | None]:
        rows = self._inventory("sessions", workspace, course_id)
        target_page: dict[str, Any] | None = None
        if request.session_mode == SessionMode.EXISTING.value:
            if not request.session_id:
                raise RequestValidationError(("EXISTING transcript requires one Session",))
            for row in rows:
                if _page_id(row) == request.session_id or row.get("ID") == request.session_id:
                    target_page = row
                    break
            if target_page is None:
                raise IntakeReconcileRequired("selected Session is missing from current inventory")
            entity_id = str(target_page.get("ID", ""))
            self._validate_entity_for_course(entity_id, "S", request.course_key or item.selected_course_key or "", target_page, course_id)
        else:
            entity_id = self._next_entity_id(rows, request.course_key or item.selected_course_key or "", "S")
        parent = workspace.recordings_folder_id
        marker = folder_marker_key(
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            parent_folder_id=parent,
            reservation_id=sha256_hex(["intake.reservation.v1", item.intake_id, plan.plan_revision, "S"]),
            source_file_id=item.provider_file_id,
            entity_app_id=entity_id,
            folder_role="entity",
        )
        reservation = self.state.reserve_entity(
            reservation_id=sha256_hex(["intake.reservation.v1", item.intake_id, plan.plan_revision, "S"]),
            intake_id=item.intake_id,
            entity_kind="SESSION",
            entity_app_id=entity_id,
            parent_folder_id=parent,
            marker_key=marker,
            state="PENDING",
            plan_revision=plan.plan_revision,
            source_file_id=item.provider_file_id,
        )
        return reservation, target_page

    def _reserve_material(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
        course_id: str,
    ) -> EntityReservation:
        rows = self._inventory("materials", workspace, course_id)
        entity_id = self._next_entity_id(rows, request.course_key or item.selected_course_key or "", "M")
        parent = workspace.materials_folder_id
        reservation_id = sha256_hex(["intake.reservation.v1", item.intake_id, plan.plan_revision, "M"])
        marker = folder_marker_key(
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            parent_folder_id=parent,
            reservation_id=reservation_id,
            source_file_id=item.provider_file_id,
            entity_app_id=entity_id,
            folder_role="entity",
        )
        return self.state.reserve_entity(
            reservation_id=reservation_id,
            intake_id=item.intake_id,
            entity_kind="MATERIAL",
            entity_app_id=entity_id,
            parent_folder_id=parent,
            marker_key=marker,
            state="PENDING",
            plan_revision=plan.plan_revision,
            source_file_id=item.provider_file_id,
        )

    def _inventory(self, logical: str, workspace: ResolvedSemesterWorkspace, course_id: str) -> list[dict[str, Any]]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        rows = self.notion.list_records(logical)
        seen: set[str] = set()
        current: list[dict[str, Any]] = []
        course_key = workspace.course_key
        for row in rows:
            relation = _relation_ids(row.get("Course"))
            if course_id not in relation:
                continue
            entity_id = row.get("ID")
            if not isinstance(entity_id, str):
                raise IntakeReconcileRequired(f"{logical} inventory contains a row without a strict ID")
            expected = "S" if logical == "sessions" else "M"
            self._validate_entity_for_course(entity_id, expected, course_key, row, course_id)
            if entity_id in seen:
                raise IntakeReconcileRequired(f"duplicate {logical} ID in current inventory")
            seen.add(entity_id)
            current.append(row)
        return current

    def _validate_entity_for_course(self, entity_id: str, entity_type: str, course_key: str, row: Mapping[str, Any], course_id: str) -> None:
        parsed = parse_entity_id(entity_id)
        course = parse_course_key(course_key)
        if parsed.entity_type != entity_type or parsed.course_code != course.code or parsed.sequence <= 0:
            raise IntakeReconcileRequired("provider entity ID does not match current Course Key")
        if _relation_ids(row.get("Course")) != [course_id]:
            raise IntakeReconcileRequired("provider entity has zero or multiple Course relations")

    @staticmethod
    def _next_entity_id(rows: Sequence[Mapping[str, Any]], course_key: str, entity_type: str) -> str:
        course = parse_course_key(course_key)
        values: list[int] = []
        for row in rows:
            parsed = parse_entity_id(str(row["ID"]))
            if parsed.entity_type == entity_type:
                values.append(parsed.sequence)
        next_value = max(values, default=0) + 1
        if next_value > 99:
            raise IntakeReconcileRequired("two-digit entity ID space is exhausted")
        return f"{course.code}-{entity_type}{next_value:02d}"

    def _ensure_session_page(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
        course_id: str,
        reservation: EntityReservation,
        existing_page: dict[str, Any] | None,
        receipt: RequestReceipt,
    ) -> dict[str, Any]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        if request.session_mode == SessionMode.EXISTING.value:
            if existing_page is None:
                raise IntakeReconcileRequired("existing Session readback is missing")
            self._read_session_tuple(_page_id(existing_page), workspace, expected={"ID": reservation.entity_app_id, "Course": course_id, "Date": request.actual_date})
            return existing_page
        properties: dict[str, Any] = {
            "Name": f"{request.actual_date} · 수업 {reservation.entity_app_id}",
            "ID": reservation.entity_app_id,
            "Course": [course_id],
            "Date": request.actual_date,
            "Status": "Not started",
            "Recording Status": "Pending",
        }
        if request.session_no is not None:
            properties["Session No"] = request.session_no
        op_key = self._plan_operation_key(INTAKE_SESSION_OPERATION, item, plan, workspace, request, target_id=None)
        prior = self.state.get_provider_write_attempt(op_key)
        rows = self.notion.list_records("sessions")
        matches = [row for row in rows if row.get("ID") == reservation.entity_app_id]
        if len(matches) > 1:
            raise IntakeReconcileRequired("multiple Session pages have the reserved ID")
        if matches:
            page = matches[0]
            self._validate_new_session_snapshot(page, properties, course_id)
            self.state.update_provider_write_attempt(op_key, target_id=_page_id(page), response_state="READBACK_OK", readback_json=page) if prior else self.state.record_provider_write_attempt(operation=INTAKE_SESSION_OPERATION, operation_key=op_key, provider=self.provider, target_id=_page_id(page), response_state="READBACK_OK", readback_json=page)
            return page
        if prior is not None:
            raise IntakeReconcileRequired("Session create outcome is indeterminate")
        self._assert_receipt_unchanged(receipt, request, workspace)
        self.state.record_provider_write_attempt(operation=INTAKE_SESSION_OPERATION, operation_key=op_key, provider=self.provider, target_id=None, response_state="PREPARED")
        try:
            page = self.notion.create_record("sessions", properties)
        except Exception:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
            raise
        page_id = _page_id(page)
        self.state.update_provider_write_attempt(op_key, target_id=page_id, dispatched_at=_utc_now(), response_state="DISPATCHED")
        readback = self.notion.read_record("sessions", page_id)
        if readback is None:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
            raise SourceUnavailableError("Session create readback is missing")
        self._validate_new_session_snapshot(readback, properties, course_id)
        self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=readback)
        self._assert_receipt_unchanged(receipt, request, workspace)
        return readback

    def _ensure_material_page(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
        course_id: str,
        reservation: EntityReservation,
        folders: Mapping[str, DriveMetadata],
        receipt: RequestReceipt,
    ) -> dict[str, Any]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        source_folder_url = _drive_folder_link(folders["source"].file_id)
        properties = {
            "Name": item.original_name,
            "ID": reservation.entity_app_id,
            "Course": [course_id],
            "Type": request.material_role,
            "Source Folder": source_folder_url,
            "Original Filename": item.original_name,
            "Text Status": "Pending",
            "Text Source": "Unavailable",
            "Visual Dependency": "Unknown",
            "AI Priority": "Normal",
            "Current Source Version": item.source_version,
        }
        op_key = self._plan_operation_key(INTAKE_MATERIAL_OPERATION, item, plan, workspace, request, target_id=None)
        prior = self.state.get_provider_write_attempt(op_key)
        rows = self.notion.list_records("materials")
        matches = [row for row in rows if row.get("ID") == reservation.entity_app_id]
        if len(matches) > 1:
            raise IntakeReconcileRequired("multiple Material pages have reserved ID")
        if matches:
            page = matches[0]
            self._validate_material_snapshot(page, properties, course_id)
            if prior is None:
                self.state.record_provider_write_attempt(operation=INTAKE_MATERIAL_OPERATION, operation_key=op_key, provider=self.provider, target_id=_page_id(page), response_state="READBACK_OK", readback_json=page)
            else:
                self.state.update_provider_write_attempt(op_key, target_id=_page_id(page), response_state="READBACK_OK", readback_json=page)
            return page
        if prior is not None:
            raise IntakeReconcileRequired("Material create outcome is indeterminate")
        self._assert_receipt_unchanged(receipt, request, workspace)
        self.state.record_provider_write_attempt(operation=INTAKE_MATERIAL_OPERATION, operation_key=op_key, provider=self.provider, target_id=None, response_state="PREPARED")
        try:
            page = self.notion.create_record("materials", properties)
        except Exception:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
            raise
        page_id = _page_id(page)
        self.state.update_provider_write_attempt(op_key, target_id=page_id, dispatched_at=_utc_now(), response_state="DISPATCHED")
        readback = self.notion.read_record("materials", page_id)
        if readback is None:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
            raise SourceUnavailableError("Material create readback is missing")
        self._validate_material_snapshot(readback, properties, course_id)
        self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=readback)
        self._assert_receipt_unchanged(receipt, request, workspace)
        return readback

    def _prepare_entity_folders(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        workspace: ResolvedSemesterWorkspace,
        *,
        role: str,
        receipt: RequestReceipt,
    ) -> dict[str, DriveMetadata]:
        parent = reservation.parent_folder_id
        entity_marker = {"uls_v": "1", "uls_t": reservation.marker_key, "uls_r": "entity"}
        entity = self._ensure_folder_with_attempt(
            item, plan, reservation, parent, reservation.entity_app_id, entity_marker, "entity", receipt, role
        )
        source = self._ensure_child_folder(item, plan, reservation, entity, "source", "source", receipt, role)
        derived = self._ensure_child_folder(item, plan, reservation, entity, "derived", "derived", receipt, role)
        return {"entity": entity, "source": source, "derived": derived}

    def _ensure_child_folder(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        parent: DriveMetadata,
        name: str,
        folder_role: str,
        receipt: RequestReceipt,
        role: str,
    ) -> DriveMetadata:
        marker_hash = folder_marker_key(
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            parent_folder_id=parent.file_id,
            reservation_id=reservation.reservation_id,
            source_file_id=item.provider_file_id,
            entity_app_id=reservation.entity_app_id,
            folder_role=folder_role,
        )
        marker = {"uls_v": "1", "uls_t": marker_hash, "uls_r": folder_role}
        return self._ensure_folder_with_attempt(item, plan, reservation, parent.file_id, name, marker, folder_role, receipt, role)

    def _ensure_folder_with_attempt(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        parent_id: str,
        name: str,
        marker: dict[str, str],
        folder_role: str,
        receipt: RequestReceipt,
        role: str,
    ) -> DriveMetadata:
        op_key = self._operation_key(
            INTAKE_FOLDER_OPERATION,
            self.provider,
            self.provider_account_binding_id,
            parent_id,
            reservation.reservation_id,
            item.provider_file_id,
            reservation.entity_app_id,
            folder_role,
        )
        prior = self.state.get_provider_write_attempt(op_key)
        if prior is not None and prior.response_state == "READBACK_OK" and prior.target_id:
            metadata = self.drive.read_metadata(prior.target_id)
            self._check_folder(metadata, parent_id, marker)
            return metadata
        self._assert_receipt_unchanged(receipt, request=None, workspace=self._workspace_for_item(item))
        if prior is None:
            self.state.record_provider_write_attempt(operation=INTAKE_FOLDER_OPERATION, operation_key=op_key, provider=self.provider, target_id=None, response_state="PREPARED")
        try:
            metadata = ensure_marked_folder(
                self.drive,
                parent_id=parent_id,
                name=name,
                marker=marker,
                create_attempted=prior is not None,
            )
        except Exception:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
            raise
        self.state.update_provider_write_attempt(op_key, target_id=metadata.file_id, dispatched_at=_utc_now(), response_state="READBACK_OK", readback_json=asdict(metadata))
        self._check_folder(metadata, parent_id, marker)
        self.state.record_intake_stage_event("folder_readback", intake_id=item.intake_id, operation_key=op_key, detail={"role": folder_role})
        return metadata

    @staticmethod
    def _check_folder(metadata: DriveMetadata, parent_id: str, marker: Mapping[str, str]) -> None:
        if metadata.mime_type != DRIVE_FOLDER_MIME or metadata.parents != (parent_id,) or metadata.app_properties != dict(marker):
            raise IntakeReconcileRequired("Drive folder marker/parent readback mismatch")
        if metadata.owned_by_me is not True:
            raise PolicyDeniedError("Drive folder is not USER owned")
        _check_private_drive_metadata(metadata)

    def _stage_derivative(
        self,
        *,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        workspace: ResolvedSemesterWorkspace,
        entity_id: str,
        source_hash: str,
        source_version: int,
        schema: str,
        processor_version: str,
        artifact_role: str,
        parent_id: str,
        name: str,
        content: bytes,
        receipt: RequestReceipt,
    ) -> DriveMetadata:
        marker = derivative_marker(
            provider=self.provider,
            source_file_id=item.provider_file_id,
            source_hash=source_hash,
            source_version=source_version,
            entity_app_id=entity_id,
            normalized_schema=schema,
            processor_version=processor_version,
            artifact_role=artifact_role,
        )
        op_key = self._operation_key(
            INTAKE_DERIVATIVE_OPERATION,
            self.provider,
            item.provider_file_id,
            source_hash,
            source_version,
            entity_id,
            schema,
            processor_version,
            artifact_role,
        )
        prior = self.state.get_provider_write_attempt(op_key)
        matches = self.drive.search_marker(marker)
        if len(matches) > 1:
            raise IntakeReconcileRequired("multiple derivative marker matches require reconciliation")
        if matches:
            result = matches[0]
            if result.parents != (parent_id,) or result.app_properties != marker:
                raise IntakeReconcileRequired("derivative marker tuple does not match target")
            if self.drive.download(result.file_id) != content:
                raise IntakeReconcileRequired("existing derivative content does not match immutable tuple")
            if prior is None:
                self.state.record_provider_write_attempt(operation=INTAKE_DERIVATIVE_OPERATION, operation_key=op_key, provider=self.provider, target_id=result.file_id, response_state="READBACK_OK", readback_json=asdict(result))
            else:
                self.state.update_provider_write_attempt(op_key, target_id=result.file_id, response_state="READBACK_OK", readback_json=asdict(result))
            self.state.record_intake_stage_event("stage_readback", intake_id=item.intake_id, operation_key=op_key)
            return result
        if prior is not None:
            raise IntakeReconcileRequired("derivative create outcome is indeterminate")
        self._assert_receipt_unchanged(receipt, request=None, workspace=workspace)
        self.state.record_provider_write_attempt(operation=INTAKE_DERIVATIVE_OPERATION, operation_key=op_key, provider=self.provider, target_id=None, response_state="PREPARED")
        self.state.record_intake_stage_event("stage_write_attempt_started", intake_id=item.intake_id, operation_key=op_key)
        try:
            result = self.drive.create_file_with_marker(parent_id, name, "text/markdown", content, marker)
        except Exception:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
            raise
        self.state.update_provider_write_attempt(op_key, target_id=result.file_id, dispatched_at=_utc_now(), response_state="DISPATCHED")
        readback = self.drive.read_metadata(result.file_id)
        _check_private_drive_metadata(readback)
        if readback.file_id != result.file_id or readback.parents != (parent_id,) or readback.app_properties != marker or self.drive.download(readback.file_id) != content:
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
            raise SourceUnavailableError("staged derivative full readback failed")
        self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=asdict(readback))
        self.state.record_intake_stage_event("stage_validate_readback", intake_id=item.intake_id, operation_key=op_key)
        return readback

    def _move_after_freshness(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        target_folder: DriveMetadata,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
    ) -> DriveMetadata:
        # The child source folder was read during entity preparation, but the
        # move gate must use a fresh destination readback as well.  This
        # catches a moved/trashed/shared destination between registration and
        # the irreversible provider mutation.
        fresh_target = self.drive.read_metadata(target_folder.file_id)
        expected_parent = target_folder.parent_id
        if expected_parent is None:
            raise IntakeReconcileRequired("move destination has no unique parent")
        self._check_folder(fresh_target, expected_parent, target_folder.app_properties)
        target_folder = fresh_target
        current = self.drive.read_metadata(item.provider_file_id)
        if current.file_id != item.provider_file_id or current.owned_by_me is not True:
            raise PolicyDeniedError("source ownership or identity readback failed before move")
        _check_private_drive_metadata(current, require_move=True)
        op_key = self._operation_key(
            INTAKE_MOVE_OPERATION,
            self.provider,
            item.provider_file_id,
            item.original_parent_id,
            target_folder.file_id,
            item.source_hash,
            plan.plan_revision,
        )
        prior = self.state.get_provider_write_attempt(op_key)
        if current.parents == (target_folder.file_id,):
            moved = current
            self.state.record_provider_write_attempt(
                operation=INTAKE_MOVE_OPERATION,
                operation_key=op_key,
                provider=self.provider,
                target_id=moved.file_id,
                response_state="READBACK_OK",
                readback_json=asdict(moved),
            ) if prior is None else self.state.update_provider_write_attempt(
                op_key,
                target_id=moved.file_id,
                response_state="READBACK_OK",
                readback_json=asdict(moved),
            )
        elif current.parents == (item.original_parent_id,):
            if not _source_hash_matches(item.source_hash, self._read_source_bytes(item)):
                raise SourceUnavailableError("source changed during freshness check")
            if prior is not None and prior.response_state == "READBACK_OK" and prior.target_id:
                moved = self.drive.read_metadata(prior.target_id)
            else:
                self._assert_receipt_unchanged(receipt, request, workspace)
                if prior is None:
                    self.state.record_provider_write_attempt(operation=INTAKE_MOVE_OPERATION, operation_key=op_key, provider=self.provider, target_id=item.provider_file_id, response_state="PREPARED")
                try:
                    moved = self.drive.move_file(item.provider_file_id, item.original_parent_id, target_folder.file_id)
                except Exception:
                    self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
                    raise
                self.state.update_provider_write_attempt(op_key, target_id=moved.file_id, dispatched_at=_utc_now(), response_state="READBACK_OK", readback_json=asdict(moved))
                self._assert_receipt_unchanged(receipt, request, workspace)
        else:
            raise IntakeReconcileRequired("source moved outside registered parents before move")
        if moved.file_id != item.provider_file_id or moved.parents != (target_folder.file_id,):
            raise SourceUnavailableError("file-ID-preserving move readback failed")
        _check_private_drive_metadata(moved, require_move=True)
        return moved

    def _apply_binding(
        self,
        item: IntakeItem,
        plan: IntakePlan,
        reservation: EntityReservation,
        source_ref: SourceRef,
        source_hash: str,
        source_kind: str,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
    ) -> None:
        self._assert_receipt_unchanged(receipt, request, workspace)
        self.state.register_source_file(
            source_file_id=item.provider_file_id,
            provider=self.provider,
            provider_file_id=item.provider_file_id,
            course_key=request.course_key or item.selected_course_key or "",
            source_kind=source_kind,
            original_filename=item.original_name,
            current_hash=source_hash,
            canonical_entity_id=reservation.entity_app_id,
        )
        self.state.register_source_version(
            source_file_id=item.provider_file_id,
            source_hash=source_hash,
            canonical_entity_id=reservation.entity_app_id,
            source_ref_json={"provider": source_ref.provider, "file_id": source_ref.file_id, "web_url": source_ref.web_url},
            processor_version=self.config.normalization.processor_version,
            version=item.source_version,
        )
        if source_kind == "transcript":
            self.state.record_session_source_binding(
                course_key=request.course_key or item.selected_course_key or "",
                session_id=reservation.entity_app_id,
                provider=self.provider,
                provider_file_id=item.provider_file_id,
                reservation_id=reservation.reservation_id,
                state="APPLIED",
            )
        self.state.update_entity_reservation(reservation.reservation_id, state="APPLIED")
        self._assert_receipt_unchanged(receipt, request, workspace)

    # ------------------------------------------------------------------
    # Notion File Intake/status and request recovery
    # ------------------------------------------------------------------
    def _project_file_intake_safe(self, item: IntakeItem, workspace: ResolvedSemesterWorkspace, workspace_fingerprint: str) -> None:
        if self.notion is None:
            return
        try:
            self._project_file_intake(item, workspace, workspace_fingerprint)
        except (IntakeReconcileRequired, ProviderUnavailableError, SourceUnavailableError, PolicyDeniedError):
            return

    def _project_file_intake(
        self,
        item: IntakeItem,
        workspace: ResolvedSemesterWorkspace,
        workspace_fingerprint: str,
        *,
        input_request_link: str | None = None,
        receipt: RequestReceipt | None = None,
    ) -> dict[str, Any] | None:
        if self.notion is None:
            return None
        status_revision = derive_status_revision(
            intake_id=item.intake_id,
            source_hash=item.source_hash,
            source_version=item.source_version,
            status=item.status,
            request_revision_hash=receipt.request_revision_hash if receipt else item.request_revision_hash,
        )
        plan_revision = receipt.plan_revision if receipt else None
        # The preclaim operation revision is immutable-observation based, but
        # one File Intake page can legitimately receive later system-only
        # projections (for example Course after an ASSIGN_COURSE claim).
        # Include the exact projection snapshot so a create attempt cannot be
        # mistaken for a later update attempt during recovery.
        properties = self._file_intake_properties(item, workspace, workspace_fingerprint, input_request_link)
        projection_revision = sha256_hex(["intake.file-intake-projection.v1", properties])
        op_key = self._operation_key(
            INTAKE_STATUS_OPERATION,
            self.provider,
            workspace.file_intake_data_source_id,
            item.intake_id,
            status_revision,
            plan_revision,
            projection_revision,
        )
        page = self._file_intake_page(item, workspace)
        if page is None:
            prior = self.state.get_provider_write_attempt(op_key)
            if prior is not None:
                raise IntakeReconcileRequired("File Intake create outcome is indeterminate")
            self.state.record_provider_write_attempt(operation=INTAKE_STATUS_OPERATION, operation_key=op_key, provider=self.provider, target_id=None, response_state="PREPARED")
            try:
                page = self.notion.create_record("file_intake", properties)
            except Exception:
                self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
                raise
            page_id = _page_id(page)
            self.state.update_provider_write_attempt(op_key, target_id=page_id, dispatched_at=_utc_now(), response_state="DISPATCHED")
            page = self.notion.read_record("file_intake", page_id)
            if page is None:
                self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
                raise SourceUnavailableError("File Intake create readback is missing")
            self._validate_file_intake_readback(page, item, workspace, workspace_fingerprint)
            self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=page)
            self.state.update_intake_item(item.intake_id, file_intake_page_id=page_id)
        else:
            page_id = _page_id(page)
            if page_id is None:
                raise SourceUnavailableError("File Intake page has no provider ID")
            self._validate_file_intake_readback(page, item, workspace, workspace_fingerprint)
            # Name is SYSTEM_INITIAL_USER_PRESERVE: it is accepted for the
            # first projection, then excluded from every update patch.
            update_properties = {key: value for key, value in properties.items() if key != "Name"}
            needs_update = any(
                not _property_matches(page.get(key), value)
                for key, value in update_properties.items()
            )
            if needs_update:
                if receipt:
                    request = self._request_from_receipt(receipt, workspace)
                    page = self._guarded_notion_update(
                        receipt,
                        request,
                        workspace,
                        "file_intake",
                        page_id,
                        update_properties,
                        operation_key=op_key,
                        attempt_operation=INTAKE_STATUS_OPERATION,
                    )
                else:
                    prior = self.state.get_provider_write_attempt(op_key)
                    if prior is not None and prior.response_state == "UNKNOWN":
                        raise IntakeReconcileRequired("File Intake update outcome is indeterminate")
                    if prior is None:
                        self.state.record_provider_write_attempt(
                            operation=INTAKE_STATUS_OPERATION,
                            operation_key=op_key,
                            provider=self.provider,
                            target_id=page_id,
                            response_state="PREPARED",
                        )
                    try:
                        self.notion.update_system_record("file_intake", page_id, update_properties)
                    except Exception:
                        self.state.update_provider_write_attempt(
                            op_key,
                            response_state="UNKNOWN",
                            error_class="PROVIDER_UNAVAILABLE",
                        )
                        raise
                    page = self.notion.read_record("file_intake", page_id)
                    if page is None:
                        self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
                        raise SourceUnavailableError("File Intake update readback is missing")
                    if not _properties_match(page, update_properties):
                        self.state.update_provider_write_attempt(
                            op_key,
                            response_state="UNKNOWN",
                            readback_json=page,
                        )
                        raise IntakeReconcileRequired("File Intake update readback does not match")
                    self.state.update_provider_write_attempt(
                        op_key,
                        target_id=page_id,
                        response_state="READBACK_OK",
                        readback_json=page,
                    )
                self._validate_file_intake_readback(page, item, workspace, workspace_fingerprint)
            else:
                attempt = self.state.get_provider_write_attempt(op_key)
                if attempt is None:
                    self.state.record_provider_write_attempt(
                        operation=INTAKE_STATUS_OPERATION,
                        operation_key=op_key,
                        provider=self.provider,
                        target_id=page_id,
                        response_state="READBACK_OK",
                        readback_json=page,
                    )
                elif attempt.response_state != "READBACK_OK":
                    self.state.update_provider_write_attempt(
                        op_key,
                        target_id=page_id,
                        response_state="READBACK_OK",
                        readback_json=page,
                    )
        return page

    def _file_intake_page(self, item: IntakeItem, workspace: ResolvedSemesterWorkspace) -> dict[str, Any] | None:
        if self.notion is None:
            return None
        if item.file_intake_page_id:
            page = self.notion.read_record("file_intake", item.file_intake_page_id)
            if page is not None:
                return page
        rows = self.notion.list_records("file_intake")
        matches = [row for row in rows if row.get("Intake ID") == item.intake_id]
        if len(matches) > 1:
            raise IntakeReconcileRequired("multiple File Intake pages share an Intake ID")
        return matches[0] if matches else None

    def _ensure_file_intake_page(self, item: IntakeItem, workspace: ResolvedSemesterWorkspace, workspace_fingerprint: str) -> str:
        page = self._project_file_intake(item, workspace, workspace_fingerprint)
        if page is None or not _page_id(page):
            raise SourceUnavailableError("File Intake page could not be projected")
        return _page_id(page)  # type: ignore[return-value]

    def _file_intake_properties(self, item: IntakeItem, workspace: ResolvedSemesterWorkspace, workspace_fingerprint: str, input_request_link: str | None) -> dict[str, Any]:
        candidates = item.course_candidates_json
        if not isinstance(candidates, str):
            candidates = json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        props: dict[str, Any] = {
            "Name": item.original_name,
            "Intake ID": item.intake_id,
            "Provider": item.provider,
            "Provider File ID": item.provider_file_id,
            "Original Link": _drive_link(item.provider_file_id),
            "Original Filename": item.original_name,
            "Original Parent ID": item.original_parent_id,
            "Observed Kind": item.observed_kind if item.observed_kind in {"TRANSCRIPT", "MATERIAL_PDF", "UNKNOWN", "UNSUPPORTED"} else "UNKNOWN",
            "Course Candidates": candidates,
            "Status": item.status,
            "Content Status": item.content_status,
            "Source Hash": item.source_hash,
            "Source Version": item.source_version,
            "Workspace Fingerprint": workspace_fingerprint,
        }
        if input_request_link:
            props["Input Request Link"] = input_request_link
        if item.selected_course_key:
            course_id = self._course_id(item.selected_course_key, workspace, allow_missing=True)
            if course_id:
                props["Course"] = [course_id]
        if item.last_error:
            props["Error"] = item.last_error
        if item.last_successful_stage:
            props["Last Successful Stage"] = item.last_successful_stage
        return props

    def _validate_file_intake_readback(
        self,
        page: Mapping[str, Any],
        item: IntakeItem,
        workspace: ResolvedSemesterWorkspace,
        workspace_fingerprint: str | None = None,
    ) -> None:
        expected_fingerprint = workspace_fingerprint or self._semester_workspace_fingerprint(workspace.semester)
        if (
            page.get("Intake ID") != item.intake_id
            or page.get("Provider File ID") != item.provider_file_id
            or page.get("Source Hash") != item.source_hash
            or page.get("Workspace Fingerprint") != expected_fingerprint
        ):
            raise IntakeReconcileRequired("File Intake readback identity mismatch")

    def _find_request_by_key(self, request_key: str) -> list[dict[str, Any]]:
        if self.notion is None:
            return []
        return [row for row in self.notion.list_records("input_request") if row.get("Request Key") == request_key]

    def _recover_or_block_pending_request(
        self, item: IntakeItem, workspace: ResolvedSemesterWorkspace
    ) -> RequestReceipt | None:
        """Resolve an unbound pending generation before any new one is computed.

        A response-loss on the first create attempt must never be silently
        superseded by a freshly recomputed generation, even if the item's
        source hash/version has since drifted: that would let a duplicate
        Input Request page reach a live provider workspace.  The pending
        key is durable and was committed before the first create, so it is
        always looked up and either recovered or explicitly blocked first.
        """

        pending_key = item.pending_request_key
        if pending_key is None:
            return
        pending_receipt = self.state.get_request_receipt(pending_key)
        if pending_receipt is None:
            return
        existing = self._find_request_by_key(pending_key)
        if len(existing) > 1:
            raise IntakeReconcileRequired("multiple Input Request pages share one pending Request Key")
        if existing:
            page = existing[0]
            page_id = _page_id(page)
            if page_id is None:
                raise SourceUnavailableError("Input Request page has no provider ID")
            intake_ids: tuple[str, ...] = ()
            if pending_receipt.intake_ids_json:
                intake_ids = tuple(json.loads(pending_receipt.intake_ids_json))
            recovered = RequestGeneration(
                request_type=pending_receipt.request_type or "",
                intake_ids=intake_ids,
                observation_refs=(),
                target_snapshot={},
                target_snapshot_hash=pending_receipt.target_snapshot_hash,
                request_revision_hash=pending_receipt.request_revision_hash,
                request_key=pending_receipt.request_key,
            )
            self._validate_request_page_tuple(page, recovered, workspace)
            self.state.bind_request_page(pending_key, page_id)
            resolved = self.state.get_request_receipt(pending_key)
            if resolved is None:
                raise SourceUnavailableError("request receipt disappeared after provider readback")
            return resolved
        # No page was found under the pending key.  The prior create call
        # may still be in flight, may have failed outright, or may have
        # succeeded with a response the worker never saw.  None of those
        # can be told apart from here, so this always blocks rather than
        # guessing; an operator resolves it by finding the real page (and
        # binding it) or by clearing the stale pending key once confirmed
        # dead.
        raise IntakeReconcileRequired(
            "a prior Input Request create outcome is indeterminate; "
            "resolve the pending Request Key before creating a new one"
        )
    def _validate_request_page_tuple(self, page: Mapping[str, Any], generation: RequestGeneration, workspace: ResolvedSemesterWorkspace) -> None:
        if page.get("Request Key") != generation.request_key or page.get("Request Revision Hash") != generation.request_revision_hash or page.get("Request Type") != generation.request_type:
            raise IntakeReconcileRequired("Input Request key/revision/type readback mismatch")
        if page.get("Workspace Fingerprint") != self._semester_workspace_fingerprint(workspace.semester):
            raise IntakeReconcileRequired("Input Request workspace readback mismatch")
        actual_intake_pages = _relation_ids(page.get("Intake Items"))
        expected_intake_pages = self._file_intake_page_ids(generation.intake_ids)
        if actual_intake_pages != expected_intake_pages:
            raise IntakeReconcileRequired("Input Request intake relation readback mismatch")

    def _file_intake_page_ids(self, intake_ids: Sequence[str]) -> list[str]:
        result: list[str] = []
        for intake_id in intake_ids:
            item = self.state.get_intake_item(intake_id)
            if item and item.file_intake_page_id:
                result.append(item.file_intake_page_id)
        return result

    # ------------------------------------------------------------------
    # Request/plan identity and current-workspace helpers
    # ------------------------------------------------------------------
    def _request_from_page(self, page: Mapping[str, Any], workspace: ResolvedSemesterWorkspace) -> RequestInput:
        values = dict(page)
        course_raw = values.get("Course")
        course_ids = _relation_ids(course_raw)
        if len(course_ids) == 1:
            course_map = self._course_page_map(workspace)
            # Provider relations contain page IDs while RequestInput uses the
            # canonical Course Key.  Keep the authoritative map key->page ID
            # for all write paths, and reverse it only at this read boundary.
            page_to_course = {page_id: key for key, page_id in course_map.items()}
            values["Course"] = page_to_course.get(course_ids[0], course_ids[0])
        intake_ids = _relation_ids(values.get("Intake Items"))
        page_map = {page_id: intake_id for intake_id, page_id in ((item.intake_id, item.file_intake_page_id) for item in self.state.list_intake_items(limit=10_000)) if page_id}
        values["Intake Items"] = [page_map.get(value, value) for value in intake_ids]
        return request_from_mapping(values)

    def _request_from_receipt(self, receipt: RequestReceipt, workspace: ResolvedSemesterWorkspace) -> RequestInput:
        if self.notion is None or not receipt.provider_page_id:
            raise SourceUnavailableError("request receipt page is unavailable")
        page = self.notion.read_record("input_request", receipt.provider_page_id)
        if page is None:
            raise SourceUnavailableError("request receipt page is unavailable")
        return self._request_from_page(page, workspace)

    def _request_target_snapshot(self, request: RequestInput, workspace: ResolvedSemesterWorkspace) -> dict[str, Any]:
        return {
            "request_type": request.request_type,
            "intake_ids": sorted(request.intake_ids),
            "course_key": request.course_key,
            "kind": request.kind,
            "actual_date": request.actual_date,
            "session_mode": request.session_mode,
            "session_id": request.session_id,
            "session_no": request.session_no,
            "material_role": request.material_role,
            "workspace_fingerprint": self._semester_workspace_fingerprint(workspace.semester),
        }

    def _receipt_for_plan(self, plan: IntakePlan) -> RequestReceipt | None:
        for receipt in self.state.list_request_receipts():
            if receipt.request_revision_hash == plan.request_revision_hash and receipt.plan_revision == plan.plan_revision:
                return receipt
        return None

    def _item_for_receipt(self, receipt: RequestReceipt) -> IntakeItem:
        for item in self.state.list_intake_items(limit=10_000):
            if item.input_request_page_id == receipt.provider_page_id:
                return item
        raise SourceUnavailableError("request receipt is not bound to an intake item")

    def _assert_receipt_unchanged(self, receipt: RequestReceipt, request: RequestInput | None, workspace: ResolvedSemesterWorkspace) -> None:
        if request is None:
            request = self._request_from_receipt(receipt, workspace)
        if self.notion is None or not receipt.provider_page_id:
            raise SourceUnavailableError("request receipt page is unavailable")
        current_page = self.notion.read_record("input_request", receipt.provider_page_id)
        if current_page is None:
            raise SourceUnavailableError("request receipt page is unavailable")
        current = self._request_from_page(current_page, workspace)
        durable_receipt = self.state.get_request_receipt(receipt.request_key) or receipt
        if type(current.submitted) is not bool or current.submitted is not True:
            raise IntakeReconcileRequired("request is no longer submitted")
        if type(current.cancelled) is not bool or current.cancelled is not False:
            raise IntakeReconcileRequired("request was cancelled after claim")
        current_user_hash = normalized_user_hash(current)
        frozen_user_hash = durable_receipt.normalized_user_hash
        if frozen_user_hash is not None:
            if current_user_hash != frozen_user_hash:
                raise IntakeReconcileRequired("USER request changed after claim")
        elif current_user_hash != normalized_user_hash(request):
            raise IntakeReconcileRequired("USER request changed or was cancelled")
        if current.request_revision_hash != durable_receipt.request_revision_hash:
            raise IntakeReconcileRequired("request revision changed after claim")
        if current.workspace_fingerprint != self._semester_workspace_fingerprint(workspace.semester):
            raise IntakeReconcileRequired("request workspace fingerprint changed")

    def _assert_request_generation_current(
        self,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
    ) -> None:
        """Reject a submitted receipt whose immutable observation is stale."""

        if request.request_type not in {
            RequestType.ASSIGN_COURSE.value,
            RequestType.FILE_DETAILS.value,
        }:
            return
        items: list[IntakeItem] = []
        for intake_id in request.intake_ids:
            item = self.state.get_intake_item(intake_id)
            if item is None:
                raise IntakeReconcileRequired("submitted request references a missing intake observation")
            items.append(item)
        if not items:
            raise IntakeReconcileRequired("submitted request has no immutable intake observation")
        if any(item.semester != workspace.semester for item in items):
            raise IntakeReconcileRequired("submitted request crosses current semester scope")
        observation_refs = [
            (
                item.intake_id,
                sha256_hex(["intake.observation.v1", item.source_hash, item.source_version]),
            )
            for item in items
        ]
        config_fingerprint = self._semester_config_fingerprint(workspace.semester)
        revision = derive_request_revision(
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            semester=workspace.semester,
            request_type=request.request_type,
            observation_refs=observation_refs,
            config_fingerprint=config_fingerprint,
            target_snapshot_hash=receipt.target_snapshot_hash,
        )
        key = derive_request_key(
            provider=self.provider,
            provider_account_binding_id=self.provider_account_binding_id,
            semester=workspace.semester,
            request_type=request.request_type,
            request_revision_hash=revision,
            intake_ids=[item.intake_id for item in items],
            target_snapshot_hash=receipt.target_snapshot_hash,
        )
        if revision != receipt.request_revision_hash or key != receipt.request_key:
            raise IntakeReconcileRequired(
                "submitted request is stale for the current immutable intake observation"
            )

    def _guarded_notion_update(
        self,
        receipt: RequestReceipt,
        request: RequestInput,
        workspace: ResolvedSemesterWorkspace,
        logical: str,
        page_id: str,
        patch: Mapping[str, Any],
        *,
        operation_key: str,
        attempt_operation: str = INTAKE_STATUS_OPERATION,
    ) -> dict[str, Any]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        self._assert_receipt_unchanged(receipt, request, workspace)
        prior = self.state.get_provider_write_attempt(operation_key)
        if prior is not None and prior.response_state == "READBACK_OK" and prior.target_id == page_id and prior.readback_json:
            row = self.notion.read_record(logical, page_id)
            if row is not None and _properties_match(row, patch):
                return row
            raise IntakeReconcileRequired("previous Notion write readback no longer matches")
        if prior is not None:
            row = self.notion.read_record(logical, page_id)
            if row is not None and _properties_match(row, patch):
                self.state.update_provider_write_attempt(
                    operation_key,
                    target_id=page_id,
                    response_state="READBACK_OK",
                    readback_json=row,
                )
                return row
            if prior.response_state == "UNKNOWN":
                raise IntakeReconcileRequired("Notion write outcome is indeterminate")
        if prior is None:
            self.state.record_provider_write_attempt(
                operation=attempt_operation,
                operation_key=operation_key,
                provider=self.provider,
                target_id=page_id,
                response_state="PREPARED",
            )
        try:
            self.notion.update_system_record(logical, page_id, patch)
        except Exception:
            self.state.update_provider_write_attempt(operation_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
            raise
        readback = self.notion.read_record(logical, page_id)
        if readback is None:
            self.state.update_provider_write_attempt(operation_key, response_state="UNKNOWN")
            raise SourceUnavailableError("Notion system write readback is missing")
        if not _properties_match(readback, patch):
            self.state.update_provider_write_attempt(
                operation_key,
                response_state="UNKNOWN",
                readback_json=readback,
            )
            raise IntakeReconcileRequired("Notion system write readback does not match")
        self.state.update_provider_write_attempt(operation_key, response_state="READBACK_OK", readback_json=readback)
        self._assert_receipt_unchanged(receipt, request, workspace)
        return readback

    def _read_session_tuple(self, session_page_id: str, workspace: ResolvedSemesterWorkspace, expected: Mapping[str, Any]) -> dict[str, Any]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        row = self.notion.read_record("sessions", session_page_id)
        if row is None:
            raise SourceUnavailableError("Session readback page is missing")
        for key, expected_value in expected.items():
            actual = row.get(key)
            if key == "Course":
                if _relation_ids(actual) != [expected_value]:
                    raise IntakeReconcileRequired("Session Course relation readback mismatch")
            elif actual != expected_value:
                raise IntakeReconcileRequired(f"Session tuple readback mismatch: {key}")
        return row

    def _read_material_tuple(self, page_id: str, workspace: ResolvedSemesterWorkspace, expected: Mapping[str, Any]) -> dict[str, Any]:
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        row = self.notion.read_record("materials", page_id)
        if row is None:
            raise SourceUnavailableError("Material readback page is missing")
        for key, expected_value in expected.items():
            actual = row.get(key)
            if key == "Course":
                if _relation_ids(actual) != [expected_value]:
                    raise IntakeReconcileRequired("Material Course relation readback mismatch")
            elif actual != expected_value:
                raise IntakeReconcileRequired(f"Material tuple readback mismatch: {key}")
        return row

    @staticmethod
    def _validate_new_session_snapshot(page: Mapping[str, Any], properties: Mapping[str, Any], course_id: str) -> None:
        if page.get("ID") != properties.get("ID") or _relation_ids(page.get("Course")) != [course_id] or page.get("Date") != properties.get("Date") or page.get("Status") != "Not started" or page.get("Recording Status") != "Pending":
            raise IntakeReconcileRequired("new Session creation snapshot readback mismatch")
        if "Session No" in properties and page.get("Session No") != properties["Session No"]:
            raise IntakeReconcileRequired("new Session user Session No readback mismatch")

    @staticmethod
    def _validate_material_snapshot(page: Mapping[str, Any], properties: Mapping[str, Any], course_id: str) -> None:
        for key in ("ID", "Type", "Source Folder", "Original Filename", "Text Status", "Text Source", "Visual Dependency", "AI Priority", "Current Source Version"):
            if page.get(key) != properties.get(key):
                raise IntakeReconcileRequired(f"new Material creation snapshot readback mismatch: {key}")
        if _relation_ids(page.get("Course")) != [course_id]:
            raise IntakeReconcileRequired("new Material Course relation readback mismatch")

    # ------------------------------------------------------------------
    # Composition/fingerprints/jobs
    # ------------------------------------------------------------------
    def _resolve_workspaces(self, semester: str | None) -> list[ResolvedSemesterWorkspace]:
        if semester:
            return resolve_configured_semester(self.config, semester)
        semesters = sorted({course.course_key.split("_", 1)[0] for course in self.config.courses if "_" in course.course_key})
        if not semesters:
            raise IntakeConfigurationError("no configured semester Course Keys")
        return resolve_configured_semester(self.config, semesters[-1])

    def _group_workspaces(self) -> dict[str, list[ResolvedSemesterWorkspace]]:
        result: dict[str, list[ResolvedSemesterWorkspace]] = {}
        for workspace in self.workspaces:
            result.setdefault(workspace.semester, []).append(workspace)
        return result

    def _workspace_for_item(self, item: IntakeItem) -> ResolvedSemesterWorkspace:
        candidates = [workspace for workspace in self.workspaces if workspace.semester == item.semester]
        if not candidates:
            raise IntakeConfigurationError("intake item semester is not currently configured")
        if item.selected_course_key:
            exact = [workspace for workspace in candidates if workspace.course_key == item.selected_course_key]
            if exact:
                return exact[0]
        return candidates[0]

    def _config_fingerprint(self, workspaces: Sequence[ResolvedSemesterWorkspace]) -> str:
        return sha256_hex(
            [
                "intake.config.v1",
                self.config.normalization.schema_version,
                self.config.normalization.processor_version,
                self.provider_account_binding_id or "unbound",
                [
                    workspace.as_dict()
                    for workspace in sorted(workspaces, key=lambda value: value.course_key)
                ],
            ]
        )

    def _workspace_fingerprint(self, workspaces: Sequence[ResolvedSemesterWorkspace]) -> str:
        return sha256_hex(
            [
                "intake.workspace.v1",
                self.provider_account_binding_id or "unbound",
                [
                    workspace.as_dict()
                    for workspace in sorted(workspaces, key=lambda value: value.course_key)
                ],
            ]
        )

    def _semester_workspaces(self, semester: str) -> list[ResolvedSemesterWorkspace]:
        rows = [workspace for workspace in self.workspaces if workspace.semester == semester]
        if not rows:
            raise IntakeConfigurationError("intake item semester is not currently configured")
        return rows

    def _semester_config_fingerprint(self, semester: str) -> str:
        return self._config_fingerprint(self._semester_workspaces(semester))

    def _semester_workspace_fingerprint(self, semester: str) -> str:
        return self._workspace_fingerprint(self._semester_workspaces(semester))

    def _course_page_map(self, workspace: ResolvedSemesterWorkspace) -> dict[str, str]:
        if self.notion is None:
            return {}
        rows = self.notion.list_records("academic_courses")
        result: dict[str, str] = {}
        for row in rows:
            key = row.get("Course Key")
            page_id = _page_id(row)
            if isinstance(key, str) and page_id and key.startswith(workspace.semester + "_"):
                if key in result:
                    raise IntakeReconcileRequired("multiple canonical Course pages share a Course Key")
                result[key] = page_id
        return result

    def _course_id(self, course_key: str, workspace: ResolvedSemesterWorkspace, *, allow_missing: bool = False) -> str | None:
        value = self._course_page_map(workspace).get(course_key)
        if value is None and not allow_missing:
            raise SourceUnavailableError("current canonical Course page is unavailable")
        return value

    def _session_course_key(self, session_page_id: str, workspace: ResolvedSemesterWorkspace) -> str | None:
        if self.notion is None:
            return None
        row = self.notion.read_record("sessions", session_page_id)
        if row is None:
            return None
        course_ids = _relation_ids(row.get("Course"))
        mapping = self._course_page_map(workspace)
        return next((key for key, page_id in mapping.items() if page_id in course_ids), None)

    def _plan_operation_key(self, operation: str, item: IntakeItem, plan: IntakePlan, workspace: ResolvedSemesterWorkspace, request: RequestInput, *, target_id: str | None = None) -> str:
        if operation == INTAKE_SESSION_OPERATION:
            values = (self.provider, item.provider_file_id, item.source_hash, item.source_version, plan.plan_revision, workspace.sessions_data_source_id, target_id, request.session_mode)
        else:
            values = (self.provider, item.provider_file_id, item.source_hash, item.source_version, plan.plan_revision, workspace.materials_data_source_id, request.material_role)
        return self._operation_key(operation, *values)

    def _enqueue_plan_job(self, operation: str, operation_key: str, item: IntakeItem, plan: IntakePlan) -> None:
        self.state.create_job(
            job_key="sha256:" + operation_key,
            operation=operation,
            stage="intake",
            course_key=item.selected_course_key,
            target_entity_id=item.intake_id,
        )

    def _require_item(self, intake_id: str) -> IntakeItem:
        item = self.state.get_intake_item(intake_id)
        if item is None:
            raise KeyError(intake_id)
        return item

    def _read_source_bytes(self, item: IntakeItem) -> bytes:
        metadata = self.drive.read_metadata(item.provider_file_id)
        if metadata.file_id != item.provider_file_id or metadata.owned_by_me is not True:
            raise PolicyDeniedError("source identity or USER ownership readback failed")
        _check_private_drive_metadata(metadata, require_move=True)
        if item.source_hash.startswith("md5:") and metadata.md5_checksum != item.source_hash[4:]:
            raise SourceUnavailableError("source checksum changed after the immutable observation")
        if item.source_hash.startswith("sha256:metadata-") and _metadata_source_hash(metadata) != item.source_hash:
            raise SourceUnavailableError("source metadata changed after the immutable observation")
        data = self.drive.download(item.provider_file_id)
        if not isinstance(data, bytes):
            raise SourceUnavailableError("Drive worker did not return bytes")
        return data

    def _require_binding(self) -> None:
        if not self.provider_account_binding_id:
            raise IntakeConfigurationError("provider account/app binding is not configured")

    def _require_mutation_capability(self) -> None:
        self._require_binding()
        if not self.drive.capabilities.full_intake:
            raise NotImplementedError(self.drive.capabilities.reason or "Drive worker marker/move capability is unsupported")
        if self.notion is None:
            raise SourceUnavailableError("Notion worker port is not configured")
        self._require_notion_workspace_verified()

    def _notion_workspace_readiness(self) -> dict[str, Any]:
        if self.notion is None:
            return {"status": "NOT_VERIFIED", "reason": "Notion worker port is not configured"}
        validator = getattr(self.notion, "validate_workspace", None)
        if not callable(validator):
            return {"status": "NOT_VERIFIED", "reason": "Notion schema/parent readback is unavailable"}
        try:
            result = validator()
        except Exception:  # noqa: BLE001 - readiness fails closed on any adapter failure
            return {"status": "NOT_VERIFIED", "reason": "Notion schema/parent readback failed"}
        if not isinstance(result, Mapping):
            return {"status": "NOT_VERIFIED", "reason": "Notion schema/parent readback is malformed"}
        if result.get("status") != "VERIFIED":
            reason = result.get("reason")
            return {
                "status": "NOT_VERIFIED",
                "reason": reason if isinstance(reason, str) else "Notion schema/parent readback is not verified",
            }
        return dict(result)

    def _require_notion_workspace_verified(self) -> None:
        result = self._notion_workspace_readiness()
        if result.get("status") != "VERIFIED":
            raise SourceUnavailableError(
                "Notion intake data sources are configured but their current parent/schema readback is not verified"
            )

    def _acquire_public_worker_lock(self) -> None:
        lock = getattr(self.state, "_worker_lock", None)
        if getattr(lock, "is_held", False):
            raise ProviderUnavailableError("intake worker is already running")
        if not self.state.acquire_local_worker_lock():
            raise ProviderUnavailableError("intake worker is already running")

    def _initial_request_type(self, item: IntakeItem) -> str:
        # A root upload with no course needs ASSIGN_COURSE first.  A registered
        # course with an unknown kind needs the full details form.
        candidates = item.course_candidates_json
        # The selected course is durable state written by an earlier
        # ASSIGN_COURSE claim.  Discovery candidates may be empty on the next
        # tick, so do not regress to a second assignment request merely
        # because the original upload is still present in + 업로드.
        has_course = bool(item.selected_course_key)
        if isinstance(candidates, str):
            try:
                values = json.loads(candidates)
                has_course = has_course or any(
                    isinstance(value, Mapping) and value.get("course_key")
                    for value in values
                )
            except (TypeError, ValueError):
                pass
        return RequestType.FILE_DETAILS.value if has_course else RequestType.ASSIGN_COURSE.value

    def _wrap_notion(self, notion: NotionWorkerPort | None, semester: str | None) -> NotionIntakeWriter | None:
        if notion is None:
            return None
        candidates = [row for row in self.config.notion.semester_workspaces if semester is None or row.semester == semester]
        if not candidates:
            return None
        row = candidates[0]
        return NotionIntakeWriter(
            notion,
            {
                "academic_courses": row.academic_courses_data_source_id,
                "sessions": row.sessions_data_source_id,
                "materials": row.materials_data_source_id,
                "file_intake": row.file_intake_data_source_id,
                "input_request": row.input_requests_data_source_id,
            },
            parent_page_id=row.connection_settings_files_parent_id,
            semester=row.semester,
        )

    @staticmethod
    def _operation_key(operation: str, *values: Any) -> str:
        return derive_operation_key(operation, *values)


def _relation_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        value = value.get("relation", value.get("ids", []))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            result.append(item["id"])
    return result


def _page_id(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    for key in ("id", "page_id", "_page_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _drive_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _drive_folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _notion_link(page_id: str | None) -> str | None:
    return f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else None


def _sha256_source(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _metadata_source_hash(metadata: DriveMetadata) -> str:
    if metadata.md5_checksum:
        return "md5:" + metadata.md5_checksum
    return "sha256:metadata-" + sha256_hex(
        [
            metadata.file_id,
            metadata.name,
            metadata.mime_type,
            list(metadata.parents),
            metadata.modified_time,
            metadata.size,
        ]
    )


def _check_private_drive_metadata(metadata: DriveMetadata, *, require_move: bool = False) -> None:
    """Require privacy readback at every source/derivative mutation gate."""

    if metadata.is_publicly_shared is None:
        raise SourceUnavailableError("Drive privacy permission readback is missing")
    if metadata.drive_id is not None:
        raise PolicyDeniedError("Drive shared-drive item is outside the owner-only intake scope")
    if metadata.permission_count is None or metadata.owner_only is None:
        raise SourceUnavailableError("Drive owner permission readback is missing")
    if metadata.owner_only is not True:
        raise PolicyDeniedError("Drive item is not solely USER owned")
    if metadata.is_publicly_shared:
        raise PolicyDeniedError("Drive item has broad sharing and cannot enter intake")
    if metadata.can_edit is not True:
        raise SourceUnavailableError("Drive edit capability readback is missing or unavailable")
    if require_move and metadata.can_move is not True:
        raise SourceUnavailableError("Drive move capability readback is missing or unavailable")


def _source_hash_matches(observed_hash: str, raw: bytes) -> bool:
    if observed_hash.startswith("md5:"):
        return observed_hash == "md5:" + hashlib.md5(raw).hexdigest()
    if observed_hash.startswith("sha256:metadata-"):
        return True
    return observed_hash == _sha256_source(raw)


def _property_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple)):
        return _relation_ids(actual) == list(expected)
    return actual == expected


def _properties_match(row: Mapping[str, Any], patch: Mapping[str, Any]) -> bool:
    return all(_property_matches(row.get(key), value) for key, value in patch.items())


def _error_class(error: BaseException) -> str:
    if isinstance(error, PolicyDeniedError):
        return "POLICY_DENIED"
    if isinstance(error, IntakeReconcileRequired):
        return "AMBIGUOUS"
    if isinstance(error, (ProviderUnavailableError, SourceUnavailableError)):
        return "TRANSIENT"
    if isinstance(error, SourcePartialError):
        return "PERMANENT"
    if isinstance(error, RequestValidationError):
        return "POLICY_DENIED"
    return "PERMANENT"


def _intake_error_state(
    error: BaseException,
    default_status: IntakeStatus | str,
) -> tuple[str, str]:
    requested = default_status.value if isinstance(default_status, IntakeStatus) else str(default_status)
    if isinstance(error, IntakeReconcileRequired):
        return IntakeStatus.RECONCILE_REQUIRED.value, "RECONCILE_REQUIRED"
    if isinstance(error, PolicyDeniedError):
        return IntakeStatus.RECONCILE_REQUIRED.value, "POLICY_DENIED"
    if isinstance(error, NotImplementedError):
        return IntakeStatus.UNSUPPORTED.value, "UNSUPPORTED_CAPABILITY"
    if isinstance(error, SourcePartialError):
        return requested, "SOURCE_PARTIAL"
    if isinstance(error, ProviderUnavailableError):
        return IntakeStatus.RETRYABLE_ERROR.value, "PROVIDER_UNAVAILABLE"
    if isinstance(error, SourceUnavailableError):
        return requested, "SOURCE_UNAVAILABLE"
    return requested, getattr(error, "code", type(error).__name__).upper()


def _safe_error_text(error: BaseException) -> str:
    if isinstance(error, ProviderUnavailableError):
        return "Provider operation was unavailable; retry after checking the worker connection."
    if isinstance(error, SourceUnavailableError):
        return str(error) or "The registered source or readback was unavailable."
    if isinstance(error, SourcePartialError):
        return str(error) or "The source was only partially readable."
    if isinstance(error, IntakeReconcileRequired):
        return str(error) or "The provider state needs reconciliation before retrying."
    if isinstance(error, PolicyDeniedError):
        return str(error) or "The requested mutation was denied by the ownership policy."
    return str(error) or type(error).__name__


def _job_status(status: str, content_status: str | None = None) -> str:
    if content_status in {"Partial", "Needs Review"}:
        return "PARTIAL"
    if content_status in {"Unavailable", "Failed"}:
        return "NEEDS_REVIEW"
    if status == IntakeStatus.ORGANIZED.value:
        return "READY"
    if status == IntakeStatus.REGISTERED.value:
        return "PARTIAL"
    return "NEEDS_REVIEW"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


__all__ = [
    "INTAKE_DERIVATIVE_OPERATION",
    "INTAKE_DISCOVER_OPERATION",
    "INTAKE_FOLDER_OPERATION",
    "INTAKE_MATERIAL_OPERATION",
    "INTAKE_MOVE_OPERATION",
    "INTAKE_OPERATIONS",
    "INTAKE_REQUEST_SYNC_OPERATION",
    "INTAKE_SESSION_OPERATION",
    "INTAKE_STATUS_OPERATION",
    "IntakeReconcileRequired",
    "IntakeWorker",
]
