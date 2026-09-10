"""Typed Phase 5 MCP request/response schemas.

This module is intentionally a transport-neutral boundary.  The Phase 8
server may adapt these schemas to an MCP SDK, while direct tests can invoke
the same read-only tool wrappers without starting a server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from uls.domain.errors import PolicyViolation
from uls.domain.ids import strict_entity_id
from uls.domain.models import ContextPackage
from uls.retrieval.schemas import context_package_to_dict


@dataclass(frozen=True)
class ContextRequest:
    """Common strict request fields for context-producing tools."""

    entity_id: str
    query: str | None = None
    caller_scope: str | None = None
    entity_type: ClassVar[str] = ""
    tool_name: ClassVar[str] = ""

    def __post_init__(self) -> None:
        if strict_entity_id(self.entity_id, self.entity_type) != self.entity_id:
            raise PolicyViolation(
                f"{self.tool_name} requires a canonical {self.entity_type} entity_id"
            )
        for value, name in ((self.query, "query"), (self.caller_scope, "caller_scope")):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise PolicyViolation(
                    f"{self.tool_name} {name} must be a non-empty string or null"
                )

    @classmethod
    def from_mapping(cls: type[Self], value: Mapping[str, Any]) -> Self:
        if not isinstance(value, Mapping):
            raise TypeError(f"{cls.tool_name} request must be a mapping")
        allowed = {cls.id_field(), "query", "caller_scope"}
        unknown = set(value).difference(allowed)
        if unknown:
            raise PolicyViolation(
                f"{cls.tool_name} request contains unknown fields: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        entity_id = value.get(cls.id_field())
        if not isinstance(entity_id, str):
            raise PolicyViolation(
                f"{cls.tool_name} request requires a string {cls.id_field()}"
            )
        return cls(
            entity_id=entity_id,
            query=value.get("query"),
            caller_scope=value.get("caller_scope"),
        )

    @classmethod
    def id_field(cls) -> str:
        return "entity_id"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {self.id_field(): self.entity_id}
        if self.query is not None:
            result["query"] = self.query
        if self.caller_scope is not None:
            result["caller_scope"] = self.caller_scope
        return result

    to_dict = as_dict


@dataclass(frozen=True, init=False)
class ExamContextRequest(ContextRequest):
    entity_type: ClassVar[str] = "E"
    tool_name: ClassVar[str] = "uls.get_exam_context"

    def __init__(
        self,
        exam_id: str | None = None,
        query: str | None = None,
        caller_scope: str | None = None,
        *,
        entity_id: str | None = None,
    ) -> None:
        if exam_id is not None and entity_id is not None and exam_id != entity_id:
            raise PolicyViolation("exam_id and entity_id disagree")
        object.__setattr__(self, "entity_id", exam_id if exam_id is not None else entity_id)
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "caller_scope", caller_scope)
        ContextRequest.__post_init__(self)

    @property
    def exam_id(self) -> str:
        return self.entity_id

    @classmethod
    def id_field(cls) -> str:
        return "exam_id"


@dataclass(frozen=True, init=False)
class ActivityContextRequest(ContextRequest):
    entity_type: ClassVar[str] = "A"
    tool_name: ClassVar[str] = "uls.get_activity_context"

    def __init__(
        self,
        activity_id: str | None = None,
        query: str | None = None,
        caller_scope: str | None = None,
        *,
        entity_id: str | None = None,
    ) -> None:
        if activity_id is not None and entity_id is not None and activity_id != entity_id:
            raise PolicyViolation("activity_id and entity_id disagree")
        object.__setattr__(self, "entity_id", activity_id if activity_id is not None else entity_id)
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "caller_scope", caller_scope)
        ContextRequest.__post_init__(self)

    @property
    def activity_id(self) -> str:
        return self.entity_id

    @classmethod
    def id_field(cls) -> str:
        return "activity_id"


@dataclass(frozen=True)
class ContextPackageResponse:
    """Serialized response wrapper for a read-only context tool."""

    package: ContextPackage

    def __post_init__(self) -> None:
        if not isinstance(self.package, ContextPackage):
            raise TypeError("context response requires a ContextPackage")

    def as_dict(self) -> dict[str, Any]:
        return context_package_to_dict(self.package)

    to_dict = as_dict


def serialize_context_package(package: ContextPackage) -> dict[str, Any]:
    """Serialize the canonical context package without adding transport fields."""

    return ContextPackageResponse(package).as_dict()


EXAM_CONTEXT_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["exam_id"],
    "properties": {
        "exam_id": {"type": "string", "pattern": r"^[A-Z][A-Z0-9]*-E[0-9]{2}$"},
        "query": {"type": ["string", "null"]},
        "caller_scope": {"type": ["string", "null"]},
    },
}

ACTIVITY_CONTEXT_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["activity_id"],
    "properties": {
        "activity_id": {"type": "string", "pattern": r"^[A-Z][A-Z0-9]*-A[0-9]{2}$"},
        "query": {"type": ["string", "null"]},
        "caller_scope": {"type": ["string", "null"]},
    },
}

CONTEXT_PACKAGE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "protocol_version",
        "context_id",
        "entity",
        "scope",
        "sources",
        "professor_signals",
        "user_context",
        "warnings",
    ],
    "properties": {
        "protocol_version": {"type": "string"},
        "context_id": {"type": ["string", "null"]},
        "entity": {"type": "object"},
        "scope": {"type": "object"},
        "sources": {"type": "array"},
        "professor_signals": {"type": "array"},
        "user_context": {"type": "array"},
        "warnings": {"type": "array"},
    },
}


# Descriptive aliases for callers that use the frozen tool vocabulary.
ExamContextInput = ExamContextRequest
ActivityContextInput = ActivityContextRequest
ContextPackageOutput = ContextPackageResponse
serialize_context_response = serialize_context_package


__all__ = [
    "ACTIVITY_CONTEXT_REQUEST_SCHEMA",
    "CONTEXT_PACKAGE_RESPONSE_SCHEMA",
    "EXAM_CONTEXT_REQUEST_SCHEMA",
    "ActivityContextInput",
    "ActivityContextRequest",
    "ContextPackageOutput",
    "ContextPackageResponse",
    "ContextRequest",
    "ExamContextInput",
    "ExamContextRequest",
    "serialize_context_package",
    "serialize_context_response",
]
