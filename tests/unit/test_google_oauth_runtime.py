"""Runtime personal-OAuth dispatch, attestor and bounded token transport (P2 plan §5)."""
from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest

from uls.config import google_oauth as oauth
from uls.config.credentials import GoogleCredentialPayload
from uls.config.errors import ConfigurationError
from uls.intake.attestation import ReconnectRequiredError, WorkerEntryAttestation
from uls.intake.identity import provider_binding_id
from uls.runtime import (
    BoundedTokenRequest,
    GoogleOAuthWorkerAttestor,
    _load_google_credentials,
    google_worker_runtime,
    google_worker_service,
)

pytestmark = pytest.mark.unit

CLIENT = oauth.GoogleOAuthClient("synthetic-client.apps.googleusercontent.com", "synthetic-secret")


def _oauth_info(scope=oauth.WORKER_SCOPE, **overrides):
    info = {
        "type": "authorized_user", "client_id": CLIENT.client_id, "client_secret": CLIENT.client_secret,
        "refresh_token": "synthetic-refresh", "token_uri": oauth.TOKEN_URI, "scopes": [scope],
    }
    info.update(overrides)
    return info


def _payload(info, name="GOOGLE_WORKER_CREDENTIALS_FILE"):
    return GoogleCredentialPayload(info=MappingProxyType(dict(info)), source_name=name)


class _FakeCredentials:
    def __init__(self, info, scopes):
        self.info = info
        self.scopes = scopes
        self.granted_scopes = None
        self.client_id = info.get("client_id")
        self.refreshes = []
        self.refresh_error = None
        self.granted_after_refresh = list(scopes)

    def refresh(self, request):
        self.refreshes.append(request)
        if self.refresh_error is not None:
            raise self.refresh_error
        self.granted_scopes = list(self.granted_after_refresh)


class _FakeService:
    def __init__(self, permission_id="owner-1"):
        self.permission_id = permission_id
        self.about_calls = 0

    def about(self):
        service = self

        class _About:
            def get(self, fields):
                assert fields == "user(permissionId)"
                service.about_calls += 1
                return SimpleNamespace(execute=lambda: {"user": {"permissionId": service.permission_id}})
        return _About()

    def files(self):
        return SimpleNamespace()


@pytest.fixture
def fake_google(monkeypatch):
    created = []
    import google.auth

    def load(info, scopes=None, **kwargs):
        credentials = _FakeCredentials(info, scopes)
        created.append(credentials)
        return credentials, None

    monkeypatch.setattr(google.auth, "load_credentials_from_dict", load)
    services = []

    def build(name, version, credentials, cache_discovery):
        service = _FakeService()
        service.credentials = credentials
        services.append(service)
        return service

    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", build)
    return SimpleNamespace(created=created, services=services)


def test_service_account_payload_is_untouched_by_oauth_dispatch(fake_google):
    sa = {"type": "service_account", "client_email": "x@y", "private_key_id": "k", "client_id": "sa-client"}
    credentials = _load_google_credentials(_payload(sa), read_only=False)
    assert credentials.info == sa and credentials.scopes == [oauth.WORKER_SCOPE]
    service, binding, attestor = google_worker_runtime(_payload(sa))
    assert attestor is None
    assert binding == provider_binding_id("google_drive", "owner-1", "sa-client")
    assert service.about_calls == 1
    assert google_worker_service(_payload(sa))[1] == binding


def test_authorized_user_requires_configured_client_and_exact_scope(fake_google):
    with pytest.raises(ReconnectRequiredError):
        _load_google_credentials(_payload(_oauth_info()), read_only=False)
    with pytest.raises(ReconnectRequiredError):
        _load_google_credentials(_payload(_oauth_info()), read_only=True, oauth_client=CLIENT)
    with pytest.raises(ReconnectRequiredError):
        _load_google_credentials(_payload(_oauth_info(client_id="other")), read_only=False, oauth_client=CLIENT)
    assert fake_google.created == []
    credentials = _load_google_credentials(_payload(_oauth_info()), read_only=False, oauth_client=CLIENT)
    assert credentials.scopes == [oauth.WORKER_SCOPE]
    read_only = _load_google_credentials(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)
    assert read_only.scopes == [oauth.MCP_SCOPE]


