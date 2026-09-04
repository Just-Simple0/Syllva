"""Provider-free NotionReader fixture for Phase 2 contract tests.

The reader intentionally has no write methods.  ``FakeNotionWriter`` is a
separate worker-side helper and routes mutations through the existing Phase 1
write policy before touching the in-memory records.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from uls.adapters.notion.base import (
    AutomationActor,
    enforce_write_policy,
    find_alias_matches,
    normalize_alias,
)


COURSE_KEY = "2026-1_COMP319-002"

# Frozen implementation spec §14.2.  Session-only writes are validated here
# so ingestion tests cannot accidentally exercise a Material field on a
# Session record without failing at the fake provider boundary.
SESSION_PROPERTIES = frozenset(
    {
        "Name",
        "ID",
        "Aliases",
        "Course",
        "Session No",
        "Date",
        "Topics",
        "Status",
        "Recording Folder",
        "Normalized Transcript",
        "Recording Status",
        "Material Usage",
        "Activities",
    }
)


def _course() -> dict[str, Any]:
    return {
        "ID": COURSE_KEY,
        "Course Key": COURSE_KEY,
        "Name": "알고리즘1",
        "Aliases": "알고리즘 | algorithms | COMP319",
        "Code": "COMP319",
        "Section": "002",
        "Semester": "2026-1",
    }


def _session(
    number: int,
    *,
    aliases: str | None = None,
    transcript_ref: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    entity_id = f"COMP319-S{number:02d}"
    return {
        "ID": entity_id,
        "Name": name or f"{number:02d} · CPU Scheduling",
        "Aliases": aliases or f"{number}강 | {number}번째 강의 | CPU Scheduling",
        "Course": COURSE_KEY,
        "Session No": number,
        "Normalized Transcript": transcript_ref or entity_id,
        "Recording Status": "Ready",
    }


def _material(material_id: str = "COMP319-M03", *, source_ref: str = "material-m03") -> dict[str, Any]:
    return {
        "ID": material_id,
        "Name": "Algorithmic Analysis II",
        "Aliases": "Lec3 | Algorithmic Analysis II | 알고리즘 분석 2",
        "Course": COURSE_KEY,
        "Normalized Source": source_ref,
        "Source Hash": "material-hash-v1",
        "Source Version": 1,
        "Text Status": "Ready",
    }


class FakeNotionReader:
    """Small in-memory implementation of the Phase 2 ``NotionReader``."""

    def __init__(
        self,
        *,
        courses: Iterable[Mapping[str, Any]] | None = None,
        sessions: Iterable[Mapping[str, Any]] | None = None,
        materials: Iterable[Mapping[str, Any]] | None = None,
        material_usage: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        enrichments: Mapping[str, Any] | None = None,
        annotations: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> None:
        course_values = list(courses or [_course()])
        session_values = list(sessions or [_session(5)])
        material_values = list(materials or [_material()])
        self.courses = {str(item.get("Course Key", item.get("ID"))): deepcopy(dict(item)) for item in course_values}
        self.sessions = {str(item["ID"]): deepcopy(dict(item)) for item in session_values}
        self.materials = {str(item["ID"]): deepcopy(dict(item)) for item in material_values}
        self.material_usage = {
            key: [deepcopy(dict(item)) for item in values]
            for key, values in (material_usage or {}).items()
        }
        self.enrichments = dict(enrichments or {})
        self.annotations = {
            key: [deepcopy(dict(item)) for item in values]
            for key, values in (annotations or {}).items()
        }

    def get_session(self, entity_id: str) -> Mapping[str, Any] | None:
        return self.sessions.get(entity_id)

    def find_sessions_by_alias(self, course: Any, alias_norm: str) -> list[Mapping[str, Any]]:
        values = [item for item in self.sessions.values() if _same_course(item, course)]
        return find_alias_matches(values, alias_norm)

    def list_course_sessions(self, course: Any) -> list[Mapping[str, Any]]:
        return [item for item in self.sessions.values() if _same_course(item, course)]

    def get_material_usage(self, session_id: str) -> list[Mapping[str, Any]]:
        return list(self.material_usage.get(session_id, ()))

    def get_session_enrichment(self, entity_id: str) -> Any | None:
        return self.enrichments.get(entity_id)

    def get_course_by_alias(self, alias_norm: str) -> Mapping[str, Any] | None:
        matches = find_alias_matches(self.courses.values(), normalize_alias(alias_norm))
        return matches[0] if matches else None

    def get_material(self, material_id: str) -> Mapping[str, Any] | None:
        return self.materials.get(material_id)

    def get_session_user_annotations(self, session_id: str) -> list[Mapping[str, Any]]:
        return list(self.annotations.get(session_id, ()))


class FakeNotionWriter:
    """Worker-side fake that preserves the Phase 1 defensive write policy."""

    def __init__(self, reader: FakeNotionReader, *, events: list[Any] | None = None) -> None:
        self.reader = reader
        self.events = events if events is not None else []

    def update_session_metadata(self, entity_id: str, patch: Mapping[str, Any]) -> Mapping[str, Any]:
        unknown = set(patch).difference(SESSION_PROPERTIES)
        if unknown:
            raise ValueError(
                "Sessions patch contains properties outside §14.2: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        enforce_write_policy(AutomationActor.AUTOMATION, "Sessions", patch)
        if entity_id not in self.reader.sessions:
            self.reader.sessions[entity_id] = {"ID": entity_id}
        self.reader.sessions[entity_id].update(dict(patch))
        self.events.append(("notion", entity_id, dict(patch)))
        return self.reader.sessions[entity_id]


FakeNotion = FakeNotionReader
FakeNotionAdapter = FakeNotionReader


def _same_course(record: Mapping[str, Any], course: Any) -> bool:
    if course is None:
        return True
    wanted = course
    if isinstance(course, Mapping):
        wanted = course.get("Course Key", course.get("ID", course.get("Code")))
    actual = record.get("Course", record.get("Course Key"))
    if isinstance(actual, Mapping):
        actual = actual.get("Course Key", actual.get("ID", actual.get("Code")))
    if actual is None or wanted is None:
        return False
    return normalize_alias(str(actual)) == normalize_alias(str(wanted)) or (
        str(wanted).casefold() == "comp319" and "comp319" in str(actual).casefold()
    )


__all__ = [
    "COURSE_KEY",
    "SESSION_PROPERTIES",
    "FakeNotion",
    "FakeNotionAdapter",
    "FakeNotionReader",
    "FakeNotionWriter",
]
