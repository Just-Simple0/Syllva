#!/usr/bin/env python3
"""Bounded, read-only Canvas metadata probe for the KNU LMS.

The access token is deliberately accepted only through getpass in a local TTY.
This script does not read credentials from arguments, environment, files, or
browser state, and it never downloads Canvas content or writes to a provider.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import importlib.util
import json
import math
import os
import queue
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, NoReturn, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CANVAS_ORIGIN = "https://canvas.knu.ac.kr"
CANVAS_SCHEME = "https"
CANVAS_NETLOC = "canvas.knu.ac.kr"
API_PREFIX = "/api/v1/"
PER_PAGE = 50
MAX_PAGES = 3
MAX_ITEMS = 500
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RUN_SECONDS = 45.0
MAX_SOCKET_TIMEOUT_SECONDS = 10.0
MAX_ANNOUNCEMENT_DAYS = 31
READ_CHUNK_BYTES = 64 * 1024
MAX_TEXT_LENGTH = 500
MAX_ASIDE_FRAME_BYTES = 512 * 1024
MAX_ASIDE_OUTPUT_BYTES = 768 * 1024
ASIDE_REAP_GRACE_SECONDS = 0.1
SENSITIVE_QUERY_KEYS = {"access_token", "api_key", "authorization", "token"}
HTTP_RESOURCES = frozenset({"course", "assignments", "announcements", "files", "modules"})


class ProbeError(Exception):
    """An error whose public representation is a fixed, non-sensitive code."""

    def __init__(self, code: str, *, incomplete: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.incomplete = incomplete


class ArgumentError(ProbeError):
    def __init__(self) -> None:
        super().__init__("argument_error")


def _validated_http_status(status: Any) -> int:
    if type(status) is not int or not 100 <= status <= 599:
        raise ProbeError("invalid_http_status")
    return status


class HttpProbeError(ProbeError):
    """Only a validated status and header-name presence cross the HTTP boundary."""

    def __init__(self, status: Any, headers: Any) -> None:
        super().__init__("http_error")
        self.http_status = _validated_http_status(status)
        self.auth_challenge_present = headers is not None and any(
            isinstance(name, str) and name.casefold() == "www-authenticate"
            for name in headers
        )

    def diagnostics(self, resource: str) -> dict[str, Any]:
        if resource not in HTTP_RESOURCES:
            raise ProbeError("invalid_resource")
        return {
            "error": self.code,
            "http_status": self.http_status,
            "resource": resource,
            "auth_challenge_present": self.auth_challenge_present,
        }


class ResourceItemIncomplete(ProbeError):
    """A safe partial resource item that makes the resource incomplete."""

    def __init__(self, code: str, item: dict[str, Any]) -> None:
        super().__init__(code, incomplete=True)
        self.item = item


class SanitizedArgumentParser(argparse.ArgumentParser):
    """Never echo an unknown option or value, which could contain a secret."""

    def error(self, message: str) -> NoReturn:
        raise ArgumentError()


class NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects before urllib can issue a request to the Location."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request:
        raise ProbeError("redirect_rejected")


@dataclass(frozen=True)
class ResourceResult:
    items: list[dict[str, Any]]
    complete: bool
    reason: str | None = None


@dataclass(frozen=True)
class AsideResponse:
    """Sanitized response metadata crossing the Aside process boundary."""

    status: int
    final_url: str
    headers: dict[str, str]
    payload: dict[str, Any]


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("integer must be positive")
    return parsed


def calendar_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid calendar date") from exc
    return value


def non_empty(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")
    return value


def build_parser() -> SanitizedArgumentParser:
    parser = SanitizedArgumentParser(
        description="Read bounded Canvas course metadata using a locally entered token.",
        epilog=(
            "Live use requires independently verified expected name, course code, and term. "
            "Missing or uncertain identity stops before resource calls; do not guess identity, "
            "try fake credentials, or relax matching."
        ),
    )
    parser.add_argument("--course-id", required=True, type=positive_int)
    parser.add_argument("--expected-name", required=True, type=non_empty)
    parser.add_argument(
        "--expected-code",
        required=True,
        type=non_empty,
        help="Independently verified course code that distinguishes the section; do not guess.",
    )
    parser.add_argument(
        "--expected-term",
        required=True,
        type=non_empty,
        help="Independently verified enrollment term; do not guess.",
    )
    parser.add_argument("--start-date", required=True, type=calendar_date)
    parser.add_argument("--end-date", required=True, type=calendar_date)
    parser.add_argument("--include-files", action="store_true")
    parser.add_argument("--include-modules", action="store_true")
    return parser


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:MAX_TEXT_LENGTH]


def _safe_id(value: Any) -> int | None:
    if type(value) is not int or value < 0:  # bool is intentionally not accepted as an ID.
        return None
    return value


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProbeError("nonfinite_scalar")
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    return None


def _reject_json_constant(value: str) -> NoReturn:
    raise ProbeError("nonfinite_json")


def _parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ProbeError("nonfinite_json")
    return parsed


def validate_access_token(token: Any) -> str:
    if not isinstance(token, str) or not token or any(
        not 0x21 <= ord(char) <= 0x7E for char in token
    ):
        raise ProbeError("credential_invalid")
    return token


def _copy_fields(record: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        if field in record:
            value = _safe_scalar(record[field])
            if value is not None or record[field] is None:
                result[field] = value
    return result


def sanitize_course(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ProbeError("course_identity_missing")
    course_id = _safe_id(record.get("id"))
    name = _safe_text(record.get("name"))
    code = _safe_text(record.get("course_code"))
    term = record.get("term")
    term_name = _safe_text(term.get("name")) if isinstance(term, dict) else None
    if course_id is None or not name or not code or not term_name:
        raise ProbeError("course_identity_missing")
    return {
        "id": course_id,
        "name": name,
        "course_code": code,
        "term": term_name,
    }


def verify_course(
    record: Any,
    *,
    course_id: int,
    expected_name: str,
    expected_code: str,
    expected_term: str,
) -> dict[str, Any]:
    course = sanitize_course(record)
    if course["id"] != course_id:
        raise ProbeError("course_identity_mismatch")
    if _normalize(course["name"]) != _normalize(expected_name):
        raise ProbeError("course_identity_mismatch")
    if _normalize(course["course_code"]) != _normalize(expected_code):
        raise ProbeError("course_identity_mismatch")
    if _normalize(course["term"]) != _normalize(expected_term):
        raise ProbeError("course_identity_mismatch")
    return course


def sanitize_assignment(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ProbeError("invalid_resource_shape")
    assignment_id = _safe_id(record.get("id"))
    name = _safe_text(record.get("name"))
    if assignment_id is None or not name:
        raise ProbeError("invalid_resource_item")
    result: dict[str, Any] = {"id": assignment_id, "name": name, "due_at": None}
    for field in ("due_at", "lock_at", "unlock_at"):
        if field in record:
            value = _safe_scalar(record[field])
            if value is not None or record[field] is None:
                result[field] = value
    for field in ("position", "published", "locked_for_user"):
        if field in record:
            value = _safe_scalar(record[field])
            if value is not None:
                result[field] = value
    return result


def sanitize_announcement(
    record: Any, *, expected_context_code: str | None = None
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ProbeError("invalid_resource_shape")
    announcement_id = _safe_id(record.get("id"))
    title = _safe_text(record.get("title"))
    context_code = _safe_text(record.get("context_code"))
    if announcement_id is None or not title or not context_code:
        raise ProbeError("invalid_resource_item")
    if expected_context_code is not None and context_code != expected_context_code:
        raise ProbeError("announcement_context_mismatch")
    result = {"id": announcement_id, "title": title, "context_code": context_code}
    for field in ("posted_at", "published_at", "delayed_post_at"):
        value = _safe_scalar(record.get(field))
        if value is not None:
            result[field] = value
    return result


def sanitize_file(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ProbeError("invalid_resource_shape")
    file_id = _safe_id(record.get("id"))
    name = _safe_text(record.get("display_name")) or _safe_text(record.get("filename"))
    if file_id is None or not name:
        raise ProbeError("invalid_resource_item")
    result: dict[str, Any] = {"id": file_id, "name": name}
    for field in ("size", "folder_id", "content_type", "created_at", "modified_at"):
        value = _safe_scalar(record.get(field))
        if value is not None:
            result[field] = value
    return result


def sanitize_module(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ProbeError("invalid_resource_shape")
    module_id = _safe_id(record.get("id"))
    name = _safe_text(record.get("name"))
    if module_id is None or not name:
        raise ProbeError("invalid_resource_item")
    result: dict[str, Any] = {"id": module_id, "name": name}
    for field in ("position", "published", "state", "unlock_at", "require_sequential_progress"):
        value = _safe_scalar(record.get(field))
        if value is not None:
            result[field] = value
    items = record.get("items")
    if not isinstance(items, list):
        result.update(
            {
                "items_available": False,
                "items_complete": False,
                "items_unavailable": True,
                "items_error": "unavailable" if items is None else "invalid_items",
                "items_count_available": False,
                "items_returned_count": 0,
            }
        )
        raise ResourceItemIncomplete("module_items_unavailable", result)

    safe_items: list[dict[str, Any]] = []
    result["items_available"] = True
    result["items_unavailable"] = False
    result["items_count_available"] = "items_count" in record or record.get(
        "items_count_available"
    ) is True
    result["items_returned_count"] = 0
    for item in items:
        if len(safe_items) >= MAX_ITEMS:
            result.update(
                {
                    "items_complete": False,
                    "items_error": "item_cap",
                    "items_expected_count": len(items),
                    "items_returned_count": len(safe_items),
                    "items": safe_items,
                }
            )
            raise ResourceItemIncomplete("module_items_item_cap", result)
        if not isinstance(item, dict):
            result.update(
                {
                    "items_complete": False,
                    "items_error": "invalid_item",
                    "items_expected_count": len(items),
                    "items_returned_count": len(safe_items),
                    "items": safe_items,
                }
            )
            raise ResourceItemIncomplete("module_item_invalid", result)
        item_id = _safe_id(item.get("id"))
        title = _safe_text(item.get("title"))
        if item_id is None or not title:
            result.update(
                {
                    "items_complete": False,
                    "items_error": "invalid_item",
                    "items_expected_count": len(items),
                    "items_returned_count": len(safe_items),
                    "items": safe_items,
                }
            )
            raise ResourceItemIncomplete("module_item_invalid", result)
        safe_item: dict[str, Any] = {"id": item_id, "title": title}
        for field in ("type", "position", "published", "content_id"):
            value = _safe_scalar(item.get(field))
            if value is not None:
                safe_item[field] = value
        safe_items.append(safe_item)

    result["items"] = safe_items
    result["items_returned_count"] = len(safe_items)
    if "items_count" not in record:
        if (
            record.get("items_available") is True
            and record.get("items_unavailable") is False
            and record.get("items_count_available") is True
            and record.get("items_returned_count") == len(safe_items)
            and record.get("items_complete") is True
        ):
            result["items_count_available"] = True
            result["items_complete"] = True
            return result
        result.update(
            {
                "items_complete": False,
                "items_error": "count_unavailable",
                "items_expected_count": None,
            }
        )
        raise ResourceItemIncomplete("module_items_count_missing", result)
    expected_count = record["items_count"]
    if type(expected_count) is not int or expected_count < 0:
        result.update({"items_complete": False, "items_error": "invalid_items_count"})
        raise ResourceItemIncomplete("module_items_count_invalid", result)
    if expected_count != len(safe_items):
        result.update(
            {
                "items_complete": False,
                "items_error": "count_mismatch",
                "items_expected_count": expected_count,
            }
        )
        raise ResourceItemIncomplete("module_items_count_mismatch", result)
    result["items_complete"] = True
    return result


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    pattern = re.compile(r"<([^>]+)>\s*;\s*rel\s*=\s*\"?([^\";,]+)", re.IGNORECASE)
    for match in pattern.finditer(link_header):
        relations = {part.strip().casefold() for part in match.group(2).split()}
        if "next" in relations:
            return match.group(1)
    return None


def _query_pairs(query: str) -> list[tuple[str, str]]:
    pairs = parse_qsl(query, keep_blank_values=True)
    if len({key for key, _value in pairs}) != len(pairs):
        raise ProbeError("unsafe_pagination")
    return pairs


def _validate_url(url: str, *, expected_path: str | None = None) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != CANVAS_SCHEME
        or parsed.netloc != CANVAS_NETLOC
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(API_PREFIX)
    ):
        raise ProbeError("unsafe_url")
    if expected_path is not None and parsed.path != expected_path:
        raise ProbeError("unsafe_pagination")
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in SENSITIVE_QUERY_KEYS:
            raise ProbeError("unsafe_url")
    return url


def _build_url(path: str, params: Sequence[tuple[str, str | int]]) -> str:
    if not path.startswith(API_PREFIX) or "?" in path or "#" in path:
        raise ProbeError("unsafe_url")
    query = urlencode(params, doseq=True)
    return _validate_url(urlunsplit((CANVAS_SCHEME, CANVAS_NETLOC, path, query, "")))


def _validate_pagination_url(url: str, *, expected_path: str) -> str:
    try:
        return _validate_url(url, expected_path=expected_path)
    except ProbeError as exc:
        if exc.code == "unsafe_url":
            raise ProbeError("unsafe_pagination") from None
        raise


class CanvasClient:
    def __init__(
        self,
        token: str,
        *,
        opener: Any,
        clock: Callable[[], float] = time.monotonic,
        run_seconds: float = MAX_RUN_SECONDS,
    ) -> None:
        self._token = validate_access_token(token)
        self._opener = opener
        self._clock = clock
        self._deadline = clock() + run_seconds

    def get_json(self, url: str) -> tuple[Any, str | None]:
        _validate_url(url)
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise ProbeError("deadline", incomplete=True)
        timeout = min(MAX_SOCKET_TIMEOUT_SECONDS, remaining)
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            method="GET",
        )
        response: Any = None
        try:
            response = self._opener.open(request, timeout=timeout)
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            status = _validated_http_status(status)
            if 300 <= status < 400:
                raise ProbeError("redirect_rejected")
            headers = getattr(response, "headers", None)
            if not 200 <= status < 300:
                raise HttpProbeError(status, headers)
            content_type = headers.get("Content-Type") if headers is not None else None
            media_type = content_type.split(";", 1)[0].strip().casefold() if content_type else ""
            if media_type != "application/json":
                raise ProbeError("non_json_content_type")
            content_length = headers.get("Content-Length") if headers is not None else None
            if content_length is not None:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise ProbeError("response_too_large")
                except ValueError as exc:
                    raise ProbeError("invalid_response") from exc
            body = self._read_bounded(response)
            try:
                data = json.loads(
                    body.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                    parse_float=_parse_json_float,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProbeError("invalid_json") from exc
            link_header = headers.get("Link") if headers is not None else None
            return data, _parse_next_link(link_header)
        except ProbeError:
            raise
        except TimeoutError:
            raise ProbeError("socket_timeout", incomplete=True) from None
        except HTTPError as exc:
            try:
                status = _validated_http_status(exc.code)
                if 300 <= status < 400:
                    raise ProbeError("redirect_rejected") from None
                raise HttpProbeError(status, exc.headers) from None
            finally:
                exc.close()
        except URLError:
            raise ProbeError("network_error") from None
        except OSError:
            raise ProbeError("network_error") from None
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()

    def _read_bounded(self, response: Any) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            if self._clock() >= self._deadline:
                raise ProbeError("deadline", incomplete=True)
            chunk = response.read(min(READ_CHUNK_BYTES, MAX_RESPONSE_BYTES - total + 1))
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise ProbeError("invalid_response")
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ProbeError("response_too_large")
            chunks.append(chunk)
        if self._clock() > self._deadline:
            raise ProbeError("deadline", incomplete=True)
        return b"".join(chunks)


def fetch_paginated(
    client: CanvasClient,
    *,
    path: str,
    params: Sequence[tuple[str, str | int]],
    sanitizer: Callable[[Any], dict[str, Any]],
) -> ResourceResult:
    url = _build_url(path, params)
    original_pairs = _query_pairs(urlsplit(url).query)
    items: list[dict[str, Any]] = []
    pages = 0
    while True:
        try:
            page, next_url = client.get_json(url)
        except ProbeError as exc:
            if exc.incomplete:
                return ResourceResult(items, False, exc.code)
            raise
        if not isinstance(page, list):
            raise ProbeError("invalid_resource_shape")
        pages += 1
        for record in page:
            if len(items) >= MAX_ITEMS:
                return ResourceResult(items, False, "item_cap")
            try:
                items.append(sanitizer(record))
            except ResourceItemIncomplete as exc:
                items.append(exc.item)
                return ResourceResult(items, False, exc.code)
        if next_url is None:
            return ResourceResult(items, True)
        if pages >= MAX_PAGES:
            return ResourceResult(items, False, "page_cap")
        _validate_pagination_url(next_url, expected_path=path)
        next_pairs = _query_pairs(urlsplit(next_url).query)
        expected_pairs = original_pairs + [("page", str(pages + 1))]
        if sorted(next_pairs) != sorted(expected_pairs):
            raise ProbeError("unsafe_pagination")
        url = _build_url(path, expected_pairs)


def _validate_date_range(start_date: str, end_date: str) -> None:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if end < start or (end - start).days + 1 > MAX_ANNOUNCEMENT_DAYS:
        raise ProbeError("invalid_date_range")


def run_probe(
    args: argparse.Namespace,
    token: str,
    *,
    opener: Any,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    _validate_date_range(args.start_date, args.end_date)
    client = CanvasClient(token, opener=opener, clock=clock)
    course_path = f"{API_PREFIX}courses/{args.course_id}"
    try:
        course_data, _ = client.get_json(_build_url(course_path, [("include[]", "term")]))
    except HttpProbeError as exc:
        return {"status": "failed", **exc.diagnostics("course")}
    course = verify_course(
        course_data,
        course_id=args.course_id,
        expected_name=args.expected_name,
        expected_code=args.expected_code,
        expected_term=args.expected_term,
    )
    result: dict[str, Any] = {"status": "complete", "course": course}

    def sanitize_target_announcement(record: Any) -> dict[str, Any]:
        return sanitize_announcement(
            record, expected_context_code=f"course_{args.course_id}"
        )

    resources: list[tuple[str, str, Sequence[tuple[str, str | int]], Callable[[Any], dict[str, Any]]]] = [
        (
            "assignments",
            f"{API_PREFIX}courses/{args.course_id}/assignments",
            [("order_by", "name"), ("per_page", PER_PAGE)],
            sanitize_assignment,
        ),
        (
            "announcements",
            f"{API_PREFIX}announcements",
            [
                ("context_codes[]", f"course_{args.course_id}"),
                ("start_date", args.start_date),
                ("end_date", args.end_date),
                ("per_page", PER_PAGE),
            ],
            sanitize_target_announcement,
        ),
    ]
    if args.include_files:
        resources.append(
            (
                "files",
                f"{API_PREFIX}courses/{args.course_id}/files",
                [("per_page", PER_PAGE)],
                sanitize_file,
            )
        )
    if args.include_modules:
        resources.append(
            (
                "modules",
                f"{API_PREFIX}courses/{args.course_id}/modules",
                [("include[]", "items"), ("per_page", PER_PAGE)],
                sanitize_module,
            )
        )
    for name, path, params, sanitizer in resources:
        try:
            resource = fetch_paginated(client, path=path, params=params, sanitizer=sanitizer)
        except HttpProbeError as exc:
            result.update(
                status="incomplete",
                **exc.diagnostics(name),
                incomplete_resources=[name],
                incomplete_reasons={name: "http_error"},
            )
            break
        result[name] = resource.items
        if not resource.complete:
            result["status"] = "incomplete"
            result["incomplete_resources"] = [name]
            result["incomplete_reasons"] = {name: resource.reason or "bounded"}
            break
    return result


def _aside_request_script(request: dict[str, Any]) -> str:
    """Create the fixed, data-only script executed inside an Aside REPL.

    The script deliberately owns JSON parsing and field selection.  Only the
    bounded canonical frame reaches Python; response bodies and browser errors
    never cross the process boundary.
    """
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return r'''await (async()=>{
const R=__REQUEST__;
const CAP=2097152, TEXT=500;
const fail=(code)=>{console.log("ULS_ASIDE_ERROR:"+code);};
const scalar=(v)=>{if(v===null||typeof v==="boolean"||typeof v==="number"&&Number.isInteger(v)||typeof v==="string")return typeof v==="string"?v.slice(0,TEXT):v;return null;};
const optionalScalar=(v)=>{if(v===undefined||v===null)return null;let x=scalar(v);if(x===null)throw Error("item");return x;};
const sid=(v)=>Number.isInteger(v)&&v>0?v:null;
const safeText=(v)=>typeof v==="string"&&v.trim()?v.slice(0,TEXT):null;
const originOf=(u)=>{try{return new URL(u).origin;}catch(_){return null;}};
const readBody=async(response)=>{if(!response.body||typeof response.body.getReader!=="function")throw Error("body");let r=response.body.getReader(),parts=[],n=0;for(;;){let x=await r.read();if(x.done)break;if(!x.value)continue;n+=x.value.byteLength;if(n>CAP)throw Error("cap");parts.push(Buffer.from(x.value));}return Buffer.concat(parts,n).toString("utf8");};
const parse=(text)=>{let p="while(1);";if(text.startsWith(p)){text=text.slice(p.length);if(text.startsWith(p))throw Error("json");}let v=JSON.parse(text);return v;};
const header=(h,k)=>{try{return h.get(k)||"";}catch(_){return "";}};
const nextLinks=(value)=>{let m=/<([^>]+)>\s*;\s*rel\s*=\s*["']?([^"';,]+)/ig,x,out=[];while((x=m.exec(value||""))){if(x[2].split(/\s+/).map(s=>s.toLowerCase()).includes("next"))out.push(x[1]);}return out;};
const canonical=(path,pairs,page)=>{let u=new URL(R.origin+path);for(let p of pairs)u.searchParams.append(p[0],String(p[1]));if(page!==null)u.searchParams.append("page",String(page));return u.toString();};
const exactQuery=(u,pairs,page)=>{let actual=Array.from(u.searchParams.entries()),expected=pairs.map(p=>[p[0],String(p[1])]);if(page!==null)expected.push(["page",String(page)]);let sort=(a,b)=>a[0].localeCompare(b[0])||a[1].localeCompare(b[1]);actual.sort(sort);expected.sort(sort);return JSON.stringify(actual)===JSON.stringify(expected);};
const get=async(url,path)=>{if(originOf(url)!==R.origin||new URL(url).pathname!==path)throw Error("url");let response=await fetch(url,{method:"GET",redirect:"error"});if(response.url!==url)throw Error("redirect");if(response.status<200||response.status>=300)throw Error("http");let ct=header(response.headers,"content-type").split(";",1)[0].trim().toLowerCase();if(ct!=="application/json")throw Error("content_type");let text=await readBody(response),data=parse(text);return {data,link:header(response.headers,"link"),contentType:ct};};
const pages=async(path,pairs,sanitize)=>{let out=[];for(let i=0;i<3;i++){let u=canonical(path,pairs,i===0?null:i+1),x=await get(u,path);if(!Array.isArray(x.data))throw Error("shape");for(let item of x.data){if(out.length>=500)throw Error("item_cap");out.push(sanitize(item));}let links=nextLinks(x.link);if(links.length===0)return out;if(links.length!==1)throw Error("pagination");let next=new URL(links[0]);if(originOf(links[0])!==R.origin||next.pathname!==path||!exactQuery(next,pairs,i+2))throw Error("pagination");}throw Error("page_cap");};
const course=(x)=>{let id=sid(x&&x.id),name=safeText(x&&x.name),code=safeText(x&&x.course_code),term=safeText(x&&x.term&&x.term.name)||safeText(x&&x.term);if(!id||!name||!code||!term)throw Error("identity");return {id,name,course_code:code,term};};
const assignment=(x)=>{let id=sid(x&&x.id),name=safeText(x&&x.name);if(!id||!name)throw Error("item");return {id,name,due_at:optionalScalar(x&&x.due_at)};};
const announcement=(x)=>{let id=sid(x&&x.id),title=safeText(x&&x.title),context=safeText(x&&x.context_code);if(!id||!title||!context)throw Error("item");let o={id,title,context_code:context};for(let k of ["posted_at","published_at","delayed_post_at"]){let v=optionalScalar(x&&x[k]);if(v!==null)o[k]=v;}return o;};
const moduleItem=(x)=>{let id=sid(x&&x.id),title=safeText(x&&x.title);if(!id||!title)throw Error("item");let o={id,title};for(let k of ["type","position","published","content_id"]){let v=optionalScalar(x&&x[k]);if(v!==null)o[k]=v;}return o;};
const module=async(x,cid)=>{let id=sid(x&&x.id),name=safeText(x&&x.name),position=x&&x.position;if(!id||!name||!Number.isInteger(position)||position<=0||!Array.isArray(x.items)||!Number.isInteger(x.items_count)||x.items_count<0)throw Error("module");let items=x.items.map(moduleItem);if(items.length!==x.items_count){let itemPath=new URL(R.origin+"/api/v1/courses/"+cid+"/modules/"+id+"/items").pathname;items=await pages(itemPath,[["per_page","50"]],moduleItem);}if(items.length!==x.items_count||items.length>500)throw Error("module_items_incomplete");return {id,name,position,items_available:true,items_unavailable:false,items_count_available:true,items_returned_count:items.length,items_complete:true,items};};
try{let tabs=await listBrowserTabs();let matches=(Array.isArray(tabs)?tabs:[]).filter(t=>String(t&&t.targetId)===String(R.tabId)&&originOf(t&&t.url)===R.origin);if(matches.length!==1){fail(matches.length?"browser_tab_ambiguous":"browser_tab_unavailable");return;}await attachBrowserTab(String(matches[0].targetId));let result={};
for(let c of R.courses){try{let base=R.origin+"/api/v1/courses/"+encodeURIComponent(String(c.id));let cp=await get(base+"?include%5B%5D=term",new URL(base).pathname);let co=course(cp.data);if(co.id!==c.id||co.name!==c.name||co.term!==c.term)throw Error("identity");if(!c.code){result[String(c.id)]={status:"needs_verification",course:co};continue;}if(co.course_code!==c.code)throw Error("identity");let a=await pages(new URL(base+"/assignments").pathname,[["order_by","name"],["per_page","50"]],assignment);let n=await pages("/api/v1/announcements",[["context_codes[]","course_"+c.id],["start_date",R.startDate],["end_date",R.endDate],["per_page","50"]],announcement);let mr=await pages(new URL(base+"/modules").pathname,[["include[]","items"],["per_page","50"]],(x)=>x);let m=[];for(let x of mr)m.push(await module(x,c.id));for(let x of n)if(x.context_code!=="course_"+c.id)throw Error("context");result[String(c.id)]={status:"complete",course:co,assignments:a,announcements:n,modules:m};}catch(e){let identity=e&&e.message==="identity";result[String(c.id)]={status:identity?"failed":"partial",error:identity?"course_identity_mismatch":"course_collection_incomplete"};}}
let frame={version:1,provenance:"aside-readonly",origin:R.origin,request_url:R.frameUrl,final_url:R.frameUrl,headers:{content_type:"application/json"},courses:result};let bytes=Buffer.from(JSON.stringify(frame));if(bytes.length>524288)throw Error("frame_cap");console.log("ULS_ASIDE_FRAME:"+bytes.toString("base64"));
}catch(_){fail("browser_transport_unavailable");}})();'''.replace("__REQUEST__", encoded)


class AsideReplTransport:
    """Shell-free, bounded transport for the sanctioned Aside REPL CLI."""

    def __init__(self, *, tab_id: str, origin: str, timeout: float = 45.0) -> None:
        if not isinstance(tab_id, str) or not tab_id or len(tab_id) > 200:
            raise ProbeError("browser_tab_invalid")
        if origin != CANVAS_ORIGIN:
            raise ProbeError("origin_not_allowlisted")
        self.tab_id = tab_id
        self.origin = origin
        self.timeout = min(max(float(timeout), 1.0), MAX_RUN_SECONDS)

    def run(self, courses: list[dict[str, Any]], *, start_date: str, end_date: str) -> dict[str, Any]:
        request = {
            "tabId": self.tab_id,
            "origin": self.origin,
            "frameUrl": self.origin,
            "startDate": start_date,
            "endDate": end_date,
            "courses": courses,
        }
        script = _aside_request_script(request)
        stdout_bytes, _stderr_bytes = _run_bounded_aside_repl(script, self.timeout)
        try:
            stdout = stdout_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProbeError("browser_frame_invalid") from exc
        frames = [line[len("ULS_ASIDE_FRAME:"):] for line in stdout.splitlines() if line.startswith("ULS_ASIDE_FRAME:")]
        errors = [line[len("ULS_ASIDE_ERROR:"):] for line in stdout.splitlines() if line.startswith("ULS_ASIDE_ERROR:")]
        if len(frames) != 1 or errors:
            code = errors[0] if len(errors) == 1 and re.fullmatch(r"[a-z_]+", errors[0]) else "browser_frame_invalid"
            raise ProbeError(code)
        try:
            decoded = base64.b64decode(frames[0], validate=True)
            if len(decoded) > MAX_ASIDE_FRAME_BYTES:
                raise ValueError
            frame = json.loads(decoded.decode("utf-8"), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProbeError("browser_frame_invalid") from exc
        parsed = parse_canonical_frame(frame, self.origin)
        parsed["status"] = "complete"
        return parsed


def _run_bounded_aside_repl(script: str, timeout: float) -> tuple[bytes, bytes]:
    """Run Aside while bounding both stdout and stderr before buffering them.

    Uses one blocking-read reader thread per stream instead of selectors
    plus a non-blocking fd. Windows selector backends only support sockets
    (not arbitrary pipe/file objects) and Windows has no os.set_blocking(),
    so the prior selectors-based implementation crashed immediately with
    AttributeError there. A reader thread per stream works identically on
    every platform: each thread only ever blocks on its own stream, and
    the main thread enforces the overall deadline and byte budget by
    polling a queue those threads push chunks onto.
    """
    try:
        process = subprocess.Popen(
            ["aside", "repl", script], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
    except (OSError, ValueError) as exc:
        raise ProbeError("browser_transport_unavailable") from exc
    if process.stdout is None or process.stderr is None:
        raise ProbeError("browser_transport_unavailable")
    deadline = time.monotonic() + timeout

    def kill_and_reap() -> None:
        try:
            process.kill()
        except OSError:
            pass
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            pass

    events: queue.Queue[tuple[str, bytes]] = queue.Queue()

    def reader(name: str, stream: TextIO) -> None:
        try:
            while True:
                chunk = os.read(stream.fileno(), READ_CHUNK_BYTES)
                events.put((name, chunk))
                if not chunk:
                    return
        except OSError:
            events.put((name, b""))

    readers = {"stdout": process.stdout, "stderr": process.stderr}
    threads = [
        threading.Thread(target=reader, args=(name, stream), daemon=True)
        for name, stream in readers.items()
    ]
    for thread in threads:
        thread.start()

    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    open_streams = set(readers)
    try:
        while open_streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_and_reap()
                raise ProbeError("browser_timeout", incomplete=True)
            try:
                name, chunk = events.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if not chunk:
                open_streams.discard(name)
                continue
            buffer = buffers[name]
            if len(buffer) + len(chunk) > MAX_ASIDE_OUTPUT_BYTES:
                kill_and_reap()
                raise ProbeError("browser_output_too_large")
            buffer.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            kill_and_reap()
            raise ProbeError("browser_timeout", incomplete=True)
        try:
            returncode = process.wait(timeout=max(0.0, remaining - ASIDE_REAP_GRACE_SECONDS))
        except subprocess.TimeoutExpired as exc:
            kill_and_reap()
            raise ProbeError("browser_timeout", incomplete=True) from exc
        if returncode != 0:
            raise ProbeError("browser_transport_failed")
        return bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except ProbeError:
        raise
    except (OSError, ValueError) as exc:
        kill_and_reap()
        raise ProbeError("browser_transport_unavailable") from exc
    finally:
        for stream in readers.values():
            try:
                stream.close()
            except OSError:
                pass
        for thread in threads:
            thread.join(timeout=1.0)


def _frame_text(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_LENGTH:
        raise ProbeError("browser_frame_invalid")
    if any(ord(char) < 32 for char in value):
        raise ProbeError("browser_frame_invalid")
    return value


def _frame_timestamp(value: Any) -> None:
    if value is None:
        return
    text = _frame_text(value)
    if not isinstance(text, str):
        raise ProbeError("browser_frame_invalid")
    if "T" not in text:
        raise ProbeError("browser_frame_invalid")
    try:
        dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProbeError("browser_frame_invalid") from exc


def _frame_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ProbeError("browser_frame_invalid")
    return value


def _validate_frame_assignment(item: Any) -> None:
    allowed = {"id", "name", "due_at", "lock_at", "unlock_at", "position", "published", "locked_for_user"}
    if not isinstance(item, dict) or not {"id", "name", "due_at"} <= set(item) or set(item) - allowed:
        raise ProbeError("browser_frame_invalid")
    _frame_id(item["id"])
    _frame_text(item["name"])
    _frame_timestamp(item["due_at"])
    for field in ("lock_at", "unlock_at"):
        if field in item:
            _frame_timestamp(item[field])
    if "position" in item and (type(item["position"]) is not int or item["position"] < 0):
        raise ProbeError("browser_frame_invalid")
    for field in ("published", "locked_for_user"):
        if field in item and type(item[field]) is not bool:
            raise ProbeError("browser_frame_invalid")


def _validate_frame_announcement(item: Any) -> None:
    allowed = {"id", "title", "context_code", "posted_at", "published_at", "delayed_post_at"}
    if not isinstance(item, dict) or not {"id", "title", "context_code"} <= set(item) or set(item) - allowed:
        raise ProbeError("browser_frame_invalid")
    _frame_id(item["id"])
    _frame_text(item["title"])
    _frame_text(item["context_code"])
    for field in ("posted_at", "published_at", "delayed_post_at"):
        if field in item:
            _frame_timestamp(item[field])


def _validate_frame_module_item(item: Any) -> None:
    allowed = {"id", "title", "type", "position", "published", "content_id"}
    if not isinstance(item, dict) or not {"id", "title"} <= set(item) or set(item) - allowed:
        raise ProbeError("browser_frame_invalid")
    _frame_id(item["id"])
    _frame_text(item["title"])
    if "type" in item:
        _frame_text(item["type"])
    if "position" in item and (type(item["position"]) is not int or item["position"] < 0):
        raise ProbeError("browser_frame_invalid")
    if "published" in item and type(item["published"]) is not bool:
        raise ProbeError("browser_frame_invalid")
    if "content_id" in item and not (
        type(item["content_id"]) is int and item["content_id"] > 0
        or isinstance(item["content_id"], str) and _frame_text(item["content_id"]) is not None
    ):
        raise ProbeError("browser_frame_invalid")


def _validate_frame_module(module: Any) -> None:
    allowed = {"id", "name", "position", "items_available", "items_unavailable", "items_count_available", "items_returned_count", "items_complete", "items"}
    if not isinstance(module, dict) or set(module) != allowed:
        raise ProbeError("browser_frame_invalid")
    items = module.get("items")
    if not isinstance(items, list):
        raise ProbeError("browser_frame_invalid")
    _frame_id(module["id"])
    _frame_text(module["name"])
    if type(module["position"]) is not int or module["position"] <= 0:
        raise ProbeError("browser_frame_invalid")
    if module["items_available"] is not True or module["items_unavailable"] is not False or module["items_count_available"] is not True or module["items_complete"] is not True:
        raise ProbeError("browser_frame_invalid")
    if type(module["items_returned_count"]) is not int or module["items_returned_count"] < 0 or module["items_returned_count"] != len(items):
        raise ProbeError("browser_frame_invalid")
    if len(items) > MAX_ITEMS:
        raise ProbeError("browser_frame_invalid")
    for item in items:
        _validate_frame_module_item(item)


def parse_canonical_frame(frame: Any, origin: str = CANVAS_ORIGIN) -> dict[str, Any]:
    """Revalidate only the sanitized frame emitted by the Aside script."""
    if not isinstance(frame, dict) or set(frame) != {
        "version", "provenance", "origin", "request_url", "final_url", "headers", "courses"
    } or frame.get("version") != 1 or frame.get("provenance") != "aside-readonly":
        raise ProbeError("browser_frame_invalid")
    if frame.get("origin") != origin or frame.get("final_url") != frame.get("request_url") or frame.get("request_url") != origin:
        raise ProbeError("browser_identity_mismatch")
    if frame.get("headers") != {"content_type": "application/json"}:
        raise ProbeError("browser_frame_invalid")
    courses = frame.get("courses")
    if not isinstance(courses, dict):
        raise ProbeError("browser_frame_invalid")
    for key, value in courses.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key) or not isinstance(value, dict):
            raise ProbeError("browser_frame_invalid")
        status = value.get("status")
        if status in {"partial", "failed"}:
            if set(value) != {"status", "error"} or value.get("error") not in {
                "course_collection_incomplete", "course_identity_mismatch"
            }:
                raise ProbeError("browser_frame_invalid")
            continue
        expected = {"status", "course"} | ({"assignments", "announcements", "modules"} if status == "complete" else set())
        if status not in {"complete", "needs_verification"} or set(value) != expected:
            raise ProbeError("browser_frame_invalid")
        course = value.get("course")
        if not isinstance(course, dict) or set(course) != {"id", "name", "course_code", "term"}:
            raise ProbeError("browser_frame_invalid")
        if _frame_id(course.get("id")) != int(key):
            raise ProbeError("browser_frame_invalid")
        for field in ("name", "course_code", "term"):
            _frame_text(course.get(field))
        if status == "complete":
            assignments = value.get("assignments")
            announcements = value.get("announcements")
            modules = value.get("modules")
            if not isinstance(assignments, list) or not isinstance(announcements, list) or not isinstance(modules, list):
                raise ProbeError("browser_frame_invalid")
            for item in assignments:
                _validate_frame_assignment(item)
            for item in announcements:
                _validate_frame_announcement(item)
            for module in modules:
                _validate_frame_module(module)
    result: dict[str, Any] = {}
    for key, value in frame.items():
        if isinstance(key, str):
            result[key] = value
    return result


def run_browser_registry(
    registry: list[dict[str, Any]], *, tab_id: str, owner_id: str, scope_hash: str,
    start_date: str, end_date: str,
) -> dict[str, Any]:
    """Public production entry point for one reserved Aside semester run."""
    return run_registry_aside(
        registry, tab_id=tab_id, owner_id=owner_id, scope_hash=scope_hash,
        start_date=start_date, end_date=end_date,
    )


def _write_json(stream: TextIO, payload: dict[str, Any]) -> None:
    serialized = StringIO()
    json.dump(payload, serialized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    stream.write(serialized.getvalue())
    stream.write("\n")


def _write_json_path(path_value: str | None, payload: dict[str, Any], output: TextIO) -> None:
    if path_value is None:
        _write_json(output, payload)
        return
    path = Path(path_value)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ProbeError("output_path_invalid")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _write_json(stream, payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except ProbeError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ProbeError("output_write_failed") from exc


def _read_registry(path: str) -> list[dict[str, Any]]:
    registry_path = Path(path)
    if registry_path.is_symlink() or not registry_path.is_file():
        raise ProbeError("registry_path_invalid")
    try:
        info = registry_path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ProbeError("registry_permissions_invalid")
        fd = os.open(registry_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            document = json.load(stream)
        fd = -1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError("registry_unreadable") from exc
    if (
        not isinstance(document, dict)
        or document.get("version") != "knu-lms-semester-registry.v1"
        or document.get("semester") != "2026-2"
    ):
        raise ProbeError("registry_invalid")
    courses = document.get("courses")
    if not isinstance(courses, list) or not courses:
        raise ProbeError("registry_invalid")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for course in courses:
        if not isinstance(course, dict):
            raise ProbeError("registry_invalid")
        course_id = course.get("id")
        if type(course_id) is not int or course_id <= 0 or course_id in seen:
            raise ProbeError("registry_invalid")
        name = course.get("name")
        term = course.get("term")
        code = course.get("code")
        if not isinstance(name, str) or not name.strip() or not isinstance(term, str) or not term.strip():
            raise ProbeError("registry_invalid")
        if code is not None and (not isinstance(code, str) or not code.strip()):
            raise ProbeError("registry_invalid")
        verification_state = course.get("verification_state")
        if verification_state not in {
            "api_code_verified", "identity_observed", "observed_candidate", "needs_verification", "verified"
        }:
            raise ProbeError("registry_invalid")
        if course.get("origin", CANVAS_ORIGIN) != CANVAS_ORIGIN:
            raise ProbeError("origin_not_allowlisted")
        if type(course.get("academic_import")) is not bool:
            raise ProbeError("registry_invalid")
        positions = course.get("module_positions")
        if positions is not None and (
            not isinstance(positions, list)
            or any(type(position) is not int or position <= 0 for position in positions)
            or len(set(positions)) != len(positions)
        ):
            raise ProbeError("registry_invalid")
        seen.add(course_id)
        result.append({
            "id": course_id,
            "name": name,
            "code": code,
            "term": term,
            "origin": CANVAS_ORIGIN,
            "academic_import": course["academic_import"],
            "verification_state": verification_state,
            **({"module_positions": positions} if positions is not None else {}),
        })
    return result


def _expected_registry_scope_hash(registry: list[dict[str, Any]]) -> str:
    """Use the sync registry validator as the single scope-hash authority."""
    try:
        module_path = Path(__file__).resolve().with_name("knu_lms_sync.py")
        spec = importlib.util.spec_from_file_location("knu_lms_sync_for_scope", module_path)
        if spec is None or spec.loader is None:
            raise ProbeError("registry_invalid")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        document = {"version": "knu-lms-semester-registry.v1", "semester": "2026-2", "courses": registry}
        scope_hash = module.registry_from_document(document).scope_hash(transport="aside-readonly")
        if not isinstance(scope_hash, str):
            raise ProbeError("registry_invalid")
        return scope_hash
    except ProbeError:
        raise
    except Exception as exc:
        raise ProbeError("registry_invalid") from exc


def _aside_participant(owner_id: str, scope_hash: str) -> None:
    """Delegate the pre/postflight reservation check to the root lock module."""
    try:
        import importlib.util
        from pathlib import Path

        module_path = Path(__file__).resolve().with_name("knu_lms_sync.py")
        spec = importlib.util.spec_from_file_location("knu_lms_sync_for_probe", module_path)
        if spec is None or spec.loader is None:
            raise ProbeError("run_busy")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module._ensure_participant(owner_id, scope_hash)
    except ProbeError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and re.fullmatch(r"[a-z_]+", code):
            raise ProbeError(code) from None
        raise ProbeError("reservation_binding_mismatch") from exc


def run_registry_aside(
    registry: list[dict[str, Any]],
    *,
    tab_id: str,
    owner_id: str,
    scope_hash: str,
    start_date: str,
    end_date: str,
    transport: AsideReplTransport | None = None,
    bootstrap_targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect the explicit registry through one authenticated Aside session."""
    if not re.fullmatch(r"[0-9a-f]{64}", scope_hash):
        raise ProbeError("scope_hash_invalid")
    if scope_hash != _expected_registry_scope_hash(registry):
        raise ProbeError("scope_hash_mismatch")
    _aside_participant(owner_id, scope_hash)
    selected_registry = registry if bootstrap_targets is None else bootstrap_targets
    selected = [
        {
            "id": course["id"],
            "name": course["name"],
            "code": course["code"],
            "term": course["term"],
        }
        for course in selected_registry
        if course["academic_import"]
    ]
    selected_transport = transport or AsideReplTransport(tab_id=tab_id, origin=CANVAS_ORIGIN)
    frame = selected_transport.run(selected, start_date=start_date, end_date=end_date)
    _aside_participant(owner_id, scope_hash)
    frame["owner_id"] = owner_id
    frame["scope_hash"] = scope_hash
    frame["registry_course_count"] = len(registry)
    return frame


