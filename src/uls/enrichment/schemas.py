"""Typed persistent enrichment records and their source fingerprint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from uls.domain.source_ref import SourceFingerprint


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
        object.__setattr__(self, "based_on_source_hash", self.based_on_source_hash.strip())
        object.__setattr__(self, "processor_version", self.processor_version.strip())

    @property
    def source_fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.based_on_source_version, self.based_on_source_hash)

    @property
    def based_on(self) -> SourceFingerprint:
        return self.source_fingerprint

    def is_fresh(self, current: SourceFingerprint) -> bool:
        return self.source_fingerprint == current

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "based_on_source_version": self.based_on_source_version,
            "based_on_source_hash": self.based_on_source_hash,
            "processor_version": self.processor_version,
        }

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
        processor = get("processor_version", "processor", default=None)
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
        return cls(payload, version, source_hash, processor)


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
        )
    raise TypeError("unsupported enrichment record")


__all__ = ["EnrichmentRecord", "coerce_enrichment"]
