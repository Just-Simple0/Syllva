"""Ephemeral session/CSRF state and a strict local HTTP security boundary.

Every launch serves HTML, static assets, APIs, and its session cookie only
under an unpredictable per-launch path prefix. Browser cookies are not
port-scoped, so the prefix is what guarantees that an old tab (even on a reused
port) never presents or receives the replacement session's cookie.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SESSION_COOKIE = "uls_settings_session"
BOOTSTRAP_TTL_SECONDS = 30.0
SESSION_IDLE_SECONDS = 15 * 60.0
MAX_REQUEST_BODY_BYTES = 16 * 1024
PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_FETCH_METADATA = (b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest", b"sec-fetch-user")
_ENDED_PAGE = (
    b"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    b"<title>Syllva Settings</title></head><body><h1>Settings session ended</h1>"
    b"<p>This Settings window is no longer connected and cannot save. "
    b"Run uls setup to open Settings again.</p></body></html>"
)


def new_path_prefix() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class _SessionRecord:
    handle_digest: bytes
    csrf_value: str
    last_activity: float


class SessionSecurity:
    """One-use bootstrap and one ephemeral authenticated browser session."""

    def __init__(
        self,
        bootstrap_token: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = SESSION_IDLE_SECONDS,
    ) -> None:
        self._clock = clock
        self.idle_seconds = idle_seconds
        self._bootstrap_digest = _digest(bootstrap_token)
        self._bootstrap_expires = clock() + BOOTSTRAP_TTL_SECONDS
        self._bootstrap_used = False
        self._session: _SessionRecord | None = None
        self._state = "new"

    @classmethod
    def issue(cls, *, clock: Callable[[], float] = time.monotonic) -> tuple[str, SessionSecurity]:
        token = secrets.token_urlsafe(32)
        return token, cls(token, clock=clock)

    @property
    def is_replaced(self) -> bool:
        return self._state == "replaced"

    @property
    def is_closed(self) -> bool:
        return self._state in {"expired", "closed", "replaced", "bootstrap_expired"}

    def consume_bootstrap(self, token: str) -> str | None:
        if self._bootstrap_used or self._clock() > self._bootstrap_expires:
            return None
        if not hmac.compare_digest(_digest(token), self._bootstrap_digest):
            return None
        self._bootstrap_used = True
        handle = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self._session = _SessionRecord(_digest(handle), csrf, self._clock())
        self._state = "active"
        return handle

    def session_code(self, handle: str | None) -> str | None:
        if self._state == "replaced":
            return "SESSION_REPLACED"
        if self._session is None:
            return "SESSION_EXPIRED"
        if self._clock() - self._session.last_activity >= self.idle_seconds:
            self._session = None
            self._state = "expired"
            return "SESSION_EXPIRED"
        if handle is None or not hmac.compare_digest(_digest(handle), self._session.handle_digest):
            return "SESSION_EXPIRED"
        return None

    def csrf_for(self, handle: str | None) -> str | None:
        if self.session_code(handle) is not None or self._session is None:
            return None
        return self._session.csrf_value

    def validate_csrf(self, handle: str | None, submitted: str | None) -> bool:
        expected = self.csrf_for(handle)
        return bool(expected and submitted and hmac.compare_digest(expected, submitted))

    def record_explicit_activity(self, handle: str | None) -> bool:
        if self.session_code(handle) is not None or self._session is None:
            return False
        self._session.last_activity = self._clock()
        return True

    def close(self, handle: str | None) -> bool:
        if self.session_code(handle) is not None:
            return False
        self._session = None
        self._state = "closed"
        return True

    def replace(self) -> None:
        self._session = None
        self._bootstrap_used = True
        self._bootstrap_digest = b""
        self._state = "replaced"

    def lifecycle_state(self) -> str:
        """Report new/active/bootstrap_expired/expired/closed/replaced for the launcher.

        This is a passive check: it never renews the inactivity timer.
        """

        if self._state == "new" and self._clock() > self._bootstrap_expires:
            self._bootstrap_used = True
            self._state = "bootstrap_expired"
        elif self._state == "active" and self._session is not None and (
            self._clock() - self._session.last_activity >= self.idle_seconds
        ):
            self._session = None
            self._state = "expired"
        return self._state


class BarrierClosed(Exception):
    """The settings process is handing over; no new mutation may start."""


class MutationBarrier:
    """Tracks in-flight mutations so a handover waits for every one to finish.

    Entry and exit happen on the event-loop thread (around the worker-thread
    call), so the counter needs no lock.
    """

    def __init__(self) -> None:
        self._active = 0
        self._closed = False
        self._idle: asyncio.Event | None = None

    @property
    def active(self) -> int:
        return self._active

    @contextlib.asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        if self._closed:
            raise BarrierClosed
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1
            if self._active == 0 and self._idle is not None:
                self._idle.set()

    def close(self) -> None:
        self._closed = True

    async def wait_idle(self, timeout: float) -> bool:
        if self._active == 0:
            return True
        self._idle = asyncio.Event()
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            return False
        return True


class SecurityBoundary:
    """Pure ASGI boundary: exact host, prefix, Fetch Metadata, session gate, bodies, headers."""

    def __init__(self, app: Any, security: SessionSecurity, expected_host: str, prefix: str) -> None:
        if not PREFIX_PATTERN.fullmatch(prefix):
            raise ValueError("settings path prefix is invalid")
        self.app = app
        self.security = security
        self.expected_host = expected_host
        self.root = f"/{prefix}/"

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _header_values(scope.get("headers", []))
        hosts = headers.get(b"host", [])
        if len(hosts) != 1 or hosts[0].decode("latin-1") != self.expected_host:
            await _send_json(send, 400, {"error": {"code": "HOST_REJECTED"}})
            return
        if any(len(headers.get(name, [])) > 1 for name in _FETCH_METADATA):
            await _send_json(send, 400, {"error": {"code": "INVALID_REQUEST"}})
            return
        path = scope.get("path", "")
        if not path.startswith(self.root):
            # Unknown or previous launch prefix: never this session.
            await _send_json(send, 404, {"error": {"code": "SESSION_NOT_FOUND"}})
            return
        if self.security.is_replaced:
            await _send_json(send, 401, {"error": {"code": "SESSION_REPLACED"}})
            return
        site = _single_header(headers, b"sec-fetch-site")
        method = scope.get("method")
        document = path == self.root and method == "GET"
        allowed_sites = {None, b"same-origin", b"none"} if document else {None, b"same-origin"}
        if site not in allowed_sites:
            await _send_json(send, 403, {"error": {"code": "SAME_ORIGIN_REQUIRED"}})
            return
        bootstrap = document and any(
            key == "bootstrap"
            for key, _value in parse_qsl(scope.get("query_string", b"").decode("latin-1"),
                                         keep_blank_values=True)
        )
        if not bootstrap:
            code = self.security.session_code(_cookie_value(headers.get(b"cookie", [])))
            if code is not None:
                if document:
                    await _send_bytes(send, 401, _ENDED_PAGE, b"text/html; charset=utf-8")
                else:
                    await _send_json(send, 401, {"error": {"code": code}})
                return
        buffered_receive = receive
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            lengths = headers.get(b"content-length", [])
            if len(lengths) > 1:
                await _send_json(send, 400, {"error": {"code": "INVALID_REQUEST"}})
                return
            if lengths:
                try:
                    declared = int(lengths[0])
                except ValueError:
                    await _send_json(send, 400, {"error": {"code": "INVALID_REQUEST"}})
                    return
                if declared < 0 or declared > MAX_REQUEST_BODY_BYTES:
                    await _send_json(send, 413, {"error": {"code": "REQUEST_TOO_LARGE"}})
                    return
            body_parts: list[bytes] = []
            body_size = 0
            more = True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                part = message.get("body", b"")
                body_size += len(part)
                if body_size > MAX_REQUEST_BODY_BYTES:
                    await _send_json(send, 413, {"error": {"code": "REQUEST_TOO_LARGE"}})
                    return
                body_parts.append(part)
                more = bool(message.get("more_body", False))
            payload = b"".join(body_parts)
            delivered = False

            async def replay_body() -> dict[str, Any]:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": payload, "more_body": False}
                return await receive()

            buffered_receive = replay_body

        async def secured_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = [(key, value) for key, value in message.get("headers", [])
                            if key.lower() not in _SECURITY_HEADERS]
                existing.extend((key, value) for key, value in _SECURITY_HEADERS.items())
                message = {**message, "headers": existing}
            await send(message)

        await self.app(scope, buffered_receive, secured_send)


_SECURITY_HEADERS: dict[bytes, bytes] = {
    b"cache-control": b"no-store",
    b"pragma": b"no-cache",
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"cross-origin-opener-policy": b"same-origin",
    b"cross-origin-resource-policy": b"same-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    b"content-security-policy": (
        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        b"connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        b"object-src 'none'"
    ),
}


def request_cookie(scope: MutableMapping[str, Any]) -> str | None:
    values = _header_values(scope.get("headers", [])).get(b"cookie", [])
    return _cookie_value(values)


def single_header(scope: MutableMapping[str, Any], name: bytes) -> bytes | None:
    return _single_header(_header_values(scope.get("headers", [])), name)


def same_origin_request(
    scope: MutableMapping[str, Any], expected_origin: str, *, require_origin: bool,
) -> bool:
    headers = _header_values(scope.get("headers", []))
    if len(headers.get(b"sec-fetch-site", [])) > 1:
        return False
    fetch_site = _single_header(headers, b"sec-fetch-site")
    if fetch_site is not None and fetch_site != b"same-origin":
        return False
    origins = headers.get(b"origin", [])
    if len(origins) > 1:
        return False
    if origins:
        return origins[0].decode("latin-1") == expected_origin
    if require_origin:
        referers = headers.get(b"referer", [])
        if len(referers) != 1:
            return False
        try:
            parsed = urlsplit(referers[0].decode("latin-1"))
        except ValueError:
            return False
        return f"{parsed.scheme}://{parsed.netloc}" == expected_origin and parsed.scheme == "http"
    return True


def _header_values(raw_headers: list[tuple[bytes, bytes]]) -> dict[bytes, list[bytes]]:
    result: dict[bytes, list[bytes]] = {}
    for key, value in raw_headers:
        result.setdefault(key.lower(), []).append(value)
    return result


def _single_header(headers: dict[bytes, list[bytes]], name: bytes) -> bytes | None:
    values = headers.get(name, [])
    return values[0] if len(values) == 1 else None


def _cookie_value(cookie_headers: list[bytes]) -> str | None:
    if len(cookie_headers) > 1:
        return None
    found: list[str] = []
    for item in cookie_headers:
        for pair in item.decode("latin-1").split(";"):
            name, separator, value = pair.strip().partition("=")
            if separator and name == SESSION_COOKIE:
                found.append(value)
    return found[0] if len(found) == 1 else None


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


async def _send_json(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, content: dict[str, Any]) -> None:
    payload = json.dumps(content, separators=(",", ":")).encode("utf-8")
    await _send_bytes(send, status, payload, b"application/json; charset=utf-8")


async def _send_bytes(
    send: Callable[[dict[str, Any]], Awaitable[None]], status: int, payload: bytes, content_type: bytes,
) -> None:
    headers = [(b"content-type", content_type), *_SECURITY_HEADERS.items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


__all__ = [
    "BOOTSTRAP_TTL_SECONDS", "MAX_REQUEST_BODY_BYTES", "PREFIX_PATTERN", "SESSION_COOKIE",
    "SESSION_IDLE_SECONDS", "BarrierClosed", "MutationBarrier", "SecurityBoundary",
    "SessionSecurity", "new_path_prefix", "request_cookie", "same_origin_request", "single_header",
]
