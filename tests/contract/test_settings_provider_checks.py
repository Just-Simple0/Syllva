from __future__ import annotations

import http.client
import io
import json

import pytest

from uls.config.loader import load_config_mapping
from uls.settings.config_service import SettingsServiceError
from uls.settings.credential_roles import ROLES
from uls.settings.provider_checks import (
    MAX_RESPONSE_BYTES,
    MESSAGES,
    OAUTH_URL,
    READ_ONLY_SCOPE,
    BoundedGoogleAuthRequest,
    LiveReadOnlyTransport,
    ProviderChecks,
    TransportCheckBudget,
    structural_credential,
)

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("status,code", [(200, "VERIFIED"), (401, "INVALID_CREDENTIAL"), (403, "PERMISSION_MISSING"), (404, "NOT_FOUND"), (503, "PROVIDER_UNAVAILABLE")])
def test_fixed_error_mapping_and_redaction(status, code, caplog):
    class Transport:
        def request(self, provider, endpoint, value, **kwargs):
            return status, {"secret": "SENTINEL-DO-NOT-ECHO"}
    checks = ProviderChecks(Transport(), cooldown=0)
    if status == 200:
        result = checks.check(ROLES["notion-mcp"], b"SENTINEL-DO-NOT-ECHO", load_config_mapping({}))
        assert result["code"] == code and "SENTINEL" not in str(result)
    else:
        with pytest.raises(SettingsServiceError) as error:
            checks.check(ROLES["notion-mcp"], b"SENTINEL-DO-NOT-ECHO", load_config_mapping({}))
        assert error.value.code == code and "SENTINEL" not in str(error.value)
    assert "SENTINEL" not in caplog.text


def test_exception_text_is_discarded_and_cooldown_enforced():
    class Transport:
        def request(self, *args, **kwargs):
            raise RuntimeError("private-token")
    checks = ProviderChecks(Transport())
    with pytest.raises(SettingsServiceError) as error:
        checks.check(ROLES["notion-mcp"], b"private-token", load_config_mapping({}))
    assert error.value.code == "PROVIDER_UNAVAILABLE" and "private-token" not in str(error.value)
    previous = checks.results["notion-mcp"].copy()
    assert previous == {
        "code": "PROVIDER_UNAVAILABLE",
        "message": MESSAGES["PROVIDER_UNAVAILABLE"],
        "checked_at": previous["checked_at"],
        "resources": [],
    }
    with pytest.raises(SettingsServiceError) as busy:
        checks.check(ROLES["notion-mcp"], b"private-token", load_config_mapping({}))
    assert busy.value.code == "CHECK_BUSY"
    assert checks.results["notion-mcp"] == previous


@pytest.mark.parametrize("failure_kind,expected_code", [
    ("unauthorized", "INVALID_CREDENTIAL"),
    ("unavailable", "PROVIDER_UNAVAILABLE"),
    ("timeout", "TIMEOUT"),
])
def test_last_check_replaces_success_with_fixed_failure_result(failure_kind, expected_code, caplog):
    now = [0.0]
    secret = "PRIVATE-CREDENTIAL-SENTINEL"

    class SequenceTransport:
        calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return 200, {"capabilities": {"canAddChildren": True}}
            if failure_kind == "unauthorized":
                return 401, {"error": secret}
            if failure_kind == "unavailable":
                return 503, {"error": secret}
            raise TimeoutError(secret)

    checks = ProviderChecks(SequenceTransport(), cooldown=5, clock=lambda: now[0])
    role = ROLES["notion-mcp"]
    config = load_config_mapping({})
    success = checks.check(role, secret.encode(), config)
    assert success["code"] == "VERIFIED"
    now[0] = 6.0

    with pytest.raises(SettingsServiceError) as error:
        checks.check(role, secret.encode(), config)
    latest = checks.results[role.slug]
    assert error.value.code == expected_code
    assert latest == {
        "code": expected_code,
        "message": MESSAGES[expected_code],
        "checked_at": latest["checked_at"],
        "resources": [],
    }
    assert isinstance(latest["checked_at"], float)
    assert secret not in str(latest) and secret not in str(error.value) and secret not in caplog.text


