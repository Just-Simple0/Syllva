"""Library and intake routes of the v2 UI (implementation spec §10.3, §11; errata E28).

All routes need a UI session; changes also need the CSRF header (and, from the
boundary, the app Origin). Uploads are the raw request body with the file name
in ``X-Syllva-Filename`` (percent-encoded UTF-8) — the Shell relay buffers
bodies anyway (plan condition B), so multipart adds nothing (E28). Evidence is
read through ONE ``RetrievalService`` whose capabilities are bound to the UI
session id (``caller_scope``), so one session cannot read another's context.
No route here writes a human-only column (C-04): only the gate does.
"""

from __future__ import annotations

import tempfile
import threading
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from syllva.core.logging import log_event
from syllva.domain.errors import SyllvaError
from syllva.ephemeral.memory import MemoryEphemeralStore
from syllva.ingestion.local import IngestError
from syllva.intake.v2 import Candidate, IntakeError, LocalIntake
from syllva.library.blobs import BlobError, BlobStore
from syllva.retrieval.service import EntityUnknown, LocatorNotAuthorized, RetrievalService
from syllva.state.v2.database import Database
from syllva.state.v2.ownership import Actor, Owner

CLASSIFY_KEYS = {"course_key", "kind", "role", "session_id", "new_session_held_on"}
ROLES = {"PRIMARY", "SUPPORTING", "REFERENCE"}
MAX_FILENAME = 200


def clean_filename(raw: str | None) -> str | None:
    """The display name of an upload: basename only, NFC, no control characters, bounded (review of PR #51)."""

    if not raw:
        return None
    name = unicodedata.normalize("NFC", unquote(raw)).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", "..") or len(name) > MAX_FILENAME:
        return None
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        return None
    return name
Current = Callable[[Request], Any]


def _error(status: int, code: str) -> Response:
    return JSONResponse({"error": code}, status_code=status)


def _candidate(c: Candidate) -> dict[str, Any]:
    suggestion = c.suggestion or {}
    return {
        "candidate_id": c.candidate_id, "state": c.state, "name": c.display_name, "reason": c.reason_code,
        "material_id": c.material_id, "duplicate_of": c.duplicate_of,
        "blockers": suggestion.get("blockers"), "suggested_kind": suggestion.get("suggested_kind"),
        "suggested_course_key": suggestion.get("suggested_course_key"),
    }


