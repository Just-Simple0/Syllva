"""GoogleOAuthFlowService state machine with the provider-free fake (P2 plan §2/§4)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from tests.contract._settings_support import FakeClock, write_config
from tests.contract.test_settings_credential_service import service

from uls.config import google_oauth as oauth
from uls.settings.config_service import SettingsServiceError
from uls.settings.credential_roles import ROLES
from uls.settings.google_oauth import (
    FLOW_TTL_SECONDS,
    FakeGoogleOAuthProvider,
    GoogleOAuthFlowService,
)
from uls.settings.security import SessionSecurity

pytestmark = pytest.mark.contract

CLIENT = {"client_id": "synthetic-client.apps.googleusercontent.com", "client_secret": "synthetic-client-secret"}


class Harness:
    def __init__(self, tmp_path: Path, *, oauth_config: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        write_config(tmp_path, **({"google_oauth": dict(CLIENT)} if oauth_config else {}))
        self.credentials = service(tmp_path)
        self.clock = FakeClock()
        self.token, self.security = SessionSecurity.issue(clock=self.clock)
        self.provider = FakeGoogleOAuthProvider()
        self.credentials.oauth_verifier = self.provider.verifier
        self.service = GoogleOAuthFlowService(
            self.credentials, self.credentials.config, authority="127.0.0.1:8765", prefix="prefix-ABCDEFGHIJKLMNOPQRSTUV",
            security=self.security, opener=self.provider.opener, exchanger=self.provider.exchanger,
            account_reader=self.provider.account_reader, clock=self.clock,
        )
        self.provider.service = self.service

    def sign_in(self) -> str:
        handle = self.security.consume_bootstrap(self.token)
        assert handle is not None
        return handle

    @property
    def generation(self) -> str:
        return self.credentials.config.load().generation


def _code(error: pytest.ExceptionInfo) -> str:
    return error.value.code


def test_begin_callback_commit_completes_without_returning_any_secret(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    assert set(begun) == {"flow_id", "purpose", "status", "error_code"} and begun["purpose"] == "worker"
    # The fake provider completes the redirect synchronously inside begin().
    status = h.service.status("worker", begun["flow_id"])
    assert status["status"] == "awaiting_commit" and status["error_code"] is None
    [url] = h.provider.opened
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(oauth.AUTH_URI + "?")
    assert query["scope"] == [oauth.WORKER_SCOPE] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [h.service.redirect_uri] and query["prompt"] == ["consent"]
    assert query["client_id"] == [CLIENT["client_id"]] and "client_secret" not in query
    assert len(query["state"][0]) == 43 and len(query["code_challenge"][0]) == 43
    assert query["state"][0] not in json.dumps(status)
    result = h.service.commit("worker", {"flow_id": begun["flow_id"], "generation": generation, "replace": False})
    assert result["status"] == "complete" and result["code"] == "VERIFIED"
    assert "fake-refresh" not in json.dumps(result) and "fake-access" not in json.dumps(result)
    assert h.credentials.stores.read(ROLES["google-worker"]) is not None
    assert h.service.status("worker", begun["flow_id"])["status"] == "complete"
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("worker", {"flow_id": begun["flow_id"], "generation": result["config_generation"], "replace": False})
    assert _code(error) == "FLOW_NOT_FOUND"


def test_begin_requires_live_session_exact_body_generation_and_readiness(tmp_path):
    h = Harness(tmp_path)
    generation = h.generation
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": generation, "replace": False})
    assert _code(error) == "SESSION_EXPIRED"
    h.sign_in()
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("picker", {"generation": generation, "replace": False})
    assert _code(error) == "NOT_FOUND"
    for body in ({"generation": generation}, {"generation": generation, "replace": "no"},
                 {"generation": generation, "replace": False, "extra": 1}):
        with pytest.raises(SettingsServiceError) as error:
            h.service.begin("worker", body)
        assert _code(error) == "INVALID_REQUEST"
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": "stale", "replace": False})
    assert _code(error) == "CONFIGURATION_CHANGED"
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": generation, "replace": True})
    assert _code(error) == "NOT_CONFIGURED"
    assert h.provider.opened == [] and h.provider.exchanges == 0
    unready = Harness(tmp_path / "unready", oauth_config=False)
    unready.sign_in()
    with pytest.raises(SettingsServiceError) as error:
        unready.service.begin("worker", {"generation": unready.generation, "replace": False})
    assert _code(error) == "OAUTH_APP_NOT_READY"
    h.credentials.config.path.chmod(0o644)
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": generation, "replace": False})
    assert _code(error) == "OAUTH_APP_NOT_READY"
    assert h.provider.opened == []


def test_flow_busy_per_purpose_and_two_purposes_in_parallel(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    generation = h.generation
    first = h.service.begin("mcp", {"generation": generation, "replace": False})
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("mcp", {"generation": generation, "replace": False})
    assert _code(error) == "FLOW_BUSY"
    assert h.service.status("mcp", first["flow_id"])["status"] == "pending"
    second = h.service.begin("worker", {"generation": generation, "replace": False})
    assert second["flow_id"] != first["flow_id"]
    assert len(h.provider.opened) == 2
    cancelled = h.service.cancel("mcp", {"flow_id": first["flow_id"], "generation": generation})
    assert cancelled["status"] == "cancelled" and cancelled["error_code"] == "FLOW_CANCELLED"
    third = h.service.begin("mcp", {"generation": generation, "replace": False})
    assert third["flow_id"] != first["flow_id"]
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("mcp", {"flow_id": first["flow_id"], "generation": generation, "replace": False})
    assert _code(error) == "FLOW_NOT_FOUND"


def test_callback_state_is_one_use_and_rejects_foreign_or_malformed_input(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[0]).query)["state"][0]
    for query in ({"state": "x" * 43, "code": "c"}, {"state": state}, {"state": state, "code": "c", "error": "x"},
                  {"state": state[:-1], "code": "c"}, {"code": "c"}, {"state": state, "scope": "s"}):
        h.service.callback(query)
    assert h.service.status("worker", begun["flow_id"])["status"] == "pending"
    assert h.provider.exchanges == 0
    h.service.callback({"state": state, "code": "x" * 4097})
    assert h.service.status("worker", begun["flow_id"]) == {
        "flow_id": begun["flow_id"], "purpose": "worker", "status": "failed", "error_code": "FLOW_STATE_REJECTED"}
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[-1]).query)["state"][0]
    h.service.callback({"state": state, "code": "good-code"})
    assert h.service.status("worker", begun["flow_id"])["status"] == "awaiting_commit"
    h.service.callback({"state": state, "code": "replayed-code"})
    assert h.service.status("worker", begun["flow_id"])["status"] == "awaiting_commit"
    assert h.provider.exchanges == 1


def test_callback_ignores_unrecognised_google_response_parameters(tmp_path):
    """Google appends scope/authuser/prompt to the redirect (RFC 6749 §4.1.2: ignore unknown params)."""
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[-1]).query)["state"][0]
    h.service.callback({"state": state, "code": "good-code", "scope": oauth.WORKER_SCOPE,
                        "authuser": "0", "prompt": "consent"})
    assert h.service.status("worker", begun["flow_id"])["status"] == "awaiting_commit"
    assert h.provider.exchanges == 1
    # The redirect's scope parameter is not trusted: the token response decides.
    begun = h.service.begin("mcp", {"generation": generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[-1]).query)["state"][0]
    h.service.callback({"state": state, "error": "access_denied", "scope": oauth.MCP_SCOPE, "authuser": "0"})
    assert h.service.status("mcp", begun["flow_id"])["error_code"] == "OAUTH_ACCESS_DENIED"


@pytest.mark.parametrize("error_value, expected", [
    ("access_denied", "OAUTH_ACCESS_DENIED"),
    ("x" * 64, "FLOW_STATE_REJECTED"),
    ("x" * 65, "FLOW_STATE_REJECTED"),
    ("거부됨", "FLOW_STATE_REJECTED"),
])
def test_callback_error_values_are_bounded_ascii(tmp_path, error_value, expected):
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    begun = h.service.begin("worker", {"generation": h.generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[-1]).query)["state"][0]
    h.service.callback({"state": state, "error": error_value})
    status = h.service.status("worker", begun["flow_id"])
    assert status["error_code"] == expected
    assert status["status"] == ("denied" if expected == "OAUTH_ACCESS_DENIED" else "failed")
    assert h.provider.exchanges == 0


def test_begin_without_browser_opener_fails_closed_without_exposing_the_url(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    h.service.opener = None
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": h.generation, "replace": False})
    assert _code(error) == "BROWSER_REQUIRED"
    assert h.provider.opened == [] and h.service._flows == {}


class _Conn:
    def __init__(self, host, timeout, *, status=200, body=b'{"access_token":"a","refresh_token":"r","scope":"s","token_type":"Bearer"}', raise_=None):
        self.host, self.timeout, self.status, self.body, self.raise_ = host, timeout, status, body, raise_
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        if self.raise_ is not None:
            raise self.raise_
        self.requests.append((method, path))

    def getresponse(self):
        body = self.body
        status = self.status

        class _R:
            def read(self, limit):
                return body[:limit]
        r = _R(); r.status = status
        return r

    def close(self):
        self.closed = True


def test_bounded_https_transport_boundaries_are_fixed_codes():
    from uls.settings.google_oauth import (
        GoogleAccountReader,
        GoogleGrantVerifier,
        GoogleTokenExchanger,
    )

    made: list[_Conn] = []

    def factory(**overrides):
        def make(host, timeout):
            conn = _Conn(host, timeout, **overrides)
            made.append(conn)
            return conn
        return make
    client = oauth.GoogleOAuthClient("id", "secret")
    ok = GoogleTokenExchanger(connection_factory=factory())(client, "code", "http://127.0.0.1:1/cb", "v")
    assert ok["refresh_token"] == "r" and made[-1].host == "oauth2.googleapis.com" and made[-1].timeout == 10.0
    assert made[-1].requests == [("POST", "/token")] and made[-1].closed
    for overrides, code in (
        ({"status": 302}, "PROVIDER_UNAVAILABLE"),          # redirects are never followed
        ({"status": 400}, "FLOW_STATE_REJECTED"),
        ({"body": b"x" * (1024 * 1024 + 1)}, "PROVIDER_UNAVAILABLE"),
        ({"body": b"not json"}, "PROVIDER_UNAVAILABLE"),
        ({"raise_": TimeoutError()}, "TIMEOUT"),
        ({"raise_": OSError("reset with code=secret")}, "PROVIDER_UNAVAILABLE"),
    ):
        with pytest.raises(SettingsServiceError) as error:
            GoogleTokenExchanger(connection_factory=factory(**overrides))(client, "code", "u", "v")
        assert error.value.code == code and "secret" not in error.value.message
        assert len(made[-1].requests) <= 1
    reader = GoogleAccountReader(connection_factory=factory(body=b'{"user": {"permissionId": "p1"}}'))
    assert reader("tok") == "p1" and made[-1].host == "www.googleapis.com"
    with pytest.raises(SettingsServiceError) as error:
        GoogleAccountReader(connection_factory=factory(status=401))("tok")
    assert error.value.code == "RECONNECT_REQUIRED"
    verifier = GoogleGrantVerifier(connection_factory=factory())
    credential = oauth.AuthorizedUserCredential(purpose=oauth.GoogleOAuthPurpose.MCP, client_id="id", client_secret="secret",
                                                refresh_token="rt", scope=oauth.MCP_SCOPE)
    verifier.account_reader = lambda token: "p1"
    assert verifier(credential) == ("s", "p1")
    with pytest.raises(SettingsServiceError) as error:
        GoogleGrantVerifier(connection_factory=factory(status=400))(credential)
    assert error.value.code == "RECONNECT_REQUIRED"


def test_denied_consent_wrong_scope_and_identity_failures_are_fixed_codes(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    h.provider.deny_next = True
    denied = h.service.begin("worker", {"generation": generation, "replace": False})
    assert h.service.status("worker", denied["flow_id"]) == {
        "flow_id": denied["flow_id"], "purpose": "worker", "status": "denied", "error_code": "OAUTH_ACCESS_DENIED"}
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("worker", {"flow_id": denied["flow_id"], "generation": generation, "replace": False})
    assert _code(error) == "OAUTH_ACCESS_DENIED"
    h.provider.scope_override = oauth.MCP_SCOPE
    narrow = h.service.begin("worker", {"generation": generation, "replace": False})
    assert h.service.status("worker", narrow["flow_id"])["error_code"] == "OAUTH_GRANT_MISMATCH"
    h.provider.scope_override = oauth.WORKER_SCOPE + " openid"
    wide = h.service.begin("worker", {"generation": generation, "replace": False})
    assert h.service.status("worker", wide["flow_id"])["error_code"] == "OAUTH_GRANT_MISMATCH"
    h.provider.scope_override = None
    h.service.account_reader = lambda token: (_ for _ in ()).throw(RuntimeError("token=" + token))
    broken = h.service.begin("worker", {"generation": generation, "replace": False})
    assert h.service.status("worker", broken["flow_id"])["error_code"] == "RECONNECT_REQUIRED"
    assert h.credentials.stores.read(ROLES["google-worker"]) is None


def test_commit_revalidates_generation_replace_session_and_flow_state(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    flow_id = begun["flow_id"]
    for body, code in ((
        {"flow_id": flow_id, "generation": "stale", "replace": False}, "CONFIGURATION_CHANGED"), (
        {"flow_id": flow_id, "generation": generation, "replace": True}, "INVALID_REQUEST"), (
        {"flow_id": "0" * 32, "generation": generation, "replace": False}, "FLOW_NOT_FOUND"), (
        {"flow_id": flow_id, "generation": generation}, "INVALID_REQUEST")):
        with pytest.raises(SettingsServiceError) as error:
            h.service.commit("worker", body)
        assert _code(error) == code
    assert h.service.status("worker", flow_id)["status"] == "awaiting_commit"
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("mcp", {"flow_id": flow_id, "generation": generation, "replace": False})
    assert _code(error) == "FLOW_NOT_FOUND"
    # A new session epoch (close + re-bootstrap) can never commit an older flow.
    h.security.close(None) or h.security.replace()
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("worker", {"flow_id": flow_id, "generation": generation, "replace": False})
    assert _code(error) == "SESSION_EXPIRED"
    assert h.credentials.stores.read(ROLES["google-worker"]) is None


def test_session_epoch_mismatch_fails_the_flow_at_callback(tmp_path):
    h = Harness(tmp_path)
    handle = h.sign_in()
    h.provider.auto_callback = False
    begun = h.service.begin("worker", {"generation": h.generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[0]).query)["state"][0]
    assert h.security.close(handle)
    h.service.callback({"state": state, "code": "late-code"})
    assert h.service.status("worker", begun["flow_id"])["error_code"] == "FLOW_STATE_REJECTED"
    assert h.provider.exchanges == 0


def test_first_callback_after_ttl_preserves_the_expired_verdict(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    begun = h.service.begin("worker", {"generation": h.generation, "replace": False})
    state = parse_qs(urlsplit(h.provider.opened[0]).query)["state"][0]
    h.clock.now += FLOW_TTL_SECONDS + 1
    h.service.callback({"state": state, "code": "late-but-valid"})  # no status() call before this
    assert h.service.status("worker", begun["flow_id"]) == {
        "flow_id": begun["flow_id"], "purpose": "worker", "status": "expired", "error_code": "FLOW_EXPIRED"}
    assert h.provider.exchanges == 0 and h.credentials.stores.read(ROLES["google-worker"]) is None


def test_flow_ttl_expires_only_pre_commit_states(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    h.provider.auto_callback = False
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    h.clock.now += FLOW_TTL_SECONDS + 1
    assert h.service.status("worker", begun["flow_id"])["error_code"] == "FLOW_EXPIRED"
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("worker", {"flow_id": begun["flow_id"], "generation": generation, "replace": False})
    assert _code(error) == "FLOW_EXPIRED"
    state = parse_qs(urlsplit(h.provider.opened[0]).query)["state"][0]
    h.service.callback({"state": state, "code": "late"})
    assert h.provider.exchanges == 0


def test_invalidate_precommit_blocks_new_flows_and_keeps_committing_untouched(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    pending = h.service.begin("mcp", {"generation": generation, "replace": False})
    h.provider.auto_callback = False
    waiting = h.service.begin("worker", {"generation": generation, "replace": False})
    committing_flow = h.service._flows[oauth.GoogleOAuthPurpose.MCP]
    committing_flow.status = "committing"
    h.service.invalidate_precommit()
    assert h.service.status("worker", waiting["flow_id"])["status"] == "cancelled"
    assert h.service.status("mcp", pending["flow_id"])["status"] == "committing"
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("worker", {"generation": generation, "replace": False})
    assert _code(error) == "SESSION_EXPIRED"
    with pytest.raises(SettingsServiceError) as error:
        h.service.cancel("mcp", {"flow_id": pending["flow_id"], "generation": generation})
    assert _code(error) == "COMMIT_IN_PROGRESS"


def test_cancel_versus_commit_race_in_both_orders(tmp_path):
    import threading

    # Order A: commit enters committing first -> cancel is refused, exactly one save completes.
    h = Harness(tmp_path / "a")
    h.sign_in()
    generation = h.generation
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    entered, release = threading.Event(), threading.Event()
    real_verifier = h.provider.verifier

    def slow_verifier(credential):
        entered.set()
        assert release.wait(10)
        return real_verifier(credential)
    h.credentials.oauth_verifier = slow_verifier
    outcome: dict[str, object] = {}
    worker = threading.Thread(target=lambda: outcome.update(result=h.service.commit(
        "worker", {"flow_id": begun["flow_id"], "generation": generation, "replace": False})))
    worker.start()
    assert entered.wait(10)
    with pytest.raises(SettingsServiceError) as error:
        h.service.cancel("worker", {"flow_id": begun["flow_id"], "generation": generation})
    assert _code(error) == "COMMIT_IN_PROGRESS"
    assert h.service.status("worker", begun["flow_id"])["status"] == "committing"
    release.set()
    worker.join(10)
    assert outcome["result"]["status"] == "complete"  # type: ignore[index]
    assert h.credentials.stores.read(ROLES["google-worker"]) is not None
    # Order B: cancel wins -> commit is refused and nothing is saved.
    h2 = Harness(tmp_path / "b")
    h2.sign_in()
    generation = h2.generation
    begun = h2.service.begin("worker", {"generation": generation, "replace": False})
    assert h2.service.cancel("worker", {"flow_id": begun["flow_id"], "generation": generation})["status"] == "cancelled"
    with pytest.raises(SettingsServiceError) as error:
        h2.service.commit("worker", {"flow_id": begun["flow_id"], "generation": generation, "replace": False})
    assert _code(error) == "FLOW_CANCELLED"
    assert h2.credentials.stores.read(ROLES["google-worker"]) is None


def test_commit_failure_from_save_is_recorded_with_fixed_code_and_zero_effects(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    h.provider.permission_id = "owner-a"
    begun = h.service.begin("worker", {"generation": generation, "replace": False})
    h.provider.permission_id = "owner-b"  # account changed between callback and commit proof
    with pytest.raises(SettingsServiceError) as error:
        h.service.commit("worker", {"flow_id": begun["flow_id"], "generation": generation, "replace": False})
    assert _code(error) == "ACCOUNT_MISMATCH"
    assert h.service.status("worker", begun["flow_id"]) == {
        "flow_id": begun["flow_id"], "purpose": "worker", "status": "account_mismatch", "error_code": "ACCOUNT_MISMATCH"}
    assert h.credentials.stores.read(ROLES["google-worker"]) is None
    assert h.credentials.config.load().generation == generation and not h.credentials.journal.unresolved()


def test_replace_flow_replaces_existing_oauth_grant(tmp_path):
    h = Harness(tmp_path)
    h.sign_in()
    generation = h.generation
    first = h.service.begin("mcp", {"generation": generation, "replace": False})
    generation = h.service.commit("mcp", {"flow_id": first["flow_id"], "generation": generation, "replace": False})["config_generation"]
    stored = h.credentials.stores.read(ROLES["google-mcp"])
    with pytest.raises(SettingsServiceError) as error:
        h.service.begin("mcp", {"generation": generation, "replace": False})
    assert _code(error) == "REPLACE_REQUIRED"
    second = h.service.begin("mcp", {"generation": generation, "replace": True})
    result = h.service.commit("mcp", {"flow_id": second["flow_id"], "generation": generation, "replace": True})
    assert result["status"] == "complete"
    assert h.credentials.stores.read(ROLES["google-mcp"]) != stored
    assert h.credentials.stores.read(ROLES["google-mcp"], "backup") is None
