"""Direct read-only invocations using the same validated MCP registry."""
from __future__ import annotations

from typing import Any


def invoke_read_only(name: str, engine: Any, arguments: Any = None, *,
                     caller_scope: str = 'local') -> dict[str, Any]:
    from uls.mcp.server import ReadOnlyMCP
    return ReadOnlyMCP(engine).invoke(name, arguments, caller_scope=caller_scope)
