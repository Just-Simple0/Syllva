"""Real MCP SDK transport plus read-only/auth boundaries."""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from uls.config.errors import ConfigurationError
from uls.config.schema import UlsConfig
from uls.mcp.server import ReadOnlyMCP
from uls.mcp.transports.remote import BearerCredential, create_remote_app
from uls.runtime import require_mcp_credentials

pytestmark = pytest.mark.contract


def test_registry_exact_read_only_schema_and_no_caller_override():
    class Trap:
        def __getattr__(self, name):
            raise AssertionError('unexpected engine access')
    registry = ReadOnlyMCP(Trap())
    tools = registry.list_tools()
    assert len(tools) == 11
    assert all(t['annotations']['readOnlyHint'] for t in tools)
    assert registry.invoke('drive.write', {})['error']['code'] == 'POLICY_DENIED'
    assert registry.invoke('uls.get_source_chunk', {'context_id': 'x', 'locator': 'x',
                                                   'caller_scope': 'other'})['error']['code'] == 'INVALID_ARGUMENT'
    assert registry.invoke('uls.get_session_context', {'session_id': 'COMP319-S05',
                                                        'include_provisional': 'false'})['error']['code'] == 'INVALID_ARGUMENT'
    assert registry.invoke('uls.ping')['ok']


def test_errors_do_not_echo_provider_secrets_or_bodies(caplog):
    class Broken:
        def get_session_context(self, **kwargs):
            raise RuntimeError('Bearer fake-private-value user note body')
    result = ReadOnlyMCP(Broken()).invoke('uls.get_session_context', {'session_id': 'COMP319-S05'})
    assert result['error']['code'] == 'PROVIDER_UNAVAILABLE'
    assert 'fake-private-value' not in str(result) + caplog.text


@pytest.mark.parametrize('secrets', [
    {}, {'NOTION_WORKER_TOKEN': 'worker', 'GOOGLE_WORKER_CREDENTIALS_FILE': '/worker.json'},
    {'NOTION_MCP_TOKEN': 'same', 'NOTION_WORKER_TOKEN': 'same', 'GOOGLE_MCP_CREDENTIALS_FILE': '/ro.json'},
    {'NOTION_MCP_TOKEN': 'ro', 'GOOGLE_MCP_CREDENTIALS_FILE': '/same.json', 'GOOGLE_WORKER_CREDENTIALS_FILE': '/same.json'},
])
def test_mcp_credentials_never_fall_back_to_worker(secrets):
    with pytest.raises(ConfigurationError):
        require_mcp_credentials(secrets)


def remote_fixture():
    pytest.importorskip('mcp')
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    config = UlsConfig()
    config.remote_mcp = replace(config.remote_mcp, enabled=True, public_url='https://uls.example/mcp')
    now = [time.time()]
    credential = BearerCredential('test-only-' + 'a' * 40, now[0] + 600)
    app = create_remote_app(ReadOnlyMCP(engine), config, credential, clock=lambda: now[0])
    return app, credential, now


def test_remote_health_requires_auth_tls_host_origin_and_expiry():
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    app, credential, now = remote_fixture()
    auth = {'Authorization': 'Bearer ' + credential.token}
    with TestClient(app, base_url='https://uls.example') as client:
        assert client.get('/health').status_code == 401
        assert client.get('/health', headers=auth).json()['read_only'] is True
        assert client.get('/health', headers={**auth, 'Origin': 'https://evil.example'}).status_code == 401
        assert client.get('/health', headers={**auth, 'Host': 'evil.example'}).status_code == 401
        assert client.get('http://uls.example/health', headers=auth).status_code == 401
        assert client.get('/health', headers=[('Authorization', 'Bearer ' + credential.token),
                                             ('Authorization', 'Bearer ' + credential.token)]).status_code == 401
        now[0] += 601
        assert client.get('/health', headers=auth).status_code == 401


def test_remote_sdk_lists_tools_and_chains_domain_capability():
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    app, credential, _ = remote_fixture()
    headers = {'Authorization': 'Bearer ' + credential.token,
               'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
               'MCP-Protocol-Version': '2025-11-25'}
    with TestClient(app, base_url='https://uls.example', headers=headers) as client:
        def rpc(method, params=None):
            response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1,
                                                'method': method, 'params': params or {}})
            assert response.status_code == 200, response.text
            return response.json()['result']
        init = rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                  'clientInfo': {'name': 'uls-test', 'version': '1'}})
        assert init['serverInfo']['name'] == 'uls'
        assert len(rpc('tools/list')['tools']) == 11
        context = rpc('tools/call', {'name': 'uls.get_session_context',
                                     'arguments': {'session_id': 'COMP319-S05'}})['structuredContent']
        assert context['sources'], context
        chunk = rpc('tools/call', {'name': 'uls.get_source_chunk', 'arguments': {
            'context_id': context['context_id'], 'locator': context['sources'][0]['locator']}})
        assert not chunk['isError']
        rejected = rpc('tools/call', {'name': 'uls.get_source_chunk', 'arguments': {
            'context_id': context['context_id'], 'locator': 'COMP319-M99:p99'}})
        assert rejected['isError']


@pytest.mark.parametrize('seconds', [-1, 0, 3601, float('inf'), float('nan')])
def test_bearer_expiry_is_bounded(seconds):
    with pytest.raises(ConfigurationError):
        BearerCredential('x' * 40, 1000 + seconds).validate(1000)


def test_real_stdio_subprocess_lists_and_chains_source():
    pytest.importorskip('mcp')
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def scenario():
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/mcp_stdio_server.py'
        params = StdioServerParameters(command=sys.executable, args=[str(fixture)], cwd=str(fixture.parent))
        async with stdio_client(params) as (read, write), ClientSession(read, write, read_timeout_seconds=10) as client:
            initialized = await client.initialize()
            assert initialized.server_info.name == 'uls'
            assert len((await client.list_tools()).tools) == 11
            result = await client.call_tool('uls.get_session_context', {'session_id': 'COMP319-S05'})
            assert not result.is_error
            context = result.structured_content
            assert context and context['sources']
            chunk = await client.call_tool('uls.get_source_chunk', {
                'context_id': context['context_id'], 'locator': context['sources'][0]['locator']})
            assert not chunk.is_error and chunk.structured_content['content']
    asyncio.run(scenario())


def test_context_is_bound_to_transport_identity():
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    registry = ReadOnlyMCP(engine)
    context = registry.invoke('uls.get_session_context', {'session_id': 'COMP319-S05'}, caller_scope='remote:user-a')
    args = {'context_id': context['context_id'], 'locator': context['sources'][0]['locator']}
    assert 'error' in registry.invoke('uls.get_source_chunk', args, caller_scope='local')
    assert 'error' in registry.invoke('uls.get_source_chunk', args, caller_scope='remote:user-b')
    assert 'error' not in registry.invoke('uls.get_source_chunk', args, caller_scope='remote:user-a')