def test_write_endpoint_refused_before_any_connection():
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport().request("notion", "/v1/pages", b"fake", version="2025-09-03", timeout=1)
    assert error.value.code == "CHECK_NOT_READ_ONLY"


def test_google_upload_bound_and_structural_loader_is_offline():
    value = {"type": "service_account", "client_email": "fake@example.com", "private_key": "fake",
             "private_key_id": "fake", "project_id": "fake", "token_uri": "https://oauth2.googleapis.com/token"}
    calls = []
    structural_credential(ROLES["google-worker"], json.dumps(value).encode(), google_loader=lambda *a, **k: calls.append(k))
    assert calls[0]["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]
    with pytest.raises(SettingsServiceError):
        structural_credential(ROLES["google-worker"], b"x" * 65537)


@pytest.mark.parametrize("loader_error", [
    RuntimeError("PRIVATE-KEY-SENTINEL"),
    SettingsServiceError("INVALID_CREDENTIAL", "PRIVATE-KEY-SENTINEL", 400),
])
def test_structural_google_loader_exceptions_are_fixed_and_redacted(loader_error, caplog):
    value = _google_payload()
    with pytest.raises(SettingsServiceError) as error:
        structural_credential(ROLES["google-worker"], value,
                              google_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(loader_error))
    assert error.value.code == "INVALID_CREDENTIAL"
    assert str(error.value) == "The credential was rejected. Check it and try again."
    assert error.value.__suppress_context__
    assert "PRIVATE-KEY-SENTINEL" not in str(error.value)
    assert "PRIVATE-KEY-SENTINEL" not in caplog.text


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.read_limits = []

    def getheader(self, name):
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()), None)

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body[:limit]


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.closed = False
        self.sent = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True

    def makefile(self, mode, buffering=None):
        raise AssertionError("the scripted response seam does not use raw socket reads")


class FakeConnection:
    def __init__(self, response, *, before_connect=None):
        self.response = response
        self.before_connect = before_connect
        self.requested = None
        self.request_calls = 0
        self.getresponse_calls = 0
        self.connect_calls = 0
        self.sock = None
        self.raw_socket = None
        self.closed = False

    def connect(self):
        self.connect_calls += 1
        self.raw_socket = FakeSocket()
        self.sock = self.raw_socket
        if self.before_connect:
            self.before_connect()

    def request(self, method, target, body=None, headers=None):
        self.request_calls += 1
        self.requested = (method, target, body, dict(headers or {}))

    def getresponse(self):
        self.getresponse_calls += 1
        return self.response

    def close(self):
        self.closed = True
        if self.sock is not None:
            self.sock.close()


class FakeConnectionFactory:
    def __init__(self, responses, *, before_connect=None):
        self.responses = list(responses)
        self.before_connect = before_connect
        self.connections = []
        self.hosts = []
        self.timeouts = []

    def __call__(self, host, *, timeout):
        self.hosts.append(host)
        self.timeouts.append(timeout)
        response = self.responses.pop(0)
        connection = FakeConnection(response, before_connect=self.before_connect)
        self.connections.append(connection)
        return connection


class TrickleReader(io.RawIOBase):
    def __init__(self, sock):
        super().__init__()
        self.sock = sock

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.sock.offset >= len(self.sock.data):
            return 0
        count = min(len(buffer), len(self.sock.data) - self.sock.offset)
        if self.sock.max_read is not None:
            count = min(count, self.sock.max_read)
        start = self.sock.offset
        end = start + count
        buffer[:count] = self.sock.data[start:end]
        delay = self.sock.slow_step if self.sock.offset >= self.sock.slow_from else self.sock.normal_step
        self.sock.offset = end
        self.sock.clock[0] += delay * count
        for span_start, span_end in self.sock.body_spans:
            self.sock.payload_bytes_consumed += max(0, min(end, span_end) - max(start, span_start))
        return count


