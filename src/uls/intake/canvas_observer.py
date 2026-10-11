"""Read-only Canvas metadata observation (plan §2.3, §6.3; P-B3a).

The observer reads the assignment, announcement and module listings of one verified
Canvas course through the pinned, bounded transport of the Settings Canvas connection
and records *metadata only*: observations, their S1 verdicts and the recording calendar.
It never creates plans, jobs, File Intake rows, Materials or Input Requests.

Fail-closed order for one course: gate → close the calendar (one transaction) → read
everything → record → reconcile → classify → project the calendar.  A crash or error at
any point leaves the calendar closed, because only the final verified projection
reopens it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from ..settings.canvas_checks import (
    CanvasCheckError,
    CanvasConnection,
    open_canvas_connection,
    validate_canvas_origin,
)
from ..state.classification_state import is_recording_observation
from .classification.calendar import RecordingEntry, SemesterRange, build_calendar
from .classification.rules import (
    CANVAS_ANNOUNCEMENT,
    CANVAS_ASSIGNMENT,
    CANVAS_MODULE_ITEM,
    ItemSignals,
    classify_by_rules,
)
from .classification.taxonomy import Origin

PER_PAGE = 50
MAX_PAGES = 10
MAX_ITEMS = 500
MAX_RUN_SECONDS = 45.0
_WEEK_MODULE = re.compile(r"^(\d+)주차$")
_CANVAS_METADATA_ONLY = "CANVAS_METADATA_ONLY"
_URI_REFERENCE = re.compile(r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+")
_TOKEN = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+")


class _Incomplete(Exception):
    """The collection is not provably complete; the calendar must stay closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CanvasReader(Protocol):
    def get_page(self, url: str, deadline: float) -> tuple[list[Any], str | None]:
        """One GET of an allowlisted listing: (items, next_url or None)."""


@dataclass(frozen=True)
class ObservationResult:
    status: str  # COMPLETE | INCOMPLETE | INELIGIBLE | DISABLED
    reason: str = ""
    course_key: str = ""
    canvas_course_id: int = 0
    observed: int = 0
    classified: int = 0


class ConnectionReader:
    """Production reader over one pinned CanvasConnection (never exposes the token)."""

    def __init__(self, connection: CanvasConnection, token: str) -> None:
        self._connection = connection
        self._token = token

    def get_page(self, url: str, deadline: float) -> tuple[list[Any], str | None]:
        response = self._connection.observe(url, self._token, deadline=deadline)
        try:
            body = json.loads(response.body)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _Incomplete("MALFORMED_RESPONSE") from None
        if not isinstance(body, list):
            raise _Incomplete("MALFORMED_RESPONSE")
        # Every Link field counts (HTTP allows several, in any letter case).
        fields = [value for key, value in response.headers.items() if key.lower() == "link"]
        if any(not value.strip() for value in fields):
            raise _Incomplete("MALFORMED_LINK")      # a present but blank Link field is not "no next page"
        link = ", ".join(fields)
        return body, _next_url(link, url, self._connection.target.origin)


