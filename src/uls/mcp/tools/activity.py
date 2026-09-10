"""Read-only ``uls.get_activity_context`` wrapper."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from uls.domain.models import ContextPackage
from uls.mcp.schemas import (
    ACTIVITY_CONTEXT_REQUEST_SCHEMA,
    CONTEXT_PACKAGE_RESPONSE_SCHEMA,
    ActivityContextRequest,
    ContextPackageResponse,
    serialize_context_package,
)

TOOL_NAME = "uls.get_activity_context"
INPUT_SCHEMA = ACTIVITY_CONTEXT_REQUEST_SCHEMA
OUTPUT_SCHEMA = CONTEXT_PACKAGE_RESPONSE_SCHEMA


def parse_activity_request(
    request: ActivityContextRequest | Mapping[str, Any] | str,
    *,
    query: str | None = None,
    caller_scope: str | None = None,
) -> ActivityContextRequest:
    if isinstance(request, ActivityContextRequest):
        if query is not None or caller_scope is not None:
            raise TypeError("request object already contains query/caller_scope")
        return request
    if isinstance(request, Mapping):
        if query is not None or caller_scope is not None:
            raise TypeError("mapping request cannot be combined with query/caller_scope")
        return ActivityContextRequest.from_mapping(request)
    return ActivityContextRequest(request, query=query, caller_scope=caller_scope)


class ActivityContextTool:
    """Callable direct wrapper used by tests and a future Phase 8 transport."""

    name = TOOL_NAME
    input_schema = INPUT_SCHEMA
    output_schema = OUTPUT_SCHEMA

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def invoke(
        self,
        request: ActivityContextRequest | Mapping[str, Any] | str,
        *,
        query: str | None = None,
        caller_scope: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_activity_request(
            request, query=query, caller_scope=caller_scope
        )
        package = self.invoke_package(parsed)
        return ContextPackageResponse(package).as_dict()

    def invoke_package(self, request: ActivityContextRequest) -> ContextPackage:
        if not isinstance(request, ActivityContextRequest):
            raise TypeError("activity tool requires ActivityContextRequest")
        return cast(
            ContextPackage,
            self.engine.get_activity_context(
                request.activity_id,
                query=request.query,
                caller_scope=request.caller_scope,
            ),
        )

    __call__ = invoke


def get_activity_context(
    engine: Any,
    request: ActivityContextRequest | Mapping[str, Any] | str,
    *,
    query: str | None = None,
    caller_scope: str | None = None,
) -> dict[str, Any]:
    """Invoke the direct engine method and return canonical wire data."""

    return ActivityContextTool(engine).invoke(
        request, query=query, caller_scope=caller_scope
    )


invoke_activity_context = get_activity_context
serialize_activity_context = serialize_context_package


__all__ = [
    "INPUT_SCHEMA",
    "OUTPUT_SCHEMA",
    "TOOL_NAME",
    "ActivityContextTool",
    "get_activity_context",
    "invoke_activity_context",
    "parse_activity_request",
    "serialize_activity_context",
]
