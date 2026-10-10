"""Offline contract tests for the Canvas destination and bounded GET boundary."""
from __future__ import annotations

import io
import json
import socket
import ssl
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

import pytest

from uls.settings import canvas_checks as checks
from uls.settings.canvas_checks import CanvasCheckError, CanvasResponse

pytestmark = pytest.mark.contract


class FakeTransport:
    def __init__(self, replies: Iterable[CanvasResponse | Exception] = ()) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, str, dict[str, str], float]] = []
        self.closed = False

    def request(self, method: str, target: str, headers: Mapping[str, str],
                timeout: float) -> CanvasResponse:
        self.requests.append((method, target, dict(headers), timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self) -> None:
        self.closed = True


def response(payload: Any, status: int = 200,
             headers: Mapping[str, str] | None = None) -> CanvasResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return CanvasResponse(status, headers or {}, body)


def build(replies: Iterable[CanvasResponse | Exception] = (),
          addresses: Iterable[str] = ("93.184.216.34",), *,
          on_connect: Callable[[str, int, str, float], None] | None = None
          ) -> tuple[FakeTransport, list[tuple[str, int]], list[tuple[str, int, str, float]],
                     checks.Resolver, checks.Connector]:
    transport = FakeTransport(replies)
    resolutions: list[tuple[str, int]] = []
    connections: list[tuple[str, int, str, float]] = []

    def resolver(host: str, port: int) -> Iterable[str]:
        resolutions.append((host, port))
        return addresses

    def connector(host: str, port: int, address: str, timeout: float) -> checks.CanvasTransport:
        connections.append((host, port, address, timeout))
        if on_connect:
            on_connect(host, port, address, timeout)
        return transport

    return transport, resolutions, connections, resolver, connector


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "192.0.2.1", "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1",
])
def test_forbidden_dns_answer_refuses_before_connection(address: str) -> None:
    transport, _, connections, resolver, connector = build(addresses=[address])
    with pytest.raises(CanvasCheckError) as error:
        checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                     connector=connector)
    assert error.value.code == "DESTINATION_NOT_ALLOWED"
    assert connections == []
    assert transport.requests == []


def test_mixed_public_and_forbidden_answer_is_refused_whole() -> None:
    _, _, connections, resolver, connector = build(addresses=["93.184.216.34", "10.0.0.8"])
    with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
        checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                     connector=connector)
    assert connections == []


def test_dns_change_after_validation_cannot_change_pinned_peer() -> None:
    _, resolutions, connections, resolver, connector = build(
        addresses=["93.184.216.34", "2606:4700:4700::1111"])
    connection = checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                               connector=connector)
    assert len(resolutions) == 1
    assert connections == [("canvas.example.edu", 443, "93.184.216.34", 10.0)]
    connection.close()


def test_tls_uses_origin_hostname_while_tcp_uses_validated_address(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class FakeSocket:
        def close(self) -> None:
            calls["raw_closed"] = True

        def connect(self, peer: tuple[str, int]) -> None:
            calls["peer"] = peer

        def settimeout(self, timeout: float) -> None:
            calls["timeout"] = timeout

    class FakeContext:
        def wrap_socket(self, raw: Any, *, server_hostname: str) -> Any:
            calls["sni"] = server_hostname
            return raw

    connection = checks._PinnedHTTPSConnection("canvas.example.edu", 443,
                                                 "93.184.216.34", 10)
    assert connection._tls_context.verify_mode == ssl.CERT_REQUIRED
    assert connection._tls_context.check_hostname is True
    connection._tls_context = cast(Any, FakeContext())
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS repeated"))
    connection.connect()
    assert calls["peer"] == ("93.184.216.34", 443)
    assert calls["sni"] == "canvas.example.edu"
    assert isinstance(connection.sock, checks._DeadlineSocket)


def test_origin_validation_rejects_ambiguity_and_accepts_normalized_host() -> None:
    assert checks.validate_canvas_origin("HTTPS://Canvas.Example.Edu:443/").origin == \
        "https://canvas.example.edu"
    for origin in ("http://canvas.example.edu", "https://user@canvas.example.edu",
                   "https://canvas.example.edu/path", "https://canvas.example.edu/?x=1",
                   "https://127.0.0.1", "https://localhost"):
        with pytest.raises(CanvasCheckError):
            checks.validate_canvas_origin(origin)


def test_redirect_is_not_followed_and_token_stays_on_single_exact_origin() -> None:
    transport, _, _, resolver, connector = build([
        response({}, 302, {"Location": "https://attacker.example/steal"})])
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError) as error):
        connection.request("GET", "/api/v1/users/self", "sentinel-token")
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert len(transport.requests) == 1
    assert transport.requests[0][2]["Authorization"] == "Bearer sentinel-token"


