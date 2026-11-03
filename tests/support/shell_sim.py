"""Test-only Shell simulator (spec §6.7): drives ``syllva core --stdio`` like the Tauri Shell.

Never imported outside ``tests/`` (checked by test_v2_core_stdio.py). Production
code has exactly one way to receive ``shell_secret``: the ``hello`` line.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ENV_ALLOWLIST = ("PATH", "HOME", "USERPROFILE", "LANG", "SYSTEMROOT", "PYTHONPATH")


class ShellSim:
    def __init__(self, data_dir: Path, *, secret: bytes | None = None, extra_args: tuple[str, ...] = ()) -> None:
        self.secret = secret if secret is not None else secrets.token_bytes(32)
        env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
        env["SYLLVA_DATA_DIR"] = str(data_dir)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "syllva", "core", "--stdio", *extra_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self.raw_stdout: list[bytes] = []
        threading.Thread(target=self._pump, daemon=True).start()
        self._stderr: list[bytes] = []
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.raw_stdout.append(line)
            self._lines.put(line)
        self._lines.put(None)

    def _pump_err(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line)

    @property
    def stderr(self) -> bytes:
        return b"".join(self._stderr)

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, message: dict[str, Any]) -> None:
        self.send_raw(json.dumps({"v": 1, **message}).encode() + b"\n")

    def hello(self, **overrides: Any) -> dict[str, Any]:
        message = {
            "t": "hello", "secret_b64": base64.urlsafe_b64encode(self.secret).decode().rstrip("="),
            "shell_version": "0.0.0-test", "platform": "linux", **overrides,
        }
        self.send(message)
        return self.recv()

    def recv(self, timeout: float = 20.0) -> dict[str, Any]:
        line = self._lines.get(timeout=timeout)
        if line is None:
            raise EOFError("core closed stdout")
        parsed: dict[str, Any] = json.loads(line)
        return parsed

    def close_stdin(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()

    def wait(self, timeout: float = 20.0) -> int:
        return self.proc.wait(timeout=timeout)

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send({"t": "shutdown", "grace_ms": 1000})
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def wait_port_closed(port: int, timeout: float = 5.0) -> bool:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.05)
    return False
