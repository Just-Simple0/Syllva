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
    def __init__(self, derived_metadata_overrides=None, generated_metadata_overrides=None):
        self.contents = {'raw1': b'[00:00:00] CPU scheduling starts here.\n[00:00:10] Round robin scheduling.\n'}
        self.uploads = []
        self.derived_metadata_overrides = derived_metadata_overrides or {}
        self.generated_metadata_overrides = generated_metadata_overrides or {}
        self.generated_metadata = {}
        self.metadata_reads = []
        self.content_reads = []
        self.generated_metadata_read_counts = {}
        self.generated_content_read_counts = {}
        self.generated_metadata_read_overrides = {}
        self.generated_metadata_read_failures = set()
        self.generated_content_read_failures = set()
        self.generated_content_read_mismatches = set()
        self.create_fields = []
        self.provider_mutations = []
        self.failure_mode = None

    def files(self):
        return self

    def get(self, fileId, **kwargs):
        def execute():
            self.metadata_reads.append(fileId)
            if fileId == 'derived-folder':
                data = {
                    'id': fileId, 'name': 'Derived', 'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [], 'trashed': False, 'ownedByMe': True,
                    'permissions': [{'type': 'user', 'role': 'owner'}],
                    'capabilities': {'canEdit': True, 'canMoveItemWithinDrive': True},
                }
                data.update(self.derived_metadata_overrides)
            elif fileId in self.generated_metadata:
                read_count = self.generated_metadata_read_counts.get(fileId, 0) + 1
                self.generated_metadata_read_counts[fileId] = read_count
                if read_count in self.generated_metadata_read_failures:
                    raise RuntimeError('synthetic nth generated metadata read failure')
                if self.failure_mode == 'metadata':
                    raise RuntimeError('synthetic metadata readback failure')
                data = copy.deepcopy(self.generated_metadata[fileId])
                data.update(copy.deepcopy(self.generated_metadata_read_overrides.get(read_count, {})))
            else:
                data = {
                    'id': fileId, 'name': fileId, 'size': len(self.contents[fileId]),
                    'mimeType': 'text/plain', 'parents': [], 'trashed': False,
                    'ownedByMe': True,
                    'permissions': [{'type': 'user', 'role': 'owner'}],
                    'capabilities': {'canEdit': True, 'canMoveItemWithinDrive': True},
                }
            return data
        return SimpleNamespace(execute=execute)

    def get_media(self, **kwargs):
        raise AssertionError('tests replace only HTTP media transfer, not worker or retrieval behavior')

    def create(self, body, media_body, **kwargs):
        self.create_fields.append(kwargs.get('fields'))
        assert kwargs.get('fields') == 'id'

        def execute():
            file_id = 'derived-' + str(len(self.uploads) + 1)
            content = media_body.getbytes(0, media_body.size())
            self.contents[file_id] = content
            self.uploads.append(copy.deepcopy(body))
            self.generated_metadata[file_id] = {
                'id': file_id,
                'name': body['name'],
                'mimeType': body['mimeType'],
                'parents': list(body['parents']),
                'size': len(content),
                'trashed': False,
                'ownedByMe': True,
                'permissions': [{'type': 'user', 'role': 'owner'}],
                'capabilities': {'canEdit': True, 'canMoveItemWithinDrive': True},
                'appProperties': copy.deepcopy(body['appProperties']),
            }
            self.generated_metadata[file_id].update(
                copy.deepcopy(self.generated_metadata_overrides)
            )
            if self.failure_mode == 'dispatch':
                raise RuntimeError('synthetic lost create response')
            return {'id': file_id}
        return SimpleNamespace(execute=execute)

    def update(self, **kwargs):
        return SimpleNamespace(
            execute=lambda: self.provider_mutations.append(('update', copy.deepcopy(kwargs)))
        )

    def delete(self, **kwargs):
        return SimpleNamespace(
            execute=lambda: self.provider_mutations.append(('delete', copy.deepcopy(kwargs)))
        )

    def permissions(self):
        return SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                execute=lambda: self.provider_mutations.append(
                    ('permission_create', copy.deepcopy(kwargs))
                )
            ),
            delete=lambda **kwargs: SimpleNamespace(
                execute=lambda: self.provider_mutations.append(
                    ('permission_delete', copy.deepcopy(kwargs))
                )
            ),
        )

    def read_content(self, file_id):
        self.content_reads.append(file_id)
        read_count = None
        if file_id in self.generated_metadata:
            read_count = self.generated_content_read_counts.get(file_id, 0) + 1
            self.generated_content_read_counts[file_id] = read_count
            if read_count in self.generated_content_read_failures:
                raise SourceUnavailableError('synthetic nth generated content read failure')
        if self.failure_mode == 'content' and file_id.startswith('derived-'):
            raise SourceUnavailableError('synthetic content readback failure')
        if self.failure_mode == 'content_mismatch' and file_id.startswith('derived-'):
            return self.contents[file_id] + b'changed after create'
        if read_count in self.generated_content_read_mismatches:
            return self.contents[file_id] + b'changed at a later gate'
        return self.contents[file_id]