def test_authorized_user_runtime_binds_fresh_identity_and_installs_attestor(fake_google):
    service, binding, attestor = google_worker_runtime(_payload(_oauth_info()), oauth_client=CLIENT)
    assert isinstance(attestor, GoogleOAuthWorkerAttestor)
    assert binding == provider_binding_id("google_drive", "owner-1", CLIENT.client_id)
    assert attestor.binding_id == binding
    credentials = fake_google.created[-1]
    # One proof at load time, one at bind: both through the bounded token transport.
    assert len(credentials.refreshes) == 2 and all(isinstance(r, BoundedTokenRequest) for r in credentials.refreshes)
    assert service.about_calls == 1
    attestation = attestor.attest_entry()
    assert isinstance(attestation, WorkerEntryAttestation)
    assert attestation.binding_id == binding and attestation.generation == 1
    assert attestation.scope == oauth.WORKER_SCOPE and attestation.role == "worker"
    assert attestation.attestor_id == attestor.attestor_id
    assert "synthetic-refresh" not in repr(attestation) and CLIENT.client_secret not in repr(attestation)
    assert "owner-1" not in repr(attestation)
    assert attestor.attest_entry().generation == 2
    assert len(credentials.refreshes) == 4 and service.about_calls == 3
    assert attestor.role == "worker" and attestor.scope == oauth.WORKER_SCOPE and attestor.generation == 2


def test_oauth_sdk_build_failures_become_reconnect_without_diagnostics(fake_google, monkeypatch):
    import googleapiclient.discovery

    def broken(name, version, credentials, cache_discovery):
        raise RuntimeError("discovery failed; bearer=synthetic-access")
    monkeypatch.setattr(googleapiclient.discovery, "build", broken)
    from uls.runtime import google_service
    with pytest.raises(ReconnectRequiredError) as error:
        google_service(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)
    assert "synthetic-access" not in str(error.value)
    with pytest.raises(ReconnectRequiredError):
        google_worker_runtime(_payload(_oauth_info()), oauth_client=CLIENT)
    sa = {"type": "service_account", "client_email": "x@y", "private_key_id": "k", "client_id": "sa-client"}
    with pytest.raises(RuntimeError, match="discovery failed"):
        google_service(_payload(sa), read_only=False)  # service-account flow is unchanged


def test_sdk_internal_refresh_is_bound_to_the_bounded_transport_and_grant_proof(fake_google):
    credentials = _load_google_credentials(_payload(_oauth_info()), read_only=False, oauth_client=CLIENT)
    assert len(credentials.refreshes) == 1 and isinstance(credentials.refreshes[0], BoundedTokenRequest)

    class _SdkRequest:  # what google-auth would hand to credentials.refresh() on expiry/401
        pass
    credentials.refresh(_SdkRequest())
    assert len(credentials.refreshes) == 2 and all(isinstance(r, BoundedTokenRequest) for r in credentials.refreshes)
    credentials.granted_after_refresh = [oauth.WORKER_SCOPE, "openid"]
    with pytest.raises(ReconnectRequiredError):
        credentials.refresh(_SdkRequest())
    credentials.granted_after_refresh = [oauth.WORKER_SCOPE]
    credentials.client_id = "rotated"
    with pytest.raises(ReconnectRequiredError):
        credentials.refresh(_SdkRequest())
    credentials.client_id = CLIENT.client_id
    credentials.refresh_error = RuntimeError("invalid_grant refresh_token=synthetic-refresh")
    with pytest.raises(ReconnectRequiredError) as error:
        credentials.refresh(_SdkRequest())
    assert "synthetic-refresh" not in str(error.value)


def test_bound_refresh_serializes_per_credential_and_verifies_each_grant(fake_google):
    import threading
    import time as _time

    credentials = _load_google_credentials(_payload(_oauth_info()), read_only=False, oauth_client=CLIENT)
    base_refresh = _FakeCredentials.refresh
    state = {"inflight": 0, "max_inflight": 0, "calls": 0}
    guard = threading.Lock()
    # Call 1 reports a too-wide grant, call 2 the exact grant; both overlap in time.
    plan = {1: [oauth.WORKER_SCOPE, "openid"], 2: [oauth.WORKER_SCOPE]}

    def interleaved(self, request):
        with guard:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            state["calls"] += 1
            call = state["calls"]
        _time.sleep(0.05)
        self.granted_after_refresh = plan[call]
        base_refresh(self, request)
        with guard:
            state["inflight"] -= 1
    _FakeCredentials.refresh = interleaved
    try:
        outcomes: dict[str, object] = {}

        def run(name):
            try:
                credentials.refresh(object())
                outcomes[name] = "ok"
            except ReconnectRequiredError:
                outcomes[name] = "reconnect"
        threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    finally:
        _FakeCredentials.refresh = base_refresh
    assert state["max_inflight"] == 1
    assert sorted(outcomes.values()) == ["ok", "reconnect"]


