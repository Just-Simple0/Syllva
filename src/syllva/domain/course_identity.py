"""Exact Course relation and Course Key identity helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import CourseKeyParseError
from .ids import CourseKey, parse_course_key, strict_entity_id


@dataclass(frozen=True)
class CourseIdentity:
    """The provider relation page and validated academic key together."""

    relation_page_id: str
    course_key: str
    parsed: CourseKey

    def as_dict(self) -> dict[str, str]:
        return {
            "relation_page_id": self.relation_page_id,
            "course_key": self.course_key,
        }


def relation_page_ids(relation_value: Any) -> tuple[str, ...]:
    """Return every physical relation entry's page ID in order.

    This intentionally preserves duplicate entries.  Exactly-one validation
    belongs to :func:`resolve_course_relation`; collapsing a duplicate before
    cardinality validation would turn malformed graph state into valid state.
    """

    values, valid = _relation_page_ids(relation_value, nested=False)
    # Returning only the complete set is important.  A provider row such as
    # ``[{"id": "course-a"}, {}]`` must not look like a valid one-relation
    # value merely because the malformed member contributes no ID.
    return values if valid else ()


def strict_relation_page_ids(
    relation_value: Any,
    *,
    expected_type: str,
) -> tuple[str, ...] | None:
    """Parse a provider relation while preserving absence and explicit empty.

    Notion relation property values carry a type-specific ``relation`` list
    and may carry ``has_more``.  A truncated list cannot authorize a complete
    graph scope, so ``has_more`` must be explicitly false when supplied.
    """

    if relation_value is None:
        return None
    if not isinstance(expected_type, str) or len(expected_type) != 1:
        raise ValueError("expected relation entity type is invalid")
    if isinstance(relation_value, Mapping):
        allowed = {"id", "type", "relation", "relations", "results", "has_more"}
        if any(key not in allowed for key in relation_value):
            raise ValueError("relation property contains unsupported fields")
        if "has_more" in relation_value:
            has_more = relation_value["has_more"]
            if type(has_more) is not bool or has_more:
                raise ValueError("relation property is truncated or has invalid has_more")
        type_value = relation_value.get("type")
        if type_value is not None and (
            not isinstance(type_value, str) or type_value.casefold() != "relation"
        ):
            raise ValueError("relation property has an invalid type")
        relation_keys = [
            key for key in ("relation", "relations", "results") if key in relation_value
        ]
        if len(relation_keys) != 1:
            raise ValueError("relation property must contain exactly one relation list")
        values = relation_value[relation_keys[0]]
    elif isinstance(relation_value, Sequence) and not isinstance(
        relation_value, (str, bytes, bytearray)
    ):
        values = relation_value
    else:
        raise TypeError("relation value is malformed")

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise TypeError("relation value must contain a list")
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            if set(value) != {"id"}:
                raise ValueError("relation member is malformed")
            entity_id = value["id"]
        else:
            entity_id = value
        if not isinstance(entity_id, str) or strict_entity_id(entity_id, expected_type) is None:
            raise ValueError("relation member has an invalid logical ID")
        if entity_id in result:
            raise ValueError("relation contains duplicate logical IDs")
        result.append(entity_id)
    return tuple(result)


def strict_single_relation_page_id(relation_value: Any) -> str | None:
    """Parse one authoritative opaque Course relation without truncation.

    Course page IDs are provider opaque identifiers, so they cannot use the
    logical ``strict_entity_id`` type check used for Session and Material
    relations.  This boundary still requires the documented relation shape,
    exactly one physical member, and an explicit non-truncated result.
    """

    if relation_value is None:
        return None
    if not isinstance(relation_value, Mapping):
        raise TypeError("Course relation value is malformed")
    allowed = {"id", "type", "relation", "relations", "results", "has_more"}
    if any(key not in allowed for key in relation_value):
        raise ValueError("Course relation contains unsupported fields")
    if "id" in relation_value:
        property_id = relation_value["id"]
        if (
            not isinstance(property_id, str)
            or not property_id
            or property_id != property_id.strip()
        ):
            raise ValueError("Course relation has an invalid property ID")
    if "has_more" in relation_value:
        has_more = relation_value["has_more"]
        if type(has_more) is not bool or has_more:
            raise ValueError("Course relation is truncated or has invalid has_more")
    type_value = relation_value.get("type")
    if type_value is not None and (
        not isinstance(type_value, str) or type_value.casefold() != "relation"
    ):
        raise ValueError("Course relation has an invalid type")
    relation_keys = [
        key for key in ("relation", "relations", "results") if key in relation_value
    ]
    if len(relation_keys) != 1:
        raise ValueError("Course relation must contain exactly one relation list")
    values = relation_value[relation_keys[0]]
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise TypeError("Course relation must contain a list")
    if len(values) == 0:
        return None
    if len(values) != 1:
        raise ValueError("Course relation must contain exactly one page")
    member = values[0]
    if not isinstance(member, Mapping) or set(member) != {"id"}:
        raise ValueError("Course relation member is malformed")
    page_id = member["id"]
    if not isinstance(page_id, str) or not page_id or page_id != page_id.strip():
        raise ValueError("Course relation member has an invalid opaque page ID")
    return page_id


def resolve_course_relation(relation_value: Any) -> str | None:
    """Extract one and only one Course relation page ID, else ``None``."""

    values = relation_page_ids(relation_value)
    if len(values) != 1 or not values[0]:
        return None
    return values[0]


def _relation_page_ids(value: Any, *, nested: bool) -> tuple[tuple[str, ...], bool]:
    """Return IDs plus a structural-validity bit for one relation value."""

    if value is None:
        # Top-level null means a missing relation.  A null member inside a
        # relation list is malformed and must invalidate the whole list.
        return (), not nested
    if isinstance(value, str):
        stripped = value.strip()
        return ((stripped,), True) if stripped else ((), False)
    if isinstance(value, Mapping):
        for key in ("relation", "relations", "results", "Course", "course"):
            if key in value:
                return _relation_page_ids(value[key], nested=True)
        for key in ("id", "ID", "page_id", "pageId"):
            if key in value:
                return _relation_page_ids(value[key], nested=True)
        return (), False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[str] = []
        for item in value:
            item_values, item_valid = _relation_page_ids(item, nested=True)
            if not item_valid:
                return (), False
            result.extend(item_values)
        return tuple(result), True
    for name in ("id", "ID", "page_id", "pageId"):
        if hasattr(value, name):
            return _relation_page_ids(getattr(value, name), nested=True)
    return (), False


def course_key_of(course_record: Any) -> str | None:
    """Read the Course's own ``Course Key`` Rich text value."""

    value = _field(course_record, "Course Key", "course_key")
    # Course Key is an identity, not display text.  Preserve the provider's
    # exact string so parse_course_key can reject surrounding whitespace and
    # follow-up freshness can observe a raw in-place edit.
    if isinstance(value, str) and value and value.strip():
        return value
    return None


