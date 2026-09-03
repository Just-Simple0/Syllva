"""Canonical course and entity identifiers for ULS."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import CourseKeyParseError, EntityIdParseError


# The frozen identifiers are deliberately strict and canonical.  In
# particular, IDs are upper-case and the entity sequence is exactly two
# digits.  Allocation policy may choose the first sequence, but the parser
# only enforces the identifier grammar itself.
_COURSE_KEY_PATTERN = re.compile(
    r"(?P<semester>[0-9]{4}-[0-9]+)_(?P<code>[A-Z][A-Z0-9]*)-(?P<section>[A-Z0-9]+)\Z"
)
_ENTITY_ID_PATTERN = re.compile(
    r"(?P<course_code>[A-Z][A-Z0-9]*)-(?P<entity_type>[A-Z])(?P<sequence>[0-9]{2})\Z"
)


def _course_key_text(semester: object, code: object, section: object) -> str | None:
    if not all(isinstance(value, str) for value in (semester, code, section)):
        return None
    return f"{semester}_{code}-{section}"


@dataclass(frozen=True)
class CourseKey:
    """Parsed ``{semester}_{course_code}-{section}`` value."""

    semester: str
    code: str
    section: str

    def __post_init__(self) -> None:
        text = _course_key_text(self.semester, self.code, self.section)
        if text is None or _COURSE_KEY_PATTERN.fullmatch(text) is None:
            raise CourseKeyParseError(f"Invalid course key: {text!r}")

    @property
    def course_code(self) -> str:
        """Alias matching the placeholder name used in the grammar."""

        return self.code

    def __str__(self) -> str:
        return f"{self.semester}_{self.code}-{self.section}"


@dataclass(frozen=True)
class EntityId:
    """Parsed ``{COURSE_CODE}-{TYPE}{NN}`` value."""

    course_code: str
    entity_type: str
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.course_code, str) or not isinstance(self.entity_type, str):
            raise EntityIdParseError("Entity ID components must be strings")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise EntityIdParseError("Entity ID sequence must be an integer")
        if self.sequence < 0 or self.sequence > 99:
            raise EntityIdParseError("Entity ID sequence must be in the range 00..99")
        text = f"{self.course_code}-{self.entity_type}{self.sequence:02d}"
        if _ENTITY_ID_PATTERN.fullmatch(text) is None:
            raise EntityIdParseError(f"Invalid entity ID: {text!r}")

    @property
    def sequence_number(self) -> int:
        return self.sequence

    def __str__(self) -> str:
        return f"{self.course_code}-{self.entity_type}{self.sequence:02d}"


def parse_course_key(text: str) -> CourseKey:
    """Parse a canonical ULS course key.

    The parser does not trim or rewrite input: surrounding whitespace and
    alternate path/URL forms are invalid identifiers.
    """

    if not isinstance(text, str):
        raise CourseKeyParseError(f"Course key must be a string, got {type(text).__name__}")
    match = _COURSE_KEY_PATTERN.fullmatch(text)
    if match is None:
        raise CourseKeyParseError(f"Invalid course key: {text!r}")
    return CourseKey(
        semester=match.group("semester"),
        code=match.group("code"),
        section=match.group("section"),
    )


def parse_entity_id(text: str) -> EntityId:
    """Parse a canonical ULS entity ID."""

    if not isinstance(text, str):
        raise EntityIdParseError(f"Entity ID must be a string, got {type(text).__name__}")
    match = _ENTITY_ID_PATTERN.fullmatch(text)
    if match is None:
        raise EntityIdParseError(f"Invalid entity ID: {text!r}")
    return EntityId(
        course_code=match.group("course_code"),
        entity_type=match.group("entity_type"),
        sequence=int(match.group("sequence")),
    )


__all__ = ["CourseKey", "EntityId", "parse_course_key", "parse_entity_id"]