@pytest.mark.parametrize("fault", ["grant", "client", "invalid_grant"])
def test_refresh_rejection_after_issuance_revokes_the_current_entry_proof(fake_google, fault):
    from uls.adapters.drive.worker import GoogleDriveWorkerAdapter
    from uls.intake.attestation import attestation_matches

    service, _binding, attestor = google_worker_runtime(_payload(_oauth_info()), oauth_client=CLIENT)
    credentials = fake_google.created[-1]
    adapter = GoogleDriveWorkerAdapter(service, attestor=attestor)
    attestation = attestor.attest_entry()
    assert attestation_matches(attestor, attestation) and not attestor.revoked
    if fault == "grant":
        credentials.granted_after_refresh = [oauth.WORKER_SCOPE, "openid"]
    elif fault == "client":
        credentials.client_id = "rotated"
    else:
        credentials.refresh_error = RuntimeError("invalid_grant")
    with pytest.raises(ReconnectRequiredError):
        credentials.refresh(object())  # the SDK's own re-authentication on expiry/401
    assert attestor.revoked and not attestation_matches(attestor, attestation)
    with pytest.raises(ReconnectRequiredError), adapter.attested(attestation):
        pass
    adapter._context.current = attestation  # even an already-entered context is refused from now on
    with pytest.raises(ReconnectRequiredError):
        adapter._require_attested()
    assert service.about_calls == 2  # bind + one entry; no provider call after the rejection
    # Only a new successful entry proof clears the revocation.
    credentials.granted_after_refresh = [oauth.WORKER_SCOPE]
    credentials.client_id = CLIENT.client_id
    credentials.refresh_error = None
    fresh = attestor.attest_entry()
    assert not attestor.revoked and attestation_matches(attestor, fresh)
    assert not attestation_matches(attestor, attestation)  # the old generation is not revived