class TrickleSocket:
    def __init__(self, data, clock, *, slow_from=0, normal_step=0, slow_step=0,
                 max_read=None, body_spans=()):
        self.data = data
        self.clock = clock
        self.slow_from = slow_from
        self.normal_step = normal_step
        self.slow_step = slow_step
        self.max_read = max_read
        self.body_spans = tuple(body_spans)
        self.payload_bytes_consumed = 0
        self.offset = 0
        self.timeouts = []
        self.sent = []
        self.closed = False
        self.reader = None

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, data):
        self.sent.append(data)

    def makefile(self, mode, buffering=None):
        assert mode == "rb" and buffering == 0
        self.reader = TrickleReader(self)
        return self.reader

    def close(self):
        self.closed = True


class TrickleHTTPConnection:
    def __init__(self, raw_socket):
        self.raw_socket = raw_socket
        self.sock = None
        self.request_calls = 0
        self.getresponse_calls = 0
        self.closed = False
        self.requested = None

    def connect(self):
        self.sock = self.raw_socket

    def request(self, method, target, body=None, headers=None):
        self.request_calls += 1
        self.requested = (method, target, body, dict(headers or {}))
        self.sock.sendall((method + " " + target + " HTTP/1.1\r\n\r\n").encode())

    def getresponse(self):
        self.getresponse_calls += 1
        response = http.client.HTTPResponse(self.sock)
        try:
            response.begin()
        except Exception:
            response.close()
            raise
        return response

    def close(self):
        self.closed = True
        if self.sock is not None:
            self.sock.close()


class StdlibHTTPResponseFactory:
    def __init__(self, responses, *, clock=None, max_read=None, slow_from=0,
                 normal_step=0, slow_step=0):
        self.responses = list(responses)
        self.clock = clock if clock is not None else [0.0]
        self.max_read = max_read
        self.slow_from = slow_from
        self.normal_step = normal_step
        self.slow_step = slow_step
        self.connections = []
        self.hosts = []
        self.timeouts = []

    def __call__(self, host, *, timeout):
        self.hosts.append(host)
        self.timeouts.append(timeout)
        data, body_spans = self.responses.pop(0)
        raw_socket = TrickleSocket(data, self.clock, slow_from=self.slow_from,
                                   normal_step=self.normal_step, slow_step=self.slow_step,
                                   max_read=self.max_read, body_spans=body_spans)
        connection = TrickleHTTPConnection(raw_socket)
        self.connections.append(connection)
        return connection


def _http_response(status, body, *, framing="length", declared_length=None):
    reasons = {200: "OK", 401: "Unauthorized", 503: "Service Unavailable"}
    headers = [f"HTTP/1.1 {status} {reasons.get(status, 'Response')}\r\n"]
    spans = []
    head = ""
    if framing == "length":
        headers.extend([f"Content-Length: {len(body) if declared_length is None else declared_length}\r\n",
                        "Connection: close\r\n"])
        head = "".join(headers) + "\r\n"
        wire = head.encode() + body
        spans.append((len(head.encode()), len(head.encode()) + len(body)))
    elif framing == "eof":
        headers.append("Connection: close\r\n")
        head = "".join(headers) + "\r\n"
        wire = head.encode() + body
        spans.append((len(head.encode()), len(head.encode()) + len(body)))
    elif framing == "chunked":
        headers.extend(["Transfer-Encoding: chunked\r\n", "Connection: close\r\n"])
        head = "".join(headers) + "\r\n"
        prefix = head.encode()
        chunk_head = f"{len(body):X}\r\n".encode()
        body_start = len(prefix) + len(chunk_head)
        wire = prefix + chunk_head + body + b"\r\n0\r\n\r\n"
        spans.append((body_start, body_start + len(body)))
    else:
        raise AssertionError(f"unknown framing: {framing}")
    return wire, tuple(spans)


