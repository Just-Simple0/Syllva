"""Composition roots. MCP never loads worker adapters or credentials."""
from __future__ import annotations

import contextlib
import http.client
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from uls.config.credentials import GoogleCredentialPayload, ResolvedCredentials
from uls.config.errors import ConfigurationError
from uls.config.google_oauth import (
    TOKEN_URI,
    AuthorizedUserCredential,
    GoogleOAuthClient,
    GoogleOAuthCredentialError,
    GoogleOAuthPurpose,
    credential_type,
    exact_scopes,
    is_authorized_user_info,
    parse_authorized_user,
)
from uls.config.schema import UlsConfig
from uls.intake.attestation import ReconnectRequiredError, WorkerEntryAttestation
from uls.intake.identity import provider_binding_id

DRIVE_READ_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
OAUTH_HTTP_TIMEOUT_SECONDS = 10.0
OAUTH_MAX_RESPONSE_BYTES = 1024 * 1024


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


def google_service(payload: GoogleCredentialPayload, *, read_only: bool,
                   oauth_client: GoogleOAuthClient | None = None) -> Any:
    credentials = _load_google_credentials(payload, read_only=read_only, oauth_client=oauth_client)
    return _build_drive(credentials, oauth=is_authorized_user_info(payload.info))


def _build_drive(credentials: Any, *, oauth: bool) -> Any:
    if not oauth:
        from googleapiclient.discovery import build  # type: ignore[import-untyped]

        return build('drive', 'v3', credentials=credentials, cache_discovery=False)
    try:
        from googleapiclient.discovery import build

        return build('drive', 'v3', credentials=credentials, cache_discovery=False)
    except Exception:  # noqa: BLE001 - SDK import/build diagnostics are never forwarded for personal OAuth
        raise ReconnectRequiredError() from None


def _authorized_user_for(payload: GoogleCredentialPayload, *, read_only: bool,
                         oauth_client: GoogleOAuthClient | None) -> AuthorizedUserCredential | None:
    """Exact personal-OAuth gate; service-account payloads return None untouched."""

    if not is_authorized_user_info(payload.info):
        return None
    if oauth_client is None:
        # A persisted personal grant without its configured Desktop client can
        # never be proven; this is the fixed reconnect signal, never a fallback.
        raise ReconnectRequiredError()
    purpose = GoogleOAuthPurpose.MCP if read_only else GoogleOAuthPurpose.WORKER
    try:
        return parse_authorized_user(payload.info, purpose=purpose, client=oauth_client)
    except GoogleOAuthCredentialError:
        raise ReconnectRequiredError() from None


def _load_google_credentials(payload: GoogleCredentialPayload, *, read_only: bool,
                             oauth_client: GoogleOAuthClient | None = None) -> Any:
    if not isinstance(payload, GoogleCredentialPayload):
        raise ConfigurationError('Google credentials must be provided as a validated GoogleCredentialPayload')
    scope = DRIVE_READ_SCOPE if read_only else 'https://www.googleapis.com/auth/drive'
    kind = credential_type(payload.info)
    if kind is None:
        # Only the two supported credential types ever reach the SDK loader.
        raise ConfigurationError('unsupported Google credential type')
    authorized = _authorized_user_for(payload, read_only=read_only, oauth_client=oauth_client)
    info = dict(payload.info)
    if authorized is not None:
        assert oauth_client is not None
        try:
            import google.auth

            # Normal credential loading: no credential values enter logs or return data.
            credentials, _ = google.auth.load_credentials_from_dict(  # type: ignore[no-untyped-call]
                info, scopes=[scope])
        except Exception:  # noqa: BLE001 - SDK import/loader diagnostics may carry the refresh token
            raise ReconnectRequiredError() from None
        # Personal OAuth: prove the grant now (fresh refresh, exact scope,
        # configured client) instead of trusting the stored scopes.
        _prove_authorized_user_grant(credentials, purpose=authorized.purpose, client=oauth_client)
        return credentials
    import google.auth

    credentials, _ = google.auth.load_credentials_from_dict(  # type: ignore[no-untyped-call]
        info, scopes=[scope])
    if read_only:
        declared = getattr(credentials, 'scopes', None)
        if declared and any(value != DRIVE_READ_SCOPE for value in declared):
            raise ConfigurationError('Drive MCP credential declares non-read-only scopes')
    return credentials