def config():
    cfg = UlsConfig(courses=[CourseCfg(course_key=COURSE, code='COMP319', section='002', semester='2026-1')])
    cfg.notion = NotionCfg(**{name: name.removesuffix('_db_id') + '-db' for name in NotionCfg.__dataclass_fields__})
    return cfg


def test_native_worker_publication_and_read_only_retrieval_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
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
        assert drive.create_fields == ['id']
        assert drive.metadata_reads.count('derived-folder') == 1
        assert [value for value in drive.metadata_reads if value in drive.generated_metadata] == ['derived-1'] * 3
        assert [value for value in drive.content_reads if value.startswith('derived-')] == ['derived-1'] * 3
        pointer_writes = [
            patch for patch in notion.writes
            if isinstance(patch.get('Normalized Transcript'), dict)
            and patch['Normalized Transcript'].get('url')
        ]
        assert len(pointer_writes) == 1
        assert drive.provider_mutations == []


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


@pytest.mark.parametrize('gate', [2, 3], ids=['prepublication-validation', 'publication-validation'])
@pytest.mark.parametrize(
    ('failure', 'metadata_override'),
    [
        pytest.param('privacy-mismatch', {'ownedByMe': False}, id='privacy-mismatch'),
        pytest.param('tuple-mismatch', {'parents': ['unexpected-parent']}, id='tuple-mismatch'),
        pytest.param('content-mismatch', {}, id='content-mismatch'),
        pytest.param('metadata-read-failure', {}, id='metadata-read-failure'),
        pytest.param('content-read-failure', {}, id='content-read-failure'),
    ],
)
def test_native_later_readback_gate_failure_needs_review_without_publication_or_retry(
    tmp_path, monkeypatch, gate, failure, metadata_override
):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    if failure in {'privacy-mismatch', 'tuple-mismatch'}:
        drive.generated_metadata_read_overrides[gate] = metadata_override
    elif failure == 'metadata-read-failure':
        drive.generated_metadata_read_failures.add(gate)
    elif failure == 'content-mismatch':
        drive.generated_content_read_mismatches.add(gate)
    else:
        drive.generated_content_read_failures.add(gate)
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}

    with SQLiteStateStore(tmp_path / 'state.sqlite3') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        first = worker.runner.run_once()

        assert first['failed'] == 1 and first['processed'] == 0, first
        assert len(drive.uploads) == 1
        assert drive.create_fields == ['id']
        assert drive.generated_metadata_read_counts == {'derived-1': gate}
        expected_content_reads = gate if failure in {'content-mismatch', 'content-read-failure'} else gate - 1
        assert drive.generated_content_read_counts == {'derived-1': expected_content_reads}
        assert notion.writes == []
        assert not any(
            row['parent'].get('data_source_id') == 'sessions-ds'
            and row.get('properties', {}).get('Recording Status', {}).get('select', {}).get('name') == 'Ready'
            for row in notion.rows.values()
        )
        assert drive.provider_mutations == []
        job = state.list_jobs(limit=10)[0]
        assert getattr(job.status, 'value', job.status) == 'NEEDS_REVIEW'
        assert job.error_class == 'AMBIGUOUS'
        assert state.connection.execute('SELECT COUNT(*) FROM processing_records').fetchone()[0] == 0

        second = worker.runner.run_once()
        assert second['processed'] == 0 and second['failed'] == 0, second
        assert len(drive.uploads) == 1
        assert notion.writes == []
        assert drive.provider_mutations == []


