"""Configuration checks used by ``uls doctor`` (spec §5/§38)."""

from __future__ import annotations

import math
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from uls.domain.errors import UlsError
from uls.domain.ids import parse_course_key

from .errors import ConfigurationError
from .schema import UlsConfig


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
        or cfg.remote_mcp.auth_mode not in {"oauth_or_bearer"}
    ):
        problems.append("remote_mcp.auth_mode is not allowed when remote_mcp.enabled")

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
    if (
        isinstance(cfg.behavior_contract.version, bool)
        or not isinstance(cfg.behavior_contract.version, int)
        or cfg.behavior_contract.version < 1
    ):
        problems.append("behavior_contract.version must be positive")
    return problems


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


__all__ = ["ConfigurationError", "MAX_CONFIG_TTL_SECONDS", "validate_config"]
