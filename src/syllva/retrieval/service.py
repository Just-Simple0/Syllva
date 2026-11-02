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
* chunks are bounded by ``RetrievalBudget`` (spec §8.8, C-25). The normalizer
  already splits long pages/segments into PARTS of one locator (errata E14), so a
  chunk larger than ``max_chars_per_item`` only appears with a smaller budget than
  the normalizer's; it is reported per locator as ``OVERSIZE_CHUNK``, never silently:

  - no query: locator order; selection STOPS at the first chunk that does not fit,
    so the package has no holes;
  - query (§8.8.2): any-term BM25-like score + adjacency bonus
    (``retrieval.ranking``); positive-score chunks are taken best first within the
    budget and then put back in locator order. If nothing scores (the query is
    unrelated to the material) only the first ``LOW_RELEVANCE_LEAD`` chunks are
    used with ``LOW_RELEVANCE`` — never a silent full fallback;
  - summaries (§8.8.3): ``get_*_windows`` cuts ALL chunks, in locator order, into
    windows; more than ``max_windows`` windows is reported as ``PARTIAL_COVERAGE``;

  whenever fewer chunks are packaged than exist, ``PARTIAL_CONTEXT(used/total)`` is
  reported (§8.8.1);
* every package carries a context capability: the allowlist of exactly the
  locators it contains, bound to the source hash/version that authorized them and
  to ``caller_scope`` (the UI session), and the service remembers which derivative
  and which (locator, part) pairs each package used, so ``get_source_chunk`` serves
  exactly the text that was packaged — only for allowlisted locators that are still
  current (spec §8.6, C-10). A missing/corrupt blob drops that material with
  ``BLOB_UNAVAILABLE`` instead of failing the whole package.

Lifetime rule: Core creates ONE ``RetrievalService`` per ``EphemeralStore`` and keeps
it for the store's lifetime (the context->derivative binding lives here, next to the
capabilities). Whether that binding moves into ``ContextCapability`` is decided with
the Core wiring in R2. Within one source version and schema version the newest
chunks derivative (highest id, i.e. the latest processor run) is used.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from syllva.domain.enums import FreshnessStatus, SourceAuthority
from syllva.domain.errors import PolicyViolation, SourceUnavailableError, SyllvaError
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
from syllva.library.blobs import BlobError, BlobStore
from syllva.retrieval.natural import NaturalResolution, NaturalResolver
from syllva.retrieval.ranking import score_texts
from syllva.retrieval.schemas import RetrievalBudget
from syllva.state.v2.reader import StateReader

PROTOCOL_VERSION = "2.0"
CHUNKS_SCHEMA = "syllva.chunks.v2"
CHUNKS_SCHEMA_VERSION = "2"  # derivatives.schema_version understood by this service
LOW_RELEVANCE_LEAD = 4  # §8.8.2: chunks used when no chunk matches the query
DEFAULT_MAX_WINDOWS = 16  # §8.8.3

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
class ContextWindows:
    """A whole entity cut into ordered windows for map/reduce summaries (§8.8.3).

    ``complete`` is True only when every chunk of every usable source is in some
    window; otherwise ``warnings`` says why (``PARTIAL_COVERAGE``, ``OVERSIZE_CHUNK``,
    source-level warnings).
    """

    entity: Mapping[str, Any]
    windows: tuple[ContextPackage, ...]
    warnings: tuple[Any, ...] = field(default_factory=tuple)
    complete: bool = False


