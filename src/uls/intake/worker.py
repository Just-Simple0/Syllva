"""Executable local semester intake worker.

The worker is deliberately provider-neutral above the two worker ports.  It
commits observations and write attempts before external calls, validates full
provider readbacks, and only moves an original Drive file after a canonical
binding is registered.  It never calls an AI provider or the read-only MCP
surface.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    DriveMetadata,
    DriveWorkerPort,
    ensure_marked_folder,
    require_private_ownership,
)
from uls.adapters.notion.intake import AUTO_RESOLVED_STATUS, NotionIntakeWriter, NotionWorkerPort
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
from uls.intake.attestation import ReconnectRequiredError, WorkerEntryAttestation
from uls.intake.classification import (
    AI_KIND_OPTIONS,
    ORIGIN_OPTIONS,
    RULE_TABLE_VERSION,
    TAG_RULE_VERSION,
    CourseAliasIndex,
    HandlingMode,
    Kind,
    Origin,
    RecordingEntry,
    SemesterRange,
    build_calendar,
    course_aliases_from_config,
    handling_mode,
    transcript_signals,
)
from uls.intake.classification.calendar import CourseCalendar
from uls.intake.classification.pipeline import (
    BLOCK_BYTES_UNPROVEN,
    BLOCK_CANVAS_COURSE,
    BLOCK_DUPLICATE_CONTENT,
    BLOCK_DUPLICATE_UNPROVEN,
    BLOCK_PLAN_CONFLICT,
    BLOCK_PLAN_RECONCILE,
    BLOCK_PLAN_SUPERSEDED,
    BLOCK_SESSION_AMBIGUOUS,
    BLOCK_SESSION_OCCUPIED,
    BLOCK_SESSION_UNKNOWN,
    NOTE_AUTO_UNAVAILABLE,
    NOTE_FORMAT_KIND_MISMATCH,
    ClassificationOutcome,
    SourceProbe,
    classify_upload_item,
)
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
from uls.state.classification_state import AutoResolveIntent, ClassificationRecord
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


AUTO_CLOSE_RECONCILE_CODE = "AUTO_RESOLVE_RECONCILE"


class RequestTerminalError(UlsError):
    """A terminal receipt (Applied/Cancelled/AutoResolved) is never re-claimed (plan §3.4 R2)."""

    code = "REQUEST_TERMINAL"


@dataclass(frozen=True)
class _LayoutValidationContext:
    """Proof that each listed semester layout passed a fresh provider read."""

    workspace_fingerprints: tuple[tuple[str, str], ...]

    def fingerprint_for(self, semester: str) -> str | None:
        return dict(self.workspace_fingerprints).get(semester)


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
        entry_attestor: Any | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.drive = drive
        self.provider_account_binding_id = provider_account_binding_id
        # Personal OAuth compositions install the same attestor object on the
        # Drive adapter; every public entry below obtains one fresh
        # attestation before its first effect (P2 plan §5). Service-account
        # compositions leave this None and are unchanged.
        self.entry_attestor = entry_attestor
        # Intake classification v2: a configured Notion profile that does not
        # match the configured data sources is recorded, never applied.
        self._classification_profile_mismatch: str | None = None
        # OAuth compositions hold this for a whole public entry so no other
        # entry can refresh the shared credential while provider work runs.
        self._entry_lock = threading.Lock()
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
        # Installed only by the explicit v1.3 composition after readiness checks.
        # The runner remains the sole local-worker-lock owner.
        self.request_coordinators: list[Any] = []
        self.request_extension_readiness: dict[str, Any] = {}
        self.extension_resources: list[Any] = []

    def close(self) -> None:
        """Close extension stores and the main state even if one close fails."""
        from contextlib import ExitStack

        with ExitStack() as resources:
            resources.callback(self.state.close)
            for resource in self.extension_resources:
                resources.callback(resource.close)
            self.extension_resources.clear()

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
            "request_extensions": dict(self.request_extension_readiness),
            "classification": self._classification_readiness(),
        }

    @contextlib.contextmanager
    def _attested_entry(self) -> Iterator[WorkerEntryAttestation | None]:
        """Gate one public entry: fresh attestation first, then the adapter context.

        Raises ``ReconnectRequiredError`` before any lock, provider, receipt,
        generation or plan effect when the attestor rejects the entry.
        """

        if self.entry_attestor is None:
            yield None
            return
        if not self._entry_lock.acquire(blocking=False):
            raise ProviderUnavailableError("intake worker is already running")
        try:
            attestation = self.entry_attestor.attest_entry()
            enter = getattr(self.drive, "attested", None)
            if callable(enter):
                with enter(attestation):
                    yield attestation
            else:
                yield attestation
        finally:
            self._entry_lock.release()

    def sync(self) -> int:
        """Discover current uploads under the single-active worker lock."""

        with self._attested_entry():
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
            layout_context = self._context_for_validated_workspaces(workspaces)
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
                if item.status == IntakeStatus.NEEDS_INPUT.value and self._classification_enabled():
                    # Plan §4 boundary: observe → classify → S3 decision → request.
                    self._classify_discovered_item(item, workspace, config_fingerprint, workspace_fingerprint)
                    item = self._require_item(item.intake_id)
                if self.provider_account_binding_id:
                    self._project_file_intake_safe(item, workspace, workspace_fingerprint)
                if (
                    item.status == IntakeStatus.NEEDS_INPUT.value
                    and self.provider_account_binding_id
                    and self._request_creation_allowed(item)
                ):
                    request_type = self._initial_request_type(item)
                    try:
                        self._create_input_request_with_context(
                            item,
                            workspace,
                            request_type=request_type,
                            target_snapshot=None,
                            layout_context=layout_context,
                        )
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
                            layout_context=layout_context,
                        )
        return total

    def run_once(self, *, sync: bool = True, process: bool = True, max_jobs: int = 100) -> dict[str, Any]:
        """Run one bounded local tick under the existing StateStore lock."""

        if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
            raise ValueError("max_jobs must be 1–1000")
        entry: contextlib.AbstractContextManager[Any] = (
            contextlib.nullcontext() if self.entry_attestor is None else self._attested_entry()
        )
        try:
            entry.__enter__()
        except ProviderUnavailableError:
            return {"status": "already_running", "discovered": 0, "processed": 0}
        except ReconnectRequiredError:
            # Fail closed before the lock, discovery, claims, jobs or extensions.
            return {
                "status": "failed",
                "code": "RECONNECT_REQUIRED",
                "discovered": 0,
                "processed": 0,
                "failed": 1,
                "needs_input": 0,
                "request_extensions": {},
            }
        try:
            return self._run_once_attested(sync=sync, process=process, max_jobs=max_jobs)
        except ReconnectRequiredError:
            return {
                "status": "failed",
                "code": "RECONNECT_REQUIRED",
                "discovered": 0,
                "processed": 0,
                "failed": 1,
                "needs_input": 0,
                "request_extensions": {},
            }
        finally:
            entry.__exit__(None, None, None)

    def _run_once_attested(self, *, sync: bool, process: bool, max_jobs: int) -> dict[str, Any]:
        if not self.state.acquire_local_worker_lock():
            return {"status": "already_running", "discovered": 0, "processed": 0}
        try:
            if self.notion is not None:
                # Plan §3.5 recovery runs before discovery, claims and jobs so a pending
                # closure never races a human claim in the same tick; it runs even when
                # AUTO is switched off so an intent that already started is settled (r1 R9).
                self._recover_auto_resolve_intents()
            discovered = self._sync_unlocked() if sync else 0
            processed = failed = needs_input = 0
            run_layout_context: _LayoutValidationContext | None = None
            if process:
                try:
                    run_layout_context = self._fresh_layout_context(
                        self._run_layout_workspaces()
                    )
                except ReconnectRequiredError:
                    raise  # a rejected OAuth refresh is never converted or recorded as another failure
                except Exception:  # noqa: BLE001 - fail closed before any request-page access
                    return {
                        "status": "failed",
                        "discovered": discovered,
                        "processed": 0,
                        "failed": 1,
                        "needs_input": 0,
                        "request_extensions": {},
                    }
                for request_key in self._submitted_request_keys()[:max_jobs]:
                    try:
                        self._claim_request_unlocked(
                            request_key, layout_context=run_layout_context
                        )
                    except RequestValidationError as exc:
                        self._record_request_error(
                            request_key, exc, layout_context=run_layout_context
                        )
                        needs_input += 1
                    except (
                        IntakeReconcileRequired,
                        ProviderUnavailableError,
                        SourceUnavailableError,
                        NotImplementedError,
                    ) as exc:
                        self._record_request_error(
                            request_key, exc, layout_context=run_layout_context
                        )
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
                    item_layout_context: _LayoutValidationContext | None = None
                    try:
                        item = self._require_item(job.target_entity_id)
                        workspace = self._workspace_for_item(item)
                        item_layout_context = self._fresh_item_layout_context(workspace)
                        result = self._process_item_unlocked(
                            job.target_entity_id,
                            layout_context=item_layout_context,
                        )
                        status = result.get("status", IntakeStatus.ORGANIZED.value)
                        content_status = result.get("content_status")
                        self.state.complete_job(
                            job.id,
                            status=_job_status(status, content_status),
                        )
                        self._mark_request_applied(job.target_entity_id, result)
                        processed += 1
                    except RequestValidationError as exc:
                        self._record_job_error(
                            job,
                            exc,
                            default_status=IntakeStatus.NEEDS_INPUT,
                            layout_context=item_layout_context,
                        )
                        needs_input += 1
                    except ReconnectRequiredError:
                        # A rejected OAuth refresh ends the tick: no further job,
                        # extension, Notion or Drive effect is attempted.
                        raise
                    except Exception as exc:  # noqa: BLE001 - worker records a safe durable failure
                        self._record_job_error(
                            job, exc, layout_context=item_layout_context
                        )
                        failed += 1
            extensions: dict[str, Any] = {}
            if process:
                for coordinator in self.request_coordinators:
                    try:
                        snapshot = coordinator.snapshot()
                        barrier = coordinator.receive(snapshot)
                        result = coordinator.publish(barrier)
                        extensions[coordinator.workspace] = result
                        if result.get("status") != "ok":
                            needs_input += 1
                    except ReconnectRequiredError:
                        raise
                    except Exception:  # noqa: BLE001 - no provider payload in status
                        extensions[coordinator.workspace] = {"status": "failed"}
                        failed += 1
            return {
                "status": "failed" if failed else "needs_input" if needs_input else "ok",
                "discovered": discovered,
                "processed": processed,
                "failed": failed,
                "needs_input": needs_input,
                "readiness": self.readiness(),
                "request_extensions": extensions,
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
            if receipt.state in {"Applied", "Cancelled", "AutoResolved"}:
                continue
            intent = self.state.get_auto_resolve_intent(request_key)
            if intent is not None and intent.state in {"PENDING", "RECONCILE"}:
                # A closure whose provider outcome is unknown is a durable barrier (§3.5).
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
        layout_context: _LayoutValidationContext | None = None,
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
        if self.notion is not None and self._layout_context_allows(
            layout_context, workspace
        ):
            try:
                self._project_file_intake(item, workspace, self._semester_workspace_fingerprint(workspace.semester))
            except (IntakeReconcileRequired, PolicyDeniedError, ProviderUnavailableError, SourceUnavailableError):
                # The durable item/error is the actionable local state.  A
                # later tick can retry the system-only projection.
                pass
        return item

    def _record_request_error(
        self,
        request_key: str,
        error: BaseException,
        *,
        layout_context: _LayoutValidationContext | None = None,
    ) -> None:
        """Record a request failure without touching USER input fields."""

        receipt = self.state.get_request_receipt(request_key)
        if receipt is None:
            return
        try:
            item = self._item_for_receipt(receipt)
            workspace = self._workspace_for_item(item)
        except (IntakeConfigurationError, SourceUnavailableError):
            return
        self._record_item_error(
            item,
            workspace,
            error,
            default_status=IntakeStatus.NEEDS_INPUT,
            layout_context=layout_context,
        )
        if (
            self.notion is None
            or not receipt.provider_page_id
            or not self._layout_context_allows(layout_context, workspace)
        ):
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
        layout_context: _LayoutValidationContext | None = None,
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
            self._record_item_error(
                item,
                workspace,
                error,
                default_status=default_status,
                layout_context=layout_context,
            )

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
                self._record_request_error(
                    receipt.request_key, error, layout_context=layout_context
                )

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
        with self._attested_entry():
            layout_context = self._fresh_item_layout_context(workspace)
            return self._create_input_request_with_context(
                item,
                workspace,
                request_type=request_type,
                target_snapshot=target_snapshot,
                layout_context=layout_context,
            )

    def _create_input_request_with_context(
        self,
        item: IntakeItem,
        workspace: ResolvedSemesterWorkspace,
        *,
        request_type: str,
        target_snapshot: Mapping[str, Any] | None,
        layout_context: _LayoutValidationContext,
    ) -> RequestReceipt:
        if not self._layout_context_allows(layout_context, workspace):
            raise IntakeReconcileRequired("Input Request layout context is stale or missing")
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
            suggestion_props = self._suggestion_properties(item)
            properties.update(suggestion_props)
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
            except ReconnectRequiredError:
                raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
            if suggestion_props:
                self.state.upsert_intake_suggestion(item.intake_id, written_to_notion=1)
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

        with self._attested_entry():
            self._acquire_public_worker_lock()
            try:
                return self._claim_request_unlocked(request_key)
            finally:
                self.state.release_local_worker_lock()

    def _claim_request_unlocked(
        self,
        request_key: str,
        *,
        layout_context: _LayoutValidationContext | None = None,
    ) -> dict[str, Any]:
        """Validate one USER-submitted request and enqueue its immutable plan."""

        receipt = self.state.get_request_receipt(request_key)
        if receipt is None or not receipt.provider_page_id:
            raise SourceUnavailableError("Request Key is not bound to a provider page")
        if receipt.state in {"Applied", "Cancelled", "AutoResolved"}:
            # Fixed refusal with no plan, job or Notion write (plan §3.4 R2).
            raise RequestTerminalError(f"request receipt is terminal ({receipt.state})")
        pending_intent = self.state.get_auto_resolve_intent(request_key)
        if pending_intent is not None and pending_intent.state in {"PENDING", "RECONCILE"}:
            raise IntakeReconcileRequired("an automatic closure of this request is still unresolved")
        item_hint = self._item_for_receipt(receipt)
        workspace = self._workspace_for_item(item_hint)
        if receipt.input_requests_data_source_id != workspace.input_requests_data_source_id:
            raise IntakeReconcileRequired("request belongs to a different current workspace")
        if layout_context is None:
            layout_context = self._fresh_item_layout_context(workspace)
        elif not self._layout_context_allows(layout_context, workspace):
            raise IntakeReconcileRequired("request claim layout context is stale or missing")
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
            schema_profile=getattr(self.notion, "schema_profile", "legacy5") or "legacy5",
        )
        if not errors and request.request_type == RequestType.FILE_DETAILS.value:
            # §6.1 common precondition: the (Kind, MIME, extension, signature) combination
            # must be in the handling matrix before any external write (r10 R4).
            errors = self._human_handling_errors(request)
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
                next_receipt = self._create_input_request_with_context(
                    self._require_item(item.intake_id),
                    workspace,
                    request_type=RequestType.FILE_DETAILS.value,
                    target_snapshot=None,
                    layout_context=layout_context,
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

        with self._attested_entry():
            self._acquire_public_worker_lock()
            try:
                return self._process_item_unlocked(intake_id)
            finally:
                self.state.release_local_worker_lock()

    def _process_item_unlocked(
        self,
        intake_id: str,
        *,
        layout_context: _LayoutValidationContext | None = None,
    ) -> dict[str, Any]:
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
        if layout_context is None:
            layout_context = self._fresh_item_layout_context(workspace)
        elif not self._layout_context_allows(layout_context, workspace):
            raise IntakeReconcileRequired("item processing layout context is stale or missing")
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
        if request.kind in self._human_material_kinds():
            # A v2 HUMAN Material Kind keeps the user's Role as Materials.Type and
            # takes the PDF normalization path only where the Kind×format matrix of
            # plan §6.1 allows PDF.  Opaque formats (code, tabular) need the
            # REGISTER_OPAQUE_NO_RETRIEVAL path of P-B and fail closed (P-A r2 #4).
            from uls.intake.classification.taxonomy import PDF_MATERIAL_KINDS, Kind

            kind = Kind(request.kind)
            pdf_allowed = kind in PDF_MATERIAL_KINDS or kind is Kind.ASSIGNMENT_RESOURCE
            is_pdf = (item.mime_type or "").lower() == "application/pdf"
            if not pdf_allowed and is_pdf:
                raise RequestValidationError(
                    (f"Kind {request.kind} never takes a PDF source (plan §6.1 matrix)",)
                )
            if not is_pdf:
                raise RequestValidationError(
                    (f"Kind {request.kind} on a non-PDF source needs the opaque registration path (not available yet)",)
                )
            return self._process_material(item, plan, receipt, request, workspace)
        raise RequestValidationError(("unsupported plan kind",))

    # ------------------------------------------------------------------
    # Intake classification v2 (P-B1): S0/S1 in the sync path, S3 suggestions
    # ------------------------------------------------------------------
    def _classification_enabled(self) -> bool:
        configured = getattr(getattr(self.config, "intake", None), "classification", None)
        return bool(getattr(configured, "enabled", False))

    def _classification_readiness(self) -> dict[str, Any]:
        """Plan §4 O2 readiness block; P-C adds the model counters."""

        counts = self.state.classification_counts() if hasattr(self.state, "classification_counts") else {}
        if not self._classification_enabled():
            status, reason = "DISABLED", "intake.classification.enabled is false"
        elif not self._classification_profile_active():
            status, reason = "DISABLED", "no verified v2 Notion schema profile"
        else:
            status, reason = "READY", None
        auto_pending = len(self.state.list_intake_plans(plan_authority="AUTO_CLASSIFICATION", status="AUTO_PENDING")) \
            if hasattr(self.state, "list_intake_plans") else 0
        pending_intents = len(self.state.list_auto_resolve_intents()) if hasattr(self.state, "list_auto_resolve_intents") else 0
        return {
            "status": status,
            "reason": reason,
            "auto_enabled": self._auto_enabled(),
            "auto_pending_plans": auto_pending,
            "pending_auto_resolve_intents": pending_intents,
            "rule_table_version": RULE_TABLE_VERSION,
            "tag_rule_version": TAG_RULE_VERSION,
            "calls_remaining": 0,
            "deferred_items": counts.get("deferred_items", 0),
            "human_fallback_items": counts.get("human_fallback_items", 0),
            "partial_tag_documents": counts.get("partial_tag_documents", 0),
            "stale_tag_documents": counts.get("stale_tag_documents", 0),
            "invalid_response_count": 0,
            "transport_failure_count": 0,
        }

    def _request_creation_allowed(self, item: IntakeItem) -> bool:
        """observe → classify → S3 decision → request (plan §4): with classification
        enabled a draft needs an explicit CLASSIFIED/HUMAN decision (or a human-labelled
        item); with it disabled the current behaviour is unchanged."""

        if not self._classification_enabled():
            return True
        if item.classification_source == "human":
            return True
        # CLASSIFIED now means "an AUTO plan owns this item" (P-B2a); only an explicit
        # S3 decision (HUMAN) opens a draft.
        return item.classification_state == "HUMAN"

    def _course_alias_index(self, semester: str) -> CourseAliasIndex:
        entries = [
            (course.course_key, course_aliases_from_config(
                course.course_key, course.name, course.code, getattr(course, "aliases", ()) or ()
            ))
            for course in self.config.courses
            if course.semester == semester
        ]
        return CourseAliasIndex.build(entries)

    def _semester_range(self, semester: str) -> SemesterRange | None:
        for registry in self.config.google_drive.semester_registries:
            if registry.semester != semester:
                continue
            start, end = getattr(registry, "start_date", ""), getattr(registry, "end_date", "")
            if start and end:
                return SemesterRange(date.fromisoformat(start), date.fromisoformat(end), "config")
        return None

    def _course_calendar(self, course_key: str, semester: SemesterRange | None) -> CourseCalendar | None:
        rows = self.state.list_recording_calendar_current(course_key)
        complete = self.state.recording_calendar_complete(course_key)
        if not rows:
            if self.state.recording_calendar_course(course_key) is None:
                return None  # no collection ever recorded: NO_CALENDAR
            if complete:
                return build_calendar(course_key, [], semester=semester, collection_complete=True)
            # A collection was started but is not complete: AMBIGUOUS, never NO_CALENDAR (r7).
            return build_calendar(course_key, [], semester=semester, collection_complete=False)
        entries = [
            RecordingEntry(row.canvas_course_id, row.resource_id, row.observation_revision, row.week,
                           date.fromisoformat(row.recorded_on))
            for row in rows
        ]
        return build_calendar(course_key, entries, semester=semester, collection_complete=complete)

    def _source_probe(self, item: IntakeItem) -> SourceProbe:
        """Bounded, proven download (plan §3.4 R3): the declared size must exist and
        be within ``max_source_bytes`` before any byte is read, and the received
        payload must match it exactly, otherwise nothing is proven."""

        limit = int(getattr(getattr(self.config.intake, "classification", None), "max_source_bytes", 0) or 0)
        try:
            metadata = self.drive.read_metadata(item.provider_file_id)
            size = getattr(metadata, "size", None)
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                return SourceProbe(None, False, unavailable=True)  # size unknown: cannot bound the read
            if limit and size > limit:
                return SourceProbe(None, False, too_large=True)
            # The adapter aborts the transfer once it passes the bound and refuses a body
            # whose length differs from the declared size; nothing oversized is kept.
            payload = self._read_source_bytes(item, max_bytes=limit or None)
        except ReconnectRequiredError:
            raise
        except (SourceUnavailableError, SourcePartialError, PolicyDeniedError, ProviderUnavailableError):
            return SourceProbe(None, False, unavailable=True)
        if limit and len(payload) > limit:
            return SourceProbe(None, False, too_large=True)
        if len(payload) != size:
            return SourceProbe(None, False, unavailable=True)  # partial or misreported: not complete
        return SourceProbe(payload, True)

    def _explicit_course_candidate(self, item: IntakeItem) -> str | None:
        candidates = item.course_candidates_json
        if isinstance(candidates, str):
            try:
                candidates = json.loads(candidates)
            except (TypeError, ValueError):
                return None
        if not isinstance(candidates, list):
            return None
        keys = [
            value.get("course_key") for value in candidates
            if isinstance(value, Mapping) and value.get("reason") == "registered upload folder"
        ]
        return keys[0] if len(keys) == 1 and isinstance(keys[0], str) else None

    def _classify_discovered_item(
        self,
        item: IntakeItem,
        workspace: ResolvedSemesterWorkspace,
        config_fingerprint: str,
        workspace_fingerprint: str,
    ) -> ClassificationOutcome | None:
        """S0/S1 + axes + calendar for one discovered upload; persists record, item columns and suggestion."""

        if item.classification_source == "human":
            return None
        alias_index = self._course_alias_index(workspace.semester)
        semester = self._semester_range(workspace.semester)
        explicit = self._explicit_course_candidate(item)
        axes = transcript_signals(item.original_name, alias_index)
        course_key = explicit or (str(axes["course_key"]) if axes.get("course_key") else None)
        calendar = self._course_calendar(course_key, semester) if course_key else None
        binding = self.state.get_canvas_drive_binding(item.provider_file_id)

        def classify(probe: SourceProbe | None) -> ClassificationOutcome:
            # S0: PROFESSOR_SOURCE only when the stored Canvas binding is proven by the
            # bytes just read (exact file id + byte_sha256); never from a title.  The
            # proven binding's resource kind is the only Canvas signal S1 may use (r2 #3).
            verified_binding = (
                binding is not None and probe is not None and probe.byte_sha256 is not None
                and binding.get("byte_sha256") == probe.byte_sha256
            )
            return classify_upload_item(
                name=item.original_name, mime_type=item.mime_type, from_upload_folder=True,
                explicit_course_key=explicit,
                origin=Origin.PROFESSOR_SOURCE if verified_binding else None,
                canvas_attachment_of=str(binding["resource_kind"]) if verified_binding and binding else None,
                alias_index=alias_index, semester=semester, calendar=calendar, probe=probe,
            )

        outcome = classify(None)
        if outcome.kind is Kind.UNSUPPORTED:
            # P0 is terminal (plan §3.1): no download, no draft, no Material; File Intake
            # carries UNSUPPORTED and the fixed reason code (r5).
            self.state.update_intake_item(
                item.intake_id,
                status=IntakeStatus.UNSUPPORTED.value,
                content_status="Unavailable",
                last_error_code="UNSUPPORTED_FORMAT",
                last_error="The file format is not part of the current deterministic intake path.",
                origin=outcome.origin.value,
                classified_kind=Kind.UNSUPPORTED.value,
                classification_source=f"rule:{outcome.rule_id}",
                classification_state="CLASSIFIED",
            )
            self.state.upsert_intake_suggestion(item.intake_id, **outcome.suggestion_fields())
            return outcome
        probe: SourceProbe | None = None
        if outcome.decided or binding is not None:
            # S1-decided items need the byte proof; a stored Canvas binding needs it to
            # settle S0 (and its P4 signal) even when S1 is undecided (r2 R3).
            probe = self._source_probe(item)
            outcome = classify(probe)
        record_id: str | None = None
        byte_proven = probe is not None and probe.complete and probe.byte_sha256 is not None
        session_mode, session_id, session_hash, session_block = outcome.session_mode, None, None, None
        if outcome.session_mode is not None and self._auto_enabled():
            session_mode, session_id, session_hash, session_block = self._session_binding(
                item, outcome.course_key, outcome.recorded_date, workspace
            )
        verified_basis = None
        if binding is not None and probe is not None and probe.byte_sha256 is not None \
                and binding.get("byte_sha256") == probe.byte_sha256:
            verified_basis = self._canvas_basis(binding)
        if outcome.decided and outcome.kind is not None and byte_proven and probe is not None:
            record = self.state.create_classification_record(
                intake_id=item.intake_id,
                provider_file_id=item.provider_file_id,
                byte_sha256=probe.byte_sha256 if probe else None,
                byte_md5=probe.byte_md5 if probe else None,
                snapshot_sha256=None,
                source_version=item.source_version,
                workspace_fingerprint=workspace_fingerprint,
                config_fingerprint=config_fingerprint,
                rule_table_version=outcome.rule_table_version,
                decision={"type": "rule", "rule_id": outcome.rule_id,
                          "candidates": [kind.value for kind in outcome.candidates],
                          "source_name": item.original_name, "source_mime": item.mime_type},
                kind=outcome.kind.value,
                origin=outcome.origin.value,
                course_key=outcome.course_key,
                week=outcome.week,
                decided_date=None if outcome.recorded_date is None else outcome.recorded_date.isoformat(),
                course_basis={
                    "type": outcome.course_basis or "none",
                    "alias_inventory_hash": self._alias_inventory_hash(workspace.semester),
                    "canvas_binding": verified_basis if outcome.origin is Origin.PROFESSOR_SOURCE else None,
                },
                session_mode=session_mode,
                session_id=session_id,
                sessions_inventory_hash=session_hash,
                calendar_projection_revision_hash=None if calendar is None else calendar.revision_hash(),
                semester_range_basis=None if semester is None else semester.basis(),
            )
            record_id = record.record_id
        # A Kind without a byte proof (download failed / too large) stays a local
        # suggestion only: no record, no classified_kind, HUMAN fallback (r1).
        decided = outcome.decided and record_id is not None
        record = self.state.get_classification_record(record_id) if record_id else None
        blockers = self._stage_a_blockers(item, outcome, record)
        if session_block is not None and outcome.decided and record is not None:
            blockers = (*blockers, session_block)
        auto_plan: IntakePlan | None = None
        if decided and record is not None and not blockers and self._auto_enabled():
            auto_plan, plan_block = self._ensure_auto_plan(item, outcome, record, workspace_fingerprint)
            if plan_block is not None:
                blockers = (plan_block,)
        if auto_plan is None and not blockers:
            blockers = (NOTE_AUTO_UNAVAILABLE,)
        outcome_notes = outcome.suggestion_fields()
        if blockers:
            note = outcome_notes.get("suggestion_note")
            prefix = [str(note)] if isinstance(note, str) and note else []
            outcome_notes["suggestion_note"] = ";".join([*prefix, *blockers])
        self.state.update_intake_item(
            item.intake_id,
            origin=outcome.origin.value,
            classified_kind=outcome.kind.value if decided and outcome.kind is not None else None,
            classification_source=f"rule:{outcome.rule_id}" if decided else None,
            classification_record_id=record_id,
            inferred_course_key=outcome.course_key,
            inferred_week=outcome.week,
            inferred_date=None if outcome.recorded_date is None else outcome.recorded_date.isoformat(),
            calendar_match=None if outcome.calendar is None else outcome.calendar.status.value,
            # CLASSIFIED = an AUTO_CLASSIFICATION plan owns the item (no draft); every
            # other outcome is an S3 decision and receives a HUMAN draft with
            # suggestions (plan §3.3, §3.4 stage A).
            classification_state="CLASSIFIED" if auto_plan is not None else "HUMAN",
        )
        self.state.upsert_intake_suggestion(item.intake_id, **outcome_notes)
        if auto_plan is not None and record is not None:
            self._close_blank_drafts(self._require_item(item.intake_id), auto_plan, record, workspace)
        return outcome

    # ------------------------------------------------------------------
    # Intake classification v2 (P-B2a): AUTO_PENDING plans and Draft auto-close
    # ------------------------------------------------------------------
    def _auto_enabled(self) -> bool:
        return bool(
            self._classification_enabled()
            and self._classification_profile_active()
            and self.provider_account_binding_id
            and self.drive.capabilities.full_intake
        )

    def _stage_a_blockers(
        self, item: IntakeItem, outcome: ClassificationOutcome, record: ClassificationRecord | None
    ) -> tuple[str, ...]:
        """Plan §3.4 stage A: pure blockers + byte proof + the duplicate-content gate.

        The duplicate gate is complete over the whole store (no listing bound) and fails
        closed: another live item whose bytes cannot be proven different (metadata
        surrogate hash, same declared size, no byte-proven record) blocks AUTO (r1 R1).
        """

        blockers = list(outcome.stage_a_blockers())
        if not outcome.decided or outcome.kind is Kind.UNSUPPORTED:
            return tuple(blockers)  # nothing to prove for an undecided or terminal item
        if record is None or not record.byte_sha256:
            blockers.append(BLOCK_BYTES_UNPROVEN)
            return tuple(dict.fromkeys(blockers))
        if item.last_error_code == "DUPLICATE_CANDIDATE":
            blockers.append(BLOCK_DUPLICATE_CONTENT)
        candidates = self.state.duplicate_content_candidates(
            intake_id=item.intake_id, provider=self.provider, byte_sha256=record.byte_sha256,
            byte_md5=record.byte_md5, size=self._declared_size(item),
        )
        if candidates["proven"]:
            blockers.append(BLOCK_DUPLICATE_CONTENT)
        if candidates["unproven"]:
            blockers.append(BLOCK_DUPLICATE_UNPROVEN)
        if record.origin == Origin.PROFESSOR_SOURCE.value and self._canvas_course_mismatch(item, record):
            blockers.append(BLOCK_CANVAS_COURSE)
        return tuple(dict.fromkeys(blockers))

    def _canvas_course_mismatch(self, item: IntakeItem, record: ClassificationRecord) -> bool:
        """A PROFESSOR_SOURCE is only usable for the course its Canvas course is mapped to
        in the verified ``canvas_course_map``; unmapped or different → HUMAN (r4 #3)."""

        binding = self.state.get_canvas_drive_binding(item.provider_file_id)
        mapping = getattr(getattr(self.config.intake, "classification", None), "canvas_course_map", {}) or {}
        mapped = None if binding is None else mapping.get(binding.get("canvas_course_id"))
        return mapped is None or mapped != record.course_key

    def _session_binding(
        self, item: IntakeItem, course_key: str | None, recorded: date | None, workspace: ResolvedSemesterWorkspace
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Plan §2.3/§3.4: bind a matched transcript to the Notion Sessions inventory of its
        course and date.  No Session → NEW; exactly one *proven free* Session → EXISTING with
        its ID; several, occupied (a Normalized Transcript pointer or another file's canonical
        source binding), an unreadable ID or an unreadable inventory → blocked, never guessed.
        The occupancy evidence is part of the inventory hash."""

        if recorded is None or not course_key or self.notion is None:
            return None, None, None, BLOCK_SESSION_UNKNOWN
        course_workspace = next(
            (w for w in self.workspaces if w.semester == workspace.semester and w.course_key == course_key), None
        )
        if course_workspace is None:
            return None, None, None, BLOCK_SESSION_UNKNOWN
        try:
            course_id = self._course_page_map(course_workspace).get(course_key)
            if course_id is None:
                return None, None, None, BLOCK_SESSION_UNKNOWN
            rows = self._inventory("sessions", course_workspace, course_id)
        except ReconnectRequiredError:
            raise
        except (IntakeReconcileRequired, SourceUnavailableError, ProviderUnavailableError, PolicyDeniedError, ValueError):
            return None, None, None, BLOCK_SESSION_UNKNOWN
        wanted = recorded.isoformat()

        def day(row: Mapping[str, Any]) -> str | None:
            value = row.get("Date")
            if isinstance(value, Mapping):
                value = value.get("start")
            return str(value)[:10] if value else None

        same = sorted((row for row in rows if day(row) == wanted), key=lambda row: str(row.get("ID")))
        evidence = []
        for row in same:
            entity_id = row.get("ID")
            binding = self.state.session_source_binding_for(course_key, entity_id) if isinstance(entity_id, str) else None
            evidence.append([entity_id, day(row), row.get("Recording Status"), bool(row.get("Normalized Transcript")),
                             None if binding is None else binding.get("provider_file_id")])
        digest = sha256_hex(["intake.sessions-inventory.v2", course_key, wanted, evidence])
        if not same:
            return SessionMode.NEW.value, None, digest, None
        if len(same) > 1:
            return None, None, digest, BLOCK_SESSION_AMBIGUOUS
        row = same[0]
        entity_id, binding_file = evidence[0][0], evidence[0][4]
        if not isinstance(entity_id, str) or not entity_id:
            return None, None, digest, BLOCK_SESSION_UNKNOWN
        own_binding = binding_file == item.provider_file_id
        if (
            row.get("Recording Status") not in (None, "", "Pending")
            or (evidence[0][3] and not own_binding)
            or (binding_file is not None and not own_binding)
        ):
            return None, None, digest, BLOCK_SESSION_OCCUPIED
        return SessionMode.EXISTING.value, entity_id, digest, None

    def _declared_size(self, item: IntakeItem) -> int | None:
        for observation in reversed(self.state.list_intake_observations(item.intake_id)):
            if observation.source_version != item.source_version:
                continue
            try:
                size = json.loads(observation.metadata_json).get("size")
            except (TypeError, ValueError, AttributeError):
                return None
            return size if isinstance(size, int) and not isinstance(size, bool) else None
        return None

    def _ensure_auto_plan(
        self, item: IntakeItem, outcome: ClassificationOutcome, record: ClassificationRecord, workspace_fingerprint: str
    ) -> tuple[IntakePlan | None, str | None]:
        """Create (or reuse) the AUTO_PENDING plan bound to this classification record.

        A plan of the same revision that a human overtook (SUPERSEDED) or whose preflight
        failed (RECONCILE_REQUIRED) is never revived: the item stays HUMAN with the
        matching reason code (r1 R7).
        """

        plan_revision = sha256_hex(["intake.auto-plan.v1", record.classification_revision_hash, workspace_fingerprint])
        existing: IntakePlan | None = self.state.get_intake_plan(plan_revision)
        if existing is not None:
            if (
                existing.plan_authority != "AUTO_CLASSIFICATION"
                or existing.classification_revision_hash != record.classification_revision_hash
                or existing.intake_id != item.intake_id
            ):
                return None, BLOCK_PLAN_CONFLICT
            if existing.status in {"AUTO_PENDING", "PLANNED"}:
                return existing, None
            return None, {"SUPERSEDED": BLOCK_PLAN_SUPERSEDED}.get(existing.status, BLOCK_PLAN_RECONCILE)
        target = {
            "intake_id": item.intake_id,
            "course_key": outcome.course_key,
            "kind": outcome.kind.value if outcome.kind else None,
            "actual_date": None if outcome.recorded_date is None else outcome.recorded_date.isoformat(),
            "week": outcome.week,
            "session_mode": record.session_mode,
            "session_id": record.session_id,
            "material_type_initial": outcome.material_type_initial,
            "handling": None if outcome.handling is None else outcome.handling.value,
            "classification_revision_hash": record.classification_revision_hash,
            "workspace_fingerprint": workspace_fingerprint,
        }
        created: IntakePlan = self.state.create_intake_plan(
            intake_id=item.intake_id,
            request_revision_hash=record.classification_revision_hash,
            plan_revision=plan_revision,
            resolved_workspace_fingerprint=workspace_fingerprint,
            target_snapshot_json=target,
            plan_hash=sha256_hex(["intake.auto-plan-record.v1", plan_revision, target]),
            plan_authority="AUTO_CLASSIFICATION",
            classification_revision_hash=record.classification_revision_hash,
        )
        return created, None

    def _blank_user_hash(self, request: RequestInput) -> str:
        return normalized_user_hash(RequestInput(request.request_key, request.request_type, intake_ids=tuple(request.intake_ids)))

    def _auto_request_snapshot(self, page: Mapping[str, Any], receipt: RequestReceipt, intake_id: str) -> str:
        """Plan §3.4 (c): exact USER values + strict checkboxes + status + the page's own
        identity fields (Request Key, Revision Hash, claim-time SYSTEM fields) + receipt key
        + generation (r2 R4: identity drift on the provider page changes the snapshot)."""

        generation = self.state.get_request_generation(receipt.request_key, intake_id) if hasattr(self.state, "get_request_generation") else None
        return sha256_hex([
            "intake.auto-snapshot.v2",
            [page.get(field) for field in ("Course", "Kind", "Actual Date", "Session", "Session Mode", "Session No", "Material Role")],
            page.get("Submitted") if type(page.get("Submitted")) is bool else "INVALID",
            page.get("Cancelled") if type(page.get("Cancelled")) is bool else "INVALID",
            page.get("Request Status"),
            page.get("Request Key"),
            sorted(str(v) for v in _relation_ids(page.get("Intake Items"))),
            page.get("Request Revision Hash"),
            page.get("Input Hash") or None,
            page.get("Plan Revision") or None,
            receipt.request_key,
            None if generation is None else generation.get("generation"),
        ])

    def _snapshot_as_draft(self, page: Mapping[str, Any], receipt: RequestReceipt, intake_id: str) -> str:
        """The AUTO snapshot of a readback with only the system status normalised to Draft,
        so a terminal readback can be compared exactly with the pre-close snapshot (r1 R4)."""

        return self._auto_request_snapshot({**dict(page), "Request Status": "Draft"}, receipt, intake_id)

    @staticmethod
    def _closure_identity_ok(page: Mapping[str, Any], receipt: RequestReceipt) -> bool:
        """The provider page is still the receipt's request and was never claimed (r2 R4)."""

        return (
            page.get("Request Key") == receipt.request_key
            and page.get("Request Revision Hash") == receipt.request_revision_hash
            and not page.get("Input Hash") and not page.get("Plan Revision")
        )

    def _closure_bound(self, page: Mapping[str, Any], receipt: RequestReceipt, workspace: ResolvedSemesterWorkspace) -> bool:
        """Identity plus the exact Intake Items binding of the persisted receipt (r3 #3)."""

        if not self._closure_identity_ok(page, receipt):
            return False
        try:
            bound = json.loads(receipt.intake_ids_json or "null")
        except ValueError:
            return False
        if not isinstance(bound, list):
            return False
        return sorted(self._request_from_page(page, workspace).intake_ids) == sorted(str(v) for v in bound)

    def _human_receipts_for_intake(self, intake_id: str) -> list[tuple[RequestReceipt, str]]:
        """Every receipt bound to this intake with its binding shape: 'single', 'multi' or
        'unknown' (unparsable).  Nothing bound to the intake is left out (r2 R2)."""

        result: list[tuple[RequestReceipt, str]] = []
        for receipt in self.state.list_request_receipts(provider=self.provider):
            try:
                bound = json.loads(receipt.intake_ids_json or "null")
            except ValueError:
                result.append((receipt, "unknown"))
                continue
            if not isinstance(bound, list):
                result.append((receipt, "unknown"))
            elif bound == [intake_id]:
                result.append((receipt, "single"))
            elif intake_id in bound:
                result.append((receipt, "multi"))
        return result

    def _judge_human_requests(
        self, item: IntakeItem, workspace: ResolvedSemesterWorkspace, *, retrying: str | None = None
    ) -> tuple[str, str, list[tuple[RequestReceipt, Mapping[str, Any]]]]:
        """Read back and judge *every* HUMAN request of the intake (plan §3.4 M2, §3.5).

        Returns ``("UNTOUCHED", "", drafts)`` when the only live requests are untouched
        single-intake Drafts of the auto-resolvable kinds; ``("HUMAN", reason, [])`` when a
        human acted anywhere (Applied, Cancelled, Submitted, Claimed, another request kind
        or a changed Draft); ``("BARRIER", key, [])`` while another closure intent is in
        flight; ``("RECONCILE", message, [])`` when a binding or page cannot be verified.
        ``retrying`` names the request whose own PENDING intent is being settled.
        """

        if self.notion is None:
            return "RECONCILE", "Notion worker port is not configured", []
        drafts: list[tuple[RequestReceipt, Mapping[str, Any]]] = []
        for receipt, binding in self._human_receipts_for_intake(item.intake_id):
            if receipt.state == "AutoResolved":
                done = self.state.get_auto_resolve_intent(receipt.request_key)
                if (
                    binding != "single" or not receipt.provider_page_id or done is None
                    or done.state != "DONE" or not done.terminal_snapshot_hash
                ):
                    return "RECONCILE", f"closed request {receipt.request_key} has no verifiable terminal snapshot", []
                closed = self.notion.read_record("input_request", receipt.provider_page_id)
                if closed is None:
                    return "RECONCILE", f"closed request page {receipt.request_key} is unavailable", []
                if self._auto_request_snapshot(closed, receipt, item.intake_id) != done.terminal_snapshot_hash:
                    return "HUMAN", "HUMAN_TERMINAL_CHANGED", []
                continue
            if binding != "single" or not receipt.provider_page_id:
                return "RECONCILE", f"request {receipt.request_key} binding or creation is unresolved", []
            if receipt.state in {"Applied", "Cancelled"}:
                return "HUMAN", f"HUMAN_REQUEST_{receipt.state.upper()}", []
            if receipt.state in {"Submitted", "Claimed"}:
                return "HUMAN", "HUMAN_REQUEST_ACTIVE", []
            if receipt.state != "Draft" or receipt.request_type not in {RequestType.ASSIGN_COURSE.value, RequestType.FILE_DETAILS.value}:
                return "HUMAN", "HUMAN_REQUEST_OTHER", []
            intent = self.state.get_auto_resolve_intent(receipt.request_key)
            if intent is not None and receipt.request_key != retrying:
                if intent.state in {"PENDING", "RECONCILE"}:
                    return "BARRIER", receipt.request_key, []
                if intent.state == "DONE":
                    continue
            page = self.notion.read_record("input_request", receipt.provider_page_id)
            if page is None:
                return "RECONCILE", f"Draft page {receipt.request_key} is unavailable", []
            if self._human_touched(page, receipt, workspace):
                return "HUMAN", "HUMAN_DRAFT_CHANGED", []
            if page.get("Result Reference"):
                # Not ours (ours is handled by recovery before this point): reconcile, never overwrite.
                return "RECONCILE", f"Draft {receipt.request_key} carries a foreign Result Reference", []
            drafts.append((receipt, page))
        return "UNTOUCHED", "", drafts

    def _supersede_auto_plan(self, item: IntakeItem, plan: IntakePlan, reason: str) -> None:
        self.state.supersede_intake_plan(plan.plan_revision, reason)
        self.state.update_intake_item(item.intake_id, classification_state="HUMAN")
        self.state.record_intake_stage_event("auto_plan_superseded", intake_id=item.intake_id, operation_key=reason)

    def _supersede_auto_plans_for_record(self, item: IntakeItem, record: ClassificationRecord, reason: str) -> None:
        for plan_row in self.state.list_intake_plans(plan_authority="AUTO_CLASSIFICATION"):
            if plan_row["classification_revision_hash"] == record.classification_revision_hash:
                self.state.supersede_intake_plan(plan_row["plan_revision"], reason)
        self.state.update_intake_item(item.intake_id, classification_state="HUMAN")
        self.state.record_intake_stage_event("auto_plan_superseded", intake_id=item.intake_id, operation_key=reason)

    def _human_touched(self, page: Mapping[str, Any], receipt: RequestReceipt, workspace: ResolvedSemesterWorkspace) -> bool:
        """Plan §3.5 conditions 1–3 on a Draft readback: anything else is a human change."""

        request = self._request_from_page(page, workspace)
        return (
            page.get("Request Status") != "Draft"
            or page.get("Submitted") is not False
            or page.get("Cancelled") is not False
            or not self._closure_bound(page, receipt, workspace)
            or normalized_user_hash(request) != self._blank_user_hash(request)
        )

    def _auto_preflight(
        self, item: IntakeItem, plan: IntakePlan, record: ClassificationRecord, workspace: ResolvedSemesterWorkspace
    ) -> bool:
        """Plan §3.5 / §3.4 M3 preflight before the first AUTO provider mutation (r1 R3).

        Live source identity, ownership and privacy, exact parent, source hash/version,
        the re-downloaded bytes, workspace/config fingerprints and the plan↔record↔item
        binding must all match the classification record.  Any mismatch parks the plan
        as RECONCILE_REQUIRED with zero external writes.  (The HUMAN requests are judged
        separately, immediately before each write: ``_judge_human_requests``.)
        """

        reason: str | None = None
        current = self._require_item(item.intake_id)
        try:
            metadata = self.drive.read_metadata(item.provider_file_id)
            if metadata.file_id != item.provider_file_id or metadata.owned_by_me is not True:
                reason = "SOURCE_IDENTITY"
            else:
                _check_private_drive_metadata(metadata)
                try:
                    decision = json.loads(record.decision_json or "{}")
                except ValueError:
                    decision = {}
                if metadata.parent_id != current.observed_parent_id:
                    reason = "SOURCE_PARENT"
                elif metadata.name != decision.get("source_name") or metadata.mime_type != decision.get("source_mime"):
                    reason = "SOURCE_METADATA"
                elif _metadata_source_hash(metadata) != current.source_hash or current.source_version != record.source_version:
                    reason = "SOURCE_VERSION"
                elif metadata.md5_checksum and record.byte_md5 and metadata.md5_checksum != record.byte_md5:
                    reason = "SOURCE_BYTES"
            if reason is None:
                probe = self._source_probe(current)
                if not probe.complete or probe.byte_sha256 != record.byte_sha256:
                    reason = "SOURCE_BYTES"
        except ReconnectRequiredError:
            raise
        except (SourceUnavailableError, SourcePartialError, PolicyDeniedError, ProviderUnavailableError):
            reason = "SOURCE_UNAVAILABLE"
        if reason is None:
            reason = self._provenance_mismatch(item, record, workspace) or self._eligibility_mismatch(item, record, workspace)
        if reason is None:
            if (
                self._semester_workspace_fingerprint(workspace.semester) != record.workspace_fingerprint
                or self._semester_config_fingerprint(workspace.semester) != record.config_fingerprint
            ):
                reason = "FINGERPRINT"
            elif (
                plan.classification_revision_hash != record.classification_revision_hash
                or plan.intake_id != item.intake_id
                or current.classification_record_id != record.record_id
                or current.classification_state != "CLASSIFIED"
            ):
                reason = "PLAN_BINDING"
            else:
                live = self.state.get_intake_plan(plan.plan_revision)
                if live is None or live.status != "AUTO_PENDING":
                    reason = "PLAN_STATUS"
        if reason is None:
            return True
        self.state.reconcile_intake_plan(plan.plan_revision, f"PREFLIGHT_{reason}")
        self.state.update_intake_item(
            item.intake_id, status=IntakeStatus.RECONCILE_REQUIRED.value, classification_state="HUMAN",
            last_error_code="RECONCILE_REQUIRED", last_error=f"AUTO preflight failed: {reason}",
        )
        self.state.record_intake_stage_event("auto_preflight_failed", intake_id=item.intake_id, operation_key=reason)
        return False

    def _eligibility_mismatch(
        self, item: IntakeItem, record: ClassificationRecord, workspace: ResolvedSemesterWorkspace
    ) -> str | None:
        """Plan §2.3: the calendar projection, the effective semester range and the Sessions
        inventory the record was decided on are still the current ones (r4 #2)."""

        semester = self._semester_range(workspace.semester)
        if record.course_key:
            calendar = self._course_calendar(record.course_key, semester)
            if (None if calendar is None else calendar.revision_hash()) != record.calendar_projection_revision_hash:
                return "CALENDAR"
        current_basis = None if semester is None else semester.basis()
        stored = None if record.semester_range_basis_json is None else json.loads(record.semester_range_basis_json)
        if json.dumps(current_basis, sort_keys=True, default=str) != json.dumps(stored, sort_keys=True, default=str):
            return "SEMESTER_RANGE"
        if record.session_mode is not None:
            recorded = None if record.decided_date is None else date.fromisoformat(record.decided_date)
            mode, session_id, digest, block = self._session_binding(item, record.course_key, recorded, workspace)
            if block is not None or (mode, session_id, digest) != (
                record.session_mode, record.session_id, record.sessions_inventory_hash
            ):
                return "SESSION_INVENTORY"
        return None

    def _alias_inventory_hash(self, semester: str) -> str:
        index = self._course_alias_index(semester)
        return sha256_hex(["intake.alias-inventory.v1", sorted(index.resolved.items()), sorted(index.disabled)])

    @staticmethod
    def _canvas_basis(binding: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("drive_file_id", "canvas_course_id", "resource_kind", "resource_id", "observation_revision",
                "attachment_id", "byte_sha256")
        return {key: binding.get(key) for key in keys}

    def _provenance_mismatch(
        self, item: IntakeItem, record: ClassificationRecord, workspace: ResolvedSemesterWorkspace
    ) -> str | None:
        """Plan §3.4 (d): the course and origin evidence the record was built on is still
        current — the alias inventory, and for a PROFESSOR_SOURCE the exact persisted Canvas
        binding (file id, resource/revision, attachment, byte hash).  Nothing is re-derived."""

        try:
            basis = json.loads(record.course_basis_json or "{}")
        except ValueError:
            return "COURSE_BASIS"
        if not isinstance(basis, dict):
            return "COURSE_BASIS"
        if basis.get("alias_inventory_hash") != self._alias_inventory_hash(workspace.semester):
            return "ALIAS_BASIS"
        if record.origin == Origin.PROFESSOR_SOURCE.value:
            binding = self.state.get_canvas_drive_binding(item.provider_file_id)
            if binding is None or basis.get("canvas_binding") != self._canvas_basis(binding):
                return "CANVAS_BINDING"
            if binding.get("byte_sha256") != record.byte_sha256:
                return "CANVAS_BINDING"
            if self._canvas_course_mismatch(item, record):
                return "CANVAS_COURSE"
        elif basis.get("canvas_binding") is not None:
            return "CANVAS_BINDING"
        return None

    def _apply_human_verdict(
        self, item: IntakeItem, record: ClassificationRecord, workspace: ResolvedSemesterWorkspace,
        verdict: str, reason: str,
    ) -> None:
        if verdict == "HUMAN":
            self._supersede_auto_plans_for_record(item, record, reason)
        elif verdict == "RECONCILE":
            self._record_item_error(item, workspace, IntakeReconcileRequired(reason),
                                    default_status=IntakeStatus.NEEDS_INPUT)

    def _close_blank_drafts(
        self, item: IntakeItem, plan: IntakePlan, record: ClassificationRecord, workspace: ResolvedSemesterWorkspace
    ) -> None:
        """Plan §3.5: close the untouched pre-v2 Drafts of this intake as Auto Resolved, or step aside.

        Every HUMAN request bound to the intake is read back and judged before any write
        (r1 R2, r2 R2): one human action anywhere (Applied/Cancelled/Submitted/Claimed,
        another request kind, a changed Draft) supersedes the AUTO plan and nothing is
        closed.  The first write is preceded by the source/plan preflight, and the HUMAN
        requests are judged *again* immediately before each write (r2 R3).  Each closure
        then runs durable PENDING intent (+pre-close snapshot) → write → readback →
        atomic receipt+intent commit.
        """

        if self.notion is None:
            return
        verdict, reason, drafts = self._judge_human_requests(item, workspace)
        if verdict != "UNTOUCHED":
            self._apply_human_verdict(item, record, workspace, verdict, reason)
            return
        if not drafts:
            return
        for receipt, _draft_page in drafts:
            fresh = self._closure_gate(item, plan, record, workspace, receipt)
            if fresh is None:
                return
            snapshot = self._auto_request_snapshot(fresh, receipt, item.intake_id)
            intent = self.state.create_auto_resolve_intent(
                record_id=record.record_id, request_key=receipt.request_key,
                expected_user_snapshot_hash=snapshot, pre_close_snapshot_hash=snapshot,
            )
            if not self._write_auto_resolved(item, receipt, record, intent, workspace, snapshot):
                return  # the barrier stands; recovery settles it before any further closure

    def _closure_gate(
        self, item: IntakeItem, plan: IntakePlan, record: ClassificationRecord,
        workspace: ResolvedSemesterWorkspace, receipt: RequestReceipt, *, retrying: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Everything that must hold immediately before one closure write (r3 #1): the
        source/provenance preflight, then — as the very last step — a fresh judgement of
        every HUMAN request of the intake.  Returns the target's fresh Draft page, or None
        after the matching supersede/reconcile transition (zero writes)."""

        if not self._auto_preflight(item, plan, record, workspace):
            return None
        verdict, reason, drafts = self._judge_human_requests(item, workspace, retrying=retrying)
        if verdict != "UNTOUCHED":
            self._apply_human_verdict(item, record, workspace, verdict, reason)
            return None
        fresh = next((page for other, page in drafts if other.request_key == receipt.request_key), None)
        if fresh is None:
            self._supersede_auto_plans_for_record(item, record, "HUMAN_DRAFT_CHANGED")
        return fresh

    def _write_auto_resolved(
        self, item: IntakeItem, receipt: RequestReceipt, record: ClassificationRecord,
        intent: AutoResolveIntent, workspace: ResolvedSemesterWorkspace, expected_snapshot: str,
    ) -> bool:
        """Write Auto Resolved + Result Reference, read back, and commit receipt+intent DONE
        atomically.  Returns True only for a verified closure (r1 R4, r2 R4): the readback
        must still carry the receipt's identity (Request Key, Revision Hash, never claimed),
        equal the pre-close snapshot except for the status, carry exactly this record as
        Result Reference and keep both checkboxes false.  A landed write the human overtook
        goes to recovery (iii); an unknown or identity-changed readback keeps the barrier."""

        assert self.notion is not None
        page_id = receipt.provider_page_id or ""
        patch = {"Request Status": AUTO_RESOLVED_STATUS, "Result Reference": record.record_id}
        op_key = derive_operation_key("intake.auto-resolve.v1", self.provider, receipt.request_key, record.record_id)
        prior = self.state.get_provider_write_attempt(op_key)
        if prior is None:
            self.state.record_provider_write_attempt(
                operation=INTAKE_REQUEST_SYNC_OPERATION, operation_key=op_key, provider=self.provider,
                target_id=page_id, response_state="PREPARED",
            )
        if prior is None or prior.response_state != "READBACK_OK":
            try:
                self.notion.update_system_record("input_request", page_id, patch)
            except ReconnectRequiredError:
                raise
            except Exception:
                self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN", error_class="PROVIDER_UNAVAILABLE")
                raise
            self.state.update_provider_write_attempt(op_key, response_state="DISPATCHED", dispatched_at=_utc_now())
        readback = self.notion.read_record("input_request", page_id)
        if readback is None or not _properties_match(readback, patch):
            self.state.update_provider_write_attempt(op_key, response_state="UNKNOWN")
            self._mark_auto_close_reconcile(item, "Auto Resolved readback is missing or mismatched")
            return False
        self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=readback)
        if not self._closure_bound(readback, receipt, workspace):
            # The page is no longer (only) this request: never DONE, never rolled back
            # blindly; the barrier stays until a human reconciles.
            self._mark_reconcile(item, "closure page identity changed after the write")
            return False
        if self._snapshot_as_draft(readback, receipt, item.intake_id) != expected_snapshot:
            # The human raced the closure (USER field, Submitted or Cancelled): recovery
            # (iii) rolls the status back and supersedes the plan.
            self._recover_auto_resolve_intents()
            return False
        terminal = self._auto_request_snapshot(readback, receipt, item.intake_id)
        self.state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash=terminal)
        self._clear_auto_close_reconcile(item)
        self.state.record_intake_stage_event("draft_auto_resolved", intake_id=item.intake_id, operation_key=op_key)
        return True

    def _mark_reconcile(self, item: IntakeItem, message: str) -> None:
        self.state.update_intake_item(item.intake_id, status=IntakeStatus.RECONCILE_REQUIRED.value,
                                      last_error_code="RECONCILE_REQUIRED", last_error=message)

    def _mark_auto_close_reconcile(self, item: IntakeItem, message: str) -> None:
        """A reconcile state owned by the closure itself (unknown readback): cleared again
        once the intent settles (r2 R6); never confused with a source/plan reconcile."""

        self.state.update_intake_item(item.intake_id, status=IntakeStatus.RECONCILE_REQUIRED.value,
                                      last_error_code=AUTO_CLOSE_RECONCILE_CODE, last_error=message)

    def _clear_auto_close_reconcile(self, item: IntakeItem) -> None:
        current = self.state.get_intake_item(item.intake_id)
        if (
            current is not None
            and current.status == IntakeStatus.RECONCILE_REQUIRED.value
            and current.last_error_code == AUTO_CLOSE_RECONCILE_CODE
        ):
            self.state.update_intake_item(item.intake_id, status=IntakeStatus.NEEDS_INPUT.value,
                                          last_error_code=None, last_error=None)

    def _recover_auto_resolve_intents(self) -> None:
        """Plan §3.5 (i)–(iv): settle every pending closure from the provider readback.

        Runs at every tick start whenever Notion is reachable, independent of the AUTO
        feature gate (r1 R9): an intent that already started must be settled or kept
        barred even after the feature was switched off.  Only the *retry* of a closure
        whose write never landed needs AUTO to be enabled and every HUMAN request of the
        intake still untouched (r2 R3); otherwise that intent is ABORTED with zero writes
        and the HUMAN path resumes.
        """

        if self.notion is None:
            return
        for intent in self.state.list_auto_resolve_intents():
            receipt = self.state.get_request_receipt(intent.request_key)
            record = self.state.get_classification_record(intent.record_id)
            if receipt is None or receipt.provider_page_id is None or record is None:
                continue  # reconciliation stays explicit; the barrier remains
            item = self.state.get_intake_item(record.intake_id)
            if item is None:
                continue
            if receipt.state != "Draft":
                # receipt AutoResolved + intent PENDING cannot exist (atomic commit); any
                # other state means the binding broke: invariant violation, barrier kept.
                self._mark_reconcile(item, f"auto resolve intent bound to a {receipt.state} receipt")
                continue
            workspace = self._workspace_for_item(item)
            page = self.notion.read_record("input_request", receipt.provider_page_id)
            if page is None:
                self._mark_auto_close_reconcile(item, "Auto Resolved readback is unavailable")
                continue  # (iv) stays PENDING
            if not self._closure_bound(page, receipt, workspace):
                self._mark_reconcile(item, "closure page identity or Intake Items binding changed")
                continue  # never DONE, never overwritten: barrier until a human reconciles
            rollback = self.state.get_auto_resolve_rollback(intent.intent_id)
            if rollback is not None:
                self._settle_rollback(item, record, receipt, intent, rollback, page)
                continue
            status = page.get("Request Status")
            pre_close = intent.pre_close_snapshot_hash
            untouched = pre_close is not None and self._snapshot_as_draft(page, receipt, item.intake_id) == pre_close
            if status == AUTO_RESOLVED_STATUS:
                if untouched and page.get("Result Reference") == record.record_id:
                    terminal = self._auto_request_snapshot(page, receipt, item.intake_id)  # (i)
                    self.state.transition_auto_resolve_intent(intent.intent_id, "DONE", terminal_snapshot_hash=terminal)
                    self._clear_auto_close_reconcile(item)
                    continue
                # (iii): the write landed and the human changed the page meanwhile.
                self._supersede_auto_plans_for_record(item, record, "HUMAN_DRAFT_CHANGED")
                target = "Submitted" if page.get("Submitted") is True else "Draft"
                rollback = self.state.create_auto_resolve_rollback(intent.intent_id, target)
                self._settle_rollback(item, record, receipt, intent, rollback, page)
                continue
            if page.get("Result Reference") == record.record_id:
                # Our reference landed but the status moved on (human or partial write):
                # clear only our reference through the rollback intent (r2 R5).
                self._supersede_auto_plans_for_record(item, record, "HUMAN_DRAFT_CHANGED")
                if status not in ("Draft", "Submitted"):
                    self._mark_reconcile(item, f"Auto Resolved reference left on a {status!r} request")
                    continue
                rollback = self.state.create_auto_resolve_rollback(intent.intent_id, str(status))
                self._settle_rollback(item, record, receipt, intent, rollback, page)
                continue
            if status == "Draft" and untouched:
                # The write never landed.  Retry under the same intent and operation key
                # only when AUTO may still write and every HUMAN request of the intake is
                # still untouched; otherwise release the human path (r1 R5, r2 R3).
                plan = self._live_auto_plan_for_record(record)
                fresh = (
                    self._closure_gate(item, plan, record, workspace, receipt, retrying=receipt.request_key)
                    if self._auto_enabled() and plan is not None else None
                )
                if fresh is not None and self._auto_request_snapshot(fresh, receipt, item.intake_id) == pre_close:
                    self._write_auto_resolved(item, receipt, record, intent, workspace, pre_close or "")
                    continue
                self.state.transition_auto_resolve_intent(intent.intent_id, "ABORTED")
                self._supersede_auto_plans_for_record(item, record, "AUTO_CLOSURE_ABORTED")
                self._clear_auto_close_reconcile(item)
                continue
            # (ii): the human changed the Draft (or moved it on) before any write landed.
            self.state.transition_auto_resolve_intent(intent.intent_id, "ABORTED")
            self._supersede_auto_plans_for_record(item, record, "HUMAN_DRAFT_CHANGED")
            self._clear_auto_close_reconcile(item)

    def _live_auto_plan_for_record(self, record: ClassificationRecord) -> IntakePlan | None:
        for plan_row in self.state.list_intake_plans(plan_authority="AUTO_CLASSIFICATION", status="AUTO_PENDING"):
            if plan_row["classification_revision_hash"] == record.classification_revision_hash:
                plan: IntakePlan | None = self.state.get_intake_plan(plan_row["plan_revision"])
                return plan
        return None

    def _settle_rollback(
        self, item: IntakeItem, record: ClassificationRecord, receipt: RequestReceipt,
        intent: AutoResolveIntent, rollback: Mapping[str, Any], page: Mapping[str, Any],
    ) -> None:
        """Drive a §3.5 (iii) rollback to completion from the *current* readback (r1 R6, r2 R5).

        Only a page still showing ``Auto Resolved`` is rewritten to the recorded target;
        a page whose status a human already moved on keeps that status and only our own
        Result Reference is cleared.  The rollback is DONE (and the intent ABORTED, in one
        transaction) only when the readback shows a non-Auto-Resolved status and an empty
        Result Reference; anything else keeps the barrier.
        """

        assert self.notion is not None
        # The target follows the live Submitted checkbox, not the possibly stale persisted
        # target, so a request the human submitted meanwhile is never turned back (r3 #2).
        target = "Submitted" if page.get("Submitted") is True else "Draft"
        self._supersede_auto_plans_for_record(item, record, "HUMAN_DRAFT_CHANGED")
        if rollback["state"] == "DONE":
            self._clear_auto_close_reconcile(item)
            return
        page_id = receipt.provider_page_id or ""
        workspace = self._workspace_for_item(item)
        status, reference = page.get("Request Status"), page.get("Result Reference")
        confirm: Mapping[str, Any] | None = page
        if status == AUTO_RESOLVED_STATUS:
            if reference != record.record_id:
                # Without proof that the reference is ours nothing is written (r3 #2).
                self._mark_reconcile(item, f"Auto Resolved page carries a foreign reference {reference!r}")
                return
            self.notion.update_system_record("input_request", page_id, {"Request Status": target, "Result Reference": None})
            confirm = self.notion.read_record("input_request", page_id)
        elif reference == record.record_id and status in ("Draft", "Submitted"):
            self.notion.update_system_record("input_request", page_id, {"Result Reference": None})
            confirm = self.notion.read_record("input_request", page_id)
        elif reference or status not in ("Draft", "Submitted"):
            self._mark_reconcile(item, f"Auto Resolved rollback found status {status!r} with reference {reference!r}")
            return
        for _ in range(2):
            if confirm is None or not self._closure_bound(confirm, receipt, workspace) or confirm.get("Result Reference"):
                self._mark_reconcile(item, "Auto Resolved rollback readback failed")
                return
            expected = "Submitted" if confirm.get("Submitted") is True else "Draft"
            if confirm.get("Request Status") == expected:
                break
            if confirm.get("Request Status") not in ("Draft", "Submitted"):
                self._mark_reconcile(item, "Auto Resolved rollback readback shows an unexpected status")
                return
            # The human flipped Submitted while the rollback was in flight: align only the
            # system-owned status projection with the live checkbox (never a USER field).
            self.notion.update_system_record("input_request", page_id, {"Request Status": expected})
            confirm = self.notion.read_record("input_request", page_id)
        else:
            self._mark_reconcile(item, "Auto Resolved rollback did not settle")
            return
        self.state.complete_auto_resolve_rollback(intent.intent_id)
        self._clear_auto_close_reconcile(item)
        self.state.record_intake_stage_event("auto_resolve_rolled_back", intake_id=item.intake_id, operation_key=target)

    def _suggestion_properties(self, item: IntakeItem) -> dict[str, Any]:
        """§3.3 suggestion fields for a new draft; only under a verified v2 profile."""

        if not self._classification_enabled() or not self._classification_profile_active():
            return {}
        suggestion = self.state.get_intake_suggestion(item.intake_id)
        if not suggestion:
            return {}
        props: dict[str, Any] = {}
        if suggestion.get("suggested_course_key"):
            props["Suggested Course"] = suggestion["suggested_course_key"]
        if suggestion.get("suggested_kind") in AI_KIND_OPTIONS:
            props["Suggested Kind"] = suggestion["suggested_kind"]
        if suggestion.get("suggested_date"):
            props["Suggested Date"] = suggestion["suggested_date"]
        if isinstance(suggestion.get("suggested_week"), int):
            props["Suggested Week"] = suggestion["suggested_week"]
        if suggestion.get("suggestion_source"):
            props["Suggestion Source"] = suggestion["suggestion_source"]
        if suggestion.get("suggestion_note"):
            props["Suggestion Note"] = suggestion["suggestion_note"]
        return props

    def _human_handling_errors(self, request: RequestInput) -> tuple[str, ...]:
        """Reject a HUMAN Kind×format combination outside the §6.1 matrix before any write."""

        if not self._classification_profile_active() or len(request.intake_ids) != 1:
            return ()
        item = self.state.get_intake_item(request.intake_ids[0])
        if item is None or not request.kind:
            return ()
        kind = Kind.LECTURE_SLIDES if request.kind == FileKind.MATERIAL_PDF.value else Kind(request.kind)
        probe = self._source_probe(item)
        extension = item.original_name.rsplit(".", 1)[1].lower() if "." in item.original_name else None
        if probe.payload is None:
            return (NOTE_FORMAT_KIND_MISMATCH + (": source too large" if probe.too_large else ": source unavailable"),)
        mode = handling_mode(kind, mime_type=item.mime_type, extension=extension,
                             head=probe.payload, payload_complete=probe.complete)
        if mode not in (HandlingMode.NORMALIZE, HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL):
            return (NOTE_FORMAT_KIND_MISMATCH,)
        return ()

    def _human_material_kinds(self) -> frozenset[str]:
        """HUMAN-selectable v2 Material Kinds under the active profile (plan §5)."""

        from uls.intake.classification.taxonomy import FILE_KINDS_V2

        if not self._classification_profile_active():
            return frozenset()
        return frozenset(k for k in FILE_KINDS_V2 if k not in (FileKind.TRANSCRIPT.value, FileKind.MATERIAL_PDF.value))

    def _classification_profile_active(self) -> bool:
        profile = getattr(self.notion, "schema_profile", "legacy5") or "legacy5"
        return profile in ("legacy5-cls", "c5-range-v2")

    def backfill_material_ai_kind(self) -> dict[str, int]:
        """Limited, idempotent Materials ``AI Kind`` backfill of plan §5 (P-A migration).

        Only ``Type == "Lecture Slides"`` rows with an empty ``AI Kind`` receive
        ``LECTURE_SLIDES``; every other Type keeps ``AI Kind`` empty until a verified
        Kind exists.  ``Type``, source binding and canonical ID are never touched, and
        nothing runs unless the v2 profile readback is VERIFIED.  A second run is 0.
        """

        if self.notion is None or not self._classification_profile_active():
            raise SourceUnavailableError("Materials AI Kind backfill needs a verified v2 classification profile")
        self._require_notion_workspace_verified()
        rows = {_page_id(row): row for row in self.notion.list_records("materials") if _page_id(row)}
        # The target set was fixed when the v2 profile first verified (r8 R4, r10 R5).
        targets = self.state.material_backfill_targets() or []
        scanned = updated = 0
        for target in targets:
            row = rows.get(target["page_id"])
            if row is None:
                continue
            scanned += 1
            if target["applied_at"] or target.get("material_type") != "Lecture Slides":
                continue
            if row.get("Type") != "Lecture Slides":
                continue
            if row.get("AI Kind") == "LECTURE_SLIDES":
                # The remote write landed earlier but the local mark was lost: the readback
                # proves the exact value, so only the ledger is completed (r13 O1).
                self.state.mark_material_backfill_applied(target["page_id"])
                continue
            if row.get("AI Kind"):
                continue
            self.notion.update_system_record("materials", target["page_id"], {"AI Kind": "LECTURE_SLIDES"})
            self.state.mark_material_backfill_applied(target["page_id"])
            updated += 1
        return {"scanned": scanned, "updated": updated}

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
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
            candidate = matches[0]
            try:
                result = self.drive.read_metadata(candidate.file_id)
                self._check_derivative_metadata(
                    result, candidate.file_id, parent_id, marker
                )
                if self.drive.download(candidate.file_id) != content:
                    raise IntakeReconcileRequired(
                        "existing derivative content does not match immutable tuple"
                    )
            except ReconnectRequiredError:
                raise  # a rejected OAuth refresh is never converted or recorded as another failure
            except Exception:  # noqa: BLE001 - a selected marker candidate must fail closed
                raise IntakeReconcileRequired(
                    "existing derivative marker candidate could not be verified"
                ) from None
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
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
        except Exception:  # noqa: BLE001 - the dispatched create outcome is indeterminate
            self.state.update_provider_write_attempt(
                op_key, response_state="UNKNOWN", error_class="AMBIGUOUS"
            )
            raise IntakeReconcileRequired(
                "derivative create outcome is indeterminate after dispatch"
            ) from None
        try:
            file_id = result.file_id
            self.state.update_provider_write_attempt(
                op_key,
                target_id=file_id,
                dispatched_at=_utc_now(),
                response_state="DISPATCHED",
            )
            readback = self.drive.read_metadata(file_id)
            self._check_derivative_metadata(readback, file_id, parent_id, marker)
            if self.drive.download(file_id) != content:
                raise IntakeReconcileRequired("staged derivative bytes differ from intent")
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
        except Exception:  # noqa: BLE001 - post-create verification must fail closed
            self.state.update_provider_write_attempt(
                op_key, response_state="UNKNOWN", error_class="AMBIGUOUS"
            )
            raise IntakeReconcileRequired(
                "staged derivative create readback is indeterminate"
            ) from None
        self.state.update_provider_write_attempt(op_key, response_state="READBACK_OK", readback_json=asdict(readback))
        self.state.record_intake_stage_event("stage_validate_readback", intake_id=item.intake_id, operation_key=op_key)
        return readback

    @staticmethod
    def _check_derivative_metadata(
        metadata: DriveMetadata,
        file_id: str,
        parent_id: str,
        marker: Mapping[str, str],
    ) -> None:
        if (
            metadata.file_id != file_id
            or metadata.trashed
            or metadata.mime_type != "text/markdown"
            or metadata.parents != (parent_id,)
            or metadata.app_properties != dict(marker)
        ):
            raise IntakeReconcileRequired(
                "Drive derivative identity, parent, MIME, marker, or trash state mismatch"
            )
        require_private_ownership(metadata, context="Drive derivative")

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
                except ReconnectRequiredError:
                    raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
            except ReconnectRequiredError:
                raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
                    except ReconnectRequiredError:
                        raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
        if self._classification_enabled() and self._classification_profile_active():
            # SYSTEM_DERIVED classification projection (plan §5): only while the feature
            # is enabled under a verified v2 profile (r6).
            if item.origin in ORIGIN_OPTIONS:
                props["Origin"] = item.origin
            if item.classified_kind in AI_KIND_OPTIONS:
                props["AI Kind"] = item.classified_kind
            if item.classification_source:
                props["Classification Source"] = item.classification_source
            if item.classification_record_id:
                props["Classification Record"] = item.classification_record_id
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
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
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

    def _context_for_validated_workspaces(
        self, workspaces: Sequence[ResolvedSemesterWorkspace]
    ) -> _LayoutValidationContext:
        fingerprints = tuple(
            sorted(
                (
                    semester,
                    self._workspace_fingerprint(rows),
                )
                for semester, rows in _group_workspace_rows(workspaces).items()
            )
        )
        return _LayoutValidationContext(fingerprints)

    def _fresh_layout_context(
        self, workspaces: Sequence[ResolvedSemesterWorkspace]
    ) -> _LayoutValidationContext:
        grouped = _group_workspace_rows(workspaces)
        for rows in grouped.values():
            validate_registered_drive_layout(self.drive, rows)
        return self._context_for_validated_workspaces(workspaces)

    def _fresh_item_layout_context(
        self, workspace: ResolvedSemesterWorkspace
    ) -> _LayoutValidationContext:
        return self._fresh_layout_context(self._semester_workspaces(workspace.semester))

    def _layout_context_allows(
        self,
        context: _LayoutValidationContext | None,
        workspace: ResolvedSemesterWorkspace,
    ) -> bool:
        return (
            context is not None
            and context.fingerprint_for(workspace.semester)
            == self._semester_workspace_fingerprint(workspace.semester)
        )

    def _run_layout_workspaces(self) -> list[ResolvedSemesterWorkspace]:
        """Resolve the complete local request/job scope without provider reads."""

        rows_by_course = {workspace.course_key: workspace for workspace in self.workspaces}
        items = self.state.list_intake_items(limit=10_000)
        for job in self.state.list_jobs(limit=1000):
            if (
                job.operation not in {INTAKE_SESSION_OPERATION, INTAKE_MATERIAL_OPERATION}
                or str(job.status) != "PENDING"
            ):
                continue
            item = self.state.get_intake_item(job.target_entity_id)
            if item is None:
                raise SourceUnavailableError("pending intake job has no local intake item")
            workspace = self._workspace_for_item(item)
            rows_by_course.update(
                {row.course_key: row for row in self._semester_workspaces(workspace.semester)}
            )

        for receipt in self.state.list_request_receipts(provider=self.provider):
            if receipt.state in {"Applied", "Cancelled", "AutoResolved"}:
                continue
            matches = [
                item
                for item in items
                if item.pending_request_key == receipt.request_key
                or (
                    receipt.provider_page_id is not None
                    and item.input_request_page_id == receipt.provider_page_id
                )
            ]
            if not matches and receipt.intake_ids_json:
                try:
                    intake_ids = json.loads(receipt.intake_ids_json)
                except (TypeError, json.JSONDecodeError):
                    intake_ids = []
                if isinstance(intake_ids, list):
                    matches = [
                        item
                        for intake_id in intake_ids
                        if isinstance(intake_id, str)
                        for item in [self.state.get_intake_item(intake_id)]
                        if item is not None
                    ]
            if not matches:
                raise SourceUnavailableError("nonterminal request receipt has no local intake item")
            for item in matches:
                workspace = self._workspace_for_item(item)
                rows_by_course.update(
                    {row.course_key: row for row in self._semester_workspaces(workspace.semester)}
                )
        return [rows_by_course[key] for key in sorted(rows_by_course)]

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
            plan_revision=plan.plan_revision,
            plan_authority=plan.plan_authority,
        )

    def _require_item(self, intake_id: str) -> IntakeItem:
        item = self.state.get_intake_item(intake_id)
        if item is None:
            raise KeyError(intake_id)
        return item

    def _read_source_bytes(self, item: IntakeItem, *, max_bytes: int | None = None) -> bytes:
        metadata = self.drive.read_metadata(item.provider_file_id)
        if metadata.file_id != item.provider_file_id or metadata.owned_by_me is not True:
            raise PolicyDeniedError("source identity or USER ownership readback failed")
        _check_private_drive_metadata(metadata, require_move=True)
        if item.source_hash.startswith("md5:") and metadata.md5_checksum != item.source_hash[4:]:
            raise SourceUnavailableError("source checksum changed after the immutable observation")
        if item.source_hash.startswith("sha256:metadata-") and _metadata_source_hash(metadata) != item.source_hash:
            raise SourceUnavailableError("source metadata changed after the immutable observation")
        data = (
            self.drive.download(item.provider_file_id) if max_bytes is None
            else self.drive.download(item.provider_file_id, max_bytes=max_bytes)
        )
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
        if self._classification_profile_mismatch:
            # A requested classification profile that does not match the configured
            # data sources fails readiness closed; nothing is written under a
            # silently downgraded profile (P-A review R6).
            return {
                "status": "NOT_VERIFIED",
                "reason": (
                    "intake.classification.schema_profile "
                    f"{self._classification_profile_mismatch!r} does not match the configured "
                    "Notion data sources"
                ),
            }
        validator = getattr(self.notion, "validate_workspace", None)
        if not callable(validator):
            return {"status": "NOT_VERIFIED", "reason": "Notion schema/parent readback is unavailable"}
        try:
            result = validator()
        except ReconnectRequiredError:
            raise  # a rejected OAuth refresh is never converted or recorded as another failure
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
        if self._classification_profile_active():
            # The pre-v2 Material set is fixed the first time a v2 profile verifies, i.e.
            # before any v2 HUMAN write can happen (mutations require this readiness),
            # so later HUMAN MATERIAL_PDF rows are never backfill targets (r10 R5).
            try:
                self._ensure_material_backfill_snapshot()
            except ReconnectRequiredError:
                raise
            except Exception:  # noqa: BLE001 - readiness fails closed
                return {"status": "NOT_VERIFIED", "reason": "pre-v2 Materials snapshot could not be recorded"}
        return dict(result)

    def _ensure_material_backfill_snapshot(self) -> None:
        if self.notion is None or self.state.material_backfill_targets() is not None:
            return
        rows = self.notion.list_records("materials")
        self.state.snapshot_material_backfill_targets(
            (_page_id(row) or "", row.get("Type")) for row in rows
        )

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
        profile: dict[str, Any] = {}
        extra_sources: dict[str, str] = {}
        if row.material_usage_data_source_id and row.automation_queue_data_source_id:
            profile["schema_profile"] = "c5-range-v1"
            extra_sources = {
                "material_usage": row.material_usage_data_source_id,
                "automation_queue": row.automation_queue_data_source_id,
            }
        # Intake classification v2 (plan §5): an explicit profile replaces the
        # default selection only after a human added the Notion properties.
        configured = getattr(getattr(self.config, "intake", None), "classification", None)
        requested = getattr(configured, "schema_profile", "") or ""
        if requested == "c5-range-v2" and extra_sources:
            profile["schema_profile"] = "c5-range-v2"
        elif requested == "legacy5-cls" and not extra_sources:
            profile["schema_profile"] = "legacy5-cls"
        elif requested:
            # A profile that does not match the configured data sources is
            # never silently downgraded; readback stays on the default profile
            # and the mismatch is visible in readiness.
            self._classification_profile_mismatch = requested
        return NotionIntakeWriter(
            notion,
            {
                "academic_courses": row.academic_courses_data_source_id,
                "sessions": row.sessions_data_source_id,
                "materials": row.materials_data_source_id,
                "file_intake": row.file_intake_data_source_id,
                "input_request": row.input_requests_data_source_id,
                **extra_sources,
            },
            parent_page_id=row.connection_settings_files_parent_id,
            semester=row.semester,
            **profile,
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


def _group_workspace_rows(
    workspaces: Sequence[ResolvedSemesterWorkspace],
) -> dict[str, list[ResolvedSemesterWorkspace]]:
    grouped: dict[str, list[ResolvedSemesterWorkspace]] = {}
    for workspace in workspaces:
        grouped.setdefault(workspace.semester, []).append(workspace)
    return {
        semester: sorted(rows, key=lambda workspace: workspace.course_key)
        for semester, rows in grouped.items()
    }


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
