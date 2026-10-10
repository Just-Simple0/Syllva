"""Real MCP SDK transport plus read-only/auth boundaries."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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


def test_mcp_oauth_full_sdk_flow_resource_binding_and_refresh_replay(tmp_path, monkeypatch):
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    from test_get_activity_context import _engine

    from uls.mcp.transports.oauth import (
        GoogleOAuthBrokerProvider,
        OAuthStore,
        canonical_public_identity,
    )
    from uls.mcp.transports.remote import create_mcp_oauth_app

    engine, _, _ = _engine()
    registry = ReadOnlyMCP(engine)
    config = UlsConfig()
    config.system = replace(config.system, workspace_dir=str(tmp_path))
    config.remote_mcp = replace(
        config.remote_mcp,
        enabled=True,
        auth_mode='mcp_oauth',
        edge_mode='direct_tls',
        public_url='https://uls.example/mcp',
        oauth=replace(
            config.remote_mcp.oauth,
            google_client_id='google-client.apps.googleusercontent.com',
            authorized_email='owner@example.com',
        ),
    )
    identity = canonical_public_identity(config.remote_mcp.public_url)
    provider = GoogleOAuthBrokerProvider(
        store=OAuthStore(tmp_path / 'remote-oauth.sqlite3'),
        identity=identity,
        google_client_id=config.remote_mcp.oauth.google_client_id,
        google_client_secret='test-only-google-secret',
        authorized_email=config.remote_mcp.oauth.authorized_email,
    )

    monkeypatch.setattr(provider, '_exchange_google_code_sync', lambda code, txn: 'fake-id-token')

    async def fake_verify(token, *, nonce):
        assert token == 'fake-id-token'
        assert nonce
        return 'google-owner-sub'

    monkeypatch.setattr(provider, '_verify_google_identity', fake_verify)
    app = create_mcp_oauth_app(
        registry, config, google_client_secret='unused-because-provider-injected', provider=provider
    )

    redirect_uri = 'http://127.0.0.1:43123/callback'
    verifier = 'v' * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')

    with TestClient(app, base_url='https://uls.example', follow_redirects=False) as client:
        resource_meta = client.get('/.well-known/oauth-protected-resource/mcp')
        assert resource_meta.status_code == 200
        assert resource_meta.json()['resource'] == identity.resource_uri
        assert resource_meta.json()['authorization_servers'] == [identity.issuer]

        as_meta = client.get('/.well-known/oauth-authorization-server')
        assert as_meta.status_code == 200
        assert as_meta.json()['issuer'] == identity.issuer
        assert as_meta.json()['authorization_endpoint'] == identity.issuer + '/authorize'
        assert as_meta.json()['token_endpoint_auth_methods_supported'] == [
            'none', 'client_secret_post', 'client_secret_basic'
        ]
        assert as_meta.json()['revocation_endpoint_auth_methods_supported'] == [
            'client_secret_post'
        ]
        assert as_meta.json()['authorization_response_iss_parameter_supported'] is True

        unauthenticated = client.post(
            '/mcp',
            headers={
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json',
            },
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}},
        )
        assert unauthenticated.status_code == 401
        assert 'resource_metadata=' in unauthenticated.headers['www-authenticate']

        registration = client.post(
            '/register',
            json={
                'redirect_uris': [redirect_uri],
                'token_endpoint_auth_method': 'none',
                'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'],
                'scope': 'uls:read',
            },
        )
        assert registration.status_code == 201, registration.text
        client_id = registration.json()['client_id']
        assert registration.json().get('client_secret') is None

        denied_authorize = client.get(
            '/authorize',
            params={
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'denied-client-state',
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        denied_upstream_state = parse_qs(urlsplit(denied_authorize.headers['location']).query)['state'][0]
        denied_callback = client.get(
            '/oauth/google/callback',
            params={'state': denied_upstream_state, 'error': 'access_denied'},
        )
        assert denied_callback.status_code == 302
        denied_query = parse_qs(urlsplit(denied_callback.headers['location']).query)
        assert denied_query['error'] == ['access_denied']
        assert denied_query['state'] == ['denied-client-state']
        assert denied_query['iss'] == [identity.issuer]

        failed_authorize = client.get(
            '/authorize',
            params={
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'promotion-failure-state',
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        failed_upstream_state = parse_qs(
            urlsplit(failed_authorize.headers['location']).query
        )['state'][0]
        original_promote = provider.store.promote_client

        def fail_promote(client_id, *, now=None):
            raise sqlite3.OperationalError('test-only store failure')

        monkeypatch.setattr(provider.store, 'promote_client', fail_promote)
        failed_callback = client.get(
            '/oauth/google/callback',
            params={'state': failed_upstream_state, 'code': 'google-code-store-failure'},
        )
        monkeypatch.setattr(provider.store, 'promote_client', original_promote)
        assert failed_callback.status_code == 302
        failed_query = parse_qs(urlsplit(failed_callback.headers['location']).query)
        assert failed_query['error'] == ['server_error']
        assert failed_query['state'] == ['promotion-failure-state']
        assert failed_query['iss'] == [identity.issuer]

        authorize = client.get(
            '/authorize',
            params={
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'client-state',
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        assert authorize.status_code == 302
        google_url = authorize.headers['location']
        google_query = parse_qs(urlsplit(google_url).query)
        assert google_query['prompt'] == ['select_account']
        upstream_state = google_query['state'][0]

        bad_resource = client.get(
            '/authorize',
            params={
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'bad-resource-state',
                'scope': 'uls:read',
                'resource': 'https://evil.example/mcp',
            },
        )
        assert bad_resource.status_code == 302
        assert parse_qs(urlsplit(bad_resource.headers['location']).query)['error'] == ['invalid_target']

        callback = client.get(
            '/oauth/google/callback', params={'state': upstream_state, 'code': 'google-code'}
        )
        assert callback.status_code == 302
        callback_query = parse_qs(urlsplit(callback.headers['location']).query)
        assert callback_query['state'] == ['client-state']
        assert callback_query['iss'] == [identity.issuer]
        authorization_code = callback_query['code'][0]

        wrong_code_resource = client.post(
            '/token',
            data={
                'grant_type': 'authorization_code',
                'code': authorization_code,
                'redirect_uri': redirect_uri,
                'client_id': client_id,
                'code_verifier': verifier,
                'resource': 'https://evil.example/mcp',
            },
        )
        assert wrong_code_resource.status_code == 400
        assert wrong_code_resource.json()['error'] == 'invalid_target'

        token_response = client.post(
            '/token',
            data={
                'grant_type': 'authorization_code',
                'code': authorization_code,
                'redirect_uri': redirect_uri,
                'client_id': client_id,
                'code_verifier': verifier,
                'resource': identity.resource_uri,
            },
        )
        assert token_response.status_code == 200, token_response.text
        tokens = token_response.json()
        access0 = tokens['access_token']
        refresh0 = tokens['refresh_token']

        assert client.get('/health', headers={'Authorization': 'Bearer ' + access0}).status_code == 200

        rpc_headers = {
            'Authorization': 'Bearer ' + access0,
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            'MCP-Protocol-Version': '2025-11-25',
        }

        def rpc(method, params=None):
            response = client.post(
                '/mcp',
                headers=rpc_headers,
                json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
            )
            assert response.status_code == 200, response.text
            return response.json()['result']

        rpc(
            'initialize',
            {
                'protocolVersion': '2025-11-25',
                'capabilities': {},
                'clientInfo': {'name': 'oauth-test', 'version': '1'},
            },
        )
        assert len(rpc('tools/list')['tools']) == 11
        context = rpc(
            'tools/call',
            {'name': 'uls.get_session_context', 'arguments': {'session_id': 'COMP319-S05'}},
        )['structuredContent']
        locator = context['sources'][0]['locator']
        direct_local = registry.invoke(
            'uls.get_source_chunk',
            {'context_id': context['context_id'], 'locator': locator},
            caller_scope='local',
        )
        assert 'error' in direct_local
        assert not rpc(
            'tools/call',
            {
                'name': 'uls.get_source_chunk',
                'arguments': {'context_id': context['context_id'], 'locator': locator},
            },
        )['isError']

        wrong_refresh_resource = client.post(
            '/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh0,
                'client_id': client_id,
                'scope': 'uls:read',
                'resource': 'https://evil.example/mcp',
            },
        )
        assert wrong_refresh_resource.status_code == 400
        assert wrong_refresh_resource.json()['error'] == 'invalid_target'

        refreshed = client.post(
            '/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh0,
                'client_id': client_id,
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        access1 = refreshed.json()['access_token']
        assert client.get('/health', headers={'Authorization': 'Bearer ' + access1}).status_code == 200

        replay = client.post(
            '/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh0,
                'client_id': client_id,
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        assert replay.status_code == 400
        assert replay.json()['error'] == 'invalid_grant'
        assert client.get('/health', headers={'Authorization': 'Bearer ' + access1}).status_code == 401

        confidential_registration = client.post(
            '/register',
            json={
                'redirect_uris': [redirect_uri],
                'token_endpoint_auth_method': 'client_secret_post',
                'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'],
                'scope': 'uls:read',
            },
        )
        assert confidential_registration.status_code == 201, confidential_registration.text
        confidential_client_id = confidential_registration.json()['client_id']
        confidential_secret = confidential_registration.json()['client_secret']
        assert confidential_secret

        confidential_authorize = client.get(
            '/authorize',
            params={
                'client_id': confidential_client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'confidential-state',
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        confidential_upstream_state = parse_qs(
            urlsplit(confidential_authorize.headers['location']).query
        )['state'][0]
        confidential_callback = client.get(
            '/oauth/google/callback',
            params={'state': confidential_upstream_state, 'code': 'google-code-confidential'},
        )
        confidential_code = parse_qs(
            urlsplit(confidential_callback.headers['location']).query
        )['code'][0]

        missing_confidential_secret = client.post(
            '/token',
            data={
                'grant_type': 'authorization_code',
                'code': confidential_code,
                'redirect_uri': redirect_uri,
                'client_id': confidential_client_id,
                'code_verifier': verifier,
                'resource': identity.resource_uri,
            },
        )
        assert missing_confidential_secret.status_code == 401
        assert missing_confidential_secret.json()['error'] == 'invalid_client'

        confidential_tokens = client.post(
            '/token',
            data={
                'grant_type': 'authorization_code',
                'code': confidential_code,
                'redirect_uri': redirect_uri,
                'client_id': confidential_client_id,
                'client_secret': confidential_secret,
                'code_verifier': verifier,
                'resource': identity.resource_uri,
            },
        )
        assert confidential_tokens.status_code == 200, confidential_tokens.text
        confidential_access = confidential_tokens.json()['access_token']
        assert client.get(
            '/health', headers={'Authorization': 'Bearer ' + confidential_access}
        ).status_code == 200

        revoke_post = client.post(
            '/revoke',
            data={
                'token': confidential_access,
                'token_type_hint': 'access_token',
                'client_id': confidential_client_id,
                'client_secret': confidential_secret,
            },
        )
        assert revoke_post.status_code == 200, revoke_post.text
        assert client.get(
            '/health', headers={'Authorization': 'Bearer ' + confidential_access}
        ).status_code == 401

        basic_registration = client.post(
            '/register',
            json={
                'redirect_uris': [redirect_uri],
                'token_endpoint_auth_method': 'client_secret_basic',
                'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'],
                'scope': 'uls:read',
            },
        )
        assert basic_registration.status_code == 201, basic_registration.text
        basic_client_id = basic_registration.json()['client_id']
        basic_secret = basic_registration.json()['client_secret']
        assert basic_secret

        basic_authorize = client.get(
            '/authorize',
            params={
                'client_id': basic_client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'state': 'basic-state',
                'scope': 'uls:read',
                'resource': identity.resource_uri,
            },
        )
        basic_upstream_state = parse_qs(urlsplit(basic_authorize.headers['location']).query)[
            'state'
        ][0]
        basic_callback = client.get(
            '/oauth/google/callback',
            params={'state': basic_upstream_state, 'code': 'google-code-basic'},
        )
        basic_code = parse_qs(urlsplit(basic_callback.headers['location']).query)['code'][0]
        basic_credentials = base64.b64encode(
            f'{basic_client_id}:{basic_secret}'.encode()
        ).decode('ascii')
        basic_tokens = client.post(
            '/token',
            headers={'Authorization': 'Basic ' + basic_credentials},
            data={
                'grant_type': 'authorization_code',
                'code': basic_code,
                'redirect_uri': redirect_uri,
                'client_id': basic_client_id,
                'code_verifier': verifier,
                'resource': identity.resource_uri,
            },
        )
        assert basic_tokens.status_code == 200, basic_tokens.text
        assert client.get(
            '/health', headers={'Authorization': 'Bearer ' + basic_tokens.json()['access_token']}
        ).status_code == 200

        assert client.get('/oauth/google/callback', params={'state': 'unknown', 'code': 'x'}).status_code == 400


def test_mcp_oauth_cloudflare_edge_accepts_only_loopback_http(tmp_path):
    pytest.importorskip('starlette')
    from starlette.testclient import TestClient
    from test_get_activity_context import _engine

    from uls.mcp.transports.oauth import (
        GoogleOAuthBrokerProvider,
        OAuthStore,
        canonical_public_identity,
    )
    from uls.mcp.transports.remote import create_mcp_oauth_app

    engine, _, _ = _engine()
    config = UlsConfig()
    config.system = replace(config.system, workspace_dir=str(tmp_path))
    config.remote_mcp = replace(
        config.remote_mcp,
        enabled=True,
        auth_mode='mcp_oauth',
        edge_mode='cloudflare_tunnel',
        host='127.0.0.1',
        public_url='https://uls.example/mcp',
        oauth=replace(
            config.remote_mcp.oauth,
            google_client_id='g.apps.googleusercontent.com',
            authorized_email='owner@example.com',
        ),
    )
    identity = canonical_public_identity(config.remote_mcp.public_url)
    def make_app(name: str):
        provider = GoogleOAuthBrokerProvider(
            store=OAuthStore(tmp_path / f'remote-oauth-{name}.sqlite3'),
            identity=identity,
            google_client_id=config.remote_mcp.oauth.google_client_id,
            google_client_secret='test-secret',
            authorized_email=config.remote_mcp.oauth.authorized_email,
        )
        return create_mcp_oauth_app(ReadOnlyMCP(engine), config, 'unused', provider=provider)

    with TestClient(
        make_app('loopback'),
        base_url='http://uls.example',
        client=('127.0.0.1', 5555),
        follow_redirects=False,
    ) as client:
        assert client.get('/.well-known/oauth-authorization-server').status_code == 200
        assert client.get(
            '/.well-known/oauth-authorization-server', headers={'Host': 'evil.example'}
        ).status_code == 401

    with TestClient(
        make_app('non-loopback'),
        base_url='http://uls.example',
        client=('10.0.0.2', 5555),
        follow_redirects=False,
    ) as non_loopback:
        assert non_loopback.get('/.well-known/oauth-authorization-server').status_code == 401

    with TestClient(
        make_app('wrong-scheme'),
        base_url='https://uls.example',
        client=('127.0.0.1', 5555),
        follow_redirects=False,
    ) as wrong_local_scheme:
        assert wrong_local_scheme.get('/.well-known/oauth-authorization-server').status_code == 401


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


def test_dispatch_mcp_oauth_requires_google_secret_and_never_resolves_legacy_bearer(tmp_path, monkeypatch):
    import yaml

    from uls.behavior import asset_root
    from uls.cli.main import dispatch, initialize, parser
    from uls.config.credentials import CredentialResolver, ResolvedCredentials

    raw = yaml.safe_load((asset_root() / 'config.example.yaml').read_text(encoding='utf-8'))
    raw['system']['workspace_dir'] = str(tmp_path / 'state')
    raw['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
    raw['worker']['enabled'] = False
    raw['remote_mcp'] = {
        'enabled': True,
        'auth_mode': 'mcp_oauth',
        'edge_mode': 'cloudflare_tunnel',
        'host': '127.0.0.1',
        'public_unauthenticated': False,
        'public_url': 'https://uls.example/mcp',
        'oauth': {
            'google_client_id': 'google-client.apps.googleusercontent.com',
            'authorized_email': 'owner@example.com',
        },
    }
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    initialize(cfg_path)

    captured_required: set[str] = set()
    captured_optional: dict[str, str] = {}

    def fake_resolve(self, *, required, optional):
        captured_required.update(required)
        captured_optional.update(optional)
        values = {name: '' for name in required}
        values.update(optional)
        values['GOOGLE_MCP_CREDENTIALS_FILE'] = str(tmp_path / 'google-mcp.json')
        (tmp_path / 'google-mcp.json').write_text('{}', encoding='utf-8')
        values['NOTION_MCP_TOKEN'] = 'mcp-token'
        values['REMOTE_MCP_GOOGLE_CLIENT_SECRET'] = 'google-secret'
        return ResolvedCredentials(values)

    monkeypatch.setattr(CredentialResolver, 'resolve', fake_resolve)

    class _FakeRegistry:
        def __init__(self, engine):
            self.engine = engine

    monkeypatch.setattr('uls.mcp.server.ReadOnlyMCP', _FakeRegistry)
    monkeypatch.setattr('uls.cli.main.build_retrieval', lambda cfg, creds: object())

    captured_run_remote: dict = {}

    def fake_run_remote(
        registry, cfg, credential=None, *, oidc_verifier=None, google_client_secret=''
    ):
        captured_run_remote['credential'] = credential
        captured_run_remote['oidc_verifier'] = oidc_verifier
        captured_run_remote['google_client_secret'] = google_client_secret

    monkeypatch.setattr('uls.mcp.transports.remote.run_remote', fake_run_remote)

    args = parser().parse_args(['--config', str(cfg_path), 'mcp', 'remote'])
    dispatch(args)

    assert 'REMOTE_MCP_GOOGLE_CLIENT_SECRET' in captured_required
    assert 'REMOTE_MCP_SECRET' not in captured_required | set(captured_optional)
    assert 'REMOTE_MCP_EXPIRES_AT' not in captured_required | set(captured_optional)
    assert captured_run_remote['credential'] is None
    assert captured_run_remote['oidc_verifier'] is None
    assert captured_run_remote['google_client_secret'] == 'google-secret'
