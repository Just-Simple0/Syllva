"""Session enrichment generation (summary, topics, and session signals)."""

from __future__ import annotations

from typing import Any

from uls.adapters.llm.base import LLMAdapter
from uls.domain.source_ref import SourceFingerprint

from ._common import (
    build_generation_result,
    call_enrichment_adapter,
    coerce_fingerprint,
    prepare_derivative,
)
from .schemas import (
    EnrichmentGenerationResult,
    SESSION_ENRICHMENT_KINDS,
)


DEFAULT_PROCESSOR_VERSION = "1.2.0"
DEFAULT_MAX_SESSION_CHUNKS = 8


class SessionEnrichmentGenerator:
    """Pure worker-side session generator.

    It reads a bounded normalized transcript, calls only
    ``LLMAdapter.enrich_session``, and performs every grounding decision
    locally.  No persistence capability is retained by this class.
    """

    def __init__(
        self,
        adapter: LLMAdapter | None = None,
        *,
        llm_adapter: LLMAdapter | None = None,
        processor_version: str = DEFAULT_PROCESSOR_VERSION,
        max_chunks: int | None = DEFAULT_MAX_SESSION_CHUNKS,
    ) -> None:
        self.adapter = adapter or llm_adapter
        if self.adapter is None:
            raise TypeError("an LLM adapter is required")
        self.processor_version = _require_processor_version(processor_version)
        self.max_chunks = max_chunks

    def generate(
        self,
        derivative: Any,
        current_fingerprint: SourceFingerprint,
        *,
        entity_id: str | None = None,
        source_ref: Any | None = None,
    ) -> EnrichmentGenerationResult:
        current = coerce_fingerprint(current_fingerprint)
        context = prepare_derivative(
            derivative,
            expected_entity_id=entity_id,
            current_fingerprint=current,
            kind="session",
            expected_source_ref=source_ref,
            max_chunks=self.max_chunks,
        )
        result = call_enrichment_adapter(self.adapter, "session", derivative, context.chunks)
        return build_generation_result(
            context=context,
            llm_result=result,
            required_kinds=SESSION_ENRICHMENT_KINDS,
            payload_kind="session",
            processor_version=self.processor_version,
        )

    # Descriptive aliases keep the generator easy to discover in worker code.
    run = generate
    enrich = generate


def generate_session_enrichment(
    derivative: Any,
    adapter: LLMAdapter | Any | None = None,
    current_fingerprint: SourceFingerprint | Any | None = None,
    *,
    llm_adapter: LLMAdapter | None = None,
    fingerprint: SourceFingerprint | Any | None = None,
    entity_id: str | None = None,
    source_ref: Any | None = None,
    processor_version: str = DEFAULT_PROCESSOR_VERSION,
    max_chunks: int | None = DEFAULT_MAX_SESSION_CHUNKS,
) -> EnrichmentGenerationResult:
    """Generate and validate one session enrichment result.

    ``llm_adapter``/``fingerprint`` are keyword aliases for worker call sites.
    A reversed ``(adapter, derivative, fingerprint)`` positional form is also
    accepted for small provider-neutral test fixtures.
    """

    if _looks_like_adapter(derivative) and adapter is not None and not _looks_like_adapter(adapter):
        derivative, adapter = adapter, derivative
    actual_adapter = llm_adapter or adapter
    if actual_adapter is None:
        raise TypeError("an LLM adapter is required")
    actual_fingerprint = fingerprint if fingerprint is not None else current_fingerprint
    if actual_fingerprint is None:
        raise TypeError("current_fingerprint is required")
    return SessionEnrichmentGenerator(
        actual_adapter,
        processor_version=processor_version,
        max_chunks=max_chunks,
    ).generate(
        derivative,
        actual_fingerprint,
        entity_id=entity_id,
        source_ref=source_ref,
    )


def _looks_like_adapter(value: Any) -> bool:
    return callable(getattr(value, "enrich_session", None))


def _require_processor_version(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("processor_version must be a non-empty string")
    return value.strip()


# Short aliases used by worker integrations that name the operation rather
# than the full payload type.
generate_session = generate_session_enrichment
enrich_session = generate_session_enrichment
SessionEnricher = SessionEnrichmentGenerator


__all__ = [
    "DEFAULT_MAX_SESSION_CHUNKS",
    "DEFAULT_PROCESSOR_VERSION",
    "SessionEnrichmentGenerator",
    "SessionEnricher",
    "enrich_session",
    "generate_session",
    "generate_session_enrichment",
]
