"""Mode-scoped evidence using the RetrievalEngine's existing validation boundary.

The public session collector expands every Usage, so this adapter deliberately uses
its narrow read helpers and canonical chunkers. It never fetches unrelated Material
bodies for transcript-only or explicit-selection requests.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import UUID

from uls.domain.errors import SourcePartialError, SourceUnavailableError
from uls.retrieval.chunking import page_chunks, timestamp_chunks
from uls.retrieval.scope import material_usage_scope_result, usage_app_id

from .evidence import EvidenceWaitingError, MaterialUsageEvidence, TranscriptEvidence

_LECTURE_TYPES = frozenset({"Lecture Slides", "Professor Notes"})


class RetrievalEngineEvidenceAdapter:
    def __init__(self, engine: Any, *,
                 usage_mapping_available: bool | Callable[[str], bool] = True,
                 max_chunks: int = 64, max_chars: int = 100_000) -> None:
        if type(max_chunks) is not int or max_chunks < 1 or type(max_chars) is not int or max_chars < 1:
            raise ValueError("positive evidence bounds are required")
        self._engine = engine
        self._usage_mapping_available = usage_mapping_available
        self.max_chunks, self.max_chars = max_chunks, max_chars

    def usage_mapping_available(self, session_id: str) -> bool:
        value = self._usage_mapping_available
        return bool(value(session_id) if callable(value) else value)

    def _bounded(self, chunks: Sequence[Any], *, source_class: str,
                 verified: bool, partial: bool) -> tuple[tuple[Mapping[str, Any], ...], bool]:
        selected: list[Mapping[str, Any]] = []
        length = 0
        nonempty = [chunk for chunk in chunks if chunk.content.strip()]
        for chunk in nonempty:
            if len(selected) >= self.max_chunks or length + len(chunk.content) > self.max_chars:
                partial = True
                break  # Never truncate text while retaining its full locator.
            selected.append({"locator": str(chunk.locator), "content": chunk.content,
                             "source_class": source_class, "verified": verified,
                             "partial": partial})
            length += len(chunk.content)
        return tuple(selected), partial or not selected

    def get_transcript_evidence(self, session_id: str) -> TranscriptEvidence | None:
        engine = self._engine
        session = engine._get_session(session_id)
        if session is None:
            return None
        try:
            course = engine._course_for_record(session, session_id)
            source = engine._resolve_source_ref(
                session, "Normalized Transcript", "normalized_transcript", entity_id=session_id,
            )
            result = engine._read_current_derivative(
                session_id, source, session, expected_schema="uls.transcript.v1",
                expected_course_key=course.course_key,
            )
        except (SourceUnavailableError, SourcePartialError):
            return None
        if result is None:
            return None
        derivative, fingerprint, front = result
        chunks, partial = self._bounded(
            timestamp_chunks(derivative, entity_id=session_id),
            source_class="professor_transcript", verified=False,
            partial=str(front["status"]).casefold() == "partial",
        )
        if not chunks:
            return None
        return TranscriptEvidence(source_version=fingerprint.source_version,
                                  source_hash=fingerprint.source_hash, chunks=chunks, partial=partial)

    def _scopes(self, session_id: str, selected_material: str | None = None) -> list[Any]:
        engine = self._engine
        session = engine._get_session(session_id)
        if session is None:
            raise EvidenceWaitingError("Session is unavailable")
        course = engine._course_for_record(session, session_id)
        usages = engine._material_usages(session_id)
        ids = Counter(usage_app_id(row) for row in usages)
        chosen = []
        for row in usages:
            parsed = material_usage_scope_result(row)
            scope = parsed.scope
            if scope is None:
                # Complete membership cannot be established from malformed rows.
                raise EvidenceWaitingError("Material Usage membership requires correction")
            if selected_material is not None and scope.material_id != selected_material:
                continue
            if selected_material is None and not scope.verified:
                continue
            if not scope.usage_id or ids[scope.usage_id] != 1:
                raise EvidenceWaitingError("Material Usage identity is ambiguous")
            chosen.append(row)
        warnings: list[Any] = []
        scopes = engine._safe_usage_scopes(chosen, session_id=session_id, course=course, warnings=warnings)
        if warnings or len(scopes) != len(chosen):
            raise EvidenceWaitingError("Material Usage membership or Course identity is unavailable")
        return list(scopes)

    def list_verified_lecture_usages(self, session_id: str) -> Sequence[MaterialUsageEvidence]:
        result = []
        for scope in self._scopes(session_id):
            material = self._engine._material_for_usage(scope.usage, scope.material_id)
            material_type = self._engine._material_type(material)
            if material_type in _LECTURE_TYPES:
                result.append(self._usage_evidence(scope, material, material_type))
        return result

    def get_selected_usage(self, session_id: str, material_id: str) -> MaterialUsageEvidence | None:
        scopes = self._scopes(session_id, material_id)
        # A Material with several Usage ranges cannot silently select the first.
        if len(scopes) != 1:
            return None
        scope = scopes[0]
        material = self._engine._material_for_usage(scope.usage, scope.material_id)
        return self._usage_evidence(scope, material, self._engine._material_type(material))

    def _usage_evidence(self, scope: Any, material: Any, material_type: str) -> MaterialUsageEvidence:
        engine = self._engine
        raw_id = scope.usage.get("page_id", scope.usage.get("id")) if isinstance(scope.usage, Mapping) else None
        try:
            provider_id = str(UUID(raw_id))
        except (ValueError, TypeError, AttributeError):
            raise EvidenceWaitingError("Material Usage physical identity is unavailable") from None
        chunks: tuple[Mapping[str, Any], ...] = ()
        source_hash = source_version = None
        partial = True
        try:
            course = engine._course_for_record(material, scope.material_id)
            source_class = engine._material_source_class(material)
            source = engine._resolve_source_ref(
                material, "Normalized Source", "normalized_source", entity_id=scope.material_id,
            )
            result = engine._read_current_derivative(
                scope.material_id, source, material, expected_schema="uls.material.v1",
                expected_course_key=course.course_key,
            )
            if result is not None:
                derivative, fingerprint, front = result
                source_hash, source_version = fingerprint.source_hash, fingerprint.source_version
                chunks, partial = self._bounded(
                    page_chunks(derivative, entity_id=scope.material_id,
                                start_page=scope.start_page, end_page=scope.end_page),
                    source_class=source_class, verified=scope.verified,
                    partial=str(front["status"]).casefold() == "partial",
                )
        except (SourceUnavailableError, SourcePartialError):
            pass  # Keep the eligible membership, truthfully unread/Partial.
        return MaterialUsageEvidence(
            material_app_id=scope.material_id, usage_app_id=scope.usage_id,
            provider_page_id=provider_id, usage_role=scope.role, verified=scope.verified,
            material_type=material_type, source_hash=source_hash, source_version=source_version,
            chunks=chunks, read=bool(chunks), partial=partial,
            start_page=scope.start_page, end_page=scope.end_page,
        )


__all__ = ["RetrievalEngineEvidenceAdapter"]
