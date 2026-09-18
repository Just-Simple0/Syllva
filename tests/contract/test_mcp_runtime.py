"""Real MCP SDK transport plus read-only/auth boundaries."""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from uls.config.credentials import ResolvedCredentials
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
        require_mcp_credentials(ResolvedCredentials(secrets))


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


def test_remote_app_with_oidc_verifier_authenticates_health_and_mcp_tools():
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    from test_get_activity_context import _engine

    from uls.mcp.transports.oidc import JwksKeyManager, OidcTokenVerifier

    engine, _, _ = _engine()
    config = UlsConfig()
    config.remote_mcp = replace(config.remote_mcp, enabled=True, public_url='https://uls.example/mcp')

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = priv.public_key()
    pem_priv = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    km = JwksKeyManager('https://accounts.google.com', 'https://accounts.google.com/jwks')
    km._cached_keys = {'key-rsa': pub}
    km._cache_expires_at = time.time() + 3600

    verifier = OidcTokenVerifier(
        issuer='https://accounts.google.com',
        audience='my-client',
        authorized_subject='student-owner',
        key_manager=km,
    )

    app = create_remote_app(ReadOnlyMCP(engine), config, credential=None, oidc_verifier=verifier)

    valid_token = jwt.encode(
        {'sub': 'student-owner', 'iss': 'https://accounts.google.com', 'aud': 'my-client', 'iat': int(time.time()), 'exp': int(time.time()) + 300},
        pem_priv, algorithm='RS256', headers={'kid': 'key-rsa'},
    )

    with TestClient(app, base_url='https://uls.example') as client:
        # Missing auth -> 401
        assert client.get('/health').status_code == 401

        # Valid OIDC token -> 200
        resp = client.get('/health', headers={'Authorization': 'Bearer ' + valid_token})
        assert resp.status_code == 200
        assert resp.json()['read_only'] is True

        # MCP tools list -> 11 tools
        rpc_headers = {
            'Authorization': 'Bearer ' + valid_token,
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            'MCP-Protocol-Version': '2025-11-25',
        }
        mcp_resp = client.post('/mcp', headers=rpc_headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
        assert mcp_resp.status_code == 200
        assert len(mcp_resp.json()['result']['tools']) == 11

        # Tampered / attacker subject -> 401
        attacker_token = jwt.encode(
            {'sub': 'attacker', 'iss': 'https://accounts.google.com', 'aud': 'my-client', 'iat': int(time.time()), 'exp': int(time.time()) + 300},
            pem_priv, algorithm='RS256', headers={'kid': 'key-rsa'},
        )
        assert client.get('/health', headers={'Authorization': 'Bearer ' + attacker_token}).status_code == 401


def test_remote_app_hybrid_prevents_jwt_downgrade_to_bearer():
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    from test_get_activity_context import _engine

    from uls.mcp.transports.oidc import JwksKeyManager, OidcTokenVerifier

    engine, _, _ = _engine()
    config = UlsConfig()
    config.remote_mcp = replace(config.remote_mcp, enabled=True, public_url='https://uls.example/mcp')

    bearer = BearerCredential('b' * 40, time.time() + 600)
    km = JwksKeyManager('https://accounts.google.com', 'https://accounts.google.com/jwks')
    verifier = OidcTokenVerifier(
        issuer='https://accounts.google.com',
        audience='my-client',
        authorized_subject='student-owner',
        key_manager=km,
    )

    app = create_remote_app(ReadOnlyMCP(engine), config, credential=bearer, oidc_verifier=verifier)

    with TestClient(app, base_url='https://uls.example') as client:
        # Valid bearer -> 200
        assert client.get('/health', headers={'Authorization': 'Bearer ' + bearer.token}).status_code == 200

        # Fake JWT with 3 parts but invalid signature -> must fail with 401, never fall back to bearer
        fake_jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.invalidsig'
        assert client.get('/health', headers={'Authorization': 'Bearer ' + fake_jwt}).status_code == 401


def test_dispatch_oidc_mode_never_resolves_remote_bearer_credentials(tmp_path, monkeypatch):
    """rev3 plan section 5.1: auth_mode=='oidc' must resolve zero
    REMOTE_MCP_SECRET / REMOTE_MCP_EXPIRES_AT credentials -- they must
    never even enter the CredentialResolver.resolve() input set for the
    'uls mcp remote' dispatch path, matching the doctor()-side exclusion.
    """
    import yaml

    from uls.behavior import asset_root
    from uls.cli.main import dispatch, initialize, parser
    from uls.config.credentials import CredentialResolver

    raw = yaml.safe_load((asset_root() / 'config.example.yaml').read_text(encoding='utf-8'))
    raw['system']['workspace_dir'] = 'state'
    raw['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
    raw['worker']['enabled'] = False
    cert = tmp_path / 'cert.pem'
    key = tmp_path / 'key.pem'
    cert.write_text('cert', encoding='utf-8')
    key.write_text('key', encoding='utf-8')
    raw['remote_mcp'] = {
        'enabled': True,
        'auth_mode': 'oidc',
        'public_unauthenticated': False,
        'public_url': 'https://uls.example/mcp',
        'tls_certfile': str(cert),
        'tls_keyfile': str(key),
        'oidc': {
            'issuer': 'https://accounts.google.com',
            'audience': 'client-123',
            'authorized_subject': 'sub-student',
        },
    }
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    initialize(cfg_path)

    captured_optional: dict = {}

    def fake_resolve(self, *, required, optional):
        captured_optional.update(optional)
        values = {name: '' for name in required}
        values.update(optional)
        values['GOOGLE_MCP_CREDENTIALS_FILE'] = str(tmp_path / 'google-mcp.json')
        (tmp_path / 'google-mcp.json').write_text('{}', encoding='utf-8')
        values['NOTION_MCP_TOKEN'] = 'mcp-token'
        return values

    monkeypatch.setattr(CredentialResolver, 'resolve', fake_resolve)

    class _FakeRegistry:
        def __init__(self, engine):
            self.engine = engine

    def fake_read_only_mcp(engine):
        return _FakeRegistry(engine)

    monkeypatch.setattr('uls.mcp.server.ReadOnlyMCP', fake_read_only_mcp)
    monkeypatch.setattr('uls.cli.main.build_retrieval', lambda cfg, creds: object())

    captured_run_remote: dict = {}

    def fake_run_remote(registry, cfg, credential=None, *, oidc_verifier=None):
        captured_run_remote['credential'] = credential
        captured_run_remote['oidc_verifier'] = oidc_verifier

    monkeypatch.setattr('uls.mcp.transports.remote.run_remote', fake_run_remote)

    args = parser().parse_args(['--config', str(cfg_path), 'mcp', 'remote'])
    dispatch(args)

    assert 'REMOTE_MCP_SECRET' not in captured_optional
    assert 'REMOTE_MCP_EXPIRES_AT' not in captured_optional
    assert captured_run_remote['credential'] is None
    assert captured_run_remote['oidc_verifier'] is not None
