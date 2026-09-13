"""Semester-scoped configuration for the v1.3 intake preview.

The legacy retrieval configuration and the intake configuration intentionally
have different authorities.  This module is the only resolver used by the
preview worker: it accepts a canonical Course Key, selects one current
semester registry/workspace, and returns an immutable snapshot of the IDs
that every subsequent operation must carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping as TypingMapping

from uls.domain.errors import UlsError
from uls.domain.ids import CourseKey, parse_course_key

from .schema import CourseStaticFolderCfg, SemesterRegistryCfg, SemesterWorkspaceCfg, UlsConfig


class IntakeConfigurationError(UlsError):
    """A missing, ambiguous, or malformed current-semester intake binding."""

    code = "CONFIGURATION_INVALID"


@dataclass(frozen=True)
class ResolvedCourseFolders:
    course_key: str
    course_folder_id: str
    recordings_folder_id: str
    materials_folder_id: str
    optional_upload_folder_id: str | None = None


@dataclass(frozen=True)
class ResolvedSemesterWorkspace:
    """All provider IDs required by one intake operation.

    ``MappingProxyType`` prevents a caller from mutating the resolved mapping
    after a request or job has captured its configuration fingerprint.
    """

    semester: str
    course_key: str
    provider: str
    drive_school_root_id: str
    drive_semester_folder_id: str
    drive_upload_folder_id: str
    notion_parent_page_id: str
    academic_courses_data_source_id: str
    sessions_data_source_id: str
    materials_data_source_id: str
    file_intake_data_source_id: str
    input_requests_data_source_id: str
    course_folders: ResolvedCourseFolders
    portal_page_id: str | None = None

    @property
    def course_folder_id(self) -> str:
        return self.course_folders.course_folder_id

    @property
    def recordings_folder_id(self) -> str:
        return self.course_folders.recordings_folder_id

    @property
    def materials_folder_id(self) -> str:
        return self.course_folders.materials_folder_id

    @property
    def optional_upload_folder_id(self) -> str | None:
        return self.course_folders.optional_upload_folder_id

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "semester": self.semester,
            "course_key": self.course_key,
            "drive": {
                "school_root_id": self.drive_school_root_id,
                "semester_folder_id": self.drive_semester_folder_id,
                "upload_folder_id": self.drive_upload_folder_id,
                "course_folder_id": self.course_folder_id,
                "recordings_folder_id": self.recordings_folder_id,
                "materials_folder_id": self.materials_folder_id,
                "optional_upload_folder_id": self.optional_upload_folder_id,
            },
            "notion": {
                "parent_page_id": self.notion_parent_page_id,
                "academic_courses_data_source_id": self.academic_courses_data_source_id,
                "sessions_data_source_id": self.sessions_data_source_id,
                "materials_data_source_id": self.materials_data_source_id,
                "file_intake_data_source_id": self.file_intake_data_source_id,
                "input_requests_data_source_id": self.input_requests_data_source_id,
                "portal_page_id": self.portal_page_id,
            },
        }


_ID_FIELDS = (
    "university_root_id",
    "semester_folder_id",
    "upload_folder_id",
    "course_folder_id",
    "recordings_folder_id",
    "materials_folder_id",
    "notion_parent_page_id",
    "academic_courses_data_source_id",
    "sessions_data_source_id",
    "materials_data_source_id",
    "file_intake_data_source_id",
    "input_requests_data_source_id",
)


def resolve_semester_workspace(config: UlsConfig, course_key: str) -> ResolvedSemesterWorkspace:
    """Resolve one current course to its explicit Drive/Notion workspace.

    No legacy/global lookup is attempted.  Empty or placeholder provider IDs
    are rejected before a worker can discover or mutate anything.
    """

    try:
        parsed = parse_course_key(course_key)
    except UlsError as exc:
        raise IntakeConfigurationError(f"invalid Course Key: {course_key!r}") from exc
    course = next((item for item in config.courses if item.course_key == course_key), None)
    if course is None:
        raise IntakeConfigurationError("Course Key is not registered in current configuration")
    if course.semester and course.semester != parsed.semester:
        raise IntakeConfigurationError("Course Key semester does not match course configuration")

    drive_rows = [
        row for row in config.google_drive.semester_registries if row.semester == parsed.semester
    ]
    notion_rows = [
        row for row in config.notion.semester_workspaces if row.semester == parsed.semester
    ]
    if len(drive_rows) != 1:
        raise IntakeConfigurationError(
            f"expected exactly one Drive semester registry for {parsed.semester}"
        )
    if len(notion_rows) != 1:
        raise IntakeConfigurationError(
            f"expected exactly one Notion semester workspace for {parsed.semester}"
        )
    drive = drive_rows[0]
    notion = notion_rows[0]
    course_folder = drive.course_folder_ids.get(course_key, "")
    static = drive.course_static_folder_ids.get(course_key)
    if not isinstance(static, CourseStaticFolderCfg):
        raise IntakeConfigurationError("course static Recordings/Materials IDs are required")
    values = {
        "university_root_id": config.google_drive.university_root_id,
        "semester_folder_id": drive.folder_id,
        "upload_folder_id": drive.upload_folder_id,
        "course_folder_id": course_folder,
        "recordings_folder_id": static.recordings_folder_id,
        "materials_folder_id": static.materials_folder_id,
        "notion_parent_page_id": notion.connection_settings_files_parent_id,
        "academic_courses_data_source_id": notion.academic_courses_data_source_id,
        "sessions_data_source_id": notion.sessions_data_source_id,
        "materials_data_source_id": notion.materials_data_source_id,
        "file_intake_data_source_id": notion.file_intake_data_source_id,
        "input_requests_data_source_id": notion.input_requests_data_source_id,
    }
    for name, value in values.items():
        _require_provider_id(value, name)
    optional_upload = drive.optional_course_upload_folder_ids.get(course_key)
    if optional_upload is not None:
        _require_provider_id(optional_upload, "optional_course_upload_folder_id")
    portal = notion.portal_page_ids.get(course_key)
    if portal is not None:
        _require_provider_id(portal, "portal_page_id")
    return ResolvedSemesterWorkspace(
        semester=parsed.semester,
        course_key=course_key,
        provider="google_drive",
        drive_school_root_id=values["university_root_id"],
        drive_semester_folder_id=values["semester_folder_id"],
        drive_upload_folder_id=values["upload_folder_id"],
        notion_parent_page_id=values["notion_parent_page_id"],
        academic_courses_data_source_id=values["academic_courses_data_source_id"],
        sessions_data_source_id=values["sessions_data_source_id"],
        materials_data_source_id=values["materials_data_source_id"],
        file_intake_data_source_id=values["file_intake_data_source_id"],
        input_requests_data_source_id=values["input_requests_data_source_id"],
        course_folders=ResolvedCourseFolders(
            course_key=course_key,
            course_folder_id=values["course_folder_id"],
            recordings_folder_id=values["recordings_folder_id"],
            materials_folder_id=values["materials_folder_id"],
            optional_upload_folder_id=optional_upload,
        ),
        portal_page_id=portal,
    )


def _require_provider_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value.strip() in {"...", "…"}:
        raise IntakeConfigurationError(f"{name} must be an explicit provider ID")
    if any(character.isspace() for character in value) or len(value.encode("utf-8")) > 512:
        raise IntakeConfigurationError(f"{name} is not a valid opaque provider ID")


def workspace_config_snapshot(workspace: ResolvedSemesterWorkspace) -> dict[str, object]:
    """Return the stable ID-only snapshot used by request/job fingerprints."""

    return workspace.as_dict()


def resolve_configured_semester(config: UlsConfig, semester: str) -> list[ResolvedSemesterWorkspace]:
    """Resolve all explicitly configured courses for one semester.

    The returned list is useful for discovery.  A course without a complete
    mapping fails the whole semester rather than being silently skipped.
    """

    keys = [course.course_key for course in config.courses if course.course_key.startswith(f"{semester}_")]
    if not keys:
        raise IntakeConfigurationError(f"no configured courses for semester {semester}")
    return [resolve_semester_workspace(config, key) for key in keys]


__all__ = [
    "IntakeConfigurationError",
    "ResolvedCourseFolders",
    "ResolvedSemesterWorkspace",
    "resolve_configured_semester",
    "resolve_semester_workspace",
    "workspace_config_snapshot",
]
