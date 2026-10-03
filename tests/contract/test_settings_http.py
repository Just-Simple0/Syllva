"""Local Settings HTTP session, bootstrap, CSRF, prefix, and header boundary."""
from __future__ import annotations

import logging

import pytest
from _settings_support import FETCH, HOST, NAVIGATE, ORIGIN, make_harness
from starlette.testclient import TestClient

from uls.settings.security import BOOTSTRAP_TTL_SECONDS, SESSION_COOKIE

pytestmark = pytest.mark.contract


def test_bootstrap_sets_only_prefix_scoped_session_cookie_and_clean_redirect(tmp_path):
    h = make_harness(tmp_path)
    response = h.bootstrap()
    assert response.status_code == 303
    assert response.headers["location"] == h.root
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered
    assert f"path={h.root.lower()}" in lowered
    assert "max-age" not in lowered and "expires" not in lowered and "domain" not in lowered
    csrf = h.csrf()
    assert csrf not in response.text and csrf not in str(response.headers)
    assert h.token not in response.headers["location"]


@pytest.mark.parametrize("site", ["none", "same-origin"])
def test_bootstrap_accepts_only_none_or_same_origin_navigation(tmp_path, site):
    h = make_harness(tmp_path)
    response = h.client.get(f"?bootstrap={h.token}", headers={**NAVIGATE, "sec-fetch-site": site},
                            follow_redirects=False)
    assert response.status_code == 303


@pytest.mark.parametrize("headers", [
    {**NAVIGATE, "sec-fetch-site": "cross-site"},
    {**NAVIGATE, "sec-fetch-site": "same-site"},
    {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"},
    {**NAVIGATE, "sec-fetch-dest": "iframe"},
    {**NAVIGATE, "sec-fetch-mode": "no-cors", "sec-fetch-dest": "script"},
    {},
])
def test_bootstrap_rejects_cross_site_missing_or_embedded_navigation(tmp_path, headers):
    h = make_harness(tmp_path)
    response = h.client.get(f"?bootstrap={h.token}", headers=headers, follow_redirects=False)
    assert response.status_code in {401, 403}
    assert "set-cookie" not in response.headers
    assert h.bootstrap().status_code == 303  # the rejected attempt did not consume it


def test_duplicate_fetch_metadata_is_rejected(tmp_path):
    h = make_harness(tmp_path)
    raw = [(b"sec-fetch-site", b"none"), (b"sec-fetch-site", b"cross-site"),
           (b"sec-fetch-mode", b"navigate"), (b"sec-fetch-dest", b"document")]
    response = h.client.get(f"?bootstrap={h.token}", headers=raw, follow_redirects=False)
    assert response.status_code == 400
    assert h.bootstrap().status_code == 303


@pytest.mark.parametrize("query", ["bootstrap={t}&bootstrap={t}", "bootstrap={t}&x=1", "x=1&bootstrap={t}",
                                   "bootstrap"])
def test_bootstrap_requires_exactly_one_query_key(tmp_path, query):
    h = make_harness(tmp_path)
    response = h.client.get("?" + query.format(t=h.token), headers=NAVIGATE, follow_redirects=False)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_bootstrap_is_single_use_and_expires(tmp_path):
    h = make_harness(tmp_path)
    assert h.bootstrap().status_code == 303
    replay = h.bootstrap()
    assert replay.status_code == 401
    assert replay.json() == {"error": {"code": "BOOTSTRAP_INVALID"}}
    (tmp_path / "second").mkdir()
    h2 = make_harness(tmp_path / "second")
    h2.clock.now += BOOTSTRAP_TTL_SECONDS + 1
    assert h2.bootstrap().status_code == 401


def test_document_and_static_assets_require_a_session(tmp_path):
    h = make_harness(tmp_path)
    page = h.client.get("", headers=NAVIGATE)
    assert page.status_code == 401
    assert "Settings session ended" in page.text and "app.js" not in page.text
    for asset in ("static/app.js", "static/styles.css"):
        response = h.client.get(asset, headers={**FETCH, "sec-fetch-dest": "script"})
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "SESSION_EXPIRED"}}
    h.bootstrap()
    assert "app.js" in h.client.get("", headers=NAVIGATE).text
    assert h.client.get("static/app.js", headers=FETCH).status_code == 200


