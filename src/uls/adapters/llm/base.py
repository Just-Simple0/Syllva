"""Provider-neutral LLM enrichment contract (implementation spec §35).

The adapter boundary is intentionally tiny.  An adapter receives one bounded
normalized derivative and returns structured candidates; it has no write
capability and cannot change Notion, Drive, or human-owned state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from uls.domain.enums import Explicitness
from uls.domain.source_ref import SourceFingerprint


@dataclass(frozen=True)
class LLMEnrichmentResult:
    """Structured provider output before deterministic ULS validation.

    ``based_on`` and the provenance fields are retained for observability and
    for the §35 wire contract.  They are *candidate metadata*: the producer
    must ignore the model-supplied fingerprint and inject the verified source
    fingerprint at the persistence boundary.
    """

    output: Any
    evidence: Any = field(default_factory=tuple)
    confidence: Any = 0.0
    classification: str = "proposal"
    provider_provenance: Mapping[str, Any] | Any = field(default_factory=dict)
    based_on: SourceFingerprint | Mapping[str, Any] | Any | None = None
    explicitness: Any | None = None

    def __post_init__(self) -> None:
        if self.explicitness is not None:
            value = self.explicitness
            if isinstance(value, bool):
                value = Explicitness.EXPLICIT if value else Explicitness.INFERRED
            elif isinstance(value, str):
                try:
                    value = Explicitness(value.upper())
                except ValueError as exc:
                    raise ValueError(f"unknown explicitness: {self.explicitness!r}") from exc
            elif not isinstance(value, Explicitness):
                raise TypeError("explicitness must be Explicitness, a string, or None")
            object.__setattr__(self, "explicitness", value)

    @property
    def proposal_fact_classification(self) -> str:
        """Descriptive alias for callers using the longer §35 wording."""

        return self.classification

    @property
    def proposal_or_fact(self) -> str:
        return self.classification

    def as_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "classification": self.classification,
            "provider_provenance": self.provider_provenance,
            "based_on": self.based_on,
            "explicitness": self.explicitness,
        }

    to_dict = as_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LLMEnrichmentResult":
        if not isinstance(value, Mapping):
            raise TypeError("LLM enrichment result must be a mapping")

        def first(*names: str, default: Any = None) -> Any:
            wanted = {name.casefold().replace("_", "") for name in names}
            for key, item in value.items():
                if isinstance(key, str) and key.casefold().replace("_", "") in wanted:
                    return item
            return default

        output = first("output", "candidates", "enrichment", default=None)
        evidence = first("evidence", "evidence_locators", default=())
        confidence = first("confidence", "score", default=0.0)
        classification = first(
            "classification",
            "proposal_fact_classification",
            "proposal_or_fact",
            "proposal/fact",
            default="proposal",
        )
        provenance = first(
            "provider_provenance",
            "provenance",
            "model_provenance",
            default={},
        )
        based_on = first("based_on", "fingerprint", default=None)
        explicitness = first("explicitness", "explicit_inferred", default=None)
        return cls(
            output=output,
            evidence=evidence,
            confidence=confidence,
            classification=str(classification) if classification is not None else "proposal",
            provider_provenance=provenance,
            based_on=based_on,
            explicitness=explicitness,
        )


@runtime_checkable
class LLMAdapter(Protocol):
    """The Phase 3 pure adapter surface.

    ``chunks`` is bounded context selected by the worker.  Implementations
    must not use this protocol to perform provider writes or human-only
    mutations.  Later-phase adapter operations intentionally do not appear in
    this Phase 3 protocol.
    """

    def enrich_session(
        self,
        derivative: Any,
        *,
        chunks: Sequence[Any] = (),
    ) -> LLMEnrichmentResult:
        ...

    def enrich_material(
        self,
        derivative: Any,
        *,
        chunks: Sequence[Any] = (),
    ) -> LLMEnrichmentResult:
        ...


__all__ = ["LLMAdapter", "LLMEnrichmentResult"]
