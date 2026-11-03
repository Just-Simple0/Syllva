"""R2-4: relay authentication in stdio mode (errata E29, R2 plan §7; C-04, C-13, C-20).

The webview sends neither Origin nor Cookie through syllva:// (measured), so in
stdio mode the Core accepts only requests the Shell relay signed for a window:
``X-Syllva-Window`` + ``X-Syllva-Relay`` (shell_secret HMAC over the label). The
session is bound to that window on every request. Headless mode ignores these
headers entirely and keeps the Origin rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.shell_sim import relay_headers
from syllva.core.secret import Secret
from syllva.ui.http import create_app
from syllva.ui.relay import RelayVerifier, relay_mac
from syllva.ui.sessions import SessionStore

pytestmark = pytest.mark.contract

SECRET = bytes(range(32))
PORT = 49153
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def store() -> SessionStore:
    return SessionStore()


@pytest.fixture
def client(store: SessionStore) -> TestClient:
    app = create_app(store, port=PORT, allowed_origins=("syllva://localhost",), relay=RelayVerifier(Secret(SECRET)))
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def _session(client: TestClient, store: SessionStore, window: str = "main") -> tuple[str, str]:
    response = client.post("/api/session", json={"bootstrap": store.issue_bootstrap(window)}, headers=relay_headers(SECRET, window))
    assert response.status_code == 200, response.text
    sid = response.cookies.get("syllva_sid") or client.cookies.get("syllva_sid")
    assert sid
    return response.json()["csrf"], sid


def test_relay_mac_vectors_are_shared_with_the_shell() -> None:
    for case in json.loads((FIXTURES / "relay_mac_vectors.json").read_text(encoding="utf-8")):
        assert relay_mac(bytes.fromhex(case["key_hex"]), case["label"]).hex() == case["mac_hex"]


def test_nothing_but_healthz_works_without_the_relay(client: TestClient, store: SessionStore) -> None:
    assert client.get("/healthz").status_code == 200
    for method, path in (("GET", "/bootstrap"), ("GET", "/static/app.js"), ("GET", "/api/session"), ("POST", "/api/session")):
        assert client.request(method, path, headers={"Origin": "syllva://localhost"}).status_code == 403, path


def test_cookie_csrf_and_origin_are_not_enough_without_the_relay(client: TestClient, store: SessionStore) -> None:
    csrf, _ = _session(client, store)
    response = client.request("DELETE", "/api/session", headers={"X-Syllva-CSRF": csrf, "Origin": "syllva://localhost"})
    assert response.status_code == 403


@pytest.mark.parametrize("tamper", ["other-label", "bad-mac", "duplicate", "bad-label"])
def test_wrong_relay_headers_are_refused(client: TestClient, tamper: str) -> None:
    headers = relay_headers(SECRET, "main")
    if tamper == "other-label":
        headers["X-Syllva-Window"] = "notes"
    elif tamper == "bad-mac":
        headers = relay_headers(b"\x00" * 32, "main")
    elif tamper == "bad-label":
        headers = relay_headers(SECRET, "Bad Label")
    if tamper == "duplicate":
        raw = [("x-syllva-window", "main"), ("x-syllva-window", "main"), ("x-syllva-relay", headers["X-Syllva-Relay"])]
        assert client.get("/bootstrap", headers=raw).status_code == 403  # type: ignore[arg-type]
    else:
        assert client.get("/bootstrap", headers=headers).status_code == 403


def test_bootstrap_must_be_exchanged_by_its_window(client: TestClient, store: SessionStore) -> None:
    token = store.issue_bootstrap("main")
    refused = client.post("/api/session", json={"bootstrap": token}, headers=relay_headers(SECRET, "side"))
    assert refused.status_code == 403
    again = client.post("/api/session", json={"bootstrap": token}, headers=relay_headers(SECRET, "main"))
    assert again.status_code == 403  # consumed by the refused attempt


def test_a_session_is_usable_only_from_its_window(client: TestClient, store: SessionStore) -> None:
    _session(client, store, "main")
    assert client.get("/api/session", headers=relay_headers(SECRET, "main")).json() == {"window_label": "main"}
    wrong = client.get("/api/session", headers=relay_headers(SECRET, "side"))
    assert wrong.status_code == 403 and wrong.json() == {"error": "WRONG_WINDOW"}
    assert client.get("/api/session", headers=relay_headers(SECRET, "main")).status_code == 200  # still alive


def test_origin_if_present_must_be_the_app(client: TestClient, store: SessionStore) -> None:
    csrf, _ = _session(client, store)
    headers = {**relay_headers(SECRET), "X-Syllva-CSRF": csrf, "Origin": "http://127.0.0.1:1"}
    assert client.request("DELETE", "/api/session", headers=headers).status_code == 403
    headers["Origin"] = "syllva://localhost"
    assert client.request("DELETE", "/api/session", headers=headers).status_code == 200


def test_relayed_mutation_without_origin_works(client: TestClient, store: SessionStore) -> None:
    csrf, _ = _session(client, store)
    response = client.request("DELETE", "/api/session", headers={**relay_headers(SECRET), "X-Syllva-CSRF": csrf})
    assert response.status_code == 200


def test_headless_ignores_relay_headers_and_keeps_the_origin_rule(store: SessionStore) -> None:
    app = create_app(store, port=PORT, allowed_origins=(f"http://127.0.0.1:{PORT}",))
    headless = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    token = store.issue_bootstrap("main")
    assert headless.post("/api/session", json={"bootstrap": token}, headers=relay_headers(SECRET)).status_code == 403  # no Origin
    ok = headless.post("/api/session", json={"bootstrap": token}, headers={"Origin": f"http://127.0.0.1:{PORT}"})
    assert ok.status_code == 200


def test_relay_mac_never_reaches_logs(client: TestClient, tmp_path: Path) -> None:
    from syllva.core import logging as core_logging

    core_logging.configure(tmp_path)
    try:
        bad = relay_headers(b"\x00" * 32, "main")
        client.get("/bootstrap", headers=bad)
        logged = b"".join(p.read_bytes() for p in tmp_path.glob("*.jsonl"))
        assert bad["X-Syllva-Relay"].encode() not in logged and b"UI_DENIED" in logged
    finally:
        core_logging.configure(None)


def test_secret_reveal_has_two_callers() -> None:
    src = Path(__file__).resolve().parents[3] / "src"
    callers = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py") if ".reveal()" in p.read_text(encoding="utf-8"))
    assert callers == ["syllva/gate/service.py", "syllva/ui/relay.py"]
