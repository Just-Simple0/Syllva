"""Model-neutral entity, concept, USER and VERIFY tool implementations."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from uls.domain.course_identity import validate_course_record
from uls.domain.errors import EntityNotFoundError, SourceUnavailableError
from uls.domain.ids import strict_entity_id
from uls.domain.models import ContextPackage
from uls.ephemeral.models import ResolutionCandidate, ResolvedEntity
from uls.retrieval._compat import exact_alias_match, field, record_label
from uls.retrieval.context import assemble_context_package
from uls.retrieval.lexical import lexical_score
from uls.retrieval.schemas import ResolutionResult
from uls.retrieval.scope import user_reference

_TYPES = {'material': 'M', 'exam': 'E', 'activity': 'A'}


def _course(engine: Any, key: str) -> tuple[Any, Any]:
    record = engine.resolver._lookup_course(key)
    if record is None or isinstance(record, str):
        raise EntityNotFoundError('Course not found')
    course = validate_course_record(record, field(record, 'id', default=''))
    if course is None:
        raise SourceUnavailableError('Course identity is invalid')
    return record, course


def resolve_academic_entity(engine: Any, query: str, course_hint: Any, entity_type: str) -> ResolutionResult:
    kind = entity_type.casefold()
    if kind not in _TYPES or not isinstance(query, str) or not query.strip():
        raise EntityNotFoundError('Unsupported entity type or empty query')
    query = query.strip()
    getter = getattr(engine, '_get_' + kind + '_record', None)
    if kind == 'material':
        getter = engine._lookup_material
    if not callable(getter):
        raise SourceUnavailableError('Entity lookup is unavailable')
    identity = strict_entity_id(query.upper(), _TYPES[kind])
    if identity:
        record = getter(identity)
        if record is None:
            raise EntityNotFoundError('Entity not found')
        current_course = (record.course if kind in {'exam', 'activity'}
                          else engine._course_for_record(record, identity))
        if course_hint is not None and _course(engine, course_hint)[1] != current_course:
            raise EntityNotFoundError('Entity is outside the requested Course')
        return ResolutionResult('resolved', ResolvedEntity(kind, identity, record_label(record) or identity))
    if not course_hint:
        raise EntityNotFoundError('Provide a Course hint or exact entity ID')
    course_record, course = _course(engine, course_hint)
    plural = 'activities' if kind == 'activity' else kind + 's'
    method = getattr(engine.notion_reader, 'list_course_' + plural, None)
    if not callable(method):
        raise SourceUnavailableError('Entity candidate lookup is unavailable')
    candidates = list(method(course_record))
    matched: list[tuple[str, str]] = []
    for record in candidates:
        entity_id = strict_entity_id(field(record, 'ID', 'entity_id'), _TYPES[kind])
        if entity_id is None or engine._course_for_record(record, entity_id) != course:
            continue
        if exact_alias_match(record, query) or record_label(record).casefold() == query.casefold():
            matched.append((entity_id, record_label(record) or entity_id))
    matched = list(dict.fromkeys(matched))[:engine.resolver.max_candidate_entities]
    if not matched:
        raise EntityNotFoundError('No exact alias or metadata match; narrow the query')
    if len(matched) == 1:
        return ResolutionResult('resolved', ResolvedEntity(kind, *matched[0]))
    handle = engine.ephemeral.create_resolution([
        ResolutionCandidate('', kind, entity_id, label) for entity_id, label in matched
    ], engine.resolver.resolution_ttl_seconds)
    return ResolutionResult('ambiguous', resolution_id=handle.resolution_id,
                            candidates=handle.candidates, expires_at=handle.expires_at)


def search_concept(engine: Any, course_key: str, concept: str, *,
                   include_textbook: bool = False, caller_scope: str | None = None) -> ContextPackage:
    if not concept.strip() or type(include_textbook) is not bool:
        raise ValueError('concept and boolean include_textbook are required')
    course_record, course = _course(engine, course_key)
    if course.course_key != course_key:
        raise EntityNotFoundError('search_concept requires the exact Course Key')
    candidates: list[tuple[int, str, str]] = []
    warnings: list[Any] = []
    for kind, letter in (('session', 'S'), ('material', 'M')):
        method = getattr(engine.notion_reader, 'list_course_' + kind + 's', None)
        if method is None:
            warnings.append({'code': 'SOURCE_UNAVAILABLE', 'message': kind + ' catalog is unavailable'})
            continue
        for record in method(course_record):
            entity_id = strict_entity_id(field(record, 'ID', 'entity_id'), letter)
            if entity_id is None or engine._course_for_record(record, entity_id) != course:
                continue
            if kind == 'material' and not include_textbook and engine._material_source_class(record) != 'professor_material':
                continue
            metadata = ' '.join(str(field(record, key, default=''))[:4000]
                                for key in ('Name', 'Aliases', 'Topics', 'Content Index'))
            score = lexical_score(concept, metadata) + 100 * exact_alias_match(record, concept)
            candidates.append((score, kind, entity_id))
    candidates.sort(key=lambda item: (-item[0], item[2]))
    limit = engine.resolver.max_candidate_entities
    if len(candidates) > limit:
        warnings.append({'code': 'CANDIDATES_TRUNCATED', 'message': 'Narrow the concept if evidence is missing.'})
    evidence: list[Any] = []
    bindings: list[Any] = []
    for _, kind, entity_id in candidates[:limit]:
        try:
            if kind == 'session':
                collection = engine._collect_session_evidence(entity_id, query=concept, include_provisional=False)
            else:
                collection = engine._collect_material_evidence(entity_id, query=concept)
        except (EntityNotFoundError, SourceUnavailableError):
            warnings.append({'code': 'SOURCE_UNAVAILABLE', 'message': 'A candidate source is unavailable.'})
            continue
        warnings.extend(collection.warnings)
        for item, binding in zip(collection.evidence, collection.bindings, strict=True):
            if (item.source_class not in {'professor_material', 'professor_transcript'}
                    and not (include_textbook and item.source_class == 'supplemental_reference')):
                continue
            if lexical_score(concept, item.content) > 0:
                evidence.append(item)
                bindings.append(binding)
    pairs = sorted(zip(evidence, bindings), key=lambda pair: -lexical_score(concept, pair[0].content))
    chunk_limit = getattr(engine.config.retrieval, 'max_candidate_chunks', 12)
    evidence, bindings = [p[0] for p in pairs[:chunk_limit]], [p[1] for p in pairs[:chunk_limit]]
    evidence, bindings = engine._retain_current_evidence(evidence, bindings, warnings)
    sources, refs = engine._budget_evidence_bindings(evidence, bindings)
    capability = engine.capabilities.issue(refs, caller_scope=caller_scope)
    if not sources:
        warnings.append({'code': 'LOW_CONFIDENCE', 'message': 'No lexical evidence matched; narrow the concept.'})
    return assemble_context_package(
        entity={'type': 'concept', 'id': course_key, 'query': concept},
        scope={'intent': 'CONCEPT', 'course_key': course_key, 'hard_boundary': True,
               'candidate_entities': [item[2] for item in candidates[:limit]],
               'include_textbook': include_textbook},
        sources=sources, warnings=warnings, context_id=capability.context_id, already_bounded=True,
    )


def verify_claim(engine: Any, course_key: str, claim: str, *, entity_hint: str | None,
                 caller_scope: str | None) -> ContextPackage:
    package: ContextPackage
    if entity_hint:
        _, course = _course(engine, course_key)
        for letter, method in (('S', engine.get_session_context), ('M', engine.get_material_context),
                               ('E', engine.get_exam_context), ('A', engine.get_activity_context)):
            entity_id = strict_entity_id(entity_hint, letter)
            if entity_id:
                if letter == 'S':
                    record = engine._get_session(entity_id)
                elif letter == 'M':
                    record = engine._lookup_material(entity_id)
                else:
                    record = getattr(engine, '_get_' + ('exam' if letter == 'E' else 'activity') + '_record')(entity_id)
                if record is None:
                    raise EntityNotFoundError('Verification entity is unavailable')
                actual = record.course if letter in {'E', 'A'} else engine._course_for_record(record, entity_id)
                if actual != course:
                    raise EntityNotFoundError('Verification entity is outside the Course')
                package = method(entity_id, query=claim, caller_scope=caller_scope)
                break
        else:
            raise EntityNotFoundError('Invalid verification entity')
    else:
        package = search_concept(engine, course_key, claim, caller_scope=caller_scope)
    allowed = {'professor_material', 'professor_transcript', 'official_activity', 'official_exam'}
    sources = tuple(item for item in package.sources if item.source_class in allowed)
    original = engine.capabilities.bindings_for(package.context_id) or ()
    refs = [binding for binding in original if any(engine._evidence_matches_binding(item, binding) for item in sources)]
    context = engine.capabilities.issue(refs, caller_scope=caller_scope)
    return replace(package, sources=sources, professor_signals=(), user_context=(),
                   context_id=context.context_id,
                   scope={'intent': 'VERIFY', 'course_key': course_key, 'hard_boundary': True,
                          'parent_scope': package.scope, 'claim': claim,
                          'verdict': 'evidence_only' if sources else 'insufficient_evidence'})


def get_user_context(engine: Any, entity_id: str, query: str, *, caller_scope: str | None) -> ContextPackage:
    if not isinstance(query, str) or not query.strip():
        raise ValueError('Explicit USER query is required')
    if strict_entity_id(entity_id, 'S'):
        record = engine._get_session(entity_id)
        method = engine.notion_reader.get_session_user_annotations
    elif strict_entity_id(entity_id, 'M'):
        record = engine._lookup_material(entity_id)
        method = getattr(engine.notion_reader, 'get_material_user_annotations', None)
    else:
        raise EntityNotFoundError('USER annotations require a Session or Material ID')
    if record is None or not callable(method):
        raise SourceUnavailableError('USER annotations are unavailable')
    course = engine._course_for_record(record, entity_id)
    refs = []
    remaining = engine.budget.max_total_chars
    for annotation in method(entity_id):
        ref = dict(user_reference(annotation))
        content = str(field(annotation, 'content', 'text', default=''))
        if content and lexical_score(query, content) == 0:
            continue
        text = content[:min(engine.budget.max_chars_per_item, remaining)]
        ref.update({'label': 'USER', 'content': text})
        refs.append(ref)
        remaining -= len(text)
        if len(refs) >= engine.budget.max_evidence_items or remaining <= 0:
            break
    capability = engine.capabilities.issue([], caller_scope=caller_scope)
    return assemble_context_package(entity={'id': entity_id, 'type': 'user_note'},
        scope={'intent': 'USER_NOTE', 'course_key': course.course_key, 'hard_boundary': True},
        sources=(), user_context=refs, context_id=capability.context_id,
        warnings=() if refs else ({'code': 'SOURCE_UNAVAILABLE', 'message': 'No USER notes matched.'},))
