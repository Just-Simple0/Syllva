"""v2 Retrieval Service (implementation spec §9.1-§9.2, design §7.1, I1/I5/I6, C-02, C-10).

Core calls this directly — there is no MCP hop. It is READ-ONLY: it takes a
``StateReader`` (``mode=ro`` + ``query_only``) and a ``BlobStore`` and never
imports ``StateWriter`` (enforced by the ``v2-retrieval-clean`` import contract).

Context assembly:

* evidence comes ONLY from the chunk derivative of a source's CURRENT version;
  a derivative of an older version is stale and is excluded from factual
  evidence (design I5), reported as a warning for routing;
* a PARTIAL derivative is used but flagged (``PARTIAL_SOURCE``) — never presented
  as complete (I3);
* chunks are bounded by ``RetrievalBudget``; dropping chunks is reported as
  ``PARTIAL_CONTEXT(used/total)``, never silent (spec §8.8.1). Relevance ranking of
  chunks is R1b-5; until then chunks are taken in locator order;
* every package carries a context capability: the allowlist of exactly the
  locators it contains, bound to the source hash/version that authorized them and
  to ``caller_scope`` (the UI session). ``get_source_chunk`` only serves a locator
  that the capability allows AND that is still current (spec §8.6, C-10).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from syllva.domain.enums import FreshnessStatus, SourceAuthority
from syllva.domain.errors import PolicyViolation, SyllvaError
from syllva.domain.models import (
    ContextPackage,
    EvidenceItem,
    PageLocator,
    TimeLocator,
    parse_locator,
    serialize_locator,
)
from syllva.domain.provenance import Provenance
from syllva.domain.source_ref import SourceFingerprint, v2_source_ref
from syllva.ephemeral.base import EphemeralStore
from syllva.ephemeral.models import AllowedLocator, ResolvedEntity
from syllva.library.blobs import BlobStore
from syllva.retrieval.natural import NaturalResolution, NaturalResolver
from syllva.retrieval.schemas import RetrievalBudget
from syllva.state.v2.reader import StateReader

PROTOCOL_VERSION = "2.0"
CHUNKS_SCHEMA = "syllva.chunks.v1"

_AUTHORITY_BY_KIND: dict[str, SourceAuthority] = {
    "TRANSCRIPT": SourceAuthority.PROFESSOR_TRANSCRIPT,
    "EXAM": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "ASSIGNMENT_BRIEF": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "SUPPLEMENT": SourceAuthority.SUPPLEMENTAL_REFERENCE,
}


class LocatorNotAuthorized(PolicyViolation):
    code = "LOCATOR_NOT_AUTHORIZED"


class EntityUnknown(PolicyViolation):
    code = "ENTITY_UNKNOWN"


@dataclass(frozen=True)
class _Source:
    material_id: str
    course_key: str
    kind: str
    source_id: str
    provider: str
    source_version_id: int
    source_version: int
    source_hash: str

    @property
    def fingerprint(self) -> SourceFingerprint:
        return SourceFingerprint(self.source_version, self.source_hash)


class RetrievalService:
    def __init__(
        self,
        reader: StateReader,
        blobs: BlobStore,
        ephemeral: EphemeralStore,
        *,
        budget: RetrievalBudget | None = None,
        context_ttl_seconds: int = 900,
        verify_blobs: bool = False,
        today: date | None = None,
        semester_starts: Mapping[str, date] | None = None,
    ) -> None:
        self.reader = reader
        self.blobs = blobs
        self.ephemeral = ephemeral
        self.budget = budget or RetrievalBudget()
        self.context_ttl = context_ttl_seconds
        self.verify_blobs = verify_blobs
        self.resolver = NaturalResolver(reader, ephemeral, today=today, semester_starts=semester_starts)

    # -- resolution -------------------------------------------------------------

    def resolve(self, query: str, *, course_hint: str | None = None) -> NaturalResolution:
        return self.resolver.resolve(query, course_hint=course_hint)

    def select_resolution(self, resolution_id: str, candidate_id: str) -> ResolvedEntity:
        resolved: ResolvedEntity = self.ephemeral.consume_resolution_choice(resolution_id, candidate_id)
        return resolved

    # -- context ------------------------------------------------------------------

    def get_material_context(self, material_id: str, *, caller_scope: str) -> ContextPackage:
        source = self._current_source(material_id)
        if source is None:
            raise EntityUnknown("unknown material", details={"material_id": material_id})
        entity = {"type": "material", "id": material_id, "course_key": source.course_key, "kind": source.kind}
        return self._package(entity, [source], caller_scope)

    def get_session_context(self, session_id: str, *, caller_scope: str) -> ContextPackage:
        row = self.reader.fetch_one(
            "SELECT s.id, s.session_id, c.course_key FROM sessions s JOIN courses c ON c.id = s.course_id WHERE s.session_id = ?",
            (session_id,),
        )
        if row is None:
            raise EntityUnknown("unknown session", details={"session_id": session_id})
        ids = [
            str(r["material_id"])
            for r in self.reader.fetch_all(
                "SELECT material_id FROM materials WHERE session_id = ? ORDER BY material_id", (int(row["id"]),)
            )
        ]
        sources = [s for s in (self._current_source(i) for i in ids) if s is not None]
        entity = {"type": "session", "id": session_id, "course_key": str(row["course_key"])}
        return self._package(entity, sources, caller_scope)

    def get_source_chunk(self, context_id: str, locator: str, *, caller_scope: str) -> EvidenceItem:
        """Serve one chunk for UI reading. Only allowlisted AND current locators (C-10)."""

        try:
            parsed = parse_locator(locator)
        except Exception as exc:
            raise LocatorNotAuthorized("invalid locator") from exc
        if not isinstance(parsed, (PageLocator, TimeLocator)):
            raise LocatorNotAuthorized("invalid locator")
        source = self._current_source(parsed.entity_id)
        if source is None:
            raise LocatorNotAuthorized("locator is not in a current context capability")
        try:
            allowed = self.ephemeral.authorize_locator(
                context_id, serialize_locator(parsed), caller_scope, current_fingerprint=source.fingerprint
            )
        except SyllvaError as exc:  # expired context, stale fingerprint, ... are all refusals
            raise LocatorNotAuthorized("locator is not in a current context capability", details={"cause": exc.code}) from exc
        if not allowed:
            raise LocatorNotAuthorized("locator is not in a current context capability")
        chunks, derivative = self._chunks(source)
        if derivative is None:
            raise LocatorNotAuthorized("source has no current derivative")
        wanted = serialize_locator(parsed)
        for chunk in chunks:
            if chunk["locator"] == wanted:
                return self._evidence(source, chunk, derivative)
        raise LocatorNotAuthorized("locator not found in the current derivative")

    # -- internals ------------------------------------------------------------------

    def _current_source(self, material_id: str) -> _Source | None:
        row = self.reader.fetch_one(
            "SELECT m.material_id, m.kind, c.course_key, f.source_id, f.provider, "
            "v.id AS version_id, v.source_version, v.source_hash "
            "FROM materials m JOIN courses c ON c.id = m.course_id "
            "JOIN source_files f ON f.id = m.source_file_id "
            "JOIN source_versions v ON v.source_file_id = f.id "
            "WHERE m.material_id = ? AND f.deleted_at IS NULL "
            "ORDER BY v.source_version DESC LIMIT 1",
            (material_id,),
        )
        if row is None:
            return None
        return _Source(
            str(row["material_id"]), str(row["course_key"]), str(row["kind"]), str(row["source_id"]), str(row["provider"]),
            int(row["version_id"]), int(row["source_version"]), str(row["source_hash"]),
        )

    def _chunks(self, source: _Source) -> tuple[list[dict[str, Any]], Any]:
        derivative = self.reader.fetch_one(
            "SELECT id, blob_sha256, completeness, processor_version, created_at FROM derivatives "
            "WHERE source_version_id = ? AND kind = 'chunks' ORDER BY id DESC LIMIT 1",
            (source.source_version_id,),
        )
        if derivative is None:
            return [], None
        payload = json.loads(self.blobs.read_bytes(str(derivative["blob_sha256"]), verify=self.verify_blobs))
        if payload.get("schema") != CHUNKS_SCHEMA:
            return [], None
        return [c for c in payload.get("chunks", []) if str(c.get("content", "")).strip()], derivative

    def _evidence(self, source: _Source, chunk: Mapping[str, Any], derivative: Any) -> EvidenceItem:
        completeness = str(derivative["completeness"])
        return EvidenceItem(
            source_class=source.kind,
            entity_id=source.material_id,
            locator=parse_locator(str(chunk["locator"])),
            fingerprint=source.fingerprint,
            authority=_AUTHORITY_BY_KIND.get(source.kind, SourceAuthority.PROFESSOR_MATERIAL),
            content=str(chunk["content"]),
            provenance=Provenance(
                schema=CHUNKS_SCHEMA,
                entity_id=source.material_id,
                course_key=source.course_key,
                source_ref=v2_source_ref(source.provider, source.source_id),
                source_hash=source.source_hash,
                source_version=source.source_version,
                processor_version=str(derivative["processor_version"]),
                normalized_at=str(derivative["created_at"]),
                status="ready" if completeness == "COMPLETE" else "partial",
            ),
            freshness=FreshnessStatus.FRESH,
            source_ref=v2_source_ref(source.provider, source.source_id),
        )

    def _stale_exists(self, source: _Source) -> bool:
        row = self.reader.fetch_one(
            "SELECT 1 FROM derivatives d JOIN source_versions v ON v.id = d.source_version_id "
            "JOIN source_files f ON f.id = v.source_file_id "
            "WHERE f.source_id = ? AND v.source_version < ? AND d.kind = 'chunks' LIMIT 1",
            (source.source_id, source.source_version),
        )
        return row is not None

    def _package(self, entity: Mapping[str, Any], sources: list[_Source], caller_scope: str) -> ContextPackage:
        if not caller_scope:
            raise PolicyViolation("caller_scope (the UI session) is required")
        warnings: list[dict[str, Any]] = []
        candidates: list[EvidenceItem] = []
        for source in sources:
            chunks, derivative = self._chunks(source)
            if derivative is None:
                code = "STALE_DERIVATIVE" if self._stale_exists(source) else "NOT_NORMALIZED"
                warnings.append({"code": code, "material_id": source.material_id})
                continue
            if str(derivative["completeness"]) != "COMPLETE":
                warnings.append({"code": "PARTIAL_SOURCE", "material_id": source.material_id})
            for chunk in chunks:
                locator = parse_locator(str(chunk["locator"]))
                if getattr(locator, "entity_id", None) != source.material_id:
                    warnings.append({"code": "LOCATOR_ENTITY_MISMATCH", "material_id": source.material_id})
                    break
                candidates.append(self._evidence(source, chunk, derivative))

        selected: list[EvidenceItem] = []
        total = 0
        for item in candidates:
            size = len(item.content)
            if (
                len(selected) >= self.budget.max_evidence_items
                or size > self.budget.max_chars_per_item
                or total + size > self.budget.max_total_chars
            ):
                continue
            selected.append(item)
            total += size
        if len(selected) < len(candidates):
            warnings.append({"code": "PARTIAL_CONTEXT", "used": len(selected), "total": len(candidates)})

        allowed = [
            AllowedLocator(item.locator, item.fingerprint.source_hash, item.fingerprint.source_version) for item in selected
        ]
        capability = self.ephemeral.create_context_capability(list(allowed), caller_scope, self.context_ttl)
        return ContextPackage(
            entity=dict(entity),
            scope={"caller_scope_bound": True, "materials": [s.material_id for s in sources]},
            sources=tuple(selected),
            warnings=tuple(warnings),
            protocol_version=PROTOCOL_VERSION,
            context_id=capability.context_id,
        )
