"""v2 UI HTTP app (implementation spec §10.1-§10.4, design §5.1(a), §5.4a; errata E25).

The Shell relays the webview's ``syllva://localhost`` requests to this loopback server
(design rev 5). Every request passes one boundary before routing:

* ``Host`` must be exactly ``127.0.0.1:<port>`` (421 otherwise: DNS rebinding);
* ``Sec-Fetch-Site``, when present, must be ``same-origin`` or ``none`` — loopback
  ports are *same-site* to each other, so ``same-site`` is refused too;
* every mutating request (not GET/HEAD) must carry an ``Origin`` equal to an
  allowed app origin (mandatory, stricter than §10.1's "if present") and, except
  the session exchange itself, the session's ``X-Syllva-CSRF`` header;
* bodies are bounded (64 KiB until the upload route, R2-3, sets its own limit);
* every response carries the §10.4 security headers and ``Cache-Control: no-store``.

Debug mode is off and the server keeps no access log of its own beyond
``log_event`` codes with the route (query and fragment never logged, §13.1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from syllva.core.logging import log_event
from syllva.gate.service import GateError, GateService
from syllva.ui.canvas import TOKEN_BODY_LIMIT, TOKEN_PATH, CanvasApi
from syllva.ui.library import LibraryApi
from syllva.ui.relay import RelayVerifier
from syllva.ui.sessions import Session, SessionStore, WrongWindow

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "syllva_sid"
CSRF_HEADER = "x-syllva-csrf"
MAX_BODY = 64 * 1024
SAFE_METHODS = frozenset({"GET", "HEAD"})
UPLOAD_PATH = re.compile(r"/api/library/(upload|[A-Za-z0-9-]+/replace)")
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
]



def _error(status: int, code: str) -> Response:
    return JSONResponse({"error": code}, status_code=status)


class Boundary:
    """ASGI middleware enforcing the request boundary and the response headers."""

    def __init__(
        self, app: ASGIApp, *, port: int, allowed_origins: Iterable[str], upload_limit: int = MAX_BODY,
        relay: RelayVerifier | None = None,
    ) -> None:
        self.app = app
        self.upload_limit = upload_limit
        self.relay = relay
        self.host = f"127.0.0.1:{port}".encode()
        self.origins = frozenset(o.encode() for o in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "lifespan":
                await self.app(scope, receive, send)
            return  # no websockets

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in SECURITY_HEADERS}
                message["headers"] = [h for h in message.get("headers", []) if h[0].lower() not in names] + SECURITY_HEADERS
            await send(message)

        denial = self._deny(scope)
        if denial is not None:
            log_event("UI_DENIED", route=scope.get("path", "")[:80], reason=denial[1])
            await _error(*denial)(scope, receive, send_with_headers)
            return
        if self.relay is not None:  # the window the Shell vouched for (relay mode only, errata E29)
            window = dict(scope.get("headers", [])).get(b"x-syllva-window", b"").decode("ascii", "replace")
            scope.setdefault("state", {})["syllva_window"] = window
        await self.app(scope, _bounded(receive, self._limit(scope)), send_with_headers)

    def _limit(self, scope: Scope) -> int:
        path = scope.get("path", "")
        if path == TOKEN_PATH:
            return TOKEN_BODY_LIMIT  # the Canvas token body (R3 plan §6.1)
        return self.upload_limit if UPLOAD_PATH.fullmatch(path) else MAX_BODY

    def _deny(self, scope: Scope) -> tuple[int, str] | None:
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        host = headers.get(b"host", [])
        if len(host) != 1 or host[0] != self.host:
            return 421, "MISDIRECTED"
        site = headers.get(b"sec-fetch-site")
        if site is not None and (len(site) != 1 or site[0] not in (b"same-origin", b"none")):
            return 403, "CROSS_SITE"
        origin = headers.get(b"origin")
        if self.relay is not None:
            # stdio mode: only the Shell relay reaches the Core; Origin, if a webview sends one, must be the app
            if scope.get("path") != "/healthz":
                window, tag = headers.get(b"x-syllva-window", []), headers.get(b"x-syllva-relay", [])
                if len(window) != 1 or len(tag) != 1 or not self.relay.verify(
                        window[0].decode("ascii", "replace"), tag[0].decode("ascii", "replace")):
                    return 403, "RELAY_REQUIRED"
            if origin is not None and (len(origin) != 1 or origin[0] not in self.origins):
                return 403, "BAD_ORIGIN"
        elif scope["method"] not in SAFE_METHODS:
            if origin is None or len(origin) != 1 or origin[0] not in self.origins:
                return 403, "BAD_ORIGIN"
        length = headers.get(b"content-length")
        if length is not None:
            try:
                if int(length[0]) > self._limit(scope):
                    return 413, "BODY_TOO_LARGE"
            except ValueError:
                return 400, "BAD_LENGTH"
        return None


class _BodyTooLarge(Exception):
    pass


def _bounded(receive: Receive, limit: int) -> Receive:
    seen = 0

    async def wrapped() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise _BodyTooLarge
        return message

    return wrapped


async def _json_body(request: Request, keys: frozenset[str]) -> dict[str, Any] | None:
    try:
        raw = await request.body()
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != keys:
        return None
    return value


def create_app(
    sessions: SessionStore,
    *,
    port: int,
    allowed_origins: Iterable[str],
    gate: GateService | None = None,
    library: LibraryApi | None = None,
    relay: RelayVerifier | None = None,
    canvas: CanvasApi | None = None,
) -> ASGIApp:
    def window_of(request: Request) -> str | None:
        return request.scope.get("state", {}).get("syllva_window") if relay is not None else None

    def current(request: Request) -> Session | None:
        return sessions.lookup(request.cookies.get(COOKIE), window_label=window_of(request))

    def mutation_ok(request: Request, session: Session) -> bool:
        return session.csrf_ok(request.headers.get(CSRF_HEADER))

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    async def session_create(request: Request) -> Response:
        body = await _json_body(request, frozenset({"bootstrap"}))
        if body is None:
            return _error(400, "BAD_REQUEST")
        session = sessions.exchange(body["bootstrap"], window_label=window_of(request))
        if session is None:
            log_event("UI_SESSION_REFUSED")
            return _error(403, "BOOTSTRAP_INVALID")
        response = JSONResponse({"csrf": session.csrf, "window_label": session.window_label})
        response.set_cookie(COOKIE, session.session_id, httponly=True, samesite="strict", path="/")
        log_event("UI_SESSION_CREATED", window=session.window_label)
        return response

    async def session_show(request: Request) -> Response:
        session = current(request)
        if session is None:
            return _error(401, "NO_SESSION")
        return JSONResponse({"window_label": session.window_label})

    async def session_delete(request: Request) -> Response:
        session = current(request)
        if session is None:
            return _error(401, "NO_SESSION")
        if not mutation_ok(request, session):
            return _error(403, "CSRF")
        sessions.logout(session.session_id)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    async def gate_begin(request: Request) -> Response:
        """§6.3 (1): ids and enums only; the answer is a nonce, never the summary or the hash (C-19)."""

        session = current(request)
        if session is None:
            return _error(401, "NO_SESSION")
        if not mutation_ok(request, session):
            return _error(403, "CSRF")
        body = await _json_body(request, frozenset({"action", "target_id", "params"}))
        if body is None or not isinstance(body["action"], str) or not isinstance(body["target_id"], str):
            return _error(400, "BAD_REQUEST")
        if gate is None:
            return _error(409, "GATE_UNAVAILABLE")
        try:
            nonce_id = gate.begin(session.session_id, session.window_label, body["action"], body["target_id"], body["params"])
        except GateError as exc:
            return _error(exc.status, exc.code)
        return JSONResponse({"nonce_id": nonce_id})

    async def not_found(request: Request, exc: Exception) -> Response:
        return _error(404, "NOT_FOUND")

    async def wrong_window(request: Request, exc: Exception) -> Response:
        return _error(403, "WRONG_WINDOW")

    async def too_large(request: Request, exc: Exception) -> Response:
        return _error(413, "BODY_TOO_LARGE")  # any route that reads past the limit (chunked bodies too)

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/", index, methods=["GET"]),
            Route("/bootstrap", index, methods=["GET"]),
            Route("/api/session", session_create, methods=["POST"]),
            Route("/api/session", session_show, methods=["GET"]),
            Route("/api/session", session_delete, methods=["DELETE"]),
            Route("/api/gate/begin", gate_begin, methods=["POST"]),
            *(library.routes(current, mutation_ok) if library is not None else []),
            *(canvas.routes(current, mutation_ok) if canvas is not None else []),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ],
        exception_handlers={404: not_found, _BodyTooLarge: too_large, WrongWindow: wrong_window},
    )
    return Boundary(app, port=port, allowed_origins=allowed_origins,
                    upload_limit=library.upload_limit if library is not None else MAX_BODY, relay=relay)


__all__ = ["COOKIE", "CSP", "create_app"]
