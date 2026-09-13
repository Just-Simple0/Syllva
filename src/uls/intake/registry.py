"""Validation of Root-provisioned semester Drive layout.

Static folders are identified by explicit IDs from the authoritative config.
This module only validates provider readbacks; it never creates, renames, or
adopts a folder by display name.
"""

from __future__ import annotations

from collections.abc import Iterable

from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, DriveWorkerPort
from uls.config.intake import ResolvedSemesterWorkspace
from uls.domain.errors import PolicyDeniedError, SourceUnavailableError, UlsError


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
        if file_id in seen:
            return metadata_by_id[file_id]
        metadata = port.read_metadata(file_id)
        if metadata.file_id != file_id or metadata.mime_type != DRIVE_FOLDER_MIME:
            raise StaticLayoutReconcileRequired("registered Drive static ID is not a folder")
        if metadata.trashed:
            raise SourceUnavailableError("registered Drive static folder is trashed")
        if metadata.owned_by_me is not True:
            raise PolicyDeniedError("registered Drive static folder is not USER owned")
        if metadata.drive_id is not None:
            raise PolicyDeniedError("registered Drive static folder is in a shared drive")
        if metadata.permission_count is None or metadata.owner_only is None:
            raise SourceUnavailableError("registered Drive static folder lacks owner permission readback")
        if metadata.owner_only is not True:
            raise PolicyDeniedError("registered Drive static folder is not solely USER owned")
        if metadata.is_publicly_shared is None:
            raise SourceUnavailableError("registered Drive static folder lacks privacy readback")
        if metadata.is_publicly_shared:
            raise PolicyDeniedError("registered Drive static folder has broad sharing")
        if metadata.can_edit is not True or metadata.can_move is not True:
            raise SourceUnavailableError("registered Drive static folder lacks worker capabilities")
        if parent_id is not None and metadata.parents != (parent_id,):
            raise StaticLayoutReconcileRequired(
                "registered Drive static folder has an unexpected parent"
            )
        seen.add(file_id)
        metadata_by_id[file_id] = metadata
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