def test_cross_origin_next_page_is_refused_without_following_it() -> None:
    transport, _, _, resolver, connector = build([
        response([], headers={"Link": '<https://evil.example/api/v1/courses?page=2>; rel="next"'})])
    with checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection:
        first = connection.request("GET", "https://canvas.example.edu/api/v1/courses", "secret")
        with pytest.raises(CanvasCheckError) as error:
            checks._next_link(checks._header(first.headers, "Link"),
                              "https://canvas.example.edu/api/v1/courses",
                              connection.target.origin)
    assert error.value.code == "DESTINATION_NOT_ALLOWED"
    assert len(transport.requests) == 1


def test_every_public_entry_point_uses_destination_contract() -> None:
    cases: list[Callable[[checks.Resolver, checks.Connector], Any]] = [
        lambda r, c: checks.verify_canvas_user("https://canvas.example.edu", "secret",
                                               resolver=r, connector=c),
        lambda r, c: checks.discover_canvas_courses("https://canvas.example.edu", "secret",
                                                    resolver=r, connector=c,
                                                    gate=checks.CanvasDiscoveryGate()),
        lambda r, c: checks.reread_canvas_courses("https://canvas.example.edu", "secret", ["12"],
                                                   resolver=r, connector=c),
    ]
    for run in cases:
        transport, resolutions, connections, resolver, connector = build(addresses=["10.1.1.1"])
        with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
            run(resolver, connector)
        assert resolutions == [("canvas.example.edu", 443)]
        assert connections == []
        assert transport.requests == []


def test_success_entry_points_make_only_bounded_gets() -> None:
    transport, resolutions, _, resolver, connector = build([
        response({"id": 42, "name": "Learner", "email": "private@example.edu"})])
    assert checks.verify_canvas_user("https://canvas.example.edu", "top-secret",
                                     resolver=resolver, connector=connector) == {
        "user_id": "42", "display_name": "Learner"}
    assert resolutions == [("canvas.example.edu", 443)]
    method, target, headers, timeout = transport.requests[0]
    assert (method, target) == ("GET", "/api/v1/users/self")
    assert 0 < timeout <= 10.0
    assert headers == {"Authorization": "Bearer top-secret", "Accept": "application/json"}


def test_discovery_paginates_same_origin_and_caps_pages() -> None:
    pages = [response([{"id": index, "name": "N", "course_code": "C",
                        "term": {"id": 1, "name": "T"}} for index in range(50)],
                      headers={"Link": f'<https://canvas.example.edu/api/v1/courses?enrollment_state=active&include%5B%5D=term&per_page=50&page={page + 2}>; rel="next"'})
             for page in range(4)]
    transport, _, _, resolver, connector = build([response({"id": 42}), *pages])
    result = checks.discover_canvas_courses("https://canvas.example.edu", "secret",
                                            resolver=resolver, connector=connector,
                                            gate=checks.CanvasDiscoveryGate())
    assert len(result["courses"]) == 200
    assert len(transport.requests) == 5
    assert all(item[0] == "GET" for item in transport.requests)
    assert result["terms"] == [{"term_id": "1", "name": "T"}]


def test_secret_never_appears_in_errors_or_exception_repr() -> None:
    sentinel = "PAT_DO_NOT_LEAK"
    transport, _, _, resolver, connector = build([RuntimeError(sentinel)])
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError) as error):
        connection.request("GET", "/api/v1/users/self", sentinel)
    assert sentinel not in str(error.value)
    assert sentinel not in repr(error.value)
    assert error.value.__context__ is None
    assert transport.closed


