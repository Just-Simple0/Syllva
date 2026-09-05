"""Worker-side enrichment generation and AI-region publication boundary."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from uls.adapters.llm.base import LLMAdapter
from uls.adapters.notion.base import AutomationActor
from uls.domain.enums import ProcessingStatus
from uls.domain.errors import (
    PolicyDeniedError,
    PolicyViolation,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.orchestration.jobs import derive_job_key
from uls.orchestration.retry import ErrorClass, next_backoff, should_retry
from uls.retrieval.chunking import derivative_parts

from ._common import (
    EnrichmentInputError,
    EnrichmentNoEvidenceError,
    EnrichmentPublishConflict,
    EnrichmentSafetyError,
    EnrichmentTerminalizationError,
    coerce_fingerprint,
)
from .material import MaterialEnrichmentGenerator
from .schemas import EnrichmentGenerationResult, EnrichmentRecord, coerce_enrichment
from .session import SessionEnrichmentGenerator
from uls.retrieval._compat import field as _record_field


DEFAULT_PROCESSOR_VERSION = "1.2.0"
_TERMINAL_STATUSES = frozenset(
    {
        ProcessingStatus.READY,
        ProcessingStatus.PARTIAL,
        ProcessingStatus.NEEDS_REVIEW,
        ProcessingStatus.FAILED,
    }
)
_SAFETY_QUARANTINE_MARKER = "ENRICHMENT_SAFETY_QUARANTINED"


@dataclass(frozen=True)
class EnrichmentWriteResult:
    """Result of a worker attempt, including its final job status."""

    status: ProcessingStatus | str
    job: Any | None = None
    generation: EnrichmentGenerationResult | None = None
    record: EnrichmentRecord | None = None
    error_class: ErrorClass | None = None
    error: str | None = None
    published: bool = False
    processing_record: Any | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            object.__setattr__(self, "status", ProcessingStatus(self.status.upper()))
        if isinstance(self.error_class, str):
            object.__setattr__(self, "error_class", ErrorClass(self.error_class.upper()))

    @property
    def job_status(self) -> ProcessingStatus:
        return self.status  # type: ignore[return-value]

    @property
    def completeness(self) -> Mapping[str, str]:
        return self.generation.completeness if self.generation is not None else {}

    @property
    def ready(self) -> bool:
        return self.job_status is ProcessingStatus.READY


def map_error_class(error: Exception | str | ErrorClass) -> ErrorClass:
    """Map provider/internal exception names to frozen §36 classes."""

    if isinstance(error, ErrorClass):
        return error
    if isinstance(
        error,
        (EnrichmentInputError, EnrichmentPublishConflict, EnrichmentNoEvidenceError, SourcePartialError),
    ):
        return ErrorClass.AMBIGUOUS
    if isinstance(error, str):
        raw = error
        name = error.casefold().replace("-", "_").replace(" ", "_")
    else:
        raw = getattr(
            error,
            "error_class",
            getattr(error, "code", getattr(error, "classification", "")),
        )
        name = type(error).__name__.casefold().replace("-", "_")
        if isinstance(raw, ErrorClass):
            return raw
        if isinstance(raw, str) and raw.strip():
            name = raw.casefold().replace("-", "_").replace(" ", "_")
    normalized = str(name).replace(".", "_")
    if normalized in {item.value.casefold() for item in ErrorClass}:
        return ErrorClass(normalized.upper())
    if any(token in normalized for token in ("policy", "denied", "forbidden", "unauthor")):
        return ErrorClass.POLICY_DENIED
    if any(token in normalized for token in ("ambiguous", "uncertain", "needs_review")):
        return ErrorClass.AMBIGUOUS
    if any(token in normalized for token in ("rate_limit", "ratelimit", "throttl", "429")):
        return ErrorClass.RATE_LIMITED
    if isinstance(error, (ProviderRateLimitedError,)):
        return ErrorClass.RATE_LIMITED
    if isinstance(error, (ProviderUnavailableError, SourceUnavailableError, TimeoutError, ConnectionError)):
        return ErrorClass.TRANSIENT
    if any(token in normalized for token in ("transient", "timeout", "unavailable", "connection", "temporary")):
        return ErrorClass.TRANSIENT
    if any(token in normalized for token in ("permanent", "invalid", "malformed", "output")):
        return ErrorClass.PERMANENT
    if isinstance(error, (PolicyDeniedError, PolicyViolation)):
        return ErrorClass.POLICY_DENIED
    del raw
    return ErrorClass.PERMANENT


classify_error = map_error_class
error_class_for = map_error_class


class EnrichmentWriter:
    """Generate, validate, and publish a fingerprint-bound enrichment.

    The writer is the only object in this module with a Notion write
    capability.  It writes one ``write_ai_region`` patch and never mirrors
    topics into Session properties or creates a second enrichment store.
    """

    def __init__(
        self,
        llm_adapter: LLMAdapter | None = None,
        notion_writer: Any | None = None,
        drive_reader: Any | None = None,
        state_store: Any | None = None,
        processor_version: str = DEFAULT_PROCESSOR_VERSION,
        *,
        llm: LLMAdapter | None = None,
        notion: Any | None = None,
        drive: Any | None = None,
        state: Any | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        session_max_chunks: int | None = 8,
        material_max_chunks: int | None = 16,
    ) -> None:
        # Support both the documented LLM-first keyword form and the common
        # worker wiring form ``(notion, drive, llm, state)``.  Capability
        # detection is used only for positional compatibility; the selected
        # adapter is still required to expose exactly the pure Phase 3 calls.
        positional = [llm_adapter, notion_writer, drive_reader, state_store]
        adapter_index = next(
            (
                index
                for index, value in enumerate(positional)
                if value is not None
                and callable(getattr(value, "enrich_session", None))
                and callable(getattr(value, "enrich_material", None))
            ),
            None,
        )
        if adapter_index == 2:
            llm_adapter, notion_writer, drive_reader, state_store = (
                drive_reader,
                llm_adapter,
                notion_writer,
                state_store,
            )
        elif adapter_index == 3:
            llm_adapter, notion_writer, drive_reader, state_store = (
                state_store,
                llm_adapter,
                notion_writer,
                drive_reader,
            )
        self.llm_adapter = llm_adapter or llm
        self.notion_writer = notion_writer or notion
        self.drive_reader = drive_reader or drive
        self.state_store = state_store if state_store is not None else state
        if self.llm_adapter is None:
            raise TypeError("llm_adapter is required")
        if not isinstance(processor_version, str) or not processor_version.strip():
            raise ValueError("processor_version must be a non-empty string")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        self.processor_version = processor_version.strip()
        self.max_attempts = max_attempts
        self.sleep = sleep or (lambda _: None)
        self.session_max_chunks = session_max_chunks
        self.material_max_chunks = material_max_chunks

    def process_session(
        self,
        entity_id: str | Any,
        derivative: Any | None = None,
        *,
        source_ref: SourceRef | Mapping[str, Any] | str | None = None,
        current_fingerprint: SourceFingerprint | Mapping[str, Any] | None = None,
    ) -> EnrichmentWriteResult:
        if _looks_like_derivative_text(entity_id) and isinstance(derivative, str):
            entity_id, derivative = derivative, entity_id
        elif not isinstance(entity_id, str) and isinstance(derivative, str):
            entity_id, derivative = derivative, entity_id
        return self._process(
            "session",
            entity_id if isinstance(entity_id, str) else None,
            derivative,
            source_ref=source_ref,
            current_fingerprint=current_fingerprint,
        )

    def process_material(
        self,
        entity_id: str | Any,
        derivative: Any | None = None,
        *,
        source_ref: SourceRef | Mapping[str, Any] | str | None = None,
        current_fingerprint: SourceFingerprint | Mapping[str, Any] | None = None,
    ) -> EnrichmentWriteResult:
        if _looks_like_derivative_text(entity_id) and isinstance(derivative, str):
            entity_id, derivative = derivative, entity_id
        elif not isinstance(entity_id, str) and isinstance(derivative, str):
            entity_id, derivative = derivative, entity_id
        return self._process(
            "material",
            entity_id if isinstance(entity_id, str) else None,
            derivative,
            source_ref=source_ref,
            current_fingerprint=current_fingerprint,
        )

    def process(
        self,
        kind: Literal["session", "material"],
        entity_id: str | Any,
        derivative: Any | None = None,
        *,
        source_ref: SourceRef | Mapping[str, Any] | str | None = None,
        current_fingerprint: SourceFingerprint | Mapping[str, Any] | None = None,
    ) -> EnrichmentWriteResult:
        normalized_kind = kind.casefold() if isinstance(kind, str) else kind
        if normalized_kind == "session":
            return self.process_session(
                entity_id,
                derivative,
                source_ref=source_ref,
                current_fingerprint=current_fingerprint,
            )
        if normalized_kind == "material":
            return self.process_material(
                entity_id,
                derivative,
                source_ref=source_ref,
                current_fingerprint=current_fingerprint,
            )
        raise ValueError("enrichment kind must be 'session' or 'material'")

    # Worker-facing descriptive aliases.
    write_session = process_session
    write_material = process_material
    enrich_session = process_session
    enrich_material = process_material
    generate_session = process_session
    generate_material = process_material
    publish_session = process_session
    publish_material = process_material
    run_session = process_session
    run_material = process_material

    def _process(
        self,
        kind: Literal["session", "material"],
        entity_id: str | None,
        derivative: Any | None,
        *,
        source_ref: SourceRef | Mapping[str, Any] | str | None,
        current_fingerprint: SourceFingerprint | Mapping[str, Any] | None,
    ) -> EnrichmentWriteResult:
        if self.state_store is None:
            error = SourceUnavailableError(
                "StateStore is required before enrichment can be published"
            )
            return EnrichmentWriteResult(
                status=ProcessingStatus.FAILED,
                error_class=ErrorClass.TRANSIENT,
                error=str(error),
            )

        try:
            derivative, front, derived_entity, derived_ref, graph_ref = self._obtain_derivative(
                kind, entity_id, derivative, source_ref
            )
            actual_entity = entity_id or derived_entity
            if not actual_entity:
                raise SourceUnavailableError("enrichment target entity_id is required")
            requested_ref = _coerce_source_ref(source_ref)
            if source_ref is not None and requested_ref is None:
                raise EnrichmentInputError("caller-supplied source_ref is invalid")
            if (
                derived_ref is not None
                and derived_ref.identity != graph_ref.identity
            ):
                raise EnrichmentInputError(
                    "normalized derivative source_ref does not match the graph source"
                )
            # The Notion graph is the independent source authority.  The
            # derivative's own front matter and any caller ref are only
            # cross-checks; neither can self-authorize the enrichment job.
            actual_ref = graph_ref
            initial = self._authoritative_fingerprint(
                actual_entity,
                actual_ref,
                supplied=current_fingerprint,
            )
            job = self._start_job(kind, actual_entity, actual_ref, initial)
        except Exception as exc:
            error_class = map_error_class(exc)
            status = (
                ProcessingStatus.NEEDS_REVIEW
                if error_class is ErrorClass.AMBIGUOUS
                else ProcessingStatus.FAILED
            )
            return EnrichmentWriteResult(
                status=status,
                error_class=error_class,
                error=str(exc),
            )

        job_status = _status_of(job)
        if job_status in _TERMINAL_STATUSES:
            if _is_safety_quarantined(job):
                self._verify_safety_quarantine(
                    job,
                    kind=kind,
                    entity_id=actual_entity,
                    fingerprint=initial,
                )
            return EnrichmentWriteResult(status=job_status, job=job)

        generation: EnrichmentGenerationResult | None = None
        try:
            generation = self._generate_with_retries(
                kind,
                derivative,
                actual_entity,
                initial,
                source_ref=actual_ref,
            )
        except (EnrichmentInputError, SourcePartialError) as exc:
            return self._finish_review(
                job,
                kind=kind,
                entity_id=actual_entity,
                fingerprint=initial,
                generation=None,
                error=exc,
                error_class=ErrorClass.AMBIGUOUS,
            )
        except Exception as exc:
            error_class = map_error_class(exc)
            if error_class is ErrorClass.AMBIGUOUS:
                return self._finish_review(
                    job,
                    kind=kind,
                    entity_id=actual_entity,
                    fingerprint=initial,
                    generation=None,
                    error=exc,
                    error_class=error_class,
                )
            return self._failure_result(
                job,
                generation=None,
                error=exc,
                error_class=error_class,
            )

        if not generation.ready:
            if generation.error_code == "ENRICHMENT_NO_EVIDENCE":
                error: Exception = EnrichmentNoEvidenceError(
                    "all enrichment candidates failed evidence grounding"
                )
            else:
                error = RuntimeError(
                    "enrichment completeness did not resolve all required kinds: "
                    + ", ".join(
                        f"{kind_name}={state}"
                        for kind_name, state in generation.completeness.items()
                        if state == "omitted_or_failed"
                    )
                )
            return self._finish_review(
                job,
                kind=kind,
                entity_id=actual_entity,
                fingerprint=initial,
                generation=generation,
                error=error,
                error_class=ErrorClass.AMBIGUOUS,
            )

        # TOCTOU gate: the value used to construct based_on is not sufficient
        # by itself.  Read the authoritative source state immediately before
        # the AI-region write and abort if it moved.  This read is inside the
        # terminalization boundary so an authority outage cannot strand the
        # durable job in PROCESSING.
        try:
            publish_fingerprint = self._authoritative_fingerprint(actual_entity, actual_ref)
        except Exception as exc:
            error_class = map_error_class(exc)
            if error_class is ErrorClass.AMBIGUOUS:
                return self._finish_review(
                    job,
                    kind=kind,
                    entity_id=actual_entity,
                    fingerprint=initial,
                    generation=generation,
                    error=exc,
                    error_class=error_class,
                )
            return self._failure_result(
                job,
                generation=generation,
                error=exc,
                error_class=error_class,
            )
        if publish_fingerprint != initial or publish_fingerprint != generation.input_fingerprint:
            conflict = EnrichmentPublishConflict(
                "source fingerprint changed before enrichment publish",
                details={"generated": initial, "publish": publish_fingerprint},
            )
            return self._finish_review(
                job,
                kind=kind,
                entity_id=actual_entity,
                fingerprint=initial,
                generation=generation,
                error=conflict,
                error_class=ErrorClass.AMBIGUOUS,
            )

        previous_record: Any | None = None
        write_attempted = False
        processing_record: Any | None = None
        try:
            # This snapshot is part of the safety boundary.  An unreadable
            # prior state is unknown, never proven absence, so publication is
            # forbidden until the strict read succeeds.
            previous_record = self._read_ai_region(kind, actual_entity, strict=True)
            write_attempted = True

            # Notion and StateStore are separate stores.  The frozen,
            # status-blind consumer can observe this AI-region publish before
            # the processing record and READY commit; verified rollback below
            # is the required mitigation for that unavoidable window.
            self._write_ai_region(kind, actual_entity, generation.record)
            processing_record = self._create_processing_record(
                job,
                kind=kind,
                entity_id=actual_entity,
                fingerprint=publish_fingerprint,
                generation=generation,
                status=ProcessingStatus.PROCESSING,
            )
            if processing_record is None:
                raise SourceUnavailableError(
                    "StateStore did not return a durable enrichment processing record"
                )
            completed = self._complete_job(job, ProcessingStatus.READY)
            if _status_of(completed) is not ProcessingStatus.READY:
                raise SourceUnavailableError(
                    "StateStore did not report a READY terminal transition"
                )
            return EnrichmentWriteResult(
                status=ProcessingStatus.READY,
                job=completed,
                generation=generation,
                record=generation.record,
                published=True,
                processing_record=processing_record,
            )
        except Exception as exc:
            # A write-capable provider failure never becomes READY.  If the
            # provider made the record visible before failing, restore the
            # previous AI region (or remove the new record) before returning.
            rollback_error: Exception | None = None
            if write_attempted:
                try:
                    self._rollback_ai_region(kind, actual_entity, previous_record)
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
            if rollback_error is not None:
                safety_error = (
                    rollback_error
                    if isinstance(rollback_error, EnrichmentSafetyError)
                    else EnrichmentSafetyError(
                        f"{exc}; AI-region rollback failed: {rollback_error}",
                        details={
                            "entity_id": actual_entity,
                            "original_error": str(exc),
                            "rollback_error": str(rollback_error),
                        },
                    )
                )
                self._record_safety_discrepancy(job, safety_error)
                raise safety_error from rollback_error
            error = exc
            error_class = map_error_class(exc)
            return self._failure_result(
                job,
                generation=generation,
                error=error,
                error_class=error_class,
            )

    def _generate_with_retries(
        self,
        kind: Literal["session", "material"],
        derivative: Any,
        entity_id: str,
        fingerprint: SourceFingerprint,
        *,
        source_ref: SourceRef,
    ) -> EnrichmentGenerationResult:
        generator: Any
        if kind == "session":
            generator = SessionEnrichmentGenerator(
                self.llm_adapter,
                processor_version=self.processor_version,
                max_chunks=self.session_max_chunks,
            )
        else:
            generator = MaterialEnrichmentGenerator(
                self.llm_adapter,
                processor_version=self.processor_version,
                max_chunks=self.material_max_chunks,
            )
        attempt = 1
        while True:
            try:
                return generator.generate(
                    derivative,
                    fingerprint,
                    entity_id=entity_id,
                    source_ref=source_ref,
                )
            except Exception as exc:
                error_class = map_error_class(exc)
                if not should_retry(error_class, attempt, self.max_attempts):
                    raise
                self.sleep(next_backoff(attempt, retry_after=_retry_after(exc)))
                attempt += 1

    def _obtain_derivative(
        self,
        kind: Literal["session", "material"],
        entity_id: str | None,
        derivative: Any | None,
        source_ref: SourceRef | Mapping[str, Any] | str | None,
    ) -> tuple[Any, Mapping[str, Any], str | None, SourceRef | None, SourceRef]:
        requested_ref = _coerce_source_ref(source_ref)
        if source_ref is not None and requested_ref is None:
            raise EnrichmentInputError("caller-supplied source_ref is invalid")
        if derivative is None:
            # A graph lookup is mandatory when the caller does not identify
            # the source.  In particular, do not manufacture a source ref from
            # the entity ID or later accept the derivative's own front matter.
            actual_ref = requested_ref
            if actual_ref is None:
                if entity_id is None:
                    raise EnrichmentInputError(
                        "an entity_id is required to resolve the graph source identity"
                    )
                actual_ref = self._graph_source_ref(kind, entity_id)
            method = getattr(self.drive_reader, "read_derived", None)
            if not callable(method):
                raise SourceUnavailableError("DriveReader has no read_derived method")
            last_error: Exception | None = None
            for candidate in (actual_ref, actual_ref.file_id, entity_id):
                if candidate is None:
                    continue
                try:
                    derivative = method(candidate)
                except (KeyError, LookupError, FileNotFoundError) as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    raise SourceUnavailableError("normalized derivative is unavailable") from exc
                if derivative is not None:
                    break
            if derivative is None:
                raise SourceUnavailableError("normalized derivative is unavailable") from last_error
            source_ref = actual_ref
        try:
            derived_entity, _, _, front = derivative_parts(derivative)
        except Exception as exc:
            raise SourceUnavailableError("normalized derivative could not be parsed") from exc
        if not isinstance(front, Mapping) or not front:
            raise SourceUnavailableError("normalized derivative has no front matter")
        front = dict(front)
        derived_ref = _coerce_source_ref(front.get("source_ref"))
        graph_entity = entity_id or derived_entity
        if not graph_entity:
            raise EnrichmentInputError("normalized derivative has no entity for graph lookup")
        graph_ref = self._graph_source_ref(kind, graph_entity)
        if requested_ref is not None and requested_ref.identity != graph_ref.identity:
            raise EnrichmentInputError(
                "caller-supplied source_ref does not match the graph source"
            )
        return derivative, front, derived_entity, derived_ref, graph_ref

    def _lookup_record(self, kind: Literal["session", "material"], entity_id: str) -> Any | None:
        name = "get_session" if kind == "session" else "get_material"
        owners = (self.notion_writer, getattr(self.notion_writer, "reader", None))
        for owner in owners:
            method = getattr(owner, name, None)
            if not callable(method):
                continue
            try:
                return method(entity_id)
            except (KeyError, LookupError, FileNotFoundError):
                return None
            except Exception as exc:
                raise SourceUnavailableError("Notion graph lookup is unavailable") from exc
        return None

    def _graph_source_ref(self, kind: Literal["session", "material"], entity_id: str) -> SourceRef:
        """Read the graph-owned source identity used to validate a derivative."""

        record = self._lookup_record(kind, entity_id)
        if record is None:
            raise EnrichmentInputError(
                "authoritative Notion graph record is unavailable for source validation"
            )
        if kind == "session":
            value = _record_field(
                record,
                "Normalized Transcript",
                "Transcript Source Ref",
                "Transcript Source",
                "Normalized Transcript URL",
                "Source Ref",
                "source_ref",
                default=None,
            )
        else:
            value = _record_field(
                record,
                "Normalized Source",
                "Normalized Source Ref",
                "Normalized Source URL",
                "Source Ref",
                "source_ref",
                default=None,
            )
        graph_ref = _coerce_source_ref(value)
        if graph_ref is None:
            raise EnrichmentInputError(
                "Notion graph does not provide an authoritative source identity"
            )
        return graph_ref

    def _authoritative_fingerprint(
        self,
        entity_id: str,
        source_ref: SourceRef,
        *,
        supplied: SourceFingerprint | Mapping[str, Any] | None = None,
    ) -> SourceFingerprint:
        # ``supplied`` is only a redundant caller cross-check.  It is never a
        # fallback because the current source authority is mandatory.
        last_error: Exception | None = None
        for provider in (self.drive_reader, self.state_store):
            method = _first_callable(
                provider,
                "get_current_fingerprint",
                "get_current_source_fingerprint",
                "get_source_fingerprint",
            )
            if method is None:
                continue
            for candidate in (entity_id, source_ref, source_ref.file_id):
                try:
                    value = method(candidate)
                except (KeyError, LookupError, FileNotFoundError) as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    raise SourceUnavailableError("current source fingerprint is unavailable") from exc
                if value is not None:
                    try:
                        authoritative = coerce_fingerprint(value)
                    except (TypeError, ValueError) as exc:
                        raise SourceUnavailableError("current source fingerprint is invalid") from exc
                    if supplied is not None:
                        try:
                            supplied_fingerprint = coerce_fingerprint(supplied)
                        except (TypeError, ValueError) as exc:
                            raise EnrichmentInputError(
                                "caller-supplied source fingerprint is invalid"
                            ) from exc
                        if supplied_fingerprint != authoritative:
                            raise EnrichmentInputError(
                                "caller-supplied source fingerprint does not match authority"
                            )
                    return authoritative
            # A callable authority that cannot resolve this entity is not
            # allowed to fall back to a caller-supplied fingerprint.  A second
            # configured authority (for example StateStore) may still be the
            # canonical source of the current fingerprint.
        raise SourceUnavailableError("current source fingerprint is unavailable") from last_error

    def _start_job(
        self,
        kind: Literal["session", "material"],
        entity_id: str,
        source_ref: SourceRef,
        fingerprint: SourceFingerprint,
    ) -> Any:
        operation = "enrich_session" if kind == "session" else "enrich_material"
        job_key = derive_job_key(
            source_ref.file_id,
            fingerprint.source_hash,
            operation,
            self.processor_version,
        )
        state = self.state_store
        if state is None:
            raise SourceUnavailableError("StateStore is required for enrichment jobs")
        existing = self._get_job(state, job_key=job_key)
        method = getattr(state, "create_job", None)
        if existing is None and not callable(method):
            raise SourceUnavailableError("StateStore has no create_job method")
        values = {
            "job_key": job_key,
            "operation": operation,
            "stage": "enrichment",
            "status": ProcessingStatus.PROCESSING.value,
            "course_key": None,
            "source_file_id": source_ref.file_id,
            "source_hash": fingerprint.source_hash,
            "target_entity_id": entity_id,
            "processor_version": self.processor_version,
        }
        if existing is not None:
            job = existing
        else:
            try:
                job = method(**values)
            except TypeError:
                job = method(
                    job_key,
                    operation,
                    "enrichment",
                    ProcessingStatus.PROCESSING.value,
                    None,
                    source_ref.file_id,
                    fingerprint.source_hash,
                    entity_id,
                )
        if job is None:
            raise SourceUnavailableError("StateStore did not return an enrichment job")
        current = _status_of(job)
        if current in _TERMINAL_STATUSES or current is ProcessingStatus.PROCESSING:
            return job
        if current is ProcessingStatus.PENDING:
            claim = getattr(state, "claim_job", None)
            if callable(claim):
                claimed = claim(_job_id(job))
                if claimed is not None:
                    job = claimed
                else:
                    refreshed = self._get_job(state, job_id=_job_id(job))
                    if refreshed is not None:
                        job = refreshed
            else:
                transition = getattr(state, "transition_job", None)
                if callable(transition):
                    job = transition(_job_id(job), ProcessingStatus.PROCESSING.value)
        if _status_of(job) is not ProcessingStatus.PROCESSING:
            raise SourceUnavailableError(
                "enrichment job did not reach PROCESSING state; publication is forbidden"
            )
        return job

    def _get_job(
        self,
        state: Any,
        *,
        job_id: str | None = None,
        job_key: str | None = None,
    ) -> Any | None:
        owners: list[Any] = []
        for candidate in (
            state,
            getattr(state, "store", None),
            getattr(state, "state_store", None),
        ):
            if candidate is not None and all(candidate is not owner for owner in owners):
                owners.append(candidate)
        for owner in owners:
            method = getattr(owner, "get_job", None)
            if not callable(method):
                continue
            try:
                if job_key is not None:
                    result = method(job_key=job_key)
                else:
                    result = method(job_id=job_id)
            except TypeError:
                try:
                    result = method(job_key if job_key is not None else job_id)
                except (KeyError, LookupError):
                    result = None
            except (KeyError, LookupError):
                result = None
            if result is not None:
                return result
        return None

    def _failure_result(
        self,
        job: Any,
        *,
        generation: EnrichmentGenerationResult | None,
        error: Exception,
        error_class: ErrorClass,
    ) -> EnrichmentWriteResult:
        failed_job: Any | None = None
        terminalization_error: Exception | None = None
        try:
            failed_job = self._fail_job(job, error_class, error)
        except Exception as exc:
            terminalization_error = exc
        reread_error: Exception | None = None
        try:
            refreshed = self._get_job(self.state_store, job_id=_job_id(job))
        except Exception as exc:
            refreshed = None
            reread_error = exc

        durable_status = _status_of(refreshed)
        if durable_status not in _TERMINAL_STATUSES:
            # A local FAILED result is unsafe while the durable row remains
            # PROCESSING (or cannot be read).  Keep the mismatch visible to
            # the caller instead of hiding a stranded worker job.
            if terminalization_error is not None or reread_error is not None or refreshed is None:
                cause = terminalization_error or reread_error
                details = {
                    "job_id": _job_id(job),
                    "durable_status": durable_status.value if durable_status else None,
                    "failure": str(error),
                }
                if cause is not None:
                    details["terminalization_error"] = str(cause)
                raise EnrichmentTerminalizationError(
                    "durable enrichment job could not be terminalized; "
                    "PROCESSING must remain visible to the caller",
                    details=details,
                ) from cause
            raise EnrichmentTerminalizationError(
                "durable enrichment job is not terminal after failure handling",
                details={
                    "job_id": _job_id(job),
                    "durable_status": durable_status.value if durable_status else None,
                },
            )

        final_job = refreshed
        if final_job is None:
            final_job = failed_job or job
        final_status = _status_of(final_job)
        # A failure result must never claim that the unverified READY
        # transition succeeded.  If the provider did not return a usable
        # terminal row, report the durable terminal state while retaining the
        # row for the caller to inspect.
        status = (
            final_status
            if final_status in _TERMINAL_STATUSES and final_status is not ProcessingStatus.READY
            else ProcessingStatus.FAILED
        )
        return EnrichmentWriteResult(
            status=status,
            job=final_job,
            generation=generation,
            error_class=error_class,
            error=str(error),
        )

    def _verify_safety_quarantine(
        self,
        job: Any,
        *,
        kind: Literal["session", "material"],
        entity_id: str,
        fingerprint: SourceFingerprint,
    ) -> None:
        """Recheck a quarantined job before allowing deterministic reuse.

        A FAILED status is not sufficient evidence of safety: the frozen
        consumer ignores it and may still serve a fresh AI-region record.
        Only an absent region, or the exact pre-publish state recorded in the
        quarantine audit, proves that the failed publication is no longer
        visible.
        """

        try:
            visible = self._read_ai_region(kind, entity_id, strict=True)
        except Exception as exc:
            raise EnrichmentSafetyError(
                "safety-quarantined enrichment could not be read back on retry",
                details={
                    "job_id": _job_id(job),
                    "entity_id": entity_id,
                    "read_error": str(exc),
                },
            ) from exc

        expected_available, expected_previous = _quarantined_expected_previous(job)
        if visible is None or (
            expected_available and _same_enrichment_state(visible, expected_previous)
        ):
            return

        try:
            observed = coerce_enrichment(visible)
            observed_fingerprint = observed.source_fingerprint
        except (TypeError, ValueError, KeyError) as exc:
            raise EnrichmentSafetyError(
                "safety-quarantined job has an unreadable consumer-visible enrichment",
                details={
                    "job_id": _job_id(job),
                    "entity_id": entity_id,
                    "observed_after_rollback": _serialize_enrichment_state(visible),
                },
            ) from exc

        message = (
            "safety-quarantined job still exposes the uncommitted consumer-visible "
            "enrichment for the attempted fingerprint"
            if observed_fingerprint == fingerprint
            else "safety-quarantined job still exposes an unverified consumer-visible enrichment"
        )
        raise EnrichmentSafetyError(
            message,
            details={
                "job_id": _job_id(job),
                "entity_id": entity_id,
                "attempted_fingerprint": {
                    "source_version": fingerprint.source_version,
                    "source_hash": fingerprint.source_hash,
                },
                "observed_fingerprint": {
                    "source_version": observed_fingerprint.source_version,
                    "source_hash": observed_fingerprint.source_hash,
                },
                "expected_previous": _serialize_enrichment_state(expected_previous)
                if expected_available
                else None,
                "observed_after_rollback": _serialize_enrichment_state(visible),
            },
        )

    def _read_ai_region(
        self,
        kind: Literal["session", "material"],
        entity_id: str,
        *,
        strict: bool = False,
    ) -> Any | None:
        method_name = "get_session_enrichment" if kind == "session" else "get_material_enrichment"
        owners = (self.notion_writer, getattr(self.notion_writer, "reader", None))
        last_error: Exception | None = None
        found_method = False
        for owner in owners:
            method = getattr(owner, method_name, None)
            if not callable(method):
                continue
            found_method = True
            try:
                return method(entity_id)
            except (KeyError, LookupError, FileNotFoundError) as exc:
                last_error = exc
                if strict:
                    # A strict read must never fall through to a guessed
                    # absence, even if another compatibility owner exists.
                    raise SourceUnavailableError("AI-region read-back is unavailable") from exc
                continue
        if strict:
            if last_error is not None:
                raise SourceUnavailableError("AI-region read-back is unavailable") from last_error
            if not found_method:
                raise SourceUnavailableError("Notion reader has no AI-region read method")
        return None

    def _record_safety_discrepancy(
        self,
        job: Any,
        error: EnrichmentSafetyError,
    ) -> None:
        """Best-effort durable audit of a consumer-visible safety failure."""

        audit_error = RuntimeError(
            f"{_SAFETY_QUARANTINE_MARKER}; {error}; "
            "safety_details="
            + json.dumps(dict(error.details), ensure_ascii=False, sort_keys=True, default=str)
        )
        try:
            failed = self._fail_job(job, ErrorClass.PERMANENT, audit_error)
        except Exception as exc:
            error.details["durable_record_error"] = str(exc)
            return
        try:
            refreshed = self._get_job(self.state_store, job_id=_job_id(job))
        except Exception as exc:
            error.details["durable_record_error"] = str(exc)
            return
        returned_status = _status_of(failed)
        durable_status = _status_of(refreshed)
        error.details["returned_job_status"] = returned_status.value if returned_status else None
        error.details["durable_job_status"] = durable_status.value if durable_status else None
        error.details["discrepancy_recorded"] = durable_status in _TERMINAL_STATUSES

    def _rollback_ai_region(
        self,
        kind: Literal["session", "material"],
        entity_id: str,
        previous: Any | None,
    ) -> None:
        target_db = "Sessions" if kind == "session" else "Materials"
        restore = _first_callable(
            self.notion_writer,
            "restore_ai_region",
            "rollback_ai_region",
        )
        if restore is not None:
            _invoke_region_compensation(restore, target_db, entity_id, previous)
        elif previous is not None:
            self._write_ai_region(kind, entity_id, coerce_enrichment(previous))
        else:
            delete = _first_callable(
                self.notion_writer,
                "delete_ai_region",
                "clear_ai_region",
                "remove_ai_region",
            )
            if delete is not None:
                _invoke_region_compensation(delete, target_db, entity_id)
            else:
                # Provider-neutral test doubles sometimes expose the
                # reader-side backing mapping but no explicit compensation
                # capability.  This is a last-resort local rollback; real
                # adapters should implement one of the explicit methods.
                for owner in (self.notion_writer, getattr(self.notion_writer, "reader", None)):
                    attribute = "enrichments" if kind == "session" else "material_enrichments"
                    store = getattr(owner, attribute, None)
                    if isinstance(store, dict):
                        store.pop(entity_id, None)
                        break
                else:
                    raise SourceUnavailableError("Notion writer cannot roll back the AI region")

        # The frozen consumer keys only on the enrichment fingerprint and does
        # not consult job status.  A successful compensation call is therefore
        # insufficient: read the consumer-visible region and require the exact
        # previous state (or absence) before failure can be returned.
        restored = self._read_ai_region(kind, entity_id, strict=True)
        if not _same_enrichment_state(restored, previous):
            raise EnrichmentSafetyError(
                "AI-region compensation did not restore the previous consumer-visible state",
                details={
                    "entity_id": entity_id,
                    "target_db": target_db,
                    "expected_previous": _serialize_enrichment_state(previous),
                    "observed_after_rollback": _serialize_enrichment_state(restored),
                },
            )

    def _write_ai_region(self, kind: Literal["session", "material"], entity_id: str, record: EnrichmentRecord) -> Any:
        method = getattr(self.notion_writer, "write_ai_region", None)
        if not callable(method):
            raise SourceUnavailableError("Notion writer has no write_ai_region method")
        target_db = "Sessions" if kind == "session" else "Materials"
        serialized = record.as_dict()
        patch = {"enrichment": serialized, "ownership": "AI"}
        # The actor is fixed at this boundary; no LLM output or caller string
        # can select a more powerful write capability.
        try:
            return method(target_db, entity_id, patch, actor=AutomationActor.AUTOMATION)
        except TypeError as first_error:
            try:
                return method(target_db, entity_id, patch)
            except TypeError:
                try:
                    return method(entity_id, patch, actor=AutomationActor.AUTOMATION)
                except TypeError:
                    raise first_error

    def _create_processing_record(
        self,
        job: Any,
        *,
        kind: Literal["session", "material"],
        entity_id: str,
        fingerprint: SourceFingerprint,
        generation: EnrichmentGenerationResult,
        status: ProcessingStatus = ProcessingStatus.READY,
    ) -> Any | None:
        state = self.state_store
        if state is None:
            return None
        method = getattr(state, "create_processing_record", None)
        if not callable(method):
            raise SourceUnavailableError("StateStore has no create_processing_record method")
        operation = "enrich_session" if kind == "session" else "enrich_material"
        audit = {
            "entity_id": entity_id,
            "kind": kind,
            "input_fingerprint": {
                "source_version": fingerprint.source_version,
                "source_hash": fingerprint.source_hash,
            },
            "completeness": dict(generation.completeness),
            "produced_count": generation.produced_count,
            "dropped_count": generation.dropped_count,
            "drop_reasons": list(generation.drop_reasons),
        }
        values = {
            "job_id": _job_id(job),
            "operation": operation,
            "processor_version": self.processor_version,
            "input_hash": fingerprint.source_hash,
            "output_ref_json": audit,
            "status": status.value,
        }
        try:
            return method(**values)
        except TypeError:
            return method(_job_id(job), operation, self.processor_version)

    def _finish_review(
        self,
        job: Any,
        *,
        kind: Literal["session", "material"],
        entity_id: str,
        fingerprint: SourceFingerprint,
        generation: EnrichmentGenerationResult | None,
        error: Exception,
        error_class: ErrorClass,
    ) -> EnrichmentWriteResult:
        processing_record = None
        record_error: Exception | None = None
        try:
            processing_record = (
                self._create_processing_record(
                    job,
                    kind=kind,
                    entity_id=entity_id,
                    fingerprint=fingerprint,
                    generation=generation,
                    status=ProcessingStatus.NEEDS_REVIEW,
                )
                if generation is not None
                else self._create_gate_processing_record(
                    job,
                    kind=kind,
                    entity_id=entity_id,
                    fingerprint=fingerprint,
                    error=error,
                )
            )
        except Exception as exc:
            # Audit-record failure must not strand a job that has no consumer
            # visible write.  The job transition below remains authoritative.
            record_error = exc
        try:
            completed = self._complete_job(
                job,
                ProcessingStatus.NEEDS_REVIEW,
                error_class=error_class,
                last_error=str(error),
            )
        except Exception as exc:
            return self._failure_result(
                job,
                generation=generation,
                error=exc,
                error_class=map_error_class(exc),
            )
        completed_status = _status_of(completed)
        if completed_status is not ProcessingStatus.NEEDS_REVIEW:
            return self._failure_result(
                job,
                generation=generation,
                error=SourceUnavailableError(
                    "StateStore did not report a NEEDS_REVIEW terminal transition"
                ),
                error_class=ErrorClass.TRANSIENT,
            )
        final_error: Exception = error
        if record_error is not None:
            final_error = RuntimeError(f"{error}; processing-record write failed: {record_error}")
        return EnrichmentWriteResult(
            status=ProcessingStatus.NEEDS_REVIEW,
            job=completed,
            generation=generation,
            record=None,
            error_class=error_class,
            error=str(final_error),
            processing_record=processing_record,
        )

    def _create_gate_processing_record(
        self,
        job: Any,
        *,
        kind: Literal["session", "material"],
        entity_id: str,
        fingerprint: SourceFingerprint,
        error: Exception,
    ) -> Any | None:
        """Record a rejected input gate without manufacturing an enrichment."""

        state = self.state_store
        if state is None:
            return None
        method = getattr(state, "create_processing_record", None)
        if not callable(method):
            return None
        required = (
            ("summary", "topics", "professor_emphasis", "professor_examples", "exam_signals", "likely_confusions")
            if kind == "session"
            else ("content_index", "topics")
        )
        audit = {
            "entity_id": entity_id,
            "kind": kind,
            "input_fingerprint": {
                "source_version": fingerprint.source_version,
                "source_hash": fingerprint.source_hash,
            },
            "completeness": {item: "omitted_or_failed" for item in required},
            "produced_count": 0,
            "dropped_count": 0,
            "drop_reasons": [type(error).__name__],
        }
        values = {
            "job_id": _job_id(job),
            "operation": "enrich_session" if kind == "session" else "enrich_material",
            "processor_version": self.processor_version,
            "input_hash": fingerprint.source_hash,
            "output_ref_json": audit,
            "status": ProcessingStatus.NEEDS_REVIEW.value,
        }
        try:
            return method(**values)
        except TypeError:
            try:
                return method(_job_id(job), values["operation"], self.processor_version)
            except Exception:
                return None

    def _complete_job(
        self,
        job: Any,
        status: ProcessingStatus,
        *,
        error_class: ErrorClass | None = None,
        last_error: str | None = None,
    ) -> Any | None:
        if self.state_store is None:
            raise SourceUnavailableError("StateStore is required for job completion")
        method = getattr(self.state_store, "complete_job", None)
        if not callable(method):
            method = getattr(self.state_store, "transition_job", None)
        if not callable(method):
            raise SourceUnavailableError("StateStore has no job completion method")
        values = {"error_class": error_class.value if error_class else None, "last_error": last_error}
        try:
            try:
                result = method(_job_id(job), status.value, **values)
            except TypeError:
                result = method(_job_id(job), status.value)
        except Exception as exc:
            # A provider exception leaves the commit outcome ambiguous.  A
            # durable reread is still authoritative: accept only if it proves
            # the requested transition, otherwise preserve the failure path.
            try:
                refreshed = self._get_job(self.state_store, job_id=_job_id(job))
            except Exception as reread_exc:
                raise EnrichmentTerminalizationError(
                    "durable enrichment job could not be reread after completion failure",
                    details={"job_id": _job_id(job), "expected_status": status.value},
                ) from reread_exc
            if _status_of(refreshed) is status:
                return refreshed
            raise
        del result
        refreshed = self._get_job(self.state_store, job_id=_job_id(job))
        durable_status = _status_of(refreshed)
        if durable_status is not status:
            raise EnrichmentTerminalizationError(
                "durable enrichment job did not reach the requested terminal state",
                details={
                    "job_id": _job_id(job),
                    "expected_status": status.value,
                    "durable_status": durable_status.value if durable_status else None,
                },
            )
        # The durable reread, not the provider method's return object, is the
        # commit proof used by the caller.
        return refreshed

    def _fail_job(self, job: Any, error_class: ErrorClass, error: Exception) -> Any | None:
        if self.state_store is None:
            raise SourceUnavailableError("StateStore is required for job failure")
        method = getattr(self.state_store, "fail_job", None)
        if callable(method):
            try:
                return method(_job_id(job), error_class.value, str(error))
            except TypeError:
                try:
                    return method(
                        _job_id(job),
                        error_class=error_class.value,
                        last_error=str(error),
                    )
                except TypeError:
                    return method(
                        _job_id(job),
                        error_class.value,
                        last_error=str(error),
                    )

        method = getattr(self.state_store, "transition_job", None)
        if not callable(method):
            raise SourceUnavailableError("StateStore has no job failure method")
        try:
            return method(
                _job_id(job),
                ProcessingStatus.FAILED.value,
                error_class=error_class.value,
                last_error=str(error),
            )
        except TypeError:
            return method(_job_id(job), ProcessingStatus.FAILED.value)
        raise SourceUnavailableError("StateStore job failure transition was not reported")


def _job_id(job: Any) -> str:
    if isinstance(job, Mapping):
        value = job.get("id", job.get("job_id"))
    else:
        value = getattr(job, "id", getattr(job, "job_id", job))
    return str(value)


def _job_field(job: Any, name: str) -> Any:
    if isinstance(job, Mapping):
        return job.get(name)
    return getattr(job, name, None)


def _is_safety_quarantined(job: Any) -> bool:
    """Return whether a durable terminal job records an unresolved safety audit."""

    marker = _SAFETY_QUARANTINE_MARKER.casefold()
    return any(
        isinstance(value, str) and marker in value.casefold()
        for value in (_job_field(job, "error_class"), _job_field(job, "last_error"))
    )


def _quarantined_expected_previous(job: Any) -> tuple[bool, Any | None]:
    """Read the pre-publish snapshot from the durable quarantine audit."""

    last_error = _job_field(job, "last_error")
    if not isinstance(last_error, str):
        return False, None
    marker = "safety_details="
    _, separator, encoded = last_error.rpartition(marker)
    if not separator:
        return False, None
    try:
        details = json.loads(encoded)
    except (TypeError, ValueError):
        return False, None
    if not isinstance(details, Mapping) or "expected_previous" not in details:
        return False, None
    return True, details["expected_previous"]


def _serialize_enrichment_state(value: Any | None) -> Any:
    if value is None:
        return None
    try:
        return coerce_enrichment(value).as_dict()
    except (TypeError, ValueError, KeyError):
        return repr(value)


def _same_enrichment_state(left: Any | None, right: Any | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return coerce_enrichment(left).as_dict() == coerce_enrichment(right).as_dict()
    except (TypeError, ValueError, KeyError):
        # An unreadable or malformed post-compensation value cannot be
        # treated as a successful restore: fail closed at the caller.
        return False


def _status_of(value: Any) -> ProcessingStatus | None:
    if value is None:
        return None
    raw = value.get("status") if isinstance(value, Mapping) else getattr(value, "status", value)
    if isinstance(raw, ProcessingStatus):
        return raw
    if isinstance(raw, str):
        try:
            return ProcessingStatus(raw.upper())
        except ValueError:
            return None
    return None


def _coerce_source_ref(value: Any) -> SourceRef | None:
    if isinstance(value, SourceRef):
        provider, file_id, web_url = value.provider, value.file_id, value.web_url
    elif isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id", value.get("id"))
        web_url = value.get("web_url")
    elif value is not None and hasattr(value, "provider") and hasattr(value, "file_id"):
        provider = getattr(value, "provider")
        file_id = getattr(value, "file_id")
        web_url = getattr(value, "web_url", None)
    elif isinstance(value, str) and value.strip():
        provider, file_id, web_url = "google_drive", value, None
    else:
        return None
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(file_id, str) or not file_id.strip():
        return None
    return SourceRef(
        provider.strip(),
        file_id.strip(),
        web_url if isinstance(web_url, str) else None,
    )


def _looks_like_derivative_text(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("---\n") and "schema:" in value


def _first_callable(value: Any, *names: str) -> Callable[..., Any] | None:
    for name in names:
        method = getattr(value, name, None)
        if callable(method):
            return method
    return None


def _invoke_region_compensation(
    method: Callable[..., Any],
    target_db: str,
    entity_id: str,
    previous: Any = None,
) -> Any:
    """Call an adapter's optional AI-region rollback shape compatibly."""

    try:
        return method(
            target_db,
            entity_id,
            previous,
            actor=AutomationActor.AUTOMATION,
        )
    except TypeError as first_error:
        try:
            return method(target_db, entity_id, previous)
        except TypeError:
            try:
                return method(
                    entity_id,
                    previous,
                    actor=AutomationActor.AUTOMATION,
                )
            except TypeError:
                try:
                    return method(entity_id, previous)
                except TypeError:
                    # Delete-style methods generally accept no previous-value
                    # argument.  Retry those shapes only when this invocation
                    # was a deletion request.
                    if previous is None:
                        try:
                            return method(
                                target_db,
                                entity_id,
                                actor=AutomationActor.AUTOMATION,
                            )
                        except TypeError:
                            return method(target_db, entity_id)
                    raise first_error


def _retry_after(error: Exception) -> float | None:
    value = getattr(error, "retry_after", None)
    if value is None:
        value = getattr(error, "retry_after_seconds", None)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


__all__ = [
    "DEFAULT_PROCESSOR_VERSION",
    "EnrichmentSafetyError",
    "EnrichmentTerminalizationError",
    "EnrichmentWriteResult",
    "EnrichmentWriter",
    "classify_error",
    "error_class_for",
    "map_error_class",
]
