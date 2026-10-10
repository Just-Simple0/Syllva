"""OIDC JWT Bearer verification and JWKS management for Remote MCP.

Implements the OIDC Resource Server role (docs/plans/remote-mcp-oauth-oidc.md rev3):
- Strictly validates asymmetric algorithms (RS256, ES256) and rejects none/HS256.
- Asynchronous non-blocking JWKS fetch using asyncio.to_thread and lazy Single-Flight lock.
- Single-owner authorization gate: strictly verifies sub and/or email (with email_verified=True).
- Generates a domain-separated 256-bit collision-resistant caller identity:
  remote:oidc:<sha256(iss + ":" + sub)>.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any, Final
from urllib.parse import urlsplit

import jwt

from uls.config.errors import ConfigurationError

ALLOWED_ALGORITHMS: Final[frozenset[str]] = frozenset({"RS256", "ES256"})
DEFAULT_CACHE_TTL_SECONDS: Final[float] = 3600.0
MIN_CACHE_TTL_SECONDS: Final[float] = 300.0
MAX_CACHE_TTL_SECONDS: Final[float] = 86400.0
REFRESH_COOLDOWN_SECONDS: Final[float] = 60.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disables automatic redirect following for JWKS/discovery trust bootstrap.

    Returning None from redirect_request tells urllib to not follow the
    redirect; the caller then observes the raw 3xx response (and rejects it
    via the non-200 status check) instead of silently trusting whatever
    Location header the server supplied. This closes the trust-bootstrap
    gap where a compromised/misconfigured HTTPS endpoint could redirect to
    an attacker-controlled HTTP or off-issuer host.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


_NO_REDIRECT_OPENER: Final[Any] = urllib.request.build_opener(_NoRedirectHandler)


def classify_token_lane(token: str) -> str:
    """Pre-dispatch token classification (rev3 plan section 3.2).

    JWT tokens (3 dot-separated base64url segments) are bound strictly to
    the OIDC pipeline; legacy bearer tokens (32-256 URL-safe characters
    without dots) are bound to the bearer pipeline.
    """
    if token.count(".") == 2:
        return "oidc"
    if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        return "bearer"
    return "invalid"


class JwksKeyManager:
    """Manages JWKS public key discovery, caching, and thread-safe fetching."""

    def __init__(self, issuer: str, jwks_uri: str = "") -> None:
        if not issuer.startswith("https://"):
            raise ConfigurationError("OIDC issuer must be an HTTPS URL")
        if issuer.endswith("/"):
            # Reject a trailing slash outright rather than normalizing it
            # away: normalizing both the configured issuer and the
            # discovery-returned issuer before comparing them would let a
            # provider satisfy an "exact match" check with a
            # slash-inconsistent value, defeating the whole point of the
            # exact-match trust check (rev3 plan section 2.3).
            raise ConfigurationError("OIDC issuer must not have a trailing slash")
        if jwks_uri and not jwks_uri.startswith("https://"):
            raise ConfigurationError("OIDC jwks_uri must be an HTTPS URL")
        self.issuer: str = issuer
        self._configured_jwks_uri: str = jwks_uri
        self._discovered_jwks_uri: str = ""
        self._cached_keys: dict[str, Any] = {}
        self._cache_expires_at: float = 0.0
        self._last_refresh: float = 0.0
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _validate_https_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise ConfigurationError("URL must be HTTPS")

    def _fetch_url_sync(self, url: str) -> tuple[dict[str, Any], float]:
        self._validate_https_url(url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Syllva-Remote-MCP/1.2", "Accept": "application/json"},
        )
        try:
            # Use the redirect-disabling opener so a 3xx response is
            # returned as-is (and rejected below) instead of transparently
            # followed to a URL we never validated.
            response: HTTPResponse
            with _NO_REDIRECT_OPENER.open(req, timeout=5.0) as response:
                if response.status != 200:
                    raise ConfigurationError("JWKS endpoint returned non-200 status")
                # Parse Cache-Control header if present
                cc = response.headers.get("Cache-Control", "")
                ttl = DEFAULT_CACHE_TTL_SECONDS
                match = re.search(r"max-age=(\d+)", cc)
                if match:
                    ttl = max(MIN_CACHE_TTL_SECONDS, min(MAX_CACHE_TTL_SECONDS, float(match.group(1))))
                data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise ConfigurationError("JWKS response must be a JSON object")
                return data, ttl
        except (OSError, ValueError, TimeoutError):
            raise ConfigurationError("failed to fetch OIDC metadata from provider") from None

    def _discover_jwks_uri_sync(self) -> str:
        if self._configured_jwks_uri:
            return self._configured_jwks_uri
        if self._discovered_jwks_uri:
            return self._discovered_jwks_uri
        discovery_url = f"{self.issuer}/.well-known/openid-configuration"
        doc, _ = self._fetch_url_sync(discovery_url)
        discovered_issuer = doc.get("issuer")
        if not isinstance(discovered_issuer, str):
            raise ConfigurationError("discovered OIDC issuer is missing or malformed")
        if discovered_issuer != self.issuer:
            raise ConfigurationError("discovered OIDC issuer does not match configured issuer")
        jwks_uri = doc.get("jwks_uri", "")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
            raise ConfigurationError("discovered jwks_uri must be an HTTPS URL")
        self._discovered_jwks_uri = jwks_uri
        return jwks_uri

    def live_check_sync(self) -> None:
        """Perform an actual network round-trip against the JWKS endpoint.

        Used by 'uls doctor --live' (rev3 plan section 5.2). Discovering
        an already-configured jwks_uri alone requires zero network I/O,
        so this always fetches the JWKS document itself, exercising
        discovery (if configured) and the JWKS endpoint, without
        mutating the instance cache.
        """
        jwks_uri = self._discover_jwks_uri_sync()
        self._fetch_url_sync(jwks_uri)

    def _refresh_keys_sync(self) -> None:
        jwks_uri = self._discover_jwks_uri_sync()
        data, ttl = self._fetch_url_sync(jwks_uri)
        raw_keys = data.get("keys")
        if not isinstance(raw_keys, list):
            raise ConfigurationError("JWKS response missing 'keys' array")
        new_keys: dict[str, Any] = {}
        for key_dict in raw_keys:
            if not isinstance(key_dict, dict):
                continue
            kid = key_dict.get("kid")
            kty = key_dict.get("kty")
            if not isinstance(kid, str) or not kid:
                continue
            if kty not in ("RSA", "EC"):
                continue  # Disallow symmetric keys
            try:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_dict) if kty == "RSA" else jwt.algorithms.ECAlgorithm.from_jwk(key_dict)
                new_keys[kid] = public_key
            except (ValueError, TypeError, KeyError):
                continue
        if not new_keys:
            raise ConfigurationError("no valid public signing keys found in JWKS")
        self._cached_keys = new_keys
        self._cache_expires_at = time.time() + ttl
        self._last_refresh = time.time()

    async def get_signing_key(self, kid: str) -> Any:
        now = time.time()
        if now < self._cache_expires_at and kid in self._cached_keys:
            return self._cached_keys[kid]

        async with self._get_lock():
            # Double-check inside lock
            now = time.time()
            if now < self._cache_expires_at and kid in self._cached_keys:
                return self._cached_keys[kid]

            # Enforce 60s cooldown if we already have non-expired cached keys
            if now - self._last_refresh < REFRESH_COOLDOWN_SECONDS and self._cached_keys:
                if kid in self._cached_keys:
                    return self._cached_keys[kid]
                raise ConfigurationError("unknown signing key id")

            try:
                await asyncio.to_thread(self._refresh_keys_sync)
            except ConfigurationError:
                # If network fails but non-expired cache has the key, use it
                if now < self._cache_expires_at and kid in self._cached_keys:
                    return self._cached_keys[kid]
                raise ConfigurationError("unable to verify token signing key") from None

            if kid not in self._cached_keys:
                raise ConfigurationError("unknown signing key id")
            return self._cached_keys[kid]


@dataclass(frozen=True)
class OidcTokenVerifier:
    """Verifies OIDC ID tokens and enforces single-user corpus authorization."""

    issuer: str
    audience: str
    authorized_subject: str = ""
    authorized_email: str = ""
    leeway_seconds: int = 60
    key_manager: JwksKeyManager = field(default_factory=lambda: JwksKeyManager("https://accounts.google.com"))

    def __post_init__(self) -> None:
        if not (self.authorized_subject or self.authorized_email):
            raise ConfigurationError("OIDC verifier requires authorized_subject or authorized_email")

    async def verify_token(self, token: str) -> str:
        """Verify JWT token and return domain-separated caller identity."""
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:  # noqa: BLE001 - any malformed token header must fail closed
            raise ConfigurationError("malformed token header") from None

        alg = unverified_header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise ConfigurationError("unsupported or insecure token algorithm")

        kid = unverified_header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ConfigurationError("token header missing kid")

        signing_key = await self.key_manager.get_signing_key(kid)

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except Exception:  # noqa: BLE001 - any signature/claim failure must fail closed without trace leakage
            raise ConfigurationError("token validation failed") from None

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ConfigurationError("token missing valid sub claim")

        # Single-user authorization gate
        if self.authorized_subject and sub != self.authorized_subject:
            raise ConfigurationError("token subject not authorized")

        if self.authorized_email:
            email = claims.get("email")
            email_verified = claims.get("email_verified")
            if email_verified is not True:
                raise ConfigurationError("unverified email claim rejected")
            if not isinstance(email, str) or email.lower() != self.authorized_email.lower():
                raise ConfigurationError("token email not authorized")

        # Full 256-bit domain-separated collision-resistant identity
        iss = claims.get("iss", self.issuer)
        principal_seed = f"{iss}:{sub}".encode()
        return f"remote:oidc:{hashlib.sha256(principal_seed).hexdigest()}"
