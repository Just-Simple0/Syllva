"""Read one Canvas course's listings and prove they are complete (spec §12; R3 plan §6.3).

Ported from the v1.2 observer (``539877d^:intake/canvas_observer.py``): the same page
proof, the same RFC 8288 Link parser. A listing whose end is not proven raises
:class:`Incomplete` with a fixed reason; nothing is written by this module.

Rows carry metadata only: ids, titles, module names/weeks, item types, due/updated
timestamps and attachment ids/names/sizes/types. No URL, no download verifier, no HTML.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from syllva.canvas.http import CanvasResponse

PER_PAGE = 50
MAX_PAGES = 10
MAX_ITEMS = 500
MAX_RUN_SECONDS = 45.0
MAX_TITLE = 500
KINDS = ("ASSIGNMENT", "ANNOUNCEMENT", "MODULE_ITEM")
_WEEK_MODULE = re.compile(r"^(\d+)주차$")
_URI_REFERENCE = re.compile(r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+")
_TOKEN = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+")
_DIGITS = re.compile(r"[0-9]{1,64}")

Fetch = Callable[[str, float], CanvasResponse]


class Incomplete(Exception):
    """The listing is not provably complete (a fixed reason, never Canvas text)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- Link header (RFC 8288) --------------------------------------------------------------------


def _link_values(header: str) -> list[tuple[str, dict[str, str]]]:
    """``<target>; name=value; name="quoted, value"``, comma separated. Anything that does not
    parse completely is malformed, so a pagination header is never half-understood."""

    if any(not (32 <= ord(c) <= 126 or c == "\t") for c in header):
        raise Incomplete("LINK_MALFORMED")
    values: list[tuple[str, dict[str, str]]] = []
    i, n = 0, len(header)
    while i < n:
        while i < n and header[i] in " \t":
            i += 1
        if i >= n:
            break
        if header[i] != "<":
            raise Incomplete("LINK_MALFORMED")
        end = header.find(">", i)
        if end < 0 or _URI_REFERENCE.fullmatch(header[i + 1:end]) is None:
            raise Incomplete("LINK_MALFORMED")
        target = header[i + 1:end]
        i = end + 1
        params: dict[str, str] = {}
        while True:
            while i < n and header[i] in " \t":
                i += 1
            if i >= n or header[i] == ",":
                break
            if header[i] != ";":
                raise Incomplete("LINK_MALFORMED")
            i += 1
            while i < n and header[i] in " \t":
                i += 1
            start = i
            while i < n and header[i] not in "=;, \t":
                i += 1
            name = header[start:i].lower()
            if not name or _TOKEN.fullmatch(name) is None or i >= n or header[i] != "=":
                raise Incomplete("LINK_MALFORMED")
            i += 1
            if i < n and header[i] == '"':
                i += 1
                chunk: list[str] = []
                while i < n and header[i] != '"':
                    if header[i] == "\\" and i + 1 < n:
                        i += 1
                    chunk.append(header[i])
                    i += 1
                if i >= n:
                    raise Incomplete("LINK_MALFORMED")
                i += 1
                value = "".join(chunk)
            else:
                start = i
                while i < n and header[i] not in ";, \t":
                    i += 1
                value = header[start:i]
                if _TOKEN.fullmatch(value.lower()) is None:
                    raise Incomplete("LINK_MALFORMED")
            if not value.strip() or (name == "rel" and name in params):
                raise Incomplete("LINK_MALFORMED")
            params.setdefault(name, value)
        values.append((target, params))
        if i < n:  # header[i] == ","
            i += 1
            while i < n and header[i] in " \t":
                i += 1
            if i >= n or header[i] != "<":
                raise Incomplete("LINK_MALFORMED")  # a trailing or doubled separator
    if header.strip() and not values:
        raise Incomplete("LINK_MALFORMED")
    return values


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


