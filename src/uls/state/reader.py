"""Read-only completed-worker provenance lookup for the MCP process."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from uls.adapters.drive.binding import ActivityInstructionBinding, SourceBindingRecord
from uls.domain.errors import SourceUnavailableError
from uls.domain.source_ref import SourceRef


def parse_derivative_ref(raw_json: Any) -> SourceRef:
    """Parse and validate a stored output derivative reference JSON string.

    Raises ValueError or TypeError if the payload is malformed, not a dict,
    has an unsupported provider (only google_drive is supported in v1.2), or
    lacks a valid non-empty file ID without path separators.
    """
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise ValueError('empty derivative ref json')
    raw = json.loads(raw_json)
    if not isinstance(raw, dict) or raw.get('provider') != 'google_drive':
        raise ValueError('unsupported derivative ref')
    file_id = raw.get('file_id')
    if not isinstance(file_id, str) or not file_id or '/' in file_id:
        raise ValueError('invalid derivative ID')
    return SourceRef(raw['provider'], file_id, raw.get('web_url'))


class ReadOnlyState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise SourceUnavailableError('StateStore is missing; run uls init locally')

    def _rows(self, query: str, args: tuple[Any, ...] = ()) -> list[Any]:
        try:
            with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True) as connection:
                connection.row_factory = sqlite3.Row
                return list(connection.execute(query, args))
        except sqlite3.Error:
            raise SourceUnavailableError('StateStore provenance is unavailable') from None

    def source(self, ref: SourceRef) -> Any:
        rows = self._rows('''SELECT sf.*, sv.version FROM source_files sf
            JOIN source_versions sv ON sv.source_file_id=sf.source_file_id
            AND sv.source_hash=sf.current_hash
            WHERE sf.provider=? AND sf.provider_file_id=?''', ref.identity)
        if len(rows) != 1:
            raise SourceUnavailableError('Source has no unique current durable version')
        return rows[0]

    def binding(self, entity_id: str) -> SourceBindingRecord:
        rows = self._rows('''SELECT sf.provider, sf.provider_file_id, pr.output_ref_json
            FROM source_files sf JOIN jobs j ON j.source_file_id=sf.source_file_id
            JOIN processing_records pr ON pr.job_id=j.id
            WHERE sf.canonical_entity_id=? AND j.source_hash=sf.current_hash
            AND pr.operation=j.operation AND pr.operation NOT IN ('enrich_session','enrich_material')
            AND pr.input_hash=sf.current_hash AND j.status IN ('READY','PARTIAL')
            AND pr.status IN ('READY','PARTIAL')
            ORDER BY pr.finished_at DESC, pr.rowid DESC''', (entity_id,))
        if not rows:
            raise SourceUnavailableError('No completed source-to-derivative association')
        refs = []
        for row in rows:
            try:
                ref = parse_derivative_ref(row['output_ref_json'])
                refs.append((row['provider'], row['provider_file_id'], ref))
            except (TypeError, ValueError, KeyError):
                raise SourceUnavailableError('Stored derivative provenance is malformed') from None
        if len({(provider, file_id) for provider, file_id, _ in refs}) != 1:
            raise SourceUnavailableError('Entity has ambiguous originating sources')
        provider, file_id, derivative = refs[0]
        pointer = derivative.web_url or f'https://drive.google.com/file/d/{derivative.file_id}/view'
        return SourceBindingRecord(entity_id, pointer, derivative, SourceRef(provider, file_id))

    def lookup_source_binding(self, entity_id: str, normalized_source_url: str) -> Any:
        return self.binding(entity_id)

    def lookup_activity_instruction_binding(self, activity_id: str, instructions_source_url: str,
                                            normalized_instructions_url: str) -> Any:
        binding = self.binding(activity_id)
        return ActivityInstructionBinding(activity_id,
            f'https://drive.google.com/file/d/{binding.source_ref.file_id}/view',
            str(binding.normalized_source_url), binding.derivative_ref, binding.source_ref)

    def health(self) -> bool:
        return bool(self._rows('PRAGMA quick_check')[0][0] == 'ok')