def _incomplete_chunked_response(body):
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    chunk_header = b"10\r\n"
    start = len(head) + len(chunk_header)
    return head + chunk_header + body, ((start, start + len(body)),)


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_http_header_and_body_trickle_are_cut_off_by_shared_deadline(stage):
    now = [0.0]
    budget = TransportCheckBudget(clock=lambda: now[0])
    header = b"HTTP/1.1 200 OK\r\nContent-Length: 64\r\nConnection: keep-alive\r\n\r\n"
    body = b"x" * 64
    slow_from = 0 if stage == "headers" else len(header)
    raw_socket = TrickleSocket(header + body, now, slow_from=slow_from,
                               normal_step=0.1, slow_step=4.0, max_read=1)
    connection = TrickleHTTPConnection(raw_socket)

    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=lambda _host, *, timeout: connection).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10, budget=budget)

    assert error.value.code == "TIMEOUT"
    assert connection.request_calls == connection.getresponse_calls == 1
    assert raw_socket.offset < len(raw_socket.data)
    assert raw_socket.timeouts[-1] < 10.0
    assert raw_socket.closed and raw_socket.reader.closed and connection.closed


def _json_body_of_size(size):
    prefix, suffix = b'{"x":"', b'"}'
    return prefix + b'a' * (size - len(prefix) - len(suffix)) + suffix


@pytest.mark.parametrize("size", [MAX_RESPONSE_BYTES - 1, MAX_RESPONSE_BYTES])
@pytest.mark.parametrize("framing", ["length", "eof", "chunked"])
def test_stdlib_http_response_accepts_complete_bodies_at_cap(size, framing):
    body = _json_body_of_size(size)
    factory = StdlibHTTPResponseFactory([_http_response(200, body, framing=framing)])
    budget = TransportCheckBudget()
    status, payload = LiveReadOnlyTransport(connection_factory=factory).request(
        "notion", "/v1/users/me", b"fake-token", version="2025-09-03", timeout=30, budget=budget)
    assert status == 200 and payload["x"] == "a" * (size - 8)
    connection = factory.connections[0]
    assert connection.raw_socket.payload_bytes_consumed == size
    assert connection.raw_socket.closed and connection.raw_socket.reader.closed and connection.closed
    assert connection.requested[3]["Accept-Encoding"] == "identity"
    assert budget.wire_calls == 1


def test_stdlib_http_response_rejects_truncated_content_length_even_for_valid_json():
    body = b'{"id":"folder-id"}'
    factory = StdlibHTTPResponseFactory([_http_response(
        200, body, declared_length=len(body) + 100)])
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10)
    connection = factory.connections[0]
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert connection.raw_socket.payload_bytes_consumed == len(body)
    assert connection.raw_socket.closed and connection.raw_socket.reader.closed and connection.closed


def test_stdlib_http_response_rejects_declared_content_length_over_cap_before_body_read():
    factory = StdlibHTTPResponseFactory([_http_response(
        200, b'{"id":"ok"}', declared_length=MAX_RESPONSE_BYTES + 1)])
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10)
    connection = factory.connections[0]
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert connection.raw_socket.payload_bytes_consumed == 0
    assert connection.raw_socket.closed and connection.raw_socket.reader.closed and connection.closed


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("framing", ["eof", "chunked"])
def test_stdlib_http_response_reads_only_cap_plus_one_payload_bytes_on_overflow(status, framing):
    body = _json_body_of_size(MAX_RESPONSE_BYTES + 1)
    factory = StdlibHTTPResponseFactory([_http_response(status, body, framing=framing)])
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"TOKEN-SENTINEL", version="2025-09-03", timeout=10)
    connection = factory.connections[0]
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert "TOKEN-SENTINEL" not in str(error.value)
    assert connection.raw_socket.payload_bytes_consumed == MAX_RESPONSE_BYTES + 1
    assert connection.raw_socket.closed and connection.raw_socket.reader.closed and connection.closed


