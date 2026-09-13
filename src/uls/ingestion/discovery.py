"""Course-scoped intake discovery and metadata change detection."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, DriveWorkerPort
from uls.config.intake import ResolvedSemesterWorkspace
from uls.domain.errors import PolicyDeniedError, SourceUnavailableError
from uls.intake.identity import canonical_json, sha256_hex
from uls.intake.models import FileKind, IntakeStatus
from uls.state.models import IntakeItem

from .classifier import SourceKind, classify_source_detailed


@dataclass(frozen=True)
class IntakeDiscoveryResult:
    items: tuple[IntakeItem, ...]
    listing_fingerprint: str
    inspected_folders: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.items)


def discover_intake(
    port: DriveWorkerPort,
    workspace: ResolvedSemesterWorkspace,
    state: Any,
    *,
    config_fingerprint: str,
    provider: str = "google_drive",
    max_files: int = 10_000,
    optional_course_uploads: Mapping[str, str] | None = None,
) -> IntakeDiscoveryResult:
    """Commit observations from the registered upload folders before routing."""

    folder_to_course: dict[str, str | None] = {workspace.drive_upload_folder_id: None}
    if optional_course_uploads:
        for course_key, folder_id in optional_course_uploads.items():
            if folder_id:
                folder_to_course[folder_id] = course_key
    elif workspace.optional_upload_folder_id:
        folder_to_course[workspace.optional_upload_folder_id] = workspace.course_key
    records: list[DriveMetadata] = []
    for folder_id in sorted(folder_to_course):
        records.extend(
            item for item in port.list_folder(folder_id) if item.mime_type != DRIVE_FOLDER_MIME
        )
    if len(records) > max_files:
        raise ValueError("Drive intake listing exceeds bounded limit")
    for record in records:
        if record.is_publicly_shared is None:
            raise SourceUnavailableError("Drive source listing lacks privacy readback")
        if record.is_publicly_shared:
            raise PolicyDeniedError("Drive source has broad sharing and cannot enter intake")
    # A file reachable through two explicitly registered folders is a routing
    # conflict.  Preserve one identity and surface the conflict in candidates.
    by_identity: dict[str, list[DriveMetadata]] = {}
    for record in records:
        by_identity.setdefault(record.identity, []).append(record)
    fingerprint = sha256_hex(
        [
            "intake.drive-listing.v1",
            provider,
            workspace.semester,
            [
                [
                    record.file_id,
                    record.name,
                    record.mime_type,
                    list(record.parents),
                    record.modified_time,
                    record.size,
                    record.md5_checksum,
                ]
                for record in sorted(records, key=lambda value: value.file_id)
            ],
            config_fingerprint,
        ]
    )
    result: list[IntakeItem] = []
    for identity, values in sorted(by_identity.items()):
        record = values[0]
        parent_id = record.parent_id or ""
        explicit_course = folder_to_course.get(parent_id)
        observed_kind = _observed_kind(record)
        source_hash = _metadata_source_hash(record)
        prior = state.get_intake_item_by_provider_file(provider, record.file_id)
        version = prior.source_version if prior is not None else 1
        if prior is not None and prior.source_hash != source_hash:
            version = prior.source_version + 1
        candidate_values: list[dict[str, str]] = []
        if explicit_course:
            candidate_values.append({"course_key": explicit_course, "reason": "registered upload folder"})
        if len(values) > 1:
            candidate_values.append({"reason": "same file observed through multiple registered parents"})
        intake_id = prior.intake_id if prior is not None else _intake_id(provider, record.file_id)
        duplicates = sorted(
            set(_same_hash_other_files(state, provider, source_hash, record.file_id))
            | {
                candidate.file_id
                for candidate in records
                if candidate.file_id != record.file_id
                and _metadata_source_hash(candidate) == source_hash
            }
        )
        if duplicates:
            candidate_values.append({"reason": "same content under another provider file ID", "file_ids": ",".join(duplicates)})
        item = state.upsert_intake_item(
            intake_id=intake_id,
            provider=provider,
            provider_file_id=record.file_id,
            semester=workspace.semester,
            original_parent_id=parent_id,
            observed_parent_id=parent_id,
            original_name=record.name,
            mime_type=record.mime_type,
            source_hash=source_hash,
            source_version=version,
            observed_kind=observed_kind,
            course_candidates_json=candidate_values,
            # Every newly observed supported/unknown file still needs an
            # explicit USER routing decision (at minimum course/kind/details);
            # discovery must therefore make it eligible for a draft request.
            status=IntakeStatus.NEEDS_INPUT.value,
            content_status="Pending",
        )
        state.record_intake_observation(
            intake_id=item.intake_id,
            source_hash=source_hash,
            source_version=version,
            metadata={
                "provider": provider,
                "provider_file_id": record.file_id,
                "name": record.name,
                "mime_type": record.mime_type,
                "parent_id": parent_id,
                "modified_time": record.modified_time,
                "size": record.size,
                "kind": observed_kind,
                "config_fingerprint": config_fingerprint,
            },
        )
        if duplicates:
            item = state.update_intake_item(
                item.intake_id,
                status=IntakeStatus.NEEDS_INPUT.value,
                last_error_code="DUPLICATE_CANDIDATE",
                last_error="Same content has another stable provider/file identity; choose explicitly.",
            )
        elif observed_kind == FileKind.UNSUPPORTED.value:
            item = state.update_intake_item(
                item.intake_id,
                status=IntakeStatus.UNSUPPORTED.value,
                content_status="Unavailable",
                last_error_code="UNSUPPORTED_FORMAT",
                last_error="The file format is not part of the current deterministic intake path.",
            )
        result.append(item)
    return IntakeDiscoveryResult(tuple(result), fingerprint, tuple(sorted(folder_to_course)))


def _observed_kind(record: DriveMetadata) -> str:
    classification = classify_source_detailed(record.name, mime_type=record.mime_type)
    if record.mime_type.casefold() == "application/pdf":
        return FileKind.MATERIAL_PDF.value
    if classification.kind is SourceKind.TRANSCRIPT:
        return FileKind.TRANSCRIPT.value
    if record.mime_type.casefold() in {"text/plain", "text/markdown", "text/vtt", "application/vnd.apple.mpegurl"}:
        return FileKind.TRANSCRIPT.value
    if record.mime_type.casefold().startswith(("audio/", "video/")):
        return FileKind.UNSUPPORTED.value
    return FileKind.UNKNOWN.value


def _metadata_source_hash(record: DriveMetadata) -> str:
    if record.md5_checksum:
        return "md5:" + record.md5_checksum
    return "sha256:metadata-" + sha256_hex(
        [record.file_id, record.name, record.mime_type, list(record.parents), record.modified_time, record.size]
    )


def _same_hash_other_files(state: Any, provider: str, source_hash: str, file_id: str) -> list[str]:
    return sorted(
        item.provider_file_id
        for item in state.list_intake_items(limit=10_000)
        if item.provider == provider and item.provider_file_id != file_id and item.source_hash == source_hash
    )


def _intake_id(provider: str, file_id: str) -> str:
    return "intake-" + sha256_hex(["intake.item.v1", provider, file_id])[:32]


__all__ = ["IntakeDiscoveryResult", "discover_intake"]
