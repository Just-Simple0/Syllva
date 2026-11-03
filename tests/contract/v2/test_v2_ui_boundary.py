"""R2-1: UI sessions and the HTTP boundary (spec §10.1-§10.4, design §5.1(a); C-04 session half, C-13, C-18 headers).

In-process: the Starlette app over a real SessionStore, through Starlette's
test client. Host must match exactly, mutating requests need the app Origin and
the CSRF header, bootstrap tokens are single-use and window-bound, one session
per window.
"""

from __future__ import annotations

import threading

import pytest
from starlette.testclient import TestClient

from syllva.ui.http import create_app
from syllva.ui.sessions import SessionStore

pytestmark = pytest.mark.contract

PORT = 49152
HOST = f"127.0.0.1:{PORT}"
ORIGIN = "syllva://localhost"
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


@pytest.fixture
def store() -> SessionStore:
    return SessionStore()


@pytest.fixture
def client(store: SessionStore) -> TestClient:
    app = create_app(store, port=PORT, allowed_origins=(ORIGIN,))
    return TestClient(app, base_url=f"http://{HOST}")


def _session(client: TestClient, store: SessionStore, window: str = "main") -> str:
    token = store.issue_bootstrap(window)
    response = client.post("/api/session", json={"bootstrap": token}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    csrf: str = response.json()["csrf"]
    return csrf


def test_security_headers_on_every_response(client: TestClient) -> None:
    for path in ("/healthz", "/bootstrap", "/nope"):
        headers = client.get(path).headers
        assert headers["content-security-policy"] == CSP
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cross-origin-opener-policy"] == "same-origin"
    assert client.get("/healthz").headers["cache-control"] == "no-store"
    assert client.get("/healthz").json() == {"ok": True}


@pytest.mark.parametrize("host", ["localhost:49152", "127.0.0.1:1", "evil.example", "127.0.0.1"])
def test_host_must_match_exactly(client: TestClient, host: str) -> None:
    """DNS rebinding: any other Host is refused before routing."""

    assert client.get("/healthz", headers={"Host": host}).status_code == 421


def test_bootstrap_is_single_use_and_sets_a_strict_cookie(client: TestClient, store: SessionStore) -> None:
    token = store.issue_bootstrap("main")
    first = client.post("/api/session", json={"bootstrap": token}, headers={"Origin": ORIGIN})
    assert first.status_code == 200
    cookie = first.headers["set-cookie"]
    assert cookie.startswith("syllva_sid=") and "HttpOnly" in cookie and "samesite=strict" in cookie.lower() and "Path=/" in cookie
    assert "csrf" in first.json() and first.json()["window_label"] == "main"
    again = client.post("/api/session", json={"bootstrap": token}, headers={"Origin": ORIGIN})
    assert again.status_code == 403


def test_concurrent_exchange_has_exactly_one_winner(store: SessionStore) -> None:
    token = store.issue_bootstrap("main")
    results: list[object] = []
    barrier = threading.Barrier(8)

    def go() -> None:
        barrier.wait()
        results.append(store.exchange(token))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1


def test_bootstrap_expires(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from syllva.ui import sessions

    token = store.issue_bootstrap("main")
    real = sessions.time.monotonic
    monkeypatch.setattr(sessions.time, "monotonic", lambda: real() + 61)
    assert store.exchange(token) is None


def test_one_session_per_window(client: TestClient, store: SessionStore) -> None:
    _session(client, store, "main")
    first_sid = client.cookies.get("syllva_sid")
    _session(client, store, "main")
    assert first_sid is not None and store.lookup(first_sid) is None  # the newer session replaced it


def test_mutations_need_origin_and_csrf(client: TestClient, store: SessionStore) -> None:
    csrf = _session(client, store)
    assert client.get("/api/session").status_code == 200
    assert client.request("DELETE", "/api/session", headers={"Origin": ORIGIN}).status_code == 403  # no CSRF
    assert client.request("DELETE", "/api/session", headers={"X-Syllva-CSRF": csrf}).status_code == 403  # no Origin
    assert client.request(
        "DELETE", "/api/session", headers={"X-Syllva-CSRF": csrf, "Origin": "http://127.0.0.1:1"}
    ).status_code == 403
    assert client.request("DELETE", "/api/session", headers={"X-Syllva-CSRF": csrf, "Origin": ORIGIN}).status_code == 200
    assert client.get("/api/session").status_code == 401  # logged out


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_fetch_metadata_other_sites_are_refused(client: TestClient, store: SessionStore, site: str) -> None:
    _session(client, store)
    assert client.get("/api/session", headers={"Sec-Fetch-Site": site}).status_code == 403


def test_no_session_no_api(client: TestClient) -> None:
    assert client.get("/api/session").status_code == 401


def test_idle_sessions_expire(client: TestClient, store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from syllva.ui import sessions

    _session(client, store)
    real = sessions.time.monotonic
    monkeypatch.setattr(sessions.time, "monotonic", lambda: real() + 8 * 3600 + 1)
    assert client.get("/api/session").status_code == 401


def test_bootstrap_page_reads_the_fragment_and_clears_it(client: TestClient) -> None:
    page = client.get("/bootstrap")
    assert page.status_code == 200 and "<script" in page.text and "inline" not in page.text
    script = client.get("/static/app.js").text
    assert "location.hash" in script and "history.replaceState" in script
    assert "localStorage" not in script and "innerHTML" not in script


def test_session_body_rejects_extra_keys_and_large_bodies(client: TestClient, store: SessionStore) -> None:
    token = store.issue_bootstrap("main")
    assert client.post("/api/session", json={"bootstrap": token, "x": 1}, headers={"Origin": ORIGIN}).status_code == 400
    big = "a" * 70000
    assert client.post("/api/session", content=big, headers={"Origin": ORIGIN, "Content-Type": "application/json"}).status_code == 413


def test_expired_tokens_and_idle_sessions_are_pruned(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from syllva.ui import sessions

    for n in range(50):
        store.issue_bootstrap(f"w{n}")
    session = store.exchange(store.issue_bootstrap("main"))
    assert session is not None
    real = sessions.time.monotonic
    monkeypatch.setattr(sessions.time, "monotonic", lambda: real() + 8 * 3600 + 61)
    store.issue_bootstrap("fresh")
    assert store.counts() == (1, 0)


def test_oversized_chunked_bodies_are_refused(client: TestClient, store: SessionStore) -> None:
    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(10):
            yield b"x" * 8000

    response = client.post("/api/session", content=chunks(), headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert response.status_code == 413
