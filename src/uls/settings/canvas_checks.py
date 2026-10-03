"""Bounded, read-only Canvas API checks behind one public-destination boundary."""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Protocol, Self, cast
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUESTS = 25
MAX_DISCOVERY_PAGES = 4
MAX_DISCOVERY_COURSES = 200
MAX_SELECTED_COURSES = 20
REQUEST_TIMEOUT_SECONDS = 10.0
DISCOVERY_TIMEOUT_SECONDS = 45.0
_FIXED_CODES = frozenset({
    "INVALID_CREDENTIAL", "PERMISSION_MISSING", "NOT_FOUND", "PROVIDER_UNAVAILABLE",
    "TIMEOUT", "RATE_LIMITED", "DESTINATION_NOT_ALLOWED", "CHECK_NOT_READ_ONLY",
})


class CanvasCheckError(Exception):
    """A fixed, secret-free result code suitable for API/UI mapping."""

    def __init__(self, code: str) -> None:
        if code not in _FIXED_CODES:
            raise ValueError("Unsupported Canvas result code")
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"CanvasCheckError({self.code!r})"


@dataclass(frozen=True)
class CanvasOrigin:
    origin: str
    host: str
    port: int


@dataclass(frozen=True)
class CanvasResponse:
    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)


class CanvasTransport(Protocol):
    def request(self, method: str, target: str, headers: Mapping[str, str],
                timeout: float) -> CanvasResponse: ...

    def close(self) -> None: ...


Resolver = Callable[[str, int], Iterable[str]]
Connector = Callable[[str, int, str, float], CanvasTransport]


def validate_canvas_origin(value: str) -> CanvasOrigin:
    """Normalize an HTTPS origin while rejecting authority/path ambiguity."""
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "\\" in value or "?" in value or "#" in value):
        raise CanvasCheckError("DESTINATION_NOT_ALLOWED")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError
        raw_host = parsed.hostname
        if not raw_host or raw_host.endswith("."):
            raise ValueError
        # Reject IP literals before IDNA normalization, including bracketed IPv6.
        try:
            ipaddress.ip_address(raw_host)
        except ValueError:
            pass
        else:
            raise ValueError
        host = raw_host.encode("idna").decode("ascii").lower()
        if (len(host) > 253 or "." not in host or
                any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split("."))):
            raise ValueError
        if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in host.split(".")):
            raise ValueError
        port = parsed.port if parsed.port is not None else 443
        if not 1 <= port <= 65535 or parsed.netloc.endswith(":"):
            raise ValueError
        authority = host if port == 443 else f"{host}:{port}"
        return CanvasOrigin(f"https://{authority}", host, port)
    except (UnicodeError, ValueError):
        pass
    raise CanvasCheckError("DESTINATION_NOT_ALLOWED")


def _system_resolver(host: str, port: int) -> Iterable[str]:
    # getaddrinfo has no portable timeout. Bound the caller's wait; the
    # daemon performs one A+AAAA lookup and never receives a credential.
    result: queue.Queue[tuple[str, ...]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            rows = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC,
                                      type=socket.SOCK_STREAM)
            addresses = tuple(str(row[4][0]) for row in rows)
        except (OSError, UnicodeError):
            addresses = ()
        result.put(addresses)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=REQUEST_TIMEOUT_SECONDS)
    except queue.Empty:
        addresses = None
    if addresses is None:
        raise CanvasCheckError("TIMEOUT")
    if not addresses:
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return addresses