def _link_values(header: str) -> list[tuple[str, dict[str, str]]]:
    """RFC 8288 link-values: ``<target>; name=value; name="quoted, value"``, comma separated.

    Commas inside ``<...>`` or quoted strings do not separate values; anything that does not
    parse completely raises, so a pagination header is never half-understood."""

    if any(not (32 <= ord(c) <= 126 or c == "\t") for c in header):
        raise _Incomplete("MALFORMED_LINK")          # printable ASCII (and HTAB) only: no controls, DEL or case-folding surprises
    values: list[tuple[str, dict[str, str]]] = []
    i, n = 0, len(header)
    while i < n:
        while i < n and header[i] in " \t":
            i += 1
        if i >= n:
            break
        if header[i] != "<":
            raise _Incomplete("MALFORMED_LINK")
        end = header.find(">", i)
        if end < 0:
            raise _Incomplete("MALFORMED_LINK")
        target = header[i + 1:end]
        if _URI_REFERENCE.fullmatch(target) is None:        # every target, not only rel=next
            raise _Incomplete("MALFORMED_LINK")
        i = end + 1
        params: dict[str, str] = {}
        while True:
            while i < n and header[i] in " \t":
                i += 1
            if i >= n or header[i] == ",":
                break
            if header[i] != ";":
                raise _Incomplete("MALFORMED_LINK")
            i += 1
            while i < n and header[i] in " \t":
                i += 1
            start = i
            while i < n and header[i] not in "=;, \t":
                i += 1
            name = header[start:i].lower()
            if not name or _TOKEN.fullmatch(name) is None:
                raise _Incomplete("MALFORMED_LINK")
            value = ""
            if i >= n or header[i] != "=":
                raise _Incomplete("MALFORMED_LINK")      # every parameter carries a value
            if i < n and header[i] == "=":
                i += 1
                if i < n and header[i] == '"':
                    i += 1
                    chunk = []
                    while i < n and header[i] != '"':
                        if header[i] == "\\" and i + 1 < n:
                            i += 1
                        chunk.append(header[i])
                        i += 1
                    if i >= n:
                        raise _Incomplete("MALFORMED_LINK")
                    i += 1
                    value = "".join(chunk)
                else:
                    start = i
                    while i < n and header[i] not in ";, \t":
                        i += 1
                    value = header[start:i]
                    if _TOKEN.fullmatch(value.lower()) is None:      # an unquoted value is a complete token
                        raise _Incomplete("MALFORMED_LINK")
            if not value.strip() or name in params and name == "rel":
                raise _Incomplete("MALFORMED_LINK")      # an empty value or a repeated rel is uninterpretable
            params.setdefault(name, value)
        values.append((target, params))
        if i < n:                                   # header[i] == ","
            i += 1
            while i < n and header[i] in " \t":
                i += 1
            if i >= n or header[i] != "<":          # a trailing or doubled separator is malformed
                raise _Incomplete("MALFORMED_LINK")
    if header.strip() and not values:
        raise _Incomplete("MALFORMED_LINK")
    return values


def _next_url(link: str, current: str, origin: str = "https://canvas.invalid") -> str | None:
    if not link:
        return None
    here = urljoin(origin + "/", current)
    found: list[str] = []
    if not link.isascii():
        raise _Incomplete("MALFORMED_LINK")
    last_pages: list[int] = []
    for target, params in _link_values(link):
        relations = params.get("rel", "").lower().split()
        if "next" in relations:
            found.append(urljoin(here, target))
        if "last" in relations:
            number = _explicit_page_number(urljoin(here, target))
            if number is not None:
                last_pages.append(number)
    if len(found) > 1:
        raise _Incomplete("MALFORMED_LINK")
    if not found and any(number > _page_number(here) for number in last_pages):
        # rel=last proves later pages exist; without a next link the collection is not complete.
        raise _Incomplete("PAGE_SEQUENCE")
    if not found:
        if "next" in link.lower():
            # Anything that mentions a next page without being a plain rel=next link is not
            # understood: the end of the collection is never concluded from it (fail closed).
            raise _Incomplete("MALFORMED_LINK")
        return None
    # The next page keeps the first request's collection: same origin, same path and the
    # same query apart from the page number (no other course or listing; plan §6.3).
    a, b = urlsplit(here), urlsplit(found[0])
    if (a.scheme, a.netloc, a.path) != (b.scheme, b.netloc, b.path):
        raise _Incomplete("PAGINATION_SCOPE")

    def keep(query: str) -> list[tuple[str, str]]:
        return sorted(p for p in parse_qsl(query, keep_blank_values=True) if p[0] != "page")
    if keep(a.query) != keep(b.query):
        raise _Incomplete("PAGINATION_SCOPE")
    return found[0]


def _page_number(url: str) -> int:
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == "page" and value.isdecimal():
            return max(1, int(value))
    return 1


def _explicit_page_number(url: str) -> int | None:
    numbers = [v for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True) if k == "page"]
    return int(numbers[0]) if len(numbers) == 1 and numbers[0].isdecimal() else None


def _with_page(url: str, number: int) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    query.append(("page", str(number)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, safe="[]"), ""))