def _bind_bounded_refresh(credentials: Any, *, purpose: GoogleOAuthPurpose, client: GoogleOAuthClient,
                          request_factory: Callable[[], Any] | None = None) -> Any:
    """Make every refresh of this credential object bounded and proven.

    The Google SDK re-authenticates through ``credentials.refresh(request)``
    with its own transport whenever an access token expires or a call returns
    401.  Binding the object once means those internal refreshes also use the
    bounded token transport and must prove the exact grant/client, or raise
    ``RECONNECT_REQUIRED`` before any further provider work.
    """

    if getattr(credentials, '_syllva_bound', False):
        return credentials
    factory = request_factory or BoundedTokenRequest
    base = type(credentials)
    # One lock per credential object: the provider-reported grant of each
    # refresh is verified before any other refresh can overwrite it.
    refresh_lock = threading.Lock()

    class ProvenRefresh(base):  # type: ignore[misc,valid-type]
        _syllva_bound = True

        def refresh(self, request: Any = None) -> None:  # the SDK-supplied request is never used
            del request
            with refresh_lock:
                try:
                    self._refresh_and_prove()
                except ReconnectRequiredError:
                    # The credential state may now carry an unproven token:
                    # every attestation issued for it is invalid until a new
                    # entry proof succeeds.
                    object.__setattr__(self, '_syllva_revoked', True)
                    raise

        def _refresh_and_prove(self) -> None:
            try:
                super().refresh(factory())
            except ReconnectRequiredError:
                raise
            except Exception:  # noqa: BLE001 - refresh diagnostics can carry tokens
                raise ReconnectRequiredError() from None
            granted = getattr(self, 'granted_scopes', None)
            if granted is None or not exact_scopes(granted, purpose):
                # No provider-reported grant is never substituted by the
                # locally configured scopes.
                raise ReconnectRequiredError()
            try:
                app_id = _credential_oauth_app_id(self)
            except ConfigurationError:
                raise ReconnectRequiredError() from None
            if app_id != client.client_id:
                raise ReconnectRequiredError()

    ProvenRefresh.__name__ = base.__name__
    try:
        credentials.__class__ = ProvenRefresh
    except Exception:  # noqa: BLE001 - an unbindable SDK credential never runs unproven
        raise ReconnectRequiredError() from None
    return credentials


def _prove_authorized_user_grant(credentials: Any, *, purpose: GoogleOAuthPurpose, client: GoogleOAuthClient,
                                 request_factory: Callable[[], Any] | None = None) -> None:
    """Refresh once through the bound transport and require the exact proven grant."""

    _bind_bounded_refresh(credentials, purpose=purpose, client=client, request_factory=request_factory)
    credentials.refresh()


def google_worker_service(payload: GoogleCredentialPayload, *,
                          oauth_client: GoogleOAuthClient | None = None) -> tuple[Any, str]:
    """Build the write service and attest its account/application identity.

    The account permission ID comes from the authenticated Drive ``about``
    resource.  The OAuth application ID comes from the normal credential
    object's non-secret client metadata.  Only their digest is returned to the
    intake ledger; neither value is sent to a provider mutation or logged.
    """

    if is_authorized_user_info(payload.info):
        # Personal OAuth WORKER credentials require the attestor-bearing
        # composition; discarding the attestor would bypass the entry gate.
        raise ReconnectRequiredError()
    service, binding, _attestor = google_worker_runtime(payload, oauth_client=oauth_client)
    return service, binding


def google_worker_runtime(payload: GoogleCredentialPayload, *,
                          oauth_client: GoogleOAuthClient | None = None,
                          clock: Callable[[], float] = time.monotonic) -> tuple[Any, str, Any | None]:
    """Build the write service, its binding digest and (for personal OAuth) one attestor.

    Service-account payloads keep the existing call graph and return no
    attestor.  ``authorized_user`` payloads are parsed exactly against the
    configured Desktop client, their grant is refreshed once here and the
    binding is derived from the fresh permission ID plus that client ID.
    """

    authorized = _authorized_user_for(payload, read_only=False, oauth_client=oauth_client)
    credentials = _load_google_credentials(payload, read_only=False, oauth_client=oauth_client)
    service = _build_drive(credentials, oauth=authorized is not None)
    if authorized is None:
        permission_id = _read_permission_id(service)
        oauth_app_id = _credential_oauth_app_id(credentials)
        return service, provider_binding_id('google_drive', permission_id, oauth_app_id), None
    assert oauth_client is not None
    attestor = GoogleOAuthWorkerAttestor(credentials, service, client=oauth_client,
                                         purpose=GoogleOAuthPurpose.WORKER, clock=clock)
    binding = attestor.bind()
    return service, binding, attestor


def _read_permission_id(service: Any) -> str:
    try:
        about = service.about().get(fields='user(permissionId)').execute()
    except Exception:
        raise ConfigurationError('authenticated Drive account identity could not be read') from None
    user = about.get('user') if isinstance(about, Mapping) else None
    permission_id = user.get('permissionId') if isinstance(user, Mapping) else None
    if not isinstance(permission_id, str) or not permission_id:
        raise ConfigurationError('authenticated Drive account permission ID is unavailable')
    return permission_id