def test_stdlib_http_response_rejects_chunked_overflow_and_incomplete_chunks_closed():
    overflow = _http_response(200, _json_body_of_size(MAX_RESPONSE_BYTES + 1), framing="chunked")
    incomplete = _incomplete_chunked_response(b'{"id"')
    for response, expected_payload_bytes in ((overflow, MAX_RESPONSE_BYTES + 1),
                                             (incomplete, len(b'{"id"'))):
        factory = StdlibHTTPResponseFactory([response])
        with pytest.raises(SettingsServiceError) as error:
            LiveReadOnlyTransport(connection_factory=factory).request(
                "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10)
        connection = factory.connections[0]
        assert error.value.code == "PROVIDER_UNAVAILABLE"
        assert connection.raw_socket.payload_bytes_consumed == expected_payload_bytes
        assert connection.raw_socket.closed and connection.raw_socket.reader.closed and connection.closed


def test_unsupported_content_encoding_is_rejected_before_read_and_connection_closes():
    response = FakeResponse(200, b'{"id":"ok"}', {"Content-Encoding": "gzip"})
    factory = FakeConnectionFactory([response])
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert response.read_limits == [] and factory.connections[0].closed


def test_redirect_is_returned_without_following_location():
    response = FakeResponse(302, b"", {"Location": "https://attacker.example/collect"})
    factory = FakeConnectionFactory([response])
    status, payload = LiveReadOnlyTransport(connection_factory=factory).request(
        "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10)
    assert status == 302 and payload == {}
    assert factory.hosts == ["api.notion.com"] and len(factory.connections) == 1
    assert factory.connections[0].closed


def test_redirect_consumes_the_last_wire_slot_and_no_followup_is_dispatched():
    factory = FakeConnectionFactory([FakeResponse(302, b"", {"Location": "https://other.example/"})])
    budget = TransportCheckBudget()
    budget.wire_calls = 7
    status, _payload = LiveReadOnlyTransport(connection_factory=factory).request(
        "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10, budget=budget)
    assert status == 302 and budget.wire_calls == 8
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10, budget=budget)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert len(factory.connections) == 1


def test_deadline_expiring_during_connect_prevents_request_and_response_dispatch():
    now = [0.0]
    budget = TransportCheckBudget(clock=lambda: now[0])
    response = FakeResponse(200, b'{"id":"ok"}')
    factory = FakeConnectionFactory([response], before_connect=lambda: now.__setitem__(0, 31.0))
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=30, budget=budget)
    assert error.value.code == "TIMEOUT"
    connection = factory.connections[0]
    assert connection.connect_calls == 1
    assert connection.request_calls == connection.getresponse_calls == 0
    assert connection.requested is None and connection.closed
    assert connection.raw_socket.closed
    assert budget.wire_calls == 1


def test_post_connect_socket_timeout_is_reduced_to_remaining_budget():
    now = [0.0]
    budget = TransportCheckBudget(clock=lambda: now[0])
    factory = FakeConnectionFactory([FakeResponse(200, b'{"id":"ok"}')],
                                    before_connect=lambda: now.__setitem__(0, 28.0))
    LiveReadOnlyTransport(connection_factory=factory).request(
        "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=30, budget=budget)
    connection = factory.connections[0]
    assert factory.timeouts == [10.0]
    assert connection.raw_socket.timeouts == [2.0, 2.0, 2.0]
    assert connection.request_calls == connection.getresponse_calls == 1
    assert connection.closed and budget.wire_calls == 1


def test_connection_construction_and_setup_failures_are_redacted_and_close_when_possible():
    secret = "PRIVATE-TRANSPORT-SENTINEL"

    def failing_factory(_host, *, timeout):
        raise RuntimeError(secret)

    with pytest.raises(SettingsServiceError) as construction_error:
        LiveReadOnlyTransport(connection_factory=failing_factory).request(
            "notion", "/v1/users/me", secret.encode(), version="2025-09-03", timeout=10)
    assert construction_error.value.code == "PROVIDER_UNAVAILABLE"
    assert secret not in str(construction_error.value)

    def fail_connect():
        raise RuntimeError(secret)

    factory = FakeConnectionFactory([FakeResponse(200, b'{"id":"ok"}')], before_connect=fail_connect)
    with pytest.raises(SettingsServiceError) as setup_error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", secret.encode(), version="2025-09-03", timeout=10)
    connection = factory.connections[0]
    assert setup_error.value.code == "PROVIDER_UNAVAILABLE"
    assert secret not in str(setup_error.value)
    assert connection.closed and connection.request_calls == connection.getresponse_calls == 0
    assert connection.raw_socket.closed


def test_deadline_is_checked_after_a_late_response_before_accepting_success():
    now = [0.0]
    response = FakeResponse(200, b'{"id":"ok"}')
    original_read = response.read

    def late_read(limit):
        data = original_read(limit)
        now[0] = 31.0
        return data

    response.read = late_read
    factory = FakeConnectionFactory([response])
    budget = TransportCheckBudget(clock=lambda: now[0])
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10, budget=budget)
    assert error.value.code == "TIMEOUT" and factory.connections[0].closed


@pytest.mark.parametrize("elapsed,expected", [(0.0, 10.0), (26.0, 4.0)])
def test_socket_timeout_is_limited_by_ten_seconds_and_remaining_budget(elapsed, expected):
    now = [0.0]
    budget = TransportCheckBudget(clock=lambda: now[0])
    now[0] = elapsed
    factory = FakeConnectionFactory([FakeResponse(200, b'{"id":"ok"}')])
    LiveReadOnlyTransport(connection_factory=factory).request(
        "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=90, budget=budget)
    assert factory.timeouts == [expected]
    assert factory.connections[0].raw_socket.timeouts == [expected, expected, expected]


def test_ninth_wire_dispatch_is_refused_before_connection_creation():
    factory = FakeConnectionFactory([])
    budget = TransportCheckBudget()
    budget.wire_calls = 8
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory).request(
            "notion", "/v1/users/me", b"fake", version="2025-09-03", timeout=10, budget=budget)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert factory.connections == [] and budget.wire_calls == 8


def _google_payload():
    return json.dumps({"type": "service_account", "client_email": "fake@example.com",
        "private_key": "fake", "private_key_id": "fake", "project_id": "fake",
        "token_uri": OAUTH_URL}).encode()


def test_google_refresh_and_drive_read_share_budget_and_use_fixed_readonly_requests():
    oauth_body = b'{"access_token":"ACCESS-SENTINEL","expires_in":3600,"token_type":"Bearer"}'
    drive_body = b'{"id":"folder-id","trashed":false,"capabilities":{"canAddChildren":true,"canListChildren":true}}'
    factory = FakeConnectionFactory([FakeResponse(200, oauth_body), FakeResponse(200, drive_body)])
    scopes = []

    class Credentials:
        token = "ACCESS-SENTINEL"

        def refresh(self, request):
            self.response = request(OAUTH_URL, method="POST", body=b"signed-jwt", headers={}, timeout=120)

        def apply(self, headers):
            headers["Authorization"] = "Bearer " + self.token

    credentials = Credentials()

    def loader(payload, *, scopes):
        assert payload["token_uri"] == OAUTH_URL
        return credentials

    def recording_loader(payload, **kwargs):
        scopes.extend(kwargs["scopes"])
        return loader(payload, **kwargs)

    budget = TransportCheckBudget()
    status, payload = LiveReadOnlyTransport(connection_factory=factory, google_loader=recording_loader).request(
        "google", "folder-id", _google_payload(), version="", timeout=10, budget=budget)
    assert status == 200 and payload["id"] == "folder-id"
    assert scopes == ["https://www.googleapis.com/auth/drive.readonly"]
    assert [c.requested[:2] for c in factory.connections] == [
        ("POST", "/token"),
        ("GET", "/drive/v3/files/folder-id?fields=id%2Ctrashed%2Ccapabilities%28canAddChildren%2CcanListChildren%29"),
    ]
    assert factory.hosts == ["oauth2.googleapis.com", "www.googleapis.com"]
    assert all(c.closed for c in factory.connections)
    assert budget.wire_calls == 2 and budget.refresh_calls == 1
    assert "ACCESS-SENTINEL" not in str(payload)


@pytest.mark.parametrize("response_stage", ["oauth", "drive"])
@pytest.mark.parametrize("size", [MAX_RESPONSE_BYTES - 1, MAX_RESPONSE_BYTES])
def test_google_oauth_and_drive_accept_responses_at_or_below_cap(response_stage, size):
    sized_body = _json_body_of_size(size)
    oauth_body = sized_body if response_stage == "oauth" else b'{"access_token":"fake"}'
    drive_body = sized_body if response_stage == "drive" else b'{"id":"folder-id"}'
    factory = StdlibHTTPResponseFactory([_http_response(200, oauth_body), _http_response(200, drive_body)])

    class Credentials:
        def refresh(self, request):
            self.response = request(OAUTH_URL, method="POST", body=b"jwt", headers={}, timeout=120)

        def apply(self, headers):
            headers["Authorization"] = "Bearer fake"

    budget = TransportCheckBudget()
    status, payload = LiveReadOnlyTransport(connection_factory=factory,
        google_loader=lambda *_args, **_kwargs: Credentials()).request(
            "google", "folder-id", _google_payload(), version="", timeout=10, budget=budget)
    assert status == 200
    if response_stage == "drive":
        assert payload["x"] == "a" * (size - 8)
    else:
        assert payload["id"] == "folder-id"
    assert all(connection.closed for connection in factory.connections)
    assert [connection.raw_socket.payload_bytes_consumed for connection in factory.connections] == [
        len(oauth_body), len(drive_body)]
    assert budget.wire_calls == 2 and budget.refresh_calls == 1


@pytest.mark.parametrize("status", [400, 401, 503])
def test_oauth_error_response_never_reaches_sdk_and_is_not_retried(status):
    response = FakeResponse(status, b'{"error":"OAUTH-SECRET-SENTINEL"}')
    factory = FakeConnectionFactory([response])
    refresh_attempts = []

    class Credentials:
        def refresh(self, request):
            refresh_attempts.append(1)
            self.response = request(OAUTH_URL, method="POST", body=b"jwt", headers={}, timeout=120)

        def apply(self, headers):
            headers["Authorization"] = "Bearer unused"

    credentials = Credentials()
    transport = LiveReadOnlyTransport(connection_factory=factory, google_loader=lambda *_args, **_kwargs: credentials)
    with pytest.raises(SettingsServiceError) as error:
        transport.request("google", "folder-id", _google_payload(), version="", timeout=10)
    assert error.value.code == ("INVALID_CREDENTIAL" if status in {400, 401} else "PROVIDER_UNAVAILABLE")
    assert len(refresh_attempts) == 1 and len(factory.connections) == 1
    assert factory.connections[0].closed and "OAUTH-SECRET-SENTINEL" not in str(error.value)


def test_installed_google_auth_does_not_retry_after_bounded_request_rejects_error_response():
    from google.oauth2.service_account import Credentials

    class Signer:
        key_id = "offline-test-key"

        def sign(self, message):
            return b"offline-signature"

    request_calls = []

    class CountingCredentials(Credentials):
        def refresh(self, request):
            class CountedRequest:
                def __call__(self, *args, **kwargs):
                    request_calls.append(1)
                    return request(*args, **kwargs)

            super().refresh(CountedRequest())

    credentials = CountingCredentials(Signer(), "offline@example.com", OAUTH_URL, scopes=[READ_ONLY_SCOPE])
    response = FakeResponse(503, b'{"error":"SDK-RETRY-SENTINEL"}')
    factory = FakeConnectionFactory([response])
    transport = LiveReadOnlyTransport(connection_factory=factory,
        google_loader=lambda *_args, **_kwargs: credentials)
    with pytest.raises(SettingsServiceError) as error:
        transport.request("google", "folder-id", _google_payload(), version="", timeout=10)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert request_calls == [1] and len(factory.connections) == 1
    assert response.read_limits == [MAX_RESPONSE_BYTES + 1] and factory.connections[0].closed


def test_bounded_google_request_refuses_other_endpoint_method_and_second_refresh():
    factory = FakeConnectionFactory([FakeResponse(200, b'{"ok":true}')])
    transport = LiveReadOnlyTransport(connection_factory=factory)
    budget = TransportCheckBudget()
    request = BoundedGoogleAuthRequest(transport, budget)
    with pytest.raises(SettingsServiceError) as endpoint_error:
        request("https://attacker.example/token", method="POST")
    assert endpoint_error.value.code == "CHECK_NOT_READ_ONLY"
    with pytest.raises(SettingsServiceError) as method_error:
        request(OAUTH_URL, method="GET")
    assert method_error.value.code == "CHECK_NOT_READ_ONLY"
    assert request(OAUTH_URL, method="POST").status == 200
    with pytest.raises(SettingsServiceError) as repeat_error:
        request(OAUTH_URL, method="POST")
    assert repeat_error.value.code == "PROVIDER_UNAVAILABLE"
    assert len(factory.connections) == 1 and budget.wire_calls == 1


def test_oauth_dispatch_at_wire_limit_blocks_drive_without_a_ninth_request():
    factory = FakeConnectionFactory([FakeResponse(200, b'{"access_token":"fake"}')])

    class Credentials:
        def refresh(self, request):
            self.response = request(OAUTH_URL, method="POST", body=b"jwt", headers={}, timeout=120)

        def apply(self, headers):
            headers["Authorization"] = "Bearer fake"

    budget = TransportCheckBudget()
    budget.wire_calls = 7
    with pytest.raises(SettingsServiceError) as error:
        LiveReadOnlyTransport(connection_factory=factory,
            google_loader=lambda *_args, **_kwargs: Credentials()).request(
                "google", "folder-id", _google_payload(), version="", timeout=10, budget=budget)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert budget.wire_calls == 8 and len(factory.connections) == 1
    assert factory.connections[0].requested[0:2] == ("POST", "/token")
    assert factory.connections[0].closed


@pytest.mark.parametrize("overflow_at", ["oauth", "drive"])
def test_google_oauth_and_drive_responses_use_the_same_cap_and_close_connections(overflow_at):
    oauth_body = b"O" * (MAX_RESPONSE_BYTES + 1) if overflow_at == "oauth" else b'{"access_token":"fake","expires_in":3600}'
    drive_body = b"D" * (MAX_RESPONSE_BYTES + 1) if overflow_at == "drive" else b'{"id":"folder-id"}'
    responses = [_http_response(200, oauth_body)]
    if overflow_at == "drive":
        responses.append(_http_response(200, drive_body))
    factory = StdlibHTTPResponseFactory(responses)

    class Credentials:
        def refresh(self, request):
            self.response = request(OAUTH_URL, method="POST", body=b"jwt", headers={}, timeout=120)

        def apply(self, headers):
            headers["Authorization"] = "Bearer fake"

    transport = LiveReadOnlyTransport(connection_factory=factory,
        google_loader=lambda *_args, **_kwargs: Credentials())
    with pytest.raises(SettingsServiceError) as error:
        transport.request("google", "folder-id", _google_payload(), version="", timeout=10)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert len(factory.connections) == (1 if overflow_at == "oauth" else 2)
    assert all(connection.closed for connection in factory.connections)
    expected_consumed = [0] if overflow_at == "oauth" else [len(oauth_body), 0]
    assert [connection.raw_socket.payload_bytes_consumed for connection in factory.connections] == expected_consumed


def test_live_http_error_body_and_transport_text_never_escape_provider_check(caplog):
    secret = b"PRIVATE-TOKEN-SENTINEL"
    factory = FakeConnectionFactory([FakeResponse(401, b'{"error":"' + secret + b'"}')])
    checks = ProviderChecks(LiveReadOnlyTransport(connection_factory=factory), cooldown=0)
    with pytest.raises(SettingsServiceError) as error:
        checks.check(ROLES["notion-mcp"], secret, load_config_mapping({}))
    assert error.value.code == "INVALID_CREDENTIAL"
    assert secret.decode() not in str(error.value) and secret.decode() not in caplog.text
    latest = checks.results["notion-mcp"]
    assert latest["code"] == "INVALID_CREDENTIAL" and latest["resources"] == []
    assert secret.decode() not in str(latest) and factory.connections[0].closed
