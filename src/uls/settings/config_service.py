"""Typed, allowlisted settings snapshots and atomic compare-and-swap applies."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from uls.config.loader import load_config_mapping
from uls.config.mutation import (
    ConfigFileLock,
    ConfigLockTimeout,
    atomic_replace_config,
    read_config_bytes,
)
from uls.config.validation import validate_config

from .journal import JournalStore

GENERATION_PATTERN = re.compile(r"^[a-f0-9]{64}$")
GROUP_FIELDS: dict[str, frozenset[str]] = {
    "general": frozenset({"system.timezone"}),
    "canvas_registry": frozenset({"canvas.registry"}),
    "canvas_sync": frozenset({"canvas.sync_enabled"}),
    "advanced": frozenset({
        "retrieval.concept_mode",
        "retrieval.max_candidate_entities",
        "retrieval.max_candidate_chunks",
        "retrieval.context_ttl_seconds",
        "retrieval.resolution_ttl_seconds",
        "retrieval.max_evidence_items",
        "retrieval.max_chars_per_item",
        "retrieval.max_total_chars",
        "retrieval.max_followup_chunks",
        "normalization.goodnotes_visual_fallback",
    }),
}
_INTEGER_BOUNDS: dict[str, tuple[int, int]] = {
    "retrieval.max_candidate_entities": (1, 500),
    "retrieval.max_candidate_chunks": (1, 1000),
    "retrieval.context_ttl_seconds": (60, 86_400),
    "retrieval.resolution_ttl_seconds": (60, 86_400),
    "retrieval.max_evidence_items": (1, 200),
    "retrieval.max_chars_per_item": (100, 20_000),
    "retrieval.max_total_chars": (1000, 1_000_000),
    "retrieval.max_followup_chunks": (1, 200),
}
_CHANGED = "Settings changed elsewhere. Review and apply again."


class SettingsServiceError(Exception):
    def __init__(
        self, code: str, message: str, status_code: int = 400, *,
        fields: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # Structured {field, code, message} entries. Field names are always
        # allowlisted names, never caller text.
        self.fields = fields


@dataclass(frozen=True)
class LoadedConfig:
    raw: dict[str, Any]
    config: Any
    raw_bytes: bytes
    generation: str


class ConfigStore:
    def __init__(self, config_path: str | os.PathLike[str]) -> None:
        self.path = Path(config_path).expanduser().absolute()

    def binding(self) -> dict[str, str]:
        """Code-owned canonical identity of this config target for journal records."""

        parent = Path(os.path.realpath(self.path.parent))
        info = os.stat(parent)
        return {
            "config_path": str(parent / self.path.name),
            "config_dir_id": f"{info.st_dev}:{info.st_ino}",
        }

    def load(self) -> LoadedConfig:
        with ConfigFileLock(self.path):
            data = read_config_bytes(self.path)
        return self._parse(data)

    def group_snapshot(self, group: str) -> dict[str, Any]:
        self._require_group(group)
        loaded = self.load()
        return {"generation": loaded.generation, "values": self._group_values(loaded.config, group)}

    def preview(self, group: str, raw_values: object, expected_generation: object) -> dict[str, Any]:
        """Validate a patch and return the reviewed candidate's identity and diff."""

        allowed = self._require_group(group)
        _require_generation(expected_generation)
        values = self._validate_patch(group, raw_values, allowed)
        current = self.load()
        if current.generation != expected_generation:
            raise SettingsServiceError("CONFIGURATION_CHANGED", _CHANGED, 409)
        candidate_bytes = self._candidate_bytes(current, values)
        candidate = self._parse(candidate_bytes)
        return {
            "generation": current.generation,
            "candidate_hash": candidate.generation,
            "values": values,
            "valid": True,
            "diff": _redacted_diff(current, candidate, values),
        }

    def apply(
        self,
        group: str,
        raw_values: object,
        expected_generation: object,
        journal: JournalStore,
        *,
        candidate_hash: object,
        fault_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Apply exactly the reviewed candidate (bound by its server-issued hash)."""

        fields = self._require_group(group)
        _require_generation(expected_generation)
        if not isinstance(candidate_hash, str) or not GENERATION_PATTERN.fullmatch(candidate_hash):
            raise SettingsServiceError("REVIEW_REQUIRED", "Review the change before applying it.")
        values = self._validate_patch(group, raw_values, fields)
        initial = self.load()
        if initial.generation != expected_generation:
            raise SettingsServiceError("CONFIGURATION_CHANGED", _CHANGED, 409)
        candidate_bytes = self._candidate_bytes(initial, values)
        if _sha256(candidate_bytes) != candidate_hash:
            raise SettingsServiceError(
                "REVIEW_STALE", "These values differ from the reviewed change. Review again.", 409,
            )
        # One durable record per operation, created before any lock is taken.
        operation_id = journal.create_config_operation(
            binding=self.binding(),
            original_generation=initial.generation,
            candidate_hash=candidate_hash,
            fields=list(values),
        )

        # Fixed lock order: this operation's record lock, then the config lock.
        with journal.operation(operation_id) as operation:
            operation.update(phase="candidate_validated", next_action="acquire_config_lock")
            config_lock = ConfigFileLock(self.path)
            if not config_lock.acquire():
                # No side effect happened: resolve the record immediately.
                operation.update(phase="resolved_without_change", next_action="none")
                raise ConfigLockTimeout("configuration is being changed by another process")
            try:
                current_bytes = read_config_bytes(self.path)
                current_generation = _sha256(current_bytes)
                if current_generation != expected_generation:
                    # CAS loss after the record exists: terminate it so no
                    # recovery entry remains for an operation with no effect.
                    operation.update(
                        phase="resolved_without_change",
                        observed_generation=current_generation,
                        next_action="none",
                    )
                    raise SettingsServiceError("CONFIGURATION_CHANGED", _CHANGED, 409)
                operation.update(phase="locked", observed_generation=current_generation,
                                 next_action="run_config_replace")
                operation.run_effect(
                    "config_replace",
                    pre_state=current_generation,
                    intended_post_state=candidate_hash,
                    perform=lambda: atomic_replace_config(self.path, candidate_bytes),
                    observe=lambda: _sha256(read_config_bytes(self.path)),
                    fault_hook=fault_hook,
                )
                readback = self._parse(read_config_bytes(self.path))
                operation.update(
                    phase="readback", readback_generation=readback.generation,
                    next_action="finalize",
                )
                diff = _redacted_diff(initial, readback, values)
                operation.update(phase="complete", next_action="none")
            finally:
                config_lock.release()
        return {
            "status": "applied",
            "generation": candidate_hash,
            "diff": diff,
            "operation_id": operation_id,
        }

    def recover(self, journal: JournalStore, operation_id: str, action: str) -> dict[str, Any]:
        """Resolve one pending config record against this exact config target only."""

        if action not in {"resume", "leave"}:
            raise SettingsServiceError("INVALID_RECOVERY_ACTION", "Choose a listed recovery action.")
        try:
            with journal.operation(operation_id) as operation:
                record = operation.read()
                if record["action_kind"] != "config_apply" or record["phase"] in {
                    "complete", "resolved_without_change", "completed_then_superseded",
                }:
                    raise SettingsServiceError(
                        "OPERATION_NOT_PENDING", "This recovery item is no longer pending.", 409,
                    )
                if record["binding"] != self.binding():
                    raise SettingsServiceError(
                        "OPERATION_OTHER_TARGET",
                        "This item belongs to another settings file. Open that file to resolve it.",
                        409,
                    )
                with ConfigFileLock(self.path):
                    raw = read_config_bytes(self.path)
                    return self._recover_locked(operation, record, raw, action)
        except FileNotFoundError:
            raise SettingsServiceError(
                "OPERATION_NOT_FOUND", "This recovery item is no longer available.", 404,
            ) from None

    def _recover_locked(
        self, operation: Any, record: dict[str, Any], raw: bytes, action: str,
    ) -> dict[str, Any]:
        generation = _sha256(raw)
        effects = dict(record["effects"])
        effect = effects.get("config_replace")
        status = effect["status"] if isinstance(effect, dict) else None
        if status in {"verified", "verified_by_recovery"}:
            assert isinstance(effect, dict)
            if generation == effect["post_state_id"]:
                # The recorded post-state is still on disk: validate readback.
                readback = self._parse(raw)
                operation.update(
                    phase="complete", readback_generation=readback.generation,
                    observed_generation=generation, next_action="none",
                )
                return {"status": "completed", "generation": generation}
            # The change committed historically and was later superseded.
            # Never attribute the newer generation as this operation's readback.
            operation.update(
                phase="completed_then_superseded", observed_generation=generation,
                next_action="none",
            )
            return {"status": "completed_then_superseded", "generation": generation}
        if status == "intent" and generation == record["candidate_hash"]:
            assert isinstance(effect, dict)
            readback = self._parse(raw)
            effects["config_replace"] = {
                **effect, "post_state_id": generation, "status": "verified_by_recovery",
            }
            operation.update(
                phase="complete", effects=effects, readback_generation=readback.generation,
                observed_generation=generation, next_action="none",
            )
            return {"status": "completed", "generation": generation}
        pre_state = effect["pre_state_id"] if isinstance(effect, dict) else record["original_generation"]
        if status in {None, "intent"} and generation == pre_state:
            # Provably no write happened. Leaving it resolves the record; a
            # resume requires a fresh reviewed apply (the journal keeps hashes only).
            if action == "leave":
                operation.update(
                    phase="resolved_without_change", observed_generation=generation,
                    next_action="none",
                )
                return {"status": "resolved_without_change", "generation": generation}
            raise SettingsServiceError(
                "NO_COMMIT_TO_RESUME",
                "No settings change reached disk. Leave it as-is, then review and apply again.",
                409,
            )
        # Any other state is a concurrent edit or failed readback: never
        # overwrite it and never guess. The item stays Partial.
        if record["phase"] != "repair_required":
            operation.update(
                phase="repair_required", observed_generation=generation,
                next_action="manual_review",
            )
        if action == "leave":
            return {"status": "left_pending", "generation": generation}
        raise SettingsServiceError(
            "CONFIGURATION_STATE_CHANGED",
            "The settings file changed again. This item stays unfinished for review.",
            409,
        )

    def _candidate_bytes(self, current: LoadedConfig, values: Mapping[str, Any]) -> bytes:
        candidate_bytes = _serialize_config(_apply_values(current.raw, values))
        try:
            self._parse(candidate_bytes)
        except (ValueError, yaml.YAMLError):
            raise SettingsServiceError(
                "CONFIGURATION_INVALID", "The proposed settings do not pass configuration checks.",
            ) from None
        return candidate_bytes

    def _parse(self, data: bytes) -> LoadedConfig:
        try:
            raw_value = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError("configuration could not be parsed") from exc
        if raw_value is None:
            raw_value = {}
        if not isinstance(raw_value, Mapping):
            raise ValueError("configuration root must be a mapping")  # noqa: TRY004 - parse errors share one type
        raw = dict(raw_value)
        config = load_config_mapping(raw)
        _resolve_relative_paths(config, self.path)
        if validate_config(config):
            raise ValueError("configuration validation failed")
        return LoadedConfig(raw=raw, config=config, raw_bytes=data, generation=_sha256(data))

    @staticmethod
    def _require_group(group: str) -> frozenset[str]:
        fields = GROUP_FIELDS.get(group)
        if fields is None:
            raise SettingsServiceError(
                "FEATURE_DEFERRED", "This settings group is not editable in this version.", 403,
            )
        return fields

    @staticmethod
    def _validate_patch(group: str, raw_values: object, allowed: frozenset[str]) -> dict[str, Any]:
        if not isinstance(raw_values, Mapping):
            raise SettingsServiceError("INVALID_PATCH", "Settings must be submitted as named values.")
        values = dict(raw_values)
        if not values or not set(values).issubset(allowed):
            raise SettingsServiceError(
                "FEATURE_DEFERRED", "One or more settings are read-only in this version.", 403,
            )
        normalized: dict[str, Any] = {}
        errors: list[dict[str, str]] = []
        for field in sorted(values):
            message = _field_problem(field, values[field])
            if message is None:
                normalized[field] = values[field]
            else:
                errors.append({"field": field, "code": "INVALID_VALUE", "message": message})
        if errors:
            raise SettingsServiceError(
                "INVALID_VALUE", "Some settings need attention. Each one is marked below.",
                fields=errors,
            )
        if group == "general" and set(normalized) != {"system.timezone"}:
            raise SettingsServiceError("INVALID_PATCH", "Choose a supported General setting.")
        return normalized

    @staticmethod
    def _group_values(config: Any, group: str) -> dict[str, Any]:
        if group == "canvas_registry":
            return {"canvas.registry": config.canvas.get("registry", {})}
        if group == "canvas_sync":
            return {"canvas.sync_enabled": config.canvas.get("sync_enabled", False)}
        if group == "general":
            return {"system.timezone": config.system.timezone}
        if group == "advanced":
            return {field: _get_config_value(config, field) for field in sorted(GROUP_FIELDS[group])}
        raise SettingsServiceError(
            "FEATURE_DEFERRED", "This settings group is not editable in this version.", 403,
        )


def _field_problem(field: str, value: Any) -> str | None:
    """Return a plain-language problem for one allowlisted field, or None."""

    if field == "canvas.sync_enabled":
        if value is True:
            raise SettingsServiceError("FEATURE_DEFERRED", "Canvas sync is not available yet.", 403)
        return None if value is False else "Choose Disable Sync."
    if field == "canvas.registry":
        if not isinstance(value, dict) or set(value) != {"term_id", "courses"}:
            return "Choose a term and courses from the checked list."
        return None
    if field == "system.timezone":
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            return "Enter a valid time zone, for example Asia/Seoul."
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            return "Enter a valid time zone, for example Asia/Seoul."
        return None
    if field == "retrieval.concept_mode":
        return None if value == "bounded_lexical" else "Choose a supported concept mode: bounded_lexical."
    if field in _INTEGER_BOUNDS:
        low, high = _INTEGER_BOUNDS[field]
        if type(value) is not int or not low <= value <= high:
            return f"Enter a whole number from {low:,} to {high:,}."
        return None
    if field == "normalization.goodnotes_visual_fallback":
        return None if type(value) is bool else "Enter true or false."
    return "This setting is read-only in this version."


def _require_generation(value: object) -> None:
    if not isinstance(value, str) or not GENERATION_PATTERN.fullmatch(value):
        raise SettingsServiceError("INVALID_GENERATION", "Reload settings and try again.")


def _apply_values(raw: dict[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    for field, value in values.items():
        section_name, name = field.split(".", 1)
        section = result.get(section_name)
        if section is None:
            section = {}
        if not isinstance(section, Mapping):
            raise SettingsServiceError(
                "CONFIGURATION_INVALID", "The current settings cannot be safely updated.", 409,
            )
        updated = dict(section)
        updated[name] = value
        result[section_name] = updated
    return result


def _get_config_value(config: Any, field: str) -> Any:
    section, name = field.split(".", 1)
    return getattr(getattr(config, section), name)


def _serialize_config(raw: Mapping[str, Any]) -> bytes:
    text = str(yaml.safe_dump(dict(raw), allow_unicode=True, sort_keys=False))
    return text.encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_relative_paths(config: Any, config_path: Path) -> None:
    for obj, field in (
        (config.system, "workspace_dir"),
        (config.behavior_contract, "path"),
        (config.remote_mcp, "tls_certfile"),
        (config.remote_mcp, "tls_keyfile"),
    ):
        value = getattr(obj, field)
        if value:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            setattr(obj, field, str(candidate.resolve()))
    for field in ("google_worker_credentials_path", "google_mcp_credentials_path"):
        value = getattr(config, field, None)
        if value:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            setattr(config, field, str(candidate.resolve()))


def _redacted_diff(before: LoadedConfig, after: LoadedConfig, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for field, _value in sorted(values.items()):
        old = _get_config_value(before.config, field)
        new = _get_config_value(after.config, field)
        if old != new:
            result.append({"field": field, "from": old, "to": new})
    return result


def generation_of(data: bytes) -> str:
    return _sha256(data)


__all__ = [
    "GROUP_FIELDS", "ConfigStore", "LoadedConfig", "SettingsServiceError", "generation_of",
]