def next_url(fields: list[str], current: str, origin: str) -> str | None:
    """The proven next page of ``current`` from its Link field(s), or None at the end."""

    if any(not value.strip() for value in fields):
        raise Incomplete("LINK_MALFORMED")  # a present but blank Link field is not "no next page"
    header = ", ".join(fields)
    if not header:
        return None
    here = urljoin(origin + "/", current)
    found: list[str] = []
    last_pages: list[int] = []
    for target, params in _link_values(header):
        relations = params.get("rel", "").lower().split()
        if "next" in relations:
            found.append(urljoin(here, target))
        if "last" in relations:
            number = _explicit_page_number(urljoin(here, target))
            if number is not None:
                last_pages.append(number)
    if len(found) > 1:
        raise Incomplete("LINK_MALFORMED")
    if not found:
        if any(number > _page_number(here) for number in last_pages):
            raise Incomplete("LAST_WITHOUT_NEXT")  # rel=last proves later pages exist
        if "next" in header.lower():
            raise Incomplete("LINK_UNRECOGNIZED")  # a "next" that is not a plain rel=next
        return None
    a, b = urlsplit(here), urlsplit(found[0])
    if (a.scheme, a.netloc, a.path) != (b.scheme, b.netloc, b.path):
        raise Incomplete("PAGINATION_SCOPE")

    def keep(query: str) -> list[tuple[str, str]]:
        return sorted(p for p in parse_qsl(query, keep_blank_values=True) if p[0] != "page")

    if keep(a.query) != keep(b.query):
        raise Incomplete("PAGINATION_SCOPE")
    return found[0]


# --- listings ----------------------------------------------------------------------------------


