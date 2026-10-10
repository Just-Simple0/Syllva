from __future__ import annotations

import asyncio
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, TypeAdapter

from uls.config.errors import ConfigurationError
from uls.mcp.transports.oauth import (
    READ_SCOPE,
    GoogleOAuthBrokerProvider,
    OAuthStore,
    authorization_policy_fingerprint,
    canonical_public_identity,
)

_ANY_URL = TypeAdapter(AnyUrl)


def _client(index: int, *, method: str = "none") -> OAuthClientInformationFull:
    return OAuthClientInformationFull.model_validate(
        {
            "client_id": f"client-{index:02d}",
            "redirect_uris": [f"http://127.0.0.1:{4000 + index}/callback"],
            "token_endpoint_auth_method": method,
            "client_secret": None if method == "none" else f"secret-{index}",
            "client_secret_expires_at": None if method == "none" else 0,
            "client_id_issued_at": 1000 + index,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": READ_SCOPE,
            "application_type": "native",
        }
    )


def _params(*, resource: str = "https://uls.example/mcp") -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=[READ_SCOPE],
        code_challenge="challenge",
        redirect_uri=_ANY_URL.validate_python("http://127.0.0.1:4001/callback"),
        redirect_uri_provided_explicitly=True,
        resource=resource,
    )


def test_canonical_public_identity_converges_origin_and_mcp_path():
    origin = canonical_public_identity("https://ULS.example:443")
    endpoint = canonical_public_identity("https://uls.example/mcp")
    assert origin == endpoint
    assert origin.public_origin == "https://uls.example"
    assert origin.resource_uri == "https://uls.example/mcp"
    assert origin.issuer == "https://uls.example"
    assert origin.callback_uri == "https://uls.example/oauth/google/callback"
    assert origin.host_header == "uls.example"


@pytest.mark.parametrize(
    "url",
    [
        "http://uls.example/mcp",
        "https://user@uls.example/mcp",
        "https://uls.example/other",
        "https://uls.example/mcp?x=1",
    ],
)
def test_canonical_public_identity_rejects_noncanonical_inputs(url):
    with pytest.raises(ConfigurationError):
        canonical_public_identity(url)


def test_oauth_store_is_0600_and_pending_dcr_is_evicted_not_durable_dos(tmp_path):
    db = tmp_path / "oauth.sqlite3"
    store = OAuthStore(db)
    for index in range(33):
        store.register_client(_client(index), now=1000 + index)
    assert store.get_client("client-00", now=1033) is None
    assert store.get_client("client-32", now=1033) is not None
    if os.name == "posix":
        assert db.stat().st_mode & 0o077 == 0


