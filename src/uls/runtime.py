"""Composition roots. MCP never loads worker adapters or credentials."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uls.config.credentials import ResolvedCredentials
from uls.config.errors import ConfigurationError
from uls.config.schema import UlsConfig
from uls.intake.identity import provider_binding_id

DRIVE_READ_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'


def state_path(config: UlsConfig) -> Path:
    return Path(config.system.workspace_dir).expanduser().resolve() / 'state.sqlite3'


def require_mcp_credentials(secrets: ResolvedCredentials) -> None:
    # Takes an already-resolved ResolvedCredentials snapshot, not a raw
    # Mapping/os.environ, per the single-read composition-root contract in
    # docs/plans/credential-resolver.md: the caller resolved this snapshot
    # exactly once, and this function reads from that same snapshot
    # instead of triggering a second resolution.
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
    credentials = _load_google_credentials(credentials_file, read_only=read_only)
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    return build('drive', 'v3', credentials=credentials, cache_discovery=False)


def _load_google_credentials(credentials_file: str, *, read_only: bool) -> Any:
    import google.auth

    scope = DRIVE_READ_SCOPE if read_only else 'https://www.googleapis.com/auth/drive'
    # Normal credential loading: no credential values enter logs or return data.
    credentials, _ = google.auth.load_credentials_from_file(  # type: ignore[no-untyped-call]
        str(Path(credentials_file).expanduser()), scopes=[scope])
    if read_only:
        declared = getattr(credentials, 'scopes', None)
        if declared and any(value != DRIVE_READ_SCOPE for value in declared):
            raise ConfigurationError('Drive MCP credential declares non-read-only scopes')
    return credentials


def google_worker_service(credentials_file: str) -> tuple[Any, str]:
    """Build the write service and attest its account/application identity.

    The account permission ID comes from the authenticated Drive ``about``
    resource.  The OAuth application ID comes from the normal credential
    object's non-secret client metadata.  Only their digest is returned to the
    intake ledger; neither value is sent to a provider mutation or logged.
    """

    credentials = _load_google_credentials(credentials_file, read_only=False)
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
    try:
        about = service.about().get(fields='user(permissionId)').execute()
    except Exception:
        raise ConfigurationError('authenticated Drive account identity could not be read') from None
    user = about.get('user') if isinstance(about, Mapping) else None
    permission_id = user.get('permissionId') if isinstance(user, Mapping) else None
    if not isinstance(permission_id, str) or not permission_id:
        raise ConfigurationError('authenticated Drive account permission ID is unavailable')
    oauth_app_id = _credential_oauth_app_id(credentials)
    return service, provider_binding_id('google_drive', permission_id, oauth_app_id)


def _credential_oauth_app_id(credentials: Any) -> str:
    for name in ('client_id', '_client_id'):
        value = getattr(credentials, name, None)
        if isinstance(value, str) and value and not any(char.isspace() for char in value):
            return value
    raise ConfigurationError('authenticated OAuth application identity is unavailable')


def worker_provider_binding(
    values: ResolvedCredentials,
    *,
    provider: str = 'google_drive',
    explicit_binding: str | None = None,
) -> str:
    """Resolve the non-secret account/application binding for worker writes.

    The two stable identity inputs are supplied by the normal authenticated
    runtime or by an injected test composition.  They are never credentials,
    persisted, or included in provider requests; only their deterministic
    digest crosses into the intake ledger.
    """

    del values
    if not explicit_binding or any(character.isspace() for character in explicit_binding):
        raise ConfigurationError(
            'explicit provider binding is available only for injected test ports'
        )
    return explicit_binding


def build_intake_worker(
    config: UlsConfig,
    credentials: ResolvedCredentials,
    *,
    state: Any | None = None,
    drive: Any | None = None,
    notion: Any | None = None,
    provider_account_binding_id: str | None = None,
    semester: str | None = None,
) -> Any:
    """Compose the writable preview worker behind explicit provider ports.

    Tests and local dry runs may inject provider-neutral ports.  The live path
    uses the separately configured worker credentials and the SDK adapters;
    retrieval credentials are never reused.  A missing identity attestation or
    marker-capable port leaves intake activation unavailable.

    credentials is a required ResolvedCredentials snapshot produced by
    exactly one CredentialResolver.resolve() call at this composition's
    root (see docs/plans/credential-resolver.md); this function must never
    read os.environ or construct its own resolver.
    """

    from uls.adapters.drive.worker import GoogleDriveWorkerAdapter
    from uls.adapters.notion.intake import NotionAPIWorker
    from uls.intake.worker import IntakeWorker
    from uls.state.sqlite import SQLiteStateStore

    injected_ports = drive is not None or notion is not None
    if injected_ports and (drive is None or notion is None):
        raise ConfigurationError('injected Drive and Notion worker ports must be supplied together')
    if injected_ports:
        binding = worker_provider_binding(credentials, explicit_binding=provider_account_binding_id)
    else:
        for key in ('GOOGLE_WORKER_CREDENTIALS_FILE', 'NOTION_WORKER_TOKEN'):
            if not credentials.get(key):
                raise ConfigurationError(key + ' is required for intake worker activation')
        service, binding = google_worker_service(credentials['GOOGLE_WORKER_CREDENTIALS_FILE'])
        drive = GoogleDriveWorkerAdapter(service)
        from notion_client import Client

        notion = NotionAPIWorker(
            Client(auth=credentials['NOTION_WORKER_TOKEN'], notion_version='2025-09-03', timeout_ms=20_000)
        )
    if state is None:
        state = SQLiteStateStore(state_path(config))
    return IntakeWorker(
        config,
        state,
        drive,
        notion,
        provider_account_binding_id=binding,
        semester=semester,
    )


def build_retrieval(config: UlsConfig, credentials: ResolvedCredentials) -> Any:
    # credentials is a required ResolvedCredentials snapshot produced by
    # exactly one CredentialResolver.resolve() call at this composition's
    # root; this function must never read os.environ or construct its own
    # resolver.
    from notion_client import Client

    from uls.adapters.drive.binding import ValidatedSourceBindingResolver
    from uls.adapters.drive.google import GoogleDriveReader
    from uls.adapters.github.api import GitHubAPIReader
    from uls.adapters.notion.api import NotionAPIReader
    from uls.ephemeral.memory import MemoryEphemeralStore
    from uls.retrieval.engine import RetrievalEngine
    from uls.state.reader import ReadOnlyState

    require_mcp_credentials(credentials)
    state = ReadOnlyState(state_path(config))
    drive = GoogleDriveReader(google_service(credentials['GOOGLE_MCP_CREDENTIALS_FILE'], read_only=True), state)
    notion = NotionAPIReader(Client(auth=credentials['NOTION_MCP_TOKEN'], notion_version='2025-09-03',
                                     timeout_ms=20_000), config.notion)
    return RetrievalEngine(notion, drive, state, MemoryEphemeralStore(), config,
                           source_binding_resolver=ValidatedSourceBindingResolver(state),
                           github_reader=GitHubAPIReader(credentials.get('GITHUB_READ_TOKEN', '')))
