"""C-13 (partial, R1b-1): the local UI server binds loopback only.

The v2 UI server starts from the v1.2 Settings server (implementation spec
§1.1, §10.1). Its socket reservation must bind exactly 127.0.0.1 on an
OS-assigned port. The remaining C-13 assertions (refusing 0.0.0.0 in config,
remote MCP transports not loaded) activate with R2 when the UI server is
rewritten; see test_v2_pending_contracts.py.
"""

from __future__ import annotations

import pytest

from syllva.settings import launcher

pytestmark = pytest.mark.contract


def test_loopback_host_constant_is_ipv4_loopback() -> None:
    assert launcher.LOOPBACK_HOST == "127.0.0.1"


def test_reserved_socket_binds_loopback_on_ephemeral_port() -> None:
    sock = launcher.reserve_loopback_socket()
    try:
        host, port = sock.getsockname()[:2]
        assert host == "127.0.0.1"
        assert port != 0
    finally:
        sock.close()
