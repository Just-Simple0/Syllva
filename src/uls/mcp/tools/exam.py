"""Read-only ``uls.get_exam_context`` wrapper."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from uls.domain.models import ContextPackage
from uls.mcp.schemas import (
    CONTEXT_PACKAGE_RESPONSE_SCHEMA,
    EXAM_CONTEXT_REQUEST_SCHEMA,
    ContextPackageResponse,
    ExamContextRequest,
    serialize_context_package,
)

TOOL_NAME = "uls.get_exam_context"
INPUT_SCHEMA = EXAM_CONTEXT_REQUEST_SCHEMA
OUTPUT_SCHEMA = CONTEXT_PACKAGE_RESPONSE_SCHEMA


def parse_exam_request(
    request: ExamContextRequest | Mapping[str, Any] | str,
    *,
    query: str | None = None,
    caller_scope: str | None = None,
) -> ExamContextRequest:
    if isinstance(request, ExamContextRequest):
        if query is not None or caller_scope is not None:
            raise TypeError("request object already contains query/caller_scope")
        return request
    if isinstance(request, Mapping):
        if query is not None or caller_scope is not None:
            raise TypeError("mapping request cannot be combined with query/caller_scope")
        return ExamContextRequest.from_mapping(request)
    return ExamContextRequest(request, query=query, caller_scope=caller_scope)


class ExamContextTool:
    """Callable direct wrapper used by tests and a future Phase 8 transport."""

    name = TOOL_NAME
    input_schema = INPUT_SCHEMA
    output_schema = OUTPUT_SCHEMA

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def invoke(
        self,
        request: ExamContextRequest | Mapping[str, Any] | str,
        *,
        query: str | None = None,
        caller_scope: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_exam_request(
            request, query=query, caller_scope=caller_scope
        )
        package = self.invoke_package(parsed)
        return ContextPackageResponse(package).as_dict()

    def invoke_package(self, request: ExamContextRequest) -> ContextPackage:
        if not isinstance(request, ExamContextRequest):
            raise TypeError("exam tool requires ExamContextRequest")
        return cast(
            ContextPackage,
            self.engine.get_exam_context(
                request.exam_id,
                query=request.query,
                caller_scope=request.caller_scope,
            ),
        )

    __call__ = invoke


def get_exam_context(
    engine: Any,
    request: ExamContextRequest | Mapping[str, Any] | str,
    *,
    query: str | None = None,
    caller_scope: str | None = None,
) -> dict[str, Any]:
    """Invoke the direct engine method and return canonical wire data."""

    return ExamContextTool(engine).invoke(
        request, query=query, caller_scope=caller_scope
    )


invoke_exam_context = get_exam_context
serialize_exam_context = serialize_context_package


__all__ = [
    "INPUT_SCHEMA",
    "OUTPUT_SCHEMA",
    "TOOL_NAME",
    "ExamContextTool",
    "get_exam_context",
    "invoke_exam_context",
    "parse_exam_request",
    "serialize_exam_context",
]
