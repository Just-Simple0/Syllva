"""Static local Settings shell and versioned same-origin JSON API.

All routes live under one unpredictable per-launch path prefix. Blocking
config/status work runs in worker threads so the event loop (and the
replacement control channel) stays responsive; every mutation runs inside the
MutationBarrier so a handover waits for in-flight mutations to finish.
"""

from __future__ import annotations

import contextlib
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
    OAUTH_CALLBACK_SUFFIX,
    OAUTH_RESULT_SUFFIX,
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
# In-flight mutations may wait up to the 5 s config-lock deadline.
CLOSE_MUTATION_DRAIN_SECONDS = 8.0
_OAUTH_RESULT_PAGE = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"referrer\" content=\"no-referrer\"><title>Syllva Settings</title></head>"
    "<body><h1>Google sign-in finished</h1>"
    "<p>You can close this tab and return to the Syllva Settings window to continue.</p>"
    "</body></html>"
)


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
    credential_service: Any = None,
    canvas_service: Any = None,
    fake_mode: bool = False,
    google_oauth_service: Any = None,
) -> Any:
    expected_origin = f"http://{expected_host}"
    root = f"/{prefix}/"
    gate = barrier or MutationBarrier()
    oauth_result_url = root + OAUTH_RESULT_SUFFIX

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
        payload["fake_mode"] = fake_mode
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

    async def mutate(request: Request, work: Callable[[dict[str, Any]], Any], unavailable: str,
                     *, code_only: bool = False) -> Response:
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
            # Personal OAuth routes answer with the fixed code only (plan §2).
            if code_only or getattr(exc, "code_only", False):
                return _error(exc.code, exc.status_code)
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
        if group == "canvas_registry":
            return await canvas_registry_validate(request)
        return await mutate(
            request,
            lambda body: config_store.preview(group, body.get("values"), body.get("generation")),
            "VALIDATION_UNAVAILABLE",
        )

    async def apply_group(request: Request) -> Response:
        group = request.path_params["group"]
        if group == "canvas_registry":
            if canvas_service is None:
                return _error("FEATURE_DEFERRED", 403)
            def apply_selection(body: dict[str, Any]) -> Any:
                if set(body) != {"term_id", "course_ids", "generation", "candidate_hash"}:
                    return _invalid_body()
                return canvas_service.apply_selection(body["term_id"], body["course_ids"], body["generation"], body["candidate_hash"])
            return await mutate(request, apply_selection, "CANVAS_UNAVAILABLE")
        return await mutate(
            request,
            lambda body: config_store.apply(
                group, body.get("values"), body.get("generation"), journal,
                candidate_hash=body.get("candidate_hash"),
            ),
            "APPLY_UNAVAILABLE",
        )

    async def recover(request: Request) -> Response:
        action = request.path_params.get("action", "test")
        operation_id = request.path_params["operation_id"]
        if not _OPERATION_ID.fullmatch(operation_id):
            return _error("OPERATION_NOT_FOUND", 404)
        def repair(_body: dict[str, Any]) -> Any:
            record = journal.read(operation_id)
            if record["schema_version"] == 4 and credential_service is not None:
                return credential_service.recover(operation_id, action)
            return config_store.recover(journal, operation_id, action)
        return await mutate(
            request, repair,
            "RECOVERY_UNAVAILABLE",
        )

    async def credentials(request: Request) -> Response:
        if credential_service is None:
            return _error("FEATURE_DEFERRED", 403)
        if not same_origin_request(request.scope, expected_origin, require_origin=False):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        try:
            return JSONResponse(await run_in_threadpool(credential_service.cards))
        except Exception:  # noqa: BLE001 - metadata errors never echo secrets
            return _error("CREDENTIALS_UNAVAILABLE", 503)

    async def credential_action(request: Request) -> Response:
        from .credential_roles import ROLES
        role = ROLES.get(request.path_params["role"])
        action = request.path_params.get("action", "test")
        if role is None or action not in {"set", "replace", "forget", "detach"}:
            return _error("NOT_FOUND", 404)
        if credential_service is None:
            return _error("FEATURE_DEFERRED", 403)
        if role.provider == "google" and action in {"set", "replace"}:
            # The upload itself is JSON; generation is outside the secret body.
            denied = _authorize_mutation(request, security, expected_origin)
            if denied is not None:
                return denied
            if not security.record_explicit_activity(request_cookie(request.scope)):
                return _error("SESSION_EXPIRED", 401)
            generation = single_header(request.scope, b"x-uls-generation")
            if generation is None:
                return _error("INVALID_GENERATION", 400)
            value = await request.body()
            if len(value) > 65536:
                return _error("REQUEST_TOO_LARGE", 413)
            try:
                _unique_json(value)
                async with gate.mutation():
                    result = await run_in_threadpool(credential_service.save, role, value, generation.decode("ascii"), replace=action == "replace")
                return JSONResponse(result)
            except SettingsServiceError as exc:
                return _service_error(exc)
            except JournalError as exc:
                return _error(exc.code, 409)
            except BarrierClosed:
                return _error("SESSION_REPLACED", 401)
            except (ValueError, UnicodeError):
                return _error("INVALID_REQUEST", 400)
            except Exception:  # noqa: BLE001 - secret upload failure
                return _error("CREDENTIAL_SAVE_UNAVAILABLE", 503)
        def work(body: dict[str, Any]) -> Any:
            keys = {"generation", "secret"} if action in {"set", "replace"} else {"generation", "confirm_role"}
            if set(body) != keys:
                raise SettingsServiceError("INVALID_REQUEST", "Choose a listed credential action.")
            if action in {"set", "replace"}:
                if not isinstance(body["secret"], str):
                    raise SettingsServiceError("INVALID_REQUEST", "Enter a credential.")
                return credential_service.save(role, body["secret"].encode(), body["generation"], replace=action == "replace")
            if body["confirm_role"] != role.slug:
                raise SettingsServiceError("CONFIRMATION_REQUIRED", "Confirm the connection shown in this dialog.")
            return credential_service.forget(role, body["generation"], detach=action == "detach")
        return await mutate(request, work, "CREDENTIAL_SAVE_UNAVAILABLE")

    async def connection_test(request: Request) -> Response:
        from .credential_roles import ROLES
        role = ROLES.get(f"{request.path_params['provider']}-{request.path_params['purpose']}")
        if role is None or credential_service is None:
            return _error("NOT_FOUND", 404)
        # No credential read happens before authorization and barrier entry;
        # CredentialService.test marks OAuth-typed failures from the very
        # snapshot it checked, so the response shape is decided atomically.
        return await mutate(request, lambda body: credential_service.test(role) if not body else _invalid_body(),
                            "CHECK_UNAVAILABLE")

    async def canvas_snapshot(request: Request) -> Response:
        if canvas_service is None:
            return _error("FEATURE_DEFERRED", 403)
        if not same_origin_request(request.scope, expected_origin, require_origin=False):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        try:
            return JSONResponse(await run_in_threadpool(canvas_service.snapshot))
        except SettingsServiceError as exc:
            return _service_error(exc)

    async def canvas_action(request: Request) -> Response:
        if canvas_service is None:
            return _error("FEATURE_DEFERRED", 403)
        action = request.path_params.get("action", "test")
        signatures = {"connect": ("origin", "secret", "generation"), "replace": ("secret", "generation"),
                      "forget": ("confirm_profile_id", "generation"), "renew": ("generation",),
                      "disable_sync": ("generation",), "discover": (), "test": ()}
        if action not in signatures:
            return _error("NOT_FOUND", 404)
        def work(body: dict[str, Any]) -> Any:
            names = signatures[action]
            if set(body) != set(names):
                return _invalid_body()
            return getattr(canvas_service, action)(*(body[name] for name in names))
        return await mutate(request, work, "CANVAS_UNAVAILABLE")

    async def canvas_registry_validate(request: Request) -> Response:
        if canvas_service is None:
            return _error("FEATURE_DEFERRED", 403)
        def work(body: dict[str, Any]) -> Any:
            if set(body) != {"term_id", "course_ids", "generation"}:
                return _invalid_body()
            # CanvasService performs bounded authoritative re-reads and returns
            # a reviewed candidate; apply goes through the config CAS below.
            return canvas_service.save_selection(body["term_id"], body["course_ids"], body["generation"])
        return await mutate(request, work, "CANVAS_UNAVAILABLE")

    async def google_oauth_action(request: Request) -> Response:
        if google_oauth_service is None:
            return _error("FEATURE_DEFERRED", 403)
        purpose = request.path_params["purpose"]
        action = request.path_params["action"]
        if action not in {"begin", "cancel", "commit"}:
            return _error("NOT_FOUND", 404)
        return await mutate(request, lambda body: getattr(google_oauth_service, action)(purpose, body),
                            "GOOGLE_OAUTH_UNAVAILABLE", code_only=True)

    async def google_oauth_status(request: Request) -> Response:
        if google_oauth_service is None:
            return _error("FEATURE_DEFERRED", 403)
        if not same_origin_request(request.scope, expected_origin, require_origin=False):
            return _error("SAME_ORIGIN_REQUIRED", 403)
        try:
            return JSONResponse(await run_in_threadpool(
                google_oauth_service.status, request.path_params["purpose"], request.path_params["flow_id"]))
        except SettingsServiceError as exc:
            return _error(exc.code, exc.status_code)
        except Exception:  # noqa: BLE001 - fixed error code; never echo internals
            return _error("GOOGLE_OAUTH_UNAVAILABLE", 503)

    async def google_oauth_callback(request: Request) -> Response:
        # Provider redirect. Every outcome lands on the fixed data-free result
        # page; the flow records its own fixed status for the live session.
        if google_oauth_service is not None:
            raw_query = request.scope.get("query_string", b"").decode("latin-1")
            pairs = parse_qsl(raw_query, keep_blank_values=True)
            query: dict[str, str] = {}
            duplicate = False
            for key, value in pairs:
                if key in query:
                    duplicate = True
                query[key] = value
            if not duplicate:
                # Never echo provider or flow internals; the flow records its own code.
                with contextlib.suppress(Exception):
                    await run_in_threadpool(google_oauth_service.callback, query)
        return RedirectResponse(url=oauth_result_url, status_code=303)

    async def google_oauth_result(_request: Request) -> Response:
        return Response(_OAUTH_RESULT_PAGE, media_type="text/html; charset=utf-8")

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
        # Same order as replacement/idle: block OAuth, close the barrier,
        # drain any committing save, then end the session.
        if google_oauth_service is not None:
            google_oauth_service.invalidate_precommit()
        gate.close()
        while not await gate.wait_idle(CLOSE_MUTATION_DRAIN_SECONDS):
            pass
        if not security.close(request_cookie(request.scope)):
            # The idle limit passed during the drain: the session still ends here.
            security.expire()
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
    routes.extend([
        Route(f"{root}api/v1/credentials", credentials, methods=["GET"]),
        Route(f"{root}api/v1/credentials/{{role:str}}/{{action:str}}", credential_action, methods=["POST"]),
        Route(f"{root}api/v1/connections/{{provider:str}}/{{purpose:str}}/test", connection_test, methods=["POST"]),
        Route(f"{root}api/v1/canvas", canvas_snapshot, methods=["GET"]),
        Route(f"{root}api/v1/canvas/{{action:str}}", canvas_action, methods=["POST"]),
        Route(f"{root}api/v1/connections/canvas/test", canvas_action, methods=["POST"], name="canvas_test"),
        Route(f"{root}api/v1/settings/canvas_registry/validate", canvas_registry_validate, methods=["POST"]),
        Route(f"{root}api/v1/google-oauth/{{purpose:str}}/{{action:str}}", google_oauth_action, methods=["POST"]),
        Route(f"{root}api/v1/google-oauth/{{purpose:str}}/{{flow_id:str}}", google_oauth_status, methods=["GET"]),
        Route(root + OAUTH_CALLBACK_SUFFIX, google_oauth_callback, methods=["GET"]),
        Route(oauth_result_url, google_oauth_result, methods=["GET"]),
    ])
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
        value = _unique_json(await request.body())
    except (ValueError, UnicodeDecodeError):
        return _error("INVALID_REQUEST", 400)
    if not isinstance(value, dict):
        return _error("INVALID_REQUEST", 400)
    return value


def _unique_json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def _invalid_body() -> Any:
    raise SettingsServiceError("INVALID_REQUEST", "Choose a listed settings action.")




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
