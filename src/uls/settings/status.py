"""Read-only Overview and resumable setup predicates shared with the CLI."""

from __future__ import annotations

import re
from typing import Any

from uls.cli.main import doctor, status

from .canvas_service import validated_canvas_profile, validated_canvas_registry
from .config_service import ConfigStore, SettingsServiceError
from .journal import TERMINAL_PHASES, JournalError, JournalStore, recovery_choices

STEP_ORDER = ("Storage", "Canvas", "Academic", "Automation", "Remote", "Check")
READY = "Ready"
PARTIAL = "Partial"
NOT_CHECKED = "Not checked"
_PLACEHOLDER = re.compile(r"^(\.\.\.|<.*>|changeme|todo)?$", re.IGNORECASE)


def settings_overview(store: ConfigStore, journal: JournalStore) -> dict[str, Any]:
    loaded = store.load()
    config = loaded.config
    runtime_status = status(config)
    doctor_status = doctor(config, live=False, include_credentials=False)
    steps = setup_steps(config)
    binding = store.binding()
    pending = []
    for item in journal.unresolved():
        target = item.get("binding")
        same_target = (isinstance(target, dict) and bool(binding)
                       and all(key in target and target[key] == value for key, value in binding.items()))
        operation_id = item.get("operation_id")
        if not same_target or not isinstance(operation_id, str):
            pending.append({"same_target": False, "choices": []})
            continue
        try:
            record = journal.read(operation_id)
        except (JournalError, OSError, ValueError, UnicodeDecodeError):
            pending.append({"same_target": False, "choices": []})
            continue
        record_target = record.get("binding")
        record_matches = (isinstance(record_target, dict)
                          and all(key in record_target and record_target[key] == value
                                  for key, value in binding.items()))
        if not record_matches:
            pending.append({"same_target": False, "choices": []})
            continue
        if record.get("phase") in TERMINAL_PHASES:
            continue
        fields = record.get("fields")
        pending.append({
            "operation_id": record["operation_id"],
            "action_kind": record["action_kind"],
            "phase": record["phase"],
            "fields": list(fields) if isinstance(fields, list) else [],
            "same_target": True,
            "choices": recovery_choices(record),
        })
    return {
        "local_runtime_healthy": runtime_status.get("status") == "ok",
        "setup_ready": all(step["state"] == READY for step in steps),
        "readiness_funnel": runtime_status.get("readiness_funnel", {}),
        "worker_enabled": bool(runtime_status.get("worker_enabled", False)),
        "remote_enabled": bool(runtime_status.get("remote_enabled", False)),
        "doctor": {
            "status": doctor_status.get("status", "unknown"),
            "checks": doctor_status.get("checks", {}),
            "credential_readiness": "Not checked in Local Settings",
        },
        "setup_steps": steps,
        "pending_operations": pending,
        "restart_required": {
            "status": "not_reported",
            "message": "Running services do not report their loaded settings version.",
        },
    }


def setup_steps(config: Any) -> list[dict[str, str]]:
    """Report only what durable, verified evidence proves; never infer Ready.

    GUI-1 owns no verified Drive/Notion binding record, no verified Canvas
    profile/term/course registry, no per-course mapping proof, no credential
    readiness proof, and no saved explicit Automation or Remote choice. Each
    step is therefore Partial (required input missing or no explicit choice
    saved) or Not checked (input saved but unverifiable here). A step becomes
    Ready only through a proven predicate supplied by a later bundle.
    """

    ids = (
        config.google_drive.university_root_id, config.google_drive.inbox_root_id,
        config.notion.courses_db_id, config.notion.sessions_db_id,
    )
    ids_saved = all(isinstance(value, str) and not _PLACEHOLDER.fullmatch(value.strip()) for value in ids)
    course_count = len(config.courses or [])
    steps = [
        _step("Storage", NOT_CHECKED if ids_saved else PARTIAL,
              "Saved Drive and Notion IDs have not been verified." if ids_saved
              else "Drive or Notion storage IDs are missing."),
        _step("Canvas", NOT_CHECKED,
              "No verified Canvas connection exists yet. Canvas setup arrives in a later version."),
        _step("Academic", NOT_CHECKED,
              (f"{course_count} saved course entries are not verified against Canvas."
               if course_count else "Course mappings need a verified Canvas connection.")),
        _step("Automation", NOT_CHECKED if config.worker.enabled else PARTIAL,
              "Automation is on; its readiness is not checked here." if config.worker.enabled
              else "Automation is off, but no explicit on/off choice has been saved."),
        _step("Remote", NOT_CHECKED if config.remote_mcp.enabled else PARTIAL,
              "Remote MCP is on; it has not been verified here." if config.remote_mcp.enabled
              else "Remote MCP is off, but no explicit Skip choice has been saved."),
    ]
    canvas = getattr(config, "canvas", {})
    canvas_ready = False
    if isinstance(canvas, dict):
        try:
            validated_canvas_profile({"canvas": canvas})
            validated_canvas_registry(canvas)
            canvas_ready = True
        except SettingsServiceError:
            pass
    steps[1] = _step("Canvas", READY if canvas_ready else PARTIAL,
                     "Verified account and course selection saved." if canvas_ready
                     else "Connect a Canvas account and save a course selection.")
    proven = all(step["state"] == READY for step in steps)
    steps.append(_step("Check", READY if proven else PARTIAL,
                       "All required steps are proven." if proven
                       else "Some steps are not proven yet."))
    return steps


def _step(name: str, state: str, reason: str) -> dict[str, str]:
    return {"name": name, "state": state, "reason": reason}


__all__ = ["NOT_CHECKED", "PARTIAL", "READY", "STEP_ORDER", "settings_overview", "setup_steps"]
