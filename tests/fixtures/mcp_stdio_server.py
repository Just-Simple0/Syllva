"""Subprocess fixture: real stdio transport, synthetic academic providers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'contract'))

from test_get_activity_context import _engine

from uls.mcp.server import ReadOnlyMCP
from uls.mcp.transports.local import run_local

if __name__ == '__main__':
    engine, _, _ = _engine()
    run_local(ReadOnlyMCP(engine))
