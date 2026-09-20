"""Real state, retrieval, staging and block bridge through the shared coordinator."""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from uls.adapters.drive.worker import DRIVE_FOLDER_MIME, DriveMetadata, InMemoryDriveWorker
from uls.intake.coordinator import RequestCoordinator
from uls.state.sqlite import SQLiteStateStore
from uls.study_notes.config import StudyNoteConfig
from uls.study_notes.core import StudyNoteSubmissionCore
from uls.study_notes.drive import StudyNoteDriveStaging
from uls.study_notes.evidence import MaterialUsageEvidence
from uls.study_notes.handler import NoteAuthorityChanged, StudyNoteHandler
from uls.study_notes.identity import PROVISIONAL_SELECTED, TRANSCRIPT_ONLY
from uls.study_notes.notion import StudyNoteAIBlockBridge
from uls.study_notes.retrieval_adapter import RetrievalEngineEvidenceAdapter
from uls.study_notes.store import StudyNoteStore


class Blocks:
    def __init__(self):
        self.blocks = {}
        self.appends = 0

    def append_block_children(self, parent_block_id, children):
        self.appends += 1
        result = []
        for child in children:
            block = {**deepcopy(child), 'id': str(uuid4()), 'archived': False,
                     'in_trash': False, 'parent': {'type': 'page_id', 'page_id': parent_block_id}}
            self.blocks[block['id']] = block
            result.append(block)
        return {'object': 'list', 'results': result, 'has_more': False, 'next_cursor': None}

    def retrieve_block(self, block_id):
        return deepcopy(self.blocks[block_id])

    def update_block(self, block_id, **fields):
        assert set(fields) == {'code'}
        self.blocks[block_id].update(deepcopy(fields))
        return self.retrieve_block(block_id)

    def list_block_children(self, block_id, *, start_cursor=None):
        return {'results': [deepcopy(block) for block in self.blocks.values()
                            if block['parent']['page_id'] == block_id],
                'has_more': False, 'next_cursor': None}


class InvalidSelectionEngine:
    def get_transcript_evidence(self, session_id):
        return None

    def usage_mapping_available(self, session_id):
        return True

    def list_verified_lecture_usages(self, session_id):
        return ()

    def get_selected_usage(self, session_id, material_id):
        return None


class DriftingSelectionEngine(InvalidSelectionEngine):
    def __init__(self):
        self.valid = True

    def get_selected_usage(self, session_id, material_id):
        if not self.valid:
            return None
        return MaterialUsageEvidence(
            material_app_id=material_id, usage_app_id='USAGE-03',
            provider_page_id='20000000-0000-0000-0000-000000000003',
            usage_role='Supporting', verified=False, material_type='Lecture Slides',
            source_hash='source-hash', source_version=1,
            chunks=({'locator': material_id + ':p1', 'content': 'grounded evidence'},),
        )


def test_invalid_provisional_selection_fails_request_without_poisoning_worker(tmp_path):
    physical = '10000000-0000-0000-0000-000000000001'
    clock = lambda: datetime.now(UTC).isoformat()
    state = SQLiteStateStore(tmp_path / 'state.db')
    store = StudyNoteStore(tmp_path / 'notes.db', clock=clock)
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        handler = StudyNoteHandler(
            state=state, store=store, engine=InvalidSelectionEngine(),
            drive_staging=object(), notion_bridge=object(), workspace='semester',
            clock=clock, local_source_id='local', session_course_key=lambda _: 'course',
            session_provider_page_id=lambda _: physical, session_derived_folder_id=lambda _: 'derived',
            template_version='study-note.v1', generator_config_version='mcp-client-draft.v1',
        )
        coordinator = RequestCoordinator(
            workspace='semester', source_ids=['local'], handlers={'notes': handler},
            list_records=lambda _: [{**row, 'id': row['client_request_id']}
                                     for row in store.list_pending_client_requests(limit=10_001)],
        )
        request = core.request_study_note(
            idempotency_key='invalid-selection', caller_context='client',
            session_id='COMP319-S05', evidence_mode=PROVISIONAL_SELECTED,
            selected_materials=['NOT-IN-SESSION'],
        )
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] == 'ok'
        status = core.get_study_note_status(request['client_request_id'], caller_context='client')
        assert status['status'] == 'FAILED'
        head = state.get_study_note_head('notion', physical)
        assert head is not None and not head.active
    finally:
        store.close()
        state.close()


