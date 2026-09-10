"""Read-only Drive adapter using independently stored processing provenance."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from uls.domain.errors import ProviderUnavailableError, SourcePartialError, SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef


class GoogleDriveReader:
    def __init__(self, service: Any, bindings: Any, *, max_bytes: int = 20_000_000) -> None:
        self._get = service.files().get
        self._get_media = service.files().get_media
        self.bindings = bindings
        self.max_bytes = max_bytes

    def download(self, file_id: str) -> bytes:
        if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
            raise SourceUnavailableError('Invalid Drive file ID')
        try:
            metadata = self._get(fileId=file_id, fields='id,size,trashed,mimeType', supportsAllDrives=True).execute()
            if metadata.get('id') != file_id or metadata.get('trashed') is not False:
                raise SourceUnavailableError('Drive source identity changed or was trashed')
            size = int(metadata.get('size', -1))
            if not 0 <= size <= self.max_bytes:
                raise SourcePartialError('Drive file is not a bounded downloadable source')
            import io

            from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]
            output = io.BytesIO()
            downloader = MediaIoBaseDownload(output,
                self._get_media(fileId=file_id, supportsAllDrives=True), chunksize=256_000)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=0)
                if output.tell() > self.max_bytes:
                    raise SourcePartialError('Drive body exceeds byte limit')
            data = output.getvalue()
            if len(data) != size:
                raise SourceUnavailableError('Drive source changed during download')
            return data
        except (SourceUnavailableError, SourcePartialError):
            raise
        except Exception:  # noqa: BLE001 - redact provider failures at the adapter boundary
            raise ProviderUnavailableError('Drive read failed') from None

    def read_derived(self, source_ref: SourceRef | str | Any) -> str:
        if not isinstance(source_ref, SourceRef) or source_ref.provider != 'google_drive':
            raise SourceUnavailableError('Only registered Drive sources can be read')
        source = self.bindings.source(source_ref)
        binding = self.bindings.binding(source['canonical_entity_id'])
        if binding.source_ref.identity != source_ref.identity:
            raise SourceUnavailableError('Derivative originating source mismatch')
        try:
            return self.download(binding.derivative_ref.file_id).decode('utf-8')
        except UnicodeError:
            raise SourceUnavailableError('Derivative is not UTF-8 text') from None

    def get_current_fingerprint(self, entity_id_or_source_ref: SourceRef | str | Any) -> SourceFingerprint:
        source_ref = entity_id_or_source_ref
        if not isinstance(source_ref, SourceRef) or source_ref.provider != 'google_drive':
            raise SourceUnavailableError('Source fingerprint requires a registered Drive identity')
        source = self.bindings.source(source_ref)
        digest = 'sha256:' + hashlib.sha256(self.download(source_ref.file_id)).hexdigest()
        return SourceFingerprint(source['version'], digest)
