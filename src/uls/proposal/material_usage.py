"""Bounded, source-grounded Material Usage proposal generation."""

from __future__ import annotations

import inspect
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from uls.adapters.drive.binding import SourceBindingResolver
from uls.adapters.notion.base import upsert_proposal
from uls.domain.approval_identity import (
    build_material_usage_semantics,
    canonical_action_json,
    canonical_semantics_from_queue,
    derive_proposal_id,
    derive_usage_id,
)
from uls.domain.course_identity import (
    CourseIdentity,
    course_key_of,
    resolve_course_relation,
    validate_course_record,
)
from uls.domain.errors import (
    PolicyViolation,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.ids import strict_entity_id
from uls.domain.models import PageLocator
from uls.domain.page_range import PageRange, PageRangeResult, parse_page_range
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment._common import (
    DerivativeContext,
    prepare_derivative,
)
from uls.enrichment.schemas import EvidenceLocator
from uls.retrieval._compat import (
    field,
    raw_field,
    record_label,
    strict_text,
    unwrap,
)
from uls.retrieval.chunking import DerivativeChunk
from uls.retrieval.scope import (
    MaterialUsageScope,
    material_usage_identity,
    material_usage_identity_counts,
    material_usage_scopes,
    usage_app_id,
)

_PROPOSER_METADATA_CHAR_LIMIT = 256
_PROPOSER_REFERENCE_LIMIT = 128
_MISSING = object()


@dataclass(frozen=True)
class MaterialUsageCandidate:
    """Untrusted proposer output normalized before any write."""

    operation: str
    material_id: str
    role: str
    start_page: Any = _MISSING
    end_page: Any = _MISSING
    usage_id: str | None = None
    evidence: Any = None
    review_reason: str | None = None
    confidence: Any = None

    @property
    def page_range_result(self) -> PageRangeResult:
        if self.operation == "update_range" and (
            self.start_page is _MISSING or self.end_page is _MISSING
        ):
            raise ValueError("update_range requires both explicit page bound keys")
        return parse_page_range(
            None if self.start_page is _MISSING else self.start_page,
            None if self.end_page is _MISSING else self.end_page,
        )

    @property
    def page_range(self) -> PageRange:
        result = self.page_range_result
        if not result.is_valid or result.value is None:
            raise ValueError(result.reason or "candidate has an invalid page range")
        return result.value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MaterialUsageCandidate:
        if not isinstance(value, Mapping):
            raise TypeError("Material Usage candidate must be a mapping")

        def first(*names: str, default: Any = None) -> Any:
            wanted = {"".join(character for character in name.casefold() if character.isalnum()) for name in names}
            for key, item in value.items():
                if isinstance(key, str) and "".join(character for character in key.casefold() if character.isalnum()) in wanted:
                    return item
            return default

        material_id = first("material_id", "material", "target_material_id")
        role = first("usage_role", "role")
        operation = first("operation", "op")
        usage_id = first("usage_id", "target_usage_id", "target_entity_id")
        page_range = first("range", "page_range", default=None)
        start = first("start_page", "start", default=_MISSING)
        end = first("end_page", "end", default=_MISSING)
        if isinstance(page_range, Mapping):
            start = page_range.get("start_page", page_range.get("start", start))
            end = page_range.get("end_page", page_range.get("end", end))
        return cls(
            operation=str(operation) if operation is not None else "",
            material_id=str(material_id) if material_id is not None else "",
            role=str(role) if role is not None else "",
            start_page=start,
            end_page=end,
            usage_id=(str(usage_id) if usage_id is not None else None),
            evidence=first("evidence", "source_evidence", default=None),
            review_reason=first("review_reason", "reason", default=None),
            confidence=first("confidence", "score", default=None),
        )


@dataclass(frozen=True)
class MaterialUsageProducerResult:
    """Provider-neutral report from one directly-called producer run."""

    proposals: tuple[Any, ...] = ()
    created_usage_ids: tuple[str, ...] = ()
    skipped: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    retry_pending: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposals": list(self.proposals),
            "created_usage_ids": list(self.created_usage_ids),
            "skipped": [dict(item) for item in self.skipped],
            "warnings": list(self.warnings),
            "retry_pending": [dict(item) for item in self.retry_pending],
        }


