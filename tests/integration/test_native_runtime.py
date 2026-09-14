"""Native worker → provider adapters → RO state → engine, with in-memory HTTP endpoints."""
from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest

from uls.adapters.drive.binding import ValidatedSourceBindingResolver
from uls.adapters.drive.google import GoogleDriveReader
from uls.adapters.notion.api import NotionAPIReader
from uls.config.schema import CourseCfg, NotionCfg, UlsConfig
from uls.domain.errors import LocatorStaleError, SourcePartialError, SourceUnavailableError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.engine import RetrievalEngine
from uls.state.reader import ReadOnlyState
from uls.state.sqlite import SQLiteStateStore
from uls.worker import NativeWorker

pytestmark = pytest.mark.integration
COURSE = '2026-1_COMP319-002'


def property_value(kind, value):
    if kind in {'title', 'rich_text'}:
        value = [{'text': {'content': value}}]
    return {'type': kind, kind: value}


class NotionService:
    def __init__(self):
        self.rows = {'course-page': {'id': 'course-page', 'parent': {'data_source_id': 'courses-ds'},
                     'archived': False, 'properties': {
            'Name': property_value('title', 'Algorithms'),
            'Course Key': property_value('rich_text', COURSE),
            'Code': property_value('rich_text', 'COMP319'),
            'Section': property_value('rich_text', '002'),
            'Semester': property_value('rich_text', '2026-1'),
            'Aliases': property_value('rich_text', 'Algorithms | 알고리즘'),
        }}}
        self.writes = []
        self.databases = SimpleNamespace(retrieve=lambda database_id: {'data_sources': [{'id': database_id.replace('-db', '-ds')}]})
        self.data_sources = SimpleNamespace(query=self.query)
        self.pages = SimpleNamespace(retrieve=lambda page_id: copy.deepcopy(self.rows[page_id]),
                                     create=self.create, update=self.update)
        self.blocks = SimpleNamespace(children=SimpleNamespace(list=lambda **_: {'results': [], 'has_more': False}))

    def query(self, data_source_id, **kwargs):
        rows = [r for r in self.rows.values() if r['parent']['data_source_id'] == data_source_id]
        filter_value = kwargs.get('filter')
        if filter_value:
            prop = filter_value['property']
            if 'rich_text' in filter_value:
                wanted = filter_value['rich_text']['equals']
                rows = [r for r in rows if r['properties'][prop]['rich_text'][0]['text']['content'] == wanted]
            else:
                wanted = filter_value['relation']['contains']
                rows = [r for r in rows if {'id': wanted} in r['properties'][prop]['relation']]
        return {'results': copy.deepcopy(rows), 'has_more': False}

    def create(self, parent, properties):
        page_id = 'page-' + str(len(self.rows))
        self.rows[page_id] = {'id': page_id, 'parent': parent, 'archived': False, 'properties': {}}
        return self.update(page_id, properties)

    def update(self, page_id, properties):
        self.writes.append(copy.deepcopy(properties))
        self.rows[page_id]['properties'].update({k: {'type': next(iter(v)), **v} for k, v in properties.items()})
        return copy.deepcopy(self.rows[page_id])


class DriveService:
    def __init__(self, derived_metadata_overrides=None):
        self.contents = {'raw1': b'[00:00:00] CPU scheduling starts here.\n[00:00:10] Round robin scheduling.\n'}
        self.uploads = []
        self.derived_metadata_overrides = derived_metadata_overrides or {}

    def files(self):
        return self

    def get(self, fileId, **kwargs):
        if fileId == 'derived-folder':
            data = {
                'id': fileId, 'name': 'Derived', 'mimeType': 'application/vnd.google-apps.folder',
                'trashed': False, 'ownedByMe': True,
                'permissions': [{'type': 'user', 'role': 'owner'}],
                'capabilities': {'canEdit': True, 'canMoveItemWithinDrive': True},
            }
            data.update(self.derived_metadata_overrides)
        else:
            data = {'id': fileId, 'name': fileId, 'size': len(self.contents[fileId]), 'mimeType': 'text/plain', 'trashed': False}
        return SimpleNamespace(execute=lambda: data)

    def get_media(self, **kwargs):
        raise AssertionError('tests replace only HTTP media transfer, not worker or retrieval behavior')

    def create(self, body, media_body, **kwargs):
        def execute():
            file_id = 'derived-' + str(len(self.uploads) + 1)
            self.contents[file_id] = media_body.getbytes(0, media_body.size())
            self.uploads.append(copy.deepcopy(body))
            return {'id': file_id}
        return SimpleNamespace(execute=execute)


