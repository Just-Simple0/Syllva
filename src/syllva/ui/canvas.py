"""Canvas settings routes of the v2 UI (spec §10.3 `/api/settings/...`, `/api/canvas/...`; R3 plan §6.1).

* The token arrives once, in ``PUT /api/settings/canvas/token`` (body bounded to 4 KiB by the
  boundary), is verified against the Canvas origin, and goes to the OS keychain. No route returns it.
* Errors are fixed codes only — never an exception message (a backend message may carry the token).
* Every route needs a UI session; changes also need the CSRF header.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from syllva.canvas.client import CanvasClient
from syllva.canvas.credential import CanvasCredentials, CanvasToken, CredentialError
from syllva.canvas.http import CanvasCheckError
from syllva.core.logging import log_event

Current = Callable[[Request], Any]
TOKEN_PATH = "/api/settings/canvas/token"
TOKEN_BODY_LIMIT = 4096
BAD_INPUT = {"TOKEN_INVALID", "FILE_HOSTS_INVALID", "COURSE_UNKNOWN", "DESTINATION_NOT_ALLOWED", "BAD_REQUEST"}


def _error(status: int, code: str) -> Response:
    return JSONResponse({"error": code}, status_code=status)


def _status_for(code: str) -> int:
    if code in BAD_INPUT:
        return 400
    if code == "RATE_LIMITED":
        return 429
    return 409


async def _object(request: Request, keys: set[str]) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) and set(value) == keys else None


class Sync(Protocol):
    def start(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...


class CanvasApi:
    def __init__(self, credentials: CanvasCredentials, client: CanvasClient, sync: Sync | None = None) -> None:
        self.credentials = credentials
        self.client = client
        self.sync = sync

    def routes(self, current: Current, mutation_ok: Callable[[Request, Any], bool]) -> list[Route]:
        def guarded(handler: Callable[..., Any], *, mutate: bool) -> Callable[[Request], Any]:
            async def endpoint(request: Request) -> Response:
                session = current(request)
                if session is None:
                    return _error(401, "NO_SESSION")
                if mutate and not mutation_ok(request, session):
                    return _error(403, "CSRF")
                try:
                    result: Response = await handler(request)
                    return result
                except (CredentialError, CanvasCheckError) as exc:
                    log_event("UI_CANVAS_REFUSED", reason=exc.code)
                    return _error(_status_for(exc.code), exc.code)

            return endpoint

        return [
            Route("/api/settings/canvas", guarded(self.show, mutate=False), methods=["GET"]),
            Route(TOKEN_PATH, guarded(self.set_token, mutate=True), methods=["PUT"]),
            Route("/api/settings/canvas", guarded(self.forget, mutate=True), methods=["DELETE"]),
            Route("/api/settings/canvas/file-hosts", guarded(self.file_hosts, mutate=True), methods=["PUT"]),
            Route("/api/canvas/courses", guarded(self.courses, mutate=False), methods=["GET"]),
            Route("/api/canvas/courses/{canvas_course_id}", guarded(self.link, mutate=True), methods=["PUT"]),
            Route("/api/canvas/sync", guarded(self.start_sync, mutate=True), methods=["POST"]),
            Route("/api/canvas/status", guarded(self.sync_status, mutate=False), methods=["GET"]),
            Route("/api/activities", guarded(self.activities, mutate=False), methods=["GET"]),
        ]

    async def show(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(self.credentials.status))

    async def set_token(self, request: Request) -> Response:
        body = await _object(request, {"origin", "token"})
        if body is None or not isinstance(body["origin"], str) or not isinstance(body["token"], str):
            return _error(400, "BAD_REQUEST")
        origin, token = body["origin"], CanvasToken(body["token"])
        status = await run_in_threadpool(lambda: self.credentials.connect(origin, token))
        return JSONResponse(status)

    async def forget(self, request: Request) -> Response:
        await run_in_threadpool(self.credentials.forget)
        return JSONResponse({"ok": True})

    async def file_hosts(self, request: Request) -> Response:
        body = await _object(request, {"hosts"})
        if body is None:
            return _error(400, "BAD_REQUEST")
        return JSONResponse({"file_hosts": await run_in_threadpool(lambda: self.credentials.set_file_hosts(body["hosts"]))})

    def _discover(self) -> dict[str, Any]:
        status = self.credentials.status()
        token = self.credentials.token()
        found = self.client.discover(self.credentials.origin(), token.reveal_token(), str(status["profile_id"]))
        linked = {c["canvas_course_id"]: c["course_key"] for c in status.get("courses", [])}
        return {"courses": [
            {"canvas_course_id": c["course_id"], "name": c["name"], "code": c["code"], "term_name": c["term_name"],
             "linked_course_key": linked.get(c["course_id"])}
            for c in found.get("courses", [])
        ]}

    async def courses(self, request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(self._discover))

    async def link(self, request: Request) -> Response:
        canvas_course_id = request.path_params["canvas_course_id"]
        body = await _object(request, {"course_key"})
        if (body is None or not re.fullmatch(r"[0-9]{1,64}", canvas_course_id)
                or not (body["course_key"] is None or isinstance(body["course_key"], str))):
            return _error(400, "BAD_REQUEST")
        course_key = body["course_key"]

        def work() -> dict[str, Any]:
            meta: dict[str, str] = {}
            if course_key is not None:  # the course must really be the user's (re-read from Canvas)
                (meta,) = self.client.reread(self.credentials.origin(), self.credentials.token().reveal_token(),
                                             [canvas_course_id])
            self.credentials.link_course(canvas_course_id, course_key, meta)
            return self.credentials.status()

        return JSONResponse(await run_in_threadpool(work))


    # -- sync and activities (R3-2) --------------------------------------------------------

    async def start_sync(self, request: Request) -> Response:
        if self.sync is None:
            return _error(409, "SYNC_UNAVAILABLE")
        status = await run_in_threadpool(self.credentials.status)
        if not status.get("connected"):
            return _error(409, "TOKEN_NOT_SET")
        if not await run_in_threadpool(self.sync.start):
            return _error(409, "SYNC_RUNNING")
        return JSONResponse({"started": True}, status_code=202)

    async def sync_status(self, request: Request) -> Response:
        if self.sync is None:
            return JSONResponse({"running": False, "error": None, "courses": []})
        return JSONResponse(await run_in_threadpool(self.sync.status))

    def _activities(self) -> list[dict[str, Any]]:
        rows = self.credentials.query(
            "SELECT a.activity_id, a.title, a.due_at, a.submission_status, a.origin, c.course_key, c.name AS course_name, "
            "COALESCE(i.active, 1) AS canvas_active FROM activities a JOIN courses c ON c.id = a.course_id "
            "LEFT JOIN canvas_courses k ON k.course_id = a.course_id "
            "LEFT JOIN canvas_items i ON i.kind = 'ASSIGNMENT' AND i.resource_id = a.canvas_assignment_id "
            "AND i.canvas_course_id = k.canvas_course_id "
            "ORDER BY a.due_at IS NULL, a.due_at, a.activity_id")
        return [{"activity_id": r["activity_id"], "title": r["title"], "due_at": r["due_at"],
                 "submission_status": r["submission_status"], "origin": r["origin"], "course_key": r["course_key"],
                 "course_name": r["course_name"], "canvas_active": bool(r["canvas_active"])} for r in rows]

    async def activities(self, request: Request) -> Response:
        return JSONResponse({"activities": await run_in_threadpool(self._activities)})


__all__ = ["TOKEN_BODY_LIMIT", "TOKEN_PATH", "CanvasApi"]
