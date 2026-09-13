"""Worker-only Notion adapter for the v1.3 intake preview.

The read-only ``NotionAPIReader`` remains the retrieval surface.  This module
owns the separate worker write port and validates provider-neutral property
shapes before any SDK call.  It never creates data-source schemas or mutates
status options; Root's reviewed native provisioning supplies those IDs/options.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from uls.domain.errors import (
    PolicyDeniedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
)

INPUT_REQUEST_TYPES = ("ASSIGN_COURSE", "FILE_DETAILS")
FILE_KINDS = ("TRANSCRIPT", "MATERIAL_PDF")
MATERIAL_ROLES = ("Lecture Slides", "Textbook")
FILE_INTAKE_STATUSES = (
    "OBSERVED", "NEEDS_INPUT", "PLANNED", "REGISTERED", "MOVING", "ORGANIZED",
    "RETRYABLE_ERROR", "UNSUPPORTED", "RECONCILE_REQUIRED",
)
CONTENT_STATUSES = ("Pending", "Ready", "Partial", "Needs Review", "Unavailable", "Failed")
TEXT_STATUSES = ("Pending", "Processing", "Ready", "Partial", "Needs Review", "Failed")

# Notion's Status property has both option names and group placement.  The
# group map is part of the reviewed native readback, so a data source with the
# right labels in the wrong group is not considered a writable match.
STATUS_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "academic_courses": {
        "complete": ("Archived",),
        "current": (),
        "future": (),
        "in_progress": ("Current",),
        "to_do": (),
    },
    "sessions": {
        "complete": ("Archived", "Complete"),
        "current": (),
        "future": (),
        "in_progress": ("In progress",),
        "to_do": ("Not started",),
    },
    "materials": {
        "complete": ("Ready",),
        "current": (),
        "future": (),
        "in_progress": ("Partial", "Processing"),
        "to_do": ("Failed", "Needs Review", "Pending"),
    },
    "file_intake": {
        "complete": ("ORGANIZED",),
        "current": (),
        "future": (),
        "in_progress": ("MOVING", "REGISTERED", "PLANNED"),
        "to_do": ("RECONCILE_REQUIRED", "UNSUPPORTED", "RETRYABLE_ERROR", "NEEDS_INPUT", "OBSERVED"),
    },
    "input_request": {
        "complete": ("Cancelled", "Applied"),
        "current": (),
        "future": (),
        "in_progress": ("Claimed", "Submitted"),
        "to_do": ("Failed", "Reconcile Required", "Needs Input", "Draft"),
    },
}


def _spec(
    kind: str,
    *,
    required: bool = False,
    nullable: bool = True,
    ownership: str = "SYSTEM_CONTROLLED",
    options: Sequence[str] = (),
    relation: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": kind,
        "required": required,
        "nullable": nullable,
        "ownership": ownership,
    }
    if options:
        result["options"] = tuple(options)
    if relation:
        result["relation"] = relation
    return result


INTAKE_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "academic_courses": {
        "Name": _spec("title", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE"),
        "Aliases": _spec("rich_text", ownership="USER"),
        "Course Key": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Code": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Section": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Semester": _spec("select", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Professor": _spec("rich_text", ownership="USER"),
        "Status": _spec("status", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE", options=("Current", "Archived")),
    },
    "sessions": {
        "Name": _spec("title", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE"),
        "ID": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Aliases": _spec("rich_text", ownership="USER"),
        "Course": _spec("relation", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE", relation="academic_courses"),
        "Session No": _spec("number", ownership="USER"),
        "Date": _spec("date", required=True, nullable=False, ownership="USER"),
        "Topics": _spec("multi_select", ownership="USER"),
        "Status": _spec("status", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE", options=("Not started", "In progress", "Complete", "Archived")),
        "Recording Folder": _spec("url", ownership="SYSTEM_CONTROLLED"),
        "Normalized Transcript": _spec("url", ownership="SYSTEM_CONTROLLED"),
        "Recording Status": _spec("select", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=("Pending", "Processing", "Ready", "Partial", "Needs Review", "Failed")),
    },
    "materials": {
        "Name": _spec("title", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE"),
        "ID": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Aliases": _spec("rich_text", ownership="USER"),
        "Course": _spec("relation", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE", relation="academic_courses"),
        "Type": _spec("select", required=True, nullable=False, ownership="USER", options=("Lecture Slides", "Professor Notes", "Syllabus", "Textbook", "Reference", "Supplementary")),
        "Source Folder": _spec("url", required=True, nullable=False, ownership="SYSTEM_CONTROLLED"),
        "Original Filename": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Normalized Source": _spec("url", ownership="SYSTEM_CONTROLLED"),
        "Normalized Annotations": _spec("url", ownership="DEFERRED_LEGACY"),
        "Text Status": _spec("status", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=TEXT_STATUSES),
        "Text Source": _spec("select", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=("Native", "PDF Extract", "OCR", "Mixed", "Manual", "Unavailable")),
        "Visual Dependency": _spec("select", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=("Unknown", "None", "Required")),
        "AI Priority": _spec("select", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE", options=("Normal",)),
        "Page Count": _spec("number", ownership="SYSTEM_CONTROLLED"),
        "Annotation Status": _spec("select", ownership="DEFERRED_LEGACY", options=TEXT_STATUSES),
        "Current Source Version": _spec("number", required=True, nullable=False, ownership="SYSTEM_CONTROLLED"),
    },
    "file_intake": {
        "Name": _spec("title", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE"),
        "Intake ID": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Provider": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Provider File ID": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Original Link": _spec("url", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Original Filename": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Original Parent ID": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Observed Kind": _spec("select", required=True, nullable=False, ownership="SYSTEM_DERIVED", options=("TRANSCRIPT", "MATERIAL_PDF", "UNKNOWN", "UNSUPPORTED")),
        "Course Candidates": _spec("rich_text", ownership="SYSTEM_DERIVED"),
        "Course": _spec("relation", ownership="SYSTEM_CONTROLLED", relation="academic_courses"),
        "Status": _spec("status", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=FILE_INTAKE_STATUSES),
        "Content Status": _spec("select", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=CONTENT_STATUSES),
        "Input Request Link": _spec("url", ownership="SYSTEM_CONTROLLED"),
        "Error": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
        "Last Sync": _spec("date", ownership="SYSTEM_CONTROLLED"),
        "Result References": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
        "Source Hash": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Source Version": _spec("number", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Workspace Fingerprint": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Last Successful Stage": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
    },
    "input_request": {
        "Name": _spec("title", required=True, nullable=False, ownership="SYSTEM_INITIAL_USER_PRESERVE"),
        "Request Key": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Request Revision Hash": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
        "Request Type": _spec("select", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE", options=INPUT_REQUEST_TYPES),
        "Intake Items": _spec("relation", required=True, nullable=False, ownership="USER", relation="file_intake"),
        "Course": _spec("relation", ownership="USER", relation="academic_courses"),
        "Session": _spec("relation", ownership="USER", relation="sessions"),
        "Kind": _spec("select", ownership="USER", options=FILE_KINDS),
        "Actual Date": _spec("date", ownership="USER"),
        "Session Mode": _spec("select", ownership="USER", options=("NEW", "EXISTING")),
        "Session No": _spec("number", ownership="USER"),
        "Material Role": _spec("select", ownership="USER", options=MATERIAL_ROLES),
        "Submitted": _spec("checkbox", required=True, nullable=False, ownership="USER"),
        "Cancelled": _spec("checkbox", required=True, nullable=False, ownership="USER"),
        "Input Hash": _spec("rich_text", ownership="SYSTEM_DERIVED"),
        "Plan Revision": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
        "Request Status": _spec("status", required=True, nullable=False, ownership="SYSTEM_CONTROLLED", options=("Draft", "Submitted", "Claimed", "Applied", "Needs Input", "Reconcile Required", "Cancelled", "Failed")),
        "Result Status": _spec("select", ownership="SYSTEM_CONTROLLED", options=CONTENT_STATUSES),
        "Result Reference": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
        "Error": _spec("rich_text", ownership="SYSTEM_CONTROLLED"),
        "Workspace Fingerprint": _spec("rich_text", required=True, nullable=False, ownership="SYSTEM_IMMUTABLE"),
    },
}

_USER_FIELDS = {
    "academic_courses": {"Aliases", "Professor"},
    "sessions": {"Aliases", "Session No", "Date", "Topics"},
    "materials": {"Aliases", "Type"},
    "input_request": {
        "Intake Items", "Course", "Session", "Kind", "Actual Date", "Session Mode",
        "Session No", "Material Role", "Submitted", "Cancelled",
    },
}
_FORBIDDEN_AUTOMATION_FIELDS = {"Verified", "Scope Confirmed", "Decision", "Decision By", "Approval"}


@runtime_checkable
class NotionWorkerPort(Protocol):
    def list_records(self, data_source_id: str) -> list[dict[str, Any]]: ...

    def read_record(self, data_source_id: str, page_id: str) -> dict[str, Any] | None: ...

    def create_record(self, data_source_id: str, properties: Mapping[str, Any]) -> dict[str, Any]: ...

    def update_record(self, data_source_id: str, page_id: str, properties: Mapping[str, Any]) -> dict[str, Any]: ...


class NotionAPIWorker:
    """Thin SDK port.  It does not read credentials or expose SDK objects."""

    def __init__(self, client: Any, *, max_records: int = 10_000) -> None:
        self.client = client
        self.max_records = max_records

    def list_records(self, data_source_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            result = self._call(self.client.data_sources.query, **kwargs)
            values = result.get("results")
            if not isinstance(values, list):
                raise SourceUnavailableError("Notion worker listing is malformed")
            rows.extend(_normalize_page(item) for item in values)
            if len(rows) > self.max_records:
                raise SourcePartialError("Notion worker listing exceeds bounded limit")
            if result.get("has_more") is False:
                return rows
            cursor = result.get("next_cursor")
            if not isinstance(cursor, str) or cursor in seen:
                raise SourcePartialError("Notion worker pagination is incomplete")
            seen.add(cursor)

    def read_record(self, data_source_id: str, page_id: str) -> dict[str, Any] | None:
        page = self._call(self.client.pages.retrieve, page_id=page_id)
        normalized = _normalize_page(page)
        if normalized.get("_parent_data_source_id", "").replace("-", "") != data_source_id.replace("-", ""):
            raise SourceUnavailableError("Notion worker page is outside resolved data source")
        return normalized

    def create_record(self, data_source_id: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        page = self._call(
            self.client.pages.create,
            parent={"data_source_id": data_source_id},
            properties=dict(properties),
        )
        return _normalize_page(page)

    def update_record(self, data_source_id: str, page_id: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        current = self.read_record(data_source_id, page_id)
        if current is None:
            raise SourceUnavailableError("Notion worker target page is missing")
        page = self._call(self.client.pages.update, page_id=page_id, properties=dict(properties))
        normalized = _normalize_page(page)
        if normalized.get("_parent_data_source_id", "").replace("-", "") != data_source_id.replace("-", ""):
            raise SourceUnavailableError("Notion worker update crossed data-source boundary")
        return normalized

    def validate_workspace(
        self,
        data_source_ids: Mapping[str, str],
        *,
        parent_page_id: str,
        semester: str | None = None,
    ) -> dict[str, Any]:
        """Read back the five reviewed data sources before enabling writes.

        This is deliberately a read-only SDK probe.  It does not create a
        schema, add an option, or repair a parent.  A missing SDK readback is
        reported as ``NOT_VERIFIED`` so callers cannot mistake configured IDs
        for a verified current workspace.
        """

        expected_logicals = tuple(INTAKE_SCHEMAS)
        if set(data_source_ids) != set(expected_logicals) or not parent_page_id:
            return {"status": "NOT_VERIFIED", "reason": "five data-source IDs and parent are required"}
        retrieve = getattr(getattr(self.client, "data_sources", None), "retrieve", None)
        if not callable(retrieve):
            return {"status": "NOT_VERIFIED", "reason": "Notion data-source schema readback is unavailable"}
        readbacks: dict[str, Mapping[str, Any]] = {}
        for logical in expected_logicals:
            data_source_id = data_source_ids.get(logical)
            if not isinstance(data_source_id, str) or not data_source_id:
                return {"status": "NOT_VERIFIED", "reason": f"missing data-source ID: {logical}"}
            try:
                raw = self._call(retrieve, data_source_id=data_source_id)
            except (SourcePartialError, SourceUnavailableError, ProviderUnavailableError):
                return {"status": "NOT_VERIFIED", "reason": f"data-source readback failed: {logical}"}
            if not isinstance(raw, Mapping):
                return {"status": "NOT_VERIFIED", "reason": f"malformed data-source readback: {logical}"}
            readbacks[logical] = raw
        for logical, raw in readbacks.items():
            reason = _validate_data_source_readback(
                logical,
                raw,
                data_source_ids=data_source_ids,
                parent_page_id=parent_page_id,
                semester=semester,
            )
            if reason is not None:
                return {"status": "NOT_VERIFIED", "reason": reason}
        return {
            "status": "VERIFIED",
            "data_sources": len(readbacks),
            "properties": sum(len(raw.get("properties", {})) for raw in readbacks.values()),
        }

    @staticmethod
    def _call(method: Any, **kwargs: Any) -> Any:
        try:
            return method(**kwargs)
        except (SourcePartialError, SourceUnavailableError):
            raise
        except Exception:  # noqa: BLE001 - SDK failures are reduced to a safe provider error
            raise ProviderUnavailableError("Notion worker operation failed") from None


class NotionIntakeWriter:
    """Schema/ownership guard around a worker port."""

    def __init__(
        self,
        backend: NotionWorkerPort,
        data_source_ids: Mapping[str, str],
        *,
        parent_page_id: str = "",
        semester: str | None = None,
    ) -> None:
        self.backend = backend
        self.data_source_ids = {str(key): str(value) for key, value in data_source_ids.items() if value}
        self.parent_page_id = parent_page_id
        self.semester = semester

    def validate_workspace(self) -> dict[str, Any]:
        """Return provider schema/parent verification for the current binding."""

        validator = getattr(self.backend, "validate_workspace", None)
        if not callable(validator):
            return {"status": "NOT_VERIFIED", "reason": "Notion port has no schema readback"}
        try:
            result = validator(
                self.data_source_ids,
                parent_page_id=self.parent_page_id,
                semester=self.semester,
            )
        except Exception:  # noqa: BLE001 - readiness fails closed on any adapter failure
            return {"status": "NOT_VERIFIED", "reason": "Notion schema readback failed"}
        if not isinstance(result, Mapping) or result.get("status") != "VERIFIED":
            reason = result.get("reason") if isinstance(result, Mapping) else None
            return {
                "status": "NOT_VERIFIED",
                "reason": reason if isinstance(reason, str) else "Notion schema readback is not verified",
            }
        return dict(result)

    def data_source_id(self, logical: str) -> str:
        if logical not in INTAKE_SCHEMAS or not self.data_source_ids.get(logical):
            raise SourceUnavailableError(f"missing resolved Notion intake data source: {logical}")
        return self.data_source_ids[logical]

    def list_records(self, logical: str) -> list[dict[str, Any]]:
        return self.backend.list_records(self.data_source_id(logical))

    def read_record(self, logical: str, page_id: str) -> dict[str, Any] | None:
        return self.backend.read_record(self.data_source_id(logical), page_id)

    def create_record(
        self,
        logical: str,
        properties: Mapping[str, Any],
        *,
        allow_user_defaults: bool = False,
    ) -> dict[str, Any]:
        self._validate(logical, properties, is_create=True, allow_user_defaults=allow_user_defaults)
        return self.backend.create_record(self.data_source_id(logical), self._wire(logical, properties))

    def update_system_record(
        self,
        logical: str,
        page_id: str,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate(logical, patch, is_create=False, allow_user_defaults=False)
        preserve = {
            key
            for key, spec in INTAKE_SCHEMAS[logical].items()
            if spec["ownership"] == "SYSTEM_INITIAL_USER_PRESERVE"
        }
        forbidden = (_USER_FIELDS.get(logical, set()) | preserve).intersection(patch)
        if forbidden:
            raise PolicyDeniedError("worker cannot overwrite USER-owned or preserved Notion fields")
        return self.backend.update_record(self.data_source_id(logical), page_id, self._wire(logical, patch))

    def read_exact(self, logical: str, page_id: str, expected: Mapping[str, Any]) -> dict[str, Any]:
        row = self.read_record(logical, page_id)
        if row is None:
            raise SourceUnavailableError("Notion readback page is missing")
        for key, value in expected.items():
            if row.get(key) != value:
                raise SourceUnavailableError(f"Notion readback mismatch for {logical}.{key}")
        return row

    def _validate(
        self,
        logical: str,
        properties: Mapping[str, Any],
        *,
        is_create: bool,
        allow_user_defaults: bool,
    ) -> None:
        if logical not in INTAKE_SCHEMAS:
            raise PolicyDeniedError("unknown Notion intake data source")
        if not isinstance(properties, Mapping):
            raise PolicyDeniedError("Notion properties must be a mapping")
        if _FORBIDDEN_AUTOMATION_FIELDS.intersection(properties):
            raise PolicyDeniedError("intake writer cannot touch human approval fields")
        schema = INTAKE_SCHEMAS[logical]
        unknown = set(properties).difference(schema)
        if unknown:
            raise PolicyDeniedError("properties outside the additive intake schema")
        required = {key for key, value in schema.items() if value["required"]}
        if is_create:
            missing = required.difference(properties)
            if missing:
                raise PolicyDeniedError("missing required Notion properties: " + ", ".join(sorted(missing)))
        for key, value in properties.items():
            spec = schema[key]
            if value is None:
                if not spec["nullable"]:
                    raise PolicyDeniedError(f"{logical}.{key} cannot be null")
                continue
            if (
                spec["ownership"] == "USER"
                and is_create
                and not allow_user_defaults
                and logical == "input_request"
            ):
                # The initial SYSTEM draft may carry only the required intake
                # relation and explicit false booleans.  Every other USER
                # choice must remain null/blank until the user submits it;
                # nonblank values are rejected before any provider call.
                if key in {"Submitted", "Cancelled"}:
                    if value is not False:
                        raise PolicyDeniedError(
                            "initial Input Request must be unsubmitted and uncancelled"
                        )
                elif key == "Intake Items":
                    _validate_value(logical, key, value, spec)
                elif not _is_blank_user_value(value):
                    raise PolicyDeniedError(
                        f"initial Input Request cannot set USER field: {key}"
                    )
            _validate_value(logical, key, value, spec)

    def _wire(self, logical: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        return {key: _wire_value(INTAKE_SCHEMAS[logical][key]["type"], value) for key, value in properties.items()}


@dataclass
class InMemoryNotionWorker:
    """Small provider-free port for acceptance tests and local dry runs."""

    data_sources: dict[str, list[dict[str, Any]]]
    parent_page_id: str = "synthetic-parent"
    events: list[tuple[Any, ...]] | None = None
    _counter: int = 0
    drop_next_create_response: bool = False
    schema_verified: bool = True

    def __post_init__(self) -> None:
        if self.events is None:
            self.events = []
        self.data_sources = {key: [deepcopy(row) for row in rows] for key, rows in self.data_sources.items()}

    def _event_log(self) -> list[tuple[Any, ...]]:
        if self.events is None:
            self.events = []
        return self.events

    def validate_workspace(
        self,
        data_source_ids: Mapping[str, str],
        *,
        parent_page_id: str,
        semester: str | None = None,
    ) -> dict[str, Any]:
        del semester
        expected = set(INTAKE_SCHEMAS)
        if not self.schema_verified:
            return {"status": "NOT_VERIFIED", "reason": "synthetic schema attestation is disabled"}
        if set(data_source_ids) != expected or not parent_page_id or parent_page_id != self.parent_page_id:
            return {"status": "NOT_VERIFIED", "reason": "synthetic parent/data-source readback mismatch"}
        if any(data_source_id not in self.data_sources for data_source_id in data_source_ids.values()):
            return {"status": "NOT_VERIFIED", "reason": "synthetic data-source readback is incomplete"}
        return {"status": "VERIFIED", "data_sources": len(expected), "properties": sum(len(spec) for spec in INTAKE_SCHEMAS.values())}

    def list_records(self, data_source_id: str) -> list[dict[str, Any]]:
        self._event_log().append(("list", data_source_id))
        return [deepcopy(row) for row in self.data_sources.get(data_source_id, [])]

    def read_record(self, data_source_id: str, page_id: str) -> dict[str, Any] | None:
        self._event_log().append(("read", data_source_id, page_id))
        for row in self.data_sources.get(data_source_id, []):
            if row.get("id") == page_id:
                return deepcopy(row)
        return None

    def create_record(self, data_source_id: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        self._counter += 1
        page = {
            "id": f"notion-page-{self._counter}",
            "_parent_data_source_id": data_source_id,
            "_parent_page_id": self.parent_page_id,
            **_normalize_properties(properties),
        }
        self.data_sources.setdefault(data_source_id, []).append(deepcopy(page))
        self._event_log().append(("create", data_source_id, page["id"], deepcopy(dict(properties))))
        if self.drop_next_create_response:
            self.drop_next_create_response = False
            raise ProviderUnavailableError("simulated Notion create response loss")
        return deepcopy(page)

    def update_record(self, data_source_id: str, page_id: str, properties: Mapping[str, Any]) -> dict[str, Any]:
        for row in self.data_sources.get(data_source_id, []):
            if row.get("id") == page_id:
                row.update(_normalize_properties(properties))
                self._event_log().append(("update", data_source_id, page_id, deepcopy(dict(properties))))
                return deepcopy(row)
        raise SourceUnavailableError("in-memory Notion page is missing")


def _validate_data_source_readback(
    logical: str,
    raw: Mapping[str, Any],
    *,
    data_source_ids: Mapping[str, str],
    parent_page_id: str,
    semester: str | None,
) -> str | None:
    expected_id = data_source_ids.get(logical)
    actual_id = raw.get("id")
    if not isinstance(actual_id, str) or not _same_provider_id(actual_id, expected_id):
        return f"data-source identity mismatch: {logical}"
    actual_parent = _data_source_parent_page_id(raw)
    if actual_parent is None or not _same_provider_id(actual_parent, parent_page_id):
        return f"data-source parent mismatch: {logical}"
    properties = raw.get("properties")
    if not isinstance(properties, Mapping):
        return f"data-source properties are missing: {logical}"
    named: dict[str, Mapping[str, Any]] = {}
    for key, value in properties.items():
        if not isinstance(value, Mapping):
            return f"data-source property is malformed: {logical}"
        name = value.get("name", key)
        if not isinstance(name, str) or not name or name in named:
            return f"data-source property name is ambiguous: {logical}"
        named[name] = value
    expected_schema = INTAKE_SCHEMAS[logical]
    if set(named) != set(expected_schema):
        return f"data-source property set mismatch: {logical}"
    for name, expected in expected_schema.items():
        actual = named[name]
        actual_type = _provider_property_type(actual)
        expected_type = expected["type"]
        if not _property_types_match(actual_type, expected_type):
            return f"data-source property type mismatch: {logical}.{name}"
        if expected_type == "relation":
            target_logical = expected.get("relation")
            target_id = _relation_target_id(actual)
            if not isinstance(target_logical, str) or not isinstance(target_id, str):
                return f"data-source relation target is missing: {logical}.{name}"
            if not _same_provider_id(target_id, data_source_ids.get(target_logical)):
                return f"data-source relation target mismatch: {logical}.{name}"
        if expected_type in {"select", "status"}:
            actual_options = _property_option_names(actual, actual_type)
            expected_options = _expected_options(logical, name, expected, semester)
            if actual_options != set(expected_options):
                return f"data-source option set mismatch: {logical}.{name}"
        if expected_type == "status":
            expected_groups = STATUS_GROUPS.get(logical)
            if expected_groups is None:
                return f"status group contract is missing: {logical}.{name}"
            actual_groups = _status_group_names(actual)
            if actual_groups != {
                group: set(values)
                for group, values in expected_groups.items()
            }:
                return f"data-source status groups mismatch: {logical}.{name}"
    return None


def _data_source_parent_page_id(raw: Mapping[str, Any]) -> str | None:
    for key in ("parent_page_id", "_parent_page_id"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    parent = raw.get("parent")
    if isinstance(parent, Mapping):
        for key in ("page_id", "parent_page_id"):
            value = parent.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _provider_property_type(value: Mapping[str, Any]) -> str | None:
    actual = value.get("type")
    if isinstance(actual, str):
        return actual
    for candidate in (
        "title", "rich_text", "text", "select", "status", "relation", "number",
        "checkbox", "url", "date", "multi_select",
    ):
        if candidate in value:
            return candidate
    return None


def _property_types_match(actual: str | None, expected: str) -> bool:
    if expected == "rich_text":
        # The SDK uses ``rich_text``; the native MCP readback calls this same
        # field ``text``.  Both names represent the exact rich-text property.
        return actual in {"rich_text", "text"}
    return actual == expected


def _expected_options(
    logical: str,
    name: str,
    spec: Mapping[str, Any],
    semester: str | None,
) -> tuple[str, ...]:
    if logical == "academic_courses" and name == "Semester":
        return (semester,) if semester else ()
    return tuple(str(value) for value in spec.get("options", ()))


def _property_option_names(value: Mapping[str, Any], actual_type: str | None) -> set[str]:
    config = value.get(actual_type) if actual_type else None
    if not isinstance(config, Mapping):
        return set()
    options = config.get("options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes, bytearray)):
        return set()
    return {
        str(option.get("name"))
        for option in options
        if isinstance(option, Mapping) and isinstance(option.get("name"), str)
    }


def _status_group_names(value: Mapping[str, Any]) -> dict[str, set[str]]:
    config = value.get("status")
    groups = config.get("groups") if isinstance(config, Mapping) else None
    if not isinstance(groups, Mapping):
        return {}
    return {
        str(group): {
            str(option.get("name"))
            for option in options
            if isinstance(option, Mapping) and isinstance(option.get("name"), str)
        }
        for group, options in groups.items()
        if isinstance(options, Sequence) and not isinstance(options, (str, bytes, bytearray))
    }


def _relation_target_id(value: Mapping[str, Any]) -> str | None:
    config = value.get("relation")
    if not isinstance(config, Mapping):
        return None
    for key in ("data_source_id", "database_id", "dataSourceId"):
        target = config.get(key)
        if isinstance(target, str) and target:
            return target
    return None


def _same_provider_id(first: Any, second: Any) -> bool:
    return isinstance(first, str) and isinstance(second, str) and first.replace("-", "") == second.replace("-", "")


def _validate_value(logical: str, key: str, value: Any, spec: Mapping[str, Any]) -> None:
    kind = spec["type"]
    if kind in {"title", "rich_text"} and not isinstance(value, str):
        raise PolicyDeniedError(f"{logical}.{key} must be text")
    if kind in {"select", "status"} and value not in spec.get("options", ()):
        # The live data source owns option configuration.  Unknown values are
        # rejected before SDK calls rather than silently creating options.
        raise PolicyDeniedError(f"{logical}.{key} has an unsupported option")
    if kind == "relation":
        if not isinstance(value, (str, list, tuple)):
            raise PolicyDeniedError(f"{logical}.{key} must be a relation ID/list")
        values = [value] if isinstance(value, str) else list(value)
        if spec["required"] and not values:
            raise PolicyDeniedError(f"{logical}.{key} requires at least one relation")
        if key in {"Course", "Session"} and len(values) > 1:
            raise PolicyDeniedError(f"{logical}.{key} accepts at most one relation")
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise PolicyDeniedError(f"{logical}.{key} must be numeric")
    if kind == "checkbox" and type(value) is not bool:
        raise PolicyDeniedError(f"{logical}.{key} must be boolean")
    if kind in {"url", "date"} and not isinstance(value, str):
        raise PolicyDeniedError(f"{logical}.{key} must be a string")


def _wire_value(kind: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and kind in value:
        return dict(value)
    if kind == "title":
        return {"title": [] if value is None else [{"text": {"content": value}}]}
    if kind == "rich_text":
        return {"rich_text": [] if value is None else [{"text": {"content": value}}]}
    if kind == "select":
        return {"select": None if value is None else {"name": value}}
    if kind == "status":
        return {"status": None if value is None else {"name": value}}
    if kind == "relation":
        values = value if isinstance(value, (list, tuple)) else [value]
        return {"relation": [{"id": item} for item in values]}
    if kind == "number":
        return {"number": value}
    if kind == "checkbox":
        return {"checkbox": value}
    if kind == "url":
        return {"url": value}
    if kind == "date":
        return {"date": {"start": value}}
    if kind == "multi_select":
        return {"multi_select": [{"name": item} for item in value]}
    raise PolicyDeniedError(f"unsupported Notion property type: {kind}")


def _normalize_page(page: Any) -> dict[str, Any]:
    if not isinstance(page, Mapping) or not isinstance(page.get("id"), str):
        raise SourceUnavailableError("Notion page readback is malformed")
    result: dict[str, Any] = {"id": page["id"]}
    parent = page.get("parent", {})
    if isinstance(parent, Mapping):
        for name in ("data_source_id", "page_id"):
            if isinstance(parent.get(name), str):
                result["_parent_" + name] = parent[name]
    for name, prop in page.get("properties", {}).items():
        if isinstance(prop, Mapping):
            result[name] = _normalize_property(prop)
    for name in ("_parent_data_source_id", "_parent_page_id"):
        if name in page:
            result[name] = page[name]
    return result


def _normalize_property(prop: Mapping[str, Any]) -> Any:
    kind = prop.get("type") or _wire_kind(prop)
    if not isinstance(kind, str):
        return None
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        if not isinstance(value, list):
            return ""
        return "".join(str(item.get("plain_text", item.get("text", {}).get("content", ""))) for item in value if isinstance(item, Mapping))
    if kind in {"select", "status"}:
        return value.get("name") if isinstance(value, Mapping) else None
    if kind == "relation":
        return {"relation": [item.get("id") for item in value if isinstance(item, Mapping) and isinstance(item.get("id"), str)]} if isinstance(value, list) else {"relation": []}
    if kind == "date":
        return value.get("start") if isinstance(value, Mapping) else None
    if kind == "multi_select":
        return [item.get("name") for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
    return value


def _normalize_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: _normalize_property(value)
        if isinstance(value, Mapping) and ("type" in value or _wire_kind(value) is not None)
        else value
        for name, value in properties.items()
    }


def _wire_kind(value: Mapping[str, Any]) -> str | None:
    for kind in ("title", "rich_text", "select", "status", "relation", "number", "checkbox", "url", "date", "multi_select"):
        if kind in value:
            return kind
    return None


def _is_blank_user_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == ()


__all__ = [
    "CONTENT_STATUSES",
    "FILE_INTAKE_STATUSES",
    "INTAKE_SCHEMAS",
    "InMemoryNotionWorker",
    "NotionAPIWorker",
    "NotionIntakeWriter",
    "NotionWorkerPort",
]