class MaterialUsageProposalProducer:
    """Generate bounded Material Usage/PAGE_RANGE review proposals.

    ``graph_reader``, ``source_reader`` and ``writer`` are deliberately
    separate capabilities.  The proposer receives only bounded chunks and
    cannot provide authoritative IDs, fingerprints, source refs, or control
    fields.
    """

    def __init__(
        self,
        graph_reader: Any | None = None,
        source_reader: Any | None = None,
        writer: Any | None = None,
        proposer: Any | None = None,
        *,
        notion_reader: Any | None = None,
        source_binding_resolver: SourceBindingResolver | None = None,
        notion_writer: Any | None = None,
        material_usage_proposer: Any | None = None,
        config: Any | None = None,
        processor_version: str = "1.2.0",
        max_candidate_entities: int | None = None,
        max_candidate_chunks: int | None = None,
        max_chars_per_item: int | None = None,
        max_total_chars: int | None = None,
    ) -> None:
        self.graph_reader = graph_reader or notion_reader
        self.source_reader = source_reader
        self.writer = writer or notion_writer
        self.proposer = proposer or material_usage_proposer
        self.source_binding_resolver = source_binding_resolver
        self.config = config
        if not isinstance(processor_version, str) or not processor_version.strip():
            raise ValueError("processor_version must be a non-empty string")
        self.processor_version = processor_version.strip()
        self.max_candidate_entities = _positive_int(
            _configured_budget(config, "max_candidate_entities", 20)
            if max_candidate_entities is None
            else max_candidate_entities,
            "max_candidate_entities",
        )
        self.max_candidate_chunks = _positive_int(
            _configured_budget(config, "max_candidate_chunks", 12)
            if max_candidate_chunks is None
            else max_candidate_chunks,
            "max_candidate_chunks",
        )
        self.max_chars_per_item = _positive_int(
            _configured_budget(config, "max_chars_per_item", 4000)
            if max_chars_per_item is None
            else max_chars_per_item,
            "max_chars_per_item",
        )
        self.max_total_chars = _positive_int(
            _configured_budget(config, "max_total_chars", 24000)
            if max_total_chars is None
            else max_total_chars,
            "max_total_chars",
        )

    def produce(
        self,
        session_id: str,
        *,
        material_ids: Sequence[str] | None = None,
        materials: Sequence[Any] | None = None,
    ) -> MaterialUsageProducerResult:
        return self.propose(session_id, material_ids=material_ids, materials=materials)

    def run(
        self,
        session_id: str,
        *,
        material_ids: Sequence[str] | None = None,
        materials: Sequence[Any] | None = None,
    ) -> MaterialUsageProducerResult:
        return self.propose(session_id, material_ids=material_ids, materials=materials)

    def propose(
        self,
        session_id: str,
        *,
        material_ids: Sequence[str] | None = None,
        materials: Sequence[Any] | None = None,
    ) -> MaterialUsageProducerResult:
        self._require_dependencies()
        session = _reader_call(self.graph_reader, "get_session", session_id)
        if session is None:
            raise SourceUnavailableError("producer Session is unavailable")
        actual_session_id = _graph_app_id(session, "S")
        if actual_session_id != session_id:
            raise SourceUnavailableError("producer Session logical ID is missing or mismatched")
        # Validate caller-supplied Material records against the live graph
        # before resolving or reading *any* normalized source.  A stale
        # record must not be silently replaced with a current record and then
        # sent to the proposer as if the request had been valid.
        material_records = self._materials(material_ids, materials, actual_session_id)
        session_course = self._course(session, actual_session_id)
        # Validate the complete explicit graph batch before source resolution
        # or model input. Discovery may filter unsuitable candidates.
        explicit = materials is not None or material_ids is not None
        valid_materials = []
        for material in material_records:
            material_id = _graph_app_id(material, "M")
            if material_id is None:
                raise SourceUnavailableError("Material logical ID is missing")
            if self._course(material, material_id) != session_course:
                if explicit:
                    raise SourceUnavailableError("requested Material belongs to another Course")
                continue
            valid_materials.append(material)
        if materials is not None:
            for supplied, current in zip(materials, material_records, strict=True):
                current_id = _graph_app_id(current, "M")
                assert current_id is not None
                self._validate_supplied_material(supplied, current, current_id)
        # Provider records may be live mutable dictionaries. Freeze the graph
        # batch before any body read so a later read cannot rewrite its baseline.
        session = deepcopy(session)
        material_records = deepcopy(valid_materials)
        session_ref = self._source_ref(
            session,
            actual_session_id,
            ("Normalized Transcript", "normalized_transcript"),
        )
        session_fp = _fingerprint(self.source_reader, session_ref)
        initial_materials = []
        for material in material_records[: self.max_candidate_entities]:
            material_id = _graph_app_id(material, "M")
            if material_id is None:
                continue
            course = self._course(material, material_id)
            if course != session_course:
                continue
            material_type = _material_type(material)
            source_class = _material_source_class(material, self.config)
            ref = self._source_ref(material, material_id, ("Normalized Source", "normalized_source"))
            fp = _fingerprint(self.source_reader, ref)
            initial_materials.append(
                (material, material_id, course, material_type, source_class, ref, fp)
            )
        session_derivative = _source_read(self.source_reader, session_ref)
        session_context = self._prepare(
            session_derivative,
            entity_id=actual_session_id,
            fingerprint=session_fp,
            kind="session",
            source_ref=session_ref,
        )
        if session_context.front_matter.get("course_key") != session_course.course_key:
            raise SourcePartialError("Session derivative Course identity does not match the graph")

        material_contexts: dict[str, tuple[Any, CourseIdentity, SourceRef, SourceFingerprint, DerivativeContext, str, str]] = {}
        for material, material_id, course, material_type, source_class, ref, fp in initial_materials:
            derivative = _source_read(self.source_reader, ref)
            context = self._prepare(
                derivative,
                entity_id=material_id,
                fingerprint=fp,
                kind="material",
                source_ref=ref,
            )
            if context.front_matter.get("course_key") != session_course.course_key:
                raise SourcePartialError(
                    f"Material derivative Course identity does not match {material_id}"
                )
            if not context.chunks:
                raise SourcePartialError(f"Material derivative has no validated pages: {material_id}")
            material_contexts[material_id] = (
                material,
                course,
                ref,
                fp,
                context,
                material_type,
                source_class,
            )
        if not material_contexts:
            return MaterialUsageProducerResult(warnings=("no valid Material candidates",))

        existing_rows = _reader_call(self.graph_reader, "get_material_usage", actual_session_id) or []
        existing_scopes, usage_warnings = _eligible_usage_scopes(
            existing_rows, actual_session_id,
        )
        session_snapshot = _session_input_snapshot(
            session,
            session_course,
            session_ref,
            session_fp,
            session_context,
        )
        material_snapshots = {
            material_id: _material_input_snapshot(
                material_id,
                value[1],
                value[2],
                value[3],
                value[4],
                value[5],
                value[6],
            )
            for material_id, value in material_contexts.items()
        }
        existing_rows_snapshot = tuple(_usage_row_snapshot(row) for row in existing_rows)
        self._revalidate_initial_batch(
            actual_session_id, session_context, session_snapshot,
            material_contexts, material_snapshots,
        )
        session_chunks, material_slices = self._allocate_proposer_chunks(
            session_context.chunks,
            material_contexts,
        )
        proposer_result = self._call_proposer(
            session=self._proposer_session_payload(session, actual_session_id, session_course),
            session_course=session_course,
            session_chunks=session_chunks,
            materials=tuple(
                {
                    "material": self._proposer_material_payload(
                        value[0], material_id, session_course
                    ),
                    "material_id": _bounded_identifier(material_id),
                    "material_type": _bounded_text(value[5]),
                    "source_class": _bounded_text(value[6]),
                    "chunks": material_slices[material_id],
                }
                for material_id, value in material_contexts.items()
                if material_slices.get(material_id)
            ),
            existing_usages=tuple(
                _proposer_usage_payload(scope.usage)
                for scope in existing_scopes[: self.max_candidate_entities]
            ),
        )
        candidates = self._coerce_candidates(proposer_result)
        proposals: list[Any] = []
        created_ids: list[str] = []
        skipped: list[Mapping[str, Any]] = []
        warnings: list[str] = list(usage_warnings)
        retry_pending: list[Mapping[str, Any]] = []
        for candidate in candidates[: self.max_candidate_entities]:
            try:
                normalized = self._validate_candidate(
                    candidate,
                    material_contexts,
                    session_context,
                    sent_material_chunks=material_slices,
                )
                result = self._persist_candidate(
                    normalized,
                    session,
                    actual_session_id,
                    session_course,
                    session_ref,
                    session_fp,
                    session_context,
                    material_contexts,
                    existing_scopes,
                    session_snapshot,
                    material_snapshots,
                    existing_rows_snapshot,
                    material_slices,
                )
            except (TypeError, ValueError, PolicyViolation, SourcePartialError, SourceUnavailableError) as exc:
                skipped.append({"candidate": candidate, "reason": str(exc)})
                continue
            if result is None:
                skipped.append({"material_id": normalized.material_id, "reason": "verified duplicate/no-op"})
                continue
            if result.get("created_usage_id"):
                created_ids.append(result["created_usage_id"])
            if result.get("proposal") is not None:
                proposals.append(result["proposal"])
            warnings.extend(result.get("warnings", ()))
            if result.get("retry_pending") is not None:
                retry_pending.append(result["retry_pending"])
            owned_usage_snapshot = result.get("_owned_usage_snapshot")
            if owned_usage_snapshot is not None:
                # Only the exact Usage row created by this producer is added
                # to the trusted baseline.  Replacing the whole baseline with
                # a fresh provider read would silently adopt an external edit
                # made after proposer generation.
                existing_rows_snapshot = (
                    *existing_rows_snapshot,
                    owned_usage_snapshot,
                )
        return MaterialUsageProducerResult(
            proposals=tuple(proposals),
            created_usage_ids=tuple(created_ids),
            skipped=tuple(skipped),
            warnings=tuple(warnings),
            retry_pending=tuple(retry_pending),
        )

    # Descriptive aliases used by worker integrations.
    create_proposals = propose
    propose_material_usage = propose

    def _require_dependencies(self) -> None:
        if self.graph_reader is None or self.source_reader is None or self.writer is None or self.proposer is None:
            raise PolicyViolation("Material Usage producer requires graph, source, writer and proposer dependencies")
        if self.source_binding_resolver is None:
            raise PolicyViolation("Material Usage producer requires a trusted source binding resolver")
        if not callable(getattr(self.source_binding_resolver, "resolve_derivative_ref", None)):
            raise PolicyViolation("Material Usage producer source binding resolver is invalid")

    def _course(self, record: Any, entity_id: str) -> CourseIdentity:
        relation = resolve_course_relation(raw_field(record, "Course", "course", default=None))
        if relation is None:
            raise SourceUnavailableError(f"{entity_id} must have exactly one Course relation")
        course = _reader_call(self.graph_reader, "get_course_by_relation_id", relation)
        identity = validate_course_record(course, relation)
        if identity is None:
            raise SourceUnavailableError(f"{entity_id} Course identity is invalid")
        # Keep the raw Course Key validation explicit at this boundary.  The
        # shared helper deliberately does not trim identity text, so an
        # in-place whitespace/edit cannot be normalized into the old key.
        if course_key_of(course) != identity.course_key:
            raise SourceUnavailableError(f"{entity_id} Course Key is not canonical")
        return identity

    def _source_ref(self, record: Any, entity_id: str, names: Sequence[str]) -> SourceRef:
        pointer = field(record, *names, default=None)
        if not isinstance(pointer, str) or not pointer.strip():
            raise SourceUnavailableError(f"{entity_id} normalized source pointer is missing")
        assert self.source_binding_resolver is not None
        ref = self.source_binding_resolver.resolve_derivative_ref(entity_id, pointer.strip())
        if not isinstance(ref, SourceRef):
            raise SourceUnavailableError(f"{entity_id} source binding is invalid")
        return ref

    def _prepare(
        self,
        derivative: Any,
        *,
        entity_id: str,
        fingerprint: SourceFingerprint,
        kind: str,
        source_ref: SourceRef,
    ) -> DerivativeContext:
        if kind not in {"session", "material"}:
            raise ValueError("invalid derivative kind")
        return prepare_derivative(
            derivative,
            expected_entity_id=entity_id,
            current_fingerprint=fingerprint,
            kind=kind,  # type: ignore[arg-type]
            expected_source_ref=source_ref,
            # Keep the complete trusted context for page/range validation.
            # The proposer receives a separately allocated bounded slice.
            max_chunks=None,
        )

    def _materials(
        self,
        material_ids: Sequence[str] | None,
        materials: Sequence[Any] | None,
        session_id: str,
    ) -> list[Any]:
        if materials is not None:
            result: list[Any] = []
            for supplied in materials:
                supplied_id = _graph_app_id(supplied)
                if supplied_id is None:
                    raise SourcePartialError(
                        "caller-supplied Material has no canonical ID"
                    )
                current = _reader_call(self.graph_reader, "get_material", supplied_id)
                if current is None or _graph_app_id(current) != supplied_id:
                    raise SourcePartialError(
                        f"caller-supplied Material is not current: {supplied_id}"
                    )
                # Only the authoritative graph record is used downstream.
                result.append(current)
            return result
        if material_ids is not None:
            fetched: list[Any] = []
            for material_id in material_ids:
                material = _reader_call(self.graph_reader, "get_material", material_id)
                if material is None or _graph_app_id(material) != material_id:
                    raise SourceUnavailableError("requested Material logical ID is missing or mismatched")
                fetched.append(material)
            return fetched
        list_method = getattr(self.graph_reader, "list_course_materials", None)
        if callable(list_method):
            return list(list_method(session_id) or [])
        rows = _reader_call(self.graph_reader, "get_material_usage", session_id) or []
        material_ids_from_rows = {
            scope.material_id for scope in material_usage_scopes(rows, session_id=session_id)
        }
        return [
            material
            for material_id in material_ids_from_rows
            if (material := _reader_call(self.graph_reader, "get_material", material_id)) is not None
        ]

    def _validate_supplied_material(
        self,
        supplied: Any,
        current: Any,
        material_id: str,
    ) -> None:
        """Check request identity before any source reader operation.

        Display metadata is intentionally not part of this comparison.  The
        request is bound to the graph Material ID, Course, raw Type and the
        trusted canonical source identity.  A navigational pointer change
        that resolves to the same source identity remains valid.
        """

        supplied_snapshot = self._material_request_snapshot(supplied, material_id)
        current_snapshot = self._material_request_snapshot(current, material_id)
        if supplied_snapshot != current_snapshot:
            raise SourcePartialError(
                f"caller-supplied Material is stale or semantically different: {material_id}"
            )

    def _material_request_snapshot(
        self,
        material: Any,
        material_id: str,
    ) -> tuple[str, CourseIdentity, str, tuple[str, str]]:
        if _graph_app_id(material) != material_id:
            raise SourcePartialError(f"Material identity does not match {material_id}")
        course = self._course(material, material_id)
        material_type = _material_type(material)
        ref = self._source_ref(
            material,
            material_id,
            ("Normalized Source", "normalized_source"),
        )
        return material_id, course, material_type, ref.identity

    def _call_proposer(self, **kwargs: Any) -> Any:
        method = getattr(self.proposer, "propose_material_usage", None)
        if not callable(method):
            raise PolicyViolation("proposer has no propose_material_usage method")
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(**kwargs)
        if any(parameter.kind is parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return method(**kwargs)
        supported = {name: value for name, value in kwargs.items() if name in signature.parameters}
        if supported:
            return method(**supported)
        positional = [
            parameter for parameter in signature.parameters.values()
            if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
        ]
        return method(*tuple(kwargs.values())[: len(positional)])

    def _allocate_proposer_chunks(
        self,
        session_chunks: Sequence[Any],
        material_contexts: Mapping[str, Any],
    ) -> tuple[tuple[DerivativeChunk, ...], dict[str, tuple[DerivativeChunk, ...]]]:
        """Allocate one request-wide, deterministic fair source budget.

        The first allocation round gives each available source one chunk when
        the request-wide chunk/character budgets permit it.  Later rounds are
        round-robin and divide the remaining character budget among sources
        that still have chunks.  A Material with no allocated chunk is omitted
        from the proposer payload, so it cannot be selected from unseen text.
        """

        sources: list[tuple[str, Sequence[Any]]] = [("__session__", session_chunks)]
        sources.extend(
            (material_id, value[4].chunks)
            for material_id, value in material_contexts.items()
        )
        selected: dict[str, list[DerivativeChunk]] = {
            source_id: [] for source_id, _ in sources
        }
        cursors = {source_id: 0 for source_id, _ in sources}
        remaining_slots = self.max_candidate_chunks
        remaining_chars = self.max_total_chars

        while remaining_slots > 0 and remaining_chars > 0:
            active = [
                (source_id, chunks)
                for source_id, chunks in sources
                if cursors[source_id] < len(chunks)
            ]
            if not active:
                break
            progressed = False
            for active_index, (source_id, chunks) in enumerate(active):
                if remaining_slots <= 0 or remaining_chars <= 0:
                    break
                if cursors[source_id] >= len(chunks):
                    continue
                chunk = chunks[cursors[source_id]]
                cursors[source_id] += 1
                raw_content = str(getattr(chunk, "content", ""))
                if not raw_content:
                    continue
                remaining_in_round = len(active) - active_index
                share = max(1, remaining_chars // max(1, remaining_in_round))
                take = min(
                    len(raw_content),
                    self.max_chars_per_item,
                    remaining_chars,
                    share,
                )
                if take <= 0:
                    continue
                selected[source_id].append(replace(chunk, content=raw_content[:take]))
                remaining_slots -= 1
                remaining_chars -= take
                progressed = True
            if not progressed:
                break

        session_selected = tuple(selected["__session__"])
        material_selected = {
            material_id: tuple(selected[material_id])
            for material_id, _ in sources[1:]
            if selected[material_id]
        }
        return session_selected, material_selected

    def _bounded_chunks(self, chunks: Sequence[Any]) -> tuple[Any, ...]:
        """Compatibility helper for integrations that call this private path."""

        selected, _ = self._allocate_proposer_chunks(chunks, {})
        return selected

    def _proposer_session_payload(
        self,
        session: Any,
        session_id: str,
        course: CourseIdentity,
    ) -> dict[str, Any]:
        return {
            "ID": _bounded_identifier(session_id),
            "Name": _bounded_text(record_label(session)),
            "Course Key": _bounded_text(course.course_key),
        }

    def _proposer_material_payload(
        self,
        material: Any,
        material_id: str,
        course: CourseIdentity,
    ) -> dict[str, Any]:
        return {
            "ID": _bounded_identifier(material_id),
            "Name": _bounded_text(record_label(material)),
            "Course Key": _bounded_text(course.course_key),
        }

    def _coerce_candidates(self, value: Any) -> list[MaterialUsageCandidate]:
        if hasattr(value, "output"):
            value = value.output
        if isinstance(value, Mapping):
            for key in ("candidates", "material_usages", "usages", "items", "output"):
                nested = value.get(key)
                if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                    value = nested
                    break
            else:
                value = [value] if any(key in value for key in ("material_id", "material", "role")) else []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise PolicyViolation("proposer returned no structured Material Usage candidates")
        result: list[MaterialUsageCandidate] = []
        for item in value:
            if isinstance(item, MaterialUsageCandidate):
                result.append(item)
            elif isinstance(item, Mapping):
                result.append(MaterialUsageCandidate.from_mapping(item))
        return result

    def _validate_candidate(
        self,
        candidate: MaterialUsageCandidate,
        material_contexts: Mapping[str, Any],
        session_context: DerivativeContext,
        *,
        sent_material_chunks: Mapping[str, Sequence[DerivativeChunk]],
    ) -> MaterialUsageCandidate:
        if candidate.operation not in {"create_usage", "update_range"}:
            raise ValueError("candidate operation must be explicitly create_usage or update_range")
        if not candidate.material_id or candidate.material_id not in material_contexts:
            raise ValueError("candidate Material is not one of the validated inputs")
        if candidate.role not in {"Primary", "Supporting", "Reference"}:
            raise ValueError("candidate Role is unsupported")
        if candidate.review_reason is not None:
            _validate_review_reason(candidate.review_reason)
        page_range = candidate.page_range
        context = material_contexts[candidate.material_id][4]
        if not _range_has_pages(context, page_range):
            raise ValueError("candidate page range is not present in the current indexed Material")
        supplied_chunks = tuple(sent_material_chunks.get(candidate.material_id, ()))
        if not supplied_chunks:
            raise ValueError("candidate Material was not supplied to the proposer")
        evidence = candidate.evidence
        values = (
            tuple(evidence)
            if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes))
            else (() if evidence is None else (evidence,))
        )
        if values:
            normalized_evidence: list[dict[str, Any]] = []
            for item in values:
                raw_quote: Any = None
                if isinstance(item, EvidenceLocator):
                    evidence_locator = item
                    raw_quote = item.quote
                else:
                    if isinstance(item, str):
                        item = {"locator": item}
                    if not isinstance(item, Mapping):
                        raise TypeError("candidate evidence is malformed")
                    locator_value = item.get("locator", item.get("location"))
                    if locator_value is None:
                        raise ValueError("candidate evidence has no locator")
                    raw_quote = item.get("quote")
                    evidence_locator = EvidenceLocator.from_mapping(
                        {"locator": locator_value, "quote": raw_quote}
                    )
                locator = evidence_locator.locator
                if not isinstance(locator, PageLocator) or locator.entity_id != candidate.material_id:
                    raise ValueError("candidate evidence must be a Material page locator")
                if not _page_range_contains(page_range, locator.start_page, locator.end_page):
                    raise ValueError("candidate evidence is outside the proposed Material range")
                source_slice = _bounded_material_source_slice(locator, supplied_chunks)
                if source_slice is None:
                    raise ValueError("candidate evidence is outside the supplied Material slice")
                if raw_quote is not None and not isinstance(raw_quote, str):
                    raise TypeError("candidate evidence quote must be a string or None")
                if isinstance(raw_quote, str) and raw_quote.strip() and evidence_locator.quote is None:
                    raise ValueError("candidate evidence quote is invalid")
                if evidence_locator.quote is not None and evidence_locator.quote not in source_slice:
                    raise ValueError("candidate evidence quote is not in the supplied Material slice")
                normalized_evidence.append(evidence_locator.as_dict())
            candidate = replace(candidate, evidence=normalized_evidence)
        else:
            candidate = replace(candidate, evidence=None)
            if candidate.review_reason is None:
                candidate = replace(
                    candidate,
                    review_reason="ungrounded AI suggestion; human review required",
                )
        return candidate

    def _persist_candidate(
        self,
        candidate: MaterialUsageCandidate,
        session: Any,
        session_id: str,
        course: CourseIdentity,
        session_ref: SourceRef,
        session_fp: SourceFingerprint,
        session_context: DerivativeContext,
        material_contexts: Mapping[str, Any],
        existing_scopes: Sequence[MaterialUsageScope],
        session_snapshot: Any,
        material_snapshots: Mapping[str, Any],
        existing_rows_snapshot: Sequence[Any],
        sent_material_chunks: Mapping[str, Sequence[DerivativeChunk]],
    ) -> dict[str, Any] | None:
        (
            session,
            course,
            session_ref,
            session_fp,
            session_context,
            material_contexts,
            existing_rows,
        ) = self._reread_trusted_inputs(
            candidate=candidate,
            session_id=session_id,
            session_snapshot=session_snapshot,
            material_snapshots=material_snapshots,
            original_session_course=course,
            original_session_ref=session_ref,
            original_session_fp=session_fp,
            original_session_context=session_context,
            original_material_contexts=material_contexts,
            existing_rows_snapshot=existing_rows_snapshot,
        )
        existing_scopes, _ = _eligible_usage_scopes(existing_rows, session_id)
        candidate = self._validate_candidate(
            candidate,
            material_contexts,
            session_context,
            sent_material_chunks=sent_material_chunks,
        )
        (
            material,
            _,
            material_ref,
            material_fp,
            _,
            material_type,
            source_class,
        ) = material_contexts[candidate.material_id]
        candidate_identity = (
            session_id,
            candidate.material_id,
            candidate.role,
            candidate.page_range,
        )
        raw_identity_counts = material_usage_identity_counts(
            existing_rows,
            session_id=session_id,
        )
        raw_identity_count = raw_identity_counts.get(candidate_identity, 0)
        same = [
            scope
            for scope in existing_scopes
            if scope.session_id == session_id
            and scope.material_id == candidate.material_id
            and scope.role == candidate.role
            and scope.range == candidate.page_range
        ]
        owned_usage_snapshot: Any | None = None
        is_new_usage = False
        if candidate.operation == "update_range":
            if not candidate.usage_id:
                raise ValueError("update_range requires an explicit usage_id")
            targets = [scope for scope in existing_scopes if scope.usage_id == candidate.usage_id]
            if len(targets) != 1:
                raise ValueError("update_range target Usage ID is missing or ambiguous")
            target = targets[0]
            if (
                target.material_id != candidate.material_id
                or target.session_id != session_id
                or target.role != candidate.role
            ):
                raise ValueError("update_range target Usage relation does not match the candidate")
            if raw_identity_count > 1 or (
                raw_identity_count == 1
                and any(
                    material_usage_identity(row) == candidate_identity
                    and _producer_usage_app_id(row) != target.usage_id
                    for row in existing_rows
                )
            ):
                return {"warnings": ("duplicate exact Material Usage rows require review",)}
            if target.range == candidate.page_range:
                return None
            operation = "update_range"
            target_id = target.usage_id
            if target_id is None:
                raise ValueError("update_range target Usage ID is missing")
            old_scope = target
        else:
            operation = "create_usage"
            if raw_identity_count > 1:
                return {"warnings": ("duplicate exact Material Usage rows require review",)}
            if raw_identity_count == 1 and not same:
                return {
                    "warnings": (
                        (
                            "exact Material Usage identity is occupied by a "
                            "malformed row; review required"
                        ),
                    )
                }
            if same:
                if same[0].verified:
                    return None
                operation = "create_usage"
                target_id = same[0].usage_id
                if target_id is None:
                    raise ValueError("existing Usage ID is missing")
                old_scope = same[0]
            else:
                target_id = derive_usage_id(
                    session_id,
                    candidate.material_id,
                    candidate.role,
                    candidate.page_range,
                )
                is_new_usage = True
                old_scope = MaterialUsageScope(
                    usage={"ID": target_id},
                    material_id=candidate.material_id,
                    verified=False,
                    start_page=candidate.page_range.start_page,
                    end_page=candidate.page_range.end_page,
                    role=candidate.role,
                    session_id=session_id,
                    page_range=candidate.page_range,
                )
        evidence = _canonical_evidence(candidate.evidence)
        confidence = (
            _confidence_select(candidate.confidence)
            if candidate.confidence is not None
            else None
        )
        if candidate.review_reason is not None:
            _validate_review_reason(candidate.review_reason)
        old_snapshot = _old_snapshot(old_scope)
        semantics = build_material_usage_semantics(
            operation=operation,
            target_entity_id=target_id,
            session_id=session_id,
            material_id=candidate.material_id,
            course_relation_page_id=course.relation_page_id,
            course_key=course.course_key,
            usage_role=candidate.role,
            material_type=material_type,
            source_class=source_class,
            old_snapshot=old_snapshot,
            desired_range=candidate.page_range,
            session_dependency=_dependency(session_ref, session_fp),
            material_dependency=_dependency(material_ref, material_fp),
            evidence=evidence,
            review_reason=candidate.review_reason,
            processor_version=self.processor_version,
        )
        proposal_type = "MATERIAL_USAGE" if operation == "create_usage" else "PAGE_RANGE"
        proposal_id = derive_proposal_id(proposal_type, semantics)
        queue_properties = _queue_properties(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
            target_id=target_id,
            course=course,
            material_ref=material_ref,
            material_fp=material_fp,
            confidence=confidence,
            evidence=evidence,
            review_reason=candidate.review_reason,
            material=material,
        )
        _validate_queue_properties(
            queue_properties,
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            semantics=semantics,
        )
        if is_new_usage:
            # The final read in _reread_trusted_inputs established the complete
            # sibling set; only pure canonical construction occurs before this
            # write. Exact siblings have already selected their actual ID above.
            if any(_producer_usage_app_id(row) == target_id for row in existing_rows):
                return {"warnings": ("generated Usage ID is already in use",)}
            self._create_usage(
                session,
                material,
                target_id,
                candidate.role,
                candidate.page_range,
            )
            try:
                owned_usage_snapshot = _capture_owned_usage_snapshot(
                    existing_rows, self.graph_reader, session_id, target_id,
                )
            except Exception as exc:
                return _queue_retry_result(target_id, proposal_id, True, exc)
        if self.writer is None:
            raise PolicyViolation("Material Usage producer writer is unavailable")
        try:
            proposal = upsert_proposal(self.writer, queue_properties)
        except Exception as exc:
            result = _queue_retry_result(target_id, proposal_id, is_new_usage, exc)
            if owned_usage_snapshot is not None:
                result["_owned_usage_snapshot"] = owned_usage_snapshot
            return result
        result = {
            "proposal": proposal,
            "created_usage_id": target_id if is_new_usage else None,
        }
        if owned_usage_snapshot is not None:
            result["_owned_usage_snapshot"] = owned_usage_snapshot
        return result

    def _create_usage(
        self,
        session: Any,
        material: Any,
        usage_id: str,
        role: str,
        page_range: PageRange,
    ) -> Any:
        session_id = _graph_app_id(session)
        material_id = _graph_app_id(material)
        if session_id is None or material_id is None:
            raise PolicyViolation("Usage creation requires canonical Session and Material IDs")
        properties = {
            "Name": f"{record_label(material) or material_id} · {role}",
            "ID": usage_id,
            "Session": {"relation": [{"id": session_id}]},
            "Material": {"relation": [{"id": material_id}]},
            "Role": role,
            "Start Page": page_range.start_page,
            "End Page": page_range.end_page,
            "Verified": False,
        }
        method = getattr(self.writer, "create_entity", None)
        if not callable(method):
            raise PolicyViolation("guarded writer has no Usage create boundary")
        return method("Material Usage", properties)

    def _revalidate_initial_batch(
        self,
        session_id: str,
        session_context: DerivativeContext,
        session_snapshot: Any,
        material_contexts: Mapping[str, Any],
        material_snapshots: Mapping[str, Any],
    ) -> None:
        """Re-establish the entire model-input batch after all body reads.

        Contexts have already passed Ready/provenance validation against the
        captured fingerprints. Reuse those bodies; only current graph, trusted
        bindings and fingerprints need another read before model disclosure.
        """

        session = _reader_call(self.graph_reader, "get_session", session_id)
        if _graph_app_id(session, "S") != session_id:
            raise SourcePartialError("Session logical ID changed during initial body reads")
        course = self._course(session, session_id)
        ref = self._source_ref(session, session_id, ("Normalized Transcript", "normalized_transcript"))
        fingerprint = _fingerprint(self.source_reader, ref)
        if _session_input_snapshot(
            session, course, ref, fingerprint, session_context,
        ) != session_snapshot:
            raise SourcePartialError("trusted Session inputs changed during initial body reads")
        for material_id, values in material_contexts.items():
            material = _reader_call(self.graph_reader, "get_material", material_id)
            if _graph_app_id(material, "M") != material_id:
                raise SourcePartialError("Material logical ID changed during initial body reads")
            material_course = self._course(material, material_id)
            material_ref = self._source_ref(
                material, material_id, ("Normalized Source", "normalized_source"),
            )
            material_fp = _fingerprint(self.source_reader, material_ref)
            if _material_input_snapshot(
                material_id, material_course, material_ref, material_fp,
                values[4], _material_type(material), _material_source_class(material, self.config),
            ) != material_snapshots[material_id]:
                raise SourcePartialError("trusted Material inputs changed during initial body reads")

    def _reread_trusted_inputs(
        self,
        *,
        candidate: MaterialUsageCandidate,
        session_id: str,
        session_snapshot: Any,
        material_snapshots: Mapping[str, Any],
        original_session_course: CourseIdentity,
        original_session_ref: SourceRef,
        original_session_fp: SourceFingerprint,
        original_session_context: DerivativeContext,
        original_material_contexts: Mapping[str, Any],
        existing_rows_snapshot: Sequence[Any],
    ) -> tuple[
        Any,
        CourseIdentity,
        SourceRef,
        SourceFingerprint,
        DerivativeContext,
        dict[str, tuple[Any, CourseIdentity, SourceRef, SourceFingerprint, DerivativeContext, str, str]],
        list[Any],
    ]:
        """Re-read trusted graph/source inputs immediately before a write."""

        fresh_session = _reader_call(self.graph_reader, "get_session", session_id)
        if fresh_session is None or _graph_app_id(fresh_session) != session_id:
            raise SourcePartialError("Session changed or became unavailable after proposal generation")
        fresh_course = self._course(fresh_session, session_id)
        fresh_session_ref = self._source_ref(
            fresh_session,
            session_id,
            ("Normalized Transcript", "normalized_transcript"),
        )
        fresh_session_fp = _fingerprint(self.source_reader, fresh_session_ref)
        fresh_session_context = self._prepare(
            _source_read(self.source_reader, fresh_session_ref),
            entity_id=session_id,
            fingerprint=fresh_session_fp,
            kind="session",
            source_ref=fresh_session_ref,
        )
        fresh_session_snapshot = _session_input_snapshot(
            fresh_session,
            fresh_course,
            fresh_session_ref,
            fresh_session_fp,
            fresh_session_context,
        )
        if fresh_session_snapshot != session_snapshot:
            raise SourcePartialError("trusted Session inputs changed after proposal generation")
        if fresh_session_context.front_matter.get("course_key") != fresh_course.course_key:
            raise SourcePartialError("Session derivative Course identity changed")

        fresh_material_contexts: dict[
            str, tuple[Any, CourseIdentity, SourceRef, SourceFingerprint, DerivativeContext, str, str]
        ] = {}
        candidate_original = original_material_contexts.get(candidate.material_id)
        if candidate_original is None or candidate.material_id not in material_snapshots:
            raise SourcePartialError("candidate Material is no longer a trusted input")
        for material_id in (candidate.material_id,):
            fresh_material = _reader_call(self.graph_reader, "get_material", material_id)
            if fresh_material is None or _graph_app_id(fresh_material) != material_id:
                raise SourcePartialError(f"Material {material_id} changed or became unavailable")
            fresh_material_course = self._course(fresh_material, material_id)
            fresh_type = _material_type(fresh_material)
            fresh_source_class = _material_source_class(fresh_material, self.config)
            fresh_ref = self._source_ref(
                fresh_material,
                material_id,
                ("Normalized Source", "normalized_source"),
            )
            fresh_fp = _fingerprint(self.source_reader, fresh_ref)
            fresh_context = self._prepare(
                _source_read(self.source_reader, fresh_ref),
                entity_id=material_id,
                fingerprint=fresh_fp,
                kind="material",
                source_ref=fresh_ref,
            )
            if fresh_context.front_matter.get("course_key") != fresh_material_course.course_key:
                raise SourcePartialError(f"Material derivative Course identity changed for {material_id}")
            fresh_material_snapshot = _material_input_snapshot(
                material_id,
                fresh_material_course,
                fresh_ref,
                fresh_fp,
                fresh_context,
                fresh_type,
                fresh_source_class,
            )
            if fresh_material_snapshot != material_snapshots[material_id]:
                raise SourcePartialError(f"trusted Material inputs changed for {material_id}")
            fresh_material_contexts[material_id] = (
                fresh_material,
                fresh_material_course,
                fresh_ref,
                fresh_fp,
                fresh_context,
                fresh_type,
                fresh_source_class,
            )

        # Body reads are external calls. Re-establish both dependencies after
        # all bodies have returned, including changes made during another
        # source's read. This narrows the visibility window without claiming CAS.
        post_session = _reader_call(self.graph_reader, "get_session", session_id)
        if post_session is None or _graph_app_id(post_session, "S") != session_id:
            raise SourcePartialError("Session changed during final derivative reads")
        post_course = self._course(post_session, session_id)
        post_ref = self._source_ref(post_session, session_id, ("Normalized Transcript", "normalized_transcript"))
        post_fp = _fingerprint(self.source_reader, post_ref)
        if _session_input_snapshot(
            post_session, post_course, post_ref, post_fp, fresh_session_context,
        ) != session_snapshot:
            raise SourcePartialError("trusted Session inputs changed during final derivative reads")
        for material_id, values in fresh_material_contexts.items():
            post_material = _reader_call(self.graph_reader, "get_material", material_id)
            if post_material is None or _graph_app_id(post_material, "M") != material_id:
                raise SourcePartialError("Material changed during final derivative reads")
            post_material_course = self._course(post_material, material_id)
            post_material_ref = self._source_ref(
                post_material, material_id, ("Normalized Source", "normalized_source"),
            )
            post_material_fp = _fingerprint(self.source_reader, post_material_ref)
            if _material_input_snapshot(
                material_id, post_material_course, post_material_ref, post_material_fp,
                values[4], _material_type(post_material), _material_source_class(post_material, self.config),
            ) != material_snapshots[material_id]:
                raise SourcePartialError("trusted Material inputs changed during final derivative reads")

        fresh_rows = list(_reader_call(self.graph_reader, "get_material_usage", session_id) or [])
        if not _usage_rows_snapshot_equal(fresh_rows, existing_rows_snapshot) and not _only_exact_siblings_added(
            fresh_rows, existing_rows_snapshot, candidate, session_id,
        ):
            raise SourcePartialError("Material Usage rows changed after proposal generation")
        if candidate.material_id not in fresh_material_contexts:
            raise SourcePartialError("candidate Material is no longer a trusted input")
        return (
            fresh_session,
            fresh_course,
            fresh_session_ref,
            fresh_session_fp,
            fresh_session_context,
            fresh_material_contexts,
            fresh_rows,
        )