def test_selection_drift_immediately_before_dispatch_retires_stale_without_provider_retry(
    tmp_path, monkeypatch,
):
    privacy = {'owned_by_me': True, 'owner_only': True, 'is_publicly_shared': False,
               'permission_count': 1, 'permission_types': ('user',),
               'permission_roles': (('user', 'owner'),), 'can_edit': True, 'can_move': True}
    drive = InMemoryDriveWorker([DriveMetadata('derived', 'derived', DRIVE_FOLDER_MIME, **privacy)])
    blocks = Blocks()
    engine = DriftingSelectionEngine()
    physical = '10000000-0000-0000-0000-000000000001'
    clock = lambda: datetime.now(UTC).isoformat()
    state = SQLiteStateStore(tmp_path / 'state.db')
    store = StudyNoteStore(tmp_path / 'notes.db', clock=clock)
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        handler = StudyNoteHandler(
            state=state, store=store, engine=engine,
            drive_staging=StudyNoteDriveStaging(drive, store), notion_bridge=StudyNoteAIBlockBridge(blocks, store),
            workspace='semester', clock=clock, local_source_id='local',
            session_course_key=lambda _: 'course', session_provider_page_id=lambda _: physical,
            session_derived_folder_id=lambda _: 'derived', template_version='study-note.v1',
            generator_config_version='mcp-client-draft.v1',
        )
        coordinator = RequestCoordinator(
            workspace='semester', source_ids=['local'], handlers={'notes': handler},
            list_records=lambda _: [{**row, 'id': row['client_request_id']}
                                     for row in store.list_pending_client_requests(limit=10_001)],
        )

        def tick():
            result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
            assert result['status'] == 'ok', result

        request = core.request_study_note(
            idempotency_key='drift', caller_context='client', session_id='COMP319-S05',
            evidence_mode=PROVISIONAL_SELECTED, selected_materials=['COMP319-M03'],
        )
        ref = request['client_request_id']
        tick()
        status = core.get_study_note_status(ref, caller_context='client')
        locator = core.get_study_note_evidence(ref, caller_context='client')['context']['locators'][0]
        body = ('# 학습 목표\nSOURCE ' + locator + '\n# 핵심 개념\nAI 설명\n'
                '# 예제\nSOURCE ' + locator + '\n# 오개념\n범위를 혼동하지 않기\n'
                '# 연습문제\n<details><summary>풀이</summary>AI 풀이</details>')
        core.submit_study_note_draft(
            grant_id=status['grant_id'], draft_text=body, caller_context='client',
        )
        original_dispatch = handler._dispatch

        def drift_then_dispatch(draft):
            engine.valid = False
            original_dispatch(draft)

        monkeypatch.setattr(handler, '_dispatch', drift_then_dispatch)
        tick()
        retired = core.get_study_note_status(ref, caller_context='client')
        assert retired['status'] == 'STALE'
        attempt = state.get_note_attempt(status['note_key'], status['attempt_no'])
        assert attempt is not None and attempt.state == 'STALE' and attempt.next_retry_at is None
        assert set(drive.files) == {'derived'} and blocks.appends == 0
    finally:
        store.close()
        state.close()


def test_prepublication_authority_loss_without_new_head_retires_stale(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    engine, _, _ = _engine()
    physical = {'value': '10000000-0000-0000-0000-000000000001'}
    clock = lambda: datetime.now(UTC).isoformat()
    state = SQLiteStateStore(tmp_path / 'state.db')
    store = StudyNoteStore(tmp_path / 'notes.db', clock=clock)
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        handler = StudyNoteHandler(
            state=state, store=store, engine=RetrievalEngineEvidenceAdapter(engine),
            drive_staging=object(), notion_bridge=object(), workspace='semester',
            clock=clock, local_source_id='local', session_course_key=lambda _: '2026-1_COMP319-002',
            session_provider_page_id=lambda _: physical['value'],
            session_derived_folder_id=lambda _: 'derived', template_version='study-note.v1',
            generator_config_version='mcp-client-draft.v1',
        )
        coordinator = RequestCoordinator(
            workspace='semester', source_ids=['local'], handlers={'notes': handler},
            list_records=lambda _: [{**row, 'id': row['client_request_id']}
                                     for row in store.list_pending_client_requests(limit=10_001)],
        )
        request = core.request_study_note(
            idempotency_key='physical-drift', caller_context='client', session_id='COMP319-S05',
            evidence_mode=TRANSCRIPT_ONLY,
        )
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] == 'ok'
        prepared = core.get_study_note_status(request['client_request_id'], caller_context='client')
        assert prepared.get('grant_id')

        physical['value'] = '10000000-0000-0000-0000-000000000002'
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] == 'ok'
        retired = core.get_study_note_status(request['client_request_id'], caller_context='client')
        assert retired['status'] == 'STALE'
        attempt = state.get_note_attempt(prepared['note_key'], prepared['attempt_no'])
        assert attempt is not None and attempt.state == 'STALE'
    finally:
        store.close()
        state.close()


