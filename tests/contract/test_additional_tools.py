from __future__ import annotations

import pytest

from uls.domain.errors import EntityNotFoundError, LocatorNotAllowedError
from uls.mcp.server import ReadOnlyMCP

pytestmark = pytest.mark.contract


def test_material_and_activity_resolution_use_existing_domain_identities():
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    assert engine.resolve_entity('COMP319-M03', entity_type='material').entity_id == 'COMP319-M03'
    assert engine.resolve_entity('COMP319-A01', entity_type='activity').entity_id == 'COMP319-A01'
    with pytest.raises(EntityNotFoundError):
        engine.resolve_entity('COMP319-M03', course_hint='2027-1_COMP319-002', entity_type='material')


def test_concept_is_bounded_course_evidence_and_can_chain():
    from test_get_activity_context import _engine
    engine, notion, _ = _engine()
    notion.list_course_materials = lambda course: [notion.get_material('COMP319-M03')]
    package = engine.search_concept('2026-1_COMP319-002', 'Professor', caller_scope='owner')
    assert package.sources
    assert all(source.source_class in {'professor_material', 'professor_transcript'} for source in package.sources)
    assert sum(len(item.content) for item in package.sources) <= engine.budget.max_total_chars
    item = package.sources[0]
    assert engine.get_source_chunk(package.context_id, str(item.locator), caller_scope='owner').content
    with pytest.raises(LocatorNotAllowedError):
        engine.get_source_chunk(package.context_id, str(item.locator), caller_scope='different')


def test_verify_does_not_include_ai_signals_or_user_notes():
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    package = engine.verify_claim('2026-1_COMP319-002', 'Professor', entity_hint='COMP319-S05')
    assert package.sources
    assert not package.professor_signals and not package.user_context
    assert package.scope['verdict'] == 'evidence_only'
    assert all(item.source_class not in {'ai', 'ai_enrichment', 'user_source'} for item in package.sources)


def test_explicit_user_query_returns_user_body_without_factual_promotion():
    from test_get_activity_context import _engine
    engine, notion, _ = _engine()
    notion.get_session_user_annotations = lambda entity: [{'ID': entity, 'text': 'My CPU scheduling notes'}]
    package = engine.get_user_context('COMP319-S05', 'CPU')
    assert not package.sources
    assert package.user_context[0]['label'] == 'USER'
    assert package.user_context[0]['content'] == 'My CPU scheduling notes'
    assert not engine.capabilities.bindings_for(package.context_id)


def test_every_advertised_tool_has_a_real_engine_method():
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    for tool in ReadOnlyMCP(engine).list_tools():
        method = tool['name'].removeprefix('uls.')
        assert method == 'ping' or callable(getattr(engine, method))
