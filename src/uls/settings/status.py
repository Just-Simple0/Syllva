"""Read-only Overview and resumable setup predicates shared with the CLI."""

from __future__ import annotations

import re
from typing import Any

from uls.cli.main import doctor, status

from .config_service import ConfigStore
from .journal import JournalStore

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
        item = dict(item)
        item["same_target"] = item.pop("binding", {}) == binding
        pending.append(item)
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
    proven = all(step["state"] == READY for step in steps)
    steps.append(_step("Check", READY if proven else PARTIAL,
                       "All required steps are proven." if proven
                       else "Some steps are not proven yet."))
    return steps


def _step(name: str, state: str, reason: str) -> dict[str, str]:
    return {"name": name, "state": state, "reason": reason}


__all__ = ["NOT_CHECKED", "PARTIAL", "READY", "STEP_ORDER", "settings_overview", "setup_steps"]
