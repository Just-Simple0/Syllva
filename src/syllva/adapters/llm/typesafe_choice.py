"""Boundary adapter for one TypeSafe "Choice" call (intake classification v2, plan §4).

Fixed endpoint and model, no proxy, no redirect, no retry, bounded time and size, strict
response validation.  The adapter never reads the environment, a file or the Keychain: the
composition root resolves the key once and passes it in.  Every failure is reduced to a fixed
code; no key, request text, response text or provider message ever leaves this module."""
from __future__ import annotations

import http.client
import json
import math
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

ENDPOINT_HOST = "api.typesafe.ai"
ENDPOINT_PATH = "/v1/systemone"
MODEL = "jev-1.13.0"
SOCKET_TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 15.0
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 65_536
PROBABILITY_SUM_TOLERANCE = 1e-6

DISABLED = "CLASSIFIER_DISABLED"
TIMEOUT = "CLASSIFIER_TIMEOUT"
INVALID_RESPONSE = "CLASSIFIER_INVALID_RESPONSE"
BUDGET_EXHAUSTED = "CLASSIFIER_BUDGET_EXHAUSTED"
TRANSPORT = "CLASSIFIER_TRANSPORT"


class ClassifierError(Exception):
    """A fixed-code failure.  ``str(error)`` is the code and nothing else."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RequestTooLarge(ClassifierError):
    """The serialized request exceeds the cap; nothing was sent.  The caller may shrink it."""

    def __init__(self) -> None:
        super().__init__(TRANSPORT)


@dataclass(frozen=True)
class ChoiceQuestion:
    name: str
    instructions: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ChoiceResult:
    model: str
    answers: Mapping[str, ChoiceAnswer]
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RawResponse:
    status: int
    content_type: str
    body: bytes


class Transport(Protocol):
    def post(self, body: bytes, api_key: str, *, deadline: float, clock: Callable[[], float]) -> RawResponse: ...


@dataclass
class HttpsTransport:
    """stdlib HTTPS: TLS verified, environment proxies are not consulted, redirects are not followed."""

    host: str = ENDPOINT_HOST
    path: str = ENDPOINT_PATH
    context: ssl.SSLContext | None = field(default=None, repr=False)

    def post(self, body: bytes, api_key: str, *, deadline: float, clock: Callable[[], float]) -> RawResponse:
        def remaining() -> float:
            left = deadline - clock()
            if left <= 0:
                raise ClassifierError(TIMEOUT)
            return min(SOCKET_TIMEOUT_SECONDS, left)

        connection = http.client.HTTPSConnection(
            self.host, timeout=remaining(), context=self.context or ssl.create_default_context())
        try:
            try:
                connection.request("POST", self.path, body=body, headers={
                    "Authorization": f"Bearer {api_key}", "Accept": "application/json",
                    "Content-Type": "application/json", "Content-Length": str(len(body)),
                    "User-Agent": "syllva-intake-classifier"})
                if connection.sock is not None:
                    connection.sock.settimeout(remaining())
                response = connection.getresponse()
                declared = response.getheader("Content-Length")
                if declared is not None and (not declared.isdecimal() or int(declared) > MAX_RESPONSE_BYTES):
                    raise ClassifierError(INVALID_RESPONSE)
                chunks: list[bytes] = []
                total = 0
                while True:
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining())
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ClassifierError(INVALID_RESPONSE)
                    chunks.append(chunk)
                return RawResponse(response.status, response.getheader("Content-Type") or "", b"".join(chunks))
            except ClassifierError:
                raise
            except TimeoutError:
                raise ClassifierError(TIMEOUT) from None
            except Exception:  # noqa: BLE001 - provider/network detail never leaves this module
                raise ClassifierError(TRANSPORT) from None
        finally:
            connection.close()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _real(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("not a real number")  # noqa: TRY004
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError("probability out of range")
    return number


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid count")
    return value


def _validate(payload: Any, questions: Sequence[ChoiceQuestion]) -> ChoiceResult:
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        raise ValueError("model")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("usage")  # noqa: TRY004
    input_tokens, output_tokens = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != {q.name for q in questions}:
        raise ValueError("answers")
    parsed: dict[str, ChoiceAnswer] = {}
    for question in questions:
        raw = answers[question.name]
        if not isinstance(raw, dict) or raw.get("type") != "choice":
            raise ValueError("type")
        probabilities = raw.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(question.options):
            raise ValueError("options")
        values = {name: _real(value) for name, value in probabilities.items()}
        if abs(sum(values.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError("sum")
        top = max(values.values())
        winners = [name for name, value in values.items() if value == top]
        choice = raw.get("choice")
        if len(winners) != 1 or choice != winners[0]:
            raise ValueError("choice")
        parsed[question.name] = ChoiceAnswer(winners[0], _real(raw.get("confidence")), values)
    return ChoiceResult(MODEL, parsed, input_tokens, output_tokens)


def _key_is_well_formed(key: Any) -> bool:
    return isinstance(key, str) and 0 < len(key) <= 4096 and all(33 <= ord(c) <= 126 for c in key)


class TypesafeChoiceClient:
    def __init__(
        self, api_key: str, *, transport: Transport | None = None, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _key_is_well_formed(api_key):
            raise ClassifierError(DISABLED)
        self._api_key = api_key
        self._transport = transport or HttpsTransport()
        self._clock = clock

    def __repr__(self) -> str:
        return "TypesafeChoiceClient()"

    def ask(self, state: Mapping[str, Any], questions: Sequence[ChoiceQuestion]) -> ChoiceResult:
        names = [q.name for q in questions]
        if not questions or len(set(names)) != len(names) or any(len(q.options) < 2 or len(set(q.options)) != len(q.options) for q in questions):
            raise ClassifierError(TRANSPORT)
        body = {
            "state": state, "model": MODEL,
            "questions": {q.name: {"type": "choice", "instructions": q.instructions,
                                   "criteria": {option: None for option in q.options}} for q in questions},
        }
        try:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise ClassifierError(TRANSPORT) from None
        if len(encoded) > MAX_REQUEST_BYTES:
            raise RequestTooLarge
        deadline = self._clock() + TOTAL_TIMEOUT_SECONDS
        response = self._transport.post(encoded, self._api_key, deadline=deadline, clock=self._clock)
        if self._clock() > deadline:
            raise ClassifierError(TIMEOUT)
        if response.status != 200:
            raise ClassifierError(TRANSPORT)
        if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise ClassifierError(INVALID_RESPONSE)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ClassifierError(INVALID_RESPONSE)
        try:
            payload = json.loads(
                response.body.decode("utf-8"), object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
            return _validate(payload, questions)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ClassifierError(INVALID_RESPONSE) from None


def resolve_typesafe_key(declared_sources: Mapping[str, str], resolver: Any) -> str | None:
    """The key, or None when classification is DISABLED.  Only an explicit
    ``credentials.TYPESAFE_API_KEY.source`` enables it (no implicit environment fallback);
    an unreadable or malformed credential is also None."""

    if "TYPESAFE_API_KEY" not in declared_sources:
        return None
    try:
        outcome = resolver.diagnose(frozenset({"TYPESAFE_API_KEY"}))
        key = outcome.select(required=frozenset({"TYPESAFE_API_KEY"}))["TYPESAFE_API_KEY"]
    except Exception:  # noqa: BLE001 - never expose resolver diagnostics
        return None
    return key if _key_is_well_formed(key) else None