MaterialUsageProducer = MaterialUsageProposalProducer


def _graph_app_id(record: Any, expected_type: str | None = None) -> str | None:
    """Only the frozen logical property may establish graph identity."""
    value = usage_app_id(record)
    if value is None:
        return None
    if expected_type is not None:
        return strict_entity_id(value, expected_type)
    return strict_entity_id(value, "S") or strict_entity_id(value, "M")


def _queue_retry_result(usage_id: str, proposal_id: str, created: bool, error: Exception) -> dict[str, Any]:
    return {
        "created_usage_id": usage_id if created else None,
        "retry_pending": {
            "usage_id": usage_id, "proposal_id": proposal_id,
            "state": "QUEUE_RECONCILIATION_REQUIRED",
            "unverified_orphan_possible": created,
            "error_code": getattr(error, "code", type(error).__name__),
        },
        "warnings": ("Usage retained; Queue persistence requires reconciliation/retry",),
    }


def _reader_call(reader: Any, method_name: str, *args: Any) -> Any:
    method = getattr(reader, method_name, None)
    if not callable(method):
        raise SourceUnavailableError(f"reader has no {method_name} method")
    try:
        return method(*args)
    except (KeyError, LookupError, FileNotFoundError):
        return None
    except Exception as exc:
        raise SourceUnavailableError(f"{method_name} is unavailable") from exc


