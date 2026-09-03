"""Configuration checks used by ``uls doctor`` (spec §5/§38)."""

from __future__ import annotations

import math
from pathlib import Path

from uls.domain.errors import UlsError
from uls.domain.ids import parse_course_key

from .schema import UlsConfig


MAX_CONFIG_TTL_SECONDS = 24 * 60 * 60


def validate_config(cfg: UlsConfig) -> list[str]:
    """Return human-readable configuration problems, without raising them."""

    problems: list[str] = []
    if not isinstance(cfg, UlsConfig):
        return ["config must be an UlsConfig instance"]

    if cfg.mcp.read_only is not True:
        problems.append("mcp.read_only must be true")
    if cfg.remote_mcp.public_unauthenticated is not False:
        problems.append("remote_mcp.public_unauthenticated must be false")

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
    if cfg.worker.poll_interval_minutes <= 0:
        problems.append("worker.poll_interval_minutes must be positive")
    if not cfg.normalization.processor_version:
        problems.append("normalization.processor_version is required")
    if cfg.mcp.mode not in {"local", "remote"}:
        problems.append("mcp.mode must be local or remote")
    if cfg.remote_mcp.enabled and not cfg.remote_mcp.auth_mode:
        problems.append("remote_mcp.auth_mode is required when remote_mcp.enabled")

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
    if cfg.retrieval.max_candidate_entities <= 0:
        problems.append("retrieval.max_candidate_entities must be positive")
    if cfg.retrieval.max_candidate_chunks <= 0:
        problems.append("retrieval.max_candidate_chunks must be positive")
    if cfg.behavior_contract.version < 1:
        problems.append("behavior_contract.version must be positive")
    return problems


def _validate_ttl(value: object, name: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{name} must be a number")
        return
    if not math.isfinite(float(value)) or value <= 0:
        problems.append(f"{name} must be positive")
    elif value > MAX_CONFIG_TTL_SECONDS:
        problems.append(f"{name} must not exceed {MAX_CONFIG_TTL_SECONDS} seconds")


__all__ = ["MAX_CONFIG_TTL_SECONDS", "validate_config"]