@dataclass(frozen=True)
class _Packaged:
    derivative_id: int
    pieces: frozenset[tuple[str, int]]  # (serialized locator, part)


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
        # context_id -> {material_id: derivative + (locator, part) pieces used in that package}
        self._context_derivatives: dict[str, dict[str, _Packaged]] = {}
        self._lock = threading.Lock()

    # -- resolution -------------------------------------------------------------

    def resolve(self, query: str, *, course_hint: str | None = None) -> NaturalResolution:
        return self.resolver.resolve(query, course_hint=course_hint)

    def select_resolution(self, resolution_id: str, candidate_id: str) -> ResolvedEntity:
        resolved: ResolvedEntity = self.ephemeral.consume_resolution_choice(resolution_id, candidate_id)
        return resolved

    # -- context ------------------------------------------------------------------

    def get_material_context(self, material_id: str, *, caller_scope: str, query: str | None = None) -> ContextPackage:
        entity, sources = self._material(material_id)
        return self._package(entity, sources, caller_scope, query)

    def get_session_context(self, session_id: str, *, caller_scope: str, query: str | None = None) -> ContextPackage:
        entity, sources = self._session(session_id)
        return self._package(entity, sources, caller_scope, query)

    def get_material_windows(
        self, material_id: str, *, caller_scope: str, window_chars: int | None = None, max_windows: int = DEFAULT_MAX_WINDOWS
    ) -> ContextWindows:
        entity, sources = self._material(material_id)
        return self._windows(entity, sources, caller_scope, window_chars, max_windows)

    def get_session_windows(
        self, session_id: str, *, caller_scope: str, window_chars: int | None = None, max_windows: int = DEFAULT_MAX_WINDOWS
    ) -> ContextWindows:
        entity, sources = self._session(session_id)
        return self._windows(entity, sources, caller_scope, window_chars, max_windows)

    def _material(self, material_id: str) -> tuple[dict[str, Any], list[_Source]]:
        source = self._current_source(material_id)
        if source is None:
            raise EntityUnknown("unknown material", details={"material_id": material_id})
        return {"type": "material", "id": material_id, "course_key": source.course_key, "kind": source.kind}, [source]

    def _session(self, session_id: str) -> tuple[dict[str, Any], list[_Source]]:
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
        return {"type": "session", "id": session_id, "course_key": str(row["course_key"])}, sources

    def get_source_chunk(self, context_id: str, locator: str, *, caller_scope: str, part: int = 1) -> EvidenceItem:
        """Serve one packaged chunk part for UI reading. Only allowlisted AND current locators (C-10)."""

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
        wanted = serialize_locator(parsed)
        with self._lock:
            packaged = self._context_derivatives.get(context_id, {}).get(source.material_id)
        if packaged is None or (wanted, part) not in packaged.pieces:
            raise LocatorNotAuthorized("locator is not in a current context capability")
        try:
            chunks, derivative = self._chunks(source, packaged.derivative_id)
        except BlobError as exc:
            raise SourceUnavailableError("source blob is unavailable", details={"material_id": source.material_id}) from exc
        if derivative is None:
            raise LocatorNotAuthorized("the packaged derivative is no longer current")
        for chunk in chunks:
            if chunk["locator"] == wanted and int(chunk.get("part", 1)) == part:
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

    def _chunks(self, source: _Source, derivative_id: int | None = None) -> tuple[list[dict[str, Any]], Any]:
        sql = (
            "SELECT id, blob_sha256, completeness, processor_version, created_at FROM derivatives "
            "WHERE source_version_id = ? AND kind = 'chunks' AND schema_version = ?"
        )
        params: tuple[Any, ...] = (source.source_version_id, CHUNKS_SCHEMA_VERSION)
        if derivative_id is not None:
            sql += " AND id = ?"
            params += (derivative_id,)
        derivative = self.reader.fetch_one(sql + " ORDER BY id DESC LIMIT 1", params)
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
            part=int(chunk.get("part", 1)),
            parts=int(chunk.get("parts", 1)),
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

    def _collect(self, sources: list[_Source]) -> tuple[list[EvidenceItem], list[dict[str, Any]], dict[str, int]]:
        """All usable evidence of ``sources`` in locator order, plus source-level warnings."""

        warnings: list[dict[str, Any]] = []
        candidates: list[EvidenceItem] = []
        used_derivatives: dict[str, int] = {}
        for source in sources:
            try:
                chunks, derivative = self._chunks(source)
            except BlobError:
                warnings.append({"code": "BLOB_UNAVAILABLE", "material_id": source.material_id})
                continue
            if derivative is None:
                code = "STALE_DERIVATIVE" if self._stale_exists(source) else "NOT_NORMALIZED"
                warnings.append({"code": code, "material_id": source.material_id})
                continue
            items = [self._evidence(source, chunk, derivative) for chunk in chunks]
            if any(item.locator.entity_id != source.material_id for item in items):
                # a derivative normalized for another entity is not evidence for this material at all
                warnings.append({"code": "LOCATOR_ENTITY_MISMATCH", "material_id": source.material_id})
                continue
            if str(derivative["completeness"]) != "COMPLETE":
                warnings.append({"code": "PARTIAL_SOURCE", "material_id": source.material_id})
            used_derivatives[source.material_id] = int(derivative["id"])
            candidates.extend(items)
        return candidates, warnings, used_derivatives

    def _fitting(self, candidates: list[EvidenceItem], warnings: list[dict[str, Any]]) -> list[EvidenceItem]:
        fitting: list[EvidenceItem] = []
        for item in candidates:
            if len(item.content) > self.budget.max_chars_per_item:
                warnings.append({
                    "code": "OVERSIZE_CHUNK", "material_id": item.entity_id,
                    "locator": serialize_locator(item.locator), "part": item.part,
                })
            else:
                fitting.append(item)
        return fitting

    def _in_order(self, items: list[EvidenceItem], *, limit: int, chars: int) -> list[EvidenceItem]:
        selected: list[EvidenceItem] = []
        total = 0
        for item in items:
            size = len(item.content)
            if len(selected) >= limit or total + size > chars:
                break  # stop instead of skipping, so the packaged context has no holes
            selected.append(item)
            total += size
        return selected

    def _by_score(self, query: str, items: list[EvidenceItem], warnings: list[dict[str, Any]]) -> list[EvidenceItem]:
        scores = score_texts(query, [item.content for item in items])
        if not any(scores):
            warnings.append({"code": "LOW_RELEVANCE"})
            lead = min(self.budget.max_evidence_items, LOW_RELEVANCE_LEAD)
            return self._in_order(items, limit=lead, chars=self.budget.max_total_chars)
        ranked = sorted((i for i, score in enumerate(scores) if score > 0), key=lambda i: (-scores[i], i))
        chosen: list[int] = []
        total = 0
        for index in ranked:
            if len(chosen) >= self.budget.max_evidence_items:
                break
            size = len(items[index].content)
            if total + size <= self.budget.max_total_chars:  # best first; a smaller later match may still fit
                chosen.append(index)
                total += size
        return [items[i] for i in sorted(chosen)]  # back to locator order (§8.8.2 step 4)

    def _package(
        self, entity: Mapping[str, Any], sources: list[_Source], caller_scope: str, query: str | None = None
    ) -> ContextPackage:
        self._require_scope(caller_scope)
        candidates, warnings, used_derivatives = self._collect(sources)
        fitting = self._fitting(candidates, warnings)
        if query is not None and query.strip():
            selected = self._by_score(query, fitting, warnings)
        else:
            selected = self._in_order(fitting, limit=self.budget.max_evidence_items, chars=self.budget.max_total_chars)
        if len(selected) < len(candidates):
            warnings.append({"code": "PARTIAL_CONTEXT", "used": len(selected), "total": len(candidates)})
        return self._issue(entity, sources, selected, warnings, used_derivatives, caller_scope)

    def _windows(
        self, entity: Mapping[str, Any], sources: list[_Source], caller_scope: str, window_chars: int | None, max_windows: int
    ) -> ContextWindows:
        self._require_scope(caller_scope)
        if max_windows <= 0:
            raise ValueError("max_windows must be positive")
        chars = window_chars or self.budget.max_total_chars
        if chars < self.budget.max_chars_per_item:
            raise ValueError("window_chars must hold at least one full chunk (max_chars_per_item)")
        candidates, warnings, used_derivatives = self._collect(sources)
        fitting = self._fitting(candidates, warnings)
        groups: list[list[EvidenceItem]] = []
        rest = fitting
        while rest:
            window = self._in_order(rest, limit=self.budget.max_evidence_items, chars=chars)
            groups.append(window)
            rest = rest[len(window):]
        covered = groups[:max_windows]
        used = sum(len(g) for g in covered)
        if len(groups) > max_windows:
            last = covered[-1][-1]
            warnings.append({
                "code": "PARTIAL_COVERAGE", "windows_used": max_windows, "windows_needed": len(groups),
                "covered_through": {"material_id": last.entity_id, "locator": serialize_locator(last.locator), "part": last.part},
            })
        if used < len(candidates):
            warnings.append({"code": "PARTIAL_CONTEXT", "used": used, "total": len(candidates)})
        source_warnings = [w for w in warnings if w.get("material_id")]
        packages = []
        for index, group in enumerate(covered, start=1):
            materials = {item.entity_id for item in group}
            window_warnings = [{"code": "WINDOW", "index": index, "count": len(covered)}]
            window_warnings += [w for w in source_warnings if w["material_id"] in materials and w["code"] == "PARTIAL_SOURCE"]
            packages.append(self._issue(entity, sources, group, window_warnings, used_derivatives, caller_scope))
        complete = used == len(candidates) and not any(
            w["code"] in ("PARTIAL_SOURCE", "BLOB_UNAVAILABLE", "STALE_DERIVATIVE", "NOT_NORMALIZED", "LOCATOR_ENTITY_MISMATCH")
            for w in warnings
        )
        return ContextWindows(dict(entity), tuple(packages), tuple(warnings), complete and bool(packages))

    @staticmethod
    def _require_scope(caller_scope: str) -> None:
        if not caller_scope:
            raise PolicyViolation("caller_scope (the UI session) is required")

    def _issue(
        self,
        entity: Mapping[str, Any],
        sources: list[_Source],
        selected: list[EvidenceItem],
        warnings: list[dict[str, Any]],
        used_derivatives: dict[str, int],
        caller_scope: str,
    ) -> ContextPackage:
        allowed: dict[str, AllowedLocator] = {}
        pieces: dict[str, set[tuple[str, int]]] = {}
        for item in selected:
            key = serialize_locator(item.locator)
            allowed.setdefault(key, AllowedLocator(item.locator, item.fingerprint.source_hash, item.fingerprint.source_version))
            pieces.setdefault(item.entity_id, set()).add((key, item.part))
        capability = self.ephemeral.create_context_capability(list(allowed.values()), caller_scope, self.context_ttl)
        with self._lock:
            for stale_id in [c for c in self._context_derivatives if self.ephemeral.get_context_capability(c) is None]:
                del self._context_derivatives[stale_id]
            self._context_derivatives[capability.context_id] = {
                m: _Packaged(used_derivatives[m], frozenset(p)) for m, p in pieces.items()
            }
        return ContextPackage(
            entity=dict(entity),
            scope={"caller_scope_bound": True, "materials": [s.material_id for s in sources]},
            sources=tuple(selected),
            warnings=tuple(warnings),
            protocol_version=PROTOCOL_VERSION,
            context_id=capability.context_id,
        )