def test_other_prefixes_are_never_this_session(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    other = TestClient(h.client.app, base_url=ORIGIN, cookies=h.client.cookies)
    for path in ("/", "/api/v1/overview", "/static/app.js", "/AAAAAAAAAAAAAAAAAAAAAAAAAAAA/api/v1/overview"):
        response = other.get(path, headers=FETCH)
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "SESSION_NOT_FOUND"}}
    assert other.post("/api/v1/session/keepalive", json={},
                      headers={**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf}).status_code == 404


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:9999", "127.0.0.1", "evil.example"])
def test_non_exact_host_is_rejected(tmp_path, host):
    h = make_harness(tmp_path)
    response = h.client.get(f"?bootstrap={h.token}", headers={**NAVIGATE, "host": host},
                            follow_redirects=False)
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "HOST_REJECTED"}}
    assert h.bootstrap().status_code == 303


def test_forwarded_headers_do_not_change_host_checks(tmp_path):
    h = make_harness(tmp_path)
    forwarded = {**NAVIGATE, "x-forwarded-host": HOST, "forwarded": f"host={HOST}", "host": "evil.example"}
    assert h.client.get(f"?bootstrap={h.token}", headers=forwarded,
                        follow_redirects=False).status_code == 400


def test_csrf_handoff_requires_session_same_origin_and_no_store(tmp_path):
    h = make_harness(tmp_path)
    assert h.client.get("/api/v1/session/csrf", headers=FETCH).json() == {"error": {"code": "SESSION_EXPIRED"}}
    h.bootstrap()
    assert h.client.get("/api/v1/session/csrf", headers={**FETCH, "sec-fetch-site": "cross-site"}).status_code == 403
    assert h.client.get("/api/v1/session/csrf", headers={**FETCH, "sec-fetch-site": "none"}).status_code == 403
    assert h.client.get("/api/v1/session/csrf", headers=NAVIGATE).status_code == 403
    wrong_origin = h.client.get("/api/v1/session/csrf", headers={**FETCH, "origin": "http://evil.example"})
    assert wrong_origin.status_code == 403
    ok = h.client.get("/api/v1/session/csrf", headers={**FETCH, "origin": ORIGIN})
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "no-store"
    csrf = ok.json()["csrf_token"]
    assert len(csrf) >= 43
    assert all(csrf not in value for value in h.client.cookies.values())


def test_mutations_require_origin_and_csrf_header(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    body = {"values": {"system.timezone": "UTC"}, "generation": h.store.load().generation}
    path = "/api/v1/settings/general/validate"
    assert h.post(path, body, None).json()["error"]["code"] == "CSRF_REJECTED"
    assert h.post(path, body, "wrong").json()["error"]["code"] == "CSRF_REJECTED"
    assert h.post(path, body, csrf, origin="http://evil.example").status_code == 403
    no_origin = h.client.post(path, json=body, headers={**FETCH, "x-uls-csrf": csrf})
    assert no_origin.status_code == 403
    with_referer = h.client.post(path, json=body, headers={**FETCH, "x-uls-csrf": csrf,
                                                           "referer": f"{ORIGIN}{h.root}"})
    assert with_referer.status_code == 200
    bad_referer = h.client.post(path, json=body, headers={**FETCH, "x-uls-csrf": csrf,
                                                          "referer": "http://evil.example/"})
    assert bad_referer.status_code == 403
    assert h.post(path, body, csrf, **{"sec-fetch-site": "cross-site"}).status_code == 403
    assert h.post(path, body, csrf).status_code == 200


def test_close_invalidates_session_and_csrf(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    assert h.post("/api/v1/session/close", {}, csrf).status_code == 200
    assert h.closed == [True]
    assert h.client.get("/api/v1/session/csrf", headers=FETCH).status_code == 401
    after = h.post("/api/v1/session/keepalive", {}, csrf)
    assert after.status_code == 401
    assert after.json() == {"error": {"code": "SESSION_EXPIRED"}}
    assert h.bootstrap().status_code == 401


def test_expiry_returns_structured_session_expired(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    h.clock.now += h.security.idle_seconds + 1
    for response in (
        h.client.get("/api/v1/overview", headers=FETCH),
        h.client.get("/api/v1/session/csrf", headers=FETCH),
        h.post("/api/v1/session/keepalive", {}, csrf),
    ):
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "SESSION_EXPIRED"}}


def test_passive_reads_do_not_renew_but_explicit_activity_does(tmp_path):
    h = make_harness(tmp_path)
    h.signed_in()
    idle = h.security.idle_seconds
    h.clock.now += idle * 0.6
    assert h.client.get("/api/v1/overview", headers=FETCH).status_code == 200
    h.clock.now += idle * 0.5
    assert h.client.get("/api/v1/overview", headers=FETCH).status_code == 401

    second = tmp_path / "second"
    second.mkdir()
    h2 = make_harness(second)
    csrf = h2.signed_in()
    h2.clock.now += idle * 0.6
    assert h2.post("/api/v1/session/keepalive", {}, csrf).status_code == 200
    h2.clock.now += idle * 0.5
    assert h2.client.get("/api/v1/overview", headers=FETCH).status_code == 200


def test_replacement_serves_only_session_replaced(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    h.security.replace()
    for response in (
        h.client.get("", headers=NAVIGATE),
        h.client.get("static/app.js"),
        h.client.get("/api/v1/overview", headers=FETCH),
        h.client.get("/api/v1/session/csrf", headers=FETCH),
        h.post("/api/v1/settings/general/apply", {"values": {"system.timezone": "UTC"},
                                                  "generation": "0" * 64}, csrf),
    ):
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "SESSION_REPLACED"}}
    assert h.security.csrf_for(None) is None


