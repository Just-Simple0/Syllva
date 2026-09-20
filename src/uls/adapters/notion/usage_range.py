"""Semester-bound physical data-source reads for C5 and retrieval composition."""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from uls.domain.approval_identity import canonical_notion_page_id
from uls.domain.errors import SourceUnavailableError

from .intake import NotionAPIWorker, NotionWorkerPort, _normalize_page, _wire_value, intake_schemas


@dataclass(frozen=True)
class UsageRangeSources:
    workspace_id: str
    parent_page_id: str
    semester: str
    input_requests: str
    courses: str
    sessions: str
    materials: str
    material_usage: str
    automation_queue: str
    file_intake: str | None = None

    def __post_init__(self) -> None:
        for name in ("parent_page_id", "input_requests", "courses", "sessions", "materials",
                     "material_usage", "automation_queue", "file_intake"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, canonical_notion_page_id(value))
        values = list(self.mapping().values())
        if len(set(values)) != len(values):
            raise ValueError("range data-source mappings must be distinct")

    def mapping(self) -> dict[str, str]:
        result = {"input_request": self.input_requests, "academic_courses": self.courses,
                  "sessions": self.sessions, "materials": self.materials,
                  "material_usage": self.material_usage, "automation_queue": self.automation_queue}
        if self.file_intake:
            result["file_intake"] = self.file_intake
        return result


@dataclass(frozen=True)
class WorkspaceReadiness:
    ready: bool
    profile: str
    errors: tuple[str, ...]


