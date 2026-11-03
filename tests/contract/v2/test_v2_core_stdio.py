"""R2-1: Core stdio protocol, lifecycle and secret handling (spec §2, §13.1; C-13, C-20 Python half).

Drives the real ``syllva core --stdio`` process through the test-only Shell
simulator. The agent-child half of C-20 (pipes and env of agent processes) is
pending until R4, when the single spawn path exists.
"""

from __future__ import annotations

import ast
import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.shell_sim import ShellSim, wait_port_closed

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
SENTINEL = b"S" * 32  # recognisable 32-byte secret


@pytest.fixture
def sim(tmp_path: Path):  # type: ignore[no-untyped-def]
    shell = ShellSim(tmp_path / "data", secret=SENTINEL)
    yield shell
    shell.stop()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_ready_reports_a_loopback_port_and_a_bootstrap_token(sim: ShellSim) -> None:
    ready = sim.hello()
    assert ready["t"] == "ready" and ready["v"] == 1
    assert isinstance(ready["port"], int) and ready["port"] > 0
    assert len(base64.urlsafe_b64decode(ready["bootstrap"] + "==")) == 32
    response = httpx.get(f"http://127.0.0.1:{ready['port']}/healthz", headers={"Host": f"127.0.0.1:{ready['port']}"})
    assert response.json() == {"ok": True}


def test_ping_pong_and_clean_shutdown(sim: ShellSim) -> None:
    ready = sim.hello()
    sim.send({"t": "ping", "seq": 7})
    assert sim.recv() == {"t": "pong", "v": 1, "seq": 7}
    sim.send({"t": "shutdown", "grace_ms": 1000})
    assert sim.recv()["t"] == "bye"
    assert sim.wait() == 0
    assert wait_port_closed(ready["port"])


def test_unknown_message_type_is_an_error_but_not_fatal(sim: ShellSim) -> None:
    sim.hello()
    sim.send({"t": "nonsense"})
    reply = sim.recv()
    assert reply["t"] == "error" and reply["code"] == "UNKNOWN_TYPE"
    sim.send({"t": "ping", "seq": 1})
    assert sim.recv()["t"] == "pong"


def test_stdin_eof_means_the_shell_is_gone(sim: ShellSim) -> None:
    ready = sim.hello()
    sim.close_stdin()
    assert sim.wait() == 5
    assert wait_port_closed(ready["port"])


@pytest.mark.parametrize(
    "first",
    [
        b'{"v":1,"t":"ping","seq":1}\n',  # anything before hello
        b"not json\n",
        b'{"v":1,"t":"hello","secret_b64":"c2hvcnQ","shell_version":"x","platform":"linux"}\n',  # 5-byte secret
        b'{"v":2,"t":"hello","secret_b64":"' + _b64(SENTINEL).encode() + b'","shell_version":"x","platform":"linux"}\n',
        b'{"v":1,"t":"hello","secret_b64":"' + _b64(SENTINEL).encode() + b'","shell_version":"x","platform":"beos"}\n',
        b"{" + b'"pad":"' + b"x" * 70000 + b'"}\n',  # over 64 KiB
    ],
)
def test_a_bad_first_line_is_a_protocol_violation(tmp_path: Path, first: bytes) -> None:
    shell = ShellSim(tmp_path / "data", secret=SENTINEL)
    try:
        shell.send_raw(first)
        assert shell.wait() == 2
        assert not (tmp_path / "data" / "state.sqlite3").exists()  # nothing opened before a valid hello
    finally:
        shell.stop()


def test_a_second_hello_is_a_protocol_violation(sim: ShellSim) -> None:
    sim.hello()
    sim.send({"t": "hello", "secret_b64": _b64(SENTINEL), "shell_version": "x", "platform": "linux"})
    assert sim.wait() == 2


def test_a_second_core_on_the_same_library_exits_db_locked(sim: ShellSim, tmp_path: Path) -> None:
    sim.hello()
    other = ShellSim(tmp_path / "data")
    try:
        reply = other.hello()
        assert reply == {"t": "error", "v": 1, "code": "DB_LOCKED", "message": reply["message"]}
        assert other.wait() == 3
    finally:
        other.stop()


def test_new_window_tokens_come_only_from_the_shell(sim: ShellSim) -> None:
    sim.hello()
    sim.send({"t": "session.bootstrap", "window_label": "notes"})
    reply = sim.recv()
    assert reply["t"] == "session.bootstrap_ok" and reply["window_label"] == "notes"
    sim.send({"t": "session.bootstrap", "window_label": "Bad Label!"})
    assert sim.recv()["code"] == "BAD_WINDOW_LABEL"


def test_the_secret_never_leaves_the_core(sim: ShellSim, tmp_path: Path) -> None:
    """C-20: the sentinel secret (raw and base64) is in no stderr, log file, stdout line or HTTP response."""

    ready = sim.hello()
    port = ready["port"]
    base = f"http://127.0.0.1:{port}"
    host = {"Host": f"127.0.0.1:{port}"}
    bodies = [
        httpx.get(base + "/healthz", headers=host).content,
        httpx.get(base + "/bootstrap", headers=host).content,
        httpx.post(base + "/api/session", json={"bootstrap": "x"}, headers={**host, "Origin": "syllva://app"}).content,
    ]
    sim.send({"t": "nonsense", "secret_b64": _b64(SENTINEL)})  # echo bait
    sim.recv()
    sim.send({"t": "shutdown", "grace_ms": 500})
    sim.recv()
    sim.wait()
    logs = b"".join(p.read_bytes() for p in (tmp_path / "data").rglob("*.jsonl"))
    haystacks = [sim.stderr, b"".join(sim.raw_stdout), logs, *bodies]
    for needle in (SENTINEL, _b64(SENTINEL).encode(), base64.b64encode(SENTINEL)):
        assert all(needle not in hay for hay in haystacks)
    assert logs, "the core writes a structured log"
    for line in logs.splitlines():
        json.loads(line)


def test_every_stdout_line_is_protocol_json(sim: ShellSim) -> None:
    sim.hello()
    sim.send({"t": "ping", "seq": 1})
    sim.recv()
    sim.stop()
    for line in sim.raw_stdout:
        assert json.loads(line)["v"] == 1


def test_the_ui_server_binds_loopback_only() -> None:
    """C-13: 0.0.0.0 (or any non-loopback host) is refused."""

    from syllva.core.stdio import bind_loopback

    with pytest.raises(ValueError):
        bind_loopback("0.0.0.0")
    sock = bind_loopback()
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


def test_core_and_ui_do_not_load_remote_mcp_transports() -> None:
    """C-13: the remote MCP transport is not reachable from the v2 Core or UI."""

    for package in ("core", "ui", "gate"):
        for path in (ROOT / "src" / "syllva" / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                assert not any(n.startswith("syllva.mcp") for n in names), path


def test_the_shell_simulator_is_test_only() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        assert "shell_sim" not in path.read_text(encoding="utf-8"), path


def test_ping_silence_ends_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2.4: 30 s without ping means the Shell is gone (exercised in-process with a short clock)."""

    import threading

    from syllva.core import stdio

    class SilentChannel:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self._never = threading.Event()

        def read(self) -> None:
            self._never.wait()  # the Shell says nothing

        def send(self, message: dict[str, object]) -> None:
            self.sent.append(message)

    monkeypatch.setattr(stdio, "PING_TIMEOUT_S", 0.2)
    assert stdio._loop(SilentChannel(), sessions=None) == stdio.EXIT_SHELL_GONE  # type: ignore[arg-type]
