"""MCP remote transport: OIDC JWT Bearer and/or short-lived bearer, over direct TLS.

Supports three auth_mode profiles (docs/plans/remote-mcp-oauth-oidc.md rev3):
``oidc`` (standard OIDC ID Token / RFC 9068 JWT verified against IdP JWKS,
recommended), ``bearer`` (development-only short-lived static secret), and
``oauth_or_bearer`` (hybrid; each configured lane must independently be valid).
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
from typing import Any
from urllib.parse import urlsplit

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


def run_remote(registry: Any, config: Any, credential: BearerCredential | None = None,
               *, oidc_verifier: OidcTokenVerifier | None = None) -> None:
    import uvicorn
    cfg = config.remote_mcp
    validate_remote_profile(config)
    for path in (cfg.tls_certfile, cfg.tls_keyfile):
        if not path or not Path(path).expanduser().is_file():
            raise ConfigurationError('direct remote TLS requires certificate and private-key files')
    app = create_remote_app(registry, config, credential, oidc_verifier=oidc_verifier)
    uvicorn.run(app, host=cfg.host, port=cfg.port, workers=1, proxy_headers=False,
                ssl_certfile=str(Path(cfg.tls_certfile).expanduser()),
                ssl_keyfile=str(Path(cfg.tls_keyfile).expanduser()),
                access_log=False, log_level='warning')
