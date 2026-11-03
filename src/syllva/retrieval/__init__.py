"""Retrieval: the v2 read-only service (``retrieval.service``) and its building blocks.

The v1.2 ``RetrievalEngine`` and its Notion/Drive readers were removed in R1c
(spec errata E13/E19); use ``syllva.retrieval.service.RetrievalService``.
"""

from .schemas import (
    CapabilityBinding,
    ContextPackage,
    EvidenceItem,
    ResolutionResult,
    RetrievalBudget,
)

__all__ = [
    "CapabilityBinding",
    "ContextPackage",
    "EvidenceItem",
    "ResolutionResult",
    "RetrievalBudget",
]