def _fingerprint(reader: Any, ref: SourceRef) -> SourceFingerprint:
    try:
        value = reader.get_current_fingerprint(ref)
    except Exception as exc:
        raise SourceUnavailableError("source fingerprint is unavailable") from exc
    if isinstance(value, SourceFingerprint):
        version, source_hash = value.source_version, value.source_hash
    elif isinstance(value, Mapping):
        version = value.get("source_version", value.get("version"))
        source_hash = value.get("source_hash", value.get("hash"))
    else:
        raise SourceUnavailableError("source fingerprint is invalid")
    if (
        type(version) is int and version >= 1
        and isinstance(source_hash, str) and source_hash
        and source_hash == source_hash.strip()
    ):
        return SourceFingerprint(version, source_hash)
    raise SourceUnavailableError("source fingerprint is invalid")


def _source_read(reader: Any, ref: SourceRef) -> Any:
    try:
        return reader.read_derived(ref)
    except Exception as exc:
        raise SourceUnavailableError("normalized source is unavailable") from exc


def _material_type(material: Any) -> str:
    # Type is a Select identity value.  Do not trim it into an allowed
    # deployment option; retrieval uses the same strict raw-value rule.
    value = strict_text(
        raw_field(material, "Type", "material_type", default=None),
        default=None,
    )
    if value is None:
        raise SourceUnavailableError("Material Type is missing")
    return value