def _registry_main(
    argv: Sequence[str], *, output: TextIO, error: TextIO, stdin: Any
) -> int:
    parser = SanitizedArgumentParser(prog="knu_lms_probe")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SanitizedArgumentParser)
    for name in ("collect", "bootstrap"):
        command = sub.add_parser(name)
        command.add_argument("--transport", choices=("aside", "fixture"), default="aside")
        command.add_argument("--registry", required=True)
        command.add_argument("--owner-id", required=True)
        command.add_argument("--scope-hash", required=True)
        command.add_argument("--aside-tab-id")
        command.add_argument("--input")
        command.add_argument("--start-date", type=calendar_date)
        command.add_argument("--end-date", type=calendar_date)
        command.add_argument("--output")
    args = parser.parse_args(list(argv))
    full_registry = _read_registry(args.registry)
    bootstrap_targets: list[dict[str, Any]] | None = None
    if args.command == "bootstrap":
        # Bootstrap is identity-only: the JS request is intentionally the same
        # bounded course GET but the caller receives no child resource data.
        bootstrap_targets = [course for course in full_registry if course["code"] is None]
    expected_scope_hash = _expected_registry_scope_hash(full_registry)
    if not re.fullmatch(r"[0-9a-f]{64}", args.scope_hash):
        raise ProbeError("scope_hash_invalid")
    if args.scope_hash != expected_scope_hash:
        raise ProbeError("scope_hash_mismatch")
    _aside_participant(args.owner_id, args.scope_hash)
    if args.transport == "fixture":
        if not args.input:
            raise ProbeError("fixture_input_required")
        frame: Any
        try:
            with open(args.input, "r", encoding="utf-8") as stream:
                frame = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProbeError("fixture_unreadable") from exc
        if not isinstance(frame, dict) or not isinstance(frame.get("courses"), dict):
            raise ProbeError("fixture_invalid")
        if frame.get("scope_hash") != args.scope_hash:
            raise ProbeError("scope_hash_mismatch")
        if bootstrap_targets is not None:
            target_ids = {str(course["id"]) for course in bootstrap_targets}
            if set(frame["courses"]) - target_ids:
                raise ProbeError("fixture_invalid")
        frame["provenance"] = "fixture-synthetic"
        frame["owner_id"] = args.owner_id
        _write_json_path(args.output, frame, output)
        return 0
    if not args.aside_tab_id:
        raise ProbeError("browser_tab_required")
    if (args.start_date is None) != (args.end_date is None):
        raise ProbeError("invalid_date_range")
    if args.start_date is None:
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        args.start_date = (today - dt.timedelta(days=MAX_ANNOUNCEMENT_DAYS - 2)).isoformat()
        args.end_date = (today + dt.timedelta(days=1)).isoformat()
    _validate_date_range(args.start_date, args.end_date)
    result = run_registry_aside(
        full_registry,
        tab_id=args.aside_tab_id,
        owner_id=args.owner_id,
        scope_hash=args.scope_hash,
        start_date=args.start_date or "1970-01-01",
        end_date=args.end_date or "1970-01-01",
        bootstrap_targets=bootstrap_targets,
    )
    if args.command == "bootstrap":
        result = {"status": "complete", "provenance": result.get("provenance"), "candidates": result.get("courses", {})}
    _write_json_path(args.output, result, output)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Any = None,
    prompt: Callable[[str], str] | None = None,
    stdin: Any = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    output_stream: TextIO = sys.stdout if output is None else output
    error_stream: TextIO = sys.stderr if error is None else error
    stdin = sys.stdin if stdin is None else stdin
    prompt = getpass.getpass if prompt is None else prompt
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv and effective_argv[0] in {"collect", "bootstrap"}:
        try:
            return _registry_main(effective_argv, output=output_stream, error=error_stream, stdin=stdin)
        except ProbeError as exc:
            _write_json(output_stream, {"status": "failed", "error": exc.code})
            error_stream.write(f"probe_error={exc.code}\n")
            return 2
        except Exception:  # noqa: BLE001 - fixed public CLI boundary
            _write_json(output_stream, {"status": "failed", "error": "unexpected_error"})
            error_stream.write("probe_error=unexpected_error\n")
            return 2
    try:
        args = build_parser().parse_args(effective_argv)
    except ArgumentError as exc:
        error_stream.write(f"probe_error={exc.code}\n")
        return 2
    except SystemExit:
        raise
    token: str | None = None
    try:
        if not stdin.isatty():
            raise ProbeError("credential_tty_required")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                token = prompt("Canvas access token: ")
            except getpass.GetPassWarning:
                raise ProbeError("credential_noecho_unavailable") from None
            except (EOFError, KeyboardInterrupt):
                raise ProbeError("credential_input_aborted") from None
        if not token:
            raise ProbeError("credential_empty")
        if opener is None:
            opener = build_opener(NoRedirectHandler())
        result = run_probe(args, token, opener=opener, clock=clock)
        _write_json(output_stream, result)
        if result.get("error") == "http_error":
            error_stream.write(
                f"probe_{result['status']}=http_error http_status={result['http_status']} "
                f"resource={result['resource']} "
                f"auth_challenge_present={str(result['auth_challenge_present']).lower()}\n"
            )
            return 3 if result["status"] == "incomplete" else 2
        if result["status"] == "incomplete":
            error_stream.write("probe_incomplete=bounded\n")
            return 3
        return 0
    except KeyboardInterrupt:
        payload = {"status": "failed", "error": "interrupted"}
        _write_json(output_stream, payload)
        error_stream.write("probe_error=interrupted\n")
        return 2
    except ProbeError as exc:
        status = "incomplete" if exc.incomplete else "failed"
        payload = {"status": status, "error": exc.code}
        _write_json(output_stream, payload)
        error_stream.write(f"probe_{status}={exc.code}\n")
        return 3 if exc.incomplete else 2
    except Exception:  # noqa: BLE001 - the CLI boundary must never expose transport details
        _write_json(output_stream, {"status": "failed", "error": "unexpected_error"})
        error_stream.write("probe_error=unexpected_error\n")
        return 2
    finally:
        token = None


if __name__ == "__main__":
    raise SystemExit(main())
