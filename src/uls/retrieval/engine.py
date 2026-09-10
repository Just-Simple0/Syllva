"""Model-independent RetrievalEngine for the Phase 2 Session slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from uls.adapters.drive.base import DriveReader
from uls.adapters.drive.binding import ActivityInstructionBinding, SourceBindingResolver
from uls.adapters.notion.base import NotionReader
from uls.domain.academic import ActivityConstraintMetadata, ActivityRecord, ExamRecord
from uls.domain.course_identity import (
    CourseIdentity,
    strict_single_relation_page_id,
    validate_course_record,
)
from uls.domain.enums import DerivativeStatus, FreshnessStatus, RetrievalIntent
from uls.domain.errors import (
    ContextExpiredError,
    EntityNotFoundError,
    LocatorNotAllowedError,
    LocatorParseError,
    LocatorStaleError,
    PolicyDeniedError,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.ids import strict_entity_id
from uls.domain.models import EvidenceItem, PageLocator, TimeLocator, parse_locator
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.schemas import coerce_enrichment

from ._compat import (
    coerce_fingerprint,
    field,
    raw_field,
    record_label,
    strict_text,
)
from .authority import SUPPORTED_MATERIAL_SOURCE_CLASSES, material_source_class
from .capabilities import CapabilityManager
from .chunking import (
    derivative_parts,
    find_chunk_containing,
    page_chunks,
    timestamp_chunks,
)
from .context import (
    assemble_context_package,
    budget_from_config,
    make_evidence_item,
)
from .freshness import revalidate_locator
from .provenance import make_provenance
from .resolver import SessionResolver
from .schemas import CapabilityBinding, ContextPackage, ResolutionResult
from .scope import (
    material_usage_identity_counts,
    material_usage_scope_result,
    usage_app_id,
    user_reference,
)


@dataclass
class _SessionEvidenceCollection:
    session: Any
    course: CourseIdentity
    evidence: list[EvidenceItem]
    bindings: list[CapabilityBinding]
    warnings: list[Any]
    user_context: tuple[Mapping[str, Any], ...]
    professor_signals: tuple[Mapping[str, Any], ...]
    transcript_loaded: bool


@dataclass
class _MaterialEvidenceCollection:
    material: Any
    course: CourseIdentity
    evidence: list[EvidenceItem]
    bindings: list[CapabilityBinding]
    warnings: list[Any]


class RetrievalEngine:
    """Read-only retrieval policy and execution boundary.

    The constructor accepts only ``NotionReader``/``DriveReader`` for source
    access.  No write-capable adapter is retained or passed to any retrieval
    helper.
    """

    def __init__(
        self,
        notion_reader: NotionReader,
        drive_reader: DriveReader,
        state_store: Any,
        ephemeral: Any,
        config: Any,
        *,
        source_binding_resolver: SourceBindingResolver | None = None,
    ) -> None:
        self.notion_reader = notion_reader
        self.drive_reader = drive_reader
        self.state_store = state_store
        self.ephemeral = ephemeral
        self.config = config
        self.source_binding_resolver: SourceBindingResolver | None
        # Source binding is an authority dependency, not a Drive convenience
        # method.  Never manufacture one from the reader: callers must inject
        # the independently validated resolver explicitly.
        self.source_binding_resolver = source_binding_resolver
        self.budget = budget_from_config(config)
        self.resolver = SessionResolver(
            notion_reader,
            ephemeral,
            resolution_ttl_seconds=_config_int(config, "resolution_ttl_seconds", 900),
            max_candidate_entities=_config_int(config, "max_candidate_entities", 20),
        )
        self.capabilities = CapabilityManager(
            ephemeral,
            ttl_seconds=_config_int(config, "context_ttl_seconds", 900),
            max_followup_chunks=self.budget.max_followup_chunks,
        )

    def resolve_entity(
        self,
        query: str,
        course_hint: Any | None = None,
        entity_type: str = "session",
    ) -> ResolutionResult:
        return self.resolver.resolve_entity(query, course_hint, entity_type)

    def select_resolution(self, resolution_id: str, candidate_id: str) -> Any:
        return self.resolver.select_resolution(resolution_id, candidate_id)

    def get_context(self, session_id: str, **kwargs: Any) -> ContextPackage:
        """Compatibility entry point for the small domain RetrievalEngine protocol."""

        return self.get_session_context(session_id, **kwargs)

    def _collect_material_evidence(
        self,
        material_id: str,
        *,
        query: str | None = None,
    ) -> _MaterialEvidenceCollection:
        """Collect one material's raw candidates without budgeting or issuing."""

        material = self._lookup_material(material_id)
        if material is None:
            raise EntityNotFoundError(
                f"Material not found: {material_id}",
                details={"material_id": material_id},
            )
        course = self._course_for_record(material, material_id)
        source_class = self._material_source_class(material)
        material_type = self._material_type(material)
        source_ref = self._resolve_source_ref(
            material,
            "Normalized Source",
            "normalized_source",
            "Normalized Source Ref",
            "normalized_source_ref",
            "Source Ref",
            "source_ref",
            entity_id=material_id,
        )
        derivative_result = self._read_current_derivative(
            material_id,
            source_ref,
            material,
            expected_schema="uls.material.v1",
            expected_course_key=course.course_key,
        )
        if derivative_result is None:
            raise SourceUnavailableError("material derivative is unavailable")
        derivative, fingerprint, front = derivative_result
        warnings: list[Any] = []
        if str(front["status"]).casefold() == DerivativeStatus.PARTIAL.value:
            warnings.append(_warning("SOURCE_PARTIAL", "material derivative is partial"))
        chunks = page_chunks(derivative, entity_id=material_id)
        if not chunks:
            raise SourceUnavailableError(
                "material derivative has no validated page markers",
                details={"material_id": material_id},
            )
        if query:
            from .chunking import select_chunks

            chunks = select_chunks(chunks, query)
        evidence: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        for chunk in chunks:
            item = self._evidence_from_chunk(
                chunk,
                source_class=source_class,
                fingerprint=fingerprint,
                front_matter=front,
                source_ref=source_ref,
            )
            evidence.append(item)
            bindings.append(
                CapabilityBinding(
                    entity_id=material_id,
                    locator=item.locator,
                    source_hash=fingerprint.source_hash,
                    source_version=fingerprint.source_version,
                    source_class=source_class,
                    source_ref=source_ref,
                    material_id=material_id,
                    material_type=material_type,
                    course_relation_page_id=course.relation_page_id,
                    course_key=course.course_key,
                )
            )
        return _MaterialEvidenceCollection(material, course, evidence, bindings, warnings)

    def get_material_context(
        self,
        material_id: str,
        *,
        query: str | None = None,
        include_user_annotations: bool = False,
        caller_scope: str | None = None,
    ) -> ContextPackage:
        """Return a bounded material context for M0/read-only callers.

        Phase 2's main path is Session retrieval, but keeping this small
        domain operation real makes the engine usable by the frozen M0
        contract without exposing arbitrary Drive reads.
        """

        collection = self._collect_material_evidence(material_id, query=query)
        material = collection.material
        warnings = collection.warnings
        sources, bindings = self._retain_current_evidence(
            collection.evidence,
            collection.bindings,
            warnings,
        )
        user_context: tuple[Mapping[str, Any], ...] = ()
        if include_user_annotations:
            # Material annotations are outside the Phase 2 NotionReader
            # protocol.  Keep this optional compatibility read, but never
            # leak a provider/network exception as an unstructured error.
            for name in ("get_material_user_annotations", "list_material_annotations"):
                method = getattr(self.notion_reader, name, None)
                if method is None:
                    continue
                try:
                    user_context = tuple(user_reference(item) for item in (method(material_id) or []))
                except _MISSING_PROVIDER_ERRORS:
                    user_context = ()
                except Exception as exc:
                    raise SourceUnavailableError(
                        "Notion material annotation lookup is unavailable"
                    ) from exc
                break
        final_sources, final_bindings = self._budget_evidence_bindings(sources, bindings)
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        return assemble_context_package(
            entity={"type": "material", "id": material_id, "title": record_label(material) or material_id},
            scope={"intent": "MATERIAL", "hard_boundary": False},
            sources=final_sources,
            user_context=user_context,
            warnings=warnings,
            context_id=capability.context_id,
            already_bounded=True,
        )

    def _collect_session_evidence(
        self,
        session_id: str,
        *,
        query: str | None = None,
        include_provisional: bool = False,
    ) -> _SessionEvidenceCollection:
        """Collect one Session's raw candidates without budgeting or issuing."""

        if type(include_provisional) is not bool:
            raise PolicyDeniedError("include_provisional must be a boolean")
        config_allows_provisional = self._config_bool(
            "allow_provisional_material_usage", True
        )
        effective_include_provisional = include_provisional and config_allows_provisional
        session = self._get_session(session_id)
        if session is None:
            raise EntityNotFoundError(
                f"Session not found: {session_id}",
                details={"session_id": session_id},
            )
        canonical_id = session_id
        session_course = self._course_for_record(session, canonical_id)
        warnings: list[Any] = []
        evidence: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        transcript_derivative: Any | None = None
        transcript_fingerprint: SourceFingerprint | None = None
        transcript_loaded = False

        transcript_source = self._resolve_source_ref(
            session,
            "Normalized Transcript",
            "normalized_transcript",
            "Transcript Source Ref",
            "transcript_source_ref",
            "Transcript Source",
            "Source Ref",
            entity_id=canonical_id,
        )
        try:
            transcript_result = self._read_current_derivative(
                canonical_id,
                transcript_source,
                session,
                expected_schema="uls.transcript.v1",
                expected_course_key=session_course.course_key,
            )
            if transcript_result is not None:
                transcript_derivative, transcript_fingerprint, transcript_front = transcript_result
                transcript_loaded = True
                derivative_status = str(transcript_front["status"]).casefold()
                if derivative_status == DerivativeStatus.PARTIAL.value:
                    warnings.append(_warning("SOURCE_PARTIAL", "transcript derivative is partial"))
                chunks = timestamp_chunks(transcript_derivative, entity_id=canonical_id)
                if query:
                    from .chunking import select_chunks
                    from .resolver import extract_session_number, strip_intent_keywords

                    context_query = strip_intent_keywords(query)
                    if extract_session_number(context_query) is None:
                        chunks = select_chunks(chunks, context_query)
                for chunk in chunks:
                    item = self._evidence_from_chunk(
                        chunk,
                        source_class="professor_transcript",
                        fingerprint=transcript_fingerprint,
                        front_matter=transcript_front,
                        source_ref=transcript_source,
                    )
                    evidence.append(item)
                    bindings.append(
                        CapabilityBinding(
                            entity_id=canonical_id,
                            locator=item.locator,
                            source_hash=item.fingerprint.source_hash,
                            source_version=item.fingerprint.source_version,
                            source_class=item.source_class,
                            source_ref=transcript_source,
                            session_id=canonical_id,
                            relation_required=False,
                            course_relation_page_id=session_course.relation_page_id,
                            course_key=session_course.course_key,
                        )
                    )
        except SourcePartialError as exc:
            warnings.append(_warning("SOURCE_PARTIAL", str(exc)))
        except SourceUnavailableError as exc:
            warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))

        usages = self._material_usages(canonical_id)
        usage_scopes = self._safe_usage_scopes(
            usages,
            session_id=canonical_id,
            course=session_course,
            warnings=warnings,
        )
        eligible_usage_scopes = [
            scope
            for scope in usage_scopes
            if scope.verified or effective_include_provisional
        ]
        for usage_scope in eligible_usage_scopes:
            material = self._material_for_usage(usage_scope.usage, usage_scope.material_id)
            try:
                material_course = self._course_for_record(material, usage_scope.material_id)
                if material_course != session_course:
                    raise SourceUnavailableError(
                        "material and session do not share the exact Course identity"
                    )
                material_type = self._material_type(material)
                material_class = self._material_source_class(material)
                # Session retrieval deliberately retains its established
                # professor-material source set.  Supplemental materials are
                # valid for direct Material retrieval but are not silently
                # promoted into Session context.
                if material_class != "professor_material":
                    warnings.append(
                        _warning(
                            "SOURCE_UNAVAILABLE",
                            f"material {usage_scope.material_id} is not an allowed Session source class",
                        )
                    )
                    continue
            except SourceUnavailableError as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))
                continue
            try:
                material_source = self._resolve_source_ref(
                    material,
                    "Normalized Source",
                    "normalized_source",
                    "Normalized Source Ref",
                    "normalized_source_ref",
                    "Source Ref",
                    "source_ref",
                    entity_id=usage_scope.material_id,
                )
                material_result = self._read_current_derivative(
                    usage_scope.material_id,
                    material_source,
                    material,
                    expected_schema="uls.material.v1",
                    expected_course_key=session_course.course_key,
                )
                if material_result is None:
                    continue
                derivative, fingerprint, front = material_result
                if str(front["status"]).casefold() == DerivativeStatus.PARTIAL.value:
                    warnings.append(
                        _warning(
                            "SOURCE_PARTIAL",
                            f"material derivative {usage_scope.material_id} is partial",
                        )
                    )
                chunks = page_chunks(
                    derivative,
                    entity_id=usage_scope.material_id,
                    start_page=usage_scope.start_page,
                    end_page=usage_scope.end_page,
                )
                if not chunks:
                    warnings.append(
                        _warning(
                            "SOURCE_PARTIAL",
                            f"Material Usage {usage_scope.usage_id} has no justified page evidence in the current derivative",
                        )
                    )
                    continue
                if query:
                    from .chunking import select_chunks

                    chunks = select_chunks(chunks, query)
                for chunk in chunks:
                    item = self._evidence_from_chunk(
                        chunk,
                        source_class=material_class,
                        fingerprint=fingerprint,
                        front_matter=front,
                        source_ref=material_source,
                        provisional=usage_scope.provisional,
                    )
                    evidence.append(item)
                    bindings.append(
                        CapabilityBinding(
                            entity_id=usage_scope.material_id,
                            locator=item.locator,
                            source_hash=item.fingerprint.source_hash,
                            source_version=item.fingerprint.source_version,
                            source_class=item.source_class,
                            source_ref=material_source,
                            session_id=canonical_id,
                            material_id=usage_scope.material_id,
                            relation_required=True,
                            provisional=usage_scope.provisional,
                            usage_id=usage_scope.usage_id,
                            usage_role=usage_scope.role,
                            material_type=material_type,
                            course_relation_page_id=session_course.relation_page_id,
                            course_key=session_course.course_key,
                            usage_range=usage_scope.range,
                        )
                    )
            except SourcePartialError as exc:
                warnings.append(_warning("SOURCE_PARTIAL", str(exc)))
            except SourceUnavailableError as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))

        user_context = self._user_references(canonical_id)
        professor_signals = self._session_signals(
            canonical_id,
            transcript_derivative,
            transcript_fingerprint,
            warnings,
        )
        return _SessionEvidenceCollection(
            session,
            session_course,
            evidence,
            bindings,
            warnings,
            user_context,
            professor_signals,
            transcript_loaded,
        )

    def get_exam_context(
        self,
        exam_id: str,
        *,
        query: str | None = None,
        caller_scope: str | None = None,
    ) -> ContextPackage:
        """Return verified evidence bounded by the current Exam scope."""

        exam = self._get_exam_record(exam_id)
        if exam is None:
            raise EntityNotFoundError(f"Exam not found: {exam_id}", details={"exam_id": exam_id})
        warnings: list[Any] = []
        included_ids = exam.included_session_ids
        if included_ids is None or not included_ids:
            warnings.append(_warning("EXAM_SCOPE_EMPTY", "Exam Included Sessions is absent or empty"))
        if not exam.scope_confirmed:
            warnings.append(_warning("EXAM_SCOPE_UNCONFIRMED", "Exam scope has not been human-confirmed"))

        evidence: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        for session_id in included_ids or ():
            try:
                collection = self._collect_session_evidence(
                    session_id,
                    query=query,
                    include_provisional=False,
                )
                if collection.course != exam.course:
                    raise SourceUnavailableError(
                        "Exam Included Session does not share the exact Course identity"
                    )
                if not collection.transcript_loaded and not collection.evidence:
                    raise SourceUnavailableError("no usable transcript derivative is available")
            except (EntityNotFoundError, SourceUnavailableError, SourcePartialError) as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", f"Session {session_id}: {exc}"))
                continue
            warnings.extend(collection.warnings)
            for item, binding in zip(collection.evidence, collection.bindings, strict=False):
                evidence.append(item)
                bindings.append(
                    replace(
                        binding,
                        parent_entity_id=exam.entity_id,
                        parent_entity_type="exam",
                        parent_course_relation_page_id=exam.course.relation_page_id,
                        parent_course_key=exam.course.course_key,
                        parent_scope_confirmed=exam.scope_confirmed,
                        parent_included_session_ids=tuple(included_ids or ()),
                        parent_path_leaf="material_usage" if binding.material_id else "session",
                    )
                )

        evidence, bindings = self._retain_current_evidence(evidence, bindings, warnings)
        final_evidence, final_bindings = self._budget_evidence_bindings(evidence, bindings)
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        scope = {
            "intent": RetrievalIntent.EXAM.value,
            "status": "confirmed" if exam.scope_confirmed else "provisional",
            "hard_boundary": exam.scope_confirmed,
            "included_sessions": None if included_ids is None else list(included_ids),
            "parent_snapshot": {
                "entity_id": exam.entity_id,
                "course": exam.course.as_dict(),
                "scope_confirmed": exam.scope_confirmed,
                "included_session_ids": None if included_ids is None else list(included_ids),
            },
        }
        return assemble_context_package(
            entity={"type": "exam", "id": exam.entity_id, "title": exam.title or exam.entity_id},
            scope=scope,
            sources=final_evidence,
            warnings=warnings,
            context_id=capability.context_id,
            already_bounded=True,
        )

    def get_activity_context(
        self,
        activity_id: str,
        *,
        query: str | None = None,
        caller_scope: str | None = None,
    ) -> ContextPackage:
        """Return official Activity instructions and related verified evidence."""

        activity = self._get_activity_record(activity_id)
        if activity is None:
            raise EntityNotFoundError(
                f"Activity not found: {activity_id}", details={"activity_id": activity_id}
            )
        warnings: list[Any] = []
        evidence: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        expected_locators: tuple[str, ...] = ()
        official_status = "unavailable"
        if activity.instructions_source_url is None or activity.normalized_instructions_url is None:
            warnings.append(_warning("INSTRUCTIONS_INCOMPLETE", "official instruction pointer pair is incomplete"))
        else:
            try:
                instruction_binding = self._resolve_activity_instructions(activity)
                derivative, fingerprint, front = self._read_activity_derivative(
                    activity, instruction_binding
                )
                all_chunks = page_chunks(derivative, entity_id=activity.entity_id)
                expected_locators = tuple(str(chunk.locator) for chunk in all_chunks)
                chunks = all_chunks
                if query:
                    from .chunking import select_chunks

                    chunks = select_chunks(chunks, query)
                if str(front.get("status", "")).casefold() == DerivativeStatus.PARTIAL.value:
                    warnings.append(_warning("SOURCE_PARTIAL", "official Activity derivative is partial"))
                constraint = ActivityConstraintMetadata(
                    official_locator_set=expected_locators,
                    official_evidence_set=expected_locators,
                )
                for chunk in chunks:
                    item = replace(
                        self._evidence_from_chunk(
                            chunk,
                            source_class="official_activity",
                            fingerprint=fingerprint,
                            front_matter=front,
                            source_ref=instruction_binding.source_ref,
                        ),
                        constraint_metadata=constraint,
                    )
                    evidence.append(item)
                    bindings.append(
                        CapabilityBinding(
                            entity_id=activity.entity_id,
                            locator=item.locator,
                            source_hash=fingerprint.source_hash,
                            source_version=fingerprint.source_version,
                            source_class="official_activity",
                            source_ref=instruction_binding.source_ref,
                            parent_entity_id=activity.entity_id,
                            parent_entity_type="activity",
                            parent_course_relation_page_id=activity.course.relation_page_id,
                            parent_course_key=activity.course.course_key,
                            parent_related_session_ids=activity.related_session_ids,
                            parent_related_material_ids=activity.related_material_ids,
                            parent_path_leaf="activity_instructions",
                            activity_instructions_source_url=activity.instructions_source_url,
                            activity_normalized_instructions_url=activity.normalized_instructions_url,
                            activity_binding_identity=instruction_binding.source_ref.identity,
                        )
                    )
                official_status = "ready" if str(front.get("status", "")).casefold() == DerivativeStatus.READY.value else "partial"
            except (SourceUnavailableError, SourcePartialError) as exc:
                warnings.append(_warning("INSTRUCTIONS_INCOMPLETE", str(exc)))
            except Exception as exc:  # noqa: BLE001 - malformed provider data is incomplete context
                warnings.append(_warning("INSTRUCTIONS_INCOMPLETE", f"official instruction read failed: {exc}"))

        parent_sessions = activity.related_session_ids
        parent_materials = activity.related_material_ids
        for session_id in parent_sessions or ():
            try:
                session_collection = self._collect_session_evidence(
                    session_id,
                    query=query,
                    include_provisional=False,
                )
                if session_collection.course != activity.course:
                    raise SourceUnavailableError(
                        "related Session does not share the exact Activity Course identity"
                    )
                if not session_collection.transcript_loaded and not session_collection.evidence:
                    raise SourceUnavailableError("no usable transcript derivative is available")
            except (EntityNotFoundError, SourceUnavailableError, SourcePartialError) as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", f"related Session {session_id}: {exc}"))
                continue
            warnings.extend(session_collection.warnings)
            for item, binding in zip(session_collection.evidence, session_collection.bindings, strict=False):
                evidence.append(item)
                bindings.append(
                    replace(
                        binding,
                        parent_entity_id=activity.entity_id,
                        parent_entity_type="activity",
                        parent_course_relation_page_id=activity.course.relation_page_id,
                        parent_course_key=activity.course.course_key,
                        parent_related_session_ids=parent_sessions,
                        parent_related_material_ids=parent_materials,
                        parent_path_leaf="material_usage" if binding.material_id else "session",
                        activity_instructions_source_url=activity.instructions_source_url,
                        activity_normalized_instructions_url=activity.normalized_instructions_url,
                    )
                )
        for material_id in parent_materials or ():
            try:
                material_collection = self._collect_material_evidence(material_id, query=query)
                if material_collection.course != activity.course:
                    raise SourceUnavailableError(
                        "related Material does not share the exact Activity Course identity"
                    )
            except (EntityNotFoundError, SourceUnavailableError, SourcePartialError) as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", f"related Material {material_id}: {exc}"))
                continue
            warnings.extend(material_collection.warnings)
            for item, binding in zip(material_collection.evidence, material_collection.bindings, strict=False):
                evidence.append(item)
                bindings.append(
                    replace(
                        binding,
                        parent_entity_id=activity.entity_id,
                        parent_entity_type="activity",
                        parent_course_relation_page_id=activity.course.relation_page_id,
                        parent_course_key=activity.course.course_key,
                        parent_related_session_ids=parent_sessions,
                        parent_related_material_ids=parent_materials,
                        parent_path_leaf="material",
                        activity_instructions_source_url=activity.instructions_source_url,
                        activity_normalized_instructions_url=activity.normalized_instructions_url,
                    )
                )

        evidence, bindings = self._retain_current_evidence(evidence, bindings, warnings)
        prebudget_evidence = tuple(evidence)
        final_evidence, final_bindings = self._budget_evidence_bindings(evidence, bindings)
        returned_locators = tuple(
            str(item.locator) for item in final_evidence if item.source_class == "official_activity"
        )
        missing_locators = tuple(
            locator for locator in expected_locators if locator not in returned_locators
        )
        budget_truncated = len(final_evidence) < len(prebudget_evidence) or any(
            len(item.content) < len(original.content)
            for item, original in zip(final_evidence, prebudget_evidence, strict=False)
        )
        complete = (
            official_status == "ready"
            and query is None
            and not missing_locators
            and not budget_truncated
            and bool(expected_locators)
        )
        if expected_locators and not complete:
            warnings.append(_warning("INSTRUCTIONS_PARTIAL", "official instruction coverage is incomplete"))
        elif not expected_locators:
            warnings.append(_warning("INSTRUCTIONS_INCOMPLETE", "official instructions have no justified page coverage"))
        coverage = {
            "expected_locators": list(expected_locators),
            "returned_locators": list(returned_locators),
            "missing_locators": list(missing_locators),
            "truncated": budget_truncated,
            "complete": complete,
        }
        constraint = ActivityConstraintMetadata(
            official_locator_set=expected_locators,
            official_evidence_set=returned_locators,
        )
        final_evidence = tuple(
            replace(item, constraint_metadata=constraint)
            if item.source_class == "official_activity" else item
            for item in final_evidence
        )
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        return assemble_context_package(
            entity={
                "type": "activity",
                "id": activity.entity_id,
                "title": activity.title or activity.entity_id,
                "result": activity.result,
            },
            scope={
                "intent": RetrievalIntent.ACTIVITY.value,
                "hard_boundary": True,
                "official_constraint": constraint,
                "instruction_coverage": coverage,
                "constraint_conflict_rule": "official_activity instructions govern conflicting recommendations",
            },
            sources=final_evidence,
            warnings=warnings,
            context_id=capability.context_id,
            already_bounded=True,
        )

    def _get_exam_record(self, exam_id: str) -> ExamRecord | None:
        expected = strict_entity_id(exam_id, "E")
        if expected is None:
            raise SourceUnavailableError("requested Exam logical ID is malformed")
        method = getattr(self.notion_reader, "get_exam", None)
        if not callable(method):
            raise SourceUnavailableError("NotionReader has no get_exam method")
        try:
            raw = method(expected)
        except _MISSING_PROVIDER_ERRORS:
            return None
        except Exception as exc:
            raise SourceUnavailableError("Notion Exam lookup is unavailable") from exc
        if raw is None:
            return None
        if isinstance(raw, ExamRecord):
            if raw.entity_id != expected:
                raise SourceUnavailableError(
                    "typed Exam logical ID does not match the requested ID",
                    details={"exam_id": expected},
                )
            self._validate_typed_course(raw.course, expected, "Exam")
            return raw
        course = self._course_for_record(raw, expected)
        try:
            record = ExamRecord.from_mapping(raw, course)
        except (TypeError, ValueError) as exc:
            raise SourceUnavailableError("Exam record is malformed") from exc
        if record.entity_id != expected:
            raise SourceUnavailableError(
                "Exam logical ID does not match the requested ID",
                details={"exam_id": expected},
            )
        return record

    def _get_activity_record(self, activity_id: str) -> ActivityRecord | None:
        expected = strict_entity_id(activity_id, "A")
        if expected is None:
            raise SourceUnavailableError("requested Activity logical ID is malformed")
        method = getattr(self.notion_reader, "get_activity", None)
        if not callable(method):
            raise SourceUnavailableError("NotionReader has no get_activity method")
        try:
            raw = method(expected)
        except _MISSING_PROVIDER_ERRORS:
            return None
        except Exception as exc:
            raise SourceUnavailableError("Notion Activity lookup is unavailable") from exc
        if raw is None:
            return None
        if isinstance(raw, ActivityRecord):
            if raw.entity_id != expected:
                raise SourceUnavailableError(
                    "typed Activity logical ID does not match the requested ID",
                    details={"activity_id": expected},
                )
            self._validate_typed_course(raw.course, expected, "Activity")
            return raw
        course = self._course_for_record(raw, expected)
        try:
            record = ActivityRecord.from_mapping(raw, course)
        except (TypeError, ValueError) as exc:
            raise SourceUnavailableError("Activity record is malformed") from exc
        if record.entity_id != expected:
            raise SourceUnavailableError(
                "Activity logical ID does not match the requested ID",
                details={"activity_id": expected},
            )
        return record

    def _validate_typed_course(
        self,
        course: CourseIdentity,
        entity_id: str,
        record_type: str,
    ) -> None:
        current = self._course_by_relation_id(course.relation_page_id, entity_id)
        if current != course:
            raise SourceUnavailableError(
                f"typed {record_type} Course identity is stale",
                details={"entity_id": entity_id},
            )

    def _resolve_activity_instructions(self, activity: ActivityRecord) -> ActivityInstructionBinding:
        resolver = self.source_binding_resolver
        if resolver is None or not callable(getattr(resolver, "resolve_activity_instructions", None)):
            raise SourceUnavailableError("trusted Activity instruction binding is unavailable")
        try:
            result = resolver.resolve_activity_instructions(
                activity.entity_id,
                activity.instructions_source_url or "",
                activity.normalized_instructions_url or "",
            )
        except SourceUnavailableError:
            raise
        except Exception as exc:
            raise SourceUnavailableError("trusted Activity instruction binding is unavailable") from exc
        if not isinstance(result, ActivityInstructionBinding):
            raise SourceUnavailableError("trusted Activity instruction binding is malformed")
        return result

    def _read_activity_derivative(
        self,
        activity: ActivityRecord,
        binding: ActivityInstructionBinding,
    ) -> tuple[Any, SourceFingerprint, dict[str, Any]]:
        derivative = self._read_derived(binding.derivative_ref, activity.entity_id)
        try:
            derived_entity, _body, _, front = derivative_parts(derivative)
        except Exception as exc:
            raise SourceUnavailableError("Activity derivative could not be parsed") from exc
        if derived_entity != activity.entity_id:
            raise SourceUnavailableError("Activity derivative entity does not match")
        derivative_fp = _front_fingerprint(front)
        if derivative_fp is None:
            raise SourceUnavailableError("Activity derivative has no valid fingerprint")
        current = self._current_fingerprint(activity.entity_id, binding.source_ref)
        if current is None:
            current = self._current_fingerprint(activity.entity_id, binding.derivative_ref)
        if current is None or current != derivative_fp:
            raise SourcePartialError("Activity derivative is behind the current source")
        _validate_read_front_matter(
            front,
            expected_schema="uls.activity.v1",
            entity_id=activity.entity_id,
            source_ref=binding.source_ref,
            expected_course_key=activity.course.course_key,
        )
        return derivative, current, dict(front)

    def get_session_context(
        self,
        session_id: str,
        *,
        query: str | None = None,
        include_provisional: bool = False,
        caller_scope: str | None = None,
    ) -> ContextPackage:
        """Return bounded context for an already-resolved Session ID."""

        collection = self._collect_session_evidence(
            session_id,
            query=query,
            include_provisional=include_provisional,
        )
        warnings = collection.warnings
        evidence, bindings = self._retain_current_evidence(
            collection.evidence,
            collection.bindings,
            warnings,
        )
        final_evidence, final_bindings = self._budget_evidence_bindings(evidence, bindings)
        provisional_usage_ids: set[str] = set()
        for binding in final_bindings:
            if not binding.provisional:
                continue
            usage_id = binding.usage_id or f"{binding.entity_id}:{binding.locator}"
            if usage_id in provisional_usage_ids:
                continue
            provisional_usage_ids.add(usage_id)
            warnings.append(
                _warning(
                    "PROVISIONAL_SOURCE",
                    (
                        f"Material Usage {binding.usage_id} is unverified"
                        if binding.usage_id
                        else f"Material source {binding.entity_id} is provisional"
                    ),
                )
            )
        effective_include_provisional = include_provisional and self._config_bool(
            "allow_provisional_material_usage", True
        )
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        package = assemble_context_package(
            entity={
                "type": "session",
                "id": session_id,
                "title": record_label(collection.session) or session_id,
            },
            scope={
                "intent": RetrievalIntent.SESSION.value,
                "status": "session",
                "hard_boundary": False,
                "include_provisional": effective_include_provisional,
                "provisional": bool(provisional_usage_ids),
            },
            sources=final_evidence,
            professor_signals=collection.professor_signals,
            user_context=collection.user_context,
            warnings=warnings,
            context_id=capability.context_id,
            already_bounded=True,
        )
        if not collection.transcript_loaded and not final_evidence:
            unavailable = next(
                (warning for warning in warnings if _warning_code(warning) == "SOURCE_UNAVAILABLE"),
                None,
            )
            if unavailable is not None:
                raise SourceUnavailableError(
                    "no usable transcript derivative is available",
                    details={"session_id": session_id},
                )
        return package

    def get_source_chunk(
        self,
        context_id: str,
        locator: str | PageLocator | TimeLocator,
        *,
        caller_scope: str | None = None,
        source_class: str | None = None,
        role: str | None = None,
    ) -> EvidenceItem:
        """Read one chunk only after the parent context capability authorizes it."""

        capability = self.ephemeral.get_context_capability(context_id)
        if capability is None:
            raise ContextExpiredError(
                "Context capability is missing or expired",
                details={"context_id": context_id},
            )
        if (
            getattr(capability, "caller_scope", None) is not None
            and getattr(capability, "caller_scope", None) != caller_scope
        ):
            raise LocatorNotAllowedError(
                "caller scope is not allowed for this context",
                details={"context_id": context_id},
            )
        bindings = self.capabilities.bindings_for(context_id)
        if bindings is None:
            raise LocatorNotAllowedError("context has no retrieval role binding")
        try:
            parsed_locator = parse_locator(locator) if isinstance(locator, str) else locator
        except LocatorParseError as exc:
            # Locator parsing is part of the capability boundary.  Do not
            # expose the lower-level parser taxonomy to a caller attempting a
            # source read.
            raise LocatorNotAllowedError(
                "requested locator is malformed",
                details={"context_id": context_id},
            ) from exc
        if not isinstance(parsed_locator, (PageLocator, TimeLocator)):
            raise LocatorNotAllowedError(
                "requested locator is malformed",
                details={"context_id": context_id},
            )
        if source_class is not None and role is not None and (
            not isinstance(source_class, str)
            or not isinstance(role, str)
            or source_class.casefold() != role.casefold()
        ):
            raise LocatorNotAllowedError(
                "conflicting source_class and role aliases",
                details={"context_id": context_id},
            )
        requested_role = source_class if source_class is not None else role

        def validate_candidate(candidate: CapabilityBinding) -> SourceFingerprint | None:
            if requested_role is not None and (
                not isinstance(requested_role, str)
                or requested_role.casefold() != candidate.source_class.casefold()
            ):
                return None
            return self._current_fingerprint_for_binding(candidate)

        # CapabilityManager performs candidate-aware selection and returns the
        # exact binding used below.  The engine never independently chooses a
        # first containing range.
        binding = self.capabilities.authorize(
            context_id,
            parsed_locator,
            caller_scope=caller_scope,
            candidate_validator=validate_candidate,
        )
        try:
            current = self._current_fingerprint_for_binding(binding)
        except LocatorStaleError:
            raise
        except Exception as exc:
            raise LocatorNotAllowedError(
                "current source authorization is unavailable",
                details={"entity_id": binding.entity_id},
            ) from exc
        if current is None:
            raise LocatorNotAllowedError(
                "current source fingerprint is unavailable",
                details={"entity_id": binding.entity_id},
            )
        issued = SourceFingerprint(binding.source_version, binding.source_hash)
        if current != issued:
            raise LocatorStaleError(
                "current source fingerprint no longer matches the issued capability",
                details={"entity_id": binding.entity_id, "locator": str(parsed_locator)},
            )
        derivative = self._read_derived(binding.source_ref, binding.entity_id)
        try:
            derived_entity, _body, _, front = derivative_parts(derivative)
        except Exception as exc:
            raise SourceUnavailableError(
                "normalized derivative could not be parsed",
                details={"entity_id": binding.entity_id},
            ) from exc
        expected_schema = _schema_for_source_class(binding.source_class)
        if front.get("schema") != expected_schema:
            raise SourceUnavailableError(
                "normalized derivative schema does not match the capability source",
                details={"entity_id": binding.entity_id, "expected_schema": expected_schema},
            )
        if derived_entity != binding.entity_id:
            raise SourceUnavailableError("derivative entity does not match capability")
        derivative_fp = _front_fingerprint(front)
        if derivative_fp is None:
            raise LocatorStaleError(
                "normalized derivative has no valid source fingerprint",
                details={"entity_id": binding.entity_id, "locator": str(parsed_locator)},
            )
        if derivative_fp != current:
            raise LocatorStaleError(
                "current derivative does not match the capability source fingerprint",
                details={"entity_id": binding.entity_id, "locator": str(parsed_locator)},
            )
        if (binding.session_id is not None or binding.material_id is not None) and not binding.course_key:
            raise SourceUnavailableError(
                "graph-bound capability has no validated Course Key",
                details={"entity_id": binding.entity_id},
            )
        _validate_read_front_matter(
            front,
            expected_schema=expected_schema,
            entity_id=binding.entity_id,
            source_ref=binding.source_ref,
            expected_course_key=binding.course_key,
        )
        if isinstance(parsed_locator, TimeLocator):
            chunks = timestamp_chunks(derivative, entity_id=binding.entity_id)
        else:
            chunks = page_chunks(derivative, entity_id=binding.entity_id)
        chunk = find_chunk_containing(chunks, parsed_locator)
        if chunk is None:
            raise LocatorNotAllowedError(
                "locator was authorized but no current chunk contains it",
                details={"locator": str(parsed_locator)},
            )
        try:
            post_read = self._current_fingerprint_for_binding(binding)
        except LocatorStaleError:
            raise
        except Exception as exc:
            raise LocatorNotAllowedError(
                "source authorization changed during derivative read",
                details={"entity_id": binding.entity_id},
            ) from exc
        if post_read is None:
            raise LocatorNotAllowedError("source authorization changed during derivative read")
        if post_read != issued or post_read != derivative_fp:
            raise LocatorStaleError("source fingerprint changed during derivative read")
        item = self._evidence_from_chunk(
            chunk,
            source_class=binding.source_class,
            fingerprint=current,
            front_matter=front,
            source_ref=binding.source_ref,
            requested_locator=parsed_locator,
            provisional=binding.provisional,
        )
        return item

    def _get_session(self, session_id: str) -> Any | None:
        method = getattr(self.notion_reader, "get_session", None)
        if method is None:
            raise SourceUnavailableError("NotionReader has no get_session method")
        expected_id = strict_entity_id(session_id, "S")
        if expected_id is None:
            raise SourceUnavailableError("requested Session logical ID is malformed")
        for value in (session_id, session_id.upper()):
            try:
                result = method(value)
            except _MISSING_PROVIDER_ERRORS:
                continue
            except Exception as exc:
                raise SourceUnavailableError("Notion session lookup is unavailable") from exc
            if result is not None:
                if _graph_logical_id(result, "S") != expected_id:
                    raise SourceUnavailableError(
                        "Notion Session logical ID is missing or mismatched",
                        details={"session_id": expected_id},
                    )
                return result
        return None

    def _material_usages(self, session_id: str) -> list[Any]:
        method = getattr(self.notion_reader, "get_material_usage", None)
        if method is None:
            raise SourceUnavailableError("NotionReader has no get_material_usage method")
        try:
            values = method(session_id)
        except _MISSING_PROVIDER_ERRORS:
            return []
        except Exception as exc:
            raise SourceUnavailableError("Notion Material Usage lookup is unavailable") from exc
        if values is None:
            return []
        if isinstance(values, Mapping):
            return [values]
        if isinstance(values, (str, bytes)):
            return []
        return list(values)

    def _material_for_usage(self, usage: Any, material_id: str) -> Any:
        # Usage.Material is a relation identity only.  Expanded relation
        # snapshots are caller/provider payloads and may be stale; every
        # retrieval and follow-up must use the current authoritative Material
        # page by ID.
        return self._lookup_material(material_id)

    def _lookup_material(self, material_id: str) -> Any | None:
        method = getattr(self.notion_reader, "get_material", None)
        if method is None:
            raise SourceUnavailableError("NotionReader has no get_material method")
        expected_id = strict_entity_id(material_id, "M")
        if expected_id is None:
            raise SourceUnavailableError("requested Material logical ID is malformed")
        try:
            result = method(material_id)
        except _MISSING_PROVIDER_ERRORS:
            return None
        except Exception as exc:
            raise SourceUnavailableError("Notion Material lookup is unavailable") from exc
        if result is not None and _graph_logical_id(result, "M") != expected_id:
            raise SourceUnavailableError(
                "Notion Material logical ID is missing or mismatched",
                details={"material_id": expected_id},
            )
        return result

    def _user_references(self, session_id: str) -> tuple[Mapping[str, Any], ...]:
        method = getattr(self.notion_reader, "get_session_user_annotations", None)
        if method is None:
            raise SourceUnavailableError(
                "NotionReader has no get_session_user_annotations method"
            )
        try:
            annotations = method(session_id)
        except _MISSING_PROVIDER_ERRORS:
            return ()
        except Exception as exc:
            raise SourceUnavailableError("Notion USER annotation lookup is unavailable") from exc
        return tuple(user_reference(annotation) for annotation in (annotations or []))

    def _session_signals(
        self,
        session_id: str,
        current_derivative: Any | None,
        current_fingerprint: SourceFingerprint | None,
        warnings: list[Any],
    ) -> tuple[Mapping[str, Any], ...]:
        method = getattr(self.notion_reader, "get_session_enrichment", None)
        if method is None:
            raise SourceUnavailableError("NotionReader has no get_session_enrichment method")
        try:
            raw = method(session_id)
        except _MISSING_PROVIDER_ERRORS:
            return ()
        except Exception as exc:
            raise SourceUnavailableError("Notion enrichment lookup is unavailable") from exc
        if raw is None or current_fingerprint is None:
            return ()
        try:
            enrichment = coerce_enrichment(raw)
        except (TypeError, ValueError):
            warnings.append(_warning("STALE_ENRICHMENT", "invalid enrichment record"))
            return ()
        if enrichment.is_fresh(current_fingerprint):
            return tuple(_fresh_signal_items(enrichment.payload))
        warnings.append(_warning("STALE_ENRICHMENT", "session enrichment is stale"))
        if current_derivative is None:
            return ()
        result: list[Mapping[str, Any]] = []
        for hint in _symbolic_hints(enrichment.payload):
            locator = revalidate_locator(hint, current_derivative)
            if locator is None:
                continue
            topic = _hint_text(hint)
            result.append(
                {
                    "kind": "stale_locator_hint",
                    "topic": topic,
                    "locator": str(locator),
                    "freshness": FreshnessStatus.STALE.value,
                    "factual": False,
                }
            )
        return tuple(result)

    def _read_current_derivative(
        self,
        entity_id: str,
        source_ref: SourceRef | None,
        record: Any,
        *,
        expected_schema: str | None,
        expected_course_key: str | None = None,
    ) -> tuple[Any, SourceFingerprint, dict[str, Any]] | None:
        derivative = self._read_derived(source_ref, entity_id)
        try:
            derived_entity, derived_body, _, front = derivative_parts(derivative)
        except Exception as exc:
            raise SourceUnavailableError("normalized derivative could not be parsed") from exc
        if derived_entity != entity_id:
            raise _InvalidDerivative(
                f"normalized derivative entity does not match {entity_id}"
            )
        if expected_schema is not None and front.get("schema") != expected_schema:
            raise _InvalidDerivative("normalized derivative schema does not match")
        derivative_fp = _front_fingerprint(front)
        if derivative_fp is None:
            raise _InvalidDerivative(
                f"normalized derivative for {entity_id} has no valid source fingerprint"
            )
        current = self._current_fingerprint(entity_id, source_ref)
        if current is None:
            raise SourceUnavailableError("canonical source fingerprint is unavailable")
        if derivative_fp != current:
            raise _DerivativeBehindSource(
                f"derivative for {entity_id} is behind the current canonical source"
            )
        _validate_read_front_matter(
            front,
            expected_schema=expected_schema,
            entity_id=entity_id,
            source_ref=source_ref,
            expected_course_key=expected_course_key,
        )
        if expected_schema == "uls.transcript.v1":
            # A hand-edited or damaged derivative may still claim ``ready``
            # in front matter.  Re-run the deterministic marker check at the
            # read boundary so malformed extraction is never presented as a
            # complete transcript.  The body itself remains untouched.
            from uls.normalization.transcript import extract_timestamp_marks

            _, extraction_failed = extract_timestamp_marks(derived_body)
            if extraction_failed and str(front["status"]).casefold() != DerivativeStatus.PARTIAL.value:
                front = dict(front)
                front["status"] = DerivativeStatus.PARTIAL.value
        return derivative, current, front

    def _read_derived(self, source_ref: SourceRef | None, entity_id: str) -> Any:
        method = getattr(self.drive_reader, "read_derived", None)
        if method is None:
            raise SourceUnavailableError("DriveReader has no read_derived method")
        if not isinstance(source_ref, SourceRef):
            raise SourceUnavailableError(
                "trusted originating SourceRef is unavailable",
                details={"entity_id": entity_id},
            )
        try:
            value = method(source_ref)
        except _MISSING_PROVIDER_ERRORS as exc:
            raise SourceUnavailableError(
                f"derived transcript/material is unavailable for {entity_id}",
                details={"entity_id": entity_id},
            ) from exc
        except Exception as exc:
            # A provider/network failure is not the same as a missing source.
            # Never retry through a graph URL, file-id coercion, or entity ID.
            raise SourceUnavailableError(
                f"derived transcript/material is unavailable for {entity_id}",
                details={"entity_id": entity_id},
            ) from exc
        if value is not None:
            return value
        raise SourceUnavailableError(
            f"derived transcript/material is unavailable for {entity_id}",
            details={"entity_id": entity_id},
        )

    def _current_fingerprint(
        self,
        entity_id: str,
        source_ref: SourceRef | None,
    ) -> SourceFingerprint | None:
        method = getattr(self.drive_reader, "get_current_fingerprint", None)
        if method is None:
            raise SourceUnavailableError(
                "DriveReader has no get_current_fingerprint method",
                details={"entity_id": entity_id},
            )
        if not isinstance(source_ref, SourceRef):
            raise SourceUnavailableError(
                "trusted originating SourceRef is unavailable",
                details={"entity_id": entity_id},
            )
        try:
            value = method(source_ref)
        except _MISSING_PROVIDER_ERRORS:
            return None
        except Exception as exc:
            raise SourceUnavailableError(
                "current source fingerprint lookup is unavailable",
                details={"entity_id": entity_id},
            ) from exc
        try:
            return coerce_fingerprint(value)
        except (TypeError, ValueError):
            return None

    def _resolve_source_ref(
        self,
        record: Any,
        *names: str,
        entity_id: str,
    ) -> SourceRef:
        """Resolve a graph pointer through trusted registered provenance."""

        value = field(record, *names, default=None)
        if not isinstance(value, str) or not value.strip():
            raise SourceUnavailableError(
                "normalized source pointer is missing or malformed",
                details={"entity_id": entity_id},
            )
        resolver = self.source_binding_resolver
        if resolver is None:
            raise SourceUnavailableError(
                "trusted source binding resolver is unavailable",
                details={"entity_id": entity_id},
            )
        try:
            result = resolver.resolve_derivative_ref(entity_id, value.strip())
        except SourceUnavailableError:
            raise
        except Exception as exc:
            raise SourceUnavailableError(
                "trusted source binding resolution is unavailable",
                details={"entity_id": entity_id},
            ) from exc
        if not isinstance(result, SourceRef):
            raise SourceUnavailableError("trusted source binding returned no SourceRef")
        return result

    def _course_for_record(self, record: Any, entity_id: str) -> CourseIdentity:
        relation_value = raw_field(record, "Course", "course", default=None)
        try:
            relation_page_id = strict_single_relation_page_id(relation_value)
        except (TypeError, ValueError) as exc:
            raise SourceUnavailableError(
                "record Course relation is malformed",
                details={"entity_id": entity_id},
            ) from exc
        if relation_page_id is None:
            raise SourceUnavailableError(
                "record must have exactly one Course relation",
                details={"entity_id": entity_id},
            )
        return self._course_by_relation_id(relation_page_id, entity_id)

    def _course_by_relation_id(self, relation_page_id: str, entity_id: str) -> CourseIdentity:
        method = getattr(self.notion_reader, "get_course_by_relation_id", None)
        if not callable(method):
            raise SourceUnavailableError(
                "NotionReader has no exact Course relation lookup",
                details={"entity_id": entity_id, "course_relation_page_id": relation_page_id},
            )
        try:
            course = method(relation_page_id)
        except _MISSING_PROVIDER_ERRORS as exc:
            raise SourceUnavailableError("Course relation target is unavailable") from exc
        except Exception as exc:
            raise SourceUnavailableError("Course relation lookup is unavailable") from exc
        identity = validate_course_record(course, relation_page_id)
        if identity is None:
            raise SourceUnavailableError(
                "Course record has an invalid Course Key identity",
                details={"entity_id": entity_id, "course_relation_page_id": relation_page_id},
            )
        return identity

    def _material_type(self, material: Any) -> str:
        value = strict_text(raw_field(material, "Type", "material_type", default=None), default=None)
        if value is None:
            raise SourceUnavailableError("material Type is missing or invalid")
        return value

    def _material_source_class(self, material: Any) -> str:
        value = self._material_type(material)
        mapping = self._config_mapping("material_type_source_class", {})
        if not isinstance(mapping, Mapping):
            raise SourceUnavailableError("material type source mapping is unavailable")
        result = material_source_class(value, mapping)
        if result is None:
            raise SourceUnavailableError(
                "material type is not mapped to a source authority",
                details={"material_type": value},
            )
        return result

    def _safe_usage_scopes(
        self,
        usages: list[Any],
        *,
        session_id: str,
        course: CourseIdentity,
        warnings: list[Any],
    ) -> list[Any]:
        """Validate duplicate Usage identity and exact graph course bindings."""

        # Count physical app-level IDs before schema/range/course filtering.
        # A malformed sibling with the same ID is still an ambiguity and must
        # not disappear before the duplicate check.
        raw_id_counts: dict[str, int] = {}
        for usage in usages:
            raw_id = usage_app_id(usage)
            if raw_id:
                raw_id_counts[raw_id] = raw_id_counts.get(raw_id, 0) + 1
        duplicate_ids = {key for key, count in raw_id_counts.items() if count > 1}

        scopes: list[Any] = []
        for usage in usages:
            parsed = material_usage_scope_result(usage)
            scope = parsed.scope
            if scope is None:
                warnings.append(
                    _warning(
                        "SOURCE_UNAVAILABLE",
                        f"Material Usage {usage_app_id(usage) or '<unknown>'} excluded: {parsed.reason or 'invalid Usage row'}",
                    )
                )
                continue
            if scope.session_id != session_id:
                warnings.append(
                    _warning(
                        "SOURCE_UNAVAILABLE",
                        f"Material Usage {scope.usage_id} does not belong to Session {session_id}",
                    )
                )
                continue
            scopes.append(scope)
        by_id: dict[str, list[Any]] = {}
        raw_identity_counts = material_usage_identity_counts(
            usages,
            session_id=session_id,
        )
        duplicate_tuples = {
            identity for identity, count in raw_identity_counts.items() if count > 1
        }
        accepted: list[Any] = []
        for scope in scopes:
            usage_id = scope.usage_id
            if usage_id is None:
                continue
            by_id.setdefault(usage_id, []).append(scope)
            try:
                material = self._material_for_usage(scope.usage, scope.material_id)
                material_course = self._course_for_record(material, scope.material_id)
            except SourceUnavailableError as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))
                continue
            if material_course != course:
                warnings.append(
                    _warning(
                        "SOURCE_UNAVAILABLE",
                        f"Material Usage {usage_id} crosses the Session Course identity",
                    )
                )
                continue
            accepted.append(scope)
        duplicate_ids.update(key for key, values in by_id.items() if len(values) > 1)
        ambiguous_scopes = [
            scope
            for scope in accepted
            if scope.usage_id in duplicate_ids
            or (session_id, scope.material_id, scope.role, scope.range) in duplicate_tuples
        ]
        for scope in ambiguous_scopes:
            warnings.append(
                _warning(
                    "SOURCE_UNAVAILABLE",
                    f"Material Usage {scope.usage_id} excluded: duplicate Material Usage identity is ambiguous",
                )
            )
        return [
            scope
            for scope in accepted
            if scope.usage_id not in duplicate_ids
            and (session_id, scope.material_id, scope.role, scope.range) not in duplicate_tuples
        ]

    def _retain_current_evidence(
        self,
        evidence: list[EvidenceItem],
        bindings: list[CapabilityBinding],
        warnings: list[Any],
    ) -> tuple[list[EvidenceItem], list[CapabilityBinding]]:
        """Recheck each independent basis after provider reads, before issuance.

        Evidence and capability bindings are created as ordered pairs.  A
        count mismatch or a pair whose identity/provisional metadata disagrees
        is unsafe to repair heuristically, so the mismatch is surfaced as a
        structured source failure (or the individual pair is excluded).  Each
        remaining pair is then revalidated independently; a revoked Usage
        cannot veto a sibling with a separate valid basis.
        """
        if len(evidence) != len(bindings):
            raise SourceUnavailableError(
                "evidence and capability bindings are misaligned",
                details={
                    "evidence_count": len(evidence),
                    "binding_count": len(bindings),
                },
            )
        retained_evidence: list[EvidenceItem] = []
        retained_bindings: list[CapabilityBinding] = []
        for item, binding in zip(evidence, bindings):
            if not self._evidence_matches_binding(item, binding):
                warnings.append(
                    _warning(
                        "SOURCE_UNAVAILABLE",
                        "evidence and capability binding identity is misaligned",
                    )
                )
                continue
            try:
                current = self._current_fingerprint_for_binding(binding)
            except SourceUnavailableError as exc:
                warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))
                continue
            except Exception:  # noqa: BLE001 - isolate one candidate failure
                # A provider or graph validation exception for one candidate
                # must not authorize that candidate or discard an independently
                # valid overlapping sibling.
                warnings.append(
                    _warning(
                        "SOURCE_UNAVAILABLE",
                        "source basis could not be revalidated",
                    )
                )
                continue
            issued = SourceFingerprint(binding.source_version, binding.source_hash)
            if current is None or current != issued:
                warnings.append(_warning("SOURCE_UNAVAILABLE", "source basis changed before context issuance"))
                continue
            retained_evidence.append(item)
            retained_bindings.append(binding)
        return retained_evidence, retained_bindings

    @staticmethod
    def _evidence_matches_binding(
        item: EvidenceItem,
        binding: CapabilityBinding,
    ) -> bool:
        """Check the immutable pair identity before any freshness lookup."""

        if not isinstance(item, EvidenceItem) or not isinstance(binding, CapabilityBinding):
            return False
        if (
            item.entity_id != binding.entity_id
            or item.locator != binding.locator
            or item.source_class != binding.source_class
            or item.fingerprint
            != SourceFingerprint(binding.source_version, binding.source_hash)
            or bool(getattr(item, "provisional", False)) != binding.provisional
        ):
            return False
        item_source_ref = getattr(item, "source_ref", None)
        if binding.source_ref is None:
            return item_source_ref is None
        return (
            isinstance(item_source_ref, SourceRef)
            and isinstance(binding.source_ref, SourceRef)
            and item_source_ref.identity == binding.source_ref.identity
        )

    def _budget_evidence_bindings(
        self,
        evidence: list[EvidenceItem],
        bindings: list[CapabilityBinding],
    ) -> tuple[tuple[EvidenceItem, ...], tuple[CapabilityBinding, ...]]:
        """Apply evidence budgets while retaining exact evidence/binding pairs.

        Locator/entity/source-class keys are intentionally not used for this
        association: independent Usage rows may return the same chunk.  The
        pair is selected before any truncation, so an omitted evidence item
        cannot leave its capability binding behind.
        """

        if len(evidence) != len(bindings):
            raise SourceUnavailableError(
                "evidence and capability bindings are misaligned",
                details={
                    "evidence_count": len(evidence),
                    "binding_count": len(bindings),
                },
            )
        selected_evidence: list[EvidenceItem] = []
        selected_bindings: list[CapabilityBinding] = []
        total = 0
        for item, binding in zip(evidence, bindings):
            if not self._evidence_matches_binding(item, binding):
                raise SourceUnavailableError(
                    "evidence and capability binding identity is misaligned"
                )
            if len(selected_evidence) >= self.budget.max_evidence_items:
                break
            if total >= self.budget.max_total_chars:
                break
            if not isinstance(item.content, str):
                raise SourceUnavailableError("evidence content is not text")
            content = item.content[: self.budget.max_chars_per_item]
            remaining = self.budget.max_total_chars - total
            content = content[:remaining]
            if not content:
                continue
            if content != item.content:
                provisional = bool(getattr(item, "provisional", False))
                source_ref = getattr(item, "source_ref", None)
                extras = {
                    key: value
                    for key, value in vars(item).items()
                    if key not in EvidenceItem.__dataclass_fields__
                }
                item = replace(item, content=content)
                for key, value in extras.items():
                    object.__setattr__(item, key, value)
                if provisional:
                    object.__setattr__(item, "provisional", True)
                if source_ref is not None:
                    object.__setattr__(item, "source_ref", source_ref)
            selected_evidence.append(item)
            selected_bindings.append(binding)
            total += len(content)
        return tuple(selected_evidence), tuple(selected_bindings)

    def _current_fingerprint_for_binding(
        self,
        binding: CapabilityBinding,
    ) -> SourceFingerprint | None:
        """Candidate-aware follow-up validation used by CapabilityManager."""

        # Any graph-bound candidate (transcript or Material Usage) must be
        # revalidated before its fingerprint can authorize a follow-up.  The
        # transcript branch used to check only the Drive fingerprint, which
        # allowed a Course/source-pointer rewire to survive until after the
        # provider read.
        if binding.parent_entity_id is not None and not self._binding_role_is_current(binding):
            return None
        if (binding.session_id is not None or binding.material_id is not None) and not self._binding_role_is_current(binding):
            return None
        return self._current_fingerprint(binding.entity_id, binding.source_ref)

    def _evidence_from_chunk(
        self,
        chunk: Any,
        *,
        source_class: str,
        fingerprint: SourceFingerprint,
        front_matter: Mapping[str, Any],
        source_ref: SourceRef | None,
        requested_locator: Any | None = None,
        provisional: bool = False,
    ) -> EvidenceItem:
        provenance = make_provenance(
            front_matter,
            entity_id=chunk.entity_id,
            source_ref=source_ref,
            source_hash=fingerprint.source_hash,
            source_version=fingerprint.source_version,
        )
        item = make_evidence_item(
            source_class=source_class,
            entity_id=chunk.entity_id,
            locator=requested_locator or chunk.locator,
            fingerprint=fingerprint,
            content=chunk.content,
            provenance=provenance,
            freshness=FreshnessStatus.FRESH,
            source_ref=source_ref,
            provisional=provisional,
        )
        return item

    def _binding_role_is_current(self, binding: CapabilityBinding) -> bool:
        if binding.provisional and not self._config_bool(
            "allow_provisional_material_usage", True
        ):
            return False
        if binding.parent_entity_id is not None and not self._parent_binding_is_current(binding):
            return False
        if not binding.relation_required and binding.session_id and binding.material_id is None:
            session = self._get_session(binding.session_id)
            if session is None:
                return False
            try:
                course = self._course_for_record(session, binding.session_id)
                current_ref = self._resolve_source_ref(
                    session,
                    "Normalized Transcript",
                    "normalized_transcript",
                    "Transcript Source Ref",
                    "transcript_source_ref",
                    "Transcript Source",
                    "Source Ref",
                    "source_ref",
                    entity_id=binding.session_id,
                )
            except SourceUnavailableError:
                return False
            return (
                binding.source_ref is not None
                and current_ref.identity == binding.source_ref.identity
                and (
                    binding.course_relation_page_id is None
                    or binding.course_relation_page_id == course.relation_page_id
                )
                and (binding.course_key is None or binding.course_key == course.course_key)
            )
        if binding.relation_required:
            if not binding.session_id or not binding.material_id or not binding.usage_id:
                return False
            current_session = self._get_session(binding.session_id)
            if current_session is None:
                return False
            try:
                session_course = self._course_for_record(current_session, binding.session_id)
            except SourceUnavailableError:
                return False
            if (
                binding.course_relation_page_id is None
                or binding.course_key is None
                or session_course.relation_page_id != binding.course_relation_page_id
                or session_course.course_key != binding.course_key
            ):
                return False
            usages = self._material_usages(binding.session_id)
            scopes = [
                scope
                for scope in self._valid_current_usage_scopes(usages)
                if scope.usage_id == binding.usage_id
                and scope.session_id == binding.session_id
                and scope.material_id == binding.material_id
                and scope.role == binding.usage_role
                and scope.range == binding.usage_range
                and (scope.verified or binding.provisional)
            ]
            if len(scopes) != 1:
                return False
            material = self._material_for_usage(scopes[0].usage, binding.material_id)
        else:
            if binding.material_id is None:
                return True
            material = self._lookup_material(binding.material_id)
            if material is None:
                return False

        try:
            course = self._course_for_record(material, binding.material_id or binding.entity_id)
            current_type = self._material_type(material)
            current_class = self._material_source_class(material)
        except SourceUnavailableError:
            return False
        if (
            binding.course_relation_page_id is not None
            and course.relation_page_id != binding.course_relation_page_id
        ):
            return False
        if binding.course_key is not None and course.course_key != binding.course_key:
            return False
        if binding.material_type is not None and current_type != binding.material_type:
            return False
        if current_class != binding.source_class:
            return False
        try:
            current_ref = self._resolve_source_ref(
                material,
                "Normalized Source",
                "normalized_source",
                "Normalized Source Ref",
                "normalized_source_ref",
                "Source Ref",
                "source_ref",
                entity_id=binding.material_id or binding.entity_id,
            )
        except SourceUnavailableError:
            return False
        return (
            binding.source_ref is not None
            and current_ref.identity == binding.source_ref.identity
        )

    def _parent_binding_is_current(self, binding: CapabilityBinding) -> bool:
        """Re-read every parent snapshot before authorizing a follow-up."""

        if (
            binding.parent_entity_id is None
            or binding.parent_entity_type not in {"exam", "activity"}
            or binding.parent_course_relation_page_id is None
            or binding.parent_course_key is None
            or binding.parent_path_leaf is None
        ):
            return False
        if binding.parent_entity_type == "exam":
            record = self._get_exam_record(binding.parent_entity_id)
            if record is None or binding.parent_included_session_ids is None:
                return False
            if (
                record.course.relation_page_id != binding.parent_course_relation_page_id
                or record.course.course_key != binding.parent_course_key
                or record.scope_confirmed != binding.parent_scope_confirmed
                or record.included_session_ids != binding.parent_included_session_ids
            ):
                return False
            if binding.entity_id == record.entity_id:
                return True
            if binding.session_id is not None:
                return binding.session_id in (record.included_session_ids or ())
            return binding.entity_id in (record.included_session_ids or ())
        activity_record = self._get_activity_record(binding.parent_entity_id)
        if activity_record is None:
            return False
        if (
            activity_record.course.relation_page_id != binding.parent_course_relation_page_id
            or activity_record.course.course_key != binding.parent_course_key
            or activity_record.related_session_ids != binding.parent_related_session_ids
            or activity_record.related_material_ids != binding.parent_related_material_ids
            or activity_record.instructions_source_url != binding.activity_instructions_source_url
            or activity_record.normalized_instructions_url != binding.activity_normalized_instructions_url
        ):
            return False
        if binding.parent_path_leaf == "activity_instructions":
            if self.source_binding_resolver is None or binding.activity_binding_identity is None:
                return False
            try:
                current = self.source_binding_resolver.resolve_activity_instructions(
                    activity_record.entity_id,
                    activity_record.instructions_source_url or "",
                    activity_record.normalized_instructions_url or "",
                )
            except Exception:  # noqa: BLE001 - failed revalidation revokes the capability
                return False
            return current.source_ref.identity == binding.activity_binding_identity
        if binding.entity_id == activity_record.entity_id:
            return True
        if binding.parent_path_leaf == "session":
            return binding.entity_id in (activity_record.related_session_ids or ())
        if binding.parent_path_leaf == "material_usage":
            return binding.session_id in (activity_record.related_session_ids or ())
        if binding.parent_path_leaf == "material":
            return binding.entity_id in (activity_record.related_material_ids or ())
        return False

    def _direct_material_binding_is_current(self, binding: CapabilityBinding) -> bool:
        return self._binding_role_is_current(binding)

    def _valid_current_usage_scopes(self, usages: list[Any]) -> list[Any]:
        raw_id_counts: dict[str, int] = {}
        for usage in usages:
            raw_id = usage_app_id(usage)
            if raw_id:
                raw_id_counts[raw_id] = raw_id_counts.get(raw_id, 0) + 1
        scopes = [
            result.scope
            for usage in usages
            for result in (material_usage_scope_result(usage),)
            if result.scope is not None
        ]
        ids: dict[str, int] = {}
        raw_identity_counts = material_usage_identity_counts(usages)
        for scope in scopes:
            if scope.usage_id:
                ids[scope.usage_id] = ids.get(scope.usage_id, 0) + 1
        return [
            scope
            for scope in scopes
            if scope.usage_id
            and scope.session_id is not None
            and scope.role is not None
            and raw_id_counts.get(scope.usage_id, 0) == 1
            and ids.get(scope.usage_id, 0) == 1
            and raw_identity_counts.get(
                (scope.session_id, scope.material_id, scope.role, scope.range),
                0,
            )
            == 1
        ]

    def _config_bool(self, name: str, default: bool) -> bool:
        missing = object()
        section = (
            self.config.get("retrieval", self.config)
            if isinstance(self.config, Mapping)
            else getattr(self.config, "retrieval", self.config)
        )
        value = (
            section.get(name, missing)
            if isinstance(section, Mapping)
            else getattr(section, name, missing)
        )
        if value is missing:
            return default
        if type(value) is not bool:
            raise PolicyDeniedError(f"retrieval.{name} must be a boolean")
        return value

    def _config_mapping(self, name: str, default: Mapping[str, Any]) -> Any:
        section = self.config.get("retrieval", self.config) if isinstance(self.config, Mapping) else getattr(self.config, "retrieval", self.config)
        value = section.get(name, default) if isinstance(section, Mapping) else getattr(section, name, default)
        return value


