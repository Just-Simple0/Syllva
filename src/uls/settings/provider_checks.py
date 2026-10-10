"""Bounded, explicit read-only checks; no provider error body leaves this module."""

from __future__ import annotations

import http.client
import io
import json
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlencode

from uls.config.google_oauth import (
    AUTHORIZED_USER_TYPE,
    SERVICE_ACCOUNT_TYPE,
    AuthorizedUserCredential,
    GoogleOAuthClient,
    GoogleOAuthCredentialError,
    GoogleOAuthPurpose,
    credential_type,
    parse_authorized_user_bytes,
)

from .config_service import SettingsServiceError
from .credential_roles import CredentialRole

READ_ONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_RESPONSE_BYTES = 1024 * 1024
OAUTH_URL = "https://oauth2.googleapis.com/token"

MESSAGES = {
    "VERIFIED": "Connection verified with read-only checks.",
    "INVALID_CREDENTIAL": "The credential was rejected. Check it and try again.",
    "PERMISSION_MISSING": "Share the configured resource with this credential, then retry.",
    "NOT_FOUND": "A configured resource could not be found.",
    "PROVIDER_UNAVAILABLE": "The provider could not be reached. Nothing changed.",
    "TIMEOUT": "The provider did not respond in time. Nothing changed.",
    "NOT_CONFIGURED": "Configure the resource before testing this connection.",
    "CHECK_BUSY": "A check is already running or was just completed. Wait a few seconds.",
    "CHECK_NOT_READ_ONLY": "This action is outside the read-only check boundary.",
    "DESTINATION_NOT_ALLOWED": "Only public Canvas addresses are supported.",
    "PROFILE_MISMATCH": "The token belongs to a different Canvas account.",
    "RATE_LIMITED": "The provider asked us to wait. Retry check later.",
    # Personal Google OAuth (P2 plan §2): fixed codes, never provider text.
    "OAUTH_APP_NOT_READY": "Add your own Google Desktop client to config.yaml (owner-only file) before connecting.",
    "FLOW_BUSY": "A Google sign-in is already in progress for this connection.",
    "FLOW_NOT_FOUND": "This Google sign-in is no longer known. Start again.",
    "FLOW_EXPIRED": "This Google sign-in took too long. Start again.",
    "FLOW_CANCELLED": "This Google sign-in was cancelled.",
    "FLOW_STATE_REJECTED": "The Google sign-in response did not match this Settings session.",
    "OAUTH_ACCESS_DENIED": "Google access was declined. Nothing changed.",
    "OAUTH_GRANT_MISMATCH": "Google granted a different permission than this connection needs.",
    "OAUTH_CLIENT_MISMATCH": "The stored credential belongs to a different Google client.",
    "ACCOUNT_MISMATCH": "Both Drive connections must use the same Google account.",
    "RECONNECT_REQUIRED": "The Google connection must be set up again.",
    "COMMIT_IN_PROGRESS": "This Google sign-in is being saved and cannot be cancelled.",
    "SAVE_FAILED": "The Google connection could not be saved. Nothing changed.",
    "CREDENTIAL_TYPE_CONFLICT": "Retrieval and worker must use the same kind of Google credential. Forget the other connection first.",
    "CREDENTIAL_SOURCE_EXTERNAL": "An environment or external credential is configured for this role. Detach it first.",
    "REPLACE_REQUIRED": "A credential is already stored for this role. Choose replace.",
    "BROWSER_REQUIRED": "Google sign-in needs a browser. Run uls setup without --no-browser.",
}


def failure(code: str) -> SettingsServiceError:
    return SettingsServiceError(code, MESSAGES.get(code, "This check could not be completed."), 409)


def structural_oauth_credential(role: CredentialRole, value: bytes, client: GoogleOAuthClient | None) -> AuthorizedUserCredential:
    """Exact ``authorized_user`` structure for a Google role; never the SA parser."""

    if role.provider != "google":
        raise failure("INVALID_CREDENTIAL")
    if client is None:
        raise failure("OAUTH_APP_NOT_READY")
    if not 0 < len(value) <= role.max_bytes:
        raise failure("INVALID_CREDENTIAL")
    try:
        return parse_authorized_user_bytes(value, purpose=GoogleOAuthPurpose.parse(role.purpose), client=client)
    except GoogleOAuthCredentialError as exc:
        raise failure(exc.oauth_code if exc.oauth_code in MESSAGES else "INVALID_CREDENTIAL") from None


