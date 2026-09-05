"""Deterministic, provider-free LLM adapters used by Phase 3 tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from uls.adapters.llm.base import LLMEnrichmentResult


class LLMTransientError(RuntimeError):
    pass


class LLMRateLimitError(RuntimeError):
    pass


class LLMPermanentError(RuntimeError):
    pass


class LLMAmbiguousError(RuntimeError):
    pass


class LLMPolicyDeniedError(RuntimeError):
    pass


class FakeLLMAdapter:
    """A pure fake with queued results/errors and captured bounded inputs."""

    def __init__(
        self,
        *,
        session_result: LLMEnrichmentResult | Mapping[str, Any] | None = None,
        material_result: LLMEnrichmentResult | Mapping[str, Any] | None = None,
        session_outputs: Iterable[Any] | None = None,
        material_outputs: Iterable[Any] | None = None,
    ) -> None:
        self.session_result = session_result
        self.material_result = material_result
        self.session_queue = list(session_outputs or ())
        self.material_queue = list(material_outputs or ())
        self.calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def enrich_session(self, derivative: Any, *, chunks: Sequence[Any] = ()) -> LLMEnrichmentResult:
        self.calls.append(("session", derivative, tuple(chunks)))
        value = self.session_queue.pop(0) if self.session_queue else self.session_result
        if self.session_queue or value is not None:
            self.session_result = value
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise LLMPermanentError("fake session result not configured")
        return _coerce_result(value)

    def enrich_material(self, derivative: Any, *, chunks: Sequence[Any] = ()) -> LLMEnrichmentResult:
        self.calls.append(("material", derivative, tuple(chunks)))
        value = self.material_queue.pop(0) if self.material_queue else self.material_result
        if self.material_queue or value is not None:
            self.material_result = value
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise LLMPermanentError("fake material result not configured")
        return _coerce_result(value)


def session_result(
    *,
    locator: str = "COMP319-S05:t00:00:01",
    quote: str = "첫 주제",
    include: Iterable[str] | None = None,
    explicitness: str = "EXPLICIT",
    based_on: Any = None,
    malicious: bool = False,
) -> LLMEnrichmentResult:
    kinds = (
        "summary",
        "topics",
        "professor_emphasis",
        "professor_examples",
        "exam_signals",
        "likely_confusions",
    )
    wanted = set(include or kinds)
    output: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    for kind in kinds:
        if kind not in wanted:
            continue
        output[kind] = [
            {
                "kind": kind,
                "content": f"{kind}: {quote}",
                "symbolic_hint": quote,
                "explicitness": explicitness,
            }
        ]
        evidence[kind] = [{"locator": locator, "quote": quote}]
    if malicious:
        output.update(
            {
                "based_on": {"source_version": 999, "source_hash": "spoof"},
                "source_class": "professor_transcript",
                "ownership": "SOURCE",
                "freshness": "FRESH",
                "factual": True,
                "Verified": True,
                "Scope Confirmed": True,
                "Decision": "Approve",
                "State": "APPROVED",
            }
        )
    return LLMEnrichmentResult(
        output=output,
        evidence=evidence,
        confidence=0.91,
        classification="fact",
        provider_provenance={"provider": "fake", "model": "deterministic"},
        based_on=based_on or {"source_version": 999, "source_hash": "spoof"},
        explicitness=explicitness,
    )


def material_result(
    *,
    locator: str = "COMP319-M03:p1",
    quote: str = "Master theorem",
    include: Iterable[str] | None = None,
) -> LLMEnrichmentResult:
    kinds = ("content_index", "topics")
    wanted = set(include or kinds)
    output: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    for kind in kinds:
        if kind not in wanted:
            continue
        output[kind] = [
            {
                "kind": kind,
                "content": f"{kind}: {quote}",
                "symbolic_hint": quote,
            }
        ]
        evidence[kind] = [{"locator": locator, "quote": quote}]
    return LLMEnrichmentResult(
        output=output,
        evidence=evidence,
        confidence=0.88,
        classification="proposal",
        provider_provenance={"provider": "fake", "model": "deterministic"},
        based_on={"source_version": 123, "source_hash": "spoof"},
    )


def ungrounded_result() -> LLMEnrichmentResult:
    return LLMEnrichmentResult(
        output={
            "summary": [
                {
                    "content": "unsupported hallucination",
                    "symbolic_hint": "missing topic",
                }
            ]
        },
        evidence={},
        confidence=0.99,
    )


def cross_locator_quote_result() -> LLMEnrichmentResult:
    return LLMEnrichmentResult(
        output={
            "professor_emphasis": [
                {
                    "content": "second-slice claim",
                    "symbolic_hint": "첫 주제",
                }
            ]
        },
        evidence={
            "professor_emphasis": [
                {"locator": "COMP319-S05:t00:00:01", "quote": "둘째 주제"}
            ]
        },
        confidence=0.9,
    )


def _coerce_result(value: Any) -> LLMEnrichmentResult:
    if isinstance(value, LLMEnrichmentResult):
        return value
    if isinstance(value, Mapping):
        return LLMEnrichmentResult.from_mapping(deepcopy(value))
    raise TypeError("fake result must be an LLMEnrichmentResult or mapping")


FakeLLM = FakeLLMAdapter


__all__ = [
    "FakeLLM",
    "FakeLLMAdapter",
    "LLMAmbiguousError",
    "LLMPermanentError",
    "LLMPolicyDeniedError",
    "LLMRateLimitError",
    "LLMTransientError",
    "cross_locator_quote_result",
    "material_result",
    "session_result",
    "ungrounded_result",
]
