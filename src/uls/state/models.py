"""Dataclasses for the durable rows described by implementation spec §8.

The database stores status values as their stable upper-case wire spelling.  The
row objects accept either that spelling or :class:`uls.domain.enums.JobStatus`
and normalize it at the boundary so callers can use either representation.
"""

from __future__ import annotations

from dataclasses import dataclass

from uls.domain.enums import JobStatus, to_processing_status


@dataclass
class Job:
    """A durable orchestration job (the ``jobs`` table)."""

    id: str
    job_key: str
    operation: str
    stage: str
    status: JobStatus | str = JobStatus.PENDING
    course_key: str | None = None
    source_file_id: str | None = None
    source_hash: str | None = None
    target_entity_id: str | None = None
    attempt_count: int = 0
    error_class: str | None = None
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None

    def __post_init__(self) -> None:
        self.status = to_processing_status(self.status)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.READY,
            JobStatus.PARTIAL,
            JobStatus.NEEDS_REVIEW,
            JobStatus.FAILED,
        }


@dataclass
class SourceFile:
    """A provider file identity and its current durable state."""

    source_file_id: str
    provider: str
    provider_file_id: str
    course_key: str
    source_kind: str
    original_filename: str | None = None
    current_hash: str | None = None
    canonical_entity_id: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""


@dataclass
class SourceVersion:
    """An immutable hash/version record for a source file."""

    id: str
    source_file_id: str
    source_hash: str
    version: int
    canonical_entity_id: str
    source_ref_json: str
    first_seen_at: str = ""
    processor_version: str | None = None


@dataclass
class ProcessingRecord:
    """A durable processing attempt/result record."""

    id: str
    job_id: str
    operation: str
    processor_version: str
    input_hash: str | None = None
    output_ref_json: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    status: JobStatus | str = JobStatus.PENDING

    def __post_init__(self) -> None:
        self.status = to_processing_status(self.status)


@dataclass
class Checkpoint:
    """A provider/scope cursor."""

    provider: str
    scope: str
    checkpoint_value: str
    updated_at: str = ""


@dataclass
class EntityAllocation:
    """The next sequence reserved for a course/entity-type pair."""

    course_key: str
    entity_type: str
    next_sequence: int


@dataclass(frozen=True)
class SemesterRegistration:
    semester: str
    config_fingerprint: str
    workspace_fingerprint: str
    drive_static_ids_json: str
    notion_resolved_ids_json: str
    provider_account_binding_id: str
    captured_at: str = ""


@dataclass(frozen=True)
class IntakeItem:
    intake_id: str
    provider: str
    provider_file_id: str
    semester: str
    original_parent_id: str
    observed_parent_id: str
    original_name: str
    mime_type: str
    source_hash: str
    source_version: int
    status: str = "OBSERVED"
    observed_kind: str = "UNKNOWN"
    course_candidates_json: str = "[]"
    selected_course_key: str | None = None
    selected_kind: str | None = None
    file_intake_page_id: str | None = None
    input_request_page_id: str | None = None
    request_revision_hash: str | None = None
    plan_revision: str | None = None
    pending_request_key: str | None = None
    canonical_entity_id: str | None = None
    canonical_source_json: str | None = None
    content_status: str = "Pending"
    last_error_code: str | None = None
    last_error: str | None = None
    last_successful_stage: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""


@dataclass(frozen=True)
class IntakeObservation:
    id: str
    intake_id: str
    source_hash: str
    source_version: int
    metadata_json: str
    observed_at: str = ""


@dataclass(frozen=True)
class RequestReceipt:
    receipt_id: str
    provider: str
    input_requests_data_source_id: str
    request_key: str
    request_revision_hash: str
    provider_page_id: str | None = None
    normalized_user_hash: str | None = None
    target_snapshot_hash: str = ""
    submitted_at: str | None = None
    plan_revision: str | None = None
    state: str = "Draft"
    workspace_fingerprint: str = ""
    request_type: str | None = None
    intake_ids_json: str | None = None


@dataclass(frozen=True)
class IntakePlan:
    plan_id: str
    intake_id: str
    request_revision_hash: str
    plan_revision: str
    resolved_workspace_fingerprint: str
    target_snapshot_json: str
    plan_hash: str
    status: str = "PLANNED"
    created_at: str = ""


@dataclass(frozen=True)
class ProviderWriteAttempt:
    attempt_id: str
    operation: str
    operation_key: str
    provider: str
    target_id: str | None
    prewrite_committed_at: str
    reservation_id: str | None = None
    stage: str | None = None
    dispatched_at: str | None = None
    response_state: str = "PREPARED"
    readback_json: str | None = None
    error_class: str | None = None


@dataclass(frozen=True)
class EntityReservation:
    reservation_id: str
    intake_id: str
    entity_kind: str
    entity_app_id: str
    parent_folder_id: str
    marker_key: str
    state: str
    plan_revision: str
    source_file_id: str | None = None
    receipt_id: str | None = None
    plan_hash: str | None = None
    source_snapshot_hash: str | None = None
    target_snapshot_hash: str | None = None
    operation_key: str | None = None
    created_at: str = ""
    updated_at: str = ""
    released_at: str | None = None


@dataclass(frozen=True)
class StudyNoteHead:
    provider: str
    session_provider_page_id: str
    course_key: str
    session_id: str
    current_request_id: str
    current_receipt_id: str
    generation: int
    active: int
    receipt_hash: str
    evidence_mode: str | None = None
    selected_materials_json: str = "[]"
    inactive_reason: str | None = None
    current_note_key: str | None = None
    current_attempt_no: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class NoteJob:
    note_key: str
    course_key: str
    session_id: str
    evidence_manifest_hash: str
    learner_request_hash: str
    template_version: str
    generator_config_version: str
    current_attempt_no: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class NoteAttempt:
    note_key: str
    attempt_no: int
    state: str
    seed_artifact_id: str | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    last_successful_stage: str | None = None
    error_class: str | None = None
    error_code: str | None = None
    created_at: str = ""
    updated_at: str = ""
    terminal_at: str | None = None


@dataclass(frozen=True)
class NoteRequestReference:
    reference_id: str
    receipt_id: str
    provider_request_id: str
    note_key: str
    attempt_no: int
    head_generation: int
    state: str = "ACTIVE"
    created_at: str = ""
    updated_at: str = ""
    ended_at: str | None = None


@dataclass(frozen=True)
class NoteArtifact:
    artifact_id: str
    note_key: str
    output_identity: str
    output_hash: str
    manifest_hash: str
    writer_version: str
    state: str
    ai_region_id: str | None = None
    ai_block_ids_json: str = "[]"
    last_publish_hash: str | None = None
    created_at: str = ""
    verified_at: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class SessionSourceBinding:
    binding_id: str
    course_key: str
    session_id: str
    provider: str
    provider_file_id: str
    reservation_id: str
    state: str
    created_at: str = ""


__all__ = [
    "Checkpoint",
    "EntityAllocation",
    "EntityReservation",
    "IntakeItem",
    "IntakeObservation",
    "IntakePlan",
    "Job",
    "NoteArtifact",
    "NoteAttempt",
    "NoteJob",
    "NoteRequestReference",
    "ProcessingRecord",
    "ProviderWriteAttempt",
    "RequestReceipt",
    "SemesterRegistration",
    "SessionSourceBinding",
    "SourceFile",
    "SourceVersion",
    "StudyNoteHead",
]