class UsageRangeNotionBridge:
    def __init__(self, backend: NotionWorkerPort, sources: UsageRangeSources, *,
                 schema_profile: str = "c5-range-v1", max_records: int = 10_000) -> None:
        if schema_profile != "c5-range-v1" or type(max_records) is not int or max_records < 1:
            raise ValueError("invalid range bridge profile or bound")
        self.backend = backend
        self.sources = sources
        self.schema_profile = schema_profile
        self.max_records = max_records

    def validate_workspace(self) -> WorkspaceReadiness:
        validator = getattr(self.backend, "validate_workspace", None)
        if not callable(validator) or not self.sources.file_intake:
            return WorkspaceReadiness(False, self.schema_profile, ("complete profile schema mapping is required",))
        result = validator(self.sources.mapping(), parent_page_id=self.sources.parent_page_id,
                           semester=self.sources.semester, schema_profile=self.schema_profile)
        ready = isinstance(result, Mapping) and result.get("status") == "VERIFIED"
        return WorkspaceReadiness(ready, self.schema_profile, () if ready else ("profile readback failed",))

    def list_rows(self, source: str) -> list[dict[str, Any]]:
        if source not in self.sources.mapping().values():
            raise SourceUnavailableError("source is outside semester mapping")
        rows = self.backend.list_records(source)
        if not isinstance(rows, list) or len(rows) > self.max_records:
            raise SourceUnavailableError("semester listing is incomplete")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise SourceUnavailableError("semester row is malformed")
            try:
                physical = canonical_notion_page_id(row["id"])
            except (KeyError, ValueError, TypeError):
                raise SourceUnavailableError("semester physical identity is malformed") from None
            if physical in seen:
                raise SourceUnavailableError("duplicate physical row in listing")
            seen.add(physical)
            self._validate_provider_row(row, source)
            row["record_id"] = physical
        return rows

    def read_row(self, source: str, physical_id: str) -> dict[str, Any]:
        if source not in self.sources.mapping().values():
            raise SourceUnavailableError("source is outside semester mapping")
        row = self.backend.read_record(source, canonical_notion_page_id(physical_id))
        if row is None or canonical_notion_page_id(row.get("id", "")) != canonical_notion_page_id(physical_id):
            raise SourceUnavailableError("physical row is missing or replaced")
        self._validate_provider_row(row, source)
        row["record_id"] = canonical_notion_page_id(row["id"])
        if row.get("archived") or row.get("in_trash"):
            raise SourceUnavailableError("physical row is archived")
        return row

    @staticmethod
    def _validate_provider_row(row: Mapping[str, Any], source: str) -> None:
        parent = row.get("_parent_data_source_id")
        if not isinstance(parent, str) or canonical_notion_page_id(parent) != canonical_notion_page_id(source):
            raise SourceUnavailableError("semester row parent is missing or mismatched")
        if type(row.get("archived")) is not bool or type(row.get("in_trash")) is not bool:
            raise SourceUnavailableError("semester row archive evidence is missing")

    def list_approval_rows(self) -> list[dict[str, Any]]:
        return self.list_rows(self.sources.automation_queue)

    def graph_view(self) -> SemesterGraphView:
        return SemesterGraphView(self)

    def _logical(self, target_db: str) -> str:
        for logical in ("material_usage", "automation_queue"):
            if target_db == self.sources.mapping()[logical]:
                return logical
        raise SourceUnavailableError("write target is outside the range bridge")

    def find_approval_rows(self, proposal_id: str) -> list[dict[str, Any]]:
        rows = [row for row in self.list_approval_rows() if row.get("Proposal ID") == proposal_id]
        if any(row["archived"] or row["in_trash"] for row in rows):
            raise SourceUnavailableError("approval row is archived")
        return rows

    def find_entity_by_id(self, target_db: str, entity_id: str) -> dict[str, Any] | None:
        logical = self._logical(target_db)
        key = "Proposal ID" if logical == "automation_queue" else "ID"
        matches = [row for row in self.list_rows(target_db)
                   if row.get(key) == entity_id or row.get("id") == entity_id]
        if len(matches) > 1:
            raise SourceUnavailableError("range bridge entity identity is ambiguous")
        if not matches:
            return None
        return self.read_row(target_db, matches[0]["id"])

    def _encode(self, logical: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        schema = intake_schemas(self.schema_profile)[logical]
        if set(properties) - set(schema):
            raise ValueError("range write contains unknown properties")
        result = {}
        for name, value in properties.items():
            kind = schema[name]["type"]
            if kind in {"created_time", "last_edited_time"}:
                raise ValueError("provider timestamp is read-only")
            if kind == "relation":
                value = list(physical_relations(value))
            elif kind == "rich_text" and isinstance(value, (Mapping, list, tuple)):
                value = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            result[name] = _wire_value(kind, value)
        return result

    def _prepare_update_properties(self, target_db: str, entity_id: str,
                                   patch: Mapping[str, Any]) -> Callable[[], Any]:
        """Pure preparation after the guard's final read; no hidden provider pre-read."""
        logical = self._logical(target_db)
        physical_id = canonical_notion_page_id(entity_id)
        encoded = self._encode(logical, patch)
        if not isinstance(self.backend, NotionAPIWorker):
            raise SourceUnavailableError("range writes require the explicit SDK worker boundary")
        method = self.backend.client.pages.update
        payload = {"page_id": physical_id, "properties": encoded}
        inspect.signature(method).bind(**payload)

        def write() -> dict[str, Any]:
            row = _normalize_page(method(**payload))
            self._validate_provider_row(row, target_db)
            if canonical_notion_page_id(row["id"]) != physical_id:
                raise SourceUnavailableError("updated physical row differs from target")
            return row

        return write

    def update_properties(self, target_db: str, entity_id: str, patch: Mapping[str, Any],
                          *, actor: Any = None, system_transition: bool = False) -> Any:
        del actor, system_transition
        row = self.find_entity_by_id(target_db, entity_id)
        if row is None:
            raise SourceUnavailableError("update target is missing")
        return self._prepare_update_properties(target_db, row["id"], patch)()

    def create_entity(self, target_db: str, properties: Mapping[str, Any], *, actor: Any = None) -> Any:
        del actor
        return self._prepare_create_entity(target_db, properties)()

    def _prepare_create_entity(self, target_db: str, properties: Mapping[str, Any]) -> Callable[[], Any]:
        encoded = self._encode(self._logical(target_db), properties)
        if not isinstance(self.backend, NotionAPIWorker):
            raise SourceUnavailableError("range writes require the explicit SDK worker boundary")
        method = self.backend.client.pages.create
        payload = {"parent": {"data_source_id": target_db}, "properties": encoded}
        inspect.signature(method).bind(**payload)

        def write() -> dict[str, Any]:
            row = _normalize_page(method(**payload))
            self._validate_provider_row(row, target_db)
            return row

        return write


def physical_relations(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        value = value.get("relation")
    if not isinstance(value, (list, tuple)):
        raise SourceUnavailableError("physical relation is malformed")
    return tuple(canonical_notion_page_id(item["id"] if isinstance(item, Mapping) else item) for item in value)


class SemesterGraphView:
    """Read-only capability; relations retain physical evidence alongside app IDs."""
    def __init__(self, bridge: UsageRangeNotionBridge) -> None:
        self._bridge = bridge

    def _normalize(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["page_id"] = canonical_notion_page_id(row["id"])
        physical: dict[str, tuple[str, ...]] = {}
        for name, source in (("Session", self._bridge.sources.sessions), ("Material", self._bridge.sources.materials)):
            if name not in row:
                continue
            ids = physical_relations(row[name])
            physical[name] = ids
            related = [self._bridge.read_row(source, page) for page in ids]
            if any(not isinstance(item.get("ID"), str) or not item["ID"] for item in related):
                raise SourceUnavailableError("related application identity is missing")
            result[name] = {"relation": [{"id": item["ID"]} for item in related]}
        if "Course" in row:
            ids = physical_relations(row["Course"])
            physical["Course"] = ids
            result["Course"] = {"relation": [{"id": page} for page in ids]}
        result["_physical_relations"] = physical
        return result

    def _get(self, source: str, app_id: str) -> dict[str, Any] | None:
        matches = [row for row in self._bridge.list_rows(source) if row.get("ID") == app_id]
        if len(matches) > 1:
            raise SourceUnavailableError("application identity is ambiguous")
        if not matches:
            return None
        if matches[0].get("archived") or matches[0].get("in_trash"):
            raise SourceUnavailableError("application row is archived")
        return self._normalize(matches[0])

    def get_session(self, entity_id: str) -> dict[str, Any] | None:
        return self._get(self._bridge.sources.sessions, entity_id)

    def get_material(self, material_id: str) -> dict[str, Any] | None:
        return self._get(self._bridge.sources.materials, material_id)

    def get_course_by_relation_id(self, relation_page_id: str) -> dict[str, Any]:
        return self._normalize(self._bridge.read_row(self._bridge.sources.courses, relation_page_id))

    def get_material_usage(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_session(session_id)
        if session is None:
            raise SourceUnavailableError("Session is missing")
        rows = self._bridge.list_rows(self._bridge.sources.material_usage)
        return [self._normalize(row) for row in rows
                if session["page_id"] in physical_relations(row.get("Session"))]