def test_oauth_sdk_import_and_rebind_failures_are_fixed_codes(fake_google, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", None)  # import fails
    from uls.runtime import _build_drive, google_service
    with pytest.raises(ReconnectRequiredError):
        _build_drive(object(), oauth=True)
    with pytest.raises(ImportError):
        _build_drive(object(), oauth=False)  # service-account path keeps the raw import failure
    monkeypatch.setitem(sys.modules, "google.auth", None)
    with pytest.raises(ReconnectRequiredError):
        google_service(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)
    monkeypatch.delitem(sys.modules, "google.auth")
    import google.auth

    class _Unbindable(_FakeCredentials):
        def __setattr__(self, name, value):
            if name == "__class__":
                raise TypeError("__class__ assignment forbidden; refresh_token=synthetic")
            super().__setattr__(name, value)
    monkeypatch.setattr(google.auth, "load_credentials_from_dict", lambda info, scopes=None, **kw: (_Unbindable(info, scopes), None))
    with pytest.raises(ReconnectRequiredError) as error:
        _load_google_credentials(_payload(_oauth_info()), read_only=False, oauth_client=CLIENT)
    assert "synthetic" not in str(error.value)


def test_credential_type_dispatch_is_closed_and_oauth_loader_errors_are_fixed(fake_google, monkeypatch):
    for bad in ({"type": []}, {"type": "unknown"}, {"type": None}, {"client_email": "x"}):
        with pytest.raises(ConfigurationError, match="unsupported Google credential type"):
            _load_google_credentials(_payload(bad), read_only=False, oauth_client=CLIENT)
    assert fake_google.created == []
    import google.auth

    def broken(info, scopes=None, **kwargs):
        raise ValueError("loader exploded with refresh_token=synthetic-refresh")
    monkeypatch.setattr(google.auth, "load_credentials_from_dict", broken)
    with pytest.raises(ReconnectRequiredError) as error:
        _load_google_credentials(_payload(_oauth_info()), read_only=False, oauth_client=CLIENT)
    assert "synthetic-refresh" not in str(error.value)


def test_authorized_user_load_requires_provider_reported_grant(fake_google, monkeypatch):
    import google.auth

    class _NoGrant(_FakeCredentials):
        def refresh(self, request):
            self.refreshes.append(request)
            self.granted_scopes = None  # provider reported nothing

    def load(info, scopes=None, **kwargs):
        credentials = _NoGrant(info, scopes)
        fake_google.created.append(credentials)
        return credentials, None
    monkeypatch.setattr(google.auth, "load_credentials_from_dict", load)
    with pytest.raises(ReconnectRequiredError):
        _load_google_credentials(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)
    with pytest.raises(ReconnectRequiredError):
        google_worker_runtime(_payload(_oauth_info()), oauth_client=CLIENT)


def test_mcp_read_only_load_proves_exact_grant_and_client(fake_google):
    credentials = _load_google_credentials(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)
    assert len(credentials.refreshes) == 1 and credentials.granted_scopes == [oauth.MCP_SCOPE]
    wide = _FakeCredentials(_oauth_info(oauth.MCP_SCOPE), [oauth.MCP_SCOPE])
    wide.granted_after_refresh = [oauth.MCP_SCOPE, oauth.WORKER_SCOPE]
    import google.auth
    google.auth.load_credentials_from_dict = lambda info, scopes=None, **kw: (wide, None)
    with pytest.raises(ReconnectRequiredError):
        _load_google_credentials(_payload(_oauth_info(oauth.MCP_SCOPE)), read_only=True, oauth_client=CLIENT)


def test_google_worker_service_refuses_oauth_payload_and_legacy_worker_refuses_before_effects(fake_google, tmp_path):
    with pytest.raises(ReconnectRequiredError):
        google_worker_service(_payload(_oauth_info()), oauth_client=CLIENT)
    from uls.config.credentials import ResolvedCredentials
    from uls.config.schema import UlsConfig
    from uls.worker import build_worker

    config = UlsConfig()
    config.system.workspace_dir = str(tmp_path)
    config.google_oauth = CLIENT
    credentials = ResolvedCredentials(
        {"GOOGLE_WORKER_CREDENTIALS_FILE": "x", "NOTION_WORKER_TOKEN": "t"},
        {"GOOGLE_WORKER_CREDENTIALS_FILE": _payload(_oauth_info())})
    with pytest.raises(ConfigurationError, match="personal Google OAuth requires the semester intake worker"):
        build_worker(config, credentials)
    assert fake_google.created == [] and not (tmp_path / "state.sqlite3").exists()


def test_attestor_serializes_concurrent_entries_so_a_failed_refresh_is_never_masked():
    import threading
    import time as _time

    credentials, _service, attestor = _attestor()
    attestor.bind()
    state = {"inflight": 0, "max_inflight": 0, "calls": 0}
    guard = threading.Lock()

    class _SlowCredentials:
        def refresh(self, request=None):
            with guard:
                state["inflight"] += 1
                state["max_inflight"] = max(state["max_inflight"], state["inflight"])
                state["calls"] += 1
                first = state["calls"] == 1
            _time.sleep(0.05)
            try:
                if first:
                    credentials.granted_after_refresh = []  # provider reports no grant for the first caller
                    try:
                        credentials.refresh(request)
                    finally:
                        credentials.granted_after_refresh = [oauth.WORKER_SCOPE]
                    raise RuntimeError("invalid_grant")
                credentials.refresh(request)
            finally:
                with guard:
                    state["inflight"] -= 1

        def __getattr__(self, name):
            return getattr(credentials, name)
    attestor._credentials = _SlowCredentials()
    outcomes: dict[str, object] = {}

    def run(name):
        try:
            outcomes[name] = attestor.attest_entry().generation
        except ReconnectRequiredError:
            outcomes[name] = "reconnect"
    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert state["max_inflight"] == 1, "refresh/proof/generation must be serialized under the attestor lock"
    assert sorted(map(str, outcomes.values())) == ["1", "reconnect"]
    assert attestor.generation == 1


def _attestor(**kwargs):
    credentials = _FakeCredentials(_oauth_info(), [oauth.WORKER_SCOPE])
    service = _FakeService()
    attestor = GoogleOAuthWorkerAttestor(credentials, service, client=CLIENT, purpose=oauth.GoogleOAuthPurpose.WORKER,
                                         request_factory=lambda: "fake-request", **kwargs)
    return credentials, service, attestor


def test_attestor_fails_closed_on_refresh_error():
    credentials, _service, attestor = _attestor()
    attestor.bind()
    credentials.refresh_error = RuntimeError("invalid_grant: token revoked; refresh=synthetic-refresh")
    with pytest.raises(ReconnectRequiredError) as error:
        attestor.attest_entry()
    assert "synthetic-refresh" not in str(error.value) and error.value.code == "RECONNECT_REQUIRED"


@pytest.mark.parametrize("granted", [
    [oauth.WORKER_SCOPE, "openid"], [oauth.MCP_SCOPE], [], "https://www.googleapis.com/auth/drive.file",
])
def test_attestor_requires_exact_granted_scope(granted):
    credentials, _service, attestor = _attestor()
    attestor.bind()
    credentials.granted_after_refresh = granted if isinstance(granted, list) else [granted]
    with pytest.raises(ReconnectRequiredError):
        attestor.attest_entry()


def test_attestor_requires_configured_client_and_same_account():
    credentials, service, attestor = _attestor()
    attestor.bind()
    credentials.client_id = "rotated-client"
    with pytest.raises(ReconnectRequiredError):
        attestor.attest_entry()
    credentials.client_id = CLIENT.client_id
    service.permission_id = "owner-2"
    with pytest.raises(ReconnectRequiredError):
        attestor.attest_entry()
    service.permission_id = "owner-1"
    assert attestor.attest_entry().generation == 1


def test_account_change_at_attest_entry_revokes_the_previous_proof():
    from uls.intake.attestation import attestation_matches

    _credentials, service, attestor = _attestor()
    attestor.bind()
    previous = attestor.attest_entry()
    service.permission_id = "owner-2"
    with pytest.raises(ReconnectRequiredError):
        attestor.attest_entry()
    assert attestor.revoked and not attestation_matches(attestor, previous)
    service.permission_id = "owner-1"
    fresh = attestor.attest_entry()
    assert not attestor.revoked and attestation_matches(attestor, fresh) and not attestation_matches(attestor, previous)


def test_attestor_entry_before_bind_is_rejected():
    _credentials, _service, attestor = _attestor()
    with pytest.raises(ReconnectRequiredError):
        attestor.attest_entry()


def test_about_failure_becomes_reconnect_not_configuration_error():
    _credentials, service, attestor = _attestor()
    attestor.bind()

    def broken_about():
        raise RuntimeError("about failed with token=synthetic-access")
    service.about = broken_about
    with pytest.raises(ReconnectRequiredError) as error:
        attestor.attest_entry()
    assert not isinstance(error.value, ConfigurationError)
    assert "synthetic-access" not in str(error.value)


class _FakeConnection:
    def __init__(self, host, timeout, *, status=200, body=b'{"access_token":"x","scope":"s"}', fail=False):
        self.host, self.timeout = host, timeout
        self.status, self.body, self.fail = status, body, fail
        self.requests = []
        self.closed = False

    def request(self, method, target, body=None, headers=None):
        if self.fail:
            raise OSError("connection reset while sending refresh_token=synthetic")
        self.requests.append((method, target, body, dict(headers or {})))

    def getresponse(self):
        body = self.body

        class _Response:
            status = self.status

            def read(self, limit):
                return body[:limit]
        return _Response()

    def close(self):
        self.closed = True


def test_bounded_token_request_allows_one_exact_post_and_bounds_bytes():
    connections = []

    def factory(host, timeout):
        connection = _FakeConnection(host, timeout)
        connections.append(connection)
        return connection

    request = BoundedTokenRequest(connection_factory=factory)
    response = request(oauth.TOKEN_URI, method="POST", body=b"grant_type=refresh_token", headers={"Accept-Encoding": "gzip"},
                       timeout=120)
    assert response.status == 200 and response.data.startswith(b"{")
    connection = connections[0]
    assert connection.host == "oauth2.googleapis.com" and connection.timeout == 10.0 and connection.closed
    assert connection.requests[0][3]["Accept-Encoding"] == "identity"
    with pytest.raises(ReconnectRequiredError):
        request(oauth.TOKEN_URI, method="POST", body=b"again")
    with pytest.raises(ReconnectRequiredError):
        BoundedTokenRequest(connection_factory=factory)("https://www.googleapis.com/drive/v3/about", method="GET")
    with pytest.raises(ReconnectRequiredError):
        BoundedTokenRequest(connection_factory=factory)(oauth.TOKEN_URI, method="POST", stream=True)
    big = BoundedTokenRequest(connection_factory=lambda host, timeout: _FakeConnection(host, timeout, body=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(ReconnectRequiredError):
        big(oauth.TOKEN_URI, method="POST")
    failing = BoundedTokenRequest(connection_factory=lambda host, timeout: _FakeConnection(host, timeout, fail=True))
    with pytest.raises(ReconnectRequiredError) as error:
        failing(oauth.TOKEN_URI, method="POST")
    assert "synthetic" not in str(error.value)