def test_response_and_request_limits_are_enforced() -> None:
    oversized = CanvasResponse(200, {}, b"x" * (checks.MAX_RESPONSE_BYTES + 1))
    _transport, _, _, resolver, connector = build([oversized])
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError, match="PROVIDER_UNAVAILABLE")):
        connection.request("GET", "/api/v1/users/self", "secret")
    _, _, _, resolver, connector = build([response({})] * (checks.MAX_REQUESTS + 1))
    with checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection:
        for _ in range(checks.MAX_REQUESTS):
            connection.request("GET", "/api/v1/users/self", "secret")
        with pytest.raises(CanvasCheckError, match="PROVIDER_UNAVAILABLE"):
            connection.request("GET", "/api/v1/users/self", "secret")


def test_status_codes_are_fixed_and_token_free() -> None:
    for status, expected in ((401, "INVALID_CREDENTIAL"), (403, "PERMISSION_MISSING"),
                             (404, "NOT_FOUND"), (429, "RATE_LIMITED"),
                             (503, "PROVIDER_UNAVAILABLE")):
        _, _, _, resolver, connector = build([response({"token": "must-not-leak"}, status)])
        with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                           connector=connector) as connection,
              pytest.raises(CanvasCheckError) as error):
            connection.request("GET", "/api/v1/users/self", "sentinel")
        assert error.value.code == expected
        assert "sentinel" not in str(error.value)


def test_selected_course_reread_caps_at_twenty() -> None:
    _, _, _, resolver, connector = build([])
    with pytest.raises(CanvasCheckError, match="NOT_FOUND"):
        checks.reread_canvas_courses("https://canvas.example.edu", "secret",
                                     [str(index) for index in range(21)],
                                     resolver=resolver, connector=connector)


@pytest.mark.parametrize("url", [
    "/api/v1/courses/12/files", "/api/v1/users/self/activity_stream",
    "/api/v1/courses?include[]=grades", "/api/v1/courses?per_page=100",
    "/api/v1/courses/12?mark_read=true",
])
def test_only_read_only_metadata_paths_and_queries_are_allowed(url: str) -> None:
    transport, _, _, resolver, connector = build()
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError, match="CHECK_NOT_READ_ONLY")):
        connection.request("GET", url, "secret")
    assert transport.requests == []


def test_methods_and_other_origins_are_rejected_before_authorization() -> None:
    transport, _, _, resolver, connector = build()
    with checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection:
        with pytest.raises(CanvasCheckError, match="CHECK_NOT_READ_ONLY"):
            connection.request("POST", "/api/v1/users/self", "secret")
        with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
            connection.request("GET", "https://other.example/api/v1/users/self", "secret")
    assert transport.requests == []


def test_origin_idna_and_invalid_ports_controls_and_numeric_spellings() -> None:
    assert checks.validate_canvas_origin("https://bücher.example").host == "xn--bcher-kva.example"
    for origin in ("https://canvas.example.edu:0", "https://canvas.example.edu:",
                   "https://canvas.example.edu?", "https://canvas.example.edu#",
                   "https://canvas.ex\nample.edu", "https://0x7f.1", "https://127.1",
                   "https://[::1]", "https://canvas.example.edu/path"):
        with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
            checks.validate_canvas_origin(origin)


def test_system_resolver_obtains_a_and_aaaa_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, int]] = []

    def lookup(host: str, port: int, *, family: int, type: int) -> Any:
        calls.append((host, port, family))
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    assert tuple(checks._system_resolver("canvas.example.edu", 443)) == (
        "93.184.216.34", "2606:4700:4700::1111")
    assert calls == [("canvas.example.edu", 443, socket.AF_UNSPEC)]


def test_dns_elapsed_time_reduces_first_request_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    transport, _, _, _, connector = build([response({"id": 12})])

    def resolver(host: str, port: int) -> Iterable[str]:
        now[0] += 3
        return ["93.184.216.34"]

    checks.verify_canvas_user("https://canvas.example.edu", "secret", resolver=resolver,
                              connector=connector)
    assert transport.requests[0][3] == 7.0


