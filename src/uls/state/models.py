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


__all__ = [
    "Checkpoint",
    "EntityAllocation",
    "Job",
    "ProcessingRecord",
    "SourceFile",
    "SourceVersion",
]