class _DerivativeBehindSource(SourcePartialError):
    """Internal control-flow marker for a derivative/source mismatch."""


class _InvalidDerivative(SourcePartialError):
    """Internal marker for a derivative that cannot be factual evidence."""


class _InadmissibleDerivative(SourceUnavailableError):
    """A well-shaped derivative whose lifecycle state is not readable evidence."""


_MISSING_PROVIDER_ERRORS = (KeyError, LookupError, FileNotFoundError)


def _graph_logical_id(record: Any, expected_type: str) -> str | None:
    """Read and strictly validate the graph record's logical ``ID``."""

    return strict_entity_id(usage_app_id(record), expected_type)


def _config_int(config: Any, name: str, default: int) -> int:
    section = config.get("retrieval", config) if isinstance(config, Mapping) else getattr(config, "retrieval", config)
    value = section.get(name, default) if isinstance(section, Mapping) else getattr(section, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _front_fingerprint(front: Mapping[str, Any]) -> SourceFingerprint | None:
    """Read a derivative fingerprint without coercing malformed metadata."""

    if not isinstance(front, Mapping):
        return None
    source_version = front.get("source_version")
    source_hash = front.get("source_hash")
    if (
        isinstance(source_version, bool)
        or not isinstance(source_version, int)
        or source_version < 1
        or not isinstance(source_hash, str)
        or not source_hash.strip()
    ):
        return None
    return SourceFingerprint(source_version, source_hash.strip())


_REQUIRED_DERIVATIVE_FRONT_MATTER = (
    "schema",
    "entity_id",
    "course_key",
    "source_ref",
    "source_hash",
    "source_version",
    "processor_version",
    "normalized_at",
    "status",
)


def _validate_read_front_matter(
    front: Mapping[str, Any],
    *,
    expected_schema: str | None,
    entity_id: str,
    source_ref: SourceRef | None,
    expected_course_key: str | None = None,
) -> None:
    """Validate every provenance field before a derivative becomes factual.

    Parsing a fingerprint is intentionally not enough here.  A derivative can
    carry a matching entity/hash/version while omitting the rest of §16, and
    those omissions must never be repaired at the retrieval boundary.
    """

    if not isinstance(front, Mapping):
        raise _InvalidDerivative("normalized derivative front matter is not a mapping")
    missing = [name for name in _REQUIRED_DERIVATIVE_FRONT_MATTER if name not in front]
    if missing:
        raise _InvalidDerivative(
            "normalized derivative front matter is incomplete: "
            + ", ".join(missing)
        )

    schema = front["schema"]
    if not isinstance(schema, str) or not schema.strip() or (
        expected_schema is not None and schema != expected_schema
    ):
        raise _InvalidDerivative("normalized derivative front matter has an invalid schema")

    front_entity = front["entity_id"]
    if not isinstance(front_entity, str) or not front_entity.strip() or front_entity != entity_id:
        raise _InvalidDerivative("normalized derivative front matter has an invalid entity_id")

    for name in ("course_key", "processor_version", "normalized_at"):
        value = front[name]
        if not isinstance(value, str) or not value.strip():
            raise _InvalidDerivative(
                f"normalized derivative front matter has an invalid {name}"
            )
    if expected_course_key is not None and front["course_key"] != expected_course_key:
        raise _InvalidDerivative(
            "normalized derivative front matter course_key does not match the graph Course"
        )

    source_hash = front["source_hash"]
    if not isinstance(source_hash, str) or not source_hash.strip():
        raise _InvalidDerivative(
            "normalized derivative front matter has an invalid source_hash"
        )
    source_version = front["source_version"]
    if (
        isinstance(source_version, bool)
        or not isinstance(source_version, int)
        or source_version < 1
    ):
        raise _InvalidDerivative(
            "normalized derivative front matter has an invalid source_version"
        )

    front_source_ref = _strict_front_source_ref(front["source_ref"])
    if (
        front_source_ref is None
        or not isinstance(front_source_ref.provider, str)
        or not isinstance(front_source_ref.file_id, str)
        or not front_source_ref.provider.strip()
        or not front_source_ref.file_id.strip()
    ):
        raise _InvalidDerivative(
            "normalized derivative front matter has an invalid source_ref"
        )
    if source_ref is not None and front_source_ref.identity != source_ref.identity:
        raise _InvalidDerivative(
            "normalized derivative front matter source_ref does not match the source"
        )

    status = front["status"]
    if isinstance(status, DerivativeStatus):
        status = status.value
    if not isinstance(status, str) or status not in {
        item.value for item in DerivativeStatus
    }:
        raise _InvalidDerivative(
            "normalized derivative front matter has an invalid status"
        )
    if status not in {
        DerivativeStatus.READY.value,
        DerivativeStatus.PARTIAL.value,
    }:
        raise _InadmissibleDerivative(
            "normalized derivative status is not admissible for factual retrieval"
        )


def _schema_for_source_class(source_class: str) -> str:
    if source_class in SUPPORTED_MATERIAL_SOURCE_CLASSES:
        return "uls.material.v1"
    if source_class == "professor_transcript":
        return "uls.transcript.v1"
    if source_class == "official_activity":
        return "uls.activity.v1"
    raise SourceUnavailableError(f"unsupported source class: {source_class}")


def _strict_front_source_ref(value: Any) -> SourceRef | None:
    if isinstance(value, SourceRef):
        return value if value.provider.strip() and value.file_id.strip() else None
    if not isinstance(value, Mapping):
        return None
    provider = value.get("provider")
    file_id = value.get("file_id")
    web_url = value.get("web_url")
    if not isinstance(provider, str) or not isinstance(file_id, str):
        return None
    if not provider.strip() or not file_id.strip():
        return None
    return SourceRef(
        provider.strip(),
        file_id.strip(),
        web_url.strip() if isinstance(web_url, str) and web_url.strip() else None,
    )


def _contains(requested: Any, allowed: Any) -> bool:
    from uls.domain.models import is_contained

    return is_contained(requested, allowed)


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _warning_code(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return value.get("code") if isinstance(value.get("code"), str) else None
    return None


def _fresh_signal_items(payload: Any) -> list[Mapping[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        result: list[Mapping[str, Any]] = []
        signal_keys = {
            "professor_signals",
            "signals",
            "exam_signals",
            "professor_emphasis",
            "professor_examples",
            "examples",
            "likely_confusions",
            "summary",
            "topics",
        }
        for key, value in payload.items():
            if key.casefold() not in signal_keys:
                continue
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if isinstance(item, Mapping):
                    signal = dict(item)
                else:
                    signal = {"content": str(item)}
                signal.setdefault("kind", key)
                signal.setdefault("freshness", FreshnessStatus.FRESH.value)
                signal.setdefault("source_class", "ai_enrichment")
                result.append(signal)
        if result:
            return result
        return [{"kind": "enrichment", "content": str(payload), "freshness": "FRESH", "source_class": "ai_enrichment"}]
    if isinstance(payload, (list, tuple)):
        return [
            dict(item) if isinstance(item, Mapping) else {"kind": "enrichment", "content": str(item), "freshness": "FRESH"}
            for item in payload
        ]
    return [{"kind": "enrichment", "content": str(payload), "freshness": "FRESH"}]


def _symbolic_hints(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        hints: list[Any] = []
        for key in ("hints", "symbolic_hints", "topics", "content_index", "signals", "professor_signals"):
            value = payload.get(key)
            if isinstance(value, (list, tuple)):
                hints.extend(value)
            elif isinstance(value, Mapping):
                hints.append(value)
            elif isinstance(value, str):
                hints.append({"topic": value})
        for key in ("topic", "heading", "term", "section", "keyword"):
            value = payload.get(key)
            if isinstance(value, str):
                hints.append({key: value})
        return hints
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return []


def _hint_text(hint: Any) -> str:
    if isinstance(hint, Mapping):
        for key in ("topic", "heading", "term", "section", "keyword", "title"):
            value = hint.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(hint)


__all__ = ["RetrievalEngine"]