def test_dns_exhausted_deadline_never_opens_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    transport, _, connections, _, connector = build()

    def resolver(host: str, port: int) -> Iterable[str]:
        now[0] += 11
        return ["93.184.216.34"]

    with pytest.raises(CanvasCheckError, match="TIMEOUT"):
        checks.verify_canvas_user("https://canvas.example.edu", "secret", resolver=resolver,
                                  connector=connector)
    assert connections == []
    assert transport.requests == []


def test_request_timeout_and_expired_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, resolver, connector = build([TimeoutError("secret")])
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError, match="TIMEOUT") as error):
        connection.request("GET", "/api/v1/users/self", "secret")
    assert error.value.__context__ is None
    transport, _, _, resolver, connector = build()
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    with (checks.open_canvas_connection("https://canvas.example.edu", resolver=resolver,
                                       connector=connector) as connection,
          pytest.raises(CanvasCheckError, match="TIMEOUT")):
        connection.request("GET", "/api/v1/users/self", "secret", deadline=99)
    assert transport.requests == []


def test_discovery_total_deadline_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    page = response([], headers={"Link": '<https://canvas.example.edu/api/v1/courses?enrollment_state=active&include%5B%5D=term&per_page=50&page=2>; rel="next"'})

    class SlowTransport(FakeTransport):
        def request(self, method: str, target: str, headers: Mapping[str, str],
                    timeout: float) -> CanvasResponse:
            reply = super().request(method, target, headers, timeout)
            now[0] += 9.5
            return reply

    transport = SlowTransport([response({"id": 12}), page, page, page, page])
    with pytest.raises(CanvasCheckError, match="TIMEOUT"):
        checks.discover_canvas_courses("https://canvas.example.edu", "secret",
                                      resolver=lambda h, p: ["93.184.216.34"],
                                      connector=lambda h, p, ip, t: transport,
                                      gate=checks.CanvasDiscoveryGate())
    assert len(transport.requests) == 5
    assert transport.requests[-1][3] == 7.0
    assert transport.closed


