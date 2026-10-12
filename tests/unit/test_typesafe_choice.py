"""TypeSafe Choice adapter: strict boundary contract against a fake transport."""
from __future__ import annotations

import json

import pytest

from uls.adapters.llm import typesafe_choice as tc
from uls.adapters.llm.typesafe_choice import (
    ChoiceQuestion,
    ClassifierError,
    HttpsTransport,
    RawResponse,
    RequestTooLarge,
    TypesafeChoiceClient,
    resolve_typesafe_key,
)
from uls.config.credentials import ALLOWED_SOURCES, KEYRING_BINDINGS, CredentialResolver

pytestmark = pytest.mark.unit

KEY = "sk-test-SECRET-key-123"
OPTIONS = ("LECTURE_SLIDES", "LAB_MATERIAL", "no_match")
QUESTION = ChoiceQuestion("kind", "Which kind?", OPTIONS)


def _ok(probabilities=None, **over):
    probabilities = probabilities or {"LECTURE_SLIDES": 0.7, "LAB_MATERIAL": 0.2, "no_match": 0.1}
    answer = {"type": "choice", "choice": max(probabilities, key=probabilities.get),
              "confidence": 0.83, "probabilities": probabilities}
    payload = {"model": tc.MODEL, "usage": {"input_tokens": 12, "output_tokens": 3}, "answers": {"kind": answer}}
    payload.update(over)
    return payload


class _Transport:
    def __init__(self, response: RawResponse | Exception) -> None:
        self.response, self.calls = response, []

    def post(self, body, api_key, *, deadline, clock):
        self.calls.append((body, api_key, deadline))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _json(payload, status=200, content_type="application/json"):
    return RawResponse(status, content_type, json.dumps(payload).encode())


def _ask(response, questions=(QUESTION,), clock=None):
    transport = _Transport(response)
    kwargs = {"clock": clock} if clock else {}
    client = TypesafeChoiceClient(KEY, transport=transport, **kwargs)
    return client.ask({"title": "x"}, questions), transport


def _code(response, **kw):
    with pytest.raises(ClassifierError) as caught:
        _ask(response, **kw)
    return caught.value


def test_valid_response_and_request_shape() -> None:
    result, transport = _ask(_json(_ok()))
    answer = result.answers["kind"]
    assert (answer.choice, answer.confidence, result.model) == ("LECTURE_SLIDES", 0.83, "jev-1.13.0")
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    body, key, _deadline = transport.calls[0]
    sent = json.loads(body)
    assert sent["model"] == "jev-1.13.0" and sent["state"] == {"title": "x"}
    assert sent["questions"]["kind"] == {"type": "choice", "instructions": "Which kind?",
                                         "criteria": {o: None for o in OPTIONS}}
    assert key == KEY and KEY.encode() not in body


def test_several_questions_in_one_request() -> None:
    second = ChoiceQuestion("tag", "Tag?", ("yes", "no"))
    payload = _ok()
    payload["answers"]["tag"] = {"type": "choice", "choice": "yes", "confidence": 0.9,
                                 "probabilities": {"yes": 0.9, "no": 0.1}}
    result, transport = _ask(_json(payload), questions=(QUESTION, second))
    assert set(result.answers) == {"kind", "tag"} and len(transport.calls) == 1


@pytest.mark.parametrize("name,payload", [
    ("sum_off", _ok({"LECTURE_SLIDES": 0.7, "LAB_MATERIAL": 0.2, "no_match": 0.2})),
    ("tie", _ok({"LECTURE_SLIDES": 0.4, "LAB_MATERIAL": 0.4, "no_match": 0.2})),
    ("option_missing", _ok({"LECTURE_SLIDES": 0.8, "LAB_MATERIAL": 0.2})),
    ("option_extra", _ok({"LECTURE_SLIDES": 0.6, "LAB_MATERIAL": 0.2, "no_match": 0.1, "X": 0.1})),
    ("model_mismatch", _ok(model="jev-9")),
    ("bool_probability", _ok({"LECTURE_SLIDES": True, "LAB_MATERIAL": 0.0, "no_match": 0.0})),
    ("negative", _ok({"LECTURE_SLIDES": 1.2, "LAB_MATERIAL": -0.2, "no_match": 0.0})),
    ("usage_missing", {k: v for k, v in _ok().items() if k != "usage"}),
    ("usage_bad", _ok(usage={"input_tokens": "1", "output_tokens": 2})),
    ("usage_bool", _ok(usage={"input_tokens": True, "output_tokens": 2})),
    ("unrequested_question", {**_ok(), "answers": {**_ok()["answers"], "extra": _ok()["answers"]["kind"]}}),
    ("missing_question", _ok(answers={})),
    ("wrong_type", _ok(answers={"kind": {**_ok()["answers"]["kind"], "type": "score"}})),
    ("choice_not_top", _ok(answers={"kind": {**_ok()["answers"]["kind"], "choice": "no_match"}})),
    ("confidence_bool", _ok(answers={"kind": {**_ok()["answers"]["kind"], "confidence": True}})),
    ("confidence_over_one", _ok(answers={"kind": {**_ok()["answers"]["kind"], "confidence": 1.5}})),
    ("not_object", [1, 2]),
])
def test_invalid_payloads_are_rejected(name, payload) -> None:
    assert _code(_json(payload)).code == tc.INVALID_RESPONSE


