"""Explicit read-only MCP tool registry, independent of SDK transport."""
from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from uls.domain.errors import UlsError
from uls.domain.models import ContextPackage, EvidenceItem
from uls.mcp.errors import safe_error_message
from uls.retrieval.schemas import context_package_to_dict, evidence_item_to_dict

caller_identity: ContextVar[str] = ContextVar('uls_caller_identity', default='local')
_LOG = logging.getLogger('uls.mcp')


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    required: tuple[str, ...]
    strings: tuple[str, ...]
    booleans: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {
            **{key: {'type': 'string', 'minLength': 1, 'maxLength': 8000} for key in self.strings},
            **{key: {'type': 'boolean'} for key in self.booleans},
        }, 'required': list(self.required), 'additionalProperties': False}


TOOLS = (
    ToolDefinition('ping', 'Connectivity only; no academic data.', (), ()),
    ToolDefinition('resolve_entity', 'Resolve a course-scoped academic entity or return candidates.',
                   ('query',), ('query', 'course_hint', 'entity_type')),
    ToolDefinition('select_resolution', 'Select an issued candidate; expired handles require re-resolution.',
                   ('resolution_id', 'candidate_id'), ('resolution_id', 'candidate_id')),
    ToolDefinition('get_material_context', 'Retrieve bounded source material.',
                   ('material_id',), ('material_id', 'query'), ('include_user_annotations',)),
    ToolDefinition('get_session_context', 'Retrieve transcript and verified material evidence.',
                   ('session_id',), ('session_id', 'query'), ('include_provisional',)),
    ToolDefinition('search_concept', 'Search a bounded set of course sources lexically.',
                   ('course_key', 'concept'), ('course_key', 'concept'), ('include_textbook',)),
    ToolDefinition('get_exam_context', 'Retrieve only evidence within the engine-enforced exam scope.',
                   ('exam_id',), ('exam_id', 'query')),
    ToolDefinition('get_activity_context', 'Retrieve official constraints and exact-ref result evidence.',
                   ('activity_id',), ('activity_id', 'query')),
    ToolDefinition('get_user_context', 'Retrieve USER annotations for an explicit user-related query.',
                   ('entity_id', 'query'), ('entity_id', 'query')),
    ToolDefinition('verify_claim', 'Return factual sources for verification; AI enrichment is not proof.',
                   ('course_key', 'claim'), ('course_key', 'claim', 'entity_hint')),
    ToolDefinition('get_source_chunk', 'Read only a locator authorized by an issued context capability.',
                   ('context_id', 'locator'), ('context_id', 'locator')),
)
_BY_NAME = {'uls.' + tool.name: tool for tool in TOOLS}


def wire(value: Any) -> Any:
    if isinstance(value, ContextPackage):
        return context_package_to_dict(value)
    if isinstance(value, EvidenceItem):
        return evidence_item_to_dict(value)
    if hasattr(value, 'as_dict'):
        return value.as_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


class ReadOnlyMCP:
    def __init__(self, engine: Any) -> None:
        self._engine = engine
        # Serialize provider operations: their SDK clients and SQLite connections
        # need not be thread-safe; ephemeral state remains one process per profile.
        self._lock = asyncio.Lock()

    def list_tools(self) -> list[dict[str, Any]]:
        return [{'name': 'uls.' + d.name, 'description': d.description,
                 'inputSchema': d.schema(),
                 'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                 'idempotentHint': False, 'openWorldHint': False}}
                for d in TOOLS]

    def invoke(self, name: str, arguments: Any = None, *, caller_scope: str = 'local') -> dict[str, Any]:
        definition = _BY_NAME.get(name)
        if definition is None:
            return {'error': {'code': 'POLICY_DENIED', 'message': 'Tool is not in the read-only registry.'}}
        args = {} if arguments is None else arguments
        if (not isinstance(args, dict) or set(args) - set(definition.strings + definition.booleans)
                or any(key not in args for key in definition.required)
                or any(not isinstance(v, str) or not v.strip() or len(v) > 8000
                       for k, v in args.items() if k in definition.strings)
                or any(type(v) is not bool for k, v in args.items() if k in definition.booleans)):
            return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'Arguments do not match the tool schema.'}}
        try:
            if definition.name == 'ping':
                return {'ok': True, 'service': 'uls', 'protocol_version': '1.2'}
            kwargs = dict(args)
            if definition.name not in {'resolve_entity', 'select_resolution'}:
                kwargs['caller_scope'] = caller_scope
            value = wire(getattr(self._engine, definition.name)(**kwargs))
            _LOG.info('tool=%s result=ok', name)
            return value if isinstance(value, dict) else {'result': value}
        except UlsError as exc:
            # No raw provider messages, stack traces, input, notes or credentials.
            _LOG.info('tool=%s result=%s', name, exc.code)
            return {'error': {'code': exc.code, 'message': safe_error_message(exc.code)}}
        except (TypeError, ValueError):
            return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'Invalid tool input.'}}
        except Exception:  # noqa: BLE001 - no raw exceptions may cross MCP
            _LOG.warning('tool=%s result=PROVIDER_UNAVAILABLE', name)
            return {'error': {'code': 'PROVIDER_UNAVAILABLE', 'message': 'Retrieval is unavailable.'}}

    def sdk_server(self) -> Any:
        """MCP Python SDK 2.2 callback API; optional dependency loaded at startup."""
        from mcp import types
        from mcp.server.lowlevel import Server

        async def list_tools(context: Any, params: Any) -> Any:
            return types.ListToolsResult(tools=[types.Tool(**item) for item in self.list_tools()])

        async def call_tool(context: Any, params: Any) -> Any:
            async with self._lock:
                result = self.invoke(params.name, params.arguments, caller_scope=caller_identity.get())
            return types.CallToolResult(
                content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                structured_content=result, is_error='error' in result,
            )
        return Server('uls', version='1.2.0', on_list_tools=list_tools, on_call_tool=call_tool)