class LibraryApi:
    def __init__(self, db: Database, blobs: BlobStore, intake: LocalIntake, *, kick: Callable[[], Any], upload_limit: int) -> None:
        self.db = db
        self.blobs = blobs
        self.intake = intake
        self.kick = kick
        self.upload_limit = upload_limit
        self._service = RetrievalService(db.reader(), blobs, MemoryEphemeralStore())
        self._service_lock = threading.Lock()

    def routes(self, current: Current, mutation_ok: Callable[[Request, Any], bool]) -> list[Route]:
        def guarded(handler: Callable[..., Any], *, mutate: bool) -> Callable[[Request], Any]:
            async def endpoint(request: Request) -> Response:
                session = current(request)
                if session is None:
                    return _error(401, "NO_SESSION")
                if mutate and not mutation_ok(request, session):
                    return _error(403, "CSRF")
                try:
                    result: Response = await handler(request, session)
                    return result
                except IntakeError as exc:
                    return JSONResponse({"error": exc.code, "message": str(exc)[:200]}, status_code=409)
                except EntityUnknown:
                    return _error(404, "NOT_FOUND")
                except LocatorNotAuthorized:
                    return _error(403, "LOCATOR_NOT_AUTHORIZED")
                except (IngestError, BlobError) as exc:
                    log_event("UI_STORE_ERROR", error=type(exc).__name__)
                    return _error(409, "STORE_REFUSED")
                except SyllvaError as exc:
                    log_event("UI_ERROR", error=exc.code)
                    return _error(409, exc.code)

            return endpoint

        return [
            Route("/api/library/items", guarded(self.items, mutate=False), methods=["GET"]),
            Route("/api/intake/candidates", guarded(self.candidates, mutate=False), methods=["GET"]),
            Route("/api/library/upload", guarded(self.upload, mutate=True), methods=["POST"]),
            Route("/api/library/{key}/classify", guarded(self.classify, mutate=True), methods=["POST"]),
            Route("/api/library/{key}/dismiss", guarded(self.dismiss, mutate=True), methods=["POST"]),
            Route("/api/library/{key}/replace", guarded(self.replace, mutate=True), methods=["POST"]),
            Route("/api/library/{key}", guarded(self.delete, mutate=True), methods=["DELETE"]),
            Route("/api/library/{key}/context", guarded(self.context, mutate=False), methods=["GET"]),
            Route("/api/library/{key}/chunk", guarded(self.chunk, mutate=False), methods=["GET"]),
        ]

    # -- reads -----------------------------------------------------------------------------

    async def items(self, request: Request, session: Any) -> Response:
        rows = await run_in_threadpool(self._items)
        return JSONResponse({"items": rows})

    def _items(self) -> list[dict[str, Any]]:
        rows = self.db.reader().fetch_all(
            "SELECT m.material_id, f.display_name, c.course_key, c.name AS course_name, m.kind, m.processing_status, "
            "COALESCE(u.verified, 0) AS verified, s.session_id FROM materials m JOIN courses c ON c.id = m.course_id "
            "JOIN source_files f ON f.id = m.source_file_id LEFT JOIN sessions s ON s.id = m.session_id "
            "LEFT JOIN material_usage u ON u.material_id = m.id WHERE f.deleted_at IS NULL ORDER BY m.material_id"
        )
        return [
            {"material_id": r["material_id"], "title": r["display_name"], "course_key": r["course_key"],
             "course_name": r["course_name"], "kind": r["kind"], "processing_status": r["processing_status"],
             "verified": bool(r["verified"]), "session_id": r["session_id"]}
            for r in rows
        ]

    async def candidates(self, request: Request, session: Any) -> Response:
        return JSONResponse({"candidates": await run_in_threadpool(self._candidates)})

    def _candidates(self) -> list[dict[str, Any]]:
        rows = self.db.reader().fetch_all(
            "SELECT c.candidate_id FROM intake_candidates c LEFT JOIN source_versions v ON v.id = c.source_version_id "
            "LEFT JOIN source_files f ON f.id = v.source_file_id WHERE c.state IN ('RECEIVED','CLASSIFYING','NEEDS_REVIEW',"
            "'DUPLICATE','UNSUPPORTED','FAILED','PARTIAL') AND f.deleted_at IS NULL ORDER BY c.id"
        )
        return [_candidate(self.intake.get(str(r["candidate_id"]))) for r in rows]

    async def context(self, request: Request, session: Any) -> Response:
        query = request.query_params.get("query") or None

        def build() -> Any:
            with self._service_lock:
                return self._service.get_material_context(request.path_params["key"], caller_scope=session.session_id, query=query)

        package = await run_in_threadpool(build)
        return JSONResponse({
            "material_id": request.path_params["key"], "context_id": package.context_id, "warnings": list(package.warnings),
            "evidence": [{"locator": str(i.locator), "part": i.part, "parts": i.parts, "text": i.content} for i in package.sources],
        })

    async def chunk(self, request: Request, session: Any) -> Response:
        params = request.query_params
        try:
            part = int(params.get("part", "1"))
        except ValueError:
            return _error(400, "BAD_REQUEST")
        context_id, locator = params.get("context_id", ""), params.get("locator", "")

        def read() -> Any:
            with self._service_lock:
                return self._service.get_source_chunk(context_id, locator, caller_scope=session.session_id, part=part)

        item = await run_in_threadpool(read)
        return JSONResponse({"locator": str(item.locator), "part": item.part, "parts": item.parts, "text": item.content})

    # -- changes ---------------------------------------------------------------------------

    async def _spool(self, request: Request) -> tuple[Any, str] | Response:
        name = clean_filename(request.headers.get("x-syllva-filename"))
        if name is None:
            return _error(400, "BAD_FILENAME")
        # spills into the library's own tmp/ (§11.1), never the OS temp directory
        self.blobs.tmp_dir.mkdir(parents=True, exist_ok=True)
        spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=str(self.blobs.tmp_dir))  # noqa: SIM115 - closed by the worker
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > self.upload_limit:
                spool.close()
                return _error(413, "BODY_TOO_LARGE")
            spool.write(chunk)
        spool.seek(0)
        return spool, name

    async def upload(self, request: Request, session: Any) -> Response:
        spooled = await self._spool(request)
        if isinstance(spooled, Response):
            return spooled
        spool, name = spooled
        course = request.query_params.get("course") or None

        def work() -> Candidate:
            with spool:
                candidate = self.intake.receive(spool, display_name=name, course_hint=course)
            if candidate.state == "RECEIVED":
                candidate = self.intake.classify(candidate.candidate_id)
            if candidate.state == "AUTO_CONFIRMED":
                self.kick()
                candidate = self.intake.get(candidate.candidate_id)
            return candidate

        return JSONResponse(_candidate(await run_in_threadpool(work)))

    async def classify(self, request: Request, session: Any) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "BAD_REQUEST")
        if (not isinstance(body, dict) or not {"course_key", "kind"} <= set(body) or not set(body) <= CLASSIFY_KEYS
                or not all(isinstance(v, str) for v in body.values()) or body.get("role", "PRIMARY") not in ROLES):
            return _error(400, "BAD_REQUEST")
        key = request.path_params["key"]

        def work() -> Candidate:
            self.intake.confirm(
                key, course_key=body["course_key"], kind=body["kind"], role=body.get("role", "PRIMARY"),
                session_id=body.get("session_id"), new_session_held_on=body.get("new_session_held_on"),
            )
            self.kick()
            return self.intake.get(key)

        return JSONResponse(_candidate(await run_in_threadpool(work)))

    async def dismiss(self, request: Request, session: Any) -> Response:
        key = request.path_params["key"]
        return JSONResponse(_candidate(await run_in_threadpool(lambda: self.intake.dismiss(key))))

    async def replace(self, request: Request, session: Any) -> Response:
        spooled = await self._spool(request)
        if isinstance(spooled, Response):
            return spooled
        spool, name = spooled
        key = request.path_params["key"]

        def work() -> Candidate:
            with spool:
                candidate = self.intake.replace(key, spool, display_name=name)
            self.kick()
            return self.intake.get(candidate.candidate_id)

        return JSONResponse(_candidate(await run_in_threadpool(work)))

    async def delete(self, request: Request, session: Any) -> Response:
        key = request.path_params["key"]

        def work() -> bool:
            writer = self.db.writer()
            with writer.transaction():
                row = writer.query_one(
                    "SELECT f.id FROM materials m JOIN source_files f ON f.id = m.source_file_id "
                    "WHERE m.material_id = ? AND f.deleted_at IS NULL", (key,))
                if row is None:
                    return False
                # logical delete (§13.2): the source file is hidden; blobs are collected later
                writer.update(Owner.SOURCE, Actor.INGESTER, "source_files", {"id": int(row["id"])},
                              {"deleted_at": datetime.now(UTC).isoformat(timespec="seconds")})
            return True

        if not await run_in_threadpool(work):
            return _error(404, "NOT_FOUND")
        log_event("UI_MATERIAL_DELETED", material=key)
        return JSONResponse({"ok": True})


__all__ = ["LibraryApi"]