def validate_course_record(course_record: Any, relation_page_id: str) -> CourseIdentity | None:
    """Validate Course Key and its Code/Section/Semester components."""

    if not isinstance(relation_page_id, str) or not relation_page_id.strip():
        return None
    key_text = course_key_of(course_record)
    if key_text is None:
        return None
    try:
        parsed = parse_course_key(key_text)
    except CourseKeyParseError:
        return None
    code = _exact_component(_field(course_record, "Code", "code"))
    section = _exact_component(_field(course_record, "Section", "section"))
    semester = _exact_component(_field(course_record, "Semester", "semester"))
    if code != parsed.code or section != parsed.section or semester != parsed.semester:
        return None
    return CourseIdentity(relation_page_id.strip(), str(parsed), parsed)


def _field(record: Any, *names: str) -> Any:
    if record is None:
        return None
    source: Any = record
    if isinstance(record, Mapping) and isinstance(record.get("properties"), Mapping):
        source = record["properties"]
    elif isinstance(getattr(record, "properties", None), Mapping):
        source = record.properties
    wanted = {_normal(name) for name in names}
    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, str) and _normal(key) in wanted:
                return _unwrap(value)
    else:
        for name in names:
            if hasattr(source, name):
                return _unwrap(getattr(source, name))
            snake = name.casefold().replace(" ", "_")
            if hasattr(source, snake):
                return _unwrap(getattr(source, snake))
    return None


def _unwrap(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "value" in value and len(value) == 1:
            return _unwrap(value["value"])
        for key in ("title", "rich_text", "select", "status", "number", "checkbox"):
            if key in value and len(value) <= 2:
                inner = value[key]
                if key in {"title", "rich_text"} and isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
                    parts: list[str] = []
                    for item in inner:
                        if isinstance(item, Mapping):
                            text = item.get("plain_text")
                            if text is None and isinstance(item.get("text"), Mapping):
                                text = item["text"].get("content")
                            if text is not None:
                                parts.append(str(text))
                        elif item is not None:
                            parts.append(str(item))
                    return "".join(parts)
                return _unwrap(inner)
        if "name" in value and len(value) <= 2:
            return value["name"]
        if "plain_text" in value and len(value) <= 2:
            return value["plain_text"]
        if "content" in value and len(value) <= 2:
            return value["content"]
    return value


def _text(value: Any) -> str | None:
    value = _unwrap(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _exact_component(value: Any) -> str | None:
    value = _unwrap(value)
    if isinstance(value, str) and value and value.strip():
        return value
    return None


def _normal(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


__all__ = [
    "CourseIdentity",
    "course_key_of",
    "parse_course_key",
    "relation_page_ids",
    "resolve_course_relation",
    "strict_relation_page_ids",
    "strict_single_relation_page_id",
    "validate_course_record",
]