def structural_credential(role: CredentialRole, value: bytes, *, google_loader: Callable[..., Any] | None = None) -> dict[str, Any] | None:
    if not 0 < len(value) <= role.max_bytes:
        raise failure("INVALID_CREDENTIAL")
    try:
        text = value.decode("utf-8")
        if role.provider != "google":
            if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
                raise ValueError("invalid token")
            return None
        payload = json.loads(text)
        if (not isinstance(payload, dict) or payload.get("type") != "service_account"
                or any(not isinstance(payload.get(key), str) or not payload[key]
                       for key in ("client_email", "private_key", "private_key_id", "project_id", "token_uri"))
                or payload["token_uri"] != "https://oauth2.googleapis.com/token"):
            raise ValueError("invalid service account")
        if google_loader is None:
            from google.oauth2.service_account import Credentials
            google_loader = Credentials.from_service_account_info
        google_loader(payload, scopes=[READ_ONLY_SCOPE])  # type: ignore[no-untyped-call]
        return payload
    except Exception:  # noqa: BLE001 - parser/SDK diagnostics may contain the submitted key
        raise failure("INVALID_CREDENTIAL") from None


class TransportCheckBudget:
    """Check-local wire/deadline bounds; socket timeouts do not forcibly bound DNS."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.deadline = clock() + 30.0
        self.wire_calls = 0
        self.refresh_calls = 0
        self.google_credentials: Any = None

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise failure("TIMEOUT")
        return remaining

    def dispatch(self, timeout: float) -> float:
        remaining = self.remaining()
        if self.wire_calls >= 8:
            raise failure("PROVIDER_UNAVAILABLE")
        self.wire_calls += 1
        return min(10.0, timeout, remaining)


class GoogleAuthResponse:
    """Bounded bytes implementing the public google.auth.transport.Response interface."""

    def __init__(self, data: bytes) -> None:
        self.status = 200
        self.data = data
        self.headers: dict[str, str] = {}


class BoundedGoogleAuthRequest:
    """Public Request callable: error responses never enter google-auth's retry loop."""

    def __init__(self, transport: LiveReadOnlyTransport, budget: TransportCheckBudget) -> None:
        self.transport, self.budget = transport, budget

    def __call__(self, url: str, method: str = "GET", body: Any = None,
                 headers: Any = None, timeout: float = 120, **kwargs: Any) -> GoogleAuthResponse:
        if url != OAUTH_URL or method != "POST" or kwargs:
            raise failure("CHECK_NOT_READ_ONLY")
        if self.budget.refresh_calls >= 1:
            raise failure("PROVIDER_UNAVAILABLE")
        self.budget.refresh_calls += 1
        status, data = self.transport._wire("oauth2.googleapis.com", "POST", "/token",
                                           headers or {}, body, self.budget, timeout)
        if status != 200:
            raise failure("INVALID_CREDENTIAL" if status in {400, 401} else "PROVIDER_UNAVAILABLE")
        return GoogleAuthResponse(data)


