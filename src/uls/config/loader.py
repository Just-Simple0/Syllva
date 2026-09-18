"""YAML configuration and separate environment-secret loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any, TypeVar

import yaml

from .credentials import ALLOWED_SOURCES
from .errors import ConfigurationError
from .schema import (
    BehaviorContractCfg,
    CourseCfg,
    CourseStaticFolderCfg,
    DriveCfg,
    McpCfg,
    NormalizationCfg,
    NotionCfg,
    OidcCfg,
    RemoteMcpCfg,
    RetrievalCfg,
    SemesterRegistryCfg,
    SemesterWorkspaceCfg,
    StorageCfg,
    SystemCfg,
    UlsConfig,
    WorkerCfg,
)
from .validation import validate_config

SECRET_KEYS = (
    "GOOGLE_WORKER_CREDENTIALS_FILE",
    "GOOGLE_MCP_CREDENTIALS_FILE",
    "NOTION_WORKER_TOKEN",
    "NOTION_MCP_TOKEN",
    "GITHUB_READ_TOKEN",
    "LLM_API_KEY",
    "REMOTE_MCP_SECRET",
    "REMOTE_MCP_EXPIRES_AT",
)

# Every top-level YAML section name this loader recognizes. Used only by the
# typo guard below; adding "credentials" here does not change the
# pre-existing behavior of silently ignoring other unrelated unknown
# top-level keys (see docs/plans/credential-resolver.md, Blocker 4).
_KNOWN_TOP_LEVEL_KEYS = frozenset({
    "system", "worker", "storage", "google_drive", "drive", "notion",
    "normalization", "retrieval", "mcp", "remote_mcp", "behavior_contract",
    "courses", "credentials", "google_worker_credentials_path", "google_mcp_credentials_path",
})

_CfgT = TypeVar("_CfgT")


def load_config(path: str | os.PathLike[str]) -> UlsConfig:
    """Load and validate a v1.2 YAML config fail-closed."""

    config = load_config_unvalidated(path)
    problems = validate_config(config)
    if problems:
        raise ConfigurationError(problems)
    return config


def load_config_unvalidated(path: str | os.PathLike[str]) -> UlsConfig:
    """Parse config without safety validation for diagnostics/tests only."""

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("configuration root must be a YAML mapping")

    _check_top_level_typos(raw)

    courses_raw = raw.get("courses", [])
    if courses_raw is None:
        courses_raw = []
    if not isinstance(courses_raw, list):
        raise ValueError("courses must be a YAML list")
    courses: list[CourseCfg] = []
    for index, value in enumerate(courses_raw):
        if isinstance(value, CourseCfg):
            courses.append(value)
        elif isinstance(value, Mapping):
            courses.append(_from_mapping(CourseCfg, value))
        else:
            raise ValueError(f"courses[{index}] must be a YAML mapping")

    drive_raw = raw.get("google_drive", raw.get("drive", {}))
    if not isinstance(drive_raw, Mapping):
        raise ValueError("google_drive must be a YAML mapping")
    semester_registries = _semester_registries(drive_raw.get("semester_registries", []))
    drive_values = dict(drive_raw)
    drive_values["semester_registries"] = semester_registries
    worker_cred_path = str(raw.get("google_worker_credentials_path", drive_raw.get("worker_credentials_path", "")) or "")
    mcp_cred_path = str(raw.get("google_mcp_credentials_path", drive_raw.get("mcp_credentials_path", "")) or "")
    drive_values["worker_credentials_path"] = worker_cred_path
    drive_values["mcp_credentials_path"] = mcp_cred_path
    notion_raw = _section(raw, "notion")
    notion_values = dict(notion_raw)
    notion_values["semester_workspaces"] = _semester_workspaces(
        notion_raw.get("semester_workspaces", [])
    )
    remote_raw = _section(raw, "remote_mcp")
    remote_values = dict(remote_raw)
    oidc_raw = remote_raw.get("oidc", {})
    if oidc_raw is None:
        oidc_raw = {}
    if not isinstance(oidc_raw, Mapping):
        raise ValueError("remote_mcp.oidc must be a YAML mapping")
    remote_values["oidc"] = _from_mapping(OidcCfg, oidc_raw)
    return UlsConfig(
        system=_from_mapping(SystemCfg, _section(raw, "system")),
        worker=_from_mapping(WorkerCfg, _section(raw, "worker")),
        storage=_from_mapping(StorageCfg, _section(raw, "storage")),
        google_drive=_from_mapping(DriveCfg, drive_values),
        notion=_from_mapping(NotionCfg, notion_values),
        normalization=_from_mapping(NormalizationCfg, _section(raw, "normalization")),
        retrieval=_from_mapping(RetrievalCfg, _section(raw, "retrieval")),
        mcp=_from_mapping(McpCfg, _section(raw, "mcp")),
        remote_mcp=_from_mapping(RemoteMcpCfg, remote_values),
        behavior_contract=_from_mapping(
            BehaviorContractCfg, _section(raw, "behavior_contract")
        ),
        courses=courses,
        credentials=_credentials_section(raw),
        google_worker_credentials_path=worker_cred_path,
        google_mcp_credentials_path=mcp_cred_path,
    )


def load_secrets(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Load secret values from ``.env`` and ``os.environ``.

    Environment variables take precedence over values in the file.  The
    parser intentionally supports only the small ``KEY=VALUE`` syntax needed
    by the project; it never treats secrets as YAML configuration.
    """

    env_path = Path(path) if path is not None else Path(".env")
    file_values = _read_dotenv(env_path) if env_path.exists() else {}
    keys = set(SECRET_KEYS) | set(file_values)
    return {key: os.environ.get(key, file_values.get(key, "")) for key in sorted(keys)}


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a YAML mapping")
    return value


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, lchar in enumerate(left, start=1):
        current = [i] + [0] * len(right)
        for j, rchar in enumerate(right, start=1):
            cost = 0 if lchar == rchar else 1
            current[j] = min(
                previous[j] + 1,       # deletion
                current[j - 1] + 1,    # insertion
                previous[j - 1] + cost,  # substitution
            )
        previous = current
    return previous[-1]


