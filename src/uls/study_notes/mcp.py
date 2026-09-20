"""StudyNoteMCP -- interim local-stdio adapter around StudyNoteSubmissionCore.

Injects one deployment-configured caller_context for this interim harness
(plan section 2); the transport-neutral core itself never assumes a local
channel. Mirrors uls.mcp.server.ReadOnlyMCP list_tools/invoke/sdk_server
shape so the existing uls.mcp.transports.local.run_local(registry) can serve
this registry directly (calls registry.sdk_server()).

Error messages returned to the caller are never a raw str(exc) for an
unclassified exception -- only a small set of this module own, content-free
validation errors ever surface their own message; everything else gets one
generic, code-scoped message, matching ReadOnlyMCP safety.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .core import (
    CallerMismatchError,
    DraftTooLargeError,
    StudyNoteSubmissionCore,
    UnknownRequestError,
)
from .evidence import EvidenceWaitingError, InvalidSelectionError
from .identity import TRANSCRIPT_ONLY, UnknownEvidenceModeError
from .store import DifferentPayloadReplayError, GrantUnavailableError

_LOG = logging.getLogger("uls.study_notes.mcp")
_MAX_ARRAY_ITEMS = 64
_MAX_ARRAY_ITEM_LEN = 512


@dataclass(frozen=True)
class _ToolDefinition:
    name: str
    description: str
    required: tuple[str, ...]
    strings: tuple[str, ...]
    booleans: tuple[str, ...] = ()
    string_arrays: tuple[str, ...] = ()
    max_string_len: int = 8000

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                **{
                    key: {"type": "string", "minLength": 1,
                          "maxLength": self.max_string_len if key == "draft_text" else 8000}
                    for key in self.strings
                },
                **{key: {"type": "boolean"} for key in self.booleans},
                **{
                    key: {"type": "array",
                          "items": {"type": "string", "minLength": 1, "maxLength": _MAX_ARRAY_ITEM_LEN},
                          "maxItems": _MAX_ARRAY_ITEMS}
                    for key in self.string_arrays
                },
            },
            "required": list(self.required),
            "additionalProperties": False,
        }


def _tools(max_draft_chars: int) -> tuple[_ToolDefinition, ...]:
    return (
        _ToolDefinition(
            "request_study_note",
            "Submit a client-origin study-note request. evidence_mode defaults to "
            "transcript-only.v1 when omitted.",
            ("idempotency_key", "session_id"),
            ("idempotency_key", "session_id", "evidence_mode", "learner_request"),
            string_arrays=("selected_materials",),
        ),
        _ToolDefinition(
            "get_study_note_status", "Read status/keys for a submitted request.",
            ("client_request_id",), ("client_request_id",),
        ),
        _ToolDefinition(
            "get_study_note_evidence", "Read the worker-prepared bounded evidence package.",
            ("client_request_id",), ("client_request_id",),
        ),
        _ToolDefinition(
            "submit_study_note_draft", "Submit an untrusted AI-generated draft for one open grant.",
            ("grant_id", "draft_text"), ("grant_id", "draft_text"),
            max_string_len=max_draft_chars,
        ),
        _ToolDefinition(
            "cancel_study_note_request", "Request cancellation of the caller-owned request.",
            ("client_request_id",), ("client_request_id",),
        ),
    )


# Exception types whose own message is a static, content-free validation
# reason safe to return verbatim.  Anything not in this map gets one
# generic per-code message instead of its own str(exc).
_SAFE_ERRORS: dict[type, str] = {
    UnknownRequestError: "NOT_FOUND",
    CallerMismatchError: "POLICY_DENIED",
    DraftTooLargeError: "INVALID_ARGUMENT",
    UnknownEvidenceModeError: "INVALID_ARGUMENT",
    DifferentPayloadReplayError: "INVALID_ARGUMENT",
    GrantUnavailableError: "POLICY_DENIED",
    EvidenceWaitingError: "WAITING_CONTEXT",
    InvalidSelectionError: "INVALID_ARGUMENT",
}

_GENERIC_MESSAGES: dict[str, str] = {
    "NOT_FOUND": "The referenced request or grant is unknown.",
    "POLICY_DENIED": "This action is not permitted for the calling context.",
    "INVALID_ARGUMENT": "Invalid tool input.",
    "WAITING_CONTEXT": "Evidence for this request is not currently available.",
    "PROVIDER_UNAVAILABLE": "Study-note submission service is unavailable.",
}


def _error_payload(exc: Exception) -> dict[str, Any]:
    for exc_type, code in _SAFE_ERRORS.items():
        if isinstance(exc, exc_type):
            return {"code": code, "message": str(exc)}
    if isinstance(exc, (ValueError, TypeError)):
        return {"code": "INVALID_ARGUMENT", "message": _GENERIC_MESSAGES["INVALID_ARGUMENT"]}
    return {"code": "PROVIDER_UNAVAILABLE", "message": _GENERIC_MESSAGES["PROVIDER_UNAVAILABLE"]}


def _validate_args(definition: _ToolDefinition, args: Any) -> str | None:
    """
    Return an error message, or None if args are valid.

    Checks real types/lengths per field, not only key presence, matching
    ReadOnlyMCP stricter schema enforcement.
    """
    allowed = set(definition.strings) | set(definition.booleans) | set(definition.string_arrays)
    if not isinstance(args, dict):
        return "arguments must be an object"
    if set(args) - allowed:
        return "unknown argument"
    if any(key not in args for key in definition.required):
        return "missing required argument"
    for key in definition.strings:
        if key not in args:
            continue
        value = args[key]
        limit = definition.max_string_len if key == "draft_text" else 8000
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return f"{key} must be a non-empty string within length limits"
    for key in definition.booleans:
        if key in args and type(args[key]) is not bool:
            return f"{key} must be a boolean"
    for key in definition.string_arrays:
        if key not in args:
            continue
        value = args[key]
        if not isinstance(value, list) or len(value) > _MAX_ARRAY_ITEMS:
            return f"{key} must be an array of at most {_MAX_ARRAY_ITEMS} strings"
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > _MAX_ARRAY_ITEM_LEN:
                return f"{key} items must be non-empty strings within length limits"
    return None


class StudyNoteMCP:
    """One local-stdio submission surface, separate from the read-only search
    registry (never merged into uls.mcp.server.TOOLS)."""

    def __init__(self, core: StudyNoteSubmissionCore, *, caller_context: str) -> None:
        if not isinstance(caller_context, str) or not caller_context.strip():
            raise ValueError("caller_context must be a non-empty string")
        self.core = core
        self.caller_context = caller_context
        self._tools = _tools(core.config.max_draft_chars)
        self._by_name = {"uls_submit." + t.name: t for t in self._tools}
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self.core.store.close()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "uls_submit." + t.name,
                "description": t.description,
                "inputSchema": t.schema(),
                "annotations": {"readOnlyHint": t.name.startswith("get_"),
                                "destructiveHint": False, "idempotentHint": t.name != "submit_study_note_draft",
                                "openWorldHint": False},
            }
            for t in self._tools
        ]

    def invoke(self, name: str, arguments: Any = None) -> dict[str, Any]:
        definition = self._by_name.get(name)
        if definition is None:
            return {"error": {"code": "POLICY_DENIED", "message": "Unknown tool."}}
        args = {} if arguments is None else arguments
        problem = _validate_args(definition, args)
        if problem is not None:
            return {"error": {"code": "INVALID_ARGUMENT", "message": problem}}
        try:
            method = getattr(self.core, definition.name)
            kwargs = dict(args)
            kwargs["caller_context"] = self.caller_context
            if definition.name == "request_study_note" and "evidence_mode" not in kwargs:
                kwargs["evidence_mode"] = TRANSCRIPT_ONLY
            result = method(**kwargs)
            _LOG.info("tool=%s result=ok", name)
            return result if isinstance(result, dict) else {"result": result}
        except Exception as exc:  # noqa: BLE001 - no raw provider/body detail crosses this boundary
            payload = _error_payload(exc)
            _LOG.info("tool=%s result=%s", name, payload["code"])
            return {"error": payload}

    def sdk_server(self) -> Any:
        """MCP SDK server; used only by uls.mcp.transports.local.run_local."""
        from mcp import types
        from mcp.server.lowlevel import Server

        async def list_tools(context: Any, params: Any) -> Any:
            return types.ListToolsResult(tools=[types.Tool(**item) for item in self.list_tools()])

        async def call_tool(context: Any, params: Any) -> Any:
            async with self._lock:
                result = self.invoke(params.name, params.arguments)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                structured_content=result, is_error="error" in result,
            )
        return Server("uls_submit", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


__all__ = ["StudyNoteMCP"]
