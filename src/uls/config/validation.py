"""Configuration checks used by ``uls doctor`` (spec §5/§38)."""

from __future__ import annotations

import math
import types
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from uls.domain.errors import UlsError
from uls.domain.ids import parse_course_key
from uls.retrieval.authority import SUPPORTED_MATERIAL_SOURCE_CLASSES

from .errors import ConfigurationError
from .schema import CourseStaticFolderCfg, SemesterRegistryCfg, SemesterWorkspaceCfg, UlsConfig

MAX_CONFIG_TTL_SECONDS = 24 * 60 * 60


def validate_config(cfg: UlsConfig) -> list[str]:
    """Return human-readable configuration problems, without raising them."""

    problems: list[str] = []
    if not isinstance(cfg, UlsConfig):
        return ["config must be an UlsConfig instance"]

    # Validate every bool declared by the typed schema before applying the
    # field-specific security defaults below.  YAML's ``"false"`` is a
    # string, not a boolean, and must never be accepted as an opt-out.
    _validate_declared_bool_fields(cfg, "", problems)

    _validate_required_bool(cfg.mcp.read_only, "mcp.read_only", True, problems)
    _validate_required_bool(
        cfg.remote_mcp.public_unauthenticated,
        "remote_mcp.public_unauthenticated",
        False,
        problems,
    )

    behavior_path = cfg.behavior_contract.path
    if not isinstance(behavior_path, str) or not behavior_path:
        problems.append("behavior_contract.path is required")
    elif not Path(behavior_path).expanduser().exists():
        problems.append(f"behavior_contract.path does not exist: {behavior_path}")

    if not cfg.courses:
        problems.append("courses must not be empty")
    else:
        seen_course_keys: set[str] = set()
        for index, course in enumerate(cfg.courses):
            prefix = f"courses[{index}]"
            if not course.course_key:
                problems.append(f"{prefix}.course_key is required")
                continue
            try:
                parsed = parse_course_key(course.course_key)
            except UlsError as exc:
                problems.append(f"{prefix}.course_key is invalid: {exc.message}")
                continue
            if course.course_key in seen_course_keys:
                problems.append(f"{prefix}.course_key is duplicated: {course.course_key}")
            seen_course_keys.add(course.course_key)
            if course.code and course.code != parsed.code:
                problems.append(f"{prefix}.code does not match course_key")
            if course.section and course.section != parsed.section:
                problems.append(f"{prefix}.section does not match course_key")
            if course.semester and course.semester != parsed.semester:
                problems.append(f"{prefix}.semester does not match course_key")

    if cfg.system.state_backend != "sqlite":
        problems.append("system.state_backend must be sqlite in v1.2")
    if cfg.system.ephemeral_backend != "memory":
        problems.append("system.ephemeral_backend must be memory in v1.2")
    if (
        isinstance(cfg.worker.poll_interval_minutes, bool)
        or not isinstance(cfg.worker.poll_interval_minutes, (int, float))
        or cfg.worker.poll_interval_minutes <= 0
    ):
        problems.append("worker.poll_interval_minutes must be positive")
    if (
        not isinstance(cfg.normalization.processor_version, str)
        or not cfg.normalization.processor_version
    ):
        problems.append("normalization.processor_version is required")
    if not isinstance(cfg.mcp.mode, str) or cfg.mcp.mode not in {"local", "remote"}:
        problems.append("mcp.mode must be local or remote")
    remote_enabled = type(cfg.remote_mcp.enabled) is bool and cfg.remote_mcp.enabled
    if remote_enabled and (
        not isinstance(cfg.remote_mcp.auth_mode, str)
        or cfg.remote_mcp.auth_mode not in {"oauth_or_bearer", "oidc", "bearer"}
    ):
        problems.append("remote_mcp.auth_mode is not allowed when remote_mcp.enabled")
    if remote_enabled:
        oidc = cfg.remote_mcp.oidc
        should_validate_oidc = cfg.remote_mcp.auth_mode == "oidc" or (
            cfg.remote_mcp.auth_mode == "oauth_or_bearer" and bool(oidc.issuer)
        )
        if should_validate_oidc:
            if (
                not isinstance(oidc.issuer, str)
                or not oidc.issuer.startswith("https://")
                or oidc.issuer.endswith("/")
            ):
                # Must mirror JwksKeyManager.__init__'s trailing-slash
                # rejection exactly: otherwise validate_config()/doctor()
                # (without --live) can call a trailing-slash issuer
                # "valid" while the actual mcp remote dispatch path
                # unconditionally constructs a JwksKeyManager and fails
                # immediately, a doctor/runtime readiness divergence.
                problems.append("remote_mcp.oidc.issuer must be a valid HTTPS URL")
            if not isinstance(oidc.audience, str) or not oidc.audience:
                problems.append("remote_mcp.oidc.audience is required when OIDC is configured")
            if not (oidc.authorized_subject or oidc.authorized_email):
                problems.append("remote_mcp.oidc requires at least authorized_subject or authorized_email")
            if oidc.jwks_uri and (not isinstance(oidc.jwks_uri, str) or not oidc.jwks_uri.startswith("https://")):
                problems.append("remote_mcp.oidc.jwks_uri must be a valid HTTPS URL")
            if isinstance(oidc.leeway_seconds, bool) or not isinstance(oidc.leeway_seconds, int) or not (0 <= oidc.leeway_seconds <= 120):
                problems.append("remote_mcp.oidc.leeway_seconds must be an integer between 0 and 120")

    _validate_ttl(
        cfg.retrieval.context_ttl_seconds,
        "retrieval.context_ttl_seconds",
        problems,
    )
    _validate_ttl(
        cfg.retrieval.resolution_ttl_seconds,
        "retrieval.resolution_ttl_seconds",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_candidate_entities,
        "retrieval.max_candidate_entities",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_candidate_chunks,
        "retrieval.max_candidate_chunks",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_evidence_items,
        "retrieval.max_evidence_items",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_chars_per_item,
        "retrieval.max_chars_per_item",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_total_chars,
        "retrieval.max_total_chars",
        problems,
    )
    _validate_positive_number(
        cfg.retrieval.max_followup_chunks,
        "retrieval.max_followup_chunks",
        problems,
    )
    mapping = cfg.retrieval.material_type_source_class
    if not isinstance(mapping, Mapping):
        problems.append("retrieval.material_type_source_class must be a mapping")
    else:
        for material_type, source_class in mapping.items():
            if not isinstance(material_type, str) or not material_type.strip():
                problems.append(
                    "retrieval.material_type_source_class keys must be non-empty strings"
                )
            if (
                not isinstance(source_class, str)
                or source_class not in SUPPORTED_MATERIAL_SOURCE_CLASSES
            ):
                problems.append(
                    "retrieval.material_type_source_class values must be one of: "
                    + ", ".join(sorted(SUPPORTED_MATERIAL_SOURCE_CLASSES))
                )
    if (
        isinstance(cfg.behavior_contract.version, bool)
        or not isinstance(cfg.behavior_contract.version, int)
        or cfg.behavior_contract.version < 1
    ):
        problems.append("behavior_contract.version must be positive")
    _validate_intake_config(cfg, problems)
    return problems


