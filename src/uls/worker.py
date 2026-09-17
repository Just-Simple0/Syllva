"""Local worker composition using the existing transactional transcript pipeline.

Source registrations contain routing metadata only. Drive remains canonical;
SQLite stores jobs/identity/provenance and never stores source bodies.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from uls.adapters.drive.google import GoogleDriveReader
from uls.adapters.drive.worker import (
    DRIVE_FOLDER_MIME,
    GoogleDriveWorkerAdapter,
    require_private_ownership,
)
from uls.adapters.notion.api import NotionAPIReader
from uls.adapters.notion.base import AutomationActor, enforce_write_policy
from uls.config.credentials import ResolvedCredentials
from uls.config.errors import ConfigurationError
from uls.domain.course_identity import resolve_course_relation, validate_course_record
from uls.domain.errors import PolicyDeniedError, ProviderUnavailableError, SourceUnavailableError
from uls.domain.ids import parse_course_key, parse_entity_id
from uls.domain.source_ref import SourceRef
from uls.ingestion.transcript_ingest import TRANSCRIPT_INGEST_OPERATION, ingest_transcript
from uls.orchestration.jobs import derive_job_key
from uls.orchestration.runner import WorkerRunner
from uls.runtime import google_service, state_path
from uls.state.sqlite import SQLiteStateStore


def load_sources(path: Path, course_keys: set[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        # The semester intake preview resolves provider files from its
        # authoritative registered upload folders.  A missing legacy
        # ``sources.json`` therefore means an empty legacy lane, not a reason
        # to block preview discovery.
        return []
    if path.stat().st_size > 1_000_000:
        raise ConfigurationError('Source registration file exceeds size limit')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ConfigurationError('Source registrations must be a list of at most 1000 entries')
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - {
            'file_id', 'course_key', 'kind', 'derived_folder_id', 'title', 'date', 'status',
        }:
            raise ConfigurationError('Invalid source registration fields')
        for key in ('file_id', 'derived_folder_id'):
            if not isinstance(entry.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', entry[key]):
                raise ConfigurationError('Source registrations require Drive source and Derived folder IDs')
        if entry.get('course_key') not in course_keys:
            raise ConfigurationError('Source Course Key must be present in configuration')
        parse_course_key(entry['course_key'])
        if entry.get('kind') != 'transcript':
            raise ConfigurationError('Native worker currently accepts transcript registrations only')
        if entry['file_id'] in seen:
            raise ConfigurationError('Duplicate source registration')
        seen.add(entry['file_id'])
    return raw


class DerivedDriveWriter:
    """Create immutable staged derivatives; never overwrite an originating file."""
    def __init__(self, service: Any, reader: GoogleDriveReader, folder_id: str) -> None:
        self._files, self.reader, self.folder_id = service.files(), reader, folder_id
        # A separate metadata-only adapter reuses the exact same field list and
        # parsing/ownership boundary enforced for the semester intake layout,
        # instead of duplicating a second, looser Drive metadata read here.
        self._metadata_port = GoogleDriveWorkerAdapter(service)

    def write_staged_derived(self, source_ref: SourceRef, entity_id: str, content: str) -> SourceRef:
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import-untyped]
        # The configured destination must be an existing, exclusively
        # USER-owned, non-shared Derived folder. Ownership/sharing can drift
        # after initial setup (or the configured ID can simply be wrong), so
        # this is re-verified on every write rather than assumed once.
        folder = self._metadata_port.read_metadata(self.folder_id)
        if folder.mime_type != DRIVE_FOLDER_MIME:
            raise SourceUnavailableError('Derived folder is unavailable')
        require_private_ownership(folder, context='Derived folder')
        result = self._files.create(body={
            'name': entity_id + '.staged.md', 'parents': [self.folder_id],
            'mimeType': 'text/markdown', 'appProperties': {'uls_entity': entity_id, 'uls_source': source_ref.file_id},
        }, media_body=MediaIoBaseUpload(io.BytesIO(content.encode()), mimetype='text/markdown', resumable=False),
            fields='id', supportsAllDrives=True).execute()
        file_id = result['id']
        if file_id == source_ref.file_id:
            raise PolicyDeniedError('Derived upload returned the original source identity')
        return SourceRef('google_drive', file_id, f'https://drive.google.com/file/d/{file_id}/view')

    def validate_derived(self, staged_ref: SourceRef, content: str) -> bool:
        return self.reader.download(staged_ref.file_id).decode('utf-8') == content

    def publish_staged_derived(self, staged_ref: SourceRef, source_ref: SourceRef,
                              entity_id: str, content: str) -> SourceRef:
        if staged_ref.provider != 'google_drive' or staged_ref.file_id == source_ref.file_id:
            raise PolicyDeniedError('Publication cannot overwrite a canonical source')
        # Publication is an immutable file reference, then Notion atomically
        # replaces its URL property. Old derivatives remain recoverable.
        if not self.validate_derived(staged_ref, content):
            raise SourceUnavailableError('Staged derivative readback failed')
        return staged_ref


class TranscriptNotionWriter:
    def __init__(self, client: Any, reader: NotionAPIReader, registration: dict[str, Any],
                 previous_pointers: frozenset[str] = frozenset()) -> None:
        self._client, self.reader, self.registration = client, reader, registration
        self.previous_pointers = previous_pointers

    def update_session_transcript(self, entity_id: str, published_ref: SourceRef, status: str) -> Any:
        if not isinstance(published_ref, SourceRef) or published_ref.provider != 'google_drive':
            raise PolicyDeniedError('Transcript metadata requires a Drive derivative')
        if status not in {'Ready', 'Partial'}:
            raise PolicyDeniedError('Invalid transcript status')
        entity = parse_entity_id(entity_id)
        if entity.entity_type != 'S':
            raise PolicyDeniedError('Transcript metadata requires a Session')
        course = self.reader.get_course_by_alias(self.registration['course_key'])
        if course is None:
            raise SourceUnavailableError('Registered Course is unavailable')
        patch = {'ID': entity_id, 'Normalized Transcript': published_ref.web_url,
                 'Recording Status': status}
        enforce_write_policy(AutomationActor.AUTOMATION, 'Sessions', patch)
        properties = {'Normalized Transcript': {'url': published_ref.web_url},
                      'Recording Status': {'select': {'name': status}}}
        current = self.reader.get_session(entity_id)
        if current is None:
            from datetime import date
            for key in ('title', 'date', 'status'):
                if not isinstance(self.registration.get(key), str) or not self.registration[key]:
                    raise ConfigurationError('New Session needs explicit title, date and Status in source registration')
            date.fromisoformat(self.registration['date'])
            properties.update({
                'ID': {'rich_text': [{'text': {'content': entity_id}}]},
                'Name': {'title': [{'text': {'content': self.registration['title']}}]},
                'Course': {'relation': [{'id': course['id']}]},
                'Session No': {'number': int(entity_id.rsplit('S', 1)[1])},
                'Date': {'date': {'start': self.registration['date']}},
                'Status': {'status': {'name': self.registration['status']}},
            })
            enforce_write_policy(AutomationActor.AUTOMATION, 'Sessions', properties, is_create=True)
            self._client.pages.create(parent={'data_source_id': self.reader._data_source('sessions')},
                                      properties=properties)
        else:
            previous = current.get('Normalized Transcript')
            if previous and previous != published_ref.web_url and previous not in self.previous_pointers:
                raise PolicyDeniedError('Existing transcript pointer has no matching durable source association')
            relation_id = resolve_course_relation(current.get('Course'))
            actual_course = self.reader.get_course_by_relation_id(relation_id) if relation_id else None
            identity = validate_course_record(actual_course, relation_id or '')
            expected_course = validate_course_record(course, course['id'])
            if identity is None or identity != expected_course:
                raise PolicyDeniedError('Session Course does not match registered source')
            self._client.pages.update(page_id=current['id'], properties=properties)
        observed = self.reader.get_session(entity_id)
        if (observed is None or observed.get('Normalized Transcript') != published_ref.web_url
                or observed.get('Recording Status') != status):
            raise ProviderUnavailableError('Notion transcript metadata readback is uncertain')
        return observed


class NativeWorker:
    def __init__(self, config: Any, state: SQLiteStateStore, drive_service: Any, notion_client: Any,
                 sources: list[dict[str, Any]]) -> None:
        self.config, self.state, self.sources = config, state, sources
        self.drive_service, self.notion_client = drive_service, notion_client
        self.drive = GoogleDriveReader(drive_service, None)
        self.notion = NotionAPIReader(notion_client, config.notion)
        self.runner = WorkerRunner(state, {TRANSCRIPT_INGEST_OPERATION: self.process}, discover=self.sync)

    def sync(self) -> int:
        count = 0
        for spec in self.sources:
            data = self.drive.download(spec['file_id'])
            digest = 'sha256:' + hashlib.sha256(data).hexdigest()
            self.state.register_source_file(spec['file_id'], provider='google_drive',
                provider_file_id=spec['file_id'], course_key=spec['course_key'],
                source_kind='transcript', current_hash=digest)
            entity_id = self.state.allocate_entity(spec['course_key'], 'S', spec['file_id'])
            key = derive_job_key(spec['file_id'], digest, TRANSCRIPT_INGEST_OPERATION,
                                 self.config.normalization.processor_version)
            existing = self.state.get_job(job_key=key)
            self.state.create_job(key, operation=TRANSCRIPT_INGEST_OPERATION, stage='transcript',
                course_key=spec['course_key'], source_file_id=spec['file_id'], source_hash=digest,
                target_entity_id=entity_id, processor_version=self.config.normalization.processor_version)
            count += existing is None
        return count

    def process(self, job: Any) -> Any:
        spec = next((entry for entry in self.sources if entry['file_id'] == job.source_file_id), None)
        if spec is None or spec['course_key'] != job.course_key:
            raise PolicyDeniedError('Job has no current source registration')
        data = self.drive.download(spec['file_id'])
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        if digest != job.source_hash:
            raise SourceUnavailableError('Source changed after discovery; run sync for the new version')
        versions = self.state.find_source_versions(source_file_id=job.source_file_id)
        matching = next((v for v in versions if v.source_hash == digest), None)
        version = matching.version if matching else max((v.version for v in versions), default=0) + 1
        previous_pointers = set()
        for row in self.state.connection.execute('''SELECT pr.output_ref_json FROM processing_records pr
                JOIN jobs j ON j.id=pr.job_id WHERE j.source_file_id=? AND j.target_entity_id=?
                AND j.operation=? AND pr.operation=j.operation
                AND pr.status IN ('READY','PARTIAL')''',
                (job.source_file_id, job.target_entity_id, TRANSCRIPT_INGEST_OPERATION)):
            ref = json.loads(row['output_ref_json'])
            if isinstance(ref, dict) and ref.get('provider') == 'google_drive':
                previous_pointers.add(ref.get('web_url') or f"https://drive.google.com/file/d/{ref['file_id']}/view")
        return ingest_transcript(data, entity_id=job.target_entity_id, course_key=job.course_key,
            source_ref=SourceRef('google_drive', spec['file_id']), source_hash=digest,
            source_version=version, state_store=self.state,
            processor_version=self.config.normalization.processor_version,
            drive_writer=DerivedDriveWriter(self.drive_service, self.drive, spec['derived_folder_id']),
            notion_writer=TranscriptNotionWriter(self.notion_client, self.notion, spec,
                                                 frozenset(previous_pointers)))


def build_worker(config: Any, credentials: ResolvedCredentials) -> Any:
    # credentials is a required ResolvedCredentials snapshot produced by
    # exactly one CredentialResolver.resolve() call at this composition's
    # root (cli/main.py's dispatch(), sync|process|run branch); this
    # function must never read os.environ or construct its own resolver.
    # Its two branches below are mutually exclusive within this one
    # function call, not a nested resolve chain, so one snapshot covers
    # both.
    if config.google_drive.semester_registries and config.notion.semester_workspaces:
        from uls.runtime import build_intake_worker

        return build_intake_worker(config, credentials)
    from notion_client import Client
    for key in ('GOOGLE_WORKER_CREDENTIALS_FILE', 'NOTION_WORKER_TOKEN'):
        if not credentials.get(key):
            raise ConfigurationError(key + ' is required for worker commands')
    sources = load_sources(Path(config.system.workspace_dir).expanduser() / 'sources.json',
                           {course.course_key for course in config.courses})
    worker_payload = credentials.get_google_payload('GOOGLE_WORKER_CREDENTIALS_FILE')
    if worker_payload is None:
        raise ConfigurationError('GOOGLE_WORKER_CREDENTIALS_FILE payload is missing')
    service = google_service(worker_payload, read_only=False)
    client = Client(auth=credentials['NOTION_WORKER_TOKEN'], notion_version='2025-09-03', timeout_ms=20_000)
    return NativeWorker(config, SQLiteStateStore(state_path(config)), service, client, sources)
