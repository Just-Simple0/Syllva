"""HTTP boundary for the personal Google OAuth routes (P2 plan §2)."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient
from tests.contract._settings_support import FETCH, HOST, NAVIGATE, ORIGIN, write_config
from tests.contract.test_settings_credential_service import service

from uls.settings.app import create_settings_app
from uls.settings.credential_roles import ROLES
from uls.settings.google_oauth import FakeGoogleOAuthProvider, GoogleOAuthFlowService
from uls.settings.security import SESSION_COOKIE, SessionSecurity, new_path_prefix

pytestmark = pytest.mark.contract

CLIENT = {"client_id": "synthetic-client.apps.googleusercontent.com", "client_secret": "synthetic-client-secret"}
ROLES_WORKER = ROLES["google-worker"]
CALLBACK_NAVIGATE = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate",
                     "sec-fetch-dest": "document", "sec-fetch-user": "?1"}


@pytest.fixture
def http(tmp_path):
    write_config(tmp_path, google_oauth=dict(CLIENT))
    s = service(tmp_path)
    provider = FakeGoogleOAuthProvider()
    provider.auto_callback = False
    s.oauth_verifier = provider.verifier
    prefix = new_path_prefix()
    token, security = SessionSecurity.issue()
    oauth = GoogleOAuthFlowService(
        s, s.config, authority=HOST, prefix=prefix, security=security, opener=provider.opener,
        exchanger=provider.exchanger, account_reader=provider.account_reader,
    )
    provider.service = oauth
    app = create_settings_app(s.config, s.journal, security, HOST, prefix=prefix, credential_service=s,
                              google_oauth_service=oauth)
    with TestClient(app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50000)) as client:
        assert client.get(f"?bootstrap={token}", headers=NAVIGATE, follow_redirects=False).status_code == 303
        csrf = client.get("/api/v1/session/csrf", headers=FETCH).json()["csrf_token"]
        yield client, s, provider, oauth, {**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf}, prefix


def _state(provider):
    return parse_qs(urlsplit(provider.opened[-1]).query)["state"][0]


def test_full_flow_over_http_stores_nothing_in_responses(http):
    client, s, provider, _oauth, headers, prefix = http
    generation = s.config.load().generation
    begun = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False}, headers=headers)
    assert begun.status_code == 200, begun.text
    flow_id = begun.json()["flow_id"]
    assert set(begun.json()) == {"flow_id", "purpose", "status", "error_code"}
    state = _state(provider)
    assert state not in begun.text and "code_challenge" not in begun.text
    status = client.get(f"/api/v1/google-oauth/worker/{flow_id}", headers=FETCH)
    assert status.json()["status"] == "pending"
    # No cookie: the provider redirect arrives cross-site on the exact loopback route.
    bare = TestClient(client.app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50001), cookies={})
    callback = bare.get(f"/oauth/google/callback?state={state}&code=fake-code", headers=CALLBACK_NAVIGATE,
                        follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == f"/{prefix}/oauth/google/result"
    assert "set-cookie" not in callback.headers
    assert callback.headers["cache-control"] == "no-store" and callback.headers["referrer-policy"] == "no-referrer"
    result = bare.get("/oauth/google/result", headers=CALLBACK_NAVIGATE)
    assert result.status_code == 200 and "Google sign-in finished" in result.text
    assert "set-cookie" not in result.headers and result.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/google-oauth/worker/{flow_id}", headers=FETCH).json()["status"] == "awaiting_commit"
    commit = client.post("/api/v1/google-oauth/worker/commit",
                         json={"flow_id": flow_id, "generation": generation, "replace": False}, headers=headers)
    assert commit.status_code == 200, commit.text
    assert commit.json()["status"] == "complete"
    assert "fake-refresh" not in commit.text and "fake-access" not in commit.text and "fake-owner" not in commit.text
    cards = client.get("/api/v1/credentials", headers=FETCH).json()["cards"]
    worker = next(card for card in cards if card["role"] == "google-worker")
    assert worker["state"] == "configured" and worker["credential_type"] == "authorized_user"
    overview = client.get("/api/v1/overview", headers=FETCH).json()
    assert overview["google_oauth_configured"] is True
    assert CLIENT["client_secret"] not in json.dumps(overview)


def test_mutations_need_session_origin_and_csrf_and_status_needs_same_origin(http):
    client, s, _provider, _oauth, headers, _prefix = http
    generation = s.config.load().generation
    body = {"generation": generation, "replace": False}
    assert client.post("/api/v1/google-oauth/worker/begin", json=body, headers={**FETCH, "origin": ORIGIN}).json()["error"]["code"] == "CSRF_REJECTED"
    assert client.post("/api/v1/google-oauth/worker/begin", json=body, headers={**headers, "origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/v1/google-oauth/worker/begin", json=body, headers={**headers, "sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/v1/google-oauth/worker/unknown", json=body, headers=headers).status_code == 404
    assert client.post("/api/v1/google-oauth/picker/begin", json=body, headers=headers).status_code == 404
    assert client.get("/api/v1/google-oauth/worker/" + "0" * 32, headers={**FETCH, "sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/v1/google-oauth/worker/" + "0" * 32, headers=FETCH).status_code == 404
    assert client.get("/api/v1/google-oauth/worker/begin", headers=FETCH).status_code == 404
    bad = client.post("/api/v1/google-oauth/worker/begin", content=b'{"generation": "x", "generation": "y", "replace": false}',
                      headers={**headers, "content-type": "application/json"})
    assert bad.status_code == 400


@pytest.mark.parametrize("headers, expected", [
    ({"sec-fetch-site": "same-origin", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-user": "?1"}, 403),
    ({"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}, 403),
    ({"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe", "sec-fetch-user": "?1"}, 403),
    ({"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}, 403),
    ({"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-user": "?1"}, 303),
    ({}, 303),
])
def test_callback_fetch_metadata_rules(http, headers, expected):
    client, s, provider, _oauth, auth_headers, prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=auth_headers).json()["flow_id"]
    bare = TestClient(client.app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50002))
    response = bare.get(f"/oauth/google/callback?state={_state(provider)}&code=fake-code", headers=headers,
                        follow_redirects=False)
    assert response.status_code == expected, response.text
    status = client.get(f"/api/v1/google-oauth/worker/{flow_id}", headers=FETCH).json()["status"]
    assert status == ("awaiting_commit" if expected == 303 else "pending")


def test_callback_and_result_reject_non_loopback_peer_methods_and_query_abuse(http):
    client, s, provider, _oauth, auth_headers, prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=auth_headers).json()["flow_id"]
    state = _state(provider)
    remote = TestClient(client.app, base_url=f"{ORIGIN}/{prefix}/", client=("192.0.2.9", 50003))
    assert remote.get(f"/oauth/google/callback?state={state}&code=c", headers=CALLBACK_NAVIGATE, follow_redirects=False).status_code == 403
    assert remote.get("/oauth/google/result", headers=CALLBACK_NAVIGATE).status_code == 403
    local = TestClient(client.app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50004))
    assert local.post(f"/oauth/google/callback?state={state}&code=c", headers=CALLBACK_NAVIGATE).status_code == 405
    assert local.get("/oauth/google/result?x=1", headers=CALLBACK_NAVIGATE).status_code == 400
    assert local.get("/oauth/google/callback?state=" + "a" * 9000, headers=CALLBACK_NAVIGATE, follow_redirects=False).status_code == 400
    dup = local.get(f"/oauth/google/callback?state={state}&state={state}&code=c", headers=CALLBACK_NAVIGATE, follow_redirects=False)
    assert dup.status_code == 303
    assert client.get(f"/api/v1/google-oauth/worker/{flow_id}", headers=FETCH).json()["status"] == "pending"
    other = TestClient(client.app, base_url=f"{ORIGIN}/other-prefix-ABCDEFGHIJKLMNOPQRS/", client=("127.0.0.1", 50005))
    assert other.get("/oauth/google/result", headers=CALLBACK_NAVIGATE).status_code == 404
    assert local.get(f"/oauth/google/callback?state={state}&error=access_denied", headers=CALLBACK_NAVIGATE,
                     follow_redirects=False).status_code == 303
    assert client.get(f"/api/v1/google-oauth/worker/{flow_id}", headers=FETCH).json()["error_code"] == "OAUTH_ACCESS_DENIED"


def test_result_page_never_touches_session_and_callback_without_service_still_redirects(tmp_path):
    write_config(tmp_path)
    s = service(tmp_path)
    prefix = new_path_prefix()
    _token, security = SessionSecurity.issue()
    app = create_settings_app(s.config, s.journal, security, HOST, prefix=prefix, credential_service=s)
    client = TestClient(app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50006))
    assert client.get("/oauth/google/result").status_code == 200
    assert client.get("/oauth/google/callback?state=x&code=y", follow_redirects=False).status_code == 303
    assert client.get("/api/v1/overview", headers=FETCH).status_code == 401
    assert client.post("/api/v1/google-oauth/worker/begin", json={}, headers={**FETCH, "origin": ORIGIN}).status_code == 401
    assert security.lifecycle_state() == "new"


def test_oauth_error_responses_carry_only_the_fixed_code(http):
    client, s, _provider, _oauth, headers, _prefix = http
    generation = s.config.load().generation
    responses = [
        client.post("/api/v1/google-oauth/worker/begin", json={"generation": "stale", "replace": False}, headers=headers),
        client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": True}, headers=headers),
        client.post("/api/v1/google-oauth/worker/commit", json={"flow_id": "0" * 32, "generation": generation, "replace": False}, headers=headers),
        client.get("/api/v1/google-oauth/worker/" + "0" * 32, headers=FETCH),
    ]
    for response in responses:
        assert response.status_code in {404, 409}, response.text
        assert set(response.json()) == {"error"} and set(response.json()["error"]) == {"code"}
    assert [r.json()["error"]["code"] for r in responses] == ["CONFIGURATION_CHANGED", "NOT_CONFIGURED", "FLOW_NOT_FOUND", "FLOW_NOT_FOUND"]


def test_result_and_callback_stay_session_free_after_replacement(http):
    client, s, provider, oauth, headers, prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=headers).json()["flow_id"]
    state = _state(provider)
    oauth.security.replace()
    bare = TestClient(client.app, base_url=f"{ORIGIN}/{prefix}/", client=("127.0.0.1", 50010))
    result = bare.get("/oauth/google/result", headers=CALLBACK_NAVIGATE)
    assert result.status_code == 200 and "set-cookie" not in result.headers
    late = bare.get(f"/oauth/google/callback?state={state}&code=fake-code", headers=CALLBACK_NAVIGATE, follow_redirects=False)
    assert late.status_code == 303
    assert provider.exchanges == 0
    assert oauth.status("worker", flow_id)["error_code"] == "FLOW_STATE_REJECTED"
    assert client.get("/api/v1/overview", headers=FETCH).json()["error"]["code"] == "SESSION_REPLACED"


def test_close_drains_a_committing_save_before_ending_the_session(http):
    import threading

    client, s, provider, oauth, headers, _prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=headers).json()["flow_id"]
    bare = TestClient(client.app, base_url=client.base_url, client=("127.0.0.1", 50011))
    bare.get(f"/oauth/google/callback?state={_state(provider)}&code=fake-code", headers=CALLBACK_NAVIGATE, follow_redirects=False)
    assert oauth.status("worker", flow_id)["status"] == "awaiting_commit"
    release = threading.Event()
    entered = threading.Event()
    real_verifier = s.oauth_verifier

    def blocking_verifier(credential):
        entered.set()
        assert release.wait(10)
        return real_verifier(credential)
    s.oauth_verifier = blocking_verifier
    outcome: dict[str, object] = {}

    def commit() -> None:
        response = client.post("/api/v1/google-oauth/worker/commit",
                               json={"flow_id": flow_id, "generation": generation, "replace": False}, headers=headers)
        outcome["status"] = response.status_code
        outcome["body"] = response.json()
    worker = threading.Thread(target=commit)
    worker.start()
    assert entered.wait(10)
    closed: dict[str, object] = {}

    def close() -> None:
        closed["response"] = client.post("/api/v1/session/close", json={}, headers=headers)
    closer = threading.Thread(target=close)
    closer.start()
    closer.join(0.5)
    assert closer.is_alive(), "close must wait for the committing save to drain"
    assert oauth.blocked is True
    release.set()
    worker.join(10)
    closer.join(10)
    assert outcome["status"] == 200 and outcome["body"]["status"] == "complete"
    assert closed["response"].status_code == 200
    assert s.stores.read(ROLES_WORKER) is not None


def test_oauth_connection_test_errors_are_code_only_while_service_account_shape_is_unchanged(http):
    client, s, _provider, _oauth, headers, _prefix = http
    from uls.config import google_oauth as oauth
    info = {"type": "authorized_user", "client_id": CLIENT["client_id"], "client_secret": CLIENT["client_secret"],
            "refresh_token": "rt-x", "token_uri": oauth.TOKEN_URI, "scopes": [oauth.MCP_SCOPE]}  # wrong scope for worker
    s.stores.write(ROLES_WORKER, json.dumps(info, sort_keys=True, separators=(",", ":")).encode())
    raw = s.config.load().raw
    raw["google_worker_credentials_path"] = ROLES_WORKER.locator(s.stores.root)
    import yaml

    from uls.config.mutation import atomic_replace_config
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    response = client.post("/api/v1/connections/google/worker/test", json={}, headers=headers)
    assert response.status_code == 409 and response.json() == {"error": {"code": "OAUTH_GRANT_MISMATCH"}}
    # Service-account bytes keep the existing {code, message} shape.
    s.stores.write(ROLES_WORKER, json.dumps({"type": "service_account", "client_email": "invalid@b", "private_key": "p",
                                             "private_key_id": "k", "project_id": "p", "token_uri": oauth.TOKEN_URI}).encode())
    s.checks._last.clear()
    sa = client.post("/api/v1/connections/google/worker/test", json={}, headers=headers)
    assert "message" in sa.json()["error"]


def test_connection_test_reads_no_credential_before_origin_and_csrf_pass(http, monkeypatch):
    client, s, _provider, _oauth, headers, _prefix = http
    reads: list[str] = []
    real_effective = s.effective
    monkeypatch.setattr(s, "effective", lambda role, raw: (reads.append(role.slug), real_effective(role, raw))[1])
    assert client.post("/api/v1/connections/google/worker/test", json={}, headers={**FETCH, "origin": ORIGIN}).json()["error"]["code"] == "CSRF_REJECTED"
    assert client.post("/api/v1/connections/google/worker/test", json={}, headers={**headers, "origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/v1/connections/google/worker/test", json={}, headers={**headers, "sec-fetch-site": "cross-site"}).status_code == 403
    assert reads == []
    response = client.post("/api/v1/connections/google/worker/test", json={}, headers=headers)
    assert response.status_code == 409 and reads == ["google-worker"]


def test_connection_test_response_shape_follows_the_checked_snapshot_type(http):
    client, s, _provider, _oauth, headers, _prefix = http
    import yaml

    from uls.config import google_oauth as oauth
    from uls.config.mutation import atomic_replace_config
    raw = s.config.load().raw
    raw["google_worker_credentials_path"] = ROLES_WORKER.locator(s.stores.root)
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    sa = json.dumps({"type": "service_account", "client_email": "invalid@b", "private_key": "p",
                     "private_key_id": "k", "project_id": "p", "token_uri": oauth.TOKEN_URI}).encode()
    au = json.dumps({"type": "authorized_user", "client_id": CLIENT["client_id"], "client_secret": CLIENT["client_secret"],
                     "refresh_token": "rt-x", "token_uri": oauth.TOKEN_URI, "scopes": [oauth.MCP_SCOPE]},
                    sort_keys=True, separators=(",", ":")).encode()
    # The stored bytes switch type right when the check reads them: the shape must follow what was checked.
    real_effective = s.effective

    def swap_then_read(role, raw_config):
        s.stores.write(ROLES_WORKER, au)
        return real_effective(role, raw_config)
    s.stores.write(ROLES_WORKER, sa)
    s.effective = swap_then_read
    response = client.post("/api/v1/connections/google/worker/test", json={}, headers=headers)
    assert response.json() == {"error": {"code": "OAUTH_GRANT_MISMATCH"}}
    s.checks._last.clear()

    def swap_back_then_read(role, raw_config):
        s.stores.write(ROLES_WORKER, sa)
        return real_effective(role, raw_config)
    s.effective = swap_back_then_read
    response = client.post("/api/v1/connections/google/worker/test", json={}, headers=headers)
    assert set(response.json()["error"]) == {"code", "message"}


def test_close_waits_beyond_one_drain_window_until_the_committing_save_finishes(http, monkeypatch):
    import threading

    from uls.settings import app as app_module
    monkeypatch.setattr(app_module, "CLOSE_MUTATION_DRAIN_SECONDS", 0.1)
    client, s, provider, oauth, headers, _prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=headers).json()["flow_id"]
    bare = TestClient(client.app, base_url=client.base_url, client=("127.0.0.1", 50012))
    bare.get(f"/oauth/google/callback?state={_state(provider)}&code=fake-code", headers=CALLBACK_NAVIGATE, follow_redirects=False)
    release, entered = threading.Event(), threading.Event()
    real_verifier = s.oauth_verifier

    def slow_verifier(credential):
        entered.set()
        assert release.wait(10)
        return real_verifier(credential)
    s.oauth_verifier = slow_verifier
    epoch_before = oauth.security.session_epoch
    outcome: dict[str, object] = {}
    worker = threading.Thread(target=lambda: outcome.update(status=client.post(
        "/api/v1/google-oauth/worker/commit", json={"flow_id": flow_id, "generation": generation, "replace": False},
        headers=headers).status_code))
    worker.start()
    assert entered.wait(10)
    closed: dict[str, object] = {}
    closer = threading.Thread(target=lambda: closed.update(status=client.post("/api/v1/session/close", json={}, headers=headers).status_code))
    closer.start()
    closer.join(0.6)  # several drain windows elapse while the commit is still inside the barrier
    assert closer.is_alive() and oauth.security.session_epoch == epoch_before
    assert oauth.status("worker", flow_id)["status"] == "committing"
    release.set()
    worker.join(10)
    closer.join(10)
    assert outcome["status"] == 200 and closed["status"] == 200
    assert oauth.security.session_epoch == epoch_before + 1
    assert s.stores.read(ROLES_WORKER) is not None


def test_close_after_a_drain_that_crossed_the_idle_limit_still_ends_the_session(tmp_path):
    from tests.contract._settings_support import FakeClock, make_harness

    clock = FakeClock()
    h = make_harness(tmp_path, clock=clock)
    csrf = h.signed_in()
    epoch = h.security.session_epoch
    # Simulate the idle limit passing between authorization and the post-drain close.
    real_close = h.security.close

    def late_close(handle):
        clock.now += 15 * 60 + 1
        return real_close(handle)
    h.security.close = late_close
    assert h.post("/api/v1/session/close", {}, csrf).status_code == 200
    assert h.security.session_epoch == epoch + 1 and h.security.lifecycle_state() == "expired"


def test_idle_expiry_is_reported_but_applied_only_by_the_coordinator(tmp_path):
    from tests.contract._settings_support import FakeClock

    clock = FakeClock()
    token, security = SessionSecurity.issue(clock=clock)
    handle = security.consume_bootstrap(token)
    epoch = security.session_epoch
    clock.now += 15 * 60 + 1
    # An authenticated request arriving first is rejected but does not consume the transition.
    assert security.session_code(handle) == "SESSION_EXPIRED"
    assert security.live_epoch() is None
    assert security.lifecycle_pending() == "expired" and security.lifecycle_state() == "expired"
    assert security.session_epoch == epoch
    security.expire()
    assert security.session_epoch == epoch + 1 and security.lifecycle_pending() is None
    assert security.lifecycle_state() == "expired"


def test_close_invalidates_pending_flows_and_blocks_new_ones(http):
    client, s, provider, oauth, headers, _prefix = http
    generation = s.config.load().generation
    flow_id = client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                          headers=headers).json()["flow_id"]
    assert client.post("/api/v1/session/close", json={}, headers=headers).status_code == 200
    assert oauth.blocked is True
    assert oauth.status("worker", flow_id)["status"] == "cancelled"
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.post("/api/v1/google-oauth/worker/begin", json={"generation": generation, "replace": False},
                       headers=headers).status_code == 401
    assert provider.exchanges == 0
