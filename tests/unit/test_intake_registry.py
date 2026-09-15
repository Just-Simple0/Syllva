from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, InMemoryDriveWorker
from uls.config.intake import ResolvedCourseFolders, ResolvedSemesterWorkspace
from uls.domain.errors import SourceUnavailableError
from uls.intake.registry import StaticLayoutReconcileRequired, validate_registered_drive_layout

pytestmark = pytest.mark.unit

DRIVE_ID = None  # personal (non-shared) drive


def _folder(file_id: str, parents: tuple[str, ...] = ()) -> DriveMetadata:
    """A private, USER-owned, non-shared, editable/movable folder --
    exactly the shape require_private_ownership() and the mime/trashed
    checks in read_folder() require."""
    return DriveMetadata(
        file_id=file_id,
        name=file_id,
        mime_type=DRIVE_FOLDER_MIME,
        parents=parents,
        trashed=False,
        owned_by_me=True,
        drive_id=DRIVE_ID,
        permission_roles=(("user", "owner"),),
        permission_count=1,
        owner_only=True,
        is_publicly_shared=False,
        can_edit=True,
        can_move=True,
    )


def _workspace(
    *,
    course_key: str = "2026-1_COMP319-001",
    school_root_id: str = "root-1",
    semester_folder_id: str = "semester-1",
    upload_folder_id: str = "upload-1",
    course_folder_id: str = "course-1",
    recordings_folder_id: str = "recordings-1",
    materials_folder_id: str = "materials-1",
    optional_upload_folder_id: str | None = None,
) -> ResolvedSemesterWorkspace:
    return ResolvedSemesterWorkspace(
        semester="2026-1",
        course_key=course_key,
        provider="google_drive",
        drive_school_root_id=school_root_id,
        drive_semester_folder_id=semester_folder_id,
        drive_upload_folder_id=upload_folder_id,
        notion_parent_page_id="notion-parent",
        academic_courses_data_source_id="ds-courses",
        sessions_data_source_id="ds-sessions",
        materials_data_source_id="ds-materials",
        file_intake_data_source_id="ds-intake",
        input_requests_data_source_id="ds-requests",
        course_folders=ResolvedCourseFolders(
            course_key=course_key,
            course_folder_id=course_folder_id,
            recordings_folder_id=recordings_folder_id,
            materials_folder_id=materials_folder_id,
            optional_upload_folder_id=optional_upload_folder_id,
        ),
    )


def _valid_tree(
    *,
    school_root_id: str = "root-1",
    semester_folder_id: str = "semester-1",
    upload_folder_id: str = "upload-1",
    course_folder_id: str = "course-1",
    recordings_folder_id: str = "recordings-1",
    materials_folder_id: str = "materials-1",
    optional_upload_folder_id: str | None = None,
) -> list[DriveMetadata]:
    files = [
        _folder(school_root_id),
        _folder(semester_folder_id, (school_root_id,)),
        _folder(upload_folder_id, (semester_folder_id,)),
        _folder(course_folder_id, (semester_folder_id,)),
        _folder(recordings_folder_id, (course_folder_id,)),
        _folder(materials_folder_id, (course_folder_id,)),
    ]
    if optional_upload_folder_id:
        files.append(_folder(optional_upload_folder_id, (course_folder_id,)))
    return files


def test_valid_layout_passes_and_reads_every_folder_once() -> None:
    port = InMemoryDriveWorker(_valid_tree())
    result = validate_registered_drive_layout(port, [_workspace()])
    assert result == {"workspaces": 1, "folders_read": 6}
    assert len([event for event in port.events if event[0] == "read"]) == 6


def test_shared_school_root_across_workspaces_is_not_a_reconcile_error() -> None:
    """Two courses legitimately sharing the same semester/school-root
    folders (same parent expectation each time) must not be rejected --
    only a role/parent mismatch is an error, not mere ID reuse."""
    files = _valid_tree()
    files += [
        _folder("course-2", ("semester-1",)),
        _folder("recordings-2", ("course-2",)),
        _folder("materials-2", ("course-2",)),
    ]
    port = InMemoryDriveWorker(files)
    workspaces = [
        _workspace(),
        _workspace(
            course_key="2026-1_COMP319-002",
            course_folder_id="course-2",
            recordings_folder_id="recordings-2",
            materials_folder_id="materials-2",
        ),
    ]
    result = validate_registered_drive_layout(port, workspaces)
    # root-1/semester-1/upload-1 are shared (read once each); course-2's
    # own three folders are new.
    assert result == {"workspaces": 2, "folders_read": 9}