def config():
    cfg = UlsConfig(courses=[CourseCfg(course_key=COURSE, code='COMP319', section='002', semester='2026-1')])
    cfg.notion = NotionCfg(**{name: name.removesuffix('_db_id') + '-db' for name in NotionCfg.__dataclass_fields__})
    return cfg


def test_native_worker_publication_and_read_only_retrieval_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.contents[file_id])
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    path = tmp_path / 'state.sqlite3'
    cfg.system.workspace_dir = str(tmp_path)
    with SQLiteStateStore(path) as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        result = worker.runner.run_once()
        assert result['processed'] == 1 and result['failed'] == 0, result
        assert worker.runner.run_once()['processed'] == 0
        assert len(drive.uploads) == 1
        assert all('Verified' not in p and 'Scope Confirmed' not in p and 'Decision' not in p for p in notion.writes)
        source = state.get_source_file('raw1')
        assert source.current_hash == 'sha256:' + hashlib.sha256(drive.contents['raw1']).hexdigest()
        readonly = ReadOnlyState(path)
        reader = GoogleDriveReader(drive, readonly)
        engine = RetrievalEngine(NotionAPIReader(notion, cfg.notion), reader, readonly,
                                 MemoryEphemeralStore(), cfg,
                                 source_binding_resolver=ValidatedSourceBindingResolver(readonly))
        context = engine.get_session_context(source.canonical_entity_id)
        assert context.sources and 'CPU' in context.sources[0].content
        locator = str(context.sources[0].locator)
        assert engine.get_source_chunk(context.context_id, locator).content
        drive.contents['raw1'] += b'Edited after capability issuance.'
        with pytest.raises(LocatorStaleError):
            engine.get_source_chunk(context.context_id, locator)
        # A real new source version refreshes the derivative and keeps the
        # allocator-backed Session identity; a second unchanged poll is a no-op.
        updated = worker.runner.run_once()
        assert updated['processed'] == 1 and updated['failed'] == 0
        assert state.get_source_file('raw1').canonical_entity_id == source.canonical_entity_id
        assert worker.runner.run_once()['processed'] == 0
        assert len(drive.uploads) == 2
        # Explicit reprocessing of the same bytes uses the existing job key.
        latest = state.list_jobs(entity_id=source.canonical_entity_id, limit=1)[0]
        assert latest.source_hash == state.get_source_file('raw1').current_hash
        history = [tuple(row) for row in state.connection.execute('SELECT * FROM processing_records ORDER BY rowid')]
        assert state.acquire_local_worker_lock()
        state.request_reprocess(latest.id)
        state.release_local_worker_lock()
        rerun = worker.runner.run_once(sync=False)
        assert rerun['processed'] == 1 and rerun['failed'] == 0, rerun
        after = [tuple(row) for row in state.connection.execute('SELECT * FROM processing_records ORDER BY rowid')]
        assert after[:len(history)] == history
        assert len(after) == len(history) + 1
        refreshed = engine.get_session_context(source.canonical_entity_id)
        assert refreshed.sources and 'CPU' in refreshed.sources[0].content
        # A completed enrichment record is not a canonical derivative binding.
        from uls.orchestration.jobs import derive_job_key
        digest = state.get_source_file('raw1').current_hash
        ai_job = state.create_job(derive_job_key('raw1', digest, 'enrich_session', '1.2.0'),
                                  operation='enrich_session', stage='enrichment',
                                  source_file_id='raw1', source_hash=digest,
                                  target_entity_id=source.canonical_entity_id,
                                  course_key=COURSE, processor_version='1.2.0')
        state.claim_job(ai_job.id)
        state.create_processing_record(job_id=ai_job.id, operation='enrich_session',
                                        processor_version='1.2.0', input_hash=digest,
                                        output_ref_json={'region': 'AI', 'entity_id': source.canonical_entity_id})
        state.complete_job(ai_job.id)
        assert engine.get_session_context(source.canonical_entity_id).sources
        # The local reprocess command selects ingestion, even when enrichment
        # is the newest job for this entity.
        import uls.cli.main as cli
        monkeypatch.setattr(cli, '_config', lambda _: cfg)
        queued = cli.dispatch(SimpleNamespace(command='reprocess', config=None,
                                              entity_id=source.canonical_entity_id))
        assert state.get_job(queued['job_id']).operation == 'TRANSCRIPT_INGEST'