def _get(fetch: Fetch, url: str, deadline: float, origin: str) -> tuple[list[Any], str | None]:
    response = fetch(url, deadline)
    try:
        body = json.loads(response.body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Incomplete("MALFORMED_BODY") from None
    if not isinstance(body, list):
        raise Incomplete("MALFORMED_BODY")
    fields = [value for key, value in response.headers.items() if key.lower() == "link"]
    return body, next_url(fields, url, origin)


def read_listing(fetch: Fetch, first: str, deadline: float, *, origin: str,
                 monotonic: Callable[[], float] = time.monotonic) -> list[Any]:
    """Every page of a listing. A full page without ``next`` is followed by a probe of page n+1:
    an empty probe ends the listing, a non-empty one is ``FULL_PAGE_PROBE`` (Canvas withheld
    ``next``; stricter than v1.2, intentionally)."""

    items: list[Any] = []
    url = urljoin(origin + "/", first)
    for _ in range(MAX_PAGES):
        if monotonic() >= deadline:
            raise Incomplete("TIME_LIMIT")
        page, following = _get(fetch, url, deadline, origin)
        items.extend(page)
        if len(items) > MAX_ITEMS:
            raise Incomplete("ITEM_LIMIT")
        number = _page_number(url)
        if following is not None:
            if _explicit_page_number(following) != number + 1:
                raise Incomplete("PAGE_SEQUENCE")
            url = _with_page(url, number + 1)
            continue
        if len(page) < PER_PAGE:
            return items
        if monotonic() >= deadline:
            raise Incomplete("TIME_LIMIT")
        probe, probe_next = _get(fetch, _with_page(url, number + 1), deadline, origin)
        if probe or probe_next is not None:
            raise Incomplete("FULL_PAGE_PROBE")
        return items
    raise Incomplete("PAGE_LIMIT")


# --- rows --------------------------------------------------------------------------------------


def _id(value: Any, reason: str = "MALFORMED_ITEM") -> str:
    if isinstance(value, bool) or not isinstance(value, int | str) or not _DIGITS.fullmatch(str(value).strip()):
        raise Incomplete(reason)
    return str(value).strip()


def _text(value: Any) -> str | None:
    return value.strip()[:MAX_TITLE] if isinstance(value, str) and value.strip() else None


def _stamp(value: Any) -> str | None:
    """An ISO-8601 timestamp as Canvas sent it, or None (anything else is dropped)."""

    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _size(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _attachments(raw: Any) -> list[dict[str, Any]]:
    """Ids, names, sizes, types and timestamps only — never ``url``/``preview_url``/verifiers."""

    out: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            file_id = _id(entry.get("id"))
        except Incomplete:
            continue
        out.append({"id": file_id, "display_name": _text(entry.get("display_name") or entry.get("filename")),
                    "size": _size(entry.get("size")), "content_type": _text(entry.get("content-type")),
                    "updated_at": _stamp(entry.get("updated_at") or entry.get("modified_at"))})
    return out


def _row(raw: Any, title_key: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise Incomplete("MALFORMED_ITEM")
    title = _text(raw.get(title_key))
    if title is None:
        raise Incomplete("MALFORMED_ITEM")
    return {"resource_id": _id(raw.get("id")), "title": title, "module_name": None, "week": None,
            "item_type": None, "due_at": None, "canvas_updated_at": None, "attachments": []}


def collect_course(fetch: Fetch, canvas_course_id: str, *, origin: str = "https://canvas.invalid",
                   monotonic: Callable[[], float] = time.monotonic,
                   max_seconds: float = MAX_RUN_SECONDS) -> dict[str, list[dict[str, Any]]]:
    """Assignments, announcements and module items of one course, each listing proven complete."""

    course = _id(canvas_course_id)
    deadline = monotonic() + max_seconds
    base = f"/api/v1/courses/{course}"

    def listing(path: str) -> list[Any]:
        return read_listing(fetch, path, deadline, origin=origin, monotonic=monotonic)

    out: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    for raw in listing(f"{base}/assignments?order_by=name&per_page={PER_PAGE}"):
        row = _row(raw, "name")
        row["due_at"] = _stamp(raw.get("due_at"))
        row["canvas_updated_at"] = _stamp(raw.get("updated_at"))
        out["ASSIGNMENT"].append(row)
    for raw in listing(f"/api/v1/announcements?context_codes[]=course_{course}&per_page={PER_PAGE}"):
        row = _row(raw, "title")
        row["canvas_updated_at"] = _stamp(raw.get("posted_at") or raw.get("updated_at"))
        row["attachments"] = _attachments(raw.get("attachments"))
        out["ANNOUNCEMENT"].append(row)
    total = 0
    for module in listing(f"{base}/modules?include[]=items&per_page={PER_PAGE}"):
        if not isinstance(module, dict):
            raise Incomplete("MALFORMED_MODULE")
        name, items, count = _text(module.get("name")), module.get("items"), module.get("items_count")
        if name is None or not isinstance(items, list) or isinstance(count, bool) or not isinstance(count, int):
            raise Incomplete("MALFORMED_MODULE")
        module_id = _id(module.get("id"))  # a URL path segment: digits only
        if len(items) != count:
            items = listing(f"{base}/modules/{module_id}/items?per_page={PER_PAGE}")
            if len(items) != count:
                raise Incomplete("MODULE_ITEMS_INCOMPLETE")
        match = _WEEK_MODULE.match(name)
        week = int(match.group(1)) if match and int(match.group(1)) > 0 else None
        for raw in items:
            total += 1
            if total > MAX_ITEMS:
                raise Incomplete("ITEM_LIMIT")
            row = _row(raw, "title")
            row.update(module_name=name, week=week, item_type=_text(raw.get("type")),
                       canvas_updated_at=_stamp(raw.get("updated_at")))
            if row["item_type"] == "File":
                try:
                    row["attachments"] = [{"id": _id(raw.get("content_id"))}]
                except Incomplete:
                    row["attachments"] = []
            out["MODULE_ITEM"].append(row)
    for rows in out.values():
        if len({r["resource_id"] for r in rows}) != len(rows):
            raise Incomplete("DUPLICATE_ID")
    return out


__all__ = ["KINDS", "MAX_ITEMS", "MAX_PAGES", "PER_PAGE", "Incomplete", "collect_course", "next_url", "read_listing"]