def test_discovery_gate_inflight_cooldown_and_failure_release(
        monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    gate = checks.CanvasDiscoveryGate()
    gate.begin("profile")
    with pytest.raises(CanvasCheckError, match="RATE_LIMITED"):
        gate.begin("profile")
    gate.finish("profile")
    with pytest.raises(CanvasCheckError, match="RATE_LIMITED"):
        gate.begin("profile")
    now[0] += 10
    gate.begin("profile")
    gate.finish("profile")
    gate = checks.CanvasDiscoveryGate()
    with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
        checks.discover_canvas_courses("https://canvas.example.edu", "secret", profile_id="p",
                                      resolver=lambda h, p: ["127.0.0.1"], gate=gate)
    with pytest.raises(CanvasCheckError, match="RATE_LIMITED"):
        gate.begin("p")
    now[0] += 10
    gate.begin("p")


def test_selected_course_reread_checks_provider_id_and_chosen_term() -> None:
    for rows, term in (([{"id": 13, "term": {"id": 1}}], "1"),
                       ([{"id": 12, "term": {"id": 2}}], "1"),
                       ([{"id": 12, "term": {"id": 1}},
                         {"id": 13, "term": {"id": 2}}], None)):
        transport, _, _, resolver, connector = build([response(row) for row in rows])
        ids = ["12", "13"] if len(rows) == 2 else ["12"]
        with pytest.raises(CanvasCheckError, match="NOT_FOUND"):
            checks.reread_canvas_courses("https://canvas.example.edu", "secret", ids,
                                         term_id=term, resolver=resolver, connector=connector)
        assert transport.closed
    _, _, _, resolver, connector = build([response({"id": 12, "term": {"id": 1},
                                                  "name": "Course", "course_code": "C"})])
    result = checks.reread_canvas_courses("https://canvas.example.edu", "secret", ["12"],
                                         term_id="1", resolver=resolver, connector=connector)
    assert result == [{"course_id": "12", "term_id": "1", "term_name": "", "name": "Course", "code": "C"}]


def test_provider_echo_and_invalid_json_never_escape_in_output_or_logs(
        caplog: pytest.LogCaptureFixture) -> None:
    sentinel = "PAT_DO_NOT_LEAK"
    for reply in (response({"id": 12, "name": sentinel}),
                  response(("not-json " + sentinel).encode()), RuntimeError(sentinel)):
        _, _, _, resolver, connector = build([reply])
        with pytest.raises(CanvasCheckError) as error:
            checks.verify_canvas_user("https://canvas.example.edu", sentinel,
                                      resolver=resolver, connector=connector)
        assert sentinel not in repr(error.value)
        assert error.value.__context__ is None
    assert sentinel not in caplog.text


def test_real_http_transport_uses_fake_wire_direct_ip_tls_and_reads_close_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    peers: list[tuple[str, int]] = []
    sni: list[str] = []
    sent: list[bytes] = []
    sockets: list[Any] = []

    class WireSocket:
        def __init__(self) -> None:
            self.timeouts: list[float] = []
            self.stream = io.BytesIO(
                b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\n{"id": 12}')
            sockets.append(self)

        def connect(self, peer: tuple[str, int]) -> None:
            peers.append(peer)

        def settimeout(self, timeout: float) -> None:
            self.timeouts.append(timeout)

        def sendall(self, data: bytes) -> None:
            sent.append(data)

        def makefile(self, mode: str, *, buffering: int = 0) -> io.BytesIO:
            assert (mode, buffering) == ("rb", 0)
            return self.stream

        def close(self) -> None:
            # The socket's real makefile reference keeps the stream alive.
            return

    class Context:
        def wrap_socket(self, raw: Any, *, server_hostname: str) -> Any:
            sni.append(server_hostname)
            return raw

    monkeypatch.setattr(socket, "socket", lambda *args: WireSocket())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS repeated"))
    pinned = checks._PinnedHTTPSConnection("canvas.example.edu", 443, "93.184.216.34", 10)
    pinned._tls_context = cast(Any, Context())
    transport = checks._HTTPTransport(pinned)
    for budget in (9.0, 2.0):
        reply = transport.request("GET", "/api/v1/users/self", {"Authorization": "Bearer sentinel"}, budget)
        assert json.loads(reply.body) == {"id": 12}
    assert peers == [("93.184.216.34", 443)] * 2
    assert sni == ["canvas.example.edu"] * 2
    assert b"Host: canvas.example.edu" in sent[0]
    assert b"Authorization: Bearer sentinel" in sent[0]
    assert all(0 < timeout <= 2 for timeout in sockets[-1].timeouts)


def test_existing_socket_timeout_is_updated_by_http_transport() -> None:
    timeouts: list[float] = []

    class ExistingSocket:
        def settimeout(self, timeout: float) -> None:
            timeouts.append(timeout)

    class Reply:
        status = 200

        def read(self, limit: int) -> bytes:
            assert limit == checks.MAX_RESPONSE_BYTES + 1
            return b"{}"

        def getheaders(self) -> list[tuple[str, str]]:
            return []

    class ExistingConnection:
        sock = ExistingSocket()

        def request(self, *args: Any, **kwargs: Any) -> None:
            return

        def getresponse(self) -> Reply:
            return Reply()

        def close(self) -> None:
            return

    transport = checks._HTTPTransport(cast(Any, ExistingConnection()))
    transport.request("GET", "/api/v1/users/self", {}, 1.25)
    assert timeouts == [1.25]


def test_header_and_body_trickle_reader_uses_total_deadline(
        monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    timeouts: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    class SlowRaw(io.RawIOBase):
        def readinto(self, buffer: Any) -> int:
            buffer[0] = 65
            now[0] += 3
            return 1

    class SlowSocket:
        def makefile(self, *args: Any, **kwargs: Any) -> SlowRaw:
            return SlowRaw()

        def settimeout(self, timeout: float) -> None:
            timeouts.append(timeout)

    reader = checks._DeadlineReader(cast(Any, SlowSocket()), 110.0)
    with pytest.raises(TimeoutError):
        io.BufferedReader(reader).read(5)
    assert timeouts == [10.0, 7.0, 4.0, 1.0]


def test_cross_origin_pagination_in_discovery_is_not_requested() -> None:
    transport, _, _, resolver, connector = build([
        response({"id": 12}), response([], headers={"Link":
            '<https://evil.example/api/v1/courses?page=2>; rel="next"'})])
    with pytest.raises(CanvasCheckError, match="DESTINATION_NOT_ALLOWED"):
        checks.discover_canvas_courses("https://canvas.example.edu", "secret",
                                      resolver=resolver, connector=connector,
                                      gate=checks.CanvasDiscoveryGate())
    assert len(transport.requests) == 2


def test_course_limit_is_independent_of_page_limit() -> None:
    rows = [{"id": index, "term": {"id": 1}, "name": "N"} for index in range(201)]
    transport, _, _, resolver, connector = build([response({"id": 12}), response(rows)])
    result = checks.discover_canvas_courses("https://canvas.example.edu", "secret",
                                           resolver=resolver, connector=connector,
                                           gate=checks.CanvasDiscoveryGate())
    assert len(result["courses"]) == 200
    assert len(transport.requests) == 2


_NEXT_FILTERS = "enrollment_state=active&include%5B%5D=term&per_page=50"


@pytest.mark.parametrize("target", [
    "/api/v1/users/self", "/api/v1/courses/12", "/api/v1/courses/99",
    "/api/v1/courses/12/assignments", "/api/v1/courses/12/files",
    "/api/v1/courses/12/modules", "/api/v1/accounts/12",
    "/api/v1/%63ourses?" + _NEXT_FILTERS,
    "/api/v1/./courses?" + _NEXT_FILTERS,
    "/api/v1/courses?page=2",
    "/api/v1/courses?include%5B%5D=term&per_page=50&page=2",
    "/api/v1/courses?enrollment_state=active&per_page=50&page=2",
    "/api/v1/courses?enrollment_state=active&include%5B%5D=term&page=2",
    "/api/v1/courses?" + _NEXT_FILTERS + "&enrollment_state=active",
    "/api/v1/courses?" + _NEXT_FILTERS + "&include[]=term",
    "/api/v1/courses?" + _NEXT_FILTERS + "&per_page=50",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=2&page=3",
    "/api/v1/courses?enrollment_state=completed&include[]=term&per_page=50",
    "/api/v1/courses?enrollment_state=active&include[]=term&per_page=25",
    "/api/v1/courses?" + _NEXT_FILTERS + "&unknown=value",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=0",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=-1",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=+1",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=１",
    "/api/v1/courses?" + _NEXT_FILTERS + "&page=10001",
])
def test_next_link_cannot_dispatch_outside_active_course_list(target: str) -> None:
    transport, _, _, resolver, connector = build([
        response({"id": 12}),
        response([], headers={"Link": f'<https://canvas.example.edu{target}>; rel="next"'}),
        response([]),
    ])
    with pytest.raises(CanvasCheckError, match="CHECK_NOT_READ_ONLY"):
        checks.discover_canvas_courses("https://canvas.example.edu", "test-pat",
                                      resolver=resolver, connector=connector,
                                      gate=checks.CanvasDiscoveryGate())
    assert [request[1] for request in transport.requests] == [
        "/api/v1/users/self", "/api/v1/courses?" + _NEXT_FILTERS]
    assert transport.closed


@pytest.mark.parametrize("page", ["", "&page=2", "&page=10000"])
def test_valid_next_link_preserves_filters_and_dispatches_metadata_only(page: str) -> None:
    target = "/api/v1/courses?" + _NEXT_FILTERS + page
    transport, _, _, resolver, connector = build([
        response({"id": 12}), response([], headers={
            "Link": f'<https://canvas.example.edu{target}>; rel="next"'}),
        response([{"id": 34, "name": "Course", "term": {"id": 7, "name": "Term"}}]),
    ])
    result = checks.discover_canvas_courses("https://canvas.example.edu", "test-pat",
                                           resolver=resolver, connector=connector,
                                           gate=checks.CanvasDiscoveryGate())
    assert [request[1] for request in transport.requests] == [
        "/api/v1/users/self", "/api/v1/courses?" + _NEXT_FILTERS, target]
    assert result["courses"][0]["course_id"] == "34"
    assert all(request[0] == "GET" for request in transport.requests)
    assert transport.closed
