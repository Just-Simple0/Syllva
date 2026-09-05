"""Structured LLM enrichment value objects.

The implementation lives in :mod:`uls.adapters.llm.base`; this module is a
convenient stable import surface for structured adapters and test fakes.
"""

from .base import LLMAdapter, LLMEnrichmentResult

__all__ = ["LLMAdapter", "LLMEnrichmentResult"]