def _check_top_level_typos(raw: Mapping[str, Any]) -> None:
    """Reject a top-level key that is a near-miss typo of 'credentials'.

    Scoped strictly to 'credentials' (edit-distance <= 2, case-insensitive)
    so this does not change the pre-existing repository-wide behavior of
    silently ignoring unrelated unknown top-level keys; it only prevents
    the 'credentials' section from being silently downgraded to 'absent' by a
    typo like 'credentails'.
    """

    target = "credentials"
    for key in raw:
        if not isinstance(key, str) or key == target:
            continue
        if key.lower() == target or 0 < _levenshtein(key.lower(), target) <= 2:
            raise ValueError(
                f"unknown top-level key {key!r} looks like a typo of {target!r}"
            )


def _credentials_section(raw: Mapping[str, Any]) -> dict[str, str]:
    """Parse the optional credentials: section (rev3 PLAN GO design).

    Absent entirely -> {} (every credential defaults to "environment" at
    CredentialResolver construction time; observably behavior-equivalent to
    today for every existing deployment). Present but malformed in any way
    -> ValueError fail-closed, never a silent partial parse.
    """

    if "credentials" not in raw:
        return {}
    section = raw["credentials"]
    if not isinstance(section, Mapping):
        raise ValueError("credentials must be a YAML mapping")
    result: dict[str, str] = {}
    for name, entry in section.items():
        if name not in ALLOWED_SOURCES:
            raise ValueError(f"credentials.{name} is not a recognized credential name")
        if not isinstance(entry, Mapping) or set(entry) != {"source"}:
            raise ValueError(
                f"credentials.{name} must be a mapping with exactly the key 'source'"
            )
        source = entry["source"]
        if not isinstance(source, str):
            raise ValueError(
                f"credentials.{name}.source must be a string"
            )
        if source not in ALLOWED_SOURCES[name]:
            raise ValueError(
                f"credentials.{name}.source must be one of "
                + ", ".join(sorted(ALLOWED_SOURCES[name]))
            )
        result[name] = source
    return result


def _from_mapping(cls: type[_CfgT], value: Any) -> _CfgT:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{cls.__name__} section must be a YAML mapping")
    allowed = {item.name for item in fields(cls)}
    kwargs = {name: value[name] for name in allowed if name in value}
    return cls(**kwargs)


def _semester_registries(value: Any) -> list[SemesterRegistryCfg]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("google_drive.semester_registries must be a YAML list")
    result: list[SemesterRegistryCfg] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"semester_registries[{index}] must be a YAML mapping")
        raw_static = item.get("course_static_folder_ids", {})
        if raw_static is None:
            raw_static = {}
        if not isinstance(raw_static, Mapping):
            raise ValueError(
                f"semester_registries[{index}].course_static_folder_ids must be a mapping"
            )
        static: dict[str, CourseStaticFolderCfg] = {}
        for course_key, folders in raw_static.items():
            if not isinstance(course_key, str) or not isinstance(folders, Mapping):
                raise ValueError(
                    f"semester_registries[{index}].course_static_folder_ids entries must be mappings"
                )
            static[course_key] = _from_mapping(CourseStaticFolderCfg, folders)
        values = dict(item)
        values["course_static_folder_ids"] = static
        for name in (
            "course_folder_ids",
            "optional_course_upload_folder_ids",
        ):
            nested = values.get(name, {})
            if nested is None:
                nested = {}
            if not isinstance(nested, Mapping):
                raise ValueError(f"semester_registries[{index}].{name} must be a mapping")
            values[name] = dict(nested)
        result.append(_from_mapping(SemesterRegistryCfg, values))
    return result


def _semester_workspaces(value: Any) -> list[SemesterWorkspaceCfg]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("notion.semester_workspaces must be a YAML list")
    result: list[SemesterWorkspaceCfg] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"semester_workspaces[{index}] must be a YAML mapping")
        values = dict(item)
        portals = values.get("portal_page_ids", {})
        if portals is None:
            portals = {}
        if not isinstance(portals, Mapping):
            raise ValueError(f"semester_workspaces[{index}].portal_page_ids must be a mapping")
        values["portal_page_ids"] = dict(portals)
        result.append(_from_mapping(SemesterWorkspaceCfg, values))
    return result


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


__all__ = [
    "ConfigurationError",
    "SECRET_KEYS",
    "load_config",
    "load_config_unvalidated",
    "load_secrets",
]
