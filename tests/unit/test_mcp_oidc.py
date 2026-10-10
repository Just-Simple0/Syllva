"""Unit tests for Remote MCP OIDC verifier and JWKS key manager.

Covers docs/plans/remote-mcp-oauth-oidc.md rev3 test matrix:
- RS256/ES256 verification and caller_identity full 256-bit hash.
- alg: none and HS256 key-confusion attack rejection.
- Expiry, not-before, issuer, audience mismatches.
- Missing/empty sub claim rejection.
- Single-owner gate: authorized_subject vs authorized_email (with email_verified=True).
- Pre-dispatch token lane classification.
- JwksKeyManager caching, TTL, Single-Flight lock, and fail-closed network errors.
- Canary secret leak test: raw token never appears in exception messages/reprs.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, ClassVar

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from uls.config.errors import ConfigurationError
from uls.mcp.transports.oidc import (
    JwksKeyManager,
    OidcTokenVerifier,
    _NoRedirectHandler,
    classify_token_lane,
)

pytestmark = pytest.mark.unit


def _generate_rsa_key_pair(kid: str = "test-rsa-1"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    pem_priv = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = kid
    return pem_priv, public_key, jwk


def _generate_ec_key_pair(kid: str = "test-ec-1"):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    pem_priv = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = kid
    return pem_priv, public_key, jwk


class FakeJwksKeyManager(JwksKeyManager):
    def __init__(self, keys_by_kid: dict[str, Any], issuer: str = "https://accounts.google.com"):
        super().__init__(issuer=issuer, jwks_uri="https://accounts.google.com/jwks")
        self._cached_keys = dict(keys_by_kid)
        self._cache_expires_at = time.time() + 3600.0


def test_classify_token_lane():
    assert classify_token_lane("header.payload.signature") == "oidc"
    assert classify_token_lane("a" * 32) == "bearer"
    assert classify_token_lane("short") == "invalid"
    assert classify_token_lane("has.one.dot.only.") == "invalid"
    assert classify_token_lane("has.too.many.dots.here") == "invalid"


def test_oidc_verifier_accepts_valid_rsa_token_with_sub():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience-client-id",
        authorized_subject="student-sub-123",
        key_manager=km,
    )

    claims = {
        "sub": "student-sub-123",
        "iss": "https://accounts.google.com",
        "aud": "my-audience-client-id",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(claims, pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"})

    caller_id = asyncio.run(verifier.verify_token(token))
    expected_seed = b"https://accounts.google.com:student-sub-123"
    assert caller_id == "remote:oidc:" + hashlib.sha256(expected_seed).hexdigest()
    assert len(caller_id) == 12 + 64


def test_oidc_verifier_accepts_valid_ec_token():
    pem_priv, pub_key, _jwk = _generate_ec_key_pair()
    km = FakeJwksKeyManager({"test-ec-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience-client-id",
        authorized_subject="student-sub-456",
        key_manager=km,
    )

    claims = {
        "sub": "student-sub-456",
        "iss": "https://accounts.google.com",
        "aud": "my-audience-client-id",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(claims, pem_priv, algorithm="ES256", headers={"kid": "test-ec-1"})

    caller_id = asyncio.run(verifier.verify_token(token))
    assert caller_id.startswith("remote:oidc:")


def test_oidc_verifier_rejects_none_algorithm():
    _pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="student-1",
        key_manager=km,
    )
    # Forge token with alg: none
    token = jwt.encode({"sub": "student-1", "iss": "https://accounts.google.com", "aud": "my-audience"}, "", algorithm="none")
    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token))
    assert "unsupported or insecure token algorithm" in str(exc_info.value)


def test_oidc_verifier_rejects_hs256_symmetric_key_confusion():
    _pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="student-1",
        key_manager=km,
    )
    token = jwt.encode({"sub": "student-1", "iss": "https://accounts.google.com", "aud": "my-audience"}, "secret", algorithm="HS256", headers={"kid": "test-rsa-1"})
    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token))
    assert "unsupported or insecure token algorithm" in str(exc_info.value)


def test_oidc_verifier_rejects_expired_token():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="student-1",
        leeway_seconds=0,
        key_manager=km,
    )
    claims = {
        "sub": "student-1",
        "iss": "https://accounts.google.com",
        "aud": "my-audience",
        "iat": int(time.time()) - 600,
        "exp": int(time.time()) - 300,  # expired in the past
    }
    token = jwt.encode(claims, pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"})
    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token))
    assert "token validation failed" in str(exc_info.value)


def test_oidc_verifier_rejects_mismatched_issuer_or_audience():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="student-1",
        key_manager=km,
    )
    # Wrong issuer
    token_wrong_iss = jwt.encode(
        {"sub": "student-1", "iss": "https://evil.example", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    with pytest.raises(ConfigurationError):
        asyncio.run(verifier.verify_token(token_wrong_iss))

    # Wrong audience
    token_wrong_aud = jwt.encode(
        {"sub": "student-1", "iss": "https://accounts.google.com", "aud": "different-aud", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    with pytest.raises(ConfigurationError):
        asyncio.run(verifier.verify_token(token_wrong_aud))


def test_oidc_verifier_rejects_missing_or_empty_sub_claim():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_email="student@knu.ac.kr",
        key_manager=km,
    )
    # Token has email and email_verified, but missing sub
    claims_no_sub = {
        "iss": "https://accounts.google.com",
        "aud": "my-audience",
        "email": "student@knu.ac.kr",
        "email_verified": True,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(claims_no_sub, pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"})
    with pytest.raises(ConfigurationError):
        asyncio.run(verifier.verify_token(token))


def test_oidc_verifier_rejects_unauthorized_subject():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="authorized-student",
        key_manager=km,
    )
    # Valid signature from Google, but attacker's subject
    token = jwt.encode(
        {"sub": "attacker-subject-999", "iss": "https://accounts.google.com", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token))
    assert "token subject not authorized" in str(exc_info.value)


def test_oidc_verifier_authorized_email_requires_email_verified_true():
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_email="owner@example.com",
        key_manager=km,
    )

    # 1. Matching email, but email_verified is False -> rejected
    token_unverified = jwt.encode(
        {"sub": "sub-1", "email": "owner@example.com", "email_verified": False, "iss": "https://accounts.google.com", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token_unverified))
    assert "unverified email claim rejected" in str(exc_info.value)

    # 2. Matching email, but email_verified claim missing entirely -> rejected
    token_missing_verified = jwt.encode(
        {"sub": "sub-1", "email": "owner@example.com", "iss": "https://accounts.google.com", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    with pytest.raises(ConfigurationError):
        asyncio.run(verifier.verify_token(token_missing_verified))

    # 3. Matching email and email_verified is True -> accepted
    token_valid = jwt.encode(
        {"sub": "sub-1", "email": "owner@example.com", "email_verified": True, "iss": "https://accounts.google.com", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300},
        pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"}
    )
    caller_id = asyncio.run(verifier.verify_token(token_valid))
    assert caller_id.startswith("remote:oidc:")


def test_canary_secret_never_leaks_in_exception_traces():
    canary = "CANARY-SECRET-PAYLOAD-xyz789"
    pem_priv, pub_key, _jwk = _generate_rsa_key_pair()
    km = FakeJwksKeyManager({"test-rsa-1": pub_key})
    verifier = OidcTokenVerifier(
        issuer="https://accounts.google.com",
        audience="my-audience",
        authorized_subject="authorized-sub",
        key_manager=km,
    )
    # Bad token containing canary in signature
    token = jwt.encode({"sub": "wrong-sub", "canary": canary, "iss": "https://accounts.google.com", "aud": "my-audience", "iat": int(time.time()), "exp": int(time.time()) + 300}, pem_priv, algorithm="RS256", headers={"kid": "test-rsa-1"})

    with pytest.raises(ConfigurationError) as exc_info:
        asyncio.run(verifier.verify_token(token))
    assert canary not in str(exc_info.value)
    assert canary not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_jwks_key_manager_rejects_trailing_slash_issuer():
    # A trailing slash is rejected outright rather than normalized away,
    # so it cannot later create a normalization mismatch between the
    # configured issuer and the literal issuer returned by discovery.
    with pytest.raises(ConfigurationError, match="trailing slash"):
        JwksKeyManager("https://accounts.google.com/")


def test_jwks_key_manager_rejects_http_issuer():
    with pytest.raises(ConfigurationError, match="HTTPS"):
        JwksKeyManager("http://accounts.google.com")


def test_no_redirect_handler_disables_redirect_following():
    handler = _NoRedirectHandler()
    # redirect_request returning None tells urllib not to follow the
    # redirect at all; this is the actual mechanism the trust-bootstrap
    # fix relies on (see JwksKeyManager._fetch_url_sync).
    result = handler.redirect_request(
        None, None, 302, "Found",
        {}, "http://evil.example/jwks",
    )
    assert result is None


def test_no_redirect_opener_actually_installs_the_no_redirect_handler():
    # This is the production-wiring check that a plain unit test of
    # _NoRedirectHandler in isolation cannot provide: it proves the
    # *actual* opener object used by _fetch_url_sync has replaced
    # urllib's default HTTPRedirectHandler with ours, instead of merely
    # having a correctly-behaving handler class that nothing uses.
    import urllib.request

    from uls.mcp.transports import oidc as oidc_module

    handlers = oidc_module._NO_REDIRECT_OPENER.handlers
    assert any(isinstance(h, _NoRedirectHandler) for h in handlers)
    assert not any(
        type(h) is urllib.request.HTTPRedirectHandler for h in handlers
    )


def test_fetch_url_sync_rejects_a_redirect_response_instead_of_following_it(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")

    class _FakeRedirectResponse:
        status = 302
        headers: ClassVar[dict[str, str]] = {"Location": "http://evil.example/jwks"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_open(req, timeout=5.0):
        # Simulates the redirect-disabling opener observing the raw 3xx
        # response because it refused to follow the Location header.
        return _FakeRedirectResponse()

    from uls.mcp.transports import oidc as oidc_module
    monkeypatch.setattr(oidc_module._NO_REDIRECT_OPENER, "open", fake_open)
    with pytest.raises(ConfigurationError, match="non-200 status"):
        km._fetch_url_sync("https://accounts.google.com/jwks")


def test_discovery_rejects_issuer_that_only_differs_by_trailing_slash(monkeypatch):
    # rev3 plan section 2.3 requires an *exact* match between the
    # configured issuer and the issuer the discovery document reports.
    # Previously both sides were rstrip("/")-normalized before comparing,
    # so a provider could satisfy the check with a value that literally
    # differs from what was configured.
    km = JwksKeyManager("https://accounts.google.com")

    def fake_fetch(url):
        assert url == "https://accounts.google.com/.well-known/openid-configuration"
        return {"issuer": "https://accounts.google.com/", "jwks_uri": "https://accounts.google.com/jwks"}, 3600.0

    monkeypatch.setattr(km, "_fetch_url_sync", fake_fetch)
    with pytest.raises(ConfigurationError, match="does not match configured issuer"):
        km._discover_jwks_uri_sync()


def test_discovery_accepts_exact_issuer_match(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com")

    def fake_fetch(url):
        return {"issuer": "https://accounts.google.com", "jwks_uri": "https://accounts.google.com/jwks"}, 3600.0

    monkeypatch.setattr(km, "_fetch_url_sync", fake_fetch)
    assert km._discover_jwks_uri_sync() == "https://accounts.google.com/jwks"


def test_discovery_rejects_non_string_issuer_in_document(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com")

    def fake_fetch(url):
        return {"issuer": None, "jwks_uri": "https://accounts.google.com/jwks"}, 3600.0

    monkeypatch.setattr(km, "_fetch_url_sync", fake_fetch)
    with pytest.raises(ConfigurationError, match="missing or malformed"):
        km._discover_jwks_uri_sync()


def test_live_check_sync_exercises_discovery_and_jwks_endpoint(monkeypatch):
    # doctor --live must actually perform network I/O against the JWKS
    # endpoint (rev3 plan section 5.2), not just resolve an already
    # explicitly-configured jwks_uri without touching the network.
    km = JwksKeyManager("https://accounts.google.com")  # no explicit jwks_uri
    calls: list[str] = []

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": "https://accounts.google.com", "jwks_uri": "https://accounts.google.com/jwks"}, 3600.0
        return {"keys": []}, 3600.0

    monkeypatch.setattr(km, "_fetch_url_sync", fake_fetch)
    km.live_check_sync()
    assert calls == [
        "https://accounts.google.com/.well-known/openid-configuration",
        "https://accounts.google.com/jwks",
    ]
    # A live check must not poison the actual signing-key cache used by
    # get_signing_key(); it is a connectivity probe, not a refresh.
    assert km._cached_keys == {}


def test_live_check_sync_with_explicit_jwks_uri_still_fetches_it(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    calls: list[str] = []

    def fake_fetch(url):
        calls.append(url)
        return {"keys": []}, 3600.0

    monkeypatch.setattr(km, "_fetch_url_sync", fake_fetch)
    km.live_check_sync()
    assert calls == ["https://accounts.google.com/jwks"]


def test_get_signing_key_cache_hit_never_calls_refresh(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {"known-kid": "the-public-key"}
    km._cache_expires_at = time.time() + 3600.0

    def fail_if_called():
        raise AssertionError("refresh must not run on a cache hit")

    monkeypatch.setattr(km, "_refresh_keys_sync", fail_if_called)
    result = asyncio.run(km.get_signing_key("known-kid"))
    assert result == "the-public-key"


def test_get_signing_key_expired_cache_triggers_exactly_one_refresh(monkeypatch):
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {"old-kid": "stale-key"}
    km._cache_expires_at = time.time() - 10.0  # already expired
    km._last_refresh = time.time() - 3600.0  # well past the cooldown window
    calls = {"n": 0}

    def fake_refresh():
        calls["n"] += 1
        km._cached_keys = {"new-kid": "fresh-key"}
        km._cache_expires_at = time.time() + 3600.0
        km._last_refresh = time.time()

    monkeypatch.setattr(km, "_refresh_keys_sync", fake_refresh)
    result = asyncio.run(km.get_signing_key("new-kid"))
    assert result == "fresh-key"
    assert calls["n"] == 1


def test_get_signing_key_cooldown_returns_known_stale_kid_without_refresh(monkeypatch):
    # Within the 60s post-refresh cooldown window, a *known* (even if
    # technically expired) kid is served from cache rather than
    # triggering a fresh network round-trip on every single request.
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {"known-kid": "still-good-key"}
    km._cache_expires_at = time.time() - 1.0  # expired
    km._last_refresh = time.time() - 5.0  # inside the 60s cooldown

    def fail_if_called():
        raise AssertionError("refresh must not run inside the cooldown window")

    monkeypatch.setattr(km, "_refresh_keys_sync", fail_if_called)
    result = asyncio.run(km.get_signing_key("known-kid"))
    assert result == "still-good-key"


def test_get_signing_key_cooldown_rejects_unknown_kid_without_refresh(monkeypatch):
    # An *unknown* kid inside the cooldown window must fail closed
    # immediately rather than hammering the JWKS endpoint once per
    # request for an attacker-supplied bogus kid.
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {"known-kid": "still-good-key"}
    km._cache_expires_at = time.time() - 1.0
    km._last_refresh = time.time() - 5.0

    def fail_if_called():
        raise AssertionError("refresh must not run inside the cooldown window")

    monkeypatch.setattr(km, "_refresh_keys_sync", fail_if_called)
    with pytest.raises(ConfigurationError, match="unknown signing key id"):
        asyncio.run(km.get_signing_key("unknown-kid"))


def test_get_signing_key_refresh_failure_fails_closed_without_stale_fallback(monkeypatch):
    # A network failure while trying to pick up a not-yet-cached (or
    # already-expired) key must fail closed -- it must never silently
    # accept an old/expired signing key just because one happens to be
    # sitting in the cache under a different kid.
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {"other-kid": "unrelated-key"}
    km._cache_expires_at = time.time() - 1.0  # expired
    km._last_refresh = time.time() - 3600.0  # past cooldown, refresh is attempted

    def fake_refresh():
        raise ConfigurationError("failed to fetch OIDC metadata from provider")

    monkeypatch.setattr(km, "_refresh_keys_sync", fake_refresh)
    with pytest.raises(ConfigurationError, match="unable to verify token signing key"):
        asyncio.run(km.get_signing_key("requested-kid"))


def test_get_signing_key_concurrent_misses_single_flight_refresh(monkeypatch):
    # Two concurrent cache-miss requests for the same kid must trigger
    # exactly one _refresh_keys_sync call: the second request has to
    # block on the asyncio.Lock and then be served by the lock-internal
    # double-check (get_signing_key's "double-check inside lock" branch)
    # rather than independently hitting the network a second time.
    #
    # The 60s post-refresh cooldown branch would also happen to return
    # a freshly-populated kid without a second refresh, which could mask
    # a removed double-check. Force the cooldown window to zero so this
    # test exercises the double-check branch specifically, not the
    # cooldown branch.
    import uls.mcp.transports.oidc as oidc_module
    monkeypatch.setattr(oidc_module, "REFRESH_COOLDOWN_SECONDS", 0.0)
    km = JwksKeyManager("https://accounts.google.com", "https://accounts.google.com/jwks")
    km._cached_keys = {}
    km._cache_expires_at = 0.0
    km._last_refresh = 0.0
    call_count = {"n": 0}

    def fake_refresh():
        call_count["n"] += 1
        # A real synchronous network call would block the worker thread
        # asyncio.to_thread() runs this on; sleeping here lets the second
        # coroutine's attempt to acquire the lock actually overlap with
        # this refresh instead of the two calls happening sequentially
        # by accident.
        time.sleep(0.05)
        km._cached_keys = {"shared-kid": "fresh-key"}
        km._cache_expires_at = time.time() + 3600.0
        km._last_refresh = time.time()

    monkeypatch.setattr(km, "_refresh_keys_sync", fake_refresh)

    async def scenario():
        return await asyncio.gather(
            km.get_signing_key("shared-kid"),
            km.get_signing_key("shared-kid"),
        )

    results = asyncio.run(scenario())
    assert results == ["fresh-key", "fresh-key"]
    assert call_count["n"] == 1