def test_native_runtime_error_with_ambiguous_prefix_is_not_classified_ambiguous(
    tmp_path, monkeypatch
):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))

    def fail_notion_create(**_kwargs):
        raise RuntimeError('DERIVED_OUTCOME_UNKNOWN: ordinary Notion write failure')

    notion.pages.create = fail_notion_create
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.sqlite3') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        result = worker.runner.run_once()

        assert result['failed'] == 1 and result['processed'] == 0, result
        assert len(drive.uploads) == 1
        job = state.list_jobs(limit=10)[0]
        assert getattr(job.status, 'value', job.status) == 'FAILED'
        assert job.error_class == 'PERMANENT'
        assert state.connection.execute('SELECT COUNT(*) FROM processing_records').fetchone()[0] == 0
        assert not any(
            isinstance(patch.get('Normalized Transcript'), dict)
            and patch['Normalized Transcript'].get('url')
            for patch in notion.writes
        )


def test_native_ambiguous_transition_write_fault_records_current_degradation(
    tmp_path, monkeypatch
):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = NotionService(), DriveService(), config()
    drive.failure_mode = 'dispatch'
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.sqlite3') as state:
        state.connection.execute(
            """CREATE TRIGGER fail_ambiguous_transition
               BEFORE UPDATE OF status ON jobs
               WHEN NEW.status = 'NEEDS_REVIEW'
               BEGIN SELECT RAISE(ABORT, 'synthetic SQLite transition fault'); END"""
        )
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        first = worker.runner.run_once()

        assert first['failed'] == 1 and first['processed'] == 0, first
        assert len(drive.uploads) == 1
        job = state.list_jobs(limit=10)[0]
        assert getattr(job.status, 'value', job.status) == 'FAILED'
        assert job.error_class == 'PERMANENT'
        assert state.connection.execute('SELECT COUNT(*) FROM processing_records').fetchone()[0] == 0
        assert notion.writes == []

        second = worker.runner.run_once()
        assert second['processed'] == 0 and second['failed'] == 0, second
        assert len(drive.uploads) == 1
        assert notion.writes == []


