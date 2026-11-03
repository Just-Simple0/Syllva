"""Smoke test of a packaged Core sidecar (implementation spec §13.6; R2-5 bundle spike).

Usage: python scripts/smoke_core_bundle.py dist/syllva-core/syllva-core[.exe]

1. ``--self-check``: pypdf, keyring, sqlite JSON, uvicorn loop/protocol modules, resources.
2. ``--stdio`` the way the Shell drives it (allowlisted environment, fresh data directory):
   hello -> ready, relay-authenticated HTTP (healthz, session exchange, library listing),
   shutdown -> bye, exit 0.
Standard library only, so it also runs where only the bundle exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ENV_ALLOWLIST = ("PATH", "HOME", "USERPROFILE", "LANG", "SYSTEMROOT")
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
KEYCHAIN = {"darwin": "keyring.backends.macOS.Keyring", "win32": "keyring.backends.Windows.WinVaultKeyring"}
PLATFORM = {"darwin": "darwin", "win32": "win32"}.get(sys.platform, "linux")


def relay_headers(secret: bytes, label: str = "main") -> dict[str, str]:
    raw = label.encode()
    tag = hmac.new(secret, b"syllva-relay-v1\x00" + len(raw).to_bytes(4, "big") + raw, hashlib.sha256).digest()
    return {"X-Syllva-Window": label, "X-Syllva-Relay": base64.urlsafe_b64encode(tag).decode().rstrip("=")}


def http(port: int, method: str, path: str, headers: dict[str, str], body: Any = None) -> tuple[int, dict[str, Any], str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={**headers, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}"), response.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), ""


def check(condition: bool, what: str) -> None:
    print(("ok   " if condition else "FAIL ") + what)
    if not condition:
        raise SystemExit(1)


def main(exe: str) -> int:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    with tempfile.TemporaryDirectory() as data_dir:
        env["SYLLVA_DATA_DIR"] = data_dir
        report = subprocess.run([exe, "--self-check"], check=False, capture_output=True, env=env, timeout=120)
        result = json.loads(report.stdout or b"{}")
        for name, item in result.get("checks", {}).items():
            print(f"     {name}: {item.get('detail')}")
        check(report.returncode == 0 and result.get("ok") is True, "self-check")
        if PLATFORM in KEYCHAIN:  # Linux CI has no Secret Service; there the fail backend is expected
            backend = str(result["checks"]["keyring"]["detail"])
            check(backend == KEYCHAIN[PLATFORM], f"platform keychain backend ({backend})")

        secret = secrets.token_bytes(32)
        proc = subprocess.Popen([exe, "core", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)
        assert proc.stdin is not None and proc.stdout is not None

        def send(message: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps({"v": 1, **message}).encode() + b"\n")
            proc.stdin.flush()

        def recv() -> dict[str, Any]:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            return json.loads(line) if line else {}

        try:
            send({"t": "hello", "secret_b64": base64.urlsafe_b64encode(secret).decode().rstrip("="),
                  "shell_version": "0.0.0-smoke", "platform": PLATFORM})
            ready = recv()
            check(ready.get("t") == "ready" and isinstance(ready.get("port"), int), "hello -> ready")
            port, auth = ready["port"], relay_headers(secret)
            check(http(port, "GET", "/healthz", {})[0] == 200, "healthz")
            check(http(port, "GET", "/api/library/items", {})[0] == 403, "relay authentication required")
            status, body, cookie = http(port, "POST", "/api/session", auth, {"bootstrap": ready["bootstrap"]})
            check(status == 200 and "csrf" in body and "syllva_sid=" in cookie, "session exchange")
            sid = {"Cookie": cookie.split(";", 1)[0]}
            status, body, _ = http(port, "GET", "/api/library/items", {**auth, **sid})
            check(status == 200 and body == {"items": []}, "library listing (migrated empty library)")
            send({"t": "ping", "seq": 1})
            check(recv().get("t") == "pong", "ping -> pong")
            send({"t": "shutdown", "grace_ms": 1000})
            check(recv().get("t") == "bye", "shutdown -> bye")
            check(proc.wait(timeout=20) == 0, "exit 0")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
    print("bundle smoke passed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        sys.stderr.write(__doc__ or "")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
