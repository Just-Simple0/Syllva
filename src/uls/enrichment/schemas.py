"""Typed, fingerprint-bound enrichment schemas.

The classes in this module are deliberately provider-neutral.  They describe
the small AI-owned record consumed by the Retrieval Engine while keeping
evidence locators parsed and auditable.  Control metadata from an LLM is not
part of these value objects; the producer constructs them only after source
validation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from uls.domain.enums import Explicitness, ProcessingStatus, OwnershipZone
from uls.domain.models import PageLocator, TimeLocator, parse_locator, serialize_locator
from uls.domain.source_ref import SourceFingerprint


Locator = PageLocator | TimeLocator

SESSION_ENRICHMENT_KINDS = (
    "summary",
    "topics",
    "professor_emphasis",
    "professor_examples",
    "exam_signals",
    "likely_confusions",
)
MATERIAL_ENRICHMENT_KINDS = ("content_index", "topics")


class CompletionState(str, Enum):
    """Per-required-kind completion state used by the worker audit record."""

    PRODUCED = "produced"
    LEGITIMATELY_EMPTY = "legitimately_empty"
    OMITTED_OR_FAILED = "omitted_or_failed"

    def __str__(self) -> str:
        return self.value


def _coerce_explicitness(value: Explicitness | str) -> Explicitness:
    if isinstance(value, Explicitness):
        return value
    if isinstance(value, str):
        try:
            return Explicitness(value.upper())
        except ValueError as exc:
            raise ValueError(f"unknown explicitness: {value!r}") from exc
    raise TypeError("explicitness must be Explicitness or a string")


def _coerce_locator(value: Locator | str) -> Locator:
    if isinstance(value, (PageLocator, TimeLocator)):
        return value
    if isinstance(value, str):
        return parse_locator(value)
    raise TypeError("locator must be a parsed Locator or canonical locator string")


def _mapping_value(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    wanted = {_normalized_key(name) for name in names}
    for key, item in value.items():
        if isinstance(key, str) and _normalized_key(key) in wanted:
            return item
    return default


def _mapping_has(value: Mapping[str, Any], *names: str) -> bool:
    marker = object()
    return _mapping_value(value, *names, default=marker) is not marker


def is_meaningful_quote(value: str | None) -> bool:
    """Return whether a quote is substantial enough to support EXPLICIT.

    A quote must contain at least four visible characters and three word
    characters.  A lone short word (for example ``"이"`` or ``"the"``) is
    still too easy to obtain accidentally, while short multi-word spans such
    as ``"첫 주제"`` remain valid.  Slice containment is checked by the
    producer because this schema does not have access to the derivative.
    """

    if not isinstance(value, str):
        return False
    quote = value.strip()
    if len(quote) < 4:
        return False
    words = re.findall(r"\w+", quote, flags=re.UNICODE)
    if not words or sum(len(word) for word in words) < 3:
        return False
    return len(words) > 1 or len(words[0]) >= 5


@dataclass(frozen=True)
class EvidenceLocator:
    """A parsed source locator and an optional quote from that locator."""

    locator: Locator | str
    quote: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "locator", _coerce_locator(self.locator))
        if self.quote is not None and not isinstance(self.quote, str):
            raise TypeError("evidence quote must be a string or None")
        if isinstance(self.quote, str):
            normalized_quote = self.quote.strip()
            object.__setattr__(
                self,
                "quote",
                normalized_quote if is_meaningful_quote(normalized_quote) else None,
            )

    @property
    def location(self) -> Locator:
        return self.locator  # type: ignore[return-value]

    @property
    def parsed_locator(self) -> Locator:
        return self.locator  # type: ignore[return-value]

    def as_dict(self) -> dict[str, Any]:
        return {
            "locator": serialize_locator(self.locator),
            "quote": self.quote,
        }

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | str) -> "EvidenceLocator":
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, Mapping):
            raise TypeError("evidence locator must be a mapping or locator string")
        locator: Any = None
        wanted = {"locator", "location", "source_locator", "sourcelocator"}
        for key, item in value.items():
            if isinstance(key, str) and key.casefold().replace("_", "") in {
                name.replace("_", "") for name in wanted
            }:
                locator = item
                break
        if locator is None:
            raise ValueError("evidence locator is missing locator")
        quote = _mapping_value(value, "quote", "verbatim_quote", "verbatim")
        return cls(locator, quote)


def _coerce_evidence(value: Any) -> EvidenceLocator:
    if isinstance(value, EvidenceLocator):
        return value
    return EvidenceLocator.from_mapping(value)


@dataclass(frozen=True)
class EnrichmentSignal:
    """One grounded, AI-owned enrichment signal."""

    kind: str
    content: str
    explicitness: Explicitness | str
    evidence: tuple[EvidenceLocator, ...] | Sequence[EvidenceLocator | Mapping[str, Any] | str]
    confidence: float
    symbolic_hint: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("enrichment signal kind must be a non-empty string")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("enrichment signal content must be a non-empty string")
        if not isinstance(self.symbolic_hint, str) or not self.symbolic_hint.strip():
            raise ValueError("enrichment signal symbolic_hint must be a non-empty string")
        object.__setattr__(self, "kind", self.kind.strip())
        object.__setattr__(self, "content", self.content.strip())
        object.__setattr__(self, "symbolic_hint", self.symbolic_hint.strip())
        object.__setattr__(self, "explicitness", _coerce_explicitness(self.explicitness))
        if isinstance(self.evidence, (str, bytes)):
            raise TypeError("enrichment signal evidence must be a sequence")
        evidence = tuple(_coerce_evidence(item) for item in self.evidence)
        if not evidence:
            raise ValueError("enrichment signal requires at least one evidence locator")
        object.__setattr__(self, "evidence", evidence)
        if self.explicitness is Explicitness.EXPLICIT:
            if not any(is_meaningful_quote(item.quote) for item in evidence):
                raise ValueError(
                    "EXPLICIT enrichment signal requires a meaningful verbatim quote"
                )
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("enrichment signal confidence must be a number")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("enrichment signal confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

    @property
    def ownership(self) -> OwnershipZone:
        """The fixed ownership of every persisted enrichment signal."""

        return OwnershipZone.AI

    @property
    def owner(self) -> OwnershipZone:
        return self.ownership

    @property
    def is_explicit(self) -> bool:
        return self.explicitness is Explicitness.EXPLICIT

    @property
    def is_inferred(self) -> bool:
        return self.explicitness is Explicitness.INFERRED

    def as_dict(self) -> dict[str, Any]:
        # These are the only signal fields intentionally exposed to the
        # consumer.  In particular, source_class/freshness/factual and human
        # state fields do not cross this serialization boundary.
        return {
            "kind": self.kind,
            "content": self.content,
            "explicitness": self.explicitness.value,
            "evidence": [item.as_dict() for item in self.evidence],
            "confidence": self.confidence,
            "symbolic_hint": self.symbolic_hint,
        }

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, kind: str | None = None) -> "EnrichmentSignal":
        if not isinstance(value, Mapping):
            raise TypeError("enrichment signal must be a mapping")

        def first(*names: str, default: Any = None) -> Any:
            wanted = {name.casefold().replace("_", "") for name in names}
            for key, item in value.items():
                if isinstance(key, str) and key.casefold().replace("_", "") in wanted:
                    return item
            return default

        actual_kind = first("kind", default=kind)
        content = first("content", "text", "description", "value", default=None)
        if content is None and actual_kind is not None:
            # Topic/content-index records often use their semantic field as
            # the display content.
            content = first("topic", "heading", "term", "title", default=None)
        explicitness = first("explicitness", "explicit_inferred", default=Explicitness.INFERRED)
        evidence = first("evidence", "evidence_locators", "locators", default=())
        confidence = first("confidence", "score", default=0.0)
        hint = first(
            "symbolic_hint",
            "topic",
            "heading",
            "term",
            "keyword",
            "section",
            "title",
            default=None,
        )
        if isinstance(evidence, Mapping):
            evidence = [evidence]
        if evidence is None:
            evidence = ()
        if actual_kind is None or content is None or hint is None:
            raise ValueError("enrichment signal requires kind, content, symbolic_hint, and evidence")
        return cls(
            kind=str(actual_kind),
            content=str(content),
            explicitness=explicitness,
            evidence=tuple(evidence),
            confidence=confidence,
            symbolic_hint=str(hint),
        )


SignalT = TypeVar("SignalT", bound=EnrichmentSignal)


class _PayloadMapping(Mapping[str, Any], Generic[SignalT]):
    """Make typed payloads usable by the frozen Mapping-based consumer."""

    _kind_names: tuple[str, ...] = ()

    def _as_mapping(self) -> dict[str, Any]:
        return self.as_dict()  # type: ignore[attr-defined]

    def __getitem__(self, key: str) -> Any:
        return self._as_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._as_mapping())

    def __len__(self) -> int:
        return len(self._as_mapping())


def _coerce_signal_sequence(value: Any, kind: str) -> tuple[EnrichmentSignal, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        # A single signal mapping is distinct from a mapping keyed by numeric
        # candidate IDs; the latter is accepted as a stable test-fixture form.
        if _mapping_has(value, "content", "text", "evidence", "symbolic_hint", "topic"):
            values: list[Any] = [value]
        else:
            values = list(value.values())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
    else:
        values = [value]
    result: list[EnrichmentSignal] = []
    for item in values:
        if isinstance(item, EnrichmentSignal):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(EnrichmentSignal.from_mapping(item, kind=kind))
        else:
            raise TypeError(f"{kind} payload items must be EnrichmentSignal mappings")
    return tuple(result)


def _projection(signal: EnrichmentSignal) -> dict[str, Any]:
    first_locator = signal.evidence[0].locator
    return {
        "topic": signal.symbolic_hint,
        "locator": serialize_locator(first_locator),
    }


@dataclass(frozen=True)
class SessionEnrichmentPayload(_PayloadMapping[EnrichmentSignal]):
    """Consumer-shaped session enrichment payload."""

    summary: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    topics: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    professor_emphasis: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    professor_examples: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    exam_signals: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    likely_confusions: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        for name in SESSION_ENRICHMENT_KINDS:
            object.__setattr__(self, name, _coerce_signal_sequence(getattr(self, name), name))

    def signals_for(self, kind: str) -> tuple[EnrichmentSignal, ...]:
        if kind not in SESSION_ENRICHMENT_KINDS:
            raise KeyError(kind)
        return getattr(self, kind)

    def all_signals(self) -> tuple[EnrichmentSignal, ...]:
        return tuple(signal for kind in SESSION_ENRICHMENT_KINDS for signal in self.signals_for(kind))

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            kind: [signal.as_dict() for signal in self.signals_for(kind)]
            for kind in SESSION_ENRICHMENT_KINDS
        }
        result["symbolic_hints"] = [_projection(signal) for signal in self.all_signals()]
        return result

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SessionEnrichmentPayload":
        if not isinstance(value, Mapping):
            raise TypeError("session enrichment payload must be a mapping")
        return cls(**{kind: _coerce_signal_sequence(value.get(kind, ()), kind) for kind in SESSION_ENRICHMENT_KINDS})


@dataclass(frozen=True)
class MaterialEnrichmentPayload(_PayloadMapping[EnrichmentSignal]):
    """Consumer-shaped material content-index payload."""

    content_index: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()
    topics: tuple[EnrichmentSignal, ...] | Sequence[EnrichmentSignal | Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        for name in MATERIAL_ENRICHMENT_KINDS:
            object.__setattr__(self, name, _coerce_signal_sequence(getattr(self, name), name))

    def signals_for(self, kind: str) -> tuple[EnrichmentSignal, ...]:
        if kind not in MATERIAL_ENRICHMENT_KINDS:
            raise KeyError(kind)
        return getattr(self, kind)

    def all_signals(self) -> tuple[EnrichmentSignal, ...]:
        return tuple(signal for kind in MATERIAL_ENRICHMENT_KINDS for signal in self.signals_for(kind))

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            kind: [signal.as_dict() for signal in self.signals_for(kind)]
            for kind in MATERIAL_ENRICHMENT_KINDS
        }
        result["symbolic_hints"] = [_projection(signal) for signal in self.all_signals()]
        return result

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MaterialEnrichmentPayload":
        if not isinstance(value, Mapping):
            raise TypeError("material enrichment payload must be a mapping")
        return cls(**{kind: _coerce_signal_sequence(value.get(kind, ()), kind) for kind in MATERIAL_ENRICHMENT_KINDS})


Payload = SessionEnrichmentPayload | MaterialEnrichmentPayload


def _serialize_payload(payload: Any) -> Any:
    if hasattr(payload, "as_dict") and callable(payload.as_dict):
        payload = payload.as_dict()
    return _sanitize_payload(payload)


_CONTROL_KEYS = frozenset(
    {
        "basedonsourcehash",
        "basedonsourceversion",
        "sourceclass",
        "sourcehash",
        "sourceversion",
        "freshness",
        "factual",
        "basedon",
        "processor",
        "processorversion",
        "verified",
        "scopeconfirmed",
        "decision",
        "state",
        "owner",
    }
)


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _sanitize_payload(value: Any) -> Any:
    """Strip provider control metadata at the final payload boundary."""

    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _CONTROL_KEYS:
                continue
            if normalized == "ownership":
                # Ownership is fixed by the record/write boundary, never by a
                # model-supplied payload field.  The writer emits the single
                # forced ``ownership=AI`` patch alongside this payload.
                continue
            result[key] = _sanitize_payload(item)
        return result
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_payload(item) for item in value]
    return value


def _sanitize_provider_provenance(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    forbidden = _CONTROL_KEYS | {"ownership"}
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                key: clean(child)
                for key, child in item.items()
                if isinstance(key, str) and _normalized_key(key) not in forbidden
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return None

    return clean(value)


def _try_typed_payload(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    # Old Phase 2 fakes intentionally use compact untyped mappings.  Only
    # promote a mapping when it contains an actual typed signal shape; this
    # keeps those backwards-compatible records readable by RetrievalEngine.
    values = payload.get("content_index")
    has_material_shape = "content_index" in payload and (
        "symbolic_hints" in payload
        or _contains_typed_signal(values)
        or _contains_typed_signal(payload.get("topics"))
    )
    if has_material_shape:
        try:
            return MaterialEnrichmentPayload.from_mapping(payload)
        except (TypeError, ValueError, KeyError):
            return payload

    has_session_shape = "symbolic_hints" in payload and all(
        kind in payload for kind in SESSION_ENRICHMENT_KINDS
    )
    if has_session_shape:
        try:
            return SessionEnrichmentPayload.from_mapping(payload)
        except (TypeError, ValueError, KeyError):
            return payload

    values = payload.get("content_index")
    if _contains_typed_signal(values):
        try:
            return MaterialEnrichmentPayload.from_mapping(payload)
        except (TypeError, ValueError, KeyError):
            return payload
    for kind in SESSION_ENRICHMENT_KINDS:
        values = payload.get(kind)
        if _contains_typed_signal(values):
            try:
                return SessionEnrichmentPayload.from_mapping(payload)
            except (TypeError, ValueError, KeyError):
                return payload
    return payload


def _contains_typed_signal(value: Any) -> bool:
    if isinstance(value, Mapping):
        values = value.values()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = value
    else:
        return False
    return any(
        isinstance(item, Mapping)
        and (_mapping_has(item, "evidence") or _mapping_has(item, "symbolic_hint"))
        for item in values
    )


@dataclass(frozen=True)
class EnrichmentRecord:
    """AI-derived metadata tied to the exact source version it inspected.

    ``payload`` is not source truth.  Retrieval may use it for symbolic
    routing, but only a matching fingerprint permits factual use.
    """

    payload: Any
    based_on_source_version: int
    based_on_source_hash: str
    processor_version: str
    provider_provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.based_on_source_version, bool)
            or not isinstance(self.based_on_source_version, int)
            or self.based_on_source_version < 1
        ):
            raise ValueError("based_on_source_version must be a positive integer")
        if not isinstance(self.based_on_source_hash, str) or not self.based_on_source_hash.strip():
            raise ValueError("based_on_source_hash must be a non-empty string")
        if not isinstance(self.processor_version, str) or not self.processor_version.strip():
            raise ValueError("processor_version must be a non-empty string")
        # Normalize and sanitize at construction time as well as in
        # ``as_dict``.  Retrieval consumes ``payload`` directly, so leaving a
        # raw provider mapping in memory would let a forged source_class or
        # freshness field bypass the serialization boundary.
        serialized_payload = _serialize_payload(self.payload)
        object.__setattr__(self, "payload", _try_typed_payload(serialized_payload))
        object.__setattr__(self, "based_on_source_hash", self.based_on_source_hash.strip())
        object.__setattr__(self, "processor_version", self.processor_version.strip())
        if self.provider_provenance is not None:
            if not isinstance(self.provider_provenance, Mapping):
                raise TypeError("provider_provenance must be a mapping or None")
            object.__setattr__(
                self,
                "provider_provenance",
                _sanitize_provider_provenance(self.provider_provenance),
            )

    @property
    def source_fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.based_on_source_version, self.based_on_source_hash)

    @property
    def based_on(self) -> SourceFingerprint:
        return self.source_fingerprint

    @property
    def ownership(self) -> OwnershipZone:
        return OwnershipZone.AI

    def is_fresh(self, current: SourceFingerprint) -> bool:
        return self.source_fingerprint == current

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "payload": _serialize_payload(self.payload),
            "based_on_source_version": self.based_on_source_version,
            "based_on_source_hash": self.based_on_source_hash,
            "processor_version": self.processor_version,
        }
        if self.provider_provenance is not None:
            result["provider_provenance"] = dict(self.provider_provenance)
        return result

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EnrichmentRecord":
        if not isinstance(value, Mapping):
            raise TypeError("enrichment record must be a mapping")

        def get(*names: str, default: Any = None) -> Any:
            wanted = {name.casefold().replace("_", "") for name in names}
            for key, item in value.items():
                if isinstance(key, str) and key.casefold().replace("_", "") in wanted:
                    return item
            return default

        based_on = get("based_on", "fingerprint", default=None)
        version = get("based_on_source_version", "source_version", default=None)
        source_hash = get("based_on_source_hash", "source_hash", default=None)
        if isinstance(based_on, Mapping):
            if version is None:
                version = based_on.get("source_version", based_on.get("version"))
            if source_hash is None:
                source_hash = based_on.get("source_hash", based_on.get("hash"))
        elif based_on is not None:
            if version is None:
                version = getattr(based_on, "source_version", getattr(based_on, "version", None))
            if source_hash is None:
                source_hash = getattr(based_on, "source_hash", getattr(based_on, "hash", None))
        payload = get("payload", "output", "enrichment", default=None)
        payload = _try_typed_payload(payload)
        processor = get("processor_version", "processor", default=None)
        provenance = get("provider_provenance", "provenance", default=None)
        if version is None or source_hash is None:
            raise ValueError("enrichment record is missing its based-on fingerprint")
        if processor is None:
            raise ValueError("enrichment record is missing processor_version")
        if isinstance(version, str) and version.strip().isdigit():
            version = int(version.strip())
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("enrichment source version must be an integer")
        if not isinstance(source_hash, str) or not source_hash.strip():
            raise TypeError("enrichment source hash must be a non-empty string")
        if not isinstance(processor, str) or not processor.strip():
            raise TypeError("enrichment processor version must be a non-empty string")
        return cls(payload, version, source_hash, processor, provenance)


@dataclass(frozen=True)
class EnrichmentGenerationResult:
    """Deterministic producer output plus machine-readable completeness."""

    payload: Payload
    record: EnrichmentRecord
    input_fingerprint: SourceFingerprint
    processor_version: str
    completeness: Mapping[str, CompletionState | str]
    produced_count: int
    dropped_count: int
    drop_reasons: tuple[str, ...] = ()
    status: ProcessingStatus | str = ProcessingStatus.READY
    classification: str = "proposal"
    provider_provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = {
            str(kind): CompletionState(state).value if isinstance(state, CompletionState) else str(state)
            for kind, state in self.completeness.items()
        }
        object.__setattr__(self, "completeness", normalized)
        object.__setattr__(self, "drop_reasons", tuple(str(item) for item in self.drop_reasons))
        if isinstance(self.status, str):
            object.__setattr__(self, "status", ProcessingStatus(self.status.upper()))
        if self.produced_count < 0 or self.dropped_count < 0:
            raise ValueError("generation counts must be non-negative")

    @property
    def job_status(self) -> ProcessingStatus:
        return self.status  # type: ignore[return-value]

    @property
    def ready(self) -> bool:
        return self.job_status is ProcessingStatus.READY

    @property
    def error_code(self) -> str | None:
        if "ENRICHMENT_NO_EVIDENCE" in self.drop_reasons:
            return "ENRICHMENT_NO_EVIDENCE"
        if not self.ready:
            return "ENRICHMENT_INCOMPLETE"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": _serialize_payload(self.payload),
            "completeness": dict(self.completeness),
            "produced_count": self.produced_count,
            "dropped_count": self.dropped_count,
            "drop_reasons": list(self.drop_reasons),
            "input_fingerprint": {
                "source_version": self.input_fingerprint.source_version,
                "source_hash": self.input_fingerprint.source_hash,
            },
            "processor_version": self.processor_version,
            "classification": self.classification,
            "provider_provenance": dict(self.provider_provenance),
            "status": self.job_status.value,
        }


EnrichmentOutcome = EnrichmentGenerationResult


def coerce_enrichment(value: EnrichmentRecord | Mapping[str, Any] | Any) -> EnrichmentRecord:
    if isinstance(value, EnrichmentRecord):
        return value
    if isinstance(value, Mapping):
        return EnrichmentRecord.from_mapping(value)
    if value is not None and all(
        hasattr(value, name)
        for name in (
            "payload",
            "based_on_source_version",
            "based_on_source_hash",
            "processor_version",
        )
    ):
        return EnrichmentRecord(
            payload=getattr(value, "payload"),
            based_on_source_version=getattr(value, "based_on_source_version"),
            based_on_source_hash=getattr(value, "based_on_source_hash"),
            processor_version=getattr(value, "processor_version"),
            provider_provenance=getattr(value, "provider_provenance", None),
        )
    raise TypeError("unsupported enrichment record")


__all__ = [
    "CompletionState",
    "EnrichmentGenerationResult",
    "EnrichmentOutcome",
    "EnrichmentRecord",
    "EnrichmentSignal",
    "EvidenceLocator",
    "Locator",
    "MATERIAL_ENRICHMENT_KINDS",
    "MaterialEnrichmentPayload",
    "SESSION_ENRICHMENT_KINDS",
    "SessionEnrichmentPayload",
    "is_meaningful_quote",
    "coerce_enrichment",
]
