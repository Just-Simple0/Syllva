"""Composition roots. MCP never loads worker adapters or credentials."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uls.config.errors import ConfigurationError
from uls.config.schema import UlsConfig

DRIVE_READ_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'


def state_path(config: UlsConfig) -> Path:
    return Path(config.system.workspace_dir).expanduser().resolve() / 'state.sqlite3'


def require_mcp_credentials(secrets: Mapping[str, str]) -> None:
    for name in ('GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'):
        if not secrets.get(name):
            raise ConfigurationError(name + ' is required; worker credentials are never a fallback')
    if secrets.get('NOTION_MCP_TOKEN') == secrets.get('NOTION_WORKER_TOKEN'):
        raise ConfigurationError('MCP and worker Notion tokens must be distinct')
    if secrets.get('GOOGLE_WORKER_CREDENTIALS_FILE'):
        mcp_path = Path(secrets['GOOGLE_MCP_CREDENTIALS_FILE']).expanduser().resolve()
        worker_path = Path(secrets['GOOGLE_WORKER_CREDENTIALS_FILE']).expanduser().resolve()
        if mcp_path == worker_path:
            raise ConfigurationError('MCP and worker Drive credential files must be distinct')


def google_service(credentials_file: str, *, read_only: bool) -> Any:
    import google.auth
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    scope = DRIVE_READ_SCOPE if read_only else 'https://www.googleapis.com/auth/drive'
    # Normal credential loading: no credential values enter logs or return data.
    credentials, _ = google.auth.load_credentials_from_file(  # type: ignore[no-untyped-call]
        str(Path(credentials_file).expanduser()), scopes=[scope])
    if read_only:
        declared = getattr(credentials, 'scopes', None)
        if declared and any(value != DRIVE_READ_SCOPE for value in declared):
            raise ConfigurationError('Drive MCP credential declares non-read-only scopes')
    return build('drive', 'v3', credentials=credentials, cache_discovery=False)


def build_retrieval(config: UlsConfig, secrets: Mapping[str, str] | None = None) -> Any:
    from notion_client import Client

    from uls.adapters.drive.binding import ValidatedSourceBindingResolver
    from uls.adapters.drive.google import GoogleDriveReader
    from uls.adapters.github.api import GitHubAPIReader
    from uls.adapters.notion.api import NotionAPIReader
    from uls.ephemeral.memory import MemoryEphemeralStore
    from uls.retrieval.engine import RetrievalEngine
    from uls.state.reader import ReadOnlyState

    values = os.environ if secrets is None else secrets
    require_mcp_credentials(values)
    state = ReadOnlyState(state_path(config))
    drive = GoogleDriveReader(google_service(values['GOOGLE_MCP_CREDENTIALS_FILE'], read_only=True), state)
    notion = NotionAPIReader(Client(auth=values['NOTION_MCP_TOKEN'], notion_version='2025-09-03',
                                     timeout_ms=20_000), config.notion)
    return RetrievalEngine(notion, drive, state, MemoryEphemeralStore(), config,
                           source_binding_resolver=ValidatedSourceBindingResolver(state),
                           github_reader=GitHubAPIReader(values.get('GITHUB_READ_TOKEN', '')))