class _PortSwitch:
    """One loopback host:port whose listening process changes (port reuse)."""

    def __init__(self, app):
        self.target = app

    async def __call__(self, scope, receive, send):
        await self.target(scope, receive, send)


def test_port_reuse_with_one_browser_cookie_store_never_joins_the_new_session(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = make_harness(tmp_path / "a")
    b = make_harness(tmp_path / "b")
    switch = _PortSwitch(a.client.app)
    browser = TestClient(switch, base_url=ORIGIN)  # one host-wide cookie store, like a browser
    assert browser.get(f"{a.root}?bootstrap={a.token}", headers=NAVIGATE,
                       follow_redirects=False).status_code == 303
    csrf_a = browser.get(f"{a.root}api/v1/session/csrf", headers=FETCH).json()["csrf_token"]

    # A is replaced and exits; B binds the very same 127.0.0.1 port.
    a.security.replace()
    switch.target = b.client.app
    assert browser.get(f"{b.root}?bootstrap={b.token}", headers=NAVIGATE,
                       follow_redirects=False).status_code == 303
    assert len(list(browser.cookies.jar)) == 2  # both cookies live in one store, path-scoped

    # The old tab keeps using its own URLs: B never treats them as its session.
    for response in (
        browser.get(f"{a.root}api/v1/overview", headers=FETCH),
        browser.get(f"{a.root}", headers=NAVIGATE),
        browser.post(f"{a.root}api/v1/session/keepalive", json={},
                     headers={**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf_a}),
    ):
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "SESSION_NOT_FOUND"}}
    # Even if the old tab reached B's prefix, its stale CSRF is refused.
    stale = browser.post(f"{b.root}api/v1/session/keepalive", json={},
                         headers={**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf_a})
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "CSRF_REJECTED"
    assert browser.get(f"{b.root}api/v1/overview", headers=FETCH).status_code == 200


def test_security_headers_no_cors_and_body_limit(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    page = h.client.get("", headers={**NAVIGATE, "origin": "http://evil.example"})
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.headers["cross-origin-resource-policy"] == "same-origin"
    assert "camera=()" in page.headers["permissions-policy"]
    assert "access-control-allow-origin" not in page.headers
    preflight = h.client.options("/api/v1/settings/general/apply", headers={
        "origin": "http://evil.example", "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in preflight.headers
    assert preflight.status_code >= 400
    huge = h.client.post("/api/v1/settings/general/validate", content=b"x" * 20000,
                         headers={**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf,
                                  "content-type": "application/json"})
    assert huge.status_code == 413


def test_deferred_remote_scheduler_and_mcp_routes_do_not_exist(tmp_path):
    h = make_harness(tmp_path)
    csrf = h.signed_in()
    for path in ("/api/v1/remote/enable",
                 "/api/v1/oauth/grants", "/mcp", "/api/v1/scheduler"):
        assert h.client.get(path, headers=FETCH).status_code == 404
        assert h.post(path, {}, csrf).status_code in {404, 405}


def test_no_session_values_in_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    h = make_harness(tmp_path)
    response = h.bootstrap()
    csrf = h.csrf()
    handle = response.cookies.get(SESSION_COOKIE) or h.client.cookies.get(SESSION_COOKIE)
    h.post("/api/v1/session/keepalive", {}, csrf)
    # The test client itself logs request URLs; only server-side records count.
    server_text = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )
    for value in (h.token, csrf, handle, h.prefix):
        assert value and value not in server_text


def test_overview_reports_replacement_notice_and_separates_runtime_health(tmp_path):
    h = make_harness(tmp_path, replaced_previous=True)
    h.signed_in()
    data = h.client.get("/api/v1/overview", headers=FETCH).json()
    assert data["session_notice"] == "replaced_previous"
    assert [step["name"] for step in data["setup_steps"]] == [
        "Storage", "Canvas", "Academic", "Automation", "Remote", "Check"]
    assert data["setup_ready"] is False
    assert isinstance(data["local_runtime_healthy"], bool)
    assert "status" not in data
    assert data["doctor"]["credential_readiness"] == "Not checked in Local Settings"