def _material_source_class(material: Any, config: Any) -> str:
    from uls.retrieval.authority import material_source_class

    value = _material_type(material)
    mapping = _config_mapping(config, "material_type_source_class")
    result = material_source_class(value, mapping)
    if result is None:
        raise SourceUnavailableError("Material Type is not mapped to a supported source class")
    return result


def _config_mapping(config: Any, name: str) -> Mapping[str, str]:
    if config is None:
        from uls.config.schema import RetrievalCfg

        return RetrievalCfg().material_type_source_class
    section = config.get("retrieval", config) if isinstance(config, Mapping) else getattr(config, "retrieval", config)
    value = section.get(name, {}) if isinstance(section, Mapping) else getattr(section, name, {})
    return value if isinstance(value, Mapping) else {}


def _configured_budget(config: Any, name: str, default: int) -> Any:
    """Read one producer budget from the configured Retrieval section."""

    if config is None:
        return default
    section = (
        config.get("retrieval", config)
        if isinstance(config, Mapping)
        else getattr(config, "retrieval", config)
    )
    if isinstance(section, Mapping):
        return section.get(name, default)
    return getattr(section, name, default)


def _bounded_text(value: Any, *, limit: int = _PROPOSER_METADATA_CHAR_LIMIT) -> str:
    """Expose only a bounded display/metadata string to the proposer."""

    if isinstance(value, str):
        return value[:limit]
    if value is None:
        return ""
    return str(value)[:limit]