def test_notion_reader_rejects_wrong_database_archival_and_truncated_relations():
    service, cfg = NotionService(), config()
    reader = NotionAPIReader(service, cfg.notion)
    assert reader.get_course_by_alias('ALGORITHMS')['id'] == 'course-page'
    service.rows['course-page']['archived'] = True
    with pytest.raises(SourceUnavailableError):
        reader.get_course_by_relation_id('course-page')
    service.rows['course-page']['archived'] = False
    service.rows['course-page']['properties']['Other'] = {'type': 'relation', 'relation': [], 'has_more': True}
    with pytest.raises(SourcePartialError):
        reader.get_course_by_relation_id('course-page')


def test_state_reader_never_manufactures_a_binding_from_an_arbitrary_pointer(tmp_path):
    path = tmp_path / 'state.db'
    with SQLiteStateStore(path):
        reader = ReadOnlyState(path)
        assert reader.health()
        with pytest.raises(SourceUnavailableError):
            reader.lookup_source_binding('COMP319-S01', 'https://drive.google.com/file/d/untrusted/view')


def test_real_drive_sdk_downloader_checks_complete_size():
    pytest.importorskip('googleapiclient')
    from googleapiclient.http import HttpMockSequence, HttpRequest
    service = DriveService()
    payload = service.contents['raw1']
    def media(**kwargs):
        assert kwargs['fileId'] == 'raw1' and kwargs['supportsAllDrives']
        return HttpRequest(HttpMockSequence([({'status': '200', 'content-length': str(len(payload))}, payload)]),
                           lambda *args: None, uri='https://www.googleapis.com/drive/v3/files/raw1?alt=media')
    service.get_media = media
    reader = GoogleDriveReader(service, None)
    assert reader.download('raw1') == payload
    payload += b'changed after metadata read'
    with pytest.raises(SourceUnavailableError):
        reader.download('raw1')


def test_worker_preserves_user_replaced_transcript_pointer(tmp_path, monkeypatch):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.contents[file_id])
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        assert worker.runner.run_once()['processed'] == 1
        session = next(row for row in notion.rows.values() if row['parent'].get('data_source_id') == 'sessions-ds')
        session['properties']['Normalized Transcript']['url'] = 'https://drive.google.com/file/d/user-owned/view'
        previous_writes = copy.deepcopy(notion.writes)
        drive.contents['raw1'] += b'next version'
        result = worker.runner.run_once()
        assert result['failed'] == 1
        assert notion.writes == previous_writes
        assert session['properties']['Normalized Transcript']['url'].endswith('/user-owned/view')


@pytest.mark.parametrize(
    'overrides',
    [
        pytest.param({'driveId': 'shared-drive-1'}, id='shared_drive'),
        pytest.param({'ownedByMe': False}, id='not_owned_by_me'),
        pytest.param(
            {'permissions': [{'type': 'user', 'role': 'owner'}, {'type': 'user', 'role': 'writer'}]},
            id='not_solely_owned',
        ),
        pytest.param(
            {'permissions': [{'type': 'anyone', 'role': 'reader'}]},
            id='publicly_shared',
        ),
        pytest.param({'capabilities': {'canEdit': False, 'canMoveItemWithinDrive': True}}, id='cannot_edit'),
        pytest.param({'permissions': None}, id='missing_permission_readback'),
        pytest.param({'trashed': None}, id='trashed_missing_readback_null'),
        pytest.param({'trashed': 'true'}, id='trashed_malformed_string'),
        pytest.param({'trashed': 1}, id='trashed_malformed_integer'),
    ],
)
def test_native_worker_rejects_unsafe_derived_folder(tmp_path, monkeypatch, overrides):
    """The legacy transcript writer must reject the same ownership/sharing
    problems the semester intake layout validator already rejects, instead of
    trusting a folder ID solely because it exists and is untrashed."""
    pytest.importorskip('googleapiclient')
    notion = NotionService()
    drive = DriveService(derived_metadata_overrides=overrides)
    cfg = config()
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.contents[file_id])
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        result = worker.runner.run_once()
        assert result['processed'] == 0 and result['failed'] == 1
        assert drive.uploads == []
        assert notion.writes == []
