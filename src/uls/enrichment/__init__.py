"""Fingerprint-bound enrichment value objects."""

from .schemas import (
    CompletionState,
    EnrichmentGenerationResult,
    EnrichmentOutcome,
    EnrichmentRecord,
    EnrichmentSignal,
    EvidenceLocator,
    MaterialEnrichmentPayload,
    SessionEnrichmentPayload,
    coerce_enrichment,
)

__all__ = [
    "CompletionState",
    "EnrichmentGenerationResult",
    "EnrichmentOutcome",
    "EnrichmentRecord",
    "EnrichmentSignal",
    "EvidenceLocator",
    "MaterialEnrichmentPayload",
    "SessionEnrichmentPayload",
    "coerce_enrichment",
]