class _DeadlineReader(io.RawIOBase):
    """Refresh the socket timeout before each raw read, including HTTP trickles."""

    def __init__(self, sock: Any, budget: TransportCheckBudget, timeout: float) -> None:
        super().__init__()
        self._sock = sock
        self._budget = budget
        self._timeout = timeout
        self._reader = sock.makefile("rb", buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        self._sock.settimeout(_socket_timeout(self._budget, self._timeout))
        self._budget.remaining()
        count = self._reader.readinto(buffer)
        return count if count is not None else 0

    def close(self) -> None:
        with suppress(Exception):
            self._reader.close()
        super().close()


class _DeadlineSocket:
    """HTTP socket facade enforcing the shared deadline on each raw operation."""

    def __init__(self, sock: Any, budget: TransportCheckBudget, timeout: float) -> None:
        self._sock = sock
        self._budget = budget
        self._timeout = timeout

    def settimeout(self, timeout: float) -> None:
        self._sock.settimeout(timeout)

    def sendall(self, data: bytes) -> None:
        self._sock.settimeout(_socket_timeout(self._budget, self._timeout))
        self._budget.remaining()
        self._sock.sendall(data)

    def makefile(self, mode: str) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("Read-only response stream required")
        # A one-byte buffer prevents BufferedReader from reading response-body
        # bytes beyond the explicit HTTPResponse.read(cap + 1) request.
        return io.BufferedReader(_DeadlineReader(self._sock, self._budget, self._timeout), buffer_size=1)

    def close(self) -> None:
        self._sock.close()


def _socket_timeout(budget: TransportCheckBudget, timeout: float) -> float:
    return min(10.0, timeout, budget.remaining())


class LiveReadOnlyTransport:
    """Fixed endpoints, bounded bytes before parsing, no redirects or automatic retries."""

    def __init__(self, *, connection_factory: Callable[..., Any] = http.client.HTTPSConnection,
                 google_loader: Callable[..., Any] | None = None,
                 oauth_loader: Callable[..., Any] | None = None) -> None:
        self.connection_factory = connection_factory
        self.google_loader = google_loader
        self.oauth_loader = oauth_loader

    def _wire(self, host: str, method: str, target: str, headers: dict[str, str], body: Any,
              budget: TransportCheckBudget, timeout: float) -> tuple[int, bytes]:
        socket_timeout = budget.dispatch(timeout)
        connection = None
        response = None
        try:
            connection = self.connection_factory(host, timeout=socket_timeout)
            headers = {key: value for key, value in headers.items() if key.lower() != "accept-encoding"}
            headers["Accept-Encoding"] = "identity"
            # HTTPSConnection.request() auto-connects when sock is None. Make
            # setup explicit so a slow DNS/TLS connect cannot be followed by
            # an expired-deadline credential-bearing request dispatch.
            connection.connect()
            raw_socket = getattr(connection, "sock", None)
            if raw_socket is None:
                raise failure("PROVIDER_UNAVAILABLE")
            connection.sock = _DeadlineSocket(raw_socket, budget, timeout)
            self._refresh_socket_timeout(connection, budget, timeout)
            budget.remaining()
            connection.request(method, target, body=body, headers=headers)
            self._refresh_socket_timeout(connection, budget, timeout)
            response = connection.getresponse()
            if (response.getheader("Content-Encoding") or "identity").strip().lower() != "identity":
                raise failure("PROVIDER_UNAVAILABLE")
            self._refresh_socket_timeout(connection, budget, timeout)
            content_length = getattr(response, "length", None)
            if content_length is not None and content_length > MAX_RESPONSE_BYTES:
                raise failure("PROVIDER_UNAVAILABLE")
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if (len(data) > MAX_RESPONSE_BYTES
                    or content_length is not None and len(data) != content_length):
                raise failure("PROVIDER_UNAVAILABLE")
            budget.remaining()
            return response.status, data
        except SettingsServiceError:
            raise
        except TimeoutError:
            raise failure("TIMEOUT") from None
        except Exception:  # noqa: BLE001 - discard potentially secret-bearing transport text
            raise failure("PROVIDER_UNAVAILABLE") from None
        finally:
            if response is not None:
                with suppress(Exception):
                    response.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

    @staticmethod
    def _refresh_socket_timeout(connection: Any, budget: TransportCheckBudget, timeout: float) -> None:
        remaining = budget.remaining()
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(min(10.0, timeout, remaining))
        # Include time spent applying the timeout before request/response work.
        budget.remaining()

    def _google(self, value: bytes, budget: TransportCheckBudget) -> Any:
        if budget.google_credentials is None:
            try:
                payload = json.loads(value)
                if not isinstance(payload, dict) or payload.get("token_uri") != OAUTH_URL:
                    raise failure("CHECK_NOT_READ_ONLY")
                if credential_type(payload) == AUTHORIZED_USER_TYPE:
                    # Personal OAuth: the stored single scope is used as-is; the
                    # check itself still performs only metadata GETs.
                    scopes = payload.get("scopes")
                    if not isinstance(scopes, list) or len(scopes) != 1 or not isinstance(scopes[0], str):
                        raise failure("INVALID_CREDENTIAL")
                    loader = self.oauth_loader
                    if loader is None:
                        from google.oauth2.credentials import Credentials as UserCredentials
                        loader = UserCredentials.from_authorized_user_info
                    credentials = loader(payload, scopes=list(scopes))  # type: ignore[no-untyped-call]
                elif credential_type(payload) != SERVICE_ACCOUNT_TYPE:
                    raise failure("INVALID_CREDENTIAL")
                else:
                    loader = self.google_loader
                    if loader is None:
                        from google.oauth2.service_account import Credentials
                        loader = Credentials.from_service_account_info
                    credentials = loader(payload, scopes=[READ_ONLY_SCOPE])  # type: ignore[no-untyped-call]
            except SettingsServiceError:
                raise
            except Exception:  # noqa: BLE001 - loader diagnostics can contain the submitted key
                raise failure("INVALID_CREDENTIAL") from None
            credentials.refresh(BoundedGoogleAuthRequest(self, budget))
            budget.remaining()
            budget.google_credentials = credentials
        return budget.google_credentials

    def request(self, provider: str, endpoint: str, value: bytes, *, version: str, timeout: float,
                budget: TransportCheckBudget | None = None) -> tuple[int, dict[str, Any]]:
        budget = budget or TransportCheckBudget()
        if provider == "notion":
            if not re.fullmatch(r"/v1/(users/me|databases/[A-Za-z0-9-]+)", endpoint):
                raise failure("CHECK_NOT_READ_ONLY")
            status, data = self._wire("api.notion.com", "GET", endpoint,
                {"Authorization": "Bearer " + value.decode(), "Notion-Version": version}, None, budget, timeout)
        else:
            if provider != "google" or not re.fullmatch(r"[A-Za-z0-9_-]+", endpoint):
                raise failure("CHECK_NOT_READ_ONLY")
            headers: dict[str, str] = {}
            self._google(value, budget).apply(headers)
            target = "/drive/v3/files/" + endpoint + "?" + urlencode({
                "fields": "id,trashed,capabilities(canAddChildren,canListChildren)"})
            status, data = self._wire("www.googleapis.com", "GET", target, headers, None, budget, timeout)
        payload = json.loads(data) if status == 200 else {}
        if not isinstance(payload, dict):
            raise failure("PROVIDER_UNAVAILABLE")
        budget.remaining()
        return status, payload


class FakeProviderTransport:
    def request(self, provider: str, endpoint: str, value: bytes, *, version: str, timeout: float,
                budget: TransportCheckBudget | None = None) -> tuple[int, dict[str, Any]]:
        if budget is not None:
            budget.dispatch(timeout)
        if b"invalid" in value:
            return 401, {}
        if b"outage" in value:
            return 503, {}
        return 200, {"id": "fake", "capabilities": {"canAddChildren": True, "canListChildren": True}}


class ProviderChecks:
    def __init__(self, transport: Any = None, *, cooldown: float = 5.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.transport = transport or LiveReadOnlyTransport()
        self.cooldown = cooldown
        self.clock = clock
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._last: dict[str, float] = {}
        self.results: dict[str, dict[str, Any]] = {}

    def check(self, role: CredentialRole, value: bytes, config: Any) -> dict[str, Any]:
        with self._lock:
            if role.slug in self._active or self.clock() - self._last.get(role.slug, -100.0) < self.cooldown:
                raise failure("CHECK_BUSY")
            self._active.add(role.slug)
        try:
            result = self._check(role, value, config)
            self.results[role.slug] = result
            return result
        except SettingsServiceError as error:
            code = error.code if isinstance(error.code, str) and error.code in MESSAGES else "PROVIDER_UNAVAILABLE"
            self.results[role.slug] = {
                "code": code,
                "message": MESSAGES[code],
                "checked_at": time.time(),
                "resources": [],
            }
            raise failure(code) from None
        except TimeoutError:
            self.results[role.slug] = {
                "code": "TIMEOUT",
                "message": MESSAGES["TIMEOUT"],
                "checked_at": time.time(),
                "resources": [],
            }
            raise failure("TIMEOUT") from None
        except Exception:  # noqa: BLE001 - unexpected check diagnostics may contain credentials
            self.results[role.slug] = {
                "code": "PROVIDER_UNAVAILABLE",
                "message": MESSAGES["PROVIDER_UNAVAILABLE"],
                "checked_at": time.time(),
                "resources": [],
            }
            raise failure("PROVIDER_UNAVAILABLE") from None
        finally:
            with self._lock:
                self._active.discard(role.slug)
                self._last[role.slug] = self.clock()

    def _check(self, role: CredentialRole, value: bytes, config: Any) -> dict[str, Any]:
        if role.provider == "notion":
            resources = [("Account", "/v1/users/me")]
            for field, label in (("courses_db_id", "Courses database"), ("sessions_db_id", "Sessions database"),
                                 ("materials_db_id", "Materials database"), ("file_intake_db_id", "File Intake database"),
                                 ("input_request_db_id", "Input Request database")):
                resource = getattr(config.notion, field, "")
                if resource:
                    resources.append((label, "/v1/databases/" + resource))
        else:
            resources = [(label, getattr(config.google_drive, field, "")) for label, field in
                         (("University folder", "university_root_id"), ("Inbox folder", "inbox_root_id"))]
            resources = [(label, resource) for label, resource in resources if resource]
        if not resources:
            raise failure("NOT_CONFIGURED")
        budget = TransportCheckBudget(clock=self.clock)
        rows = []
        for label, endpoint in resources[:8]:
            remaining = budget.remaining()
            try:
                status, payload = self.transport.request(role.provider, endpoint, value, version=getattr(config.notion, "notion_version", "2025-09-03"),
                                                        timeout=min(10, remaining), budget=budget)
            except SettingsServiceError:
                raise
            except TimeoutError:
                raise failure("TIMEOUT") from None
            except Exception:  # noqa: BLE001 - provider exception text is secret-bearing
                raise failure("PROVIDER_UNAVAILABLE") from None
            budget.remaining()
            if status != 200:
                raise failure({401: "INVALID_CREDENTIAL", 403: "PERMISSION_MISSING", 404: "NOT_FOUND"}.get(status, "PROVIDER_UNAVAILABLE"))
            if payload.get("trashed"):
                raise failure("NOT_FOUND")
            rows.append({"label": label, "reachable": True,
                         "worker_can_add": payload.get("capabilities", {}).get("canAddChildren") is True})
        budget.remaining()
        return {"code": "VERIFIED", "message": MESSAGES["VERIFIED"], "checked_at": time.time(), "resources": rows}
