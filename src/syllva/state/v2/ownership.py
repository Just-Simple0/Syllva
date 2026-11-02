"""Ownership and write-permission rules (design §2 I2-I4, implementation spec §5.1).

This module is the single table of who may write what. ``StateWriter`` enforces
it; the schema triggers are the last line of defence behind it.
"""

from __future__ import annotations

from enum import StrEnum


class Owner(StrEnum):
    SOURCE = "SOURCE"
    AI = "AI"
    USER = "USER"
    CATALOG = "CATALOG"  # academic structure: courses, sessions, materials, ... (not agent-writable)
    SYSTEM = "SYSTEM"  # processing bookkeeping: jobs, checkpoints, allocations, blob refs


class Actor(StrEnum):
    INGESTER = "INGESTER"
    CORE_AGENT_OUTPUT = "CORE_AGENT_OUTPUT"
    UI_USER = "UI_USER"
    SYSTEM_MIGRATION = "SYSTEM_MIGRATION"


TABLE_OWNERS: dict[str, Owner] = {
    "blobs": Owner.SOURCE,
    "source_files": Owner.SOURCE,
    "source_versions": Owner.SOURCE,
    "derivatives": Owner.SOURCE,
    "ai_artifacts": Owner.AI,
    "artifact_bases": Owner.AI,
    "artifact_citations": Owner.AI,
    "user_notes": Owner.USER,
    "user_edits": Owner.USER,
    "courses": Owner.CATALOG,
    "course_aliases": Owner.CATALOG,
    "sessions": Owner.CATALOG,
    "materials": Owner.CATALOG,
    "material_usage": Owner.CATALOG,
    "activities": Owner.CATALOG,
    "exams": Owner.CATALOG,
    "blob_refs": Owner.SYSTEM,
    "jobs": Owner.SYSTEM,
    "processing_records": Owner.SYSTEM,
    "checkpoints": Owner.SYSTEM,
    "entity_allocations": Owner.SYSTEM,
}

# Tables no generic write may touch: the Human-Proof audit trail is written only
# by the gate commit path (R2), and schema bookkeeping only by the migrator.
GATE_ONLY_TABLES = frozenset({"human_gate_log", "human_gate_uses"})
UNWRITABLE_TABLES = GATE_ONLY_TABLES | {"schema_migrations"}

# Human-only columns (design §5.5). Only the gate commit path may set them.
HUMAN_ONLY_COLUMNS: dict[str, frozenset[str]] = {
    "material_usage": frozenset({"verified", "verified_gate_id"}),
    "exams": frozenset({"scope_confirmed", "scope_gate_id"}),
    "activities": frozenset({"submission_status", "submission_gate_id"}),
    "user_notes": frozenset({"adopted_from_artifact_id", "adopt_gate_id"}),
}

# Spec §5.1 matrix, with CATALOG/SYSTEM made explicit.
#   SOURCE  <- INGESTER (create only)
#   AI      <- CORE_AGENT_OUTPUT (create only; content is validated before, spec §8.7)
#   USER    <- UI_USER (explicit edits, history kept)
#   CATALOG <- INGESTER, UI_USER
#   SYSTEM  <- INGESTER
#   SYSTEM_MIGRATION may write every owner (still not human-only columns or gate tables).
ALLOWED: dict[Owner, frozenset[Actor]] = {
    Owner.SOURCE: frozenset({Actor.INGESTER, Actor.SYSTEM_MIGRATION}),
    Owner.AI: frozenset({Actor.CORE_AGENT_OUTPUT, Actor.SYSTEM_MIGRATION}),
    Owner.USER: frozenset({Actor.UI_USER, Actor.SYSTEM_MIGRATION}),
    Owner.CATALOG: frozenset({Actor.INGESTER, Actor.UI_USER, Actor.SYSTEM_MIGRATION}),
    Owner.SYSTEM: frozenset({Actor.INGESTER, Actor.SYSTEM_MIGRATION}),
}

# SOURCE and AI rows are never rewritten: AI output is never relabelled and
# source content changes arrive as new source_versions (design §4.4).
INSERT_ONLY_OWNERS = frozenset({Owner.SOURCE, Owner.AI})
# Bookkeeping columns on SOURCE tables that the ingester may update in place.
SOURCE_MUTABLE_COLUMNS: dict[str, frozenset[str]] = {
    "source_files": frozenset({"deleted_at", "display_name"}),
}


def owner_of(table: str) -> Owner:
    try:
        return TABLE_OWNERS[table]
    except KeyError:
        raise KeyError(f"unknown or unwritable table: {table}") from None


# Material processing status transitions (design §2 I3). PARTIAL never goes to READY.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"PROCESSING", "FAILED"}),
    "PROCESSING": frozenset({"READY", "PARTIAL", "NEEDS_REVIEW", "FAILED"}),
    "READY": frozenset({"PENDING"}),
    "PARTIAL": frozenset({"PENDING", "PROCESSING", "NEEDS_REVIEW", "FAILED"}),
    "NEEDS_REVIEW": frozenset({"PENDING", "FAILED"}),
    "FAILED": frozenset({"PENDING"}),
}


def can_transition(old: str, new: str) -> bool:
    return old == new or new in STATUS_TRANSITIONS.get(old, frozenset())
