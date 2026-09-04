"""Transcript ingestion orchestration with the frozen commit ordering."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from uls.domain.enums import ProcessingStatus, to_processing_status
from uls.domain.errors import SourcePartialError, SourceUnavailableError
from uls.domain.ids import parse_entity_id
from uls.domain.source_ref import SourceRef
from uls.ingestion.classifier import SourceKind
from uls.normalization.schemas import NormalizedTranscript
from uls.normalization.transcript import normalize_transcript
from uls.normalization.validators import validate_normalized_transcript
from uls.orchestration.jobs import derive_job_key
from uls.orchestration.retry import ErrorClass


TRANSCRIPT_INGEST_OPERATION = "TRANSCRIPT_INGEST"


@dataclass(frozen=True)
class TranscriptIngestResult:
    transcript: NormalizedTranscript
    entity_id: str
    status: ProcessingStatus
    job: Any | None = None
    published_ref: Any | None = None
    processing_record: Any | None = None

    @property
    def derivative(self) -> NormalizedTranscript:
        return self.transcript

    @property
    def is_partial(self) -> bool:
        return self.status is ProcessingStatus.PARTIAL


def ingest_transcript(
    raw: str | bytes | None = None,
    *,
    entity_id: str | None = None,
    course_key: str,
    source_ref: SourceRef | Mapping[str, Any] | str,
    source_hash: str,
    source_version: int = 1,
    processor_version: str = "1.2.0",
    state_store: Any | None = None,
    drive_writer: Any | None = None,
    notion_writer: Any | None = None,
    now: datetime | str | None = None,
    source_file_id: str | None = None,
    derived_ref: Any | None = None,
    raw_text: str | bytes | None = None,
    state: Any | None = None,
    drive_adapter: Any | None = None,
    notion_adapter: Any | None = None,
    operation: str = TRANSCRIPT_INGEST_OPERATION,
    **_: Any,
) -> TranscriptIngestResult:
    """Normalize, publish, and register one transcript.

    The observable ordering is fixed:

    ``PROCESSING → staged write → validate → atomic publish → Notion update →
    processing record → READY/PARTIAL``.

    ``drive_writer``/``notion_writer`` are deliberately accepted only here,
    at the worker ingestion boundary; RetrievalEngine has no such dependency.
    """

    if raw is None:
        raw = raw_text
    if raw is None:
        raise SourceUnavailableError("raw transcript content is required")
    if state_store is None:
        state_store = state
    if drive_writer is None:
        drive_writer = drive_adapter
    if notion_writer is None:
        notion_writer = notion_adapter
    _require_ingest_collaborators(
        state_store,
        drive_writer,
        notion_writer,
    )
    source = _coerce_source_ref(source_ref)
    source_file = source_file_id or source.file_id

    # SQLite's source-bound allocation algorithm requires a source_files row.
    # A caller-supplied ID is only a consistency claim: allocation still runs
    # and remains the source of truth for the canonical binding.
    requested_entity_id = entity_id
    source_registered = False
    if entity_id is None:
        entity_id = _bind_source_entity(
            state_store,
            source_file,
            source,
            course_key,
            source_hash,
            requested_entity_id,
        )
        source_registered = True

    job_key = derive_job_key(source_file, source_hash, operation, processor_version)
    existing_job = _get_job_by_key(state_store, job_key)
    if _status_of(existing_job) in {ProcessingStatus.READY, ProcessingStatus.PARTIAL}:
        if not source_registered:
            entity_id = _bind_source_entity(
                state_store,
                source_file,
                source,
                course_key,
                source_hash,
                requested_entity_id,
            )
            source_registered = True
        _register_version(
            state_store,
            source_file,
            source_hash,
            entity_id,
            source,
            source_version,
            processor_version,
        )
        # The deterministic job key is the idempotency boundary.  A retry of
        # an already terminal source-processing job must not republish a
        # derivative or mutate Notion a second time.
        transcript = normalize_transcript(
            raw,
            entity_id=entity_id,
            course_key=course_key,
            source_ref=source,
            source_hash=source_hash,
            source_version=source_version,
            processor_version=processor_version,
            now=now,
        )
        return TranscriptIngestResult(
            transcript=transcript,
            entity_id=entity_id,
            status=_status_of(existing_job) or to_processing_status(transcript.status),
            job=existing_job,
            published_ref=derived_ref,
        )
    job = _create_processing_job(
        state_store,
        job_key=job_key,
        operation=operation,
        course_key=course_key,
        source_file_id=source_file,
        source_hash=source_hash,
        entity_id=entity_id,
        processor_version=processor_version,
    )
    if job is None or _status_of(job) is not ProcessingStatus.PROCESSING:
        raise SourceUnavailableError(
            "StateStore.create_job did not return a job in PROCESSING state"
        )

    transcript: NormalizedTranscript
    final_status: ProcessingStatus
    published_ref: Any = derived_ref
    processing_record: Any | None = None
    completed_job: Any | None = None
    try:
        if not source_registered:
            entity_id = _bind_source_entity(
                state_store,
                source_file,
                source,
                course_key,
                source_hash,
                requested_entity_id,
            )
            source_registered = True
        _register_version(
            state_store,
            source_file,
            source_hash,
            entity_id,
            source,
            source_version,
            processor_version,
        )
        if not entity_id:
            raise ValueError("entity_id is required when StateStore allocation is unavailable")
        _validate_commit_participants(state_store, drive_writer, notion_writer)
        transcript = normalize_transcript(
            raw,
            entity_id=entity_id,
            course_key=course_key,
            source_ref=source,
            source_hash=source_hash,
            source_version=source_version,
            processor_version=processor_version,
            now=now,
        )
        final_status = to_processing_status(transcript.status)
        staged_ref = _stage_derived(
            drive_writer,
            source,
            entity_id,
            transcript.to_markdown(),
            derived_ref,
        )
        if staged_ref is None:
            raise SourceUnavailableError("Drive writer did not return a staged derivative reference")
        _validate_staged(drive_writer, staged_ref, transcript)
        published_ref = _publish_derived(
            drive_writer,
            source,
            entity_id,
            staged_ref,
            transcript.to_markdown(),
            derived_ref,
        )
        if published_ref is None:
            raise SourceUnavailableError("Drive writer did not return a published derivative reference")
        _update_notion(
            notion_writer,
            entity_id,
            published_ref,
            transcript,
            status=final_status,
        )
        processing_record = _create_processing_record(
            state_store,
            job,
            operation=operation,
            processor_version=processor_version,
            source_hash=source_hash,
            output_ref=published_ref,
            status=final_status,
        )
        if processing_record is None:
            raise SourceUnavailableError(
                "StateStore.create_processing_record did not return a processing record"
            )
        completed_job = _complete_job(state_store, job, final_status)
        if completed_job is None or _status_of(completed_job) is not final_status:
            raise SourceUnavailableError(
                "StateStore.complete_job did not commit the terminal job status"
            )
        job = completed_job
    except Exception as exc:
        # A failed provider transaction is not a valid partial normalization.
        # Keep the job visibly failed when possible; do not claim READY.
        _fail_job(state_store, job, exc)
        raise

    return TranscriptIngestResult(
        transcript=transcript,
        entity_id=entity_id,
        status=final_status,
        job=job,
        published_ref=published_ref,
        processing_record=processing_record,
    )


def _coerce_source_ref(value: SourceRef | Mapping[str, Any] | str) -> SourceRef:
    if isinstance(value, SourceRef):
        return value
    if isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id", value.get("id"))
        if not isinstance(provider, str) or not isinstance(file_id, str) or not file_id:
            raise ValueError("source_ref requires provider and file_id")
        web_url = value.get("web_url")
        return SourceRef(provider, file_id, web_url if isinstance(web_url, str) else None)
    if value is not None and hasattr(value, "provider") and hasattr(value, "file_id"):
        provider = getattr(value, "provider")
        file_id = getattr(value, "file_id")
        web_url = getattr(value, "web_url", None)
        if isinstance(provider, str) and isinstance(file_id, str) and file_id.strip():
            return SourceRef(
                provider.strip(),
                file_id.strip(),
                web_url if isinstance(web_url, str) else None,
            )
    if isinstance(value, str) and value.strip():
        return SourceRef("google_drive", value.strip())
    raise TypeError("source_ref must be a SourceRef, mapping, or non-empty string")


def _register_source(
    state: Any,
    source_file_id: str,
    source: SourceRef,
    course_key: str,
    source_hash: str,
    entity_id: str | None,
) -> Any:
    method = getattr(state, "register_source_file", None)
    if not callable(method):
        raise ValueError("StateStore has no register_source_file method")
    values = {
        "source_file_id": source_file_id,
        "provider": source.provider,
        "provider_file_id": source.file_id,
        "course_key": course_key,
        "source_kind": SourceKind.TRANSCRIPT.value,
        "original_filename": None,
        "current_hash": source_hash,
        "canonical_entity_id": entity_id,
    }
    result = _call_data(
        method,
        values,
        [(source_file_id, source.provider, source.file_id, course_key, SourceKind.TRANSCRIPT.value)],
    )
    if result is None:
        raise SourceUnavailableError(
            "StateStore.register_source_file did not return a registered source file"
        )
    return result


def _bind_source_entity(
    state: Any,
    source_file_id: str,
    source: SourceRef,
    course_key: str,
    source_hash: str,
    requested_entity_id: str | None,
) -> str:
    """Register the source and verify its allocator-backed canonical entity.

    ``requested_entity_id`` is a caller-side consistency claim only.  It must
    never be passed to source registration, because doing so would seed the
    value that the source-bound allocator is required to determine
    independently under §8.6.1.
    """

    _register_source(
        state,
        source_file_id,
        source,
        course_key,
        source_hash,
        None,
    )
    allocated_entity_id = _allocate_entity(state, course_key, source_file_id)
    if requested_entity_id is not None and allocated_entity_id != requested_entity_id:
        raise ValueError(
            "allocated entity ID does not match the caller-supplied entity ID: "
            f"{allocated_entity_id!r} != {requested_entity_id!r}"
        )
    return allocated_entity_id


def _allocate_entity(state: Any, course_key: str, source_file_id: str) -> str:
    method = getattr(state, "allocate_entity", None)
    if not callable(method):
        raise ValueError("StateStore has no callable allocate_entity method")
    last_error: Exception | None = None
    for args, kwargs in (
        ((course_key, "S", source_file_id), {}),
        ((course_key, "session", source_file_id), {}),
    ):
        try:
            allocated = method(*args, **kwargs)
        except (TypeError, ValueError) as exc:
            last_error = exc
            continue
        if not isinstance(allocated, str) or not allocated:
            raise ValueError(
                "StateStore.allocate_entity did not return a non-empty Entity ID"
            )
        try:
            parse_entity_id(allocated)
        except Exception as exc:
            raise ValueError(
                f"StateStore.allocate_entity returned an invalid Entity ID: {allocated!r}"
            ) from exc
        return allocated
    if last_error is not None:
        raise ValueError("unable to allocate Session entity") from last_error
    raise ValueError("unable to allocate Session entity")


def _register_version(
    state: Any,
    source_file_id: str,
    source_hash: str,
    entity_id: str,
    source: SourceRef,
    source_version: int,
    processor_version: str,
) -> Any:
    method = getattr(state, "register_source_version", None)
    if not callable(method):
        raise ValueError("StateStore has no register_source_version method")
    values = {
        "source_file_id": source_file_id,
        "source_hash": source_hash,
        "canonical_entity_id": entity_id,
        "source_ref_json": {
            "provider": source.provider,
            "file_id": source.file_id,
            "web_url": source.web_url,
        },
        "processor_version": processor_version,
        "version": source_version,
    }
    result = _call_data(
        method,
        values,
        [
            (
                (
                    source_file_id,
                    source_hash,
                    entity_id,
                    values["source_ref_json"],
                    processor_version,
                    source_version,
                ),
                {},
            )
        ],
    )
    if result is None:
        raise SourceUnavailableError(
            "StateStore.register_source_version did not return a registered source version"
        )
    return result


def _create_processing_job(
    state: Any,
    *,
    job_key: str,
    operation: str,
    course_key: str,
    source_file_id: str,
    source_hash: str,
    entity_id: str,
    processor_version: str,
) -> Any:
    method = getattr(state, "create_job", None)
    if not callable(method):
        raise SourceUnavailableError("StateStore has no create_job method")
    values = {
        "job_key": job_key,
        "operation": operation,
        "stage": "transcript",
        "status": ProcessingStatus.PROCESSING.value,
        "course_key": course_key,
        "source_file_id": source_file_id,
        "source_hash": source_hash,
        "target_entity_id": entity_id,
        "processor_version": processor_version,
    }
    job = _call_data(method, values, [(job_key, operation, "transcript", ProcessingStatus.PROCESSING.value)])
    if job is None:
        raise SourceUnavailableError(
            "StateStore.create_job did not return a job in PROCESSING state"
        )

    if _status_of(job) is ProcessingStatus.PROCESSING:
        confirmed = _confirmed_processing_job(state, job_key, job)
        if confirmed is not None:
            return confirmed

    job_id = _job_id(job)
    transition_errors: list[Exception] = []
    transition = getattr(state, "transition_job", None)
    if callable(transition):
        try:
            transitioned = _call_data(
                transition,
                {
                    "job_id": job_id,
                    "status": ProcessingStatus.PROCESSING.value,
                },
                [
                    ((job_id, ProcessingStatus.PROCESSING.value), {}),
                    ((job_id,), {}),
                ],
            )
        except Exception as exc:
            transition_errors.append(exc)
        else:
            if transitioned is not None:
                job = transitioned
            confirmed = _confirmed_processing_job(state, job_key, transitioned or job)
            if confirmed is not None:
                return confirmed

    claim = getattr(state, "claim_job", None)
    if callable(claim):
        try:
            claimed = _call_data(
                claim,
                {"job_id": job_id},
                [((job_id,), {}), ((job_key,), {})],
            )
        except Exception as exc:
            transition_errors.append(exc)
        else:
            if claimed is not None:
                job = claimed
            confirmed = _confirmed_processing_job(state, job_key, claimed or job)
            if confirmed is not None:
                return confirmed

    if transition_errors:
        detail = "; ".join(str(error) for error in transition_errors)
        raise SourceUnavailableError(
            "StateStore job could not be established in PROCESSING state; "
            f"transition attempts failed: {detail}"
        ) from transition_errors[-1]
    raise SourceUnavailableError(
        "StateStore job did not reach PROCESSING state; stage/publish is forbidden"
    )


def _confirmed_processing_job(state: Any, job_key: str, candidate: Any) -> Any | None:
    """Return a job only after its durable status is confirmed as PROCESSING."""

    get_job = getattr(state, "get_job", None)
    if callable(get_job):
        refreshed = _get_job_by_key(state, job_key)
        return refreshed if _status_of(refreshed) is ProcessingStatus.PROCESSING else None
    return candidate if _status_of(candidate) is ProcessingStatus.PROCESSING else None


def _get_job_by_key(state: Any, job_key: str) -> Any | None:
    method = getattr(state, "get_job", None)
    if method is None:
        return None
    try:
        return method(job_key=job_key)
    except TypeError:
        try:
            return method(job_key)
        except (KeyError, LookupError, TypeError, ValueError):
            return None
    except (KeyError, LookupError, ValueError):
        return None


def _stage_derived(
    writer: Any | None,
    source: SourceRef,
    entity_id: str,
    markdown: str,
    derived_ref: Any,
) -> Any:
    if writer is None:
        raise SourceUnavailableError("Drive writer is required for staged derivative writes")
    method = _first_method(
        writer,
        "write_staged_derived",
        "write_staged",
    )
    if method is None:
        raise SourceUnavailableError("Drive writer has no staged derivative write method")
    values = {
        "source_ref": source,
        "derived_ref": derived_ref,
        "entity_id": entity_id,
        "content": markdown,
        "body": markdown,
        "markdown": markdown,
        "staged_ref": derived_ref,
        "staged": True,
    }
    variants = [
        ((source, markdown), {}),
        ((entity_id, markdown), {}),
        ((source, entity_id, markdown), {}),
        ((entity_id, source, markdown), {}),
        ((markdown,), {}),
    ]
    return _call_data(method, values, variants)


def _validate_staged(writer: Any, staged_ref: Any, transcript: NormalizedTranscript) -> None:
    validate_normalized_transcript(transcript)
    method = _first_method(writer, "validate_derived", "validate_staged", "validate")
    if method is None:
        return
    values = {
        "staged_ref": staged_ref,
        "derived_ref": staged_ref,
        "content": transcript.to_markdown(),
        "body": transcript.to_markdown(),
        "derivative": transcript,
        "transcript": transcript,
        "normalized_transcript": transcript,
        "fingerprint": transcript.fingerprint,
        "source_hash": transcript.front_matter.source_hash,
        "source_version": transcript.front_matter.source_version,
    }
    result = _call_data(method, values, [((staged_ref,), {}), ((transcript.to_markdown(),), {})])
    if result is False:
        raise SourcePartialError("staged transcript failed validation")


def _publish_derived(
    writer: Any | None,
    source: SourceRef,
    entity_id: str,
    staged_ref: Any,
    markdown: str,
    derived_ref: Any,
) -> Any:
    if writer is None:
        raise SourceUnavailableError("Drive writer is required for atomic derivative publish")
    method = _first_method(
        writer,
        "replace_derived_file_atomically",
        "publish_staged_derived",
        "atomic_publish",
    )
    if method is None:
        raise SourceUnavailableError("Drive writer has no atomic derivative publish method")
    values = {
        "staged_ref": staged_ref,
        "source_ref": source,
        "derived_ref": derived_ref,
        "destination_ref": derived_ref or source,
        "final_ref": derived_ref or source,
        "entity_id": entity_id,
        "content": markdown,
        "body": markdown,
    }
    return _call_data(
        method,
        values,
        [
            ((staged_ref,), {}),
            ((staged_ref, derived_ref or source), {}),
            ((source, staged_ref), {}),
            ((entity_id, staged_ref), {}),
            ((source, entity_id, staged_ref), {}),
            ((staged_ref, markdown), {}),
        ],
    )


def _update_notion(
    writer: Any,
    entity_id: str,
    published_ref: Any,
    transcript: NormalizedTranscript,
    *,
    status: ProcessingStatus,
) -> Any:
    if writer is None:
        raise SourceUnavailableError("Notion writer is required for Session metadata updates")
    method = _first_method(
        writer,
        "update_session_transcript",
        "update_session_metadata",
        "update_session",
        "update_session_properties",
        "write_session_metadata",
        "update_transcript_metadata",
        "write_source_metadata_region",
        "update_properties",
    )
    if method is None:
        raise SourceUnavailableError("Notion writer has no session metadata method")
    notion_status = {
        ProcessingStatus.READY: "Ready",
        ProcessingStatus.PARTIAL: "Partial",
    }.get(status, status.value.title())
    patch = {
        "ID": entity_id,
        "Normalized Transcript": published_ref,
        "Recording Status": notion_status,
    }
    values = {
        "entity_id": entity_id,
        "session_id": entity_id,
        "target_db": "Sessions",
        "patch": patch,
        "properties": patch,
        "status": notion_status,
        "published_ref": published_ref,
        "normalized_transcript": published_ref,
    }
    return _call_data(
        method,
        values,
        [
            ((entity_id, patch), {}),
            (("Sessions", entity_id, patch), {}),
            ((entity_id, published_ref, notion_status), {}),
        ],
    )


def _create_processing_record(
    state: Any,
    job: Any,
    *,
    operation: str,
    processor_version: str,
    source_hash: str,
    output_ref: Any,
    status: ProcessingStatus,
) -> Any:
    method = getattr(state, "create_processing_record", None)
    if not callable(method):
        raise SourceUnavailableError("StateStore has no create_processing_record method")
    values = {
        "job_id": _job_id(job),
        "operation": operation,
        "processor_version": processor_version,
        "input_hash": source_hash,
        "output_ref_json": _jsonable_ref(output_ref),
        "status": status.value,
    }
    return _call_data(method, values, [((_job_id(job), operation, processor_version), {})])


def _complete_job(state: Any, job: Any, status: ProcessingStatus) -> Any:
    method = getattr(state, "complete_job", None)
    if not callable(method):
        method = getattr(state, "transition_job", None)
    if not callable(method):
        raise SourceUnavailableError("StateStore has no complete_job or transition_job method")
    return _call_data(
        method,
        {"job_id": _job_id(job), "status": status.value},
        [((_job_id(job), status.value), {}), ((_job_id(job),), {})],
    )


def _fail_job(state: Any | None, job: Any | None, exc: Exception) -> None:
    if state is None or job is None:
        return
    method = getattr(state, "fail_job", None)
    if method is not None:
        try:
            _call_data(
                method,
                {
                    "job_id": _job_id(job),
                    "error_class": _job_error_class(exc),
                    "last_error": str(exc),
                },
                [((_job_id(job),), {})],
            )
        except Exception:
            return


def _job_error_class(exc: Exception) -> ErrorClass:
    """Map an ingestion failure to the StateStore retry taxonomy."""

    if isinstance(exc, SourceUnavailableError):
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT


def _first_method(target: Any, *names: str) -> Any | None:
    for name in names:
        method = getattr(target, name, None)
        if callable(method):
            return method
    return None


def _require_ingest_collaborators(
    state_store: Any,
    drive_writer: Any,
    notion_writer: Any,
) -> None:
    """Reject an incomplete commit participant set before any write."""

    missing = [
        name
        for name, value in (
            ("state_store", state_store),
            ("drive_writer", drive_writer),
            ("notion_writer", notion_writer),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            "transcript ingestion requires state_store, drive_writer, and notion_writer; "
            f"missing: {', '.join(missing)}"
        )
    required_state_methods = [
        "register_source_file",
        "register_source_version",
        "allocate_entity",
        "create_job",
        "create_processing_record",
    ]
    missing_state_methods = [
        name
        for name in required_state_methods
        if not callable(getattr(state_store, name, None))
    ]
    if not callable(getattr(state_store, "complete_job", None)) and not callable(
        getattr(state_store, "transition_job", None)
    ):
        missing_state_methods.append("complete_job or transition_job")
    if missing_state_methods:
        raise ValueError(
            "StateStore is missing required transcript-ingest methods: "
            + ", ".join(missing_state_methods)
        )


def _validate_commit_participants(
    state_store: Any,
    drive_writer: Any,
    notion_writer: Any,
) -> None:
    """Ensure every commit-order step has a callable implementation."""

    if not callable(getattr(state_store, "create_processing_record", None)):
        raise SourceUnavailableError("StateStore has no create_processing_record method")
    if not callable(getattr(state_store, "complete_job", None)) and not callable(
        getattr(state_store, "transition_job", None)
    ):
        raise SourceUnavailableError("StateStore has no complete_job or transition_job method")
    if _first_method(drive_writer, "write_staged_derived", "write_staged") is None:
        raise SourceUnavailableError("Drive writer has no staged derivative write method")
    if (
        _first_method(
            drive_writer,
            "replace_derived_file_atomically",
            "publish_staged_derived",
            "atomic_publish",
        )
        is None
    ):
        raise SourceUnavailableError("Drive writer has no atomic derivative publish method")
    if (
        _first_method(
            notion_writer,
            "update_session_transcript",
            "update_session_metadata",
            "update_session",
            "update_session_properties",
            "write_session_metadata",
            "update_transcript_metadata",
            "write_source_metadata_region",
            "update_properties",
        )
        is None
    ):
        raise SourceUnavailableError("Notion writer has no session metadata method")


def _call_data(method: Any, values: dict[str, Any], variants: list[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
    """Call a fake/provider method using only the parameters it declares."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(*variants[0][0], **variants[0][1])
    parameters = signature.parameters
    accepts_kwargs = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters.values())
    keyword_values = {
        key: value
        for key, value in values.items()
        if accepts_kwargs or key in parameters
    }
    required = [
        parameter
        for parameter in parameters.values()
        if parameter.default is parameter.empty
        and parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY}
    ]
    # Named invocation is safest for fakes with semantic parameter names.
    if all(parameter.name in keyword_values for parameter in required) and not any(
        parameter.kind is parameter.POSITIONAL_ONLY for parameter in required
    ):
        try:
            signature.bind(**keyword_values)
        except TypeError:
            pass
        else:
            return method(**keyword_values)
    for args, kwargs in variants:
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return method(*args, **kwargs)
    # A deliberately useful error for adapter fakes with an incompatible
    # shape; never silently skip a commit-order step.
    raise TypeError(f"unsupported signature for {getattr(method, '__name__', method)!r}")


def _status_of(value: Any) -> ProcessingStatus | None:
    if value is None:
        return None
    raw = value.get("status") if isinstance(value, Mapping) else getattr(value, "status", value)
    try:
        return to_processing_status(raw)
    except (TypeError, ValueError):
        return None


def _job_id(value: Any) -> str:
    if value is None:
        return "job_transcript"
    if isinstance(value, str):
        return value
    identifier = getattr(value, "id", None)
    if isinstance(identifier, str) and identifier:
        return identifier
    if isinstance(value, dict):
        candidate = value.get("id", value.get("job_id"))
        if isinstance(candidate, str) and candidate:
            return candidate
    return str(value)


def _jsonable_ref(value: Any) -> Any:
    if isinstance(value, SourceRef):
        return {"provider": value.provider, "file_id": value.file_id, "web_url": value.web_url}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable_ref(item) for key, item in value.items()}
    return str(value)


__all__ = ["TRANSCRIPT_INGEST_OPERATION", "TranscriptIngestResult", "ingest_transcript"]