def _bounded_identifier(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyViolation("proposer identifier is missing")
    if len(value) > _PROPOSER_REFERENCE_LIMIT:
        raise PolicyViolation("proposer identifier exceeds the bounded input limit")
    return value


def _page_range_contains(page_range: PageRange, start_page: int, end_page: int) -> bool:
    if page_range.is_whole_source:
        return True
    assert page_range.start_page is not None and page_range.end_page is not None
    return page_range.start_page <= start_page and end_page <= page_range.end_page


def _bounded_material_source_slice(
    locator: PageLocator,
    supplied_chunks: Sequence[DerivativeChunk],
) -> str | None:
    """Resolve a Material locator using only the chunks sent to the proposer."""

    matching: list[DerivativeChunk] = []
    for chunk in supplied_chunks:
        chunk_locator = chunk.locator
        if not isinstance(chunk_locator, PageLocator):
            continue
        if (
            chunk_locator.entity_id == locator.entity_id
            and chunk_locator.subtype == locator.subtype
            and chunk_locator.end_page >= locator.start_page
            and chunk_locator.start_page <= locator.end_page
        ):
            matching.append(chunk)
    if not matching:
        return None
    intervals: list[tuple[int, int]] = []
    for chunk in matching:
        chunk_locator = chunk.locator
        assert isinstance(chunk_locator, PageLocator)
        intervals.append(
            (
                max(locator.start_page, chunk_locator.start_page),
                min(locator.end_page, chunk_locator.end_page),
            )
        )
    intervals.sort()
    cursor = locator.start_page
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return None
        cursor = max(cursor, interval_end + 1)
        if cursor > locator.end_page:
            break
    if cursor <= locator.end_page:
        return None
    matching.sort(key=lambda chunk: (chunk.start_offset, chunk.end_offset))
    return "\n".join(str(chunk.content) for chunk in matching if chunk.content)


def _session_input_snapshot(
    session: Any,
    course: CourseIdentity,
    source_ref: SourceRef,
    fingerprint: SourceFingerprint,
    context: DerivativeContext,
) -> tuple[Any, ...]:
    """Capture only the Session state bound to a proposal dependency."""

    return (
        _graph_app_id(session),
        course.relation_page_id,
        course.course_key,
        source_ref.identity,
        fingerprint.source_version,
        fingerprint.source_hash,
        _context_signature(context),
    )


def _material_input_snapshot(
    material_id: str,
    course: CourseIdentity,
    source_ref: SourceRef,
    fingerprint: SourceFingerprint,
    context: DerivativeContext,
    material_type: str,
    source_class: str,
) -> tuple[Any, ...]:
    """Capture only Material identity/authority/source approval state."""

    return (
        material_id,
        course.relation_page_id,
        course.course_key,
        material_type,
        source_class,
        source_ref.identity,
        fingerprint.source_version,
        fingerprint.source_hash,
        _context_signature(context),
    )


def _usage_relation_snapshot(value: Any) -> str | None:
    relation = resolve_course_relation(value)
    return relation if relation is not None else None


def _stored_bound_snapshot(value: Any) -> Any:
    if value is _MISSING:
        return None
    value = unwrap(value)
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return _stable_record_value(value)


def _usage_row_snapshot(row: Any) -> tuple[Any, ...]:
    """Snapshot Usage semantics without provider/display metadata."""

    return (
        _producer_usage_app_id(row),
        _usage_relation_snapshot(raw_field(row, "Session", "session", default=None)),
        _usage_relation_snapshot(raw_field(row, "Material", "material", "Material ID", default=None)),
        strict_text(raw_field(row, "Role", "role", default=None), default=None),
        _stored_bound_snapshot(raw_field(row, "Start Page", "start_page", default=_MISSING)),
        _stored_bound_snapshot(raw_field(row, "End Page", "end_page", default=_MISSING)),
        _stored_bound_snapshot(raw_field(row, "Verified", "verified", default=_MISSING)),
    )


def _capture_owned_usage_snapshot(
    before_rows: Sequence[Any],
    graph_reader: Any,
    session_id: str,
    usage_id: str,
) -> Any:
    """Capture exactly one producer-created row without adopting other edits."""

    after_rows = list(_reader_call(graph_reader, "get_material_usage", session_id) or [])
    if not after_rows:
        raise SourcePartialError("created Material Usage is not visible after creation")
    matches = [row for row in after_rows if _producer_usage_app_id(row) == usage_id]
    if len(matches) != 1:
        raise SourcePartialError("created Material Usage is missing or physically ambiguous")
    before_keys = Counter(repr(_usage_row_snapshot(row)) for row in before_rows)
    after_keys = Counter(repr(_usage_row_snapshot(row)) for row in after_rows)
    created_key = repr(_usage_row_snapshot(matches[0]))
    if after_keys != before_keys + Counter({created_key: 1}):
        raise SourcePartialError("unexpected Material Usage change accompanied creation")
    return _usage_row_snapshot(matches[0])


def _usage_rows_snapshot_equal(rows: Sequence[Any], snapshot: Sequence[Any]) -> bool:
    actual = Counter(repr(_usage_row_snapshot(row)) for row in rows)
    expected = Counter(repr(value) for value in snapshot)
    return actual == expected


def _only_exact_siblings_added(
    rows: Sequence[Any],
    snapshot: Sequence[Any],
    candidate: MaterialUsageCandidate,
    session_id: str,
) -> bool:
    if candidate.operation != "create_usage":
        return False
    # Every original physical row must still exist unchanged. Only newly
    # added exact siblings for this candidate may be reconciled below.
    remaining = Counter(repr(value) for value in snapshot)
    added = []
    for row in rows:
        key = repr(_usage_row_snapshot(row))
        if remaining[key]:
            remaining[key] -= 1
        else:
            added.append(row)
    if any(remaining.values()) or not added:
        return False
    candidate_identity = (
        session_id,
        candidate.material_id,
        candidate.role,
        candidate.page_range,
    )
    identities = [material_usage_identity(row) for row in added]
    return len(identities) == len(added) and all(
        identity == candidate_identity for identity in identities
    )


def _range_has_pages(context: DerivativeContext, page_range: PageRange) -> bool:
    pages = {
        chunk.locator.start_page
        for chunk in context.chunks
        if isinstance(chunk.locator, PageLocator)
    }
    if page_range.is_whole_source:
        return bool(pages)
    assert page_range.start_page is not None and page_range.end_page is not None
    return all(page in pages for page in range(page_range.start_page, page_range.end_page + 1))


def _dependency(ref: SourceRef, fp: SourceFingerprint) -> dict[str, Any]:
    return {
        "source_ref": {"provider": ref.provider, "file_id": ref.file_id},
        "source_hash": fp.source_hash,
        "source_version": fp.source_version,
    }


def _old_snapshot(scope: MaterialUsageScope) -> dict[str, Any]:
    return {
        "usage_id": scope.usage_id,
        "session_id": scope.session_id,
        "material_id": scope.material_id,
        "role": scope.role,
        "start_page": scope.start_page,
        "end_page": scope.end_page,
        "verified": scope.verified,
    }


def _canonical_evidence(value: Any) -> Any:
    if value is None:
        return None
    values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, str):
            result.append({"locator": item, "quote": None})
        elif isinstance(item, Mapping):
            result.append({
                "locator": str(item.get("locator", item.get("location"))),
                "quote": item.get("quote"),
            })
    return result


