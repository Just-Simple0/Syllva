"""Exercise the actual submission composition root over MCP stdio."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_submission_protocol_persists_replays_and_keeps_search_separate(tmp_path):
    script = tmp_path / 'submission_server.py'
    script.write_text('''
import sys
from uls.config.schema import UlsConfig
from uls.runtime import build_study_note_submission_server
from uls.mcp.transports.local import run_local
config = UlsConfig()
config.system.workspace_dir = sys.argv[1]
config.study_notes.enabled = True
server = build_study_note_submission_server(config)
try:
    run_local(server)
finally:
    server.close()
''', encoding='utf-8')
    params = StdioServerParameters(
        command=sys.executable, args=[str(script), str(tmp_path / 'state')],
    )

    async def call(client, name, arguments):
        result = await client.call_tool('uls_submit.' + name, arguments)
        return result.is_error, result.structured_content

    async def scenario():
        request = {'idempotency_key': 'conversation-1', 'session_id': 'COMP319-S05'}
        async with stdio_client(params) as (read, write), ClientSession(
            read, write, read_timeout_seconds=10,
        ) as client:
            initialized = await client.initialize()
            assert initialized.server_info.name == 'uls_submit'
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert len(names) == 5
            assert all(name.startswith('uls_submit.') for name in names)
            error, created = await call(client, 'request_study_note', request)
            assert not error and created['status'] == 'PENDING'
            error, replay = await call(client, 'request_study_note', request)
            assert not error and replay == created
            error, changed = await call(client, 'request_study_note', {
                **request, 'session_id': 'COMP319-S06',
            })
            assert error and changed['error']['code'] == 'INVALID_ARGUMENT'
            error, malformed = await call(client, 'request_study_note', {
                **request, 'selected_materials': 'not-an-array',
            })
            assert error and malformed['error']['code'] == 'INVALID_ARGUMENT'
        # A new process must find the same durable request and cancellation.
        async with stdio_client(params) as (read, write), ClientSession(
            read, write, read_timeout_seconds=10,
        ) as client:
            await client.initialize()
            ref = {'client_request_id': created['client_request_id']}
            error, status = await call(client, 'get_study_note_status', ref)
            assert not error and status['status'] == 'PENDING'
            error, cancelled = await call(client, 'cancel_study_note_request', ref)
            assert not error and cancelled['cancel_requested'] is True
            error, status = await call(client, 'get_study_note_status', ref)
            assert not error and status['cancel_requested'] is True
            assert status['status'] == 'PENDING'  # Worker has not applied the intent.

    asyncio.run(scenario())


def test_real_retrieval_adapter_supplies_transcript_text_without_usage_reads(monkeypatch):
    # Reuse the established real-engine fixture, not a mock of the new protocol.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    from uls.study_notes.evidence import assemble_evidence
    from uls.study_notes.identity import TRANSCRIPT_ONLY
    from uls.study_notes.retrieval_adapter import RetrievalEngineEvidenceAdapter

    engine, notion, _ = _engine()

    def forbidden(*args, **kwargs):
        raise AssertionError('transcript-only must not read Material Usage')

    monkeypatch.setattr(notion, 'get_material_usage', forbidden)
    manifest = assemble_evidence(
        RetrievalEngineEvidenceAdapter(engine, usage_mapping_available=False),
        session_id='COMP319-S05', evidence_mode=TRANSCRIPT_ONLY,
    )
    assert manifest.transcript is not None
    assert manifest.transcript.chunks, 'A hash without text cannot ground a generated note'
    assert all(chunk.get('locator') and chunk.get('content') for chunk in manifest.transcript.chunks)
    assert not manifest.usages


def test_real_retrieval_budget_keeps_partial_and_changes_manifest(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    from uls.study_notes.evidence import assemble_evidence
    from uls.study_notes.identity import TRANSCRIPT_ONLY
    from uls.study_notes.retrieval_adapter import RetrievalEngineEvidenceAdapter

    engine, _, _ = _engine()
    kwargs = {'session_id': 'COMP319-S05', 'evidence_mode': TRANSCRIPT_ONLY}
    full = assemble_evidence(RetrievalEngineEvidenceAdapter(engine), **kwargs)
    bounded = assemble_evidence(RetrievalEngineEvidenceAdapter(engine, max_chunks=1), **kwargs)
    assert full.coverage == 'FULL'
    assert bounded.coverage == 'PARTIAL'
    assert bounded.manifest_hash != full.manifest_hash


@pytest.mark.parametrize('verified', [True, False])
def test_real_retrieval_preserves_usage_scope_physical_identity_and_unread_membership(monkeypatch, verified):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    from uls.study_notes.evidence import assemble_evidence
    from uls.study_notes.identity import CONFIRMED_LECTURE, PROVISIONAL_SELECTED
    from uls.study_notes.retrieval_adapter import RetrievalEngineEvidenceAdapter

    physical_id = '10000000-0000-0000-0000-000000000042'
    usage = {'id': physical_id, 'ID': 'USAGE-03', 'Session': 'COMP319-S05',
             'Material ID': 'COMP319-M03', 'Role': 'Supporting', 'Verified': verified,
             'Start Page': 2, 'End Page': 2}
    engine, _, drive = _engine(material_usage={'COMP319-S05': [usage]})
    adapter = RetrievalEngineEvidenceAdapter(engine)
    kwargs = {'session_id': 'COMP319-S05',
              'evidence_mode': CONFIRMED_LECTURE if verified else PROVISIONAL_SELECTED,
              'selected_materials': None if verified else ['COMP319-M03']}
    manifest = assemble_evidence(adapter, **kwargs)
    assert len(manifest.usages) == 1
    item = manifest.usages[0]
    assert item.provider_page_id == physical_id
    assert item.usage_role == 'Supporting' and item.verified is verified
    assert {chunk['locator'] for chunk in item.chunks} == {'COMP319-M03:p2'}
    drive.derived.pop('material-m03')
    unread = assemble_evidence(adapter, **kwargs)
    assert unread.coverage == 'PARTIAL'
    assert len(unread.usages) == 1 and unread.usages[0].read is False
    assert unread.manifest_hash != manifest.manifest_hash


def test_registered_note_destination_rejects_folder_moved_outside_course(monkeypatch):
    from dataclasses import replace

    from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, InMemoryDriveWorker
    from uls.intake.study_note_composition import StudyNoteSessionBindings
    from uls.study_notes.handler import NoteAuthorityChanged

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    engine, graph, source = _engine()
    binding = next(item for item in source.source_bindings.records if item.entity_id == 'COMP319-S05')
    provenance = SimpleNamespace(binding=lambda _: binding,
                                 lookup_source_binding=lambda *_: binding)
    privacy = {'owned_by_me': True, 'owner_only': True, 'is_publicly_shared': False,
               'permission_count': 1, 'permission_types': ('user',),
               'permission_roles': (('user', 'owner'),), 'can_edit': True, 'can_move': True}
    files = [DriveMetadata('transcript-05', 'transcript.md', 'text/markdown', parents=('derived',), **privacy),
             DriveMetadata('derived', 'derived', DRIVE_FOLDER_MIME, parents=('session',), **privacy),
             DriveMetadata('session', 'session', DRIVE_FOLDER_MIME, parents=('recordings',), **privacy)]
    drive = InMemoryDriveWorker(files)
    resolver = StudyNoteSessionBindings(
        graph=graph, engine=engine, provenance=provenance, drive=drive,
        workspaces=[SimpleNamespace(course_key='2026-1_COMP319-002', recordings_folder_id='recordings')],
    )
    assert resolver.derived_folder_id('COMP319-S05') == 'derived'
    drive.files['session'] = replace(files[2], parents=('other-course',))
    with pytest.raises(NoteAuthorityChanged, match='outside configured recordings'):
        resolver.derived_folder_id('COMP319-S05')


def test_c6_factory_activates_without_c5_queue(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    from test_intake_worker_preview import _system

    from uls.intake.study_note_composition import install_study_notes

    with _system(tmp_path) as system:
        worker = system['worker']
        config = worker.config
        config.study_notes.enabled = True
        assert not config.notion.semester_workspaces[0].automation_queue_data_source_id
        install_study_notes(worker, config, worker.state, worker.notion, object(), block_port=object())
        assert len(worker.request_coordinators) == 1
        assert worker.request_coordinators[0].handlers.keys() == {'study_notes'}
        result = worker.run_once(sync=False, process=True)
        assert result['status'] == 'ok' and result['failed'] == 0
        assert worker.extension_resources
        for resource in worker.extension_resources:
            resource.close()
        worker.extension_resources.clear()
