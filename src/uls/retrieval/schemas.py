"""Model-independent retrieval value objects and serialization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from uls.domain.models import ContextPackage, EvidenceItem
from uls.ephemeral.models import ResolutionCandidate, ResolvedEntity


ResolutionStatus = Literal["resolved", "ambiguous"]


@dataclass(frozen=True)
class ResolutionResult:
    """The one result shape returned by ``resolve_entity``."""

    status: ResolutionStatus
    entity: ResolvedEntity | None = None
    resolution_id: str | None = None
    candidates: tuple[ResolutionCandidate, ...] = field(default_factory=tuple)
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "ambiguous"}:
            raise ValueError("resolution status must be resolved or ambiguous")
        if self.status == "resolved" and self.entity is None:
            raise ValueError("resolved result requires an entity")
        if self.status == "ambiguous" and (
            not self.resolution_id or not self.candidates
        ):
            raise ValueError("ambiguous result requires a resolution handle and candidates")
        object.__setattr__(self, "candidates", tuple(self.candidates))

    @property
    def entity_id(self) -> str | None:
        return self.entity.entity_id if self.entity is not None else None

    @property
    def entity_type(self) -> str | None:
        return self.entity.entity_type if self.entity is not None else None

    def as_dict(self) -> dict[str, Any]:
        if self.status == "resolved":
            assert self.entity is not None
            return {
                "status": self.status,
                "entity": {
                    "type": self.entity.entity_type,
                    "id": self.entity.entity_id,
                    "name": self.entity.label,
                },
            }
        return {
            "status": self.status,
            "resolution_id": self.resolution_id,
            "expires_at": self.expires_at,
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "entity_type": candidate.entity_type,
                    "entity_id": candidate.entity_id,
                    "label": candidate.label,
                    "reason": candidate.reason,
                }
                for candidate in self.candidates
            ],
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True)
class RetrievalBudget:
    max_evidence_items: int = 12
    max_chars_per_item: int = 4000
    max_total_chars: int = 24000
    max_followup_chunks: int = 8

    def __post_init__(self) -> None:
        for name in (
            "max_evidence_items",
            "max_chars_per_item",
            "max_total_chars",
            "max_followup_chunks",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class CapabilityBinding:
    """Role/source binding kept beside the Phase 1 locator capability."""

    entity_id: str
    locator: Any
    source_hash: str
    source_version: int
    source_class: str
    source_ref: Any = None
    session_id: str | None = None
    material_id: str | None = None
    relation_required: bool = False
    provisional: bool = False
    usage_id: str | None = None

    @property
    def locator_range(self) -> Any:
        return self.locator


def _value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return value.as_dict()
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {key: _value(item) for key, item in vars(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    return value


def evidence_item_to_dict(item: EvidenceItem) -> dict[str, Any]:
    return {
        "source_class": item.source_class,
        "entity_id": item.entity_id,
        "locator": str(item.locator),
        "fingerprint": {
            "source_version": item.fingerprint.source_version,
            "source_hash": item.fingerprint.source_hash,
        },
        "authority": _value(item.authority),
        "content": item.content,
        "provenance": _value(item.provenance),
        "freshness": _value(item.freshness),
        **({"provisional": True} if getattr(item, "provisional", False) else {}),
    }


def context_package_to_dict(package: ContextPackage) -> dict[str, Any]:
    return {
        "protocol_version": package.protocol_version,
        "context_id": package.context_id,
        "entity": _value(package.entity),
        "scope": _value(package.scope),
        "sources": [evidence_item_to_dict(item) for item in package.sources],
        "professor_signals": [_value(item) for item in package.professor_signals],
        "user_context": [_value(item) for item in package.user_context],
        "warnings": [_value(item) for item in package.warnings],
    }


# ``SourceChunk`` is an intentionally descriptive alias.  EvidenceItem is
# still the canonical public domain type used in ContextPackage.sources.
SourceChunk = EvidenceItem


__all__ = [
    "CapabilityBinding",
    "ContextPackage",
    "EvidenceItem",
    "ResolutionCandidate",
    "ResolutionResult",
    "ResolvedEntity",
    "RetrievalBudget",
    "SourceChunk",
    "context_package_to_dict",
    "evidence_item_to_dict",
]
