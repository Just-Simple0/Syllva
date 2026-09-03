"""YAML configuration and separate environment-secret loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any, TypeVar

import yaml

from .schema import (
    BehaviorContractCfg,
    CourseCfg,
    DriveCfg,
    McpCfg,
    NormalizationCfg,
    NotionCfg,
    RemoteMcpCfg,
    RetrievalCfg,
    StorageCfg,
    SystemCfg,
    UlsConfig,
    WorkerCfg,
)


SECRET_KEYS = (
    "GOOGLE_WORKER_CREDENTIALS_FILE",
    "GOOGLE_MCP_CREDENTIALS_FILE",
    "NOTION_WORKER_TOKEN",
    "NOTION_MCP_TOKEN",
    "GITHUB_READ_TOKEN",
    "LLM_API_KEY",
    "REMOTE_MCP_SECRET",
)

_CfgT = TypeVar("_CfgT")


def load_config(path: str | os.PathLike[str]) -> UlsConfig:
    """Load a v1.2 YAML config into the typed dataclass hierarchy."""

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("configuration root must be a YAML mapping")

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
    return UlsConfig(
        system=_from_mapping(SystemCfg, _section(raw, "system")),
        worker=_from_mapping(WorkerCfg, _section(raw, "worker")),
        storage=_from_mapping(StorageCfg, _section(raw, "storage")),
        google_drive=_from_mapping(DriveCfg, drive_raw),
        notion=_from_mapping(NotionCfg, _section(raw, "notion")),
        normalization=_from_mapping(NormalizationCfg, _section(raw, "normalization")),
        retrieval=_from_mapping(RetrievalCfg, _section(raw, "retrieval")),
        mcp=_from_mapping(McpCfg, _section(raw, "mcp")),
        remote_mcp=_from_mapping(RemoteMcpCfg, _section(raw, "remote_mcp")),
        behavior_contract=_from_mapping(
            BehaviorContractCfg, _section(raw, "behavior_contract")
        ),
        courses=courses,
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


def _from_mapping(cls: type[_CfgT], value: Any) -> _CfgT:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{cls.__name__} section must be a YAML mapping")
    allowed = {item.name for item in fields(cls)}
    kwargs = {name: value[name] for name in allowed if name in value}
    return cls(**kwargs)


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


__all__ = ["SECRET_KEYS", "load_config", "load_secrets"]
