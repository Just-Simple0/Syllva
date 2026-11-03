"""C-19 (active; shell half here, summary binding in test_v2_gate.py): the Tauri shell exposes only confirm_gate and window controls (spec §13.6, §6.8).

Static checks over shell/app (no webview needed): the capability lists an exact,
enumerated permission set; no plugin crates; no remote-URL IPC; the only app
command is confirm_gate taking a nonce id; the relay identifies the window with
ctx.webview_label() and the gate uses the same label namespace; navigation is
limited; Set-Cookie/Cookie and X-Syllva-* never pass between webview and Core.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

SHELL = Path(__file__).resolve().parents[3] / "shell"
APP = SHELL / "app"
ALLOWED_PERMISSIONS = {
    "core:window:allow-minimize", "core:window:allow-maximize", "core:window:allow-unmaximize",
    "core:window:allow-close", "core:window:allow-start-dragging",
}


def _src(name: str) -> str:
    return (APP / "src" / name).read_text(encoding="utf-8")


def test_capabilities_are_exactly_the_enumerated_window_controls() -> None:
    files = sorted((APP / "capabilities").glob("*.json"))
    assert [f.name for f in files] == ["main.json"]
    capability = json.loads(files[0].read_text(encoding="utf-8"))
    assert capability["windows"] == ["main"]
    assert set(capability["permissions"]) == ALLOWED_PERMISSIONS
    assert "remote" not in capability  # no remote-URL IPC (design rev 5)


def test_no_tauri_plugins_and_no_devtools_feature() -> None:
    manifest = (APP / "Cargo.toml").read_text(encoding="utf-8")
    assert "tauri-plugin" not in manifest
    tauri_line = next(line for line in manifest.splitlines() if line.startswith("tauri ="))
    assert "devtools" not in tauri_line
    config = json.loads((APP / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["app"]["security"]["capabilities"] == ["main"]
    assert "plugins" not in config or not config["plugins"]


def test_the_only_command_is_confirm_gate_with_a_nonce() -> None:
    main = _src("main.rs")
    assert re.findall(r"generate_handler!\[([^\]]*)\]", main) == ["gate::confirm_gate"]
    gate = _src("gate.rs")
    signature = gate[gate.index("pub async fn confirm_gate("):gate.index(") -> Result<GateResult")]
    params = re.findall(r"^\s*(\w+):", signature.split("(", 1)[1], re.MULTILINE)
    assert params == ["webview", "core", "secret", "nonce_id"]  # the webview supplies only nonce_id
    assert "struct GateResult" in gate and "summary" not in gate[gate.index("struct GateResult"):gate.index("fn object")]


def test_window_identity_comes_from_tauri_on_both_paths() -> None:
    assert "ctx.webview_label()" in _src("main.rs")
    assert "webview.label()" in _src("gate.rs")


def test_navigation_is_limited_to_the_app_origin() -> None:
    assert ".on_navigation(relay::allowed_navigation)" in _src("main.rs")
    relay = _src("relay.rs")
    assert '("syllva", Some("localhost")) => true' in relay and "_ => false" in relay


def test_relay_never_passes_cookies_or_relay_headers_between_webview_and_core() -> None:
    proto = (SHELL / "proto" / "src" / "relay.rs").read_text(encoding="utf-8")
    request = re.search(r"REQUEST_HEADERS: &\[&str\] = &\[(.*?)\];", proto, re.DOTALL)
    response = re.search(r"RESPONSE_HEADERS: &\[&str\] = &\[(.*?)\];", proto, re.DOTALL)
    assert request and response
    assert not re.search(r'"(cookie|x-syllva-window|x-syllva-relay|host)"', request.group(1))
    assert '"set-cookie"' not in response.group(1)


def test_core_origins_match_the_shell_urls() -> None:
    from syllva.core.stdio import APP_ORIGINS

    main = _src("main.rs")
    assert "syllva://localhost/bootstrap#" in main and "http://syllva.localhost/bootstrap#" in main
    assert APP_ORIGINS["linux"] == APP_ORIGINS["darwin"] == ("syllva://localhost",)
    assert APP_ORIGINS["win32"] == ("http://syllva.localhost",)
