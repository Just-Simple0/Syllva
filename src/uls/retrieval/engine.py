"""Model-independent RetrievalEngine for the Phase 2 Session slice."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from uls.adapters.drive.base import DriveReader
from uls.adapters.notion.base import NotionReader
from uls.domain.enums import DerivativeStatus, FreshnessStatus, RetrievalIntent
from uls.domain.errors import (
    ContextExpiredError,
    EntityNotFoundError,
    LocatorNotAllowedError,
    LocatorParseError,
    LocatorStaleError,
    SourcePartialError,
    SourceUnavailableError,
)
from uls.domain.models import EvidenceItem, PageLocator, TimeLocator, parse_locator
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.enrichment.schemas import coerce_enrichment
from ._compat import (
    coerce_fingerprint,
    coerce_source_ref,
    field,
    record_id,
    record_label,
    relation_id,
    text,
)
from .capabilities import CapabilityManager
from .chunking import (
    derivative_parts,
    find_chunk_containing,
    page_chunks,
    timestamp_chunks,
)
from .context import assemble_context_package, bounded_evidence, budget_from_config, make_evidence_item
from .freshness import revalidate_locator
from .provenance import make_provenance
from .resolver import SessionResolver
from .schemas import CapabilityBinding, ContextPackage, ResolutionResult
from .scope import allowed_material_usages, relation_is_currently_allowed, user_reference


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
    ) -> None:
        self.notion_reader = notion_reader
        self.drive_reader = drive_reader
        self.state_store = state_store
        self.ephemeral = ephemeral
        self.config = config
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

        material = self._lookup_material(material_id)
        if material is None:
            raise EntityNotFoundError(
                f"Material not found: {material_id}",
                details={"material_id": material_id},
            )
        source_ref = self._source_ref_for_record(
            material,
            "Normalized Source",
            "normalized_source",
            "Normalized Source Ref",
            "normalized_source_ref",
            "Source Ref",
            "source_ref",
            default_entity_id=material_id,
        )
        derivative_result = self._read_current_derivative(
            material_id,
            source_ref,
            material,
            expected_schema="uls.material.v1",
        )
        if derivative_result is None:
            raise SourceUnavailableError("material derivative is unavailable")
        derivative, fingerprint, front = derivative_result
        warnings: list[Any] = []
        if str(front["status"]).casefold() == DerivativeStatus.PARTIAL.value:
            warnings.append(_warning("SOURCE_PARTIAL", "material derivative is partial"))
        chunks = page_chunks(derivative, entity_id=material_id)
        if query:
            from .chunking import select_chunks

            chunks = select_chunks(chunks, query)
        sources: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        for chunk in chunks:
            item = self._evidence_from_chunk(
                chunk,
                source_class="professor_material",
                fingerprint=fingerprint,
                front_matter=front,
                source_ref=source_ref,
            )
            sources.append(item)
            bindings.append(
                CapabilityBinding(
                    entity_id=material_id,
                    locator=item.locator,
                    source_hash=fingerprint.source_hash,
                    source_version=fingerprint.source_version,
                    source_class="professor_material",
                    source_ref=source_ref,
                )
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
        final_sources = bounded_evidence(sources, self.budget)
        final_keys = {(str(item.locator), item.entity_id, item.source_class) for item in final_sources}
        final_bindings = tuple(
            binding
            for binding in bindings
            if (str(binding.locator), binding.entity_id, binding.source_class) in final_keys
        )
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        return assemble_context_package(
            entity={"type": "material", "id": material_id, "title": record_label(material) or material_id},
            scope={"intent": "MATERIAL", "hard_boundary": False},
            sources=final_sources,
            user_context=user_context,
            warnings=warnings,
            context_id=capability.context_id,
            budget=self.budget,
        )

    def get_session_context(
        self,
        session_id: str,
        *,
        query: str | None = None,
        include_provisional: bool = False,
        caller_scope: str | None = None,
    ) -> ContextPackage:
        """Return bounded context for an already-resolved Session ID."""

        session = self._get_session(session_id)
        if session is None:
            raise EntityNotFoundError(
                f"Session not found: {session_id}",
                details={"session_id": session_id},
            )
        canonical_id = record_id(session) or session_id
        warnings: list[Any] = []
        evidence: list[EvidenceItem] = []
        bindings: list[CapabilityBinding] = []
        transcript_derivative: Any | None = None
        transcript_fingerprint: SourceFingerprint | None = None
        transcript_loaded = False
        provisional_included = False

        transcript_source = self._source_ref_for_record(
            session,
            "Normalized Transcript",
            "normalized_transcript",
            "Transcript Source Ref",
            "transcript_source_ref",
            "Transcript Source",
            "Source Ref",
            default_entity_id=canonical_id,
        )
        try:
            transcript_result = self._read_current_derivative(
                canonical_id,
                transcript_source,
                session,
                expected_schema="uls.transcript.v1",
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
                        )
                    )
        except SourcePartialError as exc:
            warnings.append(_warning("SOURCE_PARTIAL", str(exc)))
        except SourceUnavailableError as exc:
            warnings.append(_warning("SOURCE_UNAVAILABLE", exc.message))

        usages = self._material_usages(canonical_id)
        for usage_scope in allowed_material_usages(
            usages,
            include_provisional=include_provisional,
        ):
            material = self._material_for_usage(usage_scope.usage, usage_scope.material_id)
            material_source = self._source_ref_for_record(
                material,
                "Normalized Source",
                "normalized_source",
                "Normalized Source Ref",
                "normalized_source_ref",
                "Source Ref",
                "source_ref",
                default_entity_id=usage_scope.material_id,
            )
            try:
                material_result = self._read_current_derivative(
                    usage_scope.material_id,
                    material_source,
                    material,
                    expected_schema="uls.material.v1",
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
                if query:
                    from .chunking import select_chunks

                    chunks = select_chunks(chunks, query)
                for chunk in chunks:
                    item = self._evidence_from_chunk(
                        chunk,
                        source_class="professor_material",
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
                        )
                    )
                if usage_scope.provisional:
                    provisional_included = True
                    warnings.append(
                        _warning(
                            "PROVISIONAL_SOURCE",
                            f"Material Usage {usage_scope.material_id} is unverified",
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

        final_evidence = bounded_evidence(evidence, self.budget)
        final_keys = {(str(item.locator), item.entity_id, item.source_class) for item in final_evidence}
        final_bindings = tuple(
            binding
            for binding in bindings
            if (str(binding.locator), binding.entity_id, binding.source_class) in final_keys
        )
        capability = self.capabilities.issue(final_bindings, caller_scope=caller_scope)
        package = assemble_context_package(
            entity={
                "type": "session",
                "id": canonical_id,
                "title": record_label(session) or canonical_id,
            },
            scope={
                "intent": RetrievalIntent.SESSION.value,
                "status": "session",
                "hard_boundary": False,
                "include_provisional": include_provisional,
                "provisional": provisional_included,
            },
            sources=final_evidence,
            professor_signals=professor_signals,
            user_context=user_context,
            warnings=warnings,
            context_id=capability.context_id,
            budget=self.budget,
        )
        # A truly unavailable transcript with no usable relation is surfaced as
        # a structured source error.  Existing partial/stale derivatives are
        # represented by the package warning and limited evidence above.
        if not transcript_loaded and not final_evidence:
            unavailable = next(
                (warning for warning in warnings if _warning_code(warning) == "SOURCE_UNAVAILABLE"),
                None,
            )
            if unavailable is not None:
                raise SourceUnavailableError(
                    "no usable transcript derivative is available",
                    details={"session_id": canonical_id},
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
        binding = next(
            (candidate for candidate in bindings if _contains(parsed_locator, candidate.locator)),
            None,
        )
        if binding is None:
            raise LocatorNotAllowedError(
                "requested locator is outside the returned chunk ranges",
                details={"context_id": context_id, "locator": str(parsed_locator)},
            )
        requested_role = source_class if source_class is not None else role
        if requested_role is not None and requested_role.casefold() != binding.source_class.casefold():
            raise LocatorNotAllowedError(
                "requested source role is not allowed by the capability",
                details={"context_id": context_id, "source_class": requested_role},
            )
        current = self._current_fingerprint(binding.entity_id, binding.source_ref)
        if current is None:
            raise LocatorNotAllowedError(
                "current source fingerprint is unavailable",
                details={"entity_id": binding.entity_id},
            )
        self.capabilities.authorize(
            context_id,
            parsed_locator,
            caller_scope=caller_scope,
            current_fingerprint=current,
            role_validator=lambda value: self._binding_role_is_current(value),
        )
        derivative = self._read_derived(binding.source_ref, binding.entity_id)
        try:
            derived_entity, body, _, front = derivative_parts(derivative)
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
        _validate_read_front_matter(
            front,
            expected_schema=expected_schema,
            entity_id=binding.entity_id,
            source_ref=binding.source_ref,
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
        for value in (session_id, session_id.upper()):
            try:
                result = method(value)
            except _MISSING_PROVIDER_ERRORS:
                continue
            except Exception as exc:
                raise SourceUnavailableError("Notion session lookup is unavailable") from exc
            if result is not None:
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
        inline = field(usage, "Material", "material", default=None)
        if inline is not None and not isinstance(inline, str):
            inline_id = relation_id(inline)
            if inline_id == material_id or isinstance(inline, Mapping):
                return inline
        result = self._lookup_material(material_id)
        if result is not None:
            return result
        return {"ID": material_id, "Name": material_id}

    def _lookup_material(self, material_id: str) -> Any | None:
        method = getattr(self.notion_reader, "get_material", None)
        if method is None:
            raise SourceUnavailableError("NotionReader has no get_material method")
        try:
            return method(material_id)
        except _MISSING_PROVIDER_ERRORS:
            return None
        except Exception as exc:
            raise SourceUnavailableError("Notion Material lookup is unavailable") from exc

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
        candidates: list[Any] = []
        if source_ref is not None:
            candidates.extend([source_ref, source_ref.file_id])
        candidates.append(entity_id)
        last_error: Exception | None = None
        seen: set[str] = set()
        for candidate in candidates:
            key = repr(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                value = method(candidate)
            except _MISSING_PROVIDER_ERRORS as exc:
                last_error = exc
                continue
            except Exception as exc:
                # A provider/network failure is not the same as a missing
                # source.  Do not retry it through alternate aliases or turn
                # it into an empty result.
                raise SourceUnavailableError(
                    f"derived transcript/material is unavailable for {entity_id}",
                    details={"entity_id": entity_id},
                ) from exc
            if value is not None:
                return value
        raise SourceUnavailableError(
            f"derived transcript/material is unavailable for {entity_id}",
            details={"entity_id": entity_id},
        ) from last_error

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
        candidates: list[Any] = [entity_id]
        if source_ref is not None:
            candidates.extend([source_ref, source_ref.file_id])
        for candidate in candidates:
            try:
                value = method(candidate)
            except _MISSING_PROVIDER_ERRORS:
                continue
            except Exception as exc:
                raise SourceUnavailableError(
                    "current source fingerprint lookup is unavailable",
                    details={"entity_id": entity_id},
                ) from exc
            result = coerce_fingerprint(value)
            if result is not None:
                return result
        return None

    def _source_ref_for_record(
        self,
        record: Any,
        *names: str,
        default_entity_id: str,
    ) -> SourceRef | None:
        if record is None:
            return SourceRef("google_drive", default_entity_id)
        value = field(record, *names, default=None)
        result = coerce_source_ref(value)
        if result is not None:
            return result
        # In simple fakes the normalized URL/file ID is stored as a string in
        # one of the requested fields; coerce_source_ref intentionally treats
        # it as a Drive file ID.
        if isinstance(value, str) and value.strip():
            return SourceRef("google_drive", value.strip())
        return SourceRef("google_drive", default_entity_id)

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
            provisional=provisional,
        )
        if source_ref is not None:
            object.__setattr__(item, "source_ref", source_ref)
        return item

    def _binding_role_is_current(self, binding: CapabilityBinding) -> bool:
        if not binding.relation_required or not binding.session_id or not binding.material_id:
            return True
        return relation_is_currently_allowed(
            self._material_usages(binding.session_id),
            binding.material_id,
            include_provisional=binding.provisional,
            usage_id=binding.usage_id,
            locator=binding.locator,
        )


class _DerivativeBehindSource(SourcePartialError):
    """Internal control-flow marker for a derivative/source mismatch."""


class _InvalidDerivative(SourcePartialError):
    """Internal marker for a derivative that cannot be factual evidence."""


_MISSING_PROVIDER_ERRORS = (KeyError, LookupError, FileNotFoundError)


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

    front_source_ref = coerce_source_ref(front["source_ref"])
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


def _schema_for_source_class(source_class: str) -> str:
    if source_class.casefold() in {"professor_material", "material"}:
        return "uls.material.v1"
    return "uls.transcript.v1"


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