def _queue_properties(
    *,
    proposal_id: str,
    proposal_type: str,
    semantics: Mapping[str, Any],
    target_id: str,
    course: CourseIdentity,
    material_ref: SourceRef,
    material_fp: SourceFingerprint,
    confidence: Any,
    evidence: Any,
    review_reason: str | None,
    material: Any,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "Name": f"Material Usage · {target_id}",
        "Proposal ID": proposal_id,
        "Proposal Type": proposal_type,
        "State": "PENDING_REVIEW",
        "Decision": "Pending",
        "Course": {"relation": [{"id": course.relation_page_id}]},
        "Target Entity ID": target_id,
        "Source Ref": {"provider": material_ref.provider, "file_id": material_ref.file_id},
        "Source Hash": material_fp.source_hash,
        "Source Version": material_fp.source_version,
        "Proposed Action": canonical_action_json(semantics),
    }
    if confidence is not None:
        properties["Confidence"] = _confidence_select(confidence)
    if evidence is not None:
        properties["Evidence"] = evidence
    if review_reason is not None:
        properties["Review Reason"] = review_reason
    return properties


def _validate_review_reason(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("review_reason must be a non-empty string or null")


def _validate_queue_properties(
    properties: Mapping[str, Any],
    *,
    proposal_id: str,
    proposal_type: str,
    semantics: Mapping[str, Any],
) -> None:
    """Validate the complete Queue creation shape before a new Usage write."""

    allowed = {
        "Name",
        "Proposal ID",
        "Proposal Type",
        "State",
        "Decision",
        "Course",
        "Target Entity ID",
        "Source Ref",
        "Source Hash",
        "Source Version",
        "Proposed Action",
        "Confidence",
        "Evidence",
        "Review Reason",
    }
    unknown = set(properties).difference(allowed)
    if unknown:
        raise PolicyViolation(
            "Material Usage Queue shape contains unknown properties: "
            + ", ".join(sorted(unknown))
        )
    required = {
        "Name",
        "Proposal ID",
        "Proposal Type",
        "State",
        "Decision",
        "Course",
        "Target Entity ID",
        "Source Ref",
        "Source Hash",
        "Source Version",
        "Proposed Action",
    }
    missing = required.difference(properties)
    if missing:
        raise PolicyViolation(
            "Material Usage Queue shape is missing properties: "
            + ", ".join(sorted(missing))
        )
    if properties["Proposal ID"] != proposal_id:
        raise PolicyViolation("Material Usage Queue Proposal ID is inconsistent")
    if properties["Proposal Type"] != proposal_type:
        raise PolicyViolation("Material Usage Queue Proposal Type is inconsistent")
    if properties["State"] != "PENDING_REVIEW":
        raise PolicyViolation("Material Usage Queue State must start as PENDING_REVIEW")
    if properties["Decision"] != "Pending":
        raise PolicyViolation("Material Usage Queue Decision must start as Pending")
    if not isinstance(properties["Name"], str) or not properties["Name"].strip():
        raise PolicyViolation("Material Usage Queue Name must be non-empty")
    if not isinstance(properties["Source Hash"], str) or not properties["Source Hash"].strip():
        raise PolicyViolation("Material Usage Queue Source Hash must be non-empty")
    if type(properties["Source Version"]) is not int or properties["Source Version"] < 1:
        raise PolicyViolation("Material Usage Queue Source Version must be positive")
    if "Confidence" in properties and (
        not isinstance(properties["Confidence"], str)
        or not properties["Confidence"].strip()
    ):
        raise PolicyViolation("Material Usage Queue Confidence must be a non-empty Select")
    if "Review Reason" in properties:
        _validate_review_reason(properties["Review Reason"])
    try:
        canonical = canonical_semantics_from_queue(properties)
    except (TypeError, ValueError) as exc:
        raise PolicyViolation("Material Usage Queue canonical shape is invalid") from exc
    if canonical != semantics:
        raise PolicyViolation("Material Usage Queue canonical semantics changed during validation")
    if derive_proposal_id(proposal_type, canonical) != proposal_id:
        raise PolicyViolation("Material Usage Queue Proposal ID fails semantic validation")


def _confidence_select(value: Any) -> str:
    """Serialize model confidence to the frozen Queue Select field."""

    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("confidence must be a Select option or numeric score")
    if not math.isfinite(value) or value < 0 or value > 1:
        raise ValueError("confidence score must be between 0 and 1")
    if value >= 0.8:
        return "High"
    if value >= 0.5:
        return "Medium"
    return "Low"


def _context_signature(context: DerivativeContext) -> tuple[Any, ...]:
    # The derivative body/chunk evidence and fingerprint are approval-bound.
    # Front-matter display/navigation details are not; in particular a
    # navigational web_url must not turn an unchanged provider/file identity
    # into a freshness failure.
    return (
        context.entity_id,
        context.body,
        tuple(
            (
                str(chunk.locator),
                chunk.content,
                chunk.start_offset,
                chunk.end_offset,
                chunk.symbolic_hint,
            )
            for chunk in context.chunks
        ),
        context.fingerprint,
        context.kind,
    )


def _stable_record_value(value: Any) -> Any:
    """Snapshot provider-neutral records without relying on object identity."""

    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (
                    repr(key),
                    _stable_record_value(item),
                )
                for key, item in value.items()
            )
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_stable_record_value(item) for item in value)
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _stable_record_value(value.as_dict())
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _stable_record_value(vars(value))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _eligible_usage_scopes(
    rows: Sequence[Any], session_id: str,
) -> tuple[list[MaterialUsageScope], tuple[str, ...]]:
    # Count physical rows before schema filtering: a malformed row can still
    # make a selected logical ID ambiguous. Keep the caller's raw rows intact
    # for exact tuple cardinality and persistence freshness checks.
    counts: dict[str, int] = {}
    for row in rows:
        identifier = _producer_usage_app_id(row)
        if identifier is not None:
            counts[identifier] = counts.get(identifier, 0) + 1
    scopes = [
        scope for scope in material_usage_scopes(rows, session_id=session_id)
        if counts.get(_producer_usage_app_id(scope.usage) or "", 0) == 1
    ]
    excluded = len(rows) - len(scopes)
    warnings = (
        (f"excluded {excluded} invalid or ambiguous Material Usage row(s)",)
        if excluded else ()
    )
    return scopes, warnings