def _admit_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    admitted: list[str] = []
    try:
        for raw in islice(addresses, 129):
            address = ipaddress.ip_address(raw)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                mapped = address.ipv4_mapped
                if (not mapped.is_global or mapped.is_multicast or mapped.is_reserved
                        or mapped.is_unspecified or mapped.is_loopback or mapped.is_link_local):
                    raise CanvasCheckError("DESTINATION_NOT_ALLOWED")
            if (len(admitted) >= 128 or "%" in raw or not address.is_global or address.is_multicast or address.is_reserved
                    or address.is_unspecified or address.is_loopback or address.is_link_local):
                raise CanvasCheckError("DESTINATION_NOT_ALLOWED")
            admitted.append(str(address))
    except (ValueError, TypeError):
        raise CanvasCheckError("DESTINATION_NOT_ALLOWED") from None
    if not admitted:
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return tuple(dict.fromkeys(admitted))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Recompute the socket timeout for every header/body read, including trickles."""

    def __init__(self, sock: ssl.SSLSocket, deadline: float) -> None:
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        # Preserve socket.makefile's reference counting: HTTPConnection can
        # close its socket after headers while the response still reads a body.
        self._reader = cast(io.RawIOBase, sock.makefile("rb", buffering=0))

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        self._sock.settimeout(_remaining(self._deadline))
        count = self._reader.readinto(buffer)
        return count if count is not None else 0

    def close(self) -> None:
        self._reader.close()
        super().close()


class _DeadlineSocket:
    def __init__(self, sock: ssl.SSLSocket, deadline: float) -> None:
        self._sock = sock
        self.deadline = deadline

    def sendall(self, data: bytes) -> None:
        self._sock.settimeout(_remaining(self.deadline))
        self._sock.sendall(data)

    def makefile(self, mode: str) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("Read-only response stream required")
        return io.BufferedReader(_DeadlineReader(self._sock, self.deadline))

    def settimeout(self, timeout: float) -> None:
        self._sock.settimeout(timeout)

    def close(self) -> None:
        self._sock.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection whose TCP peer is pinned while TLS verifies the DNS host."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        self._tls_context = ssl.create_default_context()
        super().__init__(host=host, port=port, timeout=timeout, context=self._tls_context)
        self.set_debuglevel(0)
        self._validated_address = address
        self.deadline = time.monotonic() + timeout

    def connect(self) -> None:
        # create_connection invokes getaddrinfo again even for a numeric IP.
        family = socket.AF_INET6 if ipaddress.ip_address(self._validated_address).version == 6 else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(_remaining(self.deadline))
            raw.connect((self._validated_address, self.port))
            raw.settimeout(_remaining(self.deadline))
            tls = self._tls_context.wrap_socket(raw, server_hostname=self.host)
            self.sock = cast(Any, _DeadlineSocket(tls, self.deadline))
        except BaseException:
            raw.close()
            raise


class _HTTPTransport:
    def __init__(self, connection: _PinnedHTTPSConnection) -> None:
        self._connection = connection

    def request(self, method: str, target: str, headers: Mapping[str, str],
                timeout: float) -> CanvasResponse:
        connection = self._connection
        connection.timeout = timeout
        connection.deadline = time.monotonic() + timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)
            if isinstance(connection.sock, _DeadlineSocket):
                connection.sock.deadline = connection.deadline
        try:
            connection.request(method, target, headers=dict(headers))
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                return CanvasResponse(response.status, {}, b"")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise CanvasCheckError("PROVIDER_UNAVAILABLE")
            return CanvasResponse(response.status, dict(response.getheaders()), body)
        finally:
            # Each subsequent request reconnects to the same validated IP.
            # No response file/socket from an earlier deadline can be reused.
            self.close()

    def close(self) -> None:
        self._connection.close()


def _system_connector(host: str, port: int, address: str, timeout: float) -> CanvasTransport:
    return _HTTPTransport(_PinnedHTTPSConnection(host, port, address, timeout))


def _request_target(origin: CanvasOrigin, url: str) -> str:
    if (len(url) > 8192 or "\\" in url or "#" in url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise CanvasCheckError("DESTINATION_NOT_ALLOWED")
    try:
        parsed = urlsplit(urljoin(origin.origin + "/", url))
        destination = validate_canvas_origin(f"{parsed.scheme}://{parsed.netloc}")
    except (ValueError, CanvasCheckError):
        destination = None
    if destination != origin:
        raise CanvasCheckError("DESTINATION_NOT_ALLOWED")
    path = parsed.path
    if path not in {"/api/v1/users/self", "/api/v1/courses"} and not re.fullmatch(
            r"/api/v1/courses/[0-9]{1,64}", path):
        raise CanvasCheckError("CHECK_NOT_READ_ONLY")
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        allowed = ((key == "include[]" and value == "term")
                   or (path == "/api/v1/courses" and key == "enrollment_state" and value == "active")
                   or (path == "/api/v1/courses" and key in {"per_page", "page"}
                       and len(value) <= 5 and value.isascii() and value.isdecimal()
                       and 0 < int(value) <= (50 if key == "per_page" else 10000)))
        if path == "/api/v1/users/self" or not allowed:
            raise CanvasCheckError("CHECK_NOT_READ_ONLY")
    return urlunsplit(("", "", path, parsed.query, ""))


@dataclass
class CanvasConnection:
    """One origin-bound connection; all requests are GETs carrying one header-only token."""

    target: CanvasOrigin
    transport: CanvasTransport = field(repr=False)
    _request_count: int = 0
    _closed: bool = False
    _first_request_deadline: float | None = None

    def request(self, method: str, url: str, token: str, *, deadline: float | None = None) -> CanvasResponse:
        if method != "GET":
            raise CanvasCheckError("CHECK_NOT_READ_ONLY")
        if self._closed or self._request_count >= MAX_REQUESTS:
            raise CanvasCheckError("PROVIDER_UNAVAILABLE")
        path = _request_target(self.target, url)
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise CanvasCheckError("INVALID_CREDENTIAL")
        remaining = REQUEST_TIMEOUT_SECONDS
        now = time.monotonic()
        if self._request_count == 0 and self._first_request_deadline is not None:
            remaining = min(remaining, self._first_request_deadline - now)
        if deadline is not None:
            remaining = min(remaining, deadline - now)
        if remaining <= 0:
            raise CanvasCheckError("TIMEOUT")
        request_deadline = now + remaining
        self._request_count += 1
        failure_code: str | None = None
        try:
            response = self.transport.request(
                "GET", path, {"Authorization": f"Bearer {token}", "Accept": "application/json"},
                remaining,
            )
        except TimeoutError:
            failure_code = "TIMEOUT"
        except Exception:  # noqa: BLE001 -- redact arbitrary provider/transport failures
            # Transport exceptions may include request headers or provider data.
            failure_code = "PROVIDER_UNAVAILABLE"
        if failure_code is not None:
            raise CanvasCheckError(failure_code)
        if time.monotonic() > request_deadline:
            raise CanvasCheckError("TIMEOUT")
        if response.status == 401:
            raise CanvasCheckError("INVALID_CREDENTIAL")
        if response.status == 403:
            raise CanvasCheckError("PERMISSION_MISSING")
        if response.status == 404:
            raise CanvasCheckError("NOT_FOUND")
        if response.status == 429:
            raise CanvasCheckError("RATE_LIMITED")
        if 300 <= response.status < 400 or response.status >= 500:
            raise CanvasCheckError("PROVIDER_UNAVAILABLE")
        if not 200 <= response.status < 300:
            raise CanvasCheckError("PROVIDER_UNAVAILABLE")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise CanvasCheckError("PROVIDER_UNAVAILABLE")
        return response

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.transport.close()
            except Exception:  # noqa: BLE001 -- cleanup must never expose provider text
                return

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_canvas_connection(origin: str, *, resolver: Resolver | None = None,
                           connector: Connector | None = None) -> CanvasConnection:
    """Validate, resolve A+AAAA once, reject mixed/private answers, then pin a public IP."""
    target = validate_canvas_origin(origin)
    first_deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    failure: str | None = None
    try:
        addresses = _admit_addresses((resolver or _system_resolver)(target.host, target.port))
        if time.monotonic() >= first_deadline:
            raise CanvasCheckError("TIMEOUT")
        transport = (connector or _system_connector)(target.host, target.port, addresses[0],
                                                      REQUEST_TIMEOUT_SECONDS)
    except CanvasCheckError as error:
        failure = error.code
    except TimeoutError:
        failure = "TIMEOUT"
    except Exception:  # noqa: BLE001 -- redact arbitrary DNS/connector failures
        failure = "PROVIDER_UNAVAILABLE"
    if failure:
        raise CanvasCheckError(failure)
    return CanvasConnection(target, transport, _first_request_deadline=first_deadline)


def _json(response: CanvasResponse) -> Any:
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        pass
    raise CanvasCheckError("PROVIDER_UNAVAILABLE")


def _id(value: Any) -> str:
    if (isinstance(value, bool) or not isinstance(value, (str, int))
            or not str(value).isascii() or not str(value).isdecimal() or len(str(value)) > 64):
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return str(value)


def _text(value: Any, token: str) -> str:
    if isinstance(value, str) and token in value:
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return value[:2048] if isinstance(value, str) else ""


def _secret_free(value: Any, token: str) -> Any:
    if any(isinstance(item, str) and token in item for item in value.values()):
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return value


def _user(payload: Any, token: str) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return cast(dict[str, str], _secret_free(
        {"user_id": _id(payload.get("id")), "display_name": _text(payload.get("name"), token)}, token))


def _course(payload: Any, token: str) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("term"), dict):
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    term = payload["term"]
    return cast(dict[str, str], _secret_free({
        "course_id": _id(payload.get("id")), "term_id": _id(term.get("id")),
        "term_name": _text(term.get("name"), token), "name": _text(payload.get("name"), token),
        "code": _text(payload.get("course_code"), token),
    }, token))


def verify_canvas_user(origin: str, token: str, *, resolver: Resolver | None = None,
                       connector: Connector | None = None,
                       deadline: float | None = None) -> dict[str, str]:
    """GET users/self and return only the verified user ID and display name."""
    with open_canvas_connection(origin, resolver=resolver, connector=connector) as connection:
        payload = _json(connection.request("GET", "/api/v1/users/self", token, deadline=deadline))
    return _user(payload, token)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


def _next_link(value: str | None, current_url: str, origin: str) -> str | None:
    if not value:
        return None
    entries = re.findall(r'<([^<>]+)>\s*((?:;[^,]*)*)', value)
    next_urls: list[str] = []
    for url, attributes in entries:
        relation = re.search(r';\s*rel\s*=\s*(?:"([^"]*)"|([^;\s,]+))', attributes)
        if relation and "next" in (relation.group(1) or relation.group(2)).split():
            candidate = urljoin(current_url, url)
            _request_target(validate_canvas_origin(origin), candidate)
            parsed = urlsplit(candidate)
            try:
                fields = parse_qsl(parsed.query, keep_blank_values=True,
                                   strict_parsing=True, max_num_fields=4)
            except ValueError:
                raise CanvasCheckError("CHECK_NOT_READ_ONLY") from None
            query = dict(fields)
            required = {"enrollment_state": "active", "include[]": "term", "per_page": "50"}
            if (parsed.path != "/api/v1/courses" or len(query) != len(fields)
                    or not required.items() <= query.items()
                    or query.keys() - required.keys() - {"page"}):
                raise CanvasCheckError("CHECK_NOT_READ_ONLY")
            # _request_target already enforces a positive bounded ASCII page.
            next_urls.append(candidate)
    if len(next_urls) > 1 or (not entries and "next" in value):
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return next_urls[0] if next_urls else None


class CanvasDiscoveryGate:
    """Session-owned admission for one in-flight discovery and a 10 s cooldown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._finished: dict[str, float] = {}

    def begin(self, profile_id: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._finished = {key: stamp for key, stamp in self._finished.items()
                              if now - stamp < 10.0}
            if profile_id in self._active or profile_id in self._finished:
                raise CanvasCheckError("RATE_LIMITED")
            self._active.add(profile_id)

    def finish(self, profile_id: str) -> None:
        with self._lock:
            self._active.discard(profile_id)
            self._finished[profile_id] = time.monotonic()


_DEFAULT_GATE = CanvasDiscoveryGate()


def discover_canvas_courses(origin: str, token: str, *, resolver: Resolver | None = None,
                            connector: Connector | None = None, profile_id: str | None = None,
                            gate: CanvasDiscoveryGate | None = None, deadline: float | None = None,
                            verify_user: bool = True) -> dict[str, Any]:
    """Profile admission plus bounded metadata discovery; never persists results."""
    admission = gate or _DEFAULT_GATE
    key = profile_id or validate_canvas_origin(origin).origin
    admission.begin(key)
    try:
        return _discover(origin, token, resolver, connector, deadline, verify_user)
    finally:
        admission.finish(key)


def _discover(origin: str, token: str, resolver: Resolver | None,
              connector: Connector | None, deadline: float | None,
              verify_user: bool) -> dict[str, Any]:
    """Verify users/self, then read at most four pages / 200 active courses."""
    deadline = min(time.monotonic() + DISCOVERY_TIMEOUT_SECONDS,
                   deadline if deadline is not None else float("inf"))
    courses: list[dict[str, str]] = []
    terms: dict[str, str] = {}
    with open_canvas_connection(origin, resolver=resolver, connector=connector) as connection:
        if verify_user:
            _user(_json(connection.request("GET", "/api/v1/users/self", token, deadline=deadline)), token)
        current = (connection.target.origin +
                   "/api/v1/courses?enrollment_state=active&include%5B%5D=term&per_page=50")
        for _ in range(MAX_DISCOVERY_PAGES):
            response = connection.request("GET", current, token, deadline=deadline)
            payload = _json(response)
            if not isinstance(payload, list):
                raise CanvasCheckError("PROVIDER_UNAVAILABLE")
            for row in payload:
                if len(courses) >= MAX_DISCOVERY_COURSES:
                    break
                course = _course(row, token)
                courses.append(course)
                if course["term_id"]:
                    terms[course["term_id"]] = course["term_name"]
            next_url = _next_link(_header(response.headers, "Link"), current, connection.target.origin)
            if not next_url or len(courses) >= MAX_DISCOVERY_COURSES:
                break
            current = next_url
    return {"terms": [{"term_id": key, "name": value} for key, value in sorted(terms.items())],
            "courses": courses}


def reread_canvas_courses(origin: str, token: str, course_ids: Iterable[str], *,
                          resolver: Resolver | None = None,
                          connector: Connector | None = None,
                          term_id: str | None = None,
                          deadline: float | None = None) -> list[dict[str, str]]:
    """Re-read no more than 20 selected course IDs before registry persistence."""
    ids = list(islice(course_ids, MAX_SELECTED_COURSES + 1))
    if (not ids or len(ids) > MAX_SELECTED_COURSES
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,64}", item)
                   for item in ids)):
        raise CanvasCheckError("NOT_FOUND")
    if len(set(ids)) != len(ids):
        raise CanvasCheckError("NOT_FOUND")
    deadline = min(time.monotonic() + DISCOVERY_TIMEOUT_SECONDS,
                   deadline if deadline is not None else float("inf"))
    result: list[dict[str, str]] = []
    chosen_term = term_id
    with open_canvas_connection(origin, resolver=resolver, connector=connector) as connection:
        for course_id in ids:
            path = f"/api/v1/courses/{course_id}?include%5B%5D=term"
            course = _course(_json(connection.request("GET", path, token, deadline=deadline)), token)
            if course["course_id"] != course_id:
                raise CanvasCheckError("NOT_FOUND")
            chosen_term = chosen_term if chosen_term is not None else course["term_id"]
            if course["term_id"] != chosen_term:
                raise CanvasCheckError("NOT_FOUND")
            result.append(course)
    return result