def test_reused_id_with_conflicting_parent_expectation_is_rejected() -> None:
    """Regression: caching a folder's readback by ID must not skip the
    parent check on a cache hit. A misconfigured course folder pointed at
    the same ID as the school root must be rejected even though the ID
    was already 'seen' (as the root, which has no parent constraint)."""
    # Only one real Drive object exists at "root-1" (parents=()); the
    # course-folder role is misconfigured to reuse that same ID, so no
    # separate "course-1" object is created here at all.
    files = [
        _folder("root-1"),
        _folder("semester-1", ("root-1",)),
        _folder("upload-1", ("semester-1",)),
        _folder("recordings-1", ("root-1",)),
        _folder("materials-1", ("root-1",)),
    ]
    port = InMemoryDriveWorker(files)
    with pytest.raises(StaticLayoutReconcileRequired, match="unexpected parent"):
        validate_registered_drive_layout(port, [_workspace(course_folder_id="root-1")])


def test_reused_id_between_two_non_root_roles_is_rejected() -> None:
    """Same regression, but neither conflicting role is the parent-less
    root: the course folder is read first (real parent semester-1), then
    the recordings-folder role is misconfigured to the same ID while
    expecting the course folder itself as parent. The cached read must
    still be checked against that (different) expectation."""
    files = [
        _folder("root-1"),
        _folder("semester-1", ("root-1",)),
        _folder("upload-1", ("semester-1",)),
        _folder("course-1", ("semester-1",)),
        _folder("materials-1", ("course-1",)),
    ]
    port = InMemoryDriveWorker(files)
    with pytest.raises(StaticLayoutReconcileRequired, match="unexpected parent"):
        validate_registered_drive_layout(port, [_workspace(recordings_folder_id="course-1")])


def test_optional_upload_folder_under_wrong_parent_is_rejected() -> None:
    files = _valid_tree(optional_upload_folder_id="optional-1")
    # Put the optional upload folder under the semester folder instead of
    # its registered course folder.
    files = [f for f in files if f.file_id != "optional-1"]
    files.append(_folder("optional-1", ("semester-1",)))
    port = InMemoryDriveWorker(files)
    with pytest.raises(StaticLayoutReconcileRequired, match="unexpected parent"):
        validate_registered_drive_layout(port, [_workspace(optional_upload_folder_id="optional-1")])


def test_trashed_static_folder_is_rejected() -> None:
    files = _valid_tree()
    files = [f for f in files if f.file_id != "materials-1"]
    files.append(
        DriveMetadata(
            file_id="materials-1", name="materials-1", mime_type=DRIVE_FOLDER_MIME,
            parents=("course-1",), trashed=True, owned_by_me=True, drive_id=DRIVE_ID,
            permission_roles=(("user", "owner"),), permission_count=1, owner_only=True,
            is_publicly_shared=False, can_edit=True, can_move=True,
        )
    )
    port = InMemoryDriveWorker(files)
    with pytest.raises(SourceUnavailableError):
        validate_registered_drive_layout(port, [_workspace()])


def test_folder_in_shared_drive_is_rejected() -> None:
    import dataclasses

    files = _valid_tree()
    files = [f for f in files if f.file_id != "course-1"]
    shared = dataclasses.replace(_folder("course-1", ("semester-1",)), drive_id="shared-drive-xyz")
    files.append(shared)
    port = InMemoryDriveWorker(files)
    from uls.domain.errors import PolicyDeniedError

    with pytest.raises(PolicyDeniedError, match="shared drive"):
        validate_registered_drive_layout(port, [_workspace()])


def test_empty_workspace_iterable_is_rejected() -> None:
    port = InMemoryDriveWorker(_valid_tree())
    with pytest.raises(SourceUnavailableError, match="no registered semester workspace"):
        validate_registered_drive_layout(port, [])