class _BoundedTokenResponse:
    def __init__(self, status: int, data: bytes, headers: Mapping[str, str]) -> None:
        self.status = status
        self.data = data
        self.headers = dict(headers)


class BoundedTokenRequest:
    """google.auth transport Request allowing exactly one bounded POST to the token endpoint."""

    def __init__(self, *, connection_factory: Callable[..., Any] = http.client.HTTPSConnection) -> None:
        self.connection_factory = connection_factory
        self.calls = 0

    def __call__(self, url: str, method: str = 'GET', body: Any = None, headers: Any = None,
                 timeout: float = OAUTH_HTTP_TIMEOUT_SECONDS, **kwargs: Any) -> _BoundedTokenResponse:
        if url != TOKEN_URI or method != 'POST' or kwargs or self.calls >= 1:
            raise ReconnectRequiredError()
        self.calls += 1
        connection = None
        try:
            connection = self.connection_factory('oauth2.googleapis.com', timeout=min(OAUTH_HTTP_TIMEOUT_SECONDS, timeout))
            request_headers = {key: value for key, value in dict(headers or {}).items()
                               if key.lower() != 'accept-encoding'}
            request_headers['Accept-Encoding'] = 'identity'
            connection.request('POST', '/token', body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(OAUTH_MAX_RESPONSE_BYTES + 1)
            if len(data) > OAUTH_MAX_RESPONSE_BYTES:
                raise ReconnectRequiredError()
            return _BoundedTokenResponse(response.status, data, {})
        except ReconnectRequiredError:
            raise
        except Exception:  # noqa: BLE001 - transport text may carry the refresh token
            raise ReconnectRequiredError() from None
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()


class GoogleOAuthWorkerAttestor:
    """Fresh grant/client/account proof before every public WORKER entry (plan §5)."""

    def __init__(self, credentials: Any, service: Any, *, client: GoogleOAuthClient,
                 purpose: GoogleOAuthPurpose, request_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.attestor_id = secrets.token_hex(8)
        self._credentials = credentials
        self._service = service
        self._client = client
        self._purpose = purpose
        self._request_factory = request_factory or BoundedTokenRequest
        self._clock = clock
        self._generation = 0
        self.binding_id: str | None = None
        # One shared credential/service: refresh -> grant/client/account proof
        # -> generation issue is serialized so concurrent entries can never
        # observe each other's refresh results.
        self._lock = threading.Lock()

    @property
    def role(self) -> str:
        return self._purpose.value

    @property
    def scope(self) -> str:
        return self._purpose.scope

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def revoked(self) -> bool:
        """True after any refresh/proof failure since the last successful entry proof."""

        return bool(getattr(self._credentials, '_syllva_revoked', False))

    def _fresh_identity(self) -> str:
        """Refresh the grant and read the account; return the binding digest only."""

        _prove_authorized_user_grant(self._credentials, purpose=self._purpose, client=self._client,
                                     request_factory=self._request_factory)
        try:
            permission_id = _read_permission_id(self._service)
        except ConfigurationError:
            raise ReconnectRequiredError() from None
        return provider_binding_id('google_drive', permission_id, self._client.client_id)

    def bind(self) -> str:
        """First proof at composition time; fixes the binding for this process."""

        with self._lock:
            self.binding_id = self._fresh_identity()
            return self.binding_id

    def attest_entry(self) -> WorkerEntryAttestation:
        with self._lock:
            try:
                binding = self._fresh_identity()
            except ReconnectRequiredError:
                object.__setattr__(self._credentials, '_syllva_revoked', True)
                raise
            if self.binding_id is None or binding != self.binding_id:
                # The account behind the credential changed: no earlier proof survives.
                object.__setattr__(self._credentials, '_syllva_revoked', True)
                raise ReconnectRequiredError()
            self._generation += 1
            object.__setattr__(self._credentials, '_syllva_revoked', False)
            return WorkerEntryAttestation(
                attestor_id=self.attestor_id, role=self._purpose.value, scope=self._purpose.scope,
                generation=self._generation, binding_id=binding, issued_at=self._clock(),
            )


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
    source_reader: Any | None = None,
    study_note_block_port: Any | None = None,
    entry_attestor: Any | None = None,
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
        worker_payload = credentials.get_google_payload('GOOGLE_WORKER_CREDENTIALS_FILE')
        if worker_payload is None:
            raise ConfigurationError('GOOGLE_WORKER_CREDENTIALS_FILE payload is missing')
        service, binding, entry_attestor = google_worker_runtime(worker_payload, oauth_client=config.google_oauth)
        if entry_attestor is None:
            drive = GoogleDriveWorkerAdapter(service)
        else:
            try:
                drive = GoogleDriveWorkerAdapter(service, attestor=entry_attestor)
            except Exception:  # noqa: BLE001 - adapter init diagnostics never leave the OAuth path
                raise ReconnectRequiredError() from None
        from notion_client import Client

        notion = NotionAPIWorker(
            Client(auth=credentials['NOTION_WORKER_TOKEN'], notion_version='2025-09-03', timeout_ms=20_000)
        )
    # Startup gate first: a rejected personal-OAuth attestation leaves no local
    # state file or schema behind (cold start included).
    startup_attestation = entry_attestor.attest_entry() if entry_attestor is not None else None
    if state is None:
        state = SQLiteStateStore(state_path(config))
    worker = IntakeWorker(
        config,
        state,
        drive,
        notion,
        provider_account_binding_id=binding,
        semester=semester,
        entry_attestor=entry_attestor,
    )
    from uls.intake.composition import install_usage_range

    if source_reader is None and not injected_ports:
        from uls.adapters.drive.google import GoogleDriveReader
        from uls.state.reader import ReadOnlyState

        source_reader = GoogleDriveReader(service, ReadOnlyState(state.db_path))
    # The dynamically installed C5/C6 capabilities perform Notion reads during
    # installation, so the startup attestation taken above is the one context
    # both installers run under.
    enter = getattr(drive, "attested", None)
    startup_context: contextlib.AbstractContextManager[Any] = (
        enter(startup_attestation) if startup_attestation is not None and callable(enter) else contextlib.nullcontext()
    )
    with startup_context:
        install_usage_range(worker, config, state, notion, source_reader)
        from uls.intake.study_note_composition import install_study_notes

        install_study_notes(worker, config, state, notion, source_reader, block_port=study_note_block_port)
    return worker


def _retrieval_notion_sources(config: UlsConfig) -> tuple[dict[str, str] | None, str]:
    lane = config.retrieval.notion_lane
    semester = config.retrieval.semester
    if lane == 'legacy_global':
        if semester:
            raise ConfigurationError(
                'retrieval.semester must be empty for the legacy_global lane'
            )
        return None, ''
    if lane != 'semester_workspace' or not semester:
        raise ConfigurationError('semester_workspace retrieval requires an explicit semester')

    matches = [
        row for row in config.notion.semester_workspaces if row.semester == semester
    ]
    if len(matches) != 1:
        raise ConfigurationError(
            'semester_workspace retrieval must select exactly one Notion workspace'
        )
    workspace = matches[0]
    sources = {
        'courses': workspace.academic_courses_data_source_id,
        'sessions': workspace.sessions_data_source_id,
        'materials': workspace.materials_data_source_id,
    }
    if any(not value for value in sources.values()):
        raise ConfigurationError(
            'semester_workspace retrieval requires Courses, Sessions and Materials data sources'
        )
    if workspace.material_usage_data_source_id:
        sources['material_usage'] = workspace.material_usage_data_source_id
    return sources, semester


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
    mcp_payload = credentials.get_google_payload('GOOGLE_MCP_CREDENTIALS_FILE')
    if mcp_payload is None:
        raise ConfigurationError('GOOGLE_MCP_CREDENTIALS_FILE payload is missing')
    drive = GoogleDriveReader(google_service(mcp_payload, read_only=True, oauth_client=config.google_oauth), state)
    direct_sources, expected_semester = _retrieval_notion_sources(config)
    notion = NotionAPIReader(
        Client(
            auth=credentials['NOTION_MCP_TOKEN'],
            notion_version='2025-09-03',
            timeout_ms=20_000,
        ),
        config.notion,
        data_source_ids=direct_sources,
        expected_semester=expected_semester,
    )
    return RetrievalEngine(notion, drive, state, MemoryEphemeralStore(), config,
                           source_binding_resolver=ValidatedSourceBindingResolver(state),
                           github_reader=GitHubAPIReader(credentials.get('GITHUB_READ_TOKEN', '')))


def build_study_note_submission_server(config: UlsConfig) -> Any:
    """Local draft inbox only: never resolve or pass provider credentials."""
    if config.study_notes.enabled is not True:
        raise ConfigurationError('study_notes.enabled must be explicitly true')
    from dataclasses import asdict

    from uls.study_notes.config import StudyNoteConfig
    from uls.study_notes.core import StudyNoteSubmissionCore
    from uls.study_notes.mcp import StudyNoteMCP
    from uls.study_notes.store import StudyNoteStore

    cfg = StudyNoteConfig(**asdict(config.study_notes))
    store = StudyNoteStore(state_path(config).parent / 'study_notes.sqlite3')
    try:
        core = StudyNoteSubmissionCore(store, config=cfg)
        return StudyNoteMCP(core, caller_context=cfg.local_caller_id)
    except BaseException:
        store.close()
        raise