def _validate_intake_config(cfg: UlsConfig, problems: list[str]) -> None:
    """Validate optional v1.3 preview configuration when it is supplied.

    Empty legacy configurations remain valid for the existing retrieval and
    transcript paths.  Once a semester preview row is present, its IDs and
    every configured course binding are fail-closed and semester scoped.
    """

    drive_rows = cfg.google_drive.semester_registries
    notion_rows = cfg.notion.semester_workspaces
    if not isinstance(drive_rows, list) or not isinstance(notion_rows, list):
        problems.append("intake semester configuration must be lists")
        return
    _validate_unique_semester_rows(drive_rows, "google_drive.semester_registries", problems)
    _validate_unique_semester_rows(notion_rows, "notion.semester_workspaces", problems)
    if not drive_rows and not notion_rows:
        return
    if len(drive_rows) != len(notion_rows):
        problems.append("Drive and Notion intake semester rows must cover the same semesters")
    drive_by_semester = {row.semester: row for row in drive_rows if isinstance(row, SemesterRegistryCfg)}
    notion_by_semester = {row.semester: row for row in notion_rows if isinstance(row, SemesterWorkspaceCfg)}
    if set(drive_by_semester) != set(notion_by_semester):
        problems.append("Drive and Notion intake semester rows must match exactly")
    for index, row in enumerate(drive_rows):
        prefix = f"google_drive.semester_registries[{index}]"
        if not isinstance(row, SemesterRegistryCfg):
            problems.append(f"{prefix} must be a SemesterRegistryCfg")
            continue
        _validate_opaque_id(row.folder_id, f"{prefix}.folder_id", problems)
        _validate_opaque_id(row.upload_folder_id, f"{prefix}.upload_folder_id", problems)
        _validate_opaque_id_map(row.course_folder_ids, f"{prefix}.course_folder_ids", problems)
        _validate_opaque_id_map(
            row.optional_course_upload_folder_ids,
            f"{prefix}.optional_course_upload_folder_ids",
            problems,
        )
        if not isinstance(row.course_static_folder_ids, dict):
            problems.append(f"{prefix}.course_static_folder_ids must be a mapping")
        else:
            for key, folders in row.course_static_folder_ids.items():
                if not isinstance(folders, CourseStaticFolderCfg):
                    problems.append(f"{prefix}.course_static_folder_ids[{key!r}] is invalid")
                    continue
                _validate_opaque_id(
                    folders.recordings_folder_id,
                    f"{prefix}.course_static_folder_ids[{key!r}].recordings_folder_id",
                    problems,
                )
                _validate_opaque_id(
                    folders.materials_folder_id,
                    f"{prefix}.course_static_folder_ids[{key!r}].materials_folder_id",
                    problems,
                )
        configured_keys = {
            course.course_key
            for course in cfg.courses
            if course.course_key.startswith(f"{row.semester}_")
        }
        if set(row.course_folder_ids) != configured_keys:
            problems.append(f"{prefix}.course_folder_ids must map every configured course exactly")
        if set(row.course_static_folder_ids) != configured_keys:
            problems.append(
                f"{prefix}.course_static_folder_ids must map every configured course exactly"
            )
        if any(not key.startswith(f"{row.semester}_") for key in row.course_folder_ids):
            problems.append(f"{prefix} contains a course from another semester")
    for index, row in enumerate(notion_rows):
        prefix = f"notion.semester_workspaces[{index}]"
        if not isinstance(row, SemesterWorkspaceCfg):
            problems.append(f"{prefix} must be a SemesterWorkspaceCfg")
            continue
        for name in (
            "connection_settings_files_parent_id",
            "academic_courses_data_source_id",
            "sessions_data_source_id",
            "materials_data_source_id",
            "file_intake_data_source_id",
            "input_requests_data_source_id",
        ):
            _validate_opaque_id(getattr(row, name), f"{prefix}.{name}", problems)
        _validate_opaque_id_map(row.portal_page_ids, f"{prefix}.portal_page_ids", problems, allow_empty=True)
        if any(not key.startswith(f"{row.semester}_") for key in row.portal_page_ids):
            problems.append(f"{prefix} contains a portal from another semester")


