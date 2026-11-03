"""The Canvas sync: collect each linked course and record it fail-closed (R3 plan §6.2, §6.3).

Order per run: read the token (keychain) → the principal check (``users/self`` must still be
the connected user) → per linked course: commit a ``canvas_collections`` row ``RUNNING``
(the barrier; R3-3 also closes the recording calendar here) → collect → **only if the
collection is COMPLETE**, one transaction writes ``canvas_items`` and the Canvas activities
and finishes the row. INCOMPLETE/FAILED finish the row and write nothing else. A row still
``RUNNING`` at start-up is ``FAILED(INTERRUPTED)``.

The sync writes as ``CANVAS_COLLECTOR`` (ownership.ACTOR_TABLES/ACTOR_COLUMNS): Canvas-origin
activities' ``title/due_at/canvas_updated_at/updated_at`` only — never ``submission_status``,
never a delete. Canvas UTC timestamps for due dates are stored in local time.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol

from syllva.canvas import http
from syllva.canvas.collect import KINDS, Fetch, Incomplete, collect_course
from syllva.canvas.credential import CanvasToken, CredentialError
from syllva.canvas.http import CanvasCheckError
from syllva.core.logging import log_event
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

AUTO_SYNC_AFTER_S = 6 * 3600
MAX_ACTIVITY_SEQUENCE = 99
_CODE = re.compile(r"[A-Z][A-Z0-9]*")
COLLECTOR = Actor.CANVAS_COLLECTOR


class Credentials(Protocol):
    def token(self) -> CanvasToken: ...

    def origin(self) -> str: ...


OpenFetch = Callable[[str, str], AbstractContextManager[Fetch]]
Verify = Callable[[str, str], Mapping[str, str]]


@contextmanager
def live_fetch(origin: str, token: str) -> Iterator[Fetch]:
    """One pinned connection per course (bounded by ``http.OBSERVATION_MAX_REQUESTS``)."""

    with http.open_canvas_connection(origin) as connection:
        yield lambda url, deadline: connection.observe(url, token, deadline=deadline)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CanvasSync:
    def __init__(self, writer: StateWriter, credentials: Credentials, *, verify: Verify,
                 open_fetch: OpenFetch = live_fetch, now: Callable[[], str] = _now, tz: tzinfo | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._writer = writer
        self._credentials = credentials
        self._verify = verify
        self._open_fetch = open_fetch
        self._now = now
        self._tz = tz
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    # -- life cycle ----------------------------------------------------------------------------

    def recover(self) -> int:
        """Start-up: a run still RUNNING was interrupted (the Core stopped mid-collection)."""

        count = 0
        with self._writer.transaction():
            for row in self._writer.query("SELECT id FROM canvas_collections WHERE status = 'RUNNING'"):
                self._finish(int(row["id"]), "FAILED", "INTERRUPTED")
                count += 1
        if count:
            log_event("CANVAS_SYNC_INTERRUPTED", runs=count)
        return count

    def start(self) -> bool:
        """Run in the background; False when a run is already going."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._background, name="canvas-sync", daemon=True)
            self._thread.start()
            return True

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def due_for_auto(self) -> bool:
        """Linked courses exist and the last COMPLETE run is older than six hours (or missing)."""

        if not self._writer.query("SELECT 1 FROM canvas_courses LIMIT 1"):
            return False
        row = self._writer.query_one("SELECT max(finished_at) AS last FROM canvas_collections WHERE status = 'COMPLETE'")
        if row is None or row["last"] is None:
            return True
        last = datetime.fromisoformat(str(row["last"]))
        return (datetime.fromisoformat(self._now()) - last).total_seconds() > AUTO_SYNC_AFTER_S

    def _background(self) -> None:
        try:
            self.run()
        except Exception as exc:  # noqa: BLE001 - a fixed code; the run's rows already say what happened
            log_event("CANVAS_SYNC_CRASHED", error=type(exc).__name__)

    def status(self) -> dict[str, Any]:
        running = self.running
        courses = []
        for link in self._writer.query(
                "SELECT k.canvas_course_id, k.name, c.course_key FROM canvas_courses k JOIN courses c ON c.id = k.course_id "
                "ORDER BY k.canvas_course_id"):
            last = self._writer.query_one(
                "SELECT status, reason, started_at, finished_at, items FROM canvas_collections "
                "WHERE canvas_course_id = ? ORDER BY id DESC LIMIT 1", (link["canvas_course_id"],))
            courses.append({"canvas_course_id": link["canvas_course_id"], "name": link["name"],
                            "course_key": link["course_key"], "last": dict(last) if last is not None else None})
        return {"running": running, "error": self._last_error, "courses": courses}

    # -- one run -------------------------------------------------------------------------------

    def run(self) -> list[dict[str, Any]]:
        links = [dict(r) for r in self._writer.query(
            "SELECT k.canvas_course_id, k.course_id, c.course_key, c.code FROM canvas_courses k "
            "JOIN courses c ON c.id = k.course_id ORDER BY k.canvas_course_id")]
        try:
            token = self._credentials.token()
            origin = self._credentials.origin()
        except CredentialError as exc:
            self._last_error = exc.code
            return [{"canvas_course_id": link["canvas_course_id"], "status": "FAILED", "reason": exc.code}
                    for link in links]
        self._last_error = None
        principal = self._principal(origin, token)
        results = []
        for link in links:
            row_id = self._barrier(link["canvas_course_id"])
            if principal is not None:
                self._finish_alone(row_id, "FAILED", principal)
                results.append({"canvas_course_id": link["canvas_course_id"], "status": "FAILED", "reason": principal})
                continue
            results.append(self._course(link, row_id, origin, token))
        log_event("CANVAS_SYNC_DONE", courses=len(results),
                  complete=sum(1 for r in results if r["status"] == "COMPLETE"))
        return results

    def _principal(self, origin: str, token: CanvasToken) -> str | None:
        """None when ``users/self`` is still the connected user, else a FAILED reason."""

        try:
            user = self._verify(origin, token.reveal_token())
        except CanvasCheckError as exc:
            return exc.code
        except Exception:  # noqa: BLE001
            return "COLLECT_FAILED"
        profile = self._writer.query_one("SELECT user_id FROM canvas_profile WHERE id = 1")
        if profile is None or str(user.get("user_id")) != str(profile["user_id"]):
            return "PRINCIPAL_CHANGED"
        return None

    def _barrier(self, canvas_course_id: str) -> int:
        """Committed before anything can fail (R3-3 closes the recording calendar in the same transaction)."""

        with self._writer.transaction():
            return self._writer.insert(Owner.SYSTEM, COLLECTOR, "canvas_collections", {
                "canvas_course_id": canvas_course_id, "started_at": self._now(), "status": "RUNNING"})

    def _course(self, link: Mapping[str, Any], row_id: int, origin: str, token: CanvasToken) -> dict[str, Any]:
        cid = str(link["canvas_course_id"])
        try:
            with self._open_fetch(origin, token.reveal_token()) as fetch:
                collected = collect_course(fetch, cid, origin=origin, monotonic=self._monotonic)
        except Incomplete as exc:
            return self._finish_alone(row_id, "INCOMPLETE", exc.reason, cid)
        except CanvasCheckError as exc:
            return self._finish_alone(row_id, "FAILED", exc.code, cid)
        except Exception as exc:  # noqa: BLE001 - fixed code; Canvas text never surfaces
            log_event("CANVAS_COLLECT_ERROR", error=type(exc).__name__)
            return self._finish_alone(row_id, "FAILED", "COLLECT_FAILED", cid)
        try:
            with self._writer.transaction():
                note = self._apply(link, collected)
                count = sum(len(rows) for rows in collected.values())
                self._finish(row_id, "COMPLETE", note, items=count)
        except Exception as exc:  # noqa: BLE001 - the transaction rolled back: nothing of the run was written
            log_event("CANVAS_APPLY_ERROR", error=type(exc).__name__)
            return self._finish_alone(row_id, "FAILED", "APPLY_FAILED", cid)
        return {"canvas_course_id": cid, "status": "COMPLETE", "reason": note}

    def _finish(self, row_id: int, status: str, reason: str | None, *, items: int | None = None) -> None:
        self._writer.update(Owner.SYSTEM, COLLECTOR, "canvas_collections", {"id": row_id},
                            {"status": status, "reason": reason, "finished_at": self._now(), "items": items})

    def _finish_alone(self, row_id: int, status: str, reason: str, cid: str = "") -> dict[str, Any]:
        with self._writer.transaction():
            self._finish(row_id, status, reason)
        log_event("CANVAS_COLLECTION_" + status, reason=reason)
        return {"canvas_course_id": cid, "status": status, "reason": reason}

    # -- writing a COMPLETE collection ---------------------------------------------------------

    def _local(self, stamp: str | None) -> str | None:
        if stamp is None:
            return None
        value = datetime.fromisoformat(stamp)
        if value.tzinfo is None:
            return value.isoformat(timespec="seconds")
        return value.astimezone(self._tz).isoformat(timespec="seconds")

    def _apply(self, link: Mapping[str, Any], collected: Mapping[str, list[dict[str, Any]]]) -> str | None:
        cid, now = str(link["canvas_course_id"]), self._now()
        for kind in KINDS:
            existing = {r["resource_id"]: dict(r) for r in self._writer.query(
                "SELECT * FROM canvas_items WHERE canvas_course_id = ? AND kind = ?", (cid, kind))}
            seen = set()
            for row in collected.get(kind, []):
                seen.add(row["resource_id"])
                values = {"title": row["title"], "module_name": row["module_name"], "week": row["week"],
                          "item_type": row["item_type"], "due_at": self._local(row["due_at"]),
                          "canvas_updated_at": row["canvas_updated_at"],
                          "attachment_json": json.dumps(row["attachments"], ensure_ascii=False, separators=(",", ":")),
                          "active": 1, "last_seen_at": now}
                old = existing.get(row["resource_id"])
                if old is None:
                    self._writer.insert(Owner.SOURCE, COLLECTOR, "canvas_items", {
                        "canvas_course_id": cid, "kind": kind, "resource_id": row["resource_id"],
                        "first_seen_at": now, **values})
                else:
                    self._writer.update(Owner.SOURCE, COLLECTOR, "canvas_items", {"id": old["id"]}, values)
            for resource_id, old in existing.items():
                if resource_id not in seen and old["active"]:
                    self._writer.update(Owner.SOURCE, COLLECTOR, "canvas_items", {"id": old["id"]},
                                        {"active": 0, "last_seen_at": now})
        return self._activities(link, collected.get("ASSIGNMENT", []), now)

    def _activities(self, link: Mapping[str, Any], assignments: list[dict[str, Any]], now: str) -> str | None:
        note: str | None = None
        code = str(link["code"] or link["course_key"])
        for row in assignments:
            values = {"title": row["title"], "due_at": self._local(row["due_at"]),
                      "canvas_updated_at": row["canvas_updated_at"]}
            current = self._writer.query_one(
                "SELECT activity_id, title, due_at, canvas_updated_at FROM activities WHERE canvas_assignment_id = ?",
                (row["resource_id"],))
            if current is not None:
                if any(current[k] != v for k, v in values.items()):
                    self._writer.update(Owner.CATALOG, COLLECTOR, "activities", {"activity_id": current["activity_id"]},
                                        {**values, "updated_at": now})
                continue
            if not _CODE.fullmatch(code):
                note = "COURSE_CODE_INVALID"
                continue
            activity_id = self._next_activity_id(int(link["course_id"]), f"{code}-A")
            if activity_id is None:
                note = "ID_SPACE_EXHAUSTED"  # the assignment stays in canvas_items only
                continue
            self._writer.insert(Owner.CATALOG, COLLECTOR, "activities", {
                "activity_id": activity_id, "course_id": int(link["course_id"]), "origin": "CANVAS",
                "canvas_assignment_id": row["resource_id"], "created_at": now, "updated_at": now, **values})
        return note

    def _next_activity_id(self, course_row: int, prefix: str) -> str | None:
        used = [str(r["activity_id"])[len(prefix):] for r in self._writer.query(
            "SELECT activity_id FROM activities WHERE course_id = ?", (course_row,))]
        number = max((int(u) for u in used if u.isdigit()), default=0) + 1
        return f"{prefix}{number:02d}" if number <= MAX_ACTIVITY_SEQUENCE else None


__all__ = ["AUTO_SYNC_AFTER_S", "CanvasSync", "live_fetch"]