def test_destination_authority_drift_does_not_consume_provider_retry(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    engine, _, _ = _engine()
    privacy = {'owned_by_me': True, 'owner_only': True, 'is_publicly_shared': False,
               'permission_count': 1, 'permission_types': ('user',),
               'permission_roles': (('user', 'owner'),), 'can_edit': True, 'can_move': True}
    drive = InMemoryDriveWorker([DriveMetadata('derived', 'derived', DRIVE_FOLDER_MIME, **privacy)])
    blocks = Blocks()
    drifted = {'value': False}
    physical = '10000000-0000-0000-0000-000000000001'
    clock = lambda: datetime.now(UTC).isoformat()
    state = SQLiteStateStore(tmp_path / 'state.db')
    store = StudyNoteStore(tmp_path / 'notes.db', clock=clock)

    def destination(_: str) -> str:
        if drifted['value']:
            raise NoteAuthorityChanged('Session folder is outside configured recordings')
        return 'derived'

    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        handler = StudyNoteHandler(
            state=state, store=store, engine=RetrievalEngineEvidenceAdapter(engine),
            drive_staging=StudyNoteDriveStaging(drive, store), notion_bridge=StudyNoteAIBlockBridge(blocks, store),
            workspace='semester', clock=clock, local_source_id='local',
            session_course_key=lambda _: '2026-1_COMP319-002', session_provider_page_id=lambda _: physical,
            session_derived_folder_id=destination, template_version='study-note.v1',
            generator_config_version='mcp-client-draft.v1',
        )
        coordinator = RequestCoordinator(
            workspace='semester', source_ids=['local'], handlers={'notes': handler},
            list_records=lambda _: [{**row, 'id': row['client_request_id']}
                                     for row in store.list_pending_client_requests(limit=10_001)],
        )
        request = core.request_study_note(
            idempotency_key='destination-drift', caller_context='client', session_id='COMP319-S05',
            evidence_mode=TRANSCRIPT_ONLY,
        )
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] == 'ok'
        prepared = core.get_study_note_status(request['client_request_id'], caller_context='client')
        evidence = core.get_study_note_evidence(request['client_request_id'], caller_context='client')
        locator = evidence['context']['locators'][0]
        body = ('# 학습 목표\nSOURCE ' + locator + '\n# 핵심 개념\nAI 설명\n'
                '# 예제\nSOURCE ' + locator + '\n# 오개념\n범위를 혼동하지 않기\n'
                '# 연습문제\n<details><summary>풀이</summary>AI 풀이</details>')
        core.submit_study_note_draft(
            grant_id=prepared['grant_id'], draft_text=body, caller_context='client',
        )

        drifted['value'] = True
        result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
        assert result['status'] == 'ok'
        retired = core.get_study_note_status(request['client_request_id'], caller_context='client')
        assert retired['status'] == 'STALE'
        attempt = state.get_note_attempt(prepared['note_key'], prepared['attempt_no'])
        assert attempt is not None and attempt.state == 'STALE'
        assert attempt.retry_count == 0 and attempt.next_retry_at is None
        assert set(drive.files) == {'derived'} and blocks.appends == 0
    finally:
        store.close()
        state.close()