def _validate_unique_semester_rows(rows: Sequence[object], name: str, problems: list[str]) -> None:
    seen: set[object] = set()
    for index, row in enumerate(rows):
        semester = getattr(row, "semester", None)
        if not isinstance(semester, str) or not semester:
            problems.append(f"{name}[{index}].semester is required")
            continue
        try:
            # A semester is the first component of a valid Course Key.  Keep
            # this validation aligned with the frozen identifier grammar.
            parse_course_key(f"{semester}_LMS101-001")
        except UlsError:
            problems.append(f"{name}[{index}].semester is invalid")
        if semester in seen:
            problems.append(f"{name} contains duplicate semester {semester}")
        seen.add(semester)


def _validate_opaque_id(value: object, name: str, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip() or value.strip() in {"...", "…"}:
        problems.append(f"{name} must be an explicit provider ID")
    elif any(character.isspace() for character in value) or len(value.encode("utf-8")) > 512:
        problems.append(f"{name} is not a valid opaque provider ID")


def _validate_opaque_id_map(
    value: object,
    name: str,
    problems: list[str],
    *,
    allow_empty: bool = True,
) -> None:
    if not isinstance(value, dict):
        problems.append(f"{name} must be a mapping")
        return
    if not allow_empty and not value:
        problems.append(f"{name} must not be empty")
    for key, identifier in value.items():
        if not isinstance(key, str) or not key:
            problems.append(f"{name} keys must be non-empty strings")
        _validate_opaque_id(identifier, f"{name}[{key!r}]", problems)


def _validate_bool(value: object, name: str, problems: list[str]) -> None:
    if type(value) is not bool:
        problems.append(f"{name} must be a boolean")


def _validate_required_bool(
    value: object,
    name: str,
    expected: bool,
    problems: list[str],
) -> None:
    # The generic schema walk reports the type error.  Keep this helper
    # responsible only for the required security value when the type is
    # valid, avoiding a duplicate diagnostic for one field.
    if type(value) is bool and value is not expected:
        problems.append(f"{name} must be {'true' if expected else 'false'}")


def _validate_declared_bool_fields(
    value: object,
    path: str,
    problems: list[str],
) -> None:
    """Strictly validate bool-annotated dataclass fields recursively.

    The configuration schema is dataclass-based, so walking annotations keeps
    this check complete when a future nested config section adds another
    boolean field.  ``type(value) is bool`` intentionally rejects YAML-like
    strings, integers, and custom truthy objects.
    """

    if not is_dataclass(value):
        return
    try:
        type_hints = get_type_hints(type(value))
    except (NameError, TypeError):
        # The current schema has no unresolved annotations.  Falling back to
        # the dataclass annotations still lets validation remain fail-closed
        # if a caller supplies a partially dynamic schema object.
        type_hints = {}

    for item in fields(value):
        item_value = getattr(value, item.name)
        item_path = f"{path}.{item.name}" if path else item.name
        annotation = type_hints.get(item.name, item.type)
        if _annotation_contains_bool(annotation):
            _validate_bool(item_value, item_path, problems)
        if is_dataclass(item_value):
            _validate_declared_bool_fields(item_value, item_path, problems)
        elif isinstance(item_value, (list, tuple)):
            for index, nested in enumerate(item_value):
                if is_dataclass(nested):
                    _validate_declared_bool_fields(nested, f"{item_path}[{index}]", problems)


def _annotation_contains_bool(annotation: Any) -> bool:
    if annotation is bool:
        return True
    origin = get_origin(annotation)
    if origin in {Union, types.UnionType}:
        return any(_annotation_contains_bool(argument) for argument in get_args(annotation))
    return False


def _validate_ttl(value: object, name: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{name} must be a number")
        return
    if not math.isfinite(float(value)) or value <= 0:
        problems.append(f"{name} must be positive")
    elif value > MAX_CONFIG_TTL_SECONDS:
        problems.append(f"{name} must not exceed {MAX_CONFIG_TTL_SECONDS} seconds")


def _validate_positive_number(value: object, name: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        problems.append(f"{name} must be positive")


__all__ = ["MAX_CONFIG_TTL_SECONDS", "ConfigurationError", "validate_config"]