@pytest.mark.parametrize("raw", [
    b'{"model":"jev-1.13.0","model":"jev-1.13.0"}',
    b'{"model":NaN}', b'{"x":Infinity}', b"not json", b"\xff\xfe", b"",
])
def test_malformed_json_is_rejected(raw) -> None:
    assert _code(RawResponse(200, "application/json", raw)).code == tc.INVALID_RESPONSE


def test_duplicate_nested_key_is_rejected() -> None:
    body = json.dumps(_ok()).replace('"confidence": 0.83', '"confidence": 0.83, "confidence": 0.5').encode()
    assert _code(RawResponse(200, "application/json", body)).code == tc.INVALID_RESPONSE


def test_content_type_and_status_rules() -> None:
    assert _code(_json(_ok(), content_type="text/html")).code == tc.INVALID_RESPONSE
    assert _ask(_json(_ok(), content_type="application/json; charset=utf-8"))[0].model == tc.MODEL
    for status in (201, 204, 301, 302, 401, 429, 500):
        assert _code(_json(_ok(), status=status)).code == tc.TRANSPORT


def test_oversized_response_is_rejected() -> None:
    big = RawResponse(200, "application/json", b" " * (tc.MAX_RESPONSE_BYTES + 1))
    assert _code(big).code == tc.INVALID_RESPONSE


def test_oversized_request_sends_nothing() -> None:
    transport = _Transport(_json(_ok()))
    client = TypesafeChoiceClient(KEY, transport=transport)
    with pytest.raises(RequestTooLarge):
        client.ask({"text": "x" * (tc.MAX_REQUEST_BYTES + 1)}, [QUESTION])
    assert transport.calls == []


def test_non_finite_state_is_not_sent() -> None:
    transport = _Transport(_json(_ok()))
    with pytest.raises(ClassifierError):
        TypesafeChoiceClient(KEY, transport=transport).ask({"x": float("nan")}, [QUESTION])
    assert transport.calls == []


def test_total_deadline_is_enforced_after_the_call() -> None:
    ticks = iter([0.0, 100.0])
    assert _code(_json(_ok()), clock=lambda: next(ticks)).code == tc.TIMEOUT


def test_deadline_is_fifteen_seconds_from_start() -> None:
    _result, transport = _ask(_json(_ok()), clock=lambda: 50.0)
    assert transport.calls[0][2] == 50.0 + tc.TOTAL_TIMEOUT_SECONDS == 65.0


def test_transport_errors_are_reduced_to_codes() -> None:
    assert _code(ClassifierError(tc.TIMEOUT)).code == tc.TIMEOUT
    assert _code(ClassifierError(tc.TRANSPORT)).code == tc.TRANSPORT


def test_bad_questions_are_refused_before_sending() -> None:
    for questions in ([], [QUESTION, QUESTION], [ChoiceQuestion("a", "q", ("only",))],
                      [ChoiceQuestion("a", "q", ("x", "x"))]):
        transport = _Transport(_json(_ok()))
        with pytest.raises(ClassifierError):
            TypesafeChoiceClient(KEY, transport=transport).ask({}, questions)
        assert transport.calls == []


@pytest.mark.parametrize("key", ["", "has space", "new\nline", "é", "x" * 4097, None, 5])
def test_malformed_key_is_refused(key) -> None:
    with pytest.raises(ClassifierError) as caught:
        TypesafeChoiceClient(key)  # type: ignore[arg-type]
    assert caught.value.code == tc.DISABLED


def test_key_never_appears_in_errors_or_repr() -> None:
    client = TypesafeChoiceClient(KEY, transport=_Transport(_json(_ok(), status=500)))
    assert KEY not in repr(client)
    with pytest.raises(ClassifierError) as caught:
        client.ask({}, [QUESTION])
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)
    assert KEY not in repr(HttpsTransport())


# --- real transport against a fake connection ---------------------------------------------