@pytest.mark.parametrize(
    ('failure_mode', 'metadata_overrides'),
    [
        pytest.param('dispatch', {}, id='lost-create-response'),
        pytest.param('metadata', {}, id='metadata-read-unavailable'),
        pytest.param('content', {}, id='content-read-unavailable'),
        pytest.param('content_mismatch', {}, id='content-mismatch'),
        pytest.param('none', {'id': 'unexpected-id'}, id='identity-mismatch'),
        pytest.param('none', {'parents': ['unexpected-parent']}, id='parent-mismatch'),
        pytest.param('none', {'mimeType': 'application/octet-stream'}, id='mime-mismatch'),
        pytest.param('none', {'appProperties': {'uls_entity': 'wrong'}}, id='marker-mismatch'),
        pytest.param('none', {'trashed': True}, id='trashed'),
        pytest.param('none', {'ownedByMe': False}, id='not-user-owned'),
        pytest.param('none', {'driveId': 'shared-drive-1'}, id='shared-drive'),
        pytest.param(
            'none',
            {'permissions': [{'type': 'user', 'role': 'owner'}, {'type': 'user', 'role': 'writer'}]},
            id='not-owner-only',
        ),
        pytest.param(
            'none', {'permissions': [{'type': 'anyone', 'role': 'reader'}]}, id='broad-sharing'
        ),
        pytest.param(
            'none',
            {'capabilities': {'canEdit': True, 'canMoveItemWithinDrive': False}},
            id='missing-required-capability',
        ),
    ],
)
def test_native_uncertain_derivative_create_is_permanent_and_not_retried(
    tmp_path, monkeypatch, failure_mode, metadata_overrides
):
    pytest.importorskip('googleapiclient')
    notion, drive, cfg = (
        NotionService(), DriveService(generated_metadata_overrides=metadata_overrides), config()
    )
    drive.failure_mode = failure_mode
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.sqlite3') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        first = worker.runner.run_once()
        assert first['failed'] == 1 and first['processed'] == 0, first
        assert len(drive.uploads) == 1
        assert not any(
            isinstance(patch.get('Normalized Transcript'), dict)
            and patch['Normalized Transcript'].get('url')
            for patch in notion.writes
        )
        assert drive.provider_mutations == []
        failed_jobs = state.list_jobs(limit=10)
        assert len(failed_jobs) == 1
        assert getattr(failed_jobs[0].status, 'value', failed_jobs[0].status) == 'NEEDS_REVIEW'
        assert failed_jobs[0].error_class == 'AMBIGUOUS'
        assert state.connection.execute(
            'SELECT COUNT(*) FROM processing_records'
        ).fetchone()[0] == 0

        second = worker.runner.run_once()
        assert second['processed'] == 0 and second['failed'] == 0, second
        assert len(drive.uploads) == 1
        assert not any(
            isinstance(patch.get('Normalized Transcript'), dict)
            and patch['Normalized Transcript'].get('url')
            for patch in notion.writes
        )
        assert drive.provider_mutations == []


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


def test_semester_reader_uses_direct_sources_and_exact_session_is_semester_scoped():
    service, cfg = NotionService(), config()
    service.rows['session-page'] = {
        'id': 'session-page',
        'parent': {'data_source_id': 'sessions-ds'},
        'archived': False,
        'properties': {
            'ID': property_value('rich_text', 'COMP319-S05'),
            'Name': property_value('title', '05 · CPU Scheduling'),
            'Aliases': property_value('rich_text', '5강 | CPU Scheduling'),
            'Session No': property_value('number', 5),
            'Course': property_value('relation', [{'id': 'course-page'}]),
        },
    }
    service.databases.retrieve = lambda **_: (_ for _ in ()).throw(
        AssertionError('semester retrieval must not discover legacy databases')
    )
    reader = NotionAPIReader(
        service,
        cfg.notion,
        data_source_ids={
            'courses': 'courses-ds',
            'sessions': 'sessions-ds',
            'materials': 'materials-ds',
        },
        expected_semester='2026-1',
    )
    cfg.retrieval.notion_lane = 'semester_workspace'
    cfg.retrieval.semester = '2026-1'
    engine = RetrievalEngine(
        reader,
        object(),
        object(),
        MemoryEphemeralStore(),
        cfg,
    )

    resolved = engine.resolve_entity('COMP319-S05')
    assert resolved.status == 'resolved'
    assert resolved.entity is not None
    assert resolved.entity.entity_id == 'COMP319-S05'
    assert reader.supports_data_source('material_usage') is False
    with pytest.raises(SourceUnavailableError):
        reader.get_exam('COMP319-E01')

    service.rows['course-page']['properties']['Course Key'] = property_value(
        'rich_text', '2025-2_COMP319-002'
    )
    with pytest.raises(SourceUnavailableError):
        engine.resolve_entity('COMP319-S05')


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
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
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
    monkeypatch.setattr(GoogleDriveReader, 'download', lambda self, file_id: drive.read_content(file_id))
    registration = {'file_id': 'raw1', 'course_key': COURSE, 'kind': 'transcript',
                    'derived_folder_id': 'derived-folder', 'title': 'Scheduling',
                    'date': '2026-09-10', 'status': 'Not started'}
    with SQLiteStateStore(tmp_path / 'state.db') as state:
        worker = NativeWorker(cfg, state, drive, notion, [registration])
        result = worker.runner.run_once()
        assert result['processed'] == 0 and result['failed'] == 1
        assert drive.uploads == []
        assert notion.writes == []