@pytest.mark.parametrize('cancellation', ['none', 'before_claim', 'before_write', 'newer_request'])
def test_conversation_request_evidence_draft_publish_and_replay(tmp_path, monkeypatch, cancellation):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'contract'))
    from test_get_activity_context import _engine

    engine, _, _ = _engine()
    privacy = {'owned_by_me': True, 'owner_only': True, 'is_publicly_shared': False,
               'permission_count': 1, 'permission_types': ('user',),
               'permission_roles': (('user', 'owner'),), 'can_edit': True, 'can_move': True}
    drive = InMemoryDriveWorker([DriveMetadata('derived', 'derived', DRIVE_FOLDER_MIME, **privacy)])
    blocks = Blocks()
    physical = '10000000-0000-0000-0000-000000000001'
    clock = lambda: datetime.now(UTC).isoformat()
    state = SQLiteStateStore(tmp_path / 'state.db')
    store = StudyNoteStore(tmp_path / 'notes.db', clock=clock)
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        handler = StudyNoteHandler(
            state=state, store=store, engine=RetrievalEngineEvidenceAdapter(engine),
            drive_staging=StudyNoteDriveStaging(drive, store), notion_bridge=StudyNoteAIBlockBridge(blocks, store),
            workspace='semester', clock=clock, local_source_id='local',
            session_course_key=lambda _: '2026-1_COMP319-002', session_provider_page_id=lambda _: physical,
            session_derived_folder_id=lambda _: 'derived', template_version='study-note.v1',
            generator_config_version='mcp-client-draft.v1',
        )
        coordinator = RequestCoordinator(workspace='semester', source_ids=['local'], handlers={'notes': handler},
            list_records=lambda _: [{**row, 'id': row['client_request_id']}
                                     for row in store.list_pending_client_requests(limit=10_001)])

        def tick():
            assert state.acquire_local_worker_lock()
            try:
                result = coordinator.publish(coordinator.receive(coordinator.snapshot()))
                assert result['status'] == 'ok', result
            finally:
                state.release_local_worker_lock()

        request = core.request_study_note(idempotency_key='one', caller_context='client',
            session_id='COMP319-S05', evidence_mode=TRANSCRIPT_ONLY)
        ref = request['client_request_id']
        if cancellation == 'before_claim':
            core.cancel_study_note_request(ref, caller_context='client')
            tick()
            assert core.get_study_note_status(ref, caller_context='client')['status'] == 'CANCELLED'
            assert state.get_study_note_head('notion', physical) is None
            assert blocks.appends == 0
            return
        tick()
        status = core.get_study_note_status(ref, caller_context='client')
        assert status.get('grant_id'), status
        evidence = core.get_study_note_evidence(ref, caller_context='client')
        locator = evidence['context']['locators'][0]
        body = ('# 학습 목표\nSOURCE ' + locator + '\n# 핵심 개념\nAI 설명\n'
                '# 예제\nSOURCE ' + locator + '\n# 오개념\n범위를 혼동하지 않기\n'
                '# 연습문제\n<details><summary>풀이</summary>AI 풀이</details>')
        submitted = core.submit_study_note_draft(grant_id=status['grant_id'], draft_text=body, caller_context='client')
        assert submitted['created']
        newer = {}
        if cancellation in {'before_write', 'newer_request'}:
            search = drive.search_marker

            def cancel_during_search(marker):
                found = search(marker)
                if cancellation == 'before_write':
                    core.cancel_study_note_request(ref, caller_context='client')
                else:
                    newer.update(core.request_study_note(idempotency_key='newer', caller_context='client',
                        session_id='COMP319-S05', evidence_mode=TRANSCRIPT_ONLY, learner_request='새 요청'))
                return found

            monkeypatch.setattr(drive, 'search_marker', cancel_during_search)
        tick()
        saved = core.get_study_note_status(ref, caller_context='client')
        if cancellation == 'newer_request':
            assert set(drive.files) == {'derived'} and blocks.appends == 0
            tick()
            assert core.get_study_note_status(ref, caller_context='client')['status'] == 'SUPERSEDED'
            latest = core.get_study_note_status(newer['client_request_id'], caller_context='client')
            assert latest.get('grant_id') and latest['grant_id'] != status['grant_id']
            return
        if cancellation == 'before_write':
            assert saved['status'] == 'CANCELLED', saved
            assert set(drive.files) == {'derived'} and blocks.appends == 0
            return
        assert saved['status'] == 'READY', saved
        assert saved['artifact_link'].startswith('https://drive.google.com/file/d/')
        head = state.get_study_note_head('notion', physical)
        assert head is not None and head.current_note_key == status['note_key']
        assert state.get_study_note_head('notion', 'COMP319-S05') is None
        file_ids = set(drive.files)
        tick()
        assert set(drive.files) == file_ids and blocks.appends == 1
        assert core.submit_study_note_draft(grant_id=status['grant_id'], draft_text=body,
                                           caller_context='client')['created'] is False
    finally:
        store.close()
        state.close()