def test_oauth_store_rejects_existing_nonprivate_database_without_repairing_it(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX permission boundary")
    db = tmp_path / "oauth.sqlite3"
    db.write_bytes(b"")
    db.chmod(0o644)
    with pytest.raises(ConfigurationError, match="mode 0600"):
        OAuthStore(db)
    assert db.stat().st_mode & 0o777 == 0o644


def test_promoted_client_survives_pending_eviction(tmp_path):
    store = OAuthStore(tmp_path / "oauth.sqlite3")
    store.register_client(_client(0), now=1000)
    assert store.promote_client("client-00", now=1001)
    for index in range(1, 34):
        store.register_client(_client(index), now=1001 + index)
    assert store.get_client("client-00", now=1035) is not None


def test_authorize_requires_exact_resource_and_forces_account_chooser(tmp_path):
    identity = canonical_public_identity("https://uls.example/mcp")
    store = OAuthStore(tmp_path / "oauth.sqlite3")
    client = _client(1)
    store.register_client(client, now=1000)
    provider = GoogleOAuthBrokerProvider(
        store=store,
        identity=identity,
        google_client_id="google-client.apps.googleusercontent.com",
        google_client_secret="test-secret",
        authorized_email="owner@example.com",
        clock=lambda: 1000,
    )

    redirect = asyncio.run(provider.authorize(client, _params()))
    query = parse_qs(urlsplit(redirect).query)
    assert query["prompt"] == ["select_account"]
    assert query["scope"] == ["openid email"]
    assert query["redirect_uri"] == [identity.callback_uri]
    assert query["code_challenge_method"] == ["S256"]

    with pytest.raises(AuthorizeError) as exc:
        asyncio.run(provider.authorize(client, _params(resource="https://evil.example/mcp")))
    assert exc.value.error == "invalid_target"


def test_policy_fingerprint_changes_with_owner_or_public_identity():
    first = canonical_public_identity("https://uls.example/mcp")
    second = canonical_public_identity("https://other.example/mcp")
    a = authorization_policy_fingerprint(
        first, google_client_id="g1", authorized_email="owner@example.com"
    )
    assert a == authorization_policy_fingerprint(
        first, google_client_id="g1", authorized_email="OWNER@example.com"
    )
    assert a != authorization_policy_fingerprint(
        first, google_client_id="g1", authorized_email="other@example.com"
    )
    assert a != authorization_policy_fingerprint(
        second, google_client_id="g1", authorized_email="owner@example.com"
    )


def test_refresh_ancestor_replay_revokes_descendant_and_access_family(tmp_path):
    identity = canonical_public_identity("https://uls.example/mcp")
    policy = authorization_policy_fingerprint(
        identity,
        google_client_id="google-client.apps.googleusercontent.com",
        authorized_email="owner@example.com",
    )
    store = OAuthStore(tmp_path / "oauth.sqlite3")
    client = _client(1)
    store.register_client(client, now=1000)
    assert store.promote_client(client.client_id, now=1001)
    raw_code = store.issue_authorization_code(
        client_id=client.client_id,
        scopes=(READ_SCOPE,),
        expires_at=1100,
        code_challenge="challenge",
        redirect_uri="http://127.0.0.1:4001/callback",
        redirect_uri_explicit=True,
        resource=identity.resource_uri,
        subject="google-sub",
        policy_fingerprint=policy,
    )
    code = store.load_authorization_code(
        raw_code, client_id=client.client_id, policy_fingerprint=policy, now=1002
    )
    assert code is not None
    first = store.exchange_authorization_code(
        code,
        access_ttl=900,
        refresh_ttl=3600,
        policy_fingerprint=policy,
        now=1003,
    )
    assert first.refresh_token
    refresh0 = store.load_refresh_token(
        first.refresh_token, client_id=client.client_id, policy_fingerprint=policy, now=1004
    )
    assert refresh0 is not None
    second = store.exchange_refresh_token(
        refresh0,
        [READ_SCOPE],
        access_ttl=900,
        refresh_ttl=3600,
        policy_fingerprint=policy,
        now=1005,
    )
    assert second.refresh_token

    # Reusing the consumed ancestor is a family compromise signal.
    assert store.load_refresh_token(
        first.refresh_token, client_id=client.client_id, policy_fingerprint=policy, now=1006
    ) is None
    assert store.load_access_token(
        second.access_token, policy_fingerprint=policy, issuer=identity.issuer, now=1006
    ) is None
    assert store.load_refresh_token(
        second.refresh_token, client_id=client.client_id, policy_fingerprint=policy, now=1006
    ) is None


def test_policy_change_permanently_invalidates_grants_across_a_b_a_reversion(tmp_path):
    identity = canonical_public_identity("https://uls.example/mcp")
    old_policy = authorization_policy_fingerprint(
        identity, google_client_id="g1", authorized_email="owner@example.com"
    )
    new_policy = authorization_policy_fingerprint(
        identity, google_client_id="g1", authorized_email="new-owner@example.com"
    )
    store = OAuthStore(tmp_path / "oauth.sqlite3")
    client = _client(1)
    store.register_client(client, now=1000)
    store.promote_client(client.client_id, now=1001)
    stale_raw_code = store.issue_authorization_code(
        client_id=client.client_id,
        scopes=(READ_SCOPE,),
        expires_at=1100,
        code_challenge="challenge",
        redirect_uri="http://127.0.0.1:4001/callback",
        redirect_uri_explicit=True,
        resource=identity.resource_uri,
        subject="sub",
        policy_fingerprint=old_policy,
    )
    exchange_raw_code = store.issue_authorization_code(
        client_id=client.client_id,
        scopes=(READ_SCOPE,),
        expires_at=1100,
        code_challenge="challenge",
        redirect_uri="http://127.0.0.1:4001/callback",
        redirect_uri_explicit=True,
        resource=identity.resource_uri,
        subject="sub",
        policy_fingerprint=old_policy,
    )
    code = store.load_authorization_code(
        exchange_raw_code, client_id=client.client_id, policy_fingerprint=old_policy, now=1002
    )
    assert code is not None
    tokens = store.exchange_authorization_code(
        code,
        access_ttl=900,
        refresh_ttl=3600,
        policy_fingerprint=old_policy,
        now=1003,
    )
    assert tokens.refresh_token
    changed_provider = GoogleOAuthBrokerProvider(
        store=store,
        identity=identity,
        google_client_id="g1",
        google_client_secret="test-secret",
        authorized_email="new-owner@example.com",
        clock=lambda: 1004,
    )
    assert changed_provider.policy_fingerprint == new_policy
    reverted_provider = GoogleOAuthBrokerProvider(
        store=store,
        identity=identity,
        google_client_id="g1",
        google_client_secret="test-secret",
        authorized_email="owner@example.com",
        clock=lambda: 1005,
    )
    assert reverted_provider.policy_fingerprint == old_policy
    assert store.load_authorization_code(
        stale_raw_code, client_id=client.client_id, policy_fingerprint=old_policy, now=1006
    ) is None
    assert store.load_access_token(
        tokens.access_token, policy_fingerprint=old_policy, issuer=identity.issuer, now=1006
    ) is None
    assert store.load_refresh_token(
        tokens.refresh_token, client_id=client.client_id, policy_fingerprint=old_policy, now=1006
    ) is None
