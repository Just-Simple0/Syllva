"""Typed academic graph records used by the Phase 5 retrieval boundary.

The provider adapters are allowed to expose dictionaries, but authority
decisions must consume these small value objects.  In particular, ``None``
and an empty relation are intentionally different values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .course_identity import CourseIdentity, strict_relation_page_ids
from .ids import strict_entity_id

_MISSING = object()


def _raw(record: Any, *names: str, default: Any = _MISSING) -> Any:
    """Read one provider field without collapsing relation cardinality."""

    source = record
    if isinstance(record, Mapping) and isinstance(record.get("properties"), Mapping):
        source = record["properties"]
    elif isinstance(getattr(record, "properties", None), Mapping):
        source = record.properties
    wanted = {_normal(name) for name in names}
    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, str) and _normal(key) in wanted:
                return value
    else:
        for name in names:
            for candidate in (name, name.casefold(), name.replace(" ", "_")):
                if hasattr(source, candidate):
                    return getattr(source, candidate)
    return default


def _value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"value"}:
            return _value(value["value"])
        typed = value.get("type")
        if typed is not None:
            if not isinstance(typed, str):
                raise ValueError("provider property type must be a string")
            key = typed.casefold()
            if key == "url":
                if "url" not in value:
                    raise ValueError("URL property is missing its url value")
                url = value["url"]
                if url is None:
                    return None
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("URL property value must be a non-empty string or null")
                return url
            if key in {"title", "rich_text"}:
                if key not in value or not isinstance(value[key], Sequence) or isinstance(value[key], (str, bytes)):
                    raise ValueError(f"{key} property value is malformed")
                pieces: list[str] = []
                for item in value[key]:
                    if isinstance(item, Mapping):
                        text = item.get("plain_text")
                        if text is None and isinstance(item.get("text"), Mapping):
                            text = item["text"].get("content")
                        if text is not None:
                            if not isinstance(text, str):
                                raise ValueError(f"{key} property text is malformed")
                            pieces.append(text)
                    elif item is not None:
                        raise ValueError(f"{key} property text is malformed")
                return "".join(pieces)
            if key in {"select", "status"}:
                if key not in value:
                    raise ValueError(f"{key} property is missing its value")
                inner = value[key]
                if inner is None:
                    return None
                if not isinstance(inner, Mapping):
                    raise ValueError(f"{key} property value is malformed")
                if set(inner) - {"id", "name", "color"} or not isinstance(inner.get("name"), str):
                    raise ValueError(f"{key} option is malformed")
                return inner["name"]
            if key == "checkbox":
                if "checkbox" not in value or type(value["checkbox"]) is not bool:
                    raise ValueError("checkbox property value is malformed")
                return value["checkbox"]
            if key in {"number", "date"}:
                if key not in value:
                    raise ValueError(f"{key} property is missing its value")
                return value[key]
            if key == "relation":
                return value
            raise ValueError(f"unsupported provider property type: {typed}")
        if "name" in value and set(value) <= {"id", "name", "color"}:
            if not isinstance(value["name"], str):
                raise ValueError("provider option name is malformed")
            return value["name"]
        if set(value) == {"id"} and isinstance(value["id"], str):
            return value["id"]
    return value


def _text(value: Any, name: str, *, required: bool = True) -> str | None:
    value = _value(value)
    if value is _MISSING or value is None:
        if required:
            raise ValueError(f"{name} must be a non-empty string")
        return None
    if isinstance(value, str) and value.strip():
        # Identity-bearing text is preserved exactly after the emptiness check.
        return value
    if required:
        raise ValueError(f"{name} must be a non-empty string")
    raise ValueError(f"{name} must be a non-empty string or absent")


def _bool(value: Any, name: str, *, default: bool | None = None) -> bool | None:
    value = _value(value)
    if value is _MISSING:
        return default
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _relation_ids(value: Any, name: str, expected_type: str) -> tuple[str, ...] | None:
    if value is _MISSING:
        return None
    try:
        return strict_relation_page_ids(value, expected_type=expected_type)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} relation is malformed") from exc


def _due_at(value: Any) -> str | None:
    """Validate a Notion Date value and retain its exact start text.

    ``ActivityRecord`` currently exposes one scalar ``due_at``.  A provider
    range and time zone are therefore validated at this boundary but are not
    flattened into the scalar; the documented ``start`` value remains the
    only stored value until the domain gains a range type.
    """

    value = _value(value)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("Due must be a Notion Date value or null")
    if set(value) - {"start", "end", "time_zone"}:
        raise ValueError("Due Date contains unsupported fields")
    start = value.get("start")
    if not isinstance(start, str) or not start.strip():
        raise ValueError("Due Date start must be a non-empty ISO date or datetime")
    start = _validate_due_datetime(start, "Due Date start")
    end = value.get("end")
    if end is not None:
        if not isinstance(end, str) or not end.strip():
            raise ValueError("Due Date end must be an ISO date or datetime or null")
        end = _validate_due_datetime(end, "Due Date end")
        if ("T" in start or " " in start) != ("T" in end or " " in end):
            raise ValueError("Due Date start and end must use the same date form")
    time_zone = value.get("time_zone")
    if time_zone is not None and (
        not isinstance(time_zone, str) or not time_zone.strip()
    ):
        raise ValueError("Due Date time_zone must be a non-empty string or null")
    return start


def _validate_due_datetime(value: str, name: str) -> str:
    try:
        if "T" in value or " " in value:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            datetime.fromisoformat(normalized)
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO date or datetime") from exc
    return value


def _record_id(record: Any, expected_type: str) -> str:
    value = _raw(record, "ID", "Entity ID", "id", "entity_id", default=_MISSING)
    value = _value(value)
    result = strict_entity_id(value, expected_type)
    if result is None:
        raise ValueError(f"record ID must be a canonical {expected_type} ID")
    return result


def _normal(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


@dataclass(frozen=True)
class ActivityInstructionRefs:
    """The two independently stored graph pointers for official instructions."""

    instructions_source_url: str | None
    normalized_instructions_url: str | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.instructions_source_url, "instructions_source_url"),
            (self.normalized_instructions_url, "normalized_instructions_url"),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")

    @property
    def source_url(self) -> str | None:
        return self.instructions_source_url

    @property
    def normalized_url(self) -> str | None:
        return self.normalized_instructions_url

    def as_dict(self) -> dict[str, str | None]:
        return {
            "instructions_source_url": self.instructions_source_url,
            "normalized_instructions_url": self.normalized_instructions_url,
        }


@dataclass(frozen=True)
class ActivityResultMetadata:
    """Typed result/submission metadata; GitHub retrieval remains Phase 6."""

    result_type: str
    submission_ref: str | None = None
    repository_ref: str | None = None
    pull_request_ref: str | None = None
    status: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result_type, str) or not self.result_type.strip():
            raise ValueError("result_type must be a non-empty string")
        for value, name in (
            (self.submission_ref, "submission_ref"),
            (self.repository_ref, "repository_ref"),
            (self.pull_request_ref, "pull_request_ref"),
            (self.status, "status"),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "result_type": self.result_type,
            "submission_ref": self.submission_ref,
            "repository_ref": self.repository_ref,
            "pull_request_ref": self.pull_request_ref,
            "status": self.status,
        }


@dataclass(frozen=True)
class ActivityConstraintMetadata:
    """Official-instruction constraint metadata, separate from enum authority."""

    official_locator_set: tuple[str, ...] = field(default_factory=tuple)
    official_evidence_set: tuple[str, ...] = field(default_factory=tuple)
    priority: str = "hard"

    def __post_init__(self) -> None:
        if self.priority != "hard":
            raise ValueError("official activity constraint priority must be 'hard'")
        for name, values in (
            ("official_locator_set", self.official_locator_set),
            ("official_evidence_set", self.official_evidence_set),
        ):
            normalized = tuple(values)
            if any(not isinstance(value, str) or not value.strip() for value in normalized):
                raise ValueError(f"{name} must contain non-empty strings")
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"{name} contains duplicates")
            object.__setattr__(self, name, normalized)

    def as_dict(self) -> dict[str, Any]:
        return {
            "official_locator_set": list(self.official_locator_set),
            "official_evidence_set": list(self.official_evidence_set),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ExamRecord:
    entity_id: str
    course: CourseIdentity
    included_session_ids: tuple[str, ...] | None = None
    scope_confirmed: bool = False
    title: str | None = None

    def __post_init__(self) -> None:
        if strict_entity_id(self.entity_id, "E") is None:
            raise ValueError("Exam entity_id must be a canonical E ID")
        if not isinstance(self.course, CourseIdentity):
            raise TypeError("Exam course must be a CourseIdentity")
        if type(self.scope_confirmed) is not bool:
            raise TypeError("Exam scope_confirmed must be a boolean")
        _validate_ids(self.included_session_ids, "included_session_ids", "S")

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "course": self.course.as_dict(),
            "included_session_ids": None if self.included_session_ids is None else list(self.included_session_ids),
            "scope_confirmed": self.scope_confirmed,
            "title": self.title,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], course: CourseIdentity) -> ExamRecord:
        included = _relation_ids(_raw(value, "Included Sessions", "included_sessions", default=_MISSING), "Included Sessions", "S")
        confirmed = _bool(_raw(value, "Scope Confirmed", "scope_confirmed", default=_MISSING), "Scope Confirmed", default=False)
        assert confirmed is not None
        title = _text(_raw(value, "Name", "Title", "title", default=_MISSING), "Name", required=False)
        return cls(_record_id(value, "E"), course, included, confirmed, title)


@dataclass(frozen=True)
class ActivityRecord:
    entity_id: str
    course: CourseIdentity
    instruction_refs: ActivityInstructionRefs
    result: ActivityResultMetadata
    related_session_ids: tuple[str, ...] | None = None
    related_material_ids: tuple[str, ...] | None = None
    title: str | None = None
    due_at: str | None = None

    def __post_init__(self) -> None:
        if strict_entity_id(self.entity_id, "A") is None:
            raise ValueError("Activity entity_id must be a canonical A ID")
        if not isinstance(self.course, CourseIdentity):
            raise TypeError("Activity course must be a CourseIdentity")
        if not isinstance(self.instruction_refs, ActivityInstructionRefs):
            raise TypeError("Activity instruction_refs must be typed")
        if not isinstance(self.result, ActivityResultMetadata):
            raise TypeError("Activity result must be typed")
        _validate_ids(self.related_session_ids, "related_session_ids", "S")
        _validate_ids(self.related_material_ids, "related_material_ids", "M")
        for value, name in ((self.title, "title"), (self.due_at, "due_at")):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")

    @property
    def instructions_source_url(self) -> str | None:
        return self.instruction_refs.instructions_source_url

    @property
    def normalized_instructions_url(self) -> str | None:
        return self.instruction_refs.normalized_instructions_url

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "course": self.course.as_dict(),
            "instruction_refs": self.instruction_refs.as_dict(),
            "result": self.result.as_dict(),
            "related_session_ids": None if self.related_session_ids is None else list(self.related_session_ids),
            "related_material_ids": None if self.related_material_ids is None else list(self.related_material_ids),
            "title": self.title,
            "due_at": self.due_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], course: CourseIdentity) -> ActivityRecord:
        source = _text(_raw(value, "Instructions Source", "Instructions Source URL", "instructions_source_url", default=_MISSING), "Instructions Source", required=False)
        normalized = _text(_raw(value, "Normalized Instructions", "Normalized Instructions URL", "normalized_instructions_url", default=_MISSING), "Normalized Instructions", required=False)
        refs = ActivityInstructionRefs(source, normalized)
        result_type = _text(
            _raw(value, "Result Type", "result_type", "Submission Type", default=_MISSING),
            "Result Type",
        )
        assert result_type is not None
        result = ActivityResultMetadata(
            result_type,
            _text(_raw(value, "Submission Ref", "Submission URL", "submission_ref", default=_MISSING), "Submission Ref", required=False),
            _text(_raw(value, "Repository Ref", "repository_ref", default=_MISSING), "Repository Ref", required=False),
            _text(_raw(value, "Pull Request Ref", "pull_request_ref", default=_MISSING), "Pull Request Ref", required=False),
            _text(_raw(value, "Status", "status", default=_MISSING), "Status", required=False),
        )
        related_sessions = _relation_ids(_raw(value, "Related Sessions", "Sessions", "related_session_ids", default=_MISSING), "Related Sessions", "S")
        related_materials = _relation_ids(_raw(value, "Related Materials", "Materials", "related_material_ids", default=_MISSING), "Related Materials", "M")
        return cls(
            _record_id(value, "A"),
            course,
            refs,
            result,
            related_sessions,
            related_materials,
            _text(_raw(value, "Name", "Title", "title", default=_MISSING), "Name", required=False),
            _due_at(_raw(value, "Due", "Due At", "due_at", default=_MISSING)),
        )


def _validate_ids(values: tuple[str, ...] | None, name: str, expected_type: str) -> None:
    if values is None:
        return
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate IDs")
    for value in values:
        if strict_entity_id(value, expected_type) is None:
            raise ValueError(f"{name} contains an invalid {expected_type} ID")


__all__ = [
    "ActivityConstraintMetadata",
    "ActivityInstructionRefs",
    "ActivityRecord",
    "ActivityResultMetadata",
    "ExamRecord",
]
