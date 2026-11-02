"""Local stdio MCP transport; stdout is reserved for protocol messages."""
from __future__ import annotations

import asyncio
from typing import Any


async def serve_stdio(server: Any) -> None:
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_local(registry: Any) -> None:
    asyncio.run(serve_stdio(registry.sdk_server()))