class _Response:
    def __init__(self, status=200, headers=None, chunks=(b"{}",), clock=None, step=0.0):
        self.status, self.headers, self.chunks, self.clock, self.step = status, headers or {}, list(chunks), clock, step

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, _n):
        if self.clock is not None:
            self.clock.now += self.step
        return self.chunks.pop(0) if self.chunks else b""


class _Sock:
    def __init__(self): self.timeouts = []
    def settimeout(self, value): self.timeouts.append(value)


class _Connection:
    last = None

    def __init__(self, host, timeout=None, context=None):
        self.host, self.timeout, self.context = host, timeout, context
        self.sock, self.sent, self.closed = _Sock(), None, False
        _Connection.last = self

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path, body, headers)

    def getresponse(self):
        return _Connection.response

    def close(self):
        self.closed = True


class _Clock:
    now = 0.0
    def __call__(self): return self.now


def _post(monkeypatch, response, clock=None, error=None):
    _Connection.response = response
    if error is not None:
        def boom(self, *a, **k): raise error
        monkeypatch.setattr(_Connection, "request", boom)
    monkeypatch.setattr(tc.http.client, "HTTPSConnection", _Connection)
    clock = clock or _Clock()
    return HttpsTransport().post(b"{}", KEY, deadline=clock() + 15, clock=clock)


def test_real_transport_posts_to_the_fixed_endpoint(monkeypatch) -> None:
    raw = _post(monkeypatch, _Response(chunks=(b'{"a":', b"1}")))
    conn = _Connection.last
    assert (raw.status, raw.body) == (200, b'{"a":1}')
    assert conn.host == tc.ENDPOINT_HOST and conn.sent[:2] == ("POST", tc.ENDPOINT_PATH)
    assert conn.sent[3]["Authorization"] == f"Bearer {KEY}" and conn.closed
    assert conn.context is not None and conn.timeout <= tc.SOCKET_TIMEOUT_SECONDS


def test_real_transport_does_not_follow_redirects(monkeypatch) -> None:
    raw = _post(monkeypatch, _Response(status=302, headers={"Location": "https://evil.example/"}))
    assert raw.status == 302 and len(_Connection.last.sent) == 4   # one request only


def test_real_transport_rejects_declared_oversize(monkeypatch) -> None:
    with pytest.raises(ClassifierError) as caught:
        _post(monkeypatch, _Response(headers={"Content-Length": str(tc.MAX_RESPONSE_BYTES + 1)}))
    assert caught.value.code == tc.INVALID_RESPONSE


def test_real_transport_aborts_a_streamed_oversize(monkeypatch) -> None:
    chunks = [b"x" * 4096] * 17
    with pytest.raises(ClassifierError) as caught:
        _post(monkeypatch, _Response(chunks=chunks))
    assert caught.value.code == tc.INVALID_RESPONSE


def test_real_transport_enforces_the_total_deadline_while_streaming(monkeypatch) -> None:
    clock = _Clock()
    with pytest.raises(ClassifierError) as caught:
        _post(monkeypatch, _Response(chunks=[b"x"] * 50, clock=clock, step=4.0), clock=clock)
    assert caught.value.code == tc.TIMEOUT


@pytest.mark.parametrize("error,code", [
    (TimeoutError("secret detail"), tc.TIMEOUT), (OSError("secret detail"), tc.TRANSPORT)])
def test_real_transport_errors_are_reduced_to_codes(monkeypatch, error, code) -> None:
    with pytest.raises(ClassifierError) as caught:
        _post(monkeypatch, _Response(), error=error)
    assert caught.value.code == code and "secret" not in repr(caught.value)


# --- credential resolution -----------------------------------------------------------------

def test_credential_is_registered_with_fixed_locators() -> None:
    assert ALLOWED_SOURCES["TYPESAFE_API_KEY"] == frozenset({"environment", "keyring"})
    assert KEYRING_BINDINGS["TYPESAFE_API_KEY"] == ("Syllva LLM", "typesafe_api_key")


def test_unspecified_source_is_disabled_even_with_an_environment_value() -> None:
    resolver = CredentialResolver({}, environ={"TYPESAFE_API_KEY": KEY})
    assert resolve_typesafe_key({}, resolver) is None


def test_explicit_environment_source_resolves() -> None:
    declared = {"TYPESAFE_API_KEY": "environment"}
    resolver = CredentialResolver(declared, environ={"TYPESAFE_API_KEY": KEY})
    assert resolve_typesafe_key(declared, resolver) == KEY


def test_explicit_but_absent_or_malformed_is_disabled() -> None:
    declared = {"TYPESAFE_API_KEY": "environment"}
    assert resolve_typesafe_key(declared, CredentialResolver(declared, environ={})) is None
    assert resolve_typesafe_key(
        declared, CredentialResolver(declared, environ={"TYPESAFE_API_KEY": "bad key"})) is None
