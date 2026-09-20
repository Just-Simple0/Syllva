"""Standards-based Remote MCP OAuth broker with Google as upstream identity provider.

Google authenticates the single owner. Syllva remains the OAuth authorization
server for the MCP resource and issues opaque, resource-bound MCP credentials.
Raw authorization codes/access/refresh tokens are never persisted; the local
SQLite store keeps only SHA-256 digests. DCR client secrets are the one
recoverable credential in this store because the MCP SDK must authenticate
confidential clients at the token endpoint.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from uls.config.errors import ConfigurationError
from uls.mcp.transports.oidc import _NO_REDIRECT_OPENER, ALLOWED_ALGORITHMS, JwksKeyManager

READ_SCOPE: Final[str] = "uls:read"
GOOGLE_ISSUER: Final[str] = "https://accounts.google.com"
GOOGLE_AUTHORIZATION_ENDPOINT: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"
POLICY_FORMAT_VERSION: Final[str] = "remote-mcp-oauth.v1"
PENDING_CLIENT_CAP: Final[int] = 32
DURABLE_CLIENT_CAP: Final[int] = 32
PENDING_CLIENT_TTL_SECONDS: Final[int] = 600
GOOGLE_RESPONSE_LIMIT: Final[int] = 64 * 1024


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _random_token() -> str:
    return secrets.token_urlsafe(32)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(48)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class PublicOAuthIdentity:
    public_origin: str
    resource_uri: str
    issuer: str
    callback_uri: str
    host_header: str


def canonical_public_identity(public_url: str) -> PublicOAuthIdentity:
    """Canonicalize origin or origin+/mcp into one immutable public identity."""
    if not isinstance(public_url, str):
        raise ConfigurationError("remote public_url must be an HTTPS origin or /mcp endpoint")
    try:
        parsed = urlsplit(public_url)
        port = parsed.port
    except ValueError:
        raise ConfigurationError("remote public_url must be an HTTPS origin or /mcp endpoint") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/", "/mcp"}
    ):
        raise ConfigurationError("remote public_url must be an HTTPS origin or /mcp endpoint")

    hostname = parsed.hostname.lower()
    host_component = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != 443:
        host_header = f"{host_component}:{port}"
    else:
        host_header = host_component
    origin = f"https://{host_header}"
    return PublicOAuthIdentity(
        public_origin=origin,
        resource_uri=origin + "/mcp",
        issuer=origin,
        callback_uri=origin + "/oauth/google/callback",
        host_header=host_header,
    )


def authorization_policy_fingerprint(
    identity: PublicOAuthIdentity,
    *,
    google_client_id: str,
    authorized_email: str,
) -> str:
    payload = {
        "authorized_email": authorized_email.strip().lower(),
        "google_client_id": google_client_id,
        "issuer": identity.issuer,
        "policy_format": POLICY_FORMAT_VERSION,
        "resource": identity.resource_uri,
        "scopes": [READ_SCOPE],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BrokerAuthorizationCode(AuthorizationCode):
    digest: str
    policy_fingerprint: str


class BrokerRefreshToken(RefreshToken):
    digest: str
    family_id: str
    generation: int
    policy_fingerprint: str


class BrokerAccessToken(AccessToken):
    digest: str
    family_id: str
    policy_fingerprint: str


@dataclass(frozen=True)
class _LoginTransaction:
    upstream_state: str
    client_id: str
    client_state: str | None
    scopes: tuple[str, ...]
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    resource: str
    nonce: str
    upstream_code_verifier: str
    expires_at: float


def validate_oauth_state_boundary(path: Path, *, require_database: bool = False) -> None:
    path = path.expanduser()
    parent = path.parent
    if not parent.exists() or not parent.is_dir() or parent.is_symlink():
        raise ConfigurationError("remote OAuth state directory must be a regular directory")
    if os.name == "posix":
        try:
            parent_stat = parent.stat()
        except OSError:
            raise ConfigurationError("remote OAuth state directory is unavailable") from None
        if parent_stat.st_uid != os.getuid():
            raise ConfigurationError("remote OAuth state directory owner mismatch")
        if parent_stat.st_mode & 0o022:
            raise ConfigurationError("remote OAuth state directory must not be group/other writable")
        if parent_stat.st_mode & 0o300 != 0o300 or not os.access(parent, os.W_OK | os.X_OK):
            raise ConfigurationError("remote OAuth state directory must be owner-writable")
    if require_database and not path.exists():
        raise ConfigurationError("remote OAuth database is missing")
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise ConfigurationError("remote OAuth database must be a regular file")
    if path.exists() and os.name == "posix":
        try:
            db_stat = path.stat()
        except OSError:
            raise ConfigurationError("remote OAuth database is unavailable") from None
        if db_stat.st_uid != os.getuid():
            raise ConfigurationError("remote OAuth database owner mismatch")
        if db_stat.st_mode & 0o777 != 0o600:
            raise ConfigurationError("remote OAuth database must have mode 0600")


class OAuthStore:
    """Small transactional OAuth state store under the user's Syllva workspace."""

    def __init__(self, path: Path, *, pending_ttl_seconds: int = PENDING_CLIENT_TTL_SECONDS) -> None:
        self.path = path.expanduser()
        self.pending_ttl_seconds = pending_ttl_seconds
        self._prepare_path()
        self._initialize()

    def _prepare_path(self) -> None:
        parent = self.path.parent
        if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
            raise ConfigurationError("remote OAuth state directory must be a regular directory")
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        validate_oauth_state_boundary(self.path)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            except OSError:
                raise ConfigurationError("remote OAuth database could not be created securely") from None
            else:
                os.close(fd)
        validate_oauth_state_boundary(self.path, require_database=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    client_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING_OWNER','OWNER_AUTHORIZED')),
                    created_at REAL NOT NULL,
                    owner_authorized_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_codes (
                    digest TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_explicit INTEGER NOT NULL,
                    resource TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                    digest TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    resource TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    consumed_at REAL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                    digest TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    resource TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    revoked_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_oauth_refresh_family
                    ON oauth_refresh_tokens(family_id);
                CREATE INDEX IF NOT EXISTS idx_oauth_access_family
                    ON oauth_access_tokens(family_id);
                """
            )
        validate_oauth_state_boundary(self.path, require_database=True)

    def activate_policy(self, policy_fingerprint: str, *, now: float | None = None) -> None:
        """Permanently retire grants issued under any other authorization policy."""
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE oauth_codes SET consumed_at=COALESCE(consumed_at,?) "
                "WHERE policy_fingerprint<>?",
                (now, policy_fingerprint),
            )
            conn.execute(
                "UPDATE oauth_refresh_tokens SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE policy_fingerprint<>?",
                (now, policy_fingerprint),
            )
            conn.execute(
                "UPDATE oauth_access_tokens SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE policy_fingerprint<>?",
                (now, policy_fingerprint),
            )

    @staticmethod
    def _client_from_row(row: sqlite3.Row) -> OAuthClientInformationFull:
        return OAuthClientInformationFull.model_validate_json(str(row["client_json"]))

    def _prune_pending(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute(
            "DELETE FROM oauth_clients WHERE state='PENDING_OWNER' AND created_at < ?",
            (now - self.pending_ttl_seconds,),
        )

    def get_client(self, client_id: str, *, now: float | None = None) -> OAuthClientInformationFull | None:
        now = time.time() if now is None else now
        with self._connect() as conn:
            self._prune_pending(conn, now)
            row = conn.execute(
                "SELECT client_json FROM oauth_clients WHERE client_id=?", (client_id,)
            ).fetchone()
        return None if row is None else self._client_from_row(row)

    def register_client(self, client: OAuthClientInformationFull, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._prune_pending(conn, now)
            pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM oauth_clients WHERE state='PENDING_OWNER'"
                ).fetchone()[0]
            )
            if pending >= PENDING_CLIENT_CAP:
                oldest = conn.execute(
                    "SELECT client_id FROM oauth_clients WHERE state='PENDING_OWNER' "
                    "ORDER BY created_at ASC, client_id ASC LIMIT 1"
                ).fetchone()
                if oldest is not None:
                    conn.execute("DELETE FROM oauth_clients WHERE client_id=?", (str(oldest[0]),))
            conn.execute(
                "INSERT INTO oauth_clients(client_id,client_json,state,created_at) VALUES(?,?,?,?)",
                (client.client_id, client.model_dump_json(), "PENDING_OWNER", now),
            )

    def promote_client(self, client_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state,created_at FROM oauth_clients WHERE client_id=?", (client_id,)
            ).fetchone()
            if row is None:
                return False
            if row["state"] == "PENDING_OWNER" and float(row["created_at"]) < now - self.pending_ttl_seconds:
                conn.execute("DELETE FROM oauth_clients WHERE client_id=?", (client_id,))
                return False
            if row["state"] == "OWNER_AUTHORIZED":
                return True
            durable = int(
                conn.execute(
                    "SELECT COUNT(*) FROM oauth_clients WHERE state='OWNER_AUTHORIZED'"
                ).fetchone()[0]
            )
            if durable >= DURABLE_CLIENT_CAP:
                return False
            conn.execute(
                "UPDATE oauth_clients SET state='OWNER_AUTHORIZED', owner_authorized_at=? WHERE client_id=?",
                (now, client_id),
            )
            return True

    def issue_authorization_code(
        self,
        *,
        client_id: str,
        scopes: tuple[str, ...],
        expires_at: float,
        code_challenge: str,
        redirect_uri: str,
        redirect_uri_explicit: bool,
        resource: str,
        subject: str,
        policy_fingerprint: str,
    ) -> str:
        raw = _random_token()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO oauth_codes VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    _digest(raw), client_id, json.dumps(list(scopes)), expires_at,
                    code_challenge, redirect_uri, int(redirect_uri_explicit), resource,
                    subject, policy_fingerprint,
                ),
            )
        return raw

    def load_authorization_code(
        self, raw: str, *, client_id: str, policy_fingerprint: str, now: float | None = None
    ) -> BrokerAuthorizationCode | None:
        now = time.time() if now is None else now
        digest = _digest(raw)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM oauth_codes WHERE digest=?", (digest,)).fetchone()
        if (
            row is None
            or row["client_id"] != client_id
            or row["consumed_at"] is not None
            or float(row["expires_at"]) < now
            or row["policy_fingerprint"] != policy_fingerprint
        ):
            return None
        return BrokerAuthorizationCode(
            code=raw,
            scopes=list(json.loads(row["scopes_json"])),
            expires_at=float(row["expires_at"]),
            client_id=str(row["client_id"]),
            code_challenge=str(row["code_challenge"]),
            redirect_uri=AnyUrl(str(row["redirect_uri"])),
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]),
            resource=str(row["resource"]),
            subject=str(row["subject"]),
            digest=digest,
            policy_fingerprint=str(row["policy_fingerprint"]),
        )

    def _insert_access(
        self,
        conn: sqlite3.Connection,
        *,
        raw: str,
        client_id: str,
        scopes: list[str],
        expires_at: float,
        resource: str,
        subject: str,
        policy_fingerprint: str,
        family_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO oauth_access_tokens VALUES(?,?,?,?,?,?,?,?,NULL)",
            (
                _digest(raw), client_id, json.dumps(scopes), expires_at, resource,
                subject, policy_fingerprint, family_id,
            ),
        )

    def _insert_refresh(
        self,
        conn: sqlite3.Connection,
        *,
        raw: str,
        client_id: str,
        scopes: list[str],
        expires_at: float,
        resource: str,
        subject: str,
        policy_fingerprint: str,
        family_id: str,
        generation: int,
    ) -> None:
        conn.execute(
            "INSERT INTO oauth_refresh_tokens VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)",
            (
                _digest(raw), client_id, json.dumps(scopes), expires_at, resource,
                subject, policy_fingerprint, family_id, generation,
            ),
        )

    @staticmethod
    def _revoke_family(conn: sqlite3.Connection, family_id: str, now: float) -> None:
        conn.execute(
            "UPDATE oauth_refresh_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE family_id=?",
            (now, family_id),
        )
        conn.execute(
            "UPDATE oauth_access_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE family_id=?",
            (now, family_id),
        )

    def exchange_authorization_code(
        self,
        code: BrokerAuthorizationCode,
        *,
        access_ttl: int,
        refresh_ttl: int,
        policy_fingerprint: str,
        now: float | None = None,
    ) -> OAuthToken:
        now = time.time() if now is None else now
        access_raw, refresh_raw, family_id = _random_token(), _random_token(), _random_token()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM oauth_codes WHERE digest=?", (code.digest,)).fetchone()
            if (
                row is None
                or row["consumed_at"] is not None
                or float(row["expires_at"]) < now
                or row["client_id"] != code.client_id
                or row["policy_fingerprint"] != policy_fingerprint
            ):
                raise TokenError(error="invalid_grant", error_description="authorization code is invalid")
            conn.execute("UPDATE oauth_codes SET consumed_at=? WHERE digest=?", (now, code.digest))
            scopes = list(json.loads(row["scopes_json"]))
            self._insert_access(
                conn, raw=access_raw, client_id=str(row["client_id"]), scopes=scopes,
                expires_at=now + access_ttl, resource=str(row["resource"]),
                subject=str(row["subject"]), policy_fingerprint=policy_fingerprint,
                family_id=family_id,
            )
            self._insert_refresh(
                conn, raw=refresh_raw, client_id=str(row["client_id"]), scopes=scopes,
                expires_at=now + refresh_ttl, resource=str(row["resource"]),
                subject=str(row["subject"]), policy_fingerprint=policy_fingerprint,
                family_id=family_id, generation=0,
            )
        return OAuthToken(
            access_token=access_raw,
            expires_in=access_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh_raw,
        )

    def load_refresh_token(
        self, raw: str, *, client_id: str, policy_fingerprint: str, now: float | None = None
    ) -> BrokerRefreshToken | None:
        now = time.time() if now is None else now
        digest = _digest(raw)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM oauth_refresh_tokens WHERE digest=?", (digest,)).fetchone()
            if row is None or row["client_id"] != client_id:
                return None
            family_id = str(row["family_id"])
            if row["consumed_at"] is not None and row["revoked_at"] is None:
                self._revoke_family(conn, family_id, now)
                return None
            if (
                row["revoked_at"] is not None
                or float(row["expires_at"]) < now
                or row["policy_fingerprint"] != policy_fingerprint
            ):
                self._revoke_family(conn, family_id, now)
                return None
        return BrokerRefreshToken(
            token=raw,
            client_id=str(row["client_id"]),
            scopes=list(json.loads(row["scopes_json"])),
            expires_at=int(float(row["expires_at"])),
            resource=str(row["resource"]),
            subject=str(row["subject"]),
            digest=digest,
            family_id=family_id,
            generation=int(row["generation"]),
            policy_fingerprint=str(row["policy_fingerprint"]),
        )

    def exchange_refresh_token(
        self,
        refresh: BrokerRefreshToken,
        scopes: list[str],
        *,
        access_ttl: int,
        refresh_ttl: int,
        policy_fingerprint: str,
        now: float | None = None,
    ) -> OAuthToken:
        now = time.time() if now is None else now
        next_access, next_refresh = _random_token(), _random_token()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM oauth_refresh_tokens WHERE digest=?", (refresh.digest,)
            ).fetchone()
            if row is None:
                raise TokenError(error="invalid_grant", error_description="refresh token is invalid")
            family_id = str(row["family_id"])
            if (
                row["consumed_at"] is not None
                or row["revoked_at"] is not None
                or float(row["expires_at"]) < now
                or row["policy_fingerprint"] != policy_fingerprint
                or row["client_id"] != refresh.client_id
            ):
                self._revoke_family(conn, family_id, now)
                raise TokenError(error="invalid_grant", error_description="refresh token is invalid")
            original_scopes = list(json.loads(row["scopes_json"]))
            if any(scope not in original_scopes for scope in scopes):
                raise TokenError(error="invalid_scope", error_description="requested scope is invalid")
            updated = conn.execute(
                "UPDATE oauth_refresh_tokens SET consumed_at=? WHERE digest=? AND consumed_at IS NULL",
                (now, refresh.digest),
            ).rowcount
            if updated != 1:
                self._revoke_family(conn, family_id, now)
                raise TokenError(error="invalid_grant", error_description="refresh token is invalid")
            self._insert_access(
                conn, raw=next_access, client_id=str(row["client_id"]), scopes=scopes,
                expires_at=now + access_ttl, resource=str(row["resource"]),
                subject=str(row["subject"]), policy_fingerprint=policy_fingerprint,
                family_id=family_id,
            )
            self._insert_refresh(
                conn, raw=next_refresh, client_id=str(row["client_id"]), scopes=scopes,
                expires_at=now + refresh_ttl, resource=str(row["resource"]),
                subject=str(row["subject"]), policy_fingerprint=policy_fingerprint,
                family_id=family_id, generation=int(row["generation"]) + 1,
            )
        return OAuthToken(
            access_token=next_access,
            expires_in=access_ttl,
            scope=" ".join(scopes),
            refresh_token=next_refresh,
        )

    def load_access_token(
        self, raw: str, *, policy_fingerprint: str, issuer: str, now: float | None = None
    ) -> BrokerAccessToken | None:
        now = time.time() if now is None else now
        digest = _digest(raw)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM oauth_access_tokens WHERE digest=?", (digest,)).fetchone()
            if row is None or row["revoked_at"] is not None or float(row["expires_at"]) < now:
                return None
            if row["policy_fingerprint"] != policy_fingerprint:
                self._revoke_family(conn, str(row["family_id"]), now)
                return None
        return BrokerAccessToken(
            token=raw,
            client_id=str(row["client_id"]),
            scopes=list(json.loads(row["scopes_json"])),
            expires_at=int(float(row["expires_at"])),
            resource=str(row["resource"]),
            subject=str(row["subject"]),
            claims={"iss": issuer},
            digest=digest,
            family_id=str(row["family_id"]),
            policy_fingerprint=str(row["policy_fingerprint"]),
        )

    def revoke_token(self, token: AccessToken | RefreshToken, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        digest = _digest(token.token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT family_id FROM oauth_access_tokens WHERE digest=?", (digest,)
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT family_id FROM oauth_refresh_tokens WHERE digest=?", (digest,)
                ).fetchone()
            if row is not None:
                self._revoke_family(conn, str(row["family_id"]), now)


class _OwnerDenied(Exception):
    pass


class GoogleOAuthBrokerProvider(OAuthAuthorizationServerProvider[BrokerAuthorizationCode, BrokerRefreshToken, BrokerAccessToken]):
    """MCP authorization-server provider that brokers one Google owner login."""

    def __init__(
        self,
        *,
        store: OAuthStore,
        identity: PublicOAuthIdentity,
        google_client_id: str,
        google_client_secret: str,
        authorized_email: str,
        access_token_ttl_seconds: int = 900,
        refresh_token_ttl_seconds: int = 2_592_000,
        authorization_ttl_seconds: int = 600,
        key_manager: JwksKeyManager | None = None,
        clock: Any = time.time,
    ) -> None:
        self.store = store
        self.identity = identity
        self.google_client_id = google_client_id
        self._google_client_secret = google_client_secret
        self.authorized_email = authorized_email.strip().lower()
        self.access_token_ttl_seconds = access_token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds
        self.authorization_ttl_seconds = authorization_ttl_seconds
        self.key_manager = key_manager or JwksKeyManager(GOOGLE_ISSUER)
        self.clock = clock
        self.policy_fingerprint = authorization_policy_fingerprint(
            identity,
            google_client_id=google_client_id,
            authorized_email=self.authorized_email,
        )
        try:
            self.store.activate_policy(self.policy_fingerprint, now=self.clock())
        except sqlite3.Error:
            raise ConfigurationError("remote OAuth policy activation failed") from None
        self._transactions: dict[str, _LoginTransaction] = {}
        self._transactions_lock: asyncio.Lock | None = None

    def _lock(self) -> asyncio.Lock:
        if self._transactions_lock is None:
            self._transactions_lock = asyncio.Lock()
        return self._transactions_lock

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await asyncio.to_thread(self.store.get_client, client_id, now=self.clock())

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        try:
            await asyncio.to_thread(self.store.register_client, client_info, now=self.clock())
        except (sqlite3.Error, ConfigurationError):
            raise RegistrationError(
                error="invalid_client_metadata", error_description="client registration could not be stored"
            ) from None

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource is not None and params.resource != self.identity.resource_uri:
            raise AuthorizeError(error="invalid_target", error_description="requested resource is not supported")
        scopes = tuple(params.scopes or [READ_SCOPE])
        if set(scopes) != {READ_SCOPE}:
            raise AuthorizeError(error="invalid_scope", error_description="requested scope is not supported")

        state, nonce, verifier = _random_token(), _random_token(), _pkce_verifier()
        txn = _LoginTransaction(
            upstream_state=state,
            client_id=client.client_id,
            client_state=params.state,
            scopes=scopes,
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.identity.resource_uri,
            nonce=nonce,
            upstream_code_verifier=verifier,
            expires_at=self.clock() + self.authorization_ttl_seconds,
        )
        async with self._lock():
            now = self.clock()
            self._transactions = {
                key: value for key, value in self._transactions.items() if value.expires_at >= now
            }
            self._transactions[state] = txn

        query = urllib.parse.urlencode(
            {
                "client_id": self.google_client_id,
                "redirect_uri": self.identity.callback_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return GOOGLE_AUTHORIZATION_ENDPOINT + "?" + query

    async def _consume_transaction(self, state: str) -> _LoginTransaction | None:
        async with self._lock():
            txn = self._transactions.pop(state, None)
        if txn is None or txn.expires_at < self.clock():
            return None
        return txn

    def _exchange_google_code_sync(self, code: str, txn: _LoginTransaction) -> str:
        body = urllib.parse.urlencode(
            {
                "code": code,
                "client_id": self.google_client_id,
                "client_secret": self._google_client_secret,
                "redirect_uri": self.identity.callback_uri,
                "grant_type": "authorization_code",
                "code_verifier": txn.upstream_code_verifier,
            }
        ).encode("ascii")
        request = urllib.request.Request(
            GOOGLE_TOKEN_ENDPOINT,
            data=body,
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=5.0) as response:
                if response.status != 200:
                    raise ConfigurationError("Google token exchange failed")
                raw = response.read(GOOGLE_RESPONSE_LIMIT + 1)
                if len(raw) > GOOGLE_RESPONSE_LIMIT:
                    raise ConfigurationError("Google token response exceeded size limit")
                payload = json.loads(raw.decode("utf-8"))
            id_token = payload.get("id_token") if isinstance(payload, dict) else None
            if not isinstance(id_token, str) or not id_token:
                raise ConfigurationError("Google token response did not contain an ID token")
            return id_token
        except (OSError, ValueError, urllib.error.HTTPError):
            raise ConfigurationError("Google token exchange failed") from None

    async def _verify_google_identity(self, token: str, *, nonce: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except Exception:  # noqa: BLE001 - malformed JWT headers fail closed without payload leakage
            raise ConfigurationError("Google ID token header is invalid") from None
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or not kid:
            raise ConfigurationError("Google ID token header is invalid")
        key = await self.key_manager.get_signing_key(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self.google_client_id,
                issuer=GOOGLE_ISSUER,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub", "nonce"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except Exception:  # noqa: BLE001 - any signature/claim failure must fail closed
            raise ConfigurationError("Google ID token validation failed") from None
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ConfigurationError("Google ID token nonce mismatch")
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str):
            raise _OwnerDenied
        if email.strip().lower() != self.authorized_email:
            raise _OwnerDenied
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ConfigurationError("Google ID token subject is invalid")
        return sub

    @staticmethod
    def _safe_local_callback_error() -> PlainTextResponse:
        return PlainTextResponse(
            "This login session is invalid or expired. Start sign-in again from your MCP client.",
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )

    def _client_error(self, txn: _LoginTransaction, error: str) -> RedirectResponse:
        description = (
            "Authorization was not completed."
            if error == "access_denied"
            else "Authorization service is unavailable."
        )
        target = construct_redirect_uri(
            txn.redirect_uri,
            error=error,
            error_description=description,
            state=txn.client_state,
            iss=self.identity.issuer,
        )
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    async def handle_google_callback(self, request: Request) -> Response:
        state = request.query_params.get("state")
        if not isinstance(state, str) or not state:
            return self._safe_local_callback_error()
        txn = await self._consume_transaction(state)
        if txn is None:
            return self._safe_local_callback_error()

        if request.query_params.get("error"):
            return self._client_error(txn, "access_denied")
        code = request.query_params.get("code")
        if not isinstance(code, str) or not code:
            return self._client_error(txn, "server_error")

        try:
            id_token = await asyncio.to_thread(self._exchange_google_code_sync, code, txn)
            subject = await self._verify_google_identity(id_token, nonce=txn.nonce)
        except _OwnerDenied:
            return self._client_error(txn, "access_denied")
        except (ConfigurationError, OSError, ValueError):
            return self._client_error(txn, "server_error")

        try:
            promoted = await asyncio.to_thread(self.store.promote_client, txn.client_id, now=self.clock())
        except sqlite3.Error:
            return self._client_error(txn, "server_error")
        if not promoted:
            return self._client_error(txn, "server_error")
        try:
            raw_code = await asyncio.to_thread(
                self.store.issue_authorization_code,
                client_id=txn.client_id,
                scopes=txn.scopes,
                expires_at=self.clock() + self.authorization_ttl_seconds,
                code_challenge=txn.code_challenge,
                redirect_uri=txn.redirect_uri,
                redirect_uri_explicit=txn.redirect_uri_provided_explicitly,
                resource=txn.resource,
                subject=subject,
                policy_fingerprint=self.policy_fingerprint,
            )
        except sqlite3.Error:
            return self._client_error(txn, "server_error")
        target = construct_redirect_uri(
            txn.redirect_uri,
            code=raw_code,
            state=txn.client_state,
            iss=self.identity.issuer,
        )
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> BrokerAuthorizationCode | None:
        return await asyncio.to_thread(
            self.store.load_authorization_code,
            authorization_code,
            client_id=client.client_id,
            policy_fingerprint=self.policy_fingerprint,
            now=self.clock(),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: BrokerAuthorizationCode
    ) -> OAuthToken:
        if authorization_code.client_id != client.client_id or authorization_code.resource != self.identity.resource_uri:
            raise TokenError(error="invalid_grant", error_description="authorization code is invalid")
        return await asyncio.to_thread(
            self.store.exchange_authorization_code,
            authorization_code,
            access_ttl=self.access_token_ttl_seconds,
            refresh_ttl=self.refresh_token_ttl_seconds,
            policy_fingerprint=self.policy_fingerprint,
            now=self.clock(),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> BrokerRefreshToken | None:
        return await asyncio.to_thread(
            self.store.load_refresh_token,
            refresh_token,
            client_id=client.client_id,
            policy_fingerprint=self.policy_fingerprint,
            now=self.clock(),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: BrokerRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.client_id != client.client_id or refresh_token.resource != self.identity.resource_uri:
            raise TokenError(error="invalid_grant", error_description="refresh token is invalid")
        return await asyncio.to_thread(
            self.store.exchange_refresh_token,
            refresh_token,
            scopes,
            access_ttl=self.access_token_ttl_seconds,
            refresh_ttl=self.refresh_token_ttl_seconds,
            policy_fingerprint=self.policy_fingerprint,
            now=self.clock(),
        )

    async def load_access_token(self, token: str) -> BrokerAccessToken | None:
        return await asyncio.to_thread(
            self.store.load_access_token,
            token,
            policy_fingerprint=self.policy_fingerprint,
            issuer=self.identity.issuer,
            now=self.clock(),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await asyncio.to_thread(self.store.revoke_token, token, now=self.clock())

    async def exchange_identity_assertion(self, client: OAuthClientInformationFull, params: Any) -> OAuthToken:
        raise TokenError(
            error="unsupported_grant_type",
            error_description="identity assertion grant is not supported",
        )
