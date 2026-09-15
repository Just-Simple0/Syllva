"""Validation of Root-provisioned semester Drive layout.

Static folders are identified by explicit IDs from the authoritative config.
This module only validates provider readbacks; it never creates, renames, or
adopts a folder by display name.
"""

from __future__ import annotations

from collections.abc import Iterable

from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    DriveMetadata,
    DriveWorkerPort,
    require_private_ownership,
)
from uls.config.intake import ResolvedSemesterWorkspace
from uls.domain.errors import SourceUnavailableError, UlsError


class StaticLayoutReconcileRequired(UlsError):
    """A registered static folder no longer matches its recorded topology."""

    code = "RECONCILE_REQUIRED"


def validate_registered_drive_layout(
    port: DriveWorkerPort,
    workspaces: Iterable[ResolvedSemesterWorkspace],
) -> dict[str, int]:
    """Read back every registered static folder and verify exact containment.

    The school root has no configured parent.  Every lower level must have one
    exact parent, be a folder, remain untrashed, and be USER owned.  The
    optional course upload folder is accepted only under its registered course
    folder; an absent optional ID is not discovered.
    """

    if not port.capabilities.metadata_readback:
        raise NotImplementedError(
            port.capabilities.reason or "Drive metadata readback is unsupported"
        )
    rows = tuple(workspaces)
    if not rows:
        raise SourceUnavailableError("no registered semester workspace")
    seen: set[str] = set()
    metadata_by_id: dict[str, DriveMetadata] = {}
    root_drive_id: str | None = None

    def read_folder(file_id: str, *, parent_id: str | None = None) -> DriveMetadata:
        # A previously-seen ID reuses its cached readback (no redundant
        # provider round-trip), but the parent check below always runs
        # against that cached metadata rather than short-circuiting.
        # Otherwise the same file ID could satisfy two roles that expect
        # different parents -- e.g. a course folder misconfigured to the
        # same ID as the school root -- without ever being caught, because
        # only the first role's parent constraint would ever be checked.
        metadata = metadata_by_id.get(file_id)
        if metadata is None:
            metadata = port.read_metadata(file_id)
            if metadata.file_id != file_id or metadata.mime_type != DRIVE_FOLDER_MIME:
                raise StaticLayoutReconcileRequired("registered Drive static ID is not a folder")
            if metadata.trashed:
                raise SourceUnavailableError("registered Drive static folder is trashed")
            require_private_ownership(metadata, context="registered Drive static folder")
            seen.add(file_id)
            metadata_by_id[file_id] = metadata
        if parent_id is not None and metadata.parents != (parent_id,):
            raise StaticLayoutReconcileRequired(
                "registered Drive static folder has an unexpected parent"
            )
        return metadata

    for workspace in rows:
        root = read_folder(workspace.drive_school_root_id)
        current_root_drive_id = root.drive_id
        if root_drive_id is None:
            root_drive_id = current_root_drive_id
        elif current_root_drive_id != root_drive_id:
            raise StaticLayoutReconcileRequired("registered Drive roots use different drives")
        read_folder(
            workspace.drive_semester_folder_id,
            parent_id=workspace.drive_school_root_id,
        )
        read_folder(
            workspace.drive_upload_folder_id,
            parent_id=workspace.drive_semester_folder_id,
        )
        read_folder(workspace.course_folder_id, parent_id=workspace.drive_semester_folder_id)
        read_folder(workspace.recordings_folder_id, parent_id=workspace.course_folder_id)
        read_folder(workspace.materials_folder_id, parent_id=workspace.course_folder_id)
        if workspace.optional_upload_folder_id:
            read_folder(
                workspace.optional_upload_folder_id,
                parent_id=workspace.course_folder_id,
            )
        for metadata in metadata_by_id.values():
            if metadata.drive_id != root_drive_id:
                raise StaticLayoutReconcileRequired("registered Drive folder crossed drive boundary")
    return {"workspaces": len(rows), "folders_read": len(seen)}


__all__ = ["StaticLayoutReconcileRequired", "validate_registered_drive_layout"]