def connection_reader_factory(
    origin: str, token_provider: Callable[[], str | None],
) -> Callable[[], AbstractContextManager[CanvasReader | None]]:
    """A factory yielding a pinned reader, or None when no Canvas credential is available."""

    @contextmanager
    def factory() -> Iterator[CanvasReader | None]:
        token = token_provider()
        if not token:
            yield None
            return
        with open_canvas_connection(origin) as connection:
            yield ConnectionReader(connection, token)

    return factory


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identifier(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).strip():
        raise _Incomplete("MALFORMED_ID")
    return str(value).strip()


class CanvasObserver:
    def __init__(
        self,
        state: Any,
        *,
        origin: str,
        verified_origin: str,
        course_map: Mapping[int, str] | Callable[[], Mapping[int, str]],
        reader_factory: Callable[[], AbstractContextManager[CanvasReader | None]],
        semester_for: Callable[[str], SemesterRange | None],
        monotonic: Callable[[], float] = time.monotonic,
        max_run_seconds: float = MAX_RUN_SECONDS,
        profile_provider: Callable[[], str | None] | None = None,
        principal_provider: Callable[[], str | None] | None = None,
        verified_principal: str = "",
    ) -> None:
        self._state = state
        self._profile_provider = profile_provider
        self._denied: set[str] = set()   # calendars whose closing could not be persisted: unusable until it is
        self._principal_provider = principal_provider
        self._verified_principal = verified_principal
        self._origin = origin
        self._verified_origin = verified_origin
        self._map_source = course_map
        self._factory = reader_factory
        self._semester_for = semester_for
        self._monotonic = monotonic
        self._max_run_seconds = max_run_seconds

    @property
    def _course_map(self) -> dict[int, str]:
        """The *current* verified mapping (re-read on every use, never a copy)."""

        source = self._map_source() if callable(self._map_source) else self._map_source
        return {int(k): v for k, v in source.items()}

    def current_proof(self, course_key: str) -> str | None:
        """Evidence that the active Canvas connection is the one the course map was verified
        against for this course key (plan §2.3): the origin we read from, the verified origin
        and the ACTIVE profile origin are one, and the connection principal is readable.  Any
        missing evidence (no provider, unreadable principal, ambiguous mapping) is no proof."""

        if course_key in self._denied:
            return None
        try:
            mapping = self._course_map
        except Exception:  # noqa: BLE001 - an unreadable mapping is no proof
            return None
        ids = sorted(cid for cid, key in mapping.items() if key == course_key)
        if len(ids) != 1 or not self.profile_matches():
            return None
        principal = self._active_principal()
        if principal is None:
            return None
        return hashlib.sha256(json.dumps(
            ["canvas-calendar-proof.v1", self._origin, principal, ids[0], course_key],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    def _active_principal(self) -> str | None:
        """The active connection's principal, only when it is the one the registry verified."""

        if self._principal_provider is None or not self._verified_principal:
            return None
        try:
            principal = self._principal_provider()
        except Exception:  # noqa: BLE001 - an unreadable principal is no proof
            return None
        return principal if principal and principal == self._verified_principal else None

    def profile_matches(self) -> bool:
        """The *active* Canvas profile origin (read from the injected provider on every call; no
        provider means no evidence), the origin we read from and the verified origin are one."""

        try:
            if self._profile_provider is None:
                return False
            current = self._profile_provider()
            if not current:
                return False
            return (
                validate_canvas_origin(current) == validate_canvas_origin(self._origin)
                == validate_canvas_origin(self._verified_origin)
            )
        except Exception:  # noqa: BLE001 - an unreadable profile is no evidence (fail closed)
            return False

    # --- gates -------------------------------------------------------------------

    def _gate(self, canvas_course_id: int) -> ObservationResult | None:
        mapping = self._course_map
        course_key = mapping.get(int(canvas_course_id))
        if course_key is None:
            return ObservationResult("INELIGIBLE", "COURSE_NOT_MAPPED", "", int(canvas_course_id))
        if not self.profile_matches() or self._active_principal() is None:
            self._close(course_key)
            return ObservationResult("INELIGIBLE", "PROFILE_MISMATCH", course_key, int(canvas_course_id))
        if sum(1 for key in mapping.values() if key == course_key) != 1:
            self._close(course_key)
            return ObservationResult("INELIGIBLE", "MANY_TO_ONE_MAPPING", course_key, int(canvas_course_id))
        return None

    def _close(self, course_key: str) -> None:
        row = self._state.recording_calendar_course(course_key)
        if row is not None:
            self._state.close_recording_calendar(course_key, int(row["canvas_course_id"]))

    def _barrier(self, course_key: str, canvas_course_id: int) -> bool:
        """Close the calendar before anything can fail (one transaction).  Returns True when a
        binding was changed by the CURRENT verified mapping (the key moved to another Canvas
        course, or the Canvas course moved from another key): the old projection is withdrawn
        and the observation history is renumbered by the caller."""

        row = self._state.recording_calendar_course(course_key)
        bound_key = self._state.recording_calendar_key_for_canvas(canvas_course_id)
        moved = (row is not None and int(row["canvas_course_id"]) != canvas_course_id) or (
            bound_key is not None and bound_key != course_key)
        if moved or row is None:
            self._state.rebind_recording_calendar(course_key, canvas_course_id, release_other=True)
        self._state.close_recording_calendar(course_key, canvas_course_id)
        return moved

    # --- one course --------------------------------------------------------------

    def observe_course(self, canvas_course_id: int) -> ObservationResult:
        canvas_course_id = int(canvas_course_id)
        try:
            blocked = self._gate(canvas_course_id)
        except Exception:  # noqa: BLE001 - fixed failure; nothing was read
            # The gate may have failed while closing the calendar: the course is unusable until a
            # complete observation re-verifies it.
            try:
                key = self._course_map.get(canvas_course_id)
            except Exception:  # noqa: BLE001 - an unreadable mapping denies by id below
                key = None
            if key:
                self._denied.add(key)
            return ObservationResult("INELIGIBLE", "GATE_FAILED", key or "", canvas_course_id)
        if blocked is not None:
            return blocked
        course_key = self._course_map[canvas_course_id]
        # Barrier first (before the connection can fail): a crash or error anywhere below
        # keeps the calendar closed.
        try:
            rebound = self._barrier(course_key, canvas_course_id)
        except Exception:  # noqa: BLE001 - the closing could not be persisted: deny this calendar now
            self._denied.add(course_key)
            return ObservationResult("INCOMPLETE", "BARRIER_FAILED", course_key, canvas_course_id)
        self._denied.discard(course_key)
        try:
            with self._factory() as reader:
                if reader is None:
                    return ObservationResult("DISABLED", "NO_CREDENTIAL", course_key, canvas_course_id)
                collected = self._collect(reader, canvas_course_id)
        except _Incomplete as error:
            return self._incomplete(course_key, canvas_course_id, error.reason)
        except CanvasCheckError as error:
            return self._incomplete(course_key, canvas_course_id, error.code)
        except Exception:  # noqa: BLE001 - fixed failure code only; the calendar is already closed
            # Whatever the connection layer raised, the calendar is already closed; the
            # caller sees a fixed failure, never provider text.
            return self._incomplete(course_key, canvas_course_id, "OBSERVATION_FAILED")
        return self._record(course_key, canvas_course_id, collected, rebound)

    def _incomplete(self, course_key: str, canvas_course_id: int, reason: str) -> ObservationResult:
        self._state.reconcile_canvas_collection(
            origin=self._origin, canvas_course_id=canvas_course_id,
            resource_kind=CANVAS_MODULE_ITEM, seen_resource_ids=(), collection_complete=False,
        )
        return ObservationResult("INCOMPLETE", reason, course_key, canvas_course_id)

    # --- reading -----------------------------------------------------------------

    def _pages(self, reader: CanvasReader, first: str, deadline: float) -> list[Any]:
        """Read a listing to its end.  The end is never concluded from a Link header alone: a
        full page without a ``next`` link is followed by an explicit probe of the next page
        number, so a header the parser did not understand cannot truncate the collection."""

        items: list[Any] = []
        url: str | None = first
        for _ in range(MAX_PAGES):
            if url is None:
                return items
            if self._monotonic() >= deadline:
                raise _Incomplete("TIME_LIMIT")
            page, nxt = reader.get_page(url, deadline)
            items.extend(page)
            if len(items) > MAX_ITEMS:
                raise _Incomplete("ITEM_LIMIT")
            number = _page_number(url)
            if nxt is not None and _explicit_page_number(nxt) != number + 1:
                # A next link that skips, repeats or omits the page number proves nothing about the
                # pages in between: the collection is not complete.
                raise _Incomplete("PAGE_SEQUENCE")
            if nxt is None and len(page) >= PER_PAGE:
                nxt = _with_page(url, number + 1)
            url = None if nxt is None else _with_page(url, number + 1)
        if url is not None:
            raise _Incomplete("PAGE_LIMIT")
        return items

    def _collect(self, reader: CanvasReader, course_id: int) -> dict[str, list[dict[str, Any]]]:
        deadline = self._monotonic() + self._max_run_seconds
        base = f"/api/v1/courses/{course_id}"
        assignments = self._pages(reader, f"{base}/assignments?order_by=name&per_page={PER_PAGE}", deadline)
        announcements = self._pages(
            reader, f"/api/v1/announcements?context_codes[]=course_{course_id}&per_page={PER_PAGE}", deadline)
        modules = self._pages(reader, f"{base}/modules?include[]=items&per_page={PER_PAGE}", deadline)
        out: dict[str, list[dict[str, Any]]] = {
            CANVAS_ASSIGNMENT: [], CANVAS_ANNOUNCEMENT: [], CANVAS_MODULE_ITEM: [],
        }
        for row in assignments:
            out[CANVAS_ASSIGNMENT].append(self._plain(row, "name", None, None))
        for row in announcements:
            out[CANVAS_ANNOUNCEMENT].append(self._plain(row, "title", None, None))
        total = 0
        for module in modules:
            if not isinstance(module, dict):
                raise _Incomplete("MALFORMED_MODULE")
            module_id = _identifier(module.get("id"))
            name = _text(module.get("name"))
            items = module.get("items")
            count = module.get("items_count")
            if name is None or not isinstance(items, list) or isinstance(count, bool) or not isinstance(count, int):
                raise _Incomplete("MALFORMED_MODULE")
            if len(items) != count:
                items = self._pages(
                    reader, f"{base}/modules/{module_id}/items?per_page={PER_PAGE}", deadline)
                if len(items) != count:
                    raise _Incomplete("MODULE_ITEMS_INCOMPLETE")
            week_match = _WEEK_MODULE.match(name)
            week = int(week_match.group(1)) if week_match and int(week_match.group(1)) > 0 else None
            for item in items:
                total += 1
                if total > MAX_ITEMS:
                    raise _Incomplete("ITEM_LIMIT")
                out[CANVAS_MODULE_ITEM].append(self._plain(item, "title", name, week))
        for kind, rows in out.items():
            if len({r["resource_id"] for r in rows}) != len(rows):
                raise _Incomplete(f"DUPLICATE_{kind.upper()}")
        return out

    @staticmethod
    def _plain(row: Any, title_key: str, module_name: str | None, week: int | None) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise _Incomplete("MALFORMED_ITEM")
        title = _text(row.get(title_key))
        if title is None:
            raise _Incomplete("MALFORMED_TITLE")
        item_type = row.get("type") if module_name is not None else None
        updated = _text(row.get("updated_at")) or _text(row.get("posted_at"))
        return {
            "resource_id": _identifier(row.get("id")), "title": title, "module_name": module_name,
            "module_week": week, "item_type": _text(item_type), "updated_at": updated,
        }

    # --- recording ---------------------------------------------------------------

    def _revision(self, kind: str, row: dict[str, Any], course_id: int, bump: bool = False) -> int:
        latest = self._state.latest_canvas_observation(self._origin, course_id, kind, row["resource_id"])
        if latest is None:
            return 1
        stored = (latest["title"], latest["module_name"], latest["module_week"],
                  latest["item_type"], latest["updated_at"])
        payload = (row["title"], row["module_name"], row["module_week"], row["item_type"], row["updated_at"])
        # The whole stored payload is compared, not only updated_at (P-B3 A3); after a re-mapping
        # every observation is a new revision (the calendar history is immutable per key).
        return int(latest["observation_revision"]) + (0 if stored == payload and not bump else 1)

    def _record(
        self, course_key: str, course_id: int, collected: dict[str, list[dict[str, Any]]], bump: bool = False,
    ) -> ObservationResult:
        observed = classified = 0
        self._state.deactivate_foreign_origin_observations(course_id, self._origin)
        for kind, rows in collected.items():
            for row in rows:
                revision = self._revision(kind, row, course_id, bump)
                self._state.record_canvas_observation(
                    origin=self._origin, canvas_course_id=course_id, resource_kind=kind,
                    resource_id=row["resource_id"], observation_revision=revision, title=row["title"],
                    module_name=row["module_name"], module_week=row["module_week"],
                    item_type=row["item_type"], updated_at=row["updated_at"], collection_complete=True,
                )
                observed += 1
                classified += self._classify(course_id, kind, row, revision)
            self._state.reconcile_canvas_collection(
                origin=self._origin, canvas_course_id=course_id, resource_kind=kind,
                seen_resource_ids=[r["resource_id"] for r in rows], collection_complete=True,
            )
        reason = self._project_calendar(course_key, course_id)
        status = "COMPLETE" if reason == "" else "INCOMPLETE"
        return ObservationResult(status, reason, course_key, course_id, observed, classified)

    def _classify(self, course_id: int, kind: str, row: dict[str, Any], revision: int) -> int:
        decision = classify_by_rules(ItemSignals(
            title=row["title"], origin=Origin.UNKNOWN, canvas_resource_kind=kind,
            canvas_item_type=row["item_type"],
        ))
        payload = {
            "handling_mode": _CANVAS_METADATA_ONLY,
            "decision": decision.decision_type.value,
            "rule_id": decision.rule_id,
            "priority": decision.priority,
            "candidates": [k.value for k in decision.candidates],
            "origin_candidate": Origin.PROFESSOR_SOURCE.value if decision.decided else None,
            "week": row["module_week"],
            "reason": "" if decision.decided else "S3_DEFERRED",
            "unsupported": decision.terminal_unsupported,
        }
        fingerprint = hashlib.sha256(json.dumps(
            [self._origin, course_id, kind, row["resource_id"], revision, row["title"], row["module_name"],
             row["module_week"], row["item_type"], row["updated_at"]],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self._state.record_canvas_classification(
            observation_fingerprint=fingerprint, origin=self._origin, canvas_course_id=course_id,
            resource_kind=kind, resource_id=row["resource_id"], observation_revision=revision,
            kind=decision.kind.value if decision.kind is not None else None,
            decision_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            rule_table_version=decision.rule_table_version,
        )
        return 1

    def _project_calendar(self, course_key: str, course_id: int) -> str:
        entries: list[RecordingEntry] = []
        for obs in self._state.list_active_canvas_observations(course_id, CANVAS_MODULE_ITEM, self._origin):
            if not is_recording_observation(obs["item_type"], obs["title"], obs["resource_kind"]):
                continue
            week = obs["module_week"]
            if isinstance(week, bool) or not isinstance(week, int) or week < 1:
                return "RECORDING_WITHOUT_WEEK"  # cannot be placed: the calendar stays closed
            entries.append(RecordingEntry(
                course_id, str(obs["resource_id"]), int(obs["observation_revision"]), week,
                date.fromisoformat(str(obs["title"]).strip()),
            ))
        # The eligibility is proven again immediately before the projection is made.
        proof = self.current_proof(course_key)
        if proof is None or self._gate(course_id) is not None:
            return "ELIGIBILITY_LOST"
        calendar = build_calendar(
            course_key, entries, semester=self._semester_for(course_key), collection_complete=True)
        self._state.replace_recording_calendar_current(
            course_key, course_id, calendar, collection_complete=True)
        self._state.set_classification_cursor(f"calendar_proof:{course_key}", proof)
        return ""