def _proposer_usage_payload(row: Any) -> dict[str, Any]:
    verified = raw_field(row, "Verified", "verified", default=None)
    start_page = raw_field(row, "Start Page", "start_page", default=None)
    end_page = raw_field(row, "End Page", "end_page", default=None)
    return {
        "ID": _bounded_text(
            _producer_usage_app_id(row),
            limit=_PROPOSER_REFERENCE_LIMIT,
        ),
        "Session": _bounded_reference(
            raw_field(row, "Session", "session", default=None)
        ),
        "Material": _bounded_reference(
            raw_field(row, "Material", "Material ID", "material", default=None)
        ),
        "Role": _bounded_text(
            strict_text(raw_field(row, "Role", "role", default=None), default=None)
        ),
        "Start Page": start_page if isinstance(start_page, int) and not isinstance(start_page, bool) else None,
        "End Page": end_page if isinstance(end_page, int) and not isinstance(end_page, bool) else None,
        "Verified": verified if isinstance(verified, bool) else None,
    }


def _explicit_usage_id_raw(usage: Any) -> Any:
    """Read only the frozen app-ID property, never a provider record ID."""

    if isinstance(usage, Mapping):
        nested = usage.get("properties")
        if isinstance(nested, Mapping):
            return nested.get("ID", _MISSING)
        return usage.get("ID", _MISSING)
    nested = getattr(usage, "properties", None)
    if isinstance(nested, Mapping):
        return nested.get("ID", _MISSING)
    return getattr(usage, "ID", _MISSING)


def _producer_usage_app_id(usage: Any) -> str | None:
    """Use the shared extractor, with an exact frozen-property guard.

    ``usage_app_id`` is the retrieval-owned helper and is intentionally used
    here for producer discovery/payloads.  The exact-property check keeps the
    producer contract explicit across flat and provider-shaped records and
    rejects whitespace-padded logical IDs.
    """

    shared_id = usage_app_id(usage)
    raw_id = _explicit_usage_id_raw(usage)
    value = unwrap(raw_id)
    if (
        shared_id is None
        or raw_id is _MISSING
        or not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or shared_id != value
    ):
        return None
    return value


def _bounded_reference(value: Any) -> Any:
    """Keep relation hints useful while preventing nested raw-record leakage."""

    if isinstance(value, Mapping):
        for key in ("relation", "relations", "results"):
            if key in value:
                return _bounded_reference(value[key])
        for key in ("id", "ID", "page_id", "pageId"):
            if key in value:
                return _bounded_text(value[key], limit=_PROPOSER_REFERENCE_LIMIT)
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = tuple(_bounded_reference(item) for item in value[:4])
        values = tuple(item for item in values if item is not None)
        if len(values) == 1:
            return values[0]
        return values
    if isinstance(value, str):
        return value[:_PROPOSER_REFERENCE_LIMIT]
    if isinstance(value, (bool, int, float)):
        return value
    return None


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


__all__ = [
    "MaterialUsageCandidate",
    "MaterialUsageProducer",
    "MaterialUsageProducerResult",
    "MaterialUsageProposalProducer",
]
