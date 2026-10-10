"""Personal Google Desktop OAuth flow for Local Settings (P2 plan §2–§4).

One flow per purpose, at most two per process.  The browser is opened with a
PKCE authorization URL; Google redirects back to this launch's exact loopback
callback, which validates the one-use state, exchanges the code, reads the
account identity and keeps the candidate in memory only.  Nothing is stored
until the same live session commits over its CSRF-protected POST, and that
commit performs exactly one protected save through ``CredentialService``.

No state, verifier, URL, code, token, client secret or permission ID is ever
returned to the browser, logged or written to journal/config metadata.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

from uls.config.google_oauth import (
    AUTH_URI,
    TOKEN_URI,
    AuthorizedUserCredential,
    GoogleOAuthClient,
    GoogleOAuthCredentialError,
    GoogleOAuthPurpose,
    config_file_is_private,
    exact_scopes,
)

from .config_service import ConfigStore, SettingsServiceError
from .credential_roles import ROLES
from .credential_service import CredentialService
from .provider_checks import failure
from .security import OAUTH_CALLBACK_SUFFIX, OAUTH_RESULT_SUFFIX, SessionSecurity

FLOW_TTL_SECONDS = 300.0
PRE_COMMIT_STATUSES = frozenset({"pending", "exchanging", "awaiting_commit"})
TERMINAL_STATUSES = frozenset({"complete", "cancelled", "expired", "denied", "account_mismatch", "failed"})
STATE_LENGTH = 43
MAX_CODE_BYTES = 4096
MAX_ERROR_BYTES = 64
OAUTH_HTTP_TIMEOUT_SECONDS = 10.0
OAUTH_MAX_RESPONSE_BYTES = 1024 * 1024
ABOUT_PATH = "/drive/v3/about?fields=user(permissionId)"


def _token_urlsafe_43() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@dataclass
class _Flow:
    flow_id: str
    purpose: GoogleOAuthPurpose
    generation: str
    replace: bool
    session_epoch: int
    created_at: float
    status: str = "pending"
    error_code: str | None = None
    state: str | None = field(default=None, repr=False)
    verifier: str | None = field(default=None, repr=False)
    candidate: AuthorizedUserCredential | None = field(default=None, repr=False)
    fresh_permission_id: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"flow_id": self.flow_id, "purpose": self.purpose.value, "status": self.status,
                "error_code": self.error_code}

    def discard_secrets(self) -> None:
        self.state = None
        self.verifier = None
        self.candidate = None
        self.fresh_permission_id = None


class GoogleOAuthFlowService:
    """Flow state machine bound to one exact loopback authority, prefix and session epoch."""

    def __init__(
        self,
        credentials: CredentialService,
        config: ConfigStore,
        *,
        authority: str,
        prefix: str,
        security: SessionSecurity,
        opener: Callable[[str], None] | None,
        exchanger: Callable[[GoogleOAuthClient, str, str, str], Mapping[str, Any]],
        account_reader: Callable[[str], str],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.credentials = credentials
        self.config = config
        self.authority = authority
        self.prefix = prefix
        self.security = security
        self.opener = opener
        self.exchanger = exchanger
        self.account_reader = account_reader
        self.clock = clock
        self.redirect_uri = f"http://{authority}/{prefix}/{OAUTH_CALLBACK_SUFFIX}"
        self.result_path = f"/{prefix}/{OAUTH_RESULT_SUFFIX}"
        self._flows: dict[GoogleOAuthPurpose, _Flow] = {}
        self._lock = threading.Lock()
        self._blocked = False

    # -- readiness -------------------------------------------------------------

    def readiness(self, loaded: Any | None = None) -> GoogleOAuthClient:
        loaded = loaded or self.config.load()
        client = loaded.config.google_oauth
        if not isinstance(client, GoogleOAuthClient) or not config_file_is_private(self.config.path):
            raise failure("OAUTH_APP_NOT_READY")
        return client

    @property
    def blocked(self) -> bool:
        return self._blocked

    # -- helpers ---------------------------------------------------------------

    def _require_live(self) -> int:
        if self._blocked:
            raise SettingsServiceError("SESSION_EXPIRED", "This Settings session has ended.", 401)
        epoch = self.security.live_epoch()
        if epoch is None:
            raise SettingsServiceError("SESSION_EXPIRED", "This Settings session has ended.", 401)
        return epoch

    def _expire_locked(self, flow: _Flow) -> None:
        if flow.status in PRE_COMMIT_STATUSES and self.clock() - flow.created_at > FLOW_TTL_SECONDS:
            flow.status, flow.error_code = "expired", "FLOW_EXPIRED"
            flow.discard_secrets()

    def _flow_locked(self, purpose: GoogleOAuthPurpose, flow_id: object) -> _Flow:
        flow = self._flows.get(purpose)
        if flow is None or not isinstance(flow_id, str) or not hmac.compare_digest(flow.flow_id, flow_id):
            raise SettingsServiceError("FLOW_NOT_FOUND", "This Google sign-in is no longer known. Start again.", 404)
        self._expire_locked(flow)
        return flow

    @staticmethod
    def _purpose(value: object) -> GoogleOAuthPurpose:
        try:
            return GoogleOAuthPurpose.parse(value)
        except ValueError:
            raise SettingsServiceError("NOT_FOUND", "Unknown Google connection.", 404) from None

    def _target_state(self, purpose: GoogleOAuthPurpose, loaded: Any) -> bool:
        role = ROLES[purpose.role_slug]
        source, path = self.credentials.source(role, loaded.raw)
        if source == "external_file" or (source == "environment" and path):
            raise failure("CREDENTIAL_SOURCE_EXTERNAL")
        try:
            return self.credentials.stores.read(role) is not None
        except Exception:  # noqa: BLE001 - store read failures never echo details
            raise failure("INVALID_CREDENTIAL") from None

    def authorization_url(self, client: GoogleOAuthClient, purpose: GoogleOAuthPurpose, state: str, verifier: str) -> str:
        query = urlencode({
            "client_id": client.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": purpose.scope,
            "state": state,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        })
        return f"{AUTH_URI}?{query}"

    # -- API -------------------------------------------------------------------

    def begin(self, purpose_value: object, body: Mapping[str, Any]) -> dict[str, Any]:
        purpose = self._purpose(purpose_value)
        if set(body) != {"generation", "replace"} or not isinstance(body["generation"], str) or not isinstance(body["replace"], bool):
            raise SettingsServiceError("INVALID_REQUEST", "Choose a listed Google connection action.")
        epoch = self._require_live()
        loaded = self.config.load()
        if body["generation"] != loaded.generation:
            raise SettingsServiceError("CONFIGURATION_CHANGED", "Settings changed elsewhere. Review and try again.", 409)
        client = self.readiness(loaded)
        if self.opener is None:
            # No browser can be opened in this launch mode; the authorization
            # URL (state, challenge) is never printed or returned instead.
            raise failure("BROWSER_REQUIRED")
        managed = self._target_state(purpose, loaded)
        if body["replace"] and not managed:
            raise failure("NOT_CONFIGURED")
        if not body["replace"] and managed:
            raise failure("REPLACE_REQUIRED")
        with self._lock:
            if self._blocked:
                raise SettingsServiceError("SESSION_EXPIRED", "This Settings session has ended.", 401)
            existing = self._flows.get(purpose)
            if existing is not None:
                self._expire_locked(existing)
                if existing.status not in TERMINAL_STATUSES:
                    raise failure("FLOW_BUSY")
            state, verifier = _token_urlsafe_43(), _token_urlsafe_43()
            flow = _Flow(flow_id=secrets.token_hex(16), purpose=purpose, generation=loaded.generation,
                         replace=body["replace"], session_epoch=epoch, created_at=self.clock(),
                         state=state, verifier=verifier)
            self._flows[purpose] = flow
            url = self.authorization_url(client, purpose, state, verifier)
        try:
            assert self.opener is not None
            self.opener(url)
        except Exception:  # noqa: BLE001 - browser failures never expose the URL
            with self._lock:
                if flow.status == "pending":
                    flow.status, flow.error_code = "failed", "PROVIDER_UNAVAILABLE"
                    flow.discard_secrets()
            raise failure("PROVIDER_UNAVAILABLE") from None
        return flow.public()

    def status(self, purpose_value: object, flow_id: object) -> dict[str, Any]:
        purpose = self._purpose(purpose_value)
        with self._lock:
            return self._flow_locked(purpose, flow_id).public()

    def cancel(self, purpose_value: object, body: Mapping[str, Any]) -> dict[str, Any]:
        purpose = self._purpose(purpose_value)
        if set(body) != {"flow_id", "generation"} or not isinstance(body["generation"], str):
            raise SettingsServiceError("INVALID_REQUEST", "Choose a listed Google connection action.")
        with self._lock:
            flow = self._flow_locked(purpose, body["flow_id"])
            if body["generation"] != flow.generation:
                raise SettingsServiceError("CONFIGURATION_CHANGED", "Settings changed elsewhere. Review and try again.", 409)
            if flow.status == "committing":
                raise failure("COMMIT_IN_PROGRESS")
            if flow.status in PRE_COMMIT_STATUSES:
                flow.status, flow.error_code = "cancelled", "FLOW_CANCELLED"
                flow.discard_secrets()
            return flow.public()

    def callback(self, query: Mapping[str, str]) -> None:
        """Provider redirect: one-use state, PKCE exchange, identity read; stores nothing."""

        keys = set(query)
        # Google appends parameters such as ``scope``, ``authuser`` and
        # ``prompt`` to the authorization response; RFC 6749 §4.1.2 requires
        # ignoring unrecognised response parameters.  The response must carry
        # ``state`` and exactly one of ``code`` / ``error``; the token response
        # (not this query) is what proves the granted scope.
        if "state" not in keys or ("code" in keys) == ("error" in keys):
            return
        state = query["state"]
        if not isinstance(state, str) or len(state) != STATE_LENGTH or not state.isascii():
            return
        with self._lock:
            flow: _Flow | None = None
            for known in self._flows.values():
                if known.state is not None and hmac.compare_digest(known.state, state):
                    flow = known
            if flow is None:
                return
            self._expire_locked(flow)
            if flow.status == "expired":
                # The TTL verdict is terminal; a late callback never rewrites it.
                return
            live = self.security.live_epoch()
            if flow.status != "pending" or self._blocked or live is None or live != flow.session_epoch:
                flow.status, flow.error_code = "failed", "FLOW_STATE_REJECTED"
                flow.discard_secrets()
                return
            verifier = flow.verifier
            flow.state = None  # one-use
            if "error" in query:
                error = query["error"]
                if not isinstance(error, str) or not error.isascii() or len(error) > MAX_ERROR_BYTES:
                    flow.status, flow.error_code = "failed", "FLOW_STATE_REJECTED"
                else:
                    flow.status = "denied" if error == "access_denied" else "failed"
                    flow.error_code = "OAUTH_ACCESS_DENIED" if error == "access_denied" else "FLOW_STATE_REJECTED"
                flow.discard_secrets()
                return
            code = query["code"]
            if not isinstance(code, str) or not 1 <= len(code.encode("utf-8")) <= MAX_CODE_BYTES or not code.isascii():
                flow.status, flow.error_code = "failed", "FLOW_STATE_REJECTED"
                flow.discard_secrets()
                return
            flow.status = "exchanging"
            purpose, epoch = flow.purpose, flow.session_epoch
        assert verifier is not None
        result_status, error_code, candidate, permission_id = self._exchange(purpose, code, verifier)
        with self._lock:
            live = self.security.live_epoch()
            if flow.status != "exchanging" or self._blocked or live is None or live != epoch:
                flow.discard_secrets()
                if flow.status == "exchanging":
                    flow.status, flow.error_code = "failed", "FLOW_STATE_REJECTED"
                return
            flow.verifier = None
            if result_status == "awaiting_commit":
                flow.candidate, flow.fresh_permission_id = candidate, permission_id
                flow.status, flow.error_code = "awaiting_commit", None
            else:
                flow.status, flow.error_code = result_status, error_code
                flow.discard_secrets()

    def _exchange(self, purpose: GoogleOAuthPurpose, code: str, verifier: str) -> tuple[str, str | None, AuthorizedUserCredential | None, str | None]:
        try:
            client = self.readiness()
        except SettingsServiceError as exc:
            return "failed", exc.code, None, None
        try:
            response = self.exchanger(client, code, self.redirect_uri, verifier)
        except SettingsServiceError as exc:
            return "failed", exc.code if exc.code in {"OAUTH_GRANT_MISMATCH", "PROVIDER_UNAVAILABLE", "TIMEOUT"} else "FLOW_STATE_REJECTED", None, None
        except Exception:  # noqa: BLE001 - token endpoint diagnostics may carry secrets
            return "failed", "PROVIDER_UNAVAILABLE", None, None
        if not isinstance(response, Mapping):
            return "failed", "PROVIDER_UNAVAILABLE", None, None
        refresh_token = response.get("refresh_token")
        access_token = response.get("access_token")
        if not exact_scopes(response.get("scope"), purpose):
            return "failed", "OAUTH_GRANT_MISMATCH", None, None
        if (not isinstance(refresh_token, str) or not refresh_token or not isinstance(access_token, str)
                or not access_token or str(response.get("token_type", "Bearer")).lower() != "bearer"):
            return "failed", "FLOW_STATE_REJECTED", None, None
        try:
            permission_id = self.account_reader(access_token)
        except Exception:  # noqa: BLE001 - identity read diagnostics may carry the token
            return "failed", "RECONNECT_REQUIRED", None, None
        if not isinstance(permission_id, str) or not permission_id:
            return "failed", "RECONNECT_REQUIRED", None, None
        try:
            candidate = AuthorizedUserCredential(purpose=purpose, client_id=client.client_id,
                                                 client_secret=client.client_secret,
                                                 refresh_token=refresh_token, scope=purpose.scope)
        except GoogleOAuthCredentialError as exc:
            return "failed", exc.oauth_code, None, None
        return "awaiting_commit", None, candidate, permission_id

    def commit(self, purpose_value: object, body: Mapping[str, Any]) -> dict[str, Any]:
        purpose = self._purpose(purpose_value)
        if (set(body) != {"flow_id", "generation", "replace"} or not isinstance(body["generation"], str)
                or not isinstance(body["replace"], bool)):
            raise SettingsServiceError("INVALID_REQUEST", "Choose a listed Google connection action.")
        epoch = self._require_live()
        with self._lock:
            flow = self._flow_locked(purpose, body["flow_id"])
            if flow.status == "expired":
                raise failure("FLOW_EXPIRED")
            if flow.status == "cancelled":
                raise failure("FLOW_CANCELLED")
            if flow.status == "denied":
                raise failure("OAUTH_ACCESS_DENIED")
            if flow.status == "failed":
                raise failure(flow.error_code or "FLOW_STATE_REJECTED")
            if flow.status in {"pending", "exchanging", "committing"}:
                raise failure("FLOW_BUSY" if flow.status != "committing" else "COMMIT_IN_PROGRESS")
            if flow.status != "awaiting_commit" or flow.candidate is None or flow.fresh_permission_id is None:
                raise SettingsServiceError("FLOW_NOT_FOUND", "This Google sign-in is no longer known. Start again.", 404)
            if flow.session_epoch != epoch:
                flow.status, flow.error_code = "failed", "FLOW_STATE_REJECTED"
                flow.discard_secrets()
                raise failure("FLOW_STATE_REJECTED")
            if body["generation"] != flow.generation:
                raise SettingsServiceError("CONFIGURATION_CHANGED", "Settings changed elsewhere. Review and try again.", 409)
            if body["replace"] is not flow.replace:
                raise SettingsServiceError("INVALID_REQUEST", "Choose a listed Google connection action.")
            flow.status = "committing"
            candidate, permission_id = flow.candidate, flow.fresh_permission_id
        role = ROLES[purpose.role_slug]
        try:
            result = self.credentials.save_google_oauth(role, candidate, flow.generation, replace=flow.replace,
                                                        fresh_permission_id=permission_id)
        except SettingsServiceError as exc:
            with self._lock:
                flow.status, flow.error_code = ("account_mismatch" if exc.code == "ACCOUNT_MISMATCH" else "failed"), exc.code
                flow.discard_secrets()
            raise
        except Exception:  # noqa: BLE001 - save diagnostics may contain credential material
            with self._lock:
                flow.status, flow.error_code = "failed", "SAVE_FAILED"
                flow.discard_secrets()
            raise failure("SAVE_FAILED") from None
        with self._lock:
            flow.status, flow.error_code = "complete", None
            flow.discard_secrets()
        return {**result, "flow_id": flow.flow_id, "purpose": purpose.value, "status": "complete"}

    # -- lifecycle -------------------------------------------------------------

    def invalidate_precommit(self) -> None:
        """Block new begin/commit and drop pre-commit flows; committing flows finish untouched."""

        with self._lock:
            self._blocked = True
            for flow in self._flows.values():
                if flow.status in PRE_COMMIT_STATUSES:
                    flow.status, flow.error_code = "cancelled", "FLOW_CANCELLED"
                    flow.discard_secrets()


# -- bounded live transport ----------------------------------------------------------

def _bounded_https(host: str, method: str, path: str, headers: Mapping[str, str], body: bytes | None, *,
                   connection_factory: Callable[..., Any] = http.client.HTTPSConnection) -> tuple[int, bytes]:
    """One HTTPS request: fixed host, 10 s socket timeout, no redirects, ≤1 MiB."""

    connection = None
    try:
        connection = connection_factory(host, timeout=OAUTH_HTTP_TIMEOUT_SECONDS)
        merged = {key: value for key, value in headers.items() if key.lower() != "accept-encoding"}
        merged["Accept-Encoding"] = "identity"
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        data = response.read(OAUTH_MAX_RESPONSE_BYTES + 1)
        if len(data) > OAUTH_MAX_RESPONSE_BYTES:
            raise failure("PROVIDER_UNAVAILABLE")
        return int(response.status), data
    except SettingsServiceError:
        raise
    except TimeoutError:
        raise failure("TIMEOUT") from None
    except Exception:  # noqa: BLE001 - transport text may carry codes or tokens
        raise failure("PROVIDER_UNAVAILABLE") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001, S110 - best-effort close
                pass


def _json_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise failure("PROVIDER_UNAVAILABLE") from None
    if not isinstance(value, dict):
        raise failure("PROVIDER_UNAVAILABLE")
    return value


class GoogleTokenExchanger:
    """Authorization-code exchange against the fixed Google token endpoint."""

    def __init__(self, *, connection_factory: Callable[..., Any] = http.client.HTTPSConnection) -> None:
        self.connection_factory = connection_factory

    def __call__(self, client: GoogleOAuthClient, code: str, redirect_uri: str, verifier: str) -> Mapping[str, Any]:
        body = urlencode({
            "grant_type": "authorization_code", "code": code, "client_id": client.client_id,
            "client_secret": client.client_secret, "redirect_uri": redirect_uri, "code_verifier": verifier,
        }).encode("ascii")
        host = urlsplit(TOKEN_URI).netloc
        status, data = _bounded_https(host, "POST", "/token", {"Content-Type": "application/x-www-form-urlencoded"},
                                      body, connection_factory=self.connection_factory)
        if status != 200:
            raise failure("FLOW_STATE_REJECTED" if status in {400, 401} else "PROVIDER_UNAVAILABLE")
        return _json_object(data)


class GoogleAccountReader:
    """Identity-only ``about.get(fields=user(permissionId))`` with a bearer access token."""

    def __init__(self, *, connection_factory: Callable[..., Any] = http.client.HTTPSConnection) -> None:
        self.connection_factory = connection_factory

    def __call__(self, access_token: str) -> str:
        status, data = _bounded_https("www.googleapis.com", "GET", ABOUT_PATH,
                                      {"Authorization": "Bearer " + access_token}, None,
                                      connection_factory=self.connection_factory)
        if status != 200:
            raise failure("RECONNECT_REQUIRED")
        payload = _json_object(data)
        user = payload.get("user")
        permission_id = user.get("permissionId") if isinstance(user, Mapping) else None
        if not isinstance(permission_id, str) or not permission_id:
            raise failure("RECONNECT_REQUIRED")
        return permission_id


class GoogleGrantVerifier:
    """Fresh refresh-token grant + account read for ``CredentialService.oauth_verifier``."""

    def __init__(self, *, connection_factory: Callable[..., Any] = http.client.HTTPSConnection) -> None:
        self.connection_factory = connection_factory
        self.account_reader = GoogleAccountReader(connection_factory=connection_factory)

    def __call__(self, credential: AuthorizedUserCredential) -> tuple[Any, str]:
        body = urlencode({
            "grant_type": "refresh_token", "refresh_token": credential.refresh_token,
            "client_id": credential.client_id, "client_secret": credential.client_secret,
        }).encode("ascii")
        status, data = _bounded_https(urlsplit(TOKEN_URI).netloc, "POST", "/token",
                                      {"Content-Type": "application/x-www-form-urlencoded"}, body,
                                      connection_factory=self.connection_factory)
        if status != 200:
            raise failure("RECONNECT_REQUIRED" if status in {400, 401} else "PROVIDER_UNAVAILABLE")
        payload = _json_object(data)
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise failure("RECONNECT_REQUIRED")
        return payload.get("scope"), self.account_reader(access_token)


class FakeGoogleOAuthProvider:
    """Provider-free fake used by fake mode and tests; completes the redirect itself."""

    def __init__(self, *, permission_id: str = "fake-owner", peer_permission_id: str | None = None) -> None:
        self.permission_id = permission_id
        self.peer_permission_id = peer_permission_id or permission_id
        self.service: GoogleOAuthFlowService | None = None
        self.opened: list[str] = []
        self.exchanges = 0
        self.deny_next = False
        self.auto_callback = True
        self.scope_override: str | None = None
        self.pending_scope: str | None = None
        self.refresh_counter = 0

    def opener(self, url: str) -> None:
        from urllib.parse import parse_qs

        self.opened.append(url)
        params = parse_qs(urlsplit(url).query)
        self.pending_scope = params["scope"][0]
        if self.service is None or not self.auto_callback:
            return
        state = params["state"][0]
        query = {"state": state, "error": "access_denied"} if self.deny_next else {"state": state, "code": "fake-code"}
        self.deny_next = False
        self.service.callback(query)

    def exchanger(self, client: GoogleOAuthClient, code: str, redirect_uri: str, verifier: str) -> Mapping[str, Any]:
        self.exchanges += 1
        self.refresh_counter += 1
        scope = self.scope_override or self.pending_scope or GoogleOAuthPurpose.MCP.scope
        return {"access_token": "fake-access", "refresh_token": f"fake-refresh-{self.refresh_counter}",
                "scope": scope, "token_type": "Bearer", "expires_in": 3599}

    def account_reader(self, access_token: str) -> str:
        return self.permission_id

    def verifier(self, credential: AuthorizedUserCredential) -> tuple[Any, str]:
        own = credential.refresh_token.startswith("fake-refresh-")
        return credential.scope, self.permission_id if own else self.peer_permission_id


__all__ = [
    "FLOW_TTL_SECONDS",
    "PRE_COMMIT_STATUSES",
    "TERMINAL_STATUSES",
    "FakeGoogleOAuthProvider",
    "GoogleAccountReader",
    "GoogleGrantVerifier",
    "GoogleOAuthFlowService",
    "GoogleTokenExchanger",
]
