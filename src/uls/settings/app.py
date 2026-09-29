"""Static local Settings shell and versioned same-origin JSON API.

All routes live under one unpredictable per-launch path prefix. Blocking
config/status work runs in worker threads so the event loop (and the
replacement control channel) stays responsive; every mutation runs inside the
MutationBarrier so a handover waits for in-flight mutations to finish.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from uls.config.mutation import ConfigLockTimeout

from .config_service import ConfigStore, SettingsServiceError
from .journal import JournalError, JournalStore
from .security import (
    SESSION_COOKIE,
    BarrierClosed,
    MutationBarrier,
    SecurityBoundary,
    SessionSecurity,
    request_cookie,
    same_origin_request,
    single_header,
)
from .status import settings_overview

_STATIC = Path(__file__).with_name("static")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")


def create_settings_app(
    config_store: ConfigStore,
    journal: JournalStore,
    security: SessionSecurity,
    expected_host: str,
    *,
    prefix: str,
    barrier: MutationBarrier | None = None,
    on_close: Callable[[], None] | None = None,
    replaced_previous: bool = False,
) -> Any:
    expected_origin = f"http://{expected_host}"
    root = f"/{prefix}/"
    gate = barrier or MutationBarrier()

    async def home(request: Request) -> Response:
        raw_query = request.scope.get("query_string", b"").decode("latin-1")
        if raw_query:
            pairs = parse_qsl(raw_query, keep_blank_values=True)
            if len(pairs) != 1 or pairs[0][0] != "bootstrap":
                return _error("BOOTSTRAP_INVALID", 401)
            if (
                single_header(request.scope, b"sec-fetch-mode") != b"navigate"
                or single_header(request.scope, b"sec-fetch-dest") != b"document"
                or single_header(request.scope, b"sec-fetch-site") not in {b"none", b"same-origin"}
            ):
                return _error("BOOTSTRAP_INVALID", 401)
            handle = security.consume_bootstrap(pairs[0][1])
            if handle is None:
                return _error("BOOTSTRAP_INVALID", 401)
            response = RedirectResponse(url=root, status_code=303)
            # Session cookie scoped to this launch's prefix, no Max-Age/Expires.
            response.set_cookie(SESSION_COOKIE, handle, path=root, httponly=True, samesite="strict")
            return response
        return FileResponse(_STATIC / "index.html", media_type="text/html; charset=utf-8")

    async def app_js(_request: Request) -> Response:
        return FileResponse(_STATIC / "app.js", media_type="text/javascript; charset=utf-8")

    async def app_css(_request: Request) -> Response:
        return FileResponse(_STATIC / "styles.css", media_type="text/css; charset=utf-8")

    async def csrf(request: Request) -> Response:
        if not _csrf_fetch_allowed(request, expected_origin):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        value = security.csrf_for(request_cookie(request.scope))
        if value is None:
            return _error("SESSION_EXPIRED", 401)
        return JSONResponse({"csrf_token": value, "idle_seconds": int(security.idle_seconds)})

    async def overview(request: Request) -> Response:
        if not same_origin_request(request.scope, expected_origin, require_origin=False):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        try:
            payload = await run_in_threadpool(settings_overview, config_store, journal)
        except Exception:  # noqa: BLE001 - fixed error code; never echo internals
            return _error("OVERVIEW_UNAVAILABLE", 503)
        payload["session_notice"] = "replaced_previous" if replaced_previous else None
        return JSONResponse(payload)

    async def get_group(request: Request) -> Response:
        if not same_origin_request(request.scope, expected_origin, require_origin=False):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        try:
            return JSONResponse(
                await run_in_threadpool(config_store.group_snapshot, request.path_params["group"]),
            )
        except SettingsServiceError as exc:
            return _service_error(exc)
        except Exception:  # noqa: BLE001 - fixed error code; never echo internals
            return _error("SETTINGS_UNAVAILABLE", 503)

    async def mutate(request: Request, work: Callable[[dict[str, Any]], Any], unavailable: str) -> Response:
        denied = _authorize_mutation(request, security, expected_origin)
        if denied is not None:
            return denied
        if not security.record_explicit_activity(request_cookie(request.scope)):
            return _error("SESSION_EXPIRED", 401)
        body = await _json_object(request)
        if isinstance(body, Response):
            return body
        try:
            async with gate.mutation():
                result = await run_in_threadpool(work, body)
        except BarrierClosed:
            return _error("SESSION_REPLACED", 401)
        except SettingsServiceError as exc:
            return _service_error(exc)
        except JournalError as exc:
            return _error(exc.code, 409)
        except (ConfigLockTimeout, TimeoutError):
            return _error("CONFIGURATION_BUSY", 409)
        except Exception:  # noqa: BLE001 - fixed error code; never echo internals
            return _error(unavailable, 503)
        return JSONResponse(result)

    async def validate_group(request: Request) -> Response:
        group = request.path_params["group"]
        return await mutate(
            request,
            lambda body: config_store.preview(group, body.get("values"), body.get("generation")),
            "VALIDATION_UNAVAILABLE",
        )

    async def apply_group(request: Request) -> Response:
        group = request.path_params["group"]
        return await mutate(
            request,
            lambda body: config_store.apply(
                group, body.get("values"), body.get("generation"), journal,
                candidate_hash=body.get("candidate_hash"),
            ),
            "APPLY_UNAVAILABLE",
        )

    async def recover(request: Request) -> Response:
        action = request.path_params["action"]
        operation_id = request.path_params["operation_id"]
        if not _OPERATION_ID.fullmatch(operation_id):
            return _error("OPERATION_NOT_FOUND", 404)
        return await mutate(
            request, lambda _body: config_store.recover(journal, operation_id, action),
            "RECOVERY_UNAVAILABLE",
        )

    async def keepalive(request: Request) -> Response:
        denied = _authorize_mutation(request, security, expected_origin)
        if denied is not None:
            return denied
        if not security.record_explicit_activity(request_cookie(request.scope)):
            return _error("SESSION_EXPIRED", 401)
        return JSONResponse({"status": "active"})

    async def close_session(request: Request) -> Response:
        denied = _authorize_mutation(request, security, expected_origin)
        if denied is not None:
            return denied
        security.close(request_cookie(request.scope))
        response = JSONResponse({"status": "closed"})
        response.delete_cookie(SESSION_COOKIE, path=root, httponly=True, samesite="strict")
        if on_close is not None:
            on_close()
        return response

    routes = [
        Route(root, home, methods=["GET"]),
        Route(f"{root}static/app.js", app_js, methods=["GET"]),
        Route(f"{root}static/styles.css", app_css, methods=["GET"]),
        Route(f"{root}api/v1/session/csrf", csrf, methods=["GET"]),
        Route(f"{root}api/v1/overview", overview, methods=["GET"]),
        Route(f"{root}api/v1/settings/{{group:str}}", get_group, methods=["GET"]),
        Route(f"{root}api/v1/settings/{{group:str}}/validate", validate_group, methods=["POST"]),
        Route(f"{root}api/v1/settings/{{group:str}}/apply", apply_group, methods=["POST"]),
        Route(f"{root}api/v1/session/keepalive", keepalive, methods=["POST"]),
        Route(f"{root}api/v1/session/close", close_session, methods=["POST"]),
        Route(f"{root}api/v1/recovery/{{operation_id:str}}/{{action:str}}", recover, methods=["POST"]),
    ]
    application = Starlette(debug=False, routes=routes)
    return SecurityBoundary(application, security, expected_host, prefix)


def _authorize_mutation(request: Request, security: SessionSecurity, expected_origin: str) -> Response | None:
    handle = request_cookie(request.scope)
    if security.is_replaced:
        return _error("SESSION_REPLACED", 401)
    if security.session_code(handle) is not None:
        return _error("SESSION_EXPIRED", 401)
    if not same_origin_request(request.scope, expected_origin, require_origin=True):
        return _error("SAME_ORIGIN_REQUIRED", 403)
    csrf_headers = [value for key, value in request.scope.get("headers", []) if key.lower() == b"x-uls-csrf"]
    if len(csrf_headers) != 1:
        return _error("CSRF_REJECTED", 403)
    try:
        csrf_value = csrf_headers[0].decode("ascii")
    except UnicodeDecodeError:
        return _error("CSRF_REJECTED", 403)
    if not security.validate_csrf(handle, csrf_value):
        return _error("CSRF_REJECTED", 403)
    return None


async def _json_object(request: Request) -> dict[str, Any] | Response:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return _error("JSON_REQUIRED", 415)
    try:
        value = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error("INVALID_REQUEST", 400)
    if not isinstance(value, dict):
        return _error("INVALID_REQUEST", 400)
    return value


def _csrf_fetch_allowed(request: Request, expected_origin: str) -> bool:
    if single_header(request.scope, b"sec-fetch-site") != b"same-origin":
        return False
    if single_header(request.scope, b"sec-fetch-mode") not in {b"cors", b"same-origin"}:
        return False
    return same_origin_request(request.scope, expected_origin, require_origin=False)


def _error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status)


def _service_error(exc: SettingsServiceError) -> JSONResponse:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.fields:
        error["fields"] = exc.fields
    return JSONResponse({"error": error}, status_code=exc.status_code)


__all__ = ["create_settings_app"]
