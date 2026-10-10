"""Authenticated Remote MCP transports.

Legacy direct-TLS profiles remain unchanged (docs/plans/remote-mcp-oauth-oidc.md rev3):
``oidc`` (standard OIDC ID Token / RFC 9068 JWT verified against IdP JWKS,
recommended), ``bearer`` (development-only short-lived static secret), and
``oauth_or_bearer`` (hybrid; each configured lane must independently be valid).
The additive ``mcp_oauth`` profile makes Syllva the MCP authorization server,
uses Google only as the upstream owner login, and supports either direct TLS
or a loopback-only Cloudflare Tunnel edge.
A credential grants the single user's corpus subject to retrieval policy; it is
not a per-course authorization scheme.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from uls.config.errors import ConfigurationError
from uls.mcp.server import caller_identity
from uls.mcp.transports.oidc import OidcTokenVerifier, classify_token_lane


@dataclass(frozen=True)
class BearerCredential:
    token: str = field(repr=False)
    expires_at: float

    def validate(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not isinstance(self.token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', self.token):
            raise ConfigurationError('remote bearer must be 32–256 URL-safe random characters')
        if (not math.isfinite(self.expires_at) or not 0 < self.expires_at - now <= 3600):
            raise ConfigurationError('remote bearer must expire within one hour')

    @property
    def identity(self) -> str:
        return 'remote:' + hashlib.sha256(self.token.encode()).hexdigest()


def validate_remote_profile(config: Any) -> tuple[str, str]:
    cfg = config.remote_mcp
    if cfg.enabled is not True or cfg.public_unauthenticated is not False or config.mcp.read_only is not True:
        raise ConfigurationError('remote MCP requires an enabled authenticated read-only profile')
    url = urlsplit(cfg.public_url)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in {'', '/mcp'}):
        raise ConfigurationError('remote public_url must be an HTTPS origin or /mcp endpoint')
    if type(cfg.port) is not int or not 1 <= cfg.port <= 65535:
        raise ConfigurationError('remote port must be 1–65535')
    if not isinstance(cfg.host, str) or not cfg.host:
        raise ConfigurationError('remote host is required')
    edge_mode = getattr(cfg, 'edge_mode', 'direct_tls')
    if edge_mode not in {'direct_tls', 'cloudflare_tunnel'}:
        raise ConfigurationError('remote edge_mode is not supported')
    if edge_mode == 'cloudflare_tunnel':
        if cfg.auth_mode != 'mcp_oauth':
            raise ConfigurationError('cloudflare_tunnel is supported only by mcp_oauth')
        if cfg.host not in {'127.0.0.1', '::1'}:
            raise ConfigurationError('cloudflare_tunnel requires an explicit loopback bind address')
    return url.netloc, 'https://' + url.netloc


class AuthenticatedApp:
    def __init__(self, app: Any, host: str, origin: str,
                 *, credential: BearerCredential | None = None,
                 oidc_verifier: OidcTokenVerifier | None = None,
                 clock: Any = time.time) -> None:
        if credential is None and oidc_verifier is None:
            raise ConfigurationError('remote MCP requires either BearerCredential or OidcTokenVerifier')
        if credential is not None:
            credential.validate(clock())
        self.app = app
        self._credential = credential
        self._oidc_verifier = oidc_verifier
        self.host, self.origin, self.clock = host, origin, clock

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope['type'] == 'lifespan':
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        raw = scope.get('headers', [])
        def values(name: bytes) -> list[str]:
            return [value.decode('latin-1') for key, value in raw if key.lower() == name]
        auth = values(b'authorization')
        transport_valid = (
            scope.get('scheme') == 'https'
            and values(b'host') == [self.host]
            and (not values(b'origin') or values(b'origin') == [self.origin])
            and len(auth) == 1
            and auth[0].startswith('Bearer ')
        )
        if not transport_valid:
            response = JSONResponse({'error': 'unauthorized'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})
            await response(scope, receive, send)
            return

        raw_token = auth[0][7:]
        lane = classify_token_lane(raw_token)
        identity: str | None = None

        if lane == 'oidc' and self._oidc_verifier is not None:
            try:
                identity = await self._oidc_verifier.verify_token(raw_token)
            except ConfigurationError:
                identity = None
        elif (lane == 'bearer' and self._credential is not None
                and self.clock() < self._credential.expires_at
                and hmac.compare_digest(auth[0].encode(), ('Bearer ' + self._credential.token).encode())):
            identity = self._credential.identity

        if identity is None:
            response = JSONResponse({'error': 'unauthorized'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})
            await response(scope, receive, send)
            return
        token = caller_identity.set(identity)
        try:
            if scope.get('path') == '/health':
                await JSONResponse({'ok': True, 'service': 'uls', 'read_only': True})(scope, receive, send)
            else:
                await self.app(scope, receive, send)
        finally:
            caller_identity.reset(token)


def create_remote_app(registry: Any, config: Any, credential: BearerCredential | None = None,
                      *, oidc_verifier: OidcTokenVerifier | None = None,
                      clock: Any = time.time) -> AuthenticatedApp:
    from mcp.server.transport_security import TransportSecuritySettings
    host, origin = validate_remote_profile(config)
    app = registry.sdk_server().streamable_http_app(
        json_response=True, stateless_http=True, max_request_body_size=64_000,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[host], allowed_origins=[origin]),
    )
    return AuthenticatedApp(app, host, origin, credential=credential, oidc_verifier=oidc_verifier, clock=clock)


class OAuthEdgeBoundaryApp:
    """Transport boundary plus retrieval caller binding for ``mcp_oauth``.

    OAuth discovery/authorization routes intentionally do not require an MCP
    access token, but every route still passes the exact edge/Host/Origin
    checks. `/mcp` bearer enforcement remains owned by the MCP SDK so clients
    receive its RFC 9728 challenge. A valid token is looked up once here only
    to bind Syllva's existing caller_identity capability namespace.
    """

    def __init__(
        self,
        app: Any,
        *,
        provider: Any,
        identity: Any,
        edge_mode: str,
        authorization_metadata: dict[str, Any],
    ) -> None:
        self.app = app
        self.provider = provider
        self.identity = identity
        self.edge_mode = edge_mode
        self.authorization_metadata = authorization_metadata

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope['type'] == 'lifespan':
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1008})
            return

        raw_headers = scope.get('headers', [])

        def values(name: bytes) -> list[str]:
            return [value.decode('latin-1') for key, value in raw_headers if key.lower() == name]

        host_ok = values(b'host') == [self.identity.host_header]
        origins = values(b'origin')
        origin_ok = not origins or origins == [self.identity.public_origin]
        if self.edge_mode == 'direct_tls':
            edge_ok = scope.get('scheme') == 'https'
        else:
            peer = scope.get('client')
            peer_host = peer[0] if isinstance(peer, (tuple, list)) and peer else None
            edge_ok = scope.get('scheme') == 'http' and peer_host in {'127.0.0.1', '::1'}
        if not (host_ok and origin_ok and edge_ok):
            unauthorized_response = JSONResponse(
                {'error': 'unauthorized'}, status_code=401,
                headers={'Cache-Control': 'no-store'},
            )
            await unauthorized_response(scope, receive, send)
            return

        path = scope.get('path')
        if path == '/token' and scope.get('method') == 'POST':
            content_types = values(b'content-type')
            if content_types and content_types[0].split(';', 1)[0].strip().lower() == 'application/x-www-form-urlencoded':
                body = bytearray()
                more_body = True
                while more_body:
                    message = await receive()
                    if message.get('type') != 'http.request':
                        await JSONResponse(
                            {'error': 'invalid_request'}, status_code=400,
                            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
                        )(scope, receive, send)
                        return
                    body.extend(message.get('body', b''))
                    if len(body) > 64_000:
                        await JSONResponse(
                            {'error': 'invalid_request'}, status_code=400,
                            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
                        )(scope, receive, send)
                        return
                    more_body = bool(message.get('more_body', False))

                try:
                    form = parse_qs(body.decode('utf-8'), keep_blank_values=True)
                except UnicodeDecodeError:
                    form = {}
                resources = form.get('resource', [])
                if len(resources) > 1:
                    await JSONResponse(
                        {'error': 'invalid_request'}, status_code=400,
                        headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
                    )(scope, receive, send)
                    return
                if resources and resources[0] != self.identity.resource_uri:
                    await JSONResponse(
                        {'error': 'invalid_target'}, status_code=400,
                        headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
                    )(scope, receive, send)
                    return

                replayed = False

                async def replay_receive() -> dict[str, Any]:
                    nonlocal replayed
                    if replayed:
                        return {'type': 'http.request', 'body': b'', 'more_body': False}
                    replayed = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

                receive = replay_receive
        if path == '/.well-known/oauth-authorization-server':
            from starlette.responses import JSONResponse, Response

            if scope.get('method') == 'OPTIONS':
                metadata_response: Response = Response(
                    status_code=204,
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET, OPTIONS',
                        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                        'Cache-Control': 'no-store',
                    },
                )
            else:
                metadata_response = JSONResponse(
                    self.authorization_metadata,
                    headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store'},
                )
            await metadata_response(scope, receive, send)
            return
        auth = values(b'authorization')
        verified = None
        if len(auth) == 1 and auth[0].startswith('Bearer '):
            verified = await self.provider.load_access_token(auth[0][7:])

        if path == '/health':
            if verified is None or 'uls:read' not in verified.scopes:
                response = JSONResponse(
                    {'error': 'unauthorized'}, status_code=401,
                    headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'},
                )
                await response(scope, receive, send)
                return
            await JSONResponse({'ok': True, 'service': 'uls', 'read_only': True})(scope, receive, send)
            return

        identity_token = None
        if path == '/mcp' and verified is not None and verified.subject:
            subject_key = self.identity.issuer + ':' + verified.subject
            identity = 'remote:oauth:' + hashlib.sha256(subject_key.encode()).hexdigest()
            identity_token = caller_identity.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            if identity_token is not None:
                caller_identity.reset(identity_token)


def create_mcp_oauth_app(
    registry: Any,
    config: Any,
    google_client_secret: str,
    *,
    provider: Any | None = None,
) -> OAuthEdgeBoundaryApp:
    from mcp.server.auth.provider import ProviderTokenVerifier
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from mcp.server.transport_security import TransportSecuritySettings
    from pydantic import AnyHttpUrl
    from starlette.routing import Route

    from uls.mcp.transports.oauth import (
        READ_SCOPE,
        GoogleOAuthBrokerProvider,
        OAuthStore,
        canonical_public_identity,
    )

    validate_remote_profile(config)
    cfg = config.remote_mcp
    if cfg.auth_mode != 'mcp_oauth':
        raise ConfigurationError('mcp_oauth app requires auth_mode=mcp_oauth')
    identity = canonical_public_identity(cfg.public_url)
    if provider is None:
        if not google_client_secret:
            raise ConfigurationError('mcp_oauth requires the Google client secret')
        oauth_cfg = cfg.oauth
        store = OAuthStore(Path(config.system.workspace_dir).expanduser() / 'remote-oauth.sqlite3')
        provider = GoogleOAuthBrokerProvider(
            store=store,
            identity=identity,
            google_client_id=oauth_cfg.google_client_id,
            google_client_secret=google_client_secret,
            authorized_email=oauth_cfg.authorized_email,
            access_token_ttl_seconds=oauth_cfg.access_token_ttl_seconds,
            refresh_token_ttl_seconds=oauth_cfg.refresh_token_ttl_seconds,
            authorization_ttl_seconds=oauth_cfg.authorization_ttl_seconds,
        )
    token_verifier = ProviderTokenVerifier(provider)  # type: ignore[arg-type]
    auth = AuthSettings(
        issuer_url=cast(AnyHttpUrl, identity.issuer),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[READ_SCOPE],
            default_scopes=[READ_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[READ_SCOPE],
        resource_server_url=cast(AnyHttpUrl, identity.resource_uri),
        validate_token_resource=True,
    )
    authorization_metadata = {
        'issuer': identity.issuer,
        'authorization_endpoint': identity.issuer + '/authorize',
        'token_endpoint': identity.issuer + '/token',
        'registration_endpoint': identity.issuer + '/register',
        'revocation_endpoint': identity.issuer + '/revoke',
        'scopes_supported': [READ_SCOPE],
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'token_endpoint_auth_methods_supported': [
            'none', 'client_secret_post', 'client_secret_basic'
        ],
        'revocation_endpoint_auth_methods_supported': ['client_secret_post'],
        'code_challenge_methods_supported': ['S256'],
        'authorization_response_iss_parameter_supported': True,
    }
    app = registry.sdk_server().streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=64_000,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[identity.host_header],
            allowed_origins=[identity.public_origin],
        ),
        auth=auth,
        token_verifier=token_verifier,
        auth_server_provider=provider,
        custom_starlette_routes=[
            Route('/oauth/google/callback', endpoint=provider.handle_google_callback, methods=['GET']),
        ],
    )
    return OAuthEdgeBoundaryApp(
        app,
        provider=provider,
        identity=identity,
        edge_mode=cfg.edge_mode,
        authorization_metadata=authorization_metadata,
    )


def run_remote(registry: Any, config: Any, credential: BearerCredential | None = None,
               *, oidc_verifier: OidcTokenVerifier | None = None,
               google_client_secret: str = '') -> None:
    import uvicorn
    cfg = config.remote_mcp
    validate_remote_profile(config)
    if cfg.auth_mode == 'mcp_oauth':
        oauth_app = create_mcp_oauth_app(registry, config, google_client_secret)
        if cfg.edge_mode == 'cloudflare_tunnel':
            uvicorn.run(oauth_app, host=cfg.host, port=cfg.port, workers=1, proxy_headers=False,
                        access_log=False, log_level='warning')
            return
        for path in (cfg.tls_certfile, cfg.tls_keyfile):
            if not path or not Path(path).expanduser().is_file():
                raise ConfigurationError('direct remote TLS requires certificate and private-key files')
        uvicorn.run(oauth_app, host=cfg.host, port=cfg.port, workers=1, proxy_headers=False,
                    ssl_certfile=str(Path(cfg.tls_certfile).expanduser()),
                    ssl_keyfile=str(Path(cfg.tls_keyfile).expanduser()),
                    access_log=False, log_level='warning')
        return
    for path in (cfg.tls_certfile, cfg.tls_keyfile):
        if not path or not Path(path).expanduser().is_file():
            raise ConfigurationError('direct remote TLS requires certificate and private-key files')
    legacy_app = create_remote_app(registry, config, credential, oidc_verifier=oidc_verifier)
    uvicorn.run(legacy_app, host=cfg.host, port=cfg.port, workers=1, proxy_headers=False,
                ssl_certfile=str(Path(cfg.tls_certfile).expanduser()),
                ssl_keyfile=str(Path(cfg.tls_keyfile).expanduser()),
                access_log=False, log_level='warning')
