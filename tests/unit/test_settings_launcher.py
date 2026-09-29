"""Local Settings launcher: loopback reservation, ownership, and two-stage replacement."""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from uls.config.mutation import ConfigFileLock
from uls.orchestration.locks import LocalFileLock
from uls.settings import launcher
from uls.settings.launcher import (
    ControlHandler,
    SettingsLaunchError,
    acquire_ownership,
    bootstrap_url,
    control_socket_path,
    owner_lock_path,
    peer_identity,
    read_owner_record,
    reserve_loopback_socket,
    runtime_directory,
    uvicorn_config,
)

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]
NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-site": "none"}
FETCH = {"sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}


def _can_bind_loopback() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError:
        return False
    finally:
        probe.close()
    return True


needs_loopback = pytest.mark.skipif(not _can_bind_loopback(), reason="loopback bind not permitted here")


@pytest.fixture
def short_runtime():
    # AF_UNIX socket paths are length-limited; pytest temp paths can exceed it.
    return runtime_directory(tempfile.mkdtemp(prefix="uls-rt-", dir="/tmp"))


def test_runtime_directory_is_private(tmp_path, short_runtime):
    if os.name != "nt":
        assert short_runtime.stat().st_mode & 0o777 == 0o700
    link = tmp_path / "link"
    link.symlink_to(short_runtime)
    with pytest.raises(SettingsLaunchError):
        runtime_directory(link)
    with pytest.raises(SettingsLaunchError) as error:
        runtime_directory(tmp_path / ("x" * 120))
    assert error.value.code == "RUNTIME_DIR_TOO_LONG"


def test_bootstrap_url_and_uvicorn_settings():
    assert bootstrap_url(4321, "P" * 24, "tok") == f"http://127.0.0.1:4321/{'P' * 24}/?bootstrap=tok"
    config = uvicorn_config(object())
    assert config.access_log is False
    assert config.proxy_headers is False
    assert config.reload is False
    assert config.workers in (None, 1)
    assert config.lifespan == "off"
    assert config.server_header is False


def _write_config(tmp_path: Path) -> Path:
    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    data["system"]["workspace_dir"] = str(tmp_path / "workspace")
    data["behavior_contract"]["path"] = str(REPO / "contracts" / "study-behavior.md")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_windows_is_refused_before_any_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_IS_WINDOWS", True)
    runtime = tmp_path / "rt"
    messages: list[str] = []
    urls: list[str] = []
    result = launcher.run_setup(_write_config(tmp_path), open_browser=False, runtime_dir=runtime,
                                announce=messages.append, emit_url=urls.append)
    assert result == {"status": "failed", "code": "PLATFORM_UNSUPPORTED"}
    assert not runtime.exists() and urls == []
    assert "not available on Windows" in messages[0]
    with pytest.raises(SettingsLaunchError):
        runtime_directory(runtime)
    assert not runtime.exists()


def test_cli_setup_refuses_windows_before_runtime_state(tmp_path, monkeypatch, capsys):
    from uls.cli.main import main

    monkeypatch.setattr(launcher, "_IS_WINDOWS", True)
    runtime = tmp_path / "rt"
    monkeypatch.setenv(launcher.RUNTIME_DIR_ENV, str(runtime))
    assert main(["--config", str(_write_config(tmp_path)), "setup", "--no-browser"]) == 1
    assert "PLATFORM_UNSUPPORTED" in capsys.readouterr().out
    assert not runtime.exists()


def test_missing_web_extra_is_reported_before_any_runtime_state(tmp_path, monkeypatch):
    real_find_spec = launcher.importlib.util.find_spec
    monkeypatch.setattr(launcher.importlib.util, "find_spec",
                        lambda name, *a: None if name == "starlette" else real_find_spec(name, *a))
    runtime = tmp_path / "rt"
    messages: list[str] = []
    result = launcher.run_setup(_write_config(tmp_path), open_browser=False, runtime_dir=runtime,
                                announce=messages.append, emit_url=lambda _u: None)
    assert result == {"status": "failed", "code": "WEB_EXTRA_MISSING"}
    assert "university-learning-system[web]" in messages[0]
    assert not runtime.exists()


@needs_loopback
def test_reserve_loopback_socket_binds_exact_loopback_port_zero():
    sock = reserve_loopback_socket()
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1" and port > 0
    finally:
        sock.close()


def test_control_handler_prepare_has_no_effect_and_commit_needs_the_nonce():
    calls: list[bool] = []
    handler = ControlHandler("lock-token", lambda: calls.append(True))
    good = {"v": 2, "op": "prepare", "owner_pid": os.getpid(), "lock_token": "lock-token"}
    for request, uid in [
        (good, None), (good, os.getuid() + 1),
        ({**good, "lock_token": "wrong"}, os.getuid()),
        ({**good, "owner_pid": os.getpid() + 1}, os.getuid()),
        ({**good, "v": 1}, os.getuid()), ({**good, "op": "commit"}, os.getuid()),
    ]:
        assert handler.prepare(json.dumps(request).encode(), uid)["ok"] is False
    assert handler.prepare(b"not json", os.getuid())["ok"] is False
    ready = handler.prepare(json.dumps(good).encode(), os.getuid())
    assert ready["ok"] is True and ready["stage"] == "ready" and calls == []
    for bad in ({"v": 2, "op": "commit", "nonce": "other"}, {"v": 2, "op": "prepare", "nonce": ready["nonce"]}):
        assert handler.commit(json.dumps(bad).encode(), ready["nonce"])["ok"] is False
    assert calls == []
    assert handler.commit(json.dumps({"v": 2, "op": "commit", "nonce": ready["nonce"]}).encode(),
                          ready["nonce"]) == {"ok": True, "stage": "committed"}
    assert calls == [True]


def test_peer_identity_reports_same_user():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        uid, pid = peer_identity(left)
        assert uid == os.getuid()
        if pid is not None:
            assert pid == os.getpid()
    finally:
        left.close()
        right.close()


def test_owner_without_verifiable_channel_fails_closed(short_runtime):
    owner = LocalFileLock(owner_lock_path(short_runtime))
    assert owner.acquire()
    try:
        with pytest.raises(SettingsLaunchError) as error:
            acquire_ownership(short_runtime, exit_wait_seconds=0.2)
        assert error.value.code in {"OWNER_UNREACHABLE", "OWNER_UNVERIFIED"}
        assert "A new session was not started" in error.value.message
    finally:
        owner.release()


def test_owner_that_does_not_exit_after_commit_fails_closed(short_runtime, monkeypatch):
    owner = LocalFileLock(owner_lock_path(short_runtime))
    assert owner.acquire()
    monkeypatch.setattr(launcher, "request_replacement", lambda _dir, _owner: "committed")
    try:
        started = time.monotonic()
        with pytest.raises(SettingsLaunchError) as error:
            acquire_ownership(short_runtime, exit_wait_seconds=0.3)
        assert error.value.code == "REPLACEMENT_TIMEOUT"
        assert time.monotonic() - started < 2
    finally:
        owner.release()


def test_launch_failure_never_emits_a_url(tmp_path, short_runtime):
    owner = LocalFileLock(owner_lock_path(short_runtime))
    assert owner.acquire()
    urls: list[str] = []
    try:
        result = launcher.run_setup(tmp_path / "missing.yaml", open_browser=False, runtime_dir=short_runtime,
                                    announce=lambda _m: None, emit_url=urls.append)
    finally:
        owner.release()
    assert result["status"] == "failed"
    assert urls == []


class _Launch:
    def __init__(self, config: Path, runtime: Path, *, browser: str | None = None) -> None:
        env = {**os.environ, launcher.RUNTIME_DIR_ENV: str(runtime)}
        args = [sys.executable, "-m", "uls.cli.main", "--config", str(config), "setup"]
        if browser is None:
            args.append("--no-browser")
        else:
            env["BROWSER"] = browser
        self.runtime = runtime
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.lines: queue.Queue[str] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line.strip())

    def _pump_err(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line)

    def url(self, timeout: float = 20.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line.startswith("http://127.0.0.1:"):
                return line
        return None

    def wait_listening(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if control_socket_path(self.runtime).exists():
                return
            time.sleep(0.05)
        raise AssertionError("".join(self.stderr))

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)


def _sign_in(httpx, url: str):
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    client = httpx.Client(base_url=base, timeout=15.0)
    response = client.get(f"?{parts.query}", headers=NAVIGATE, follow_redirects=False)
    assert response.status_code == 303
    csrf = client.get("api/v1/session/csrf", headers=FETCH).json()["csrf_token"]
    return client, csrf, f"{parts.scheme}://{parts.netloc}"


def _runtime() -> Path:
    return Path(tempfile.mkdtemp(prefix="uls-rt-", dir="/tmp"))


@needs_loopback
def test_double_launch_replaces_old_process_before_new_bootstrap(tmp_path):
    httpx = pytest.importorskip("httpx")
    config = _write_config(tmp_path)
    runtime = _runtime()
    first = _Launch(config, runtime)
    second = None
    try:
        url_a = first.url()
        assert url_a, "".join(first.stderr)
        client_a, csrf_a, origin_a = _sign_in(httpx, url_a)
        mutation = {**FETCH, "origin": origin_a, "x-uls-csrf": csrf_a}
        body = {"values": {"system.timezone": "UTC"}, "generation": "0" * 64}
        assert client_a.post("api/v1/settings/general/validate", json=body,
                             headers=mutation).status_code == 409
        threading.Thread(target=first.proc.wait, daemon=True).start()  # reap A promptly

        second = _Launch(config, runtime)
        observed: set[str] = set()
        url_b = None
        deadline = time.monotonic() + 25
        while url_b is None and time.monotonic() < deadline:
            try:
                response = client_a.get("api/v1/overview", headers=FETCH, timeout=0.3)
                observed.add(response.json().get("error", {}).get("code", str(response.status_code)))
            except httpx.TransportError:
                observed.add("unreachable")
            try:
                line = second.lines.get(timeout=0.05)
                if line.startswith("http://127.0.0.1:"):
                    url_b = line
            except queue.Empty:
                pass
        assert url_b, "".join(second.stderr)
        assert "SESSION_REPLACED" in observed
        assert observed <= {"SESSION_REPLACED", "unreachable", "200"}
        assert first.proc.poll() is not None
        with pytest.raises(httpx.TransportError):
            client_a.get("api/v1/overview", headers=FETCH)
        assert urlsplit(url_b).path != urlsplit(url_a).path  # fresh per-launch prefix

        client_b, csrf_b, origin_b = _sign_in(httpx, url_b)
        overview = client_b.get("api/v1/overview", headers=FETCH).json()
        assert overview["session_notice"] == "replaced_previous"
        assert client_b.post("api/v1/session/close", json={},
                             headers={**FETCH, "origin": origin_b, "x-uls-csrf": csrf_b}).status_code == 200
        assert second.proc.wait(timeout=15) == 0
        stderr_b = "".join(second.stderr)
        assert "Existing Settings session is being replaced" in stderr_b
        assert "Previous Settings process exited" in stderr_b
        for stderr in (stderr_b, "".join(first.stderr)):
            assert "bootstrap=" not in stderr and csrf_a not in stderr
    finally:
        for launch in (first, second):
            if launch is not None:
                launch.stop()


@needs_loopback
def test_prepare_without_commit_leaves_the_owner_fully_usable(tmp_path):
    httpx = pytest.importorskip("httpx")
    runtime = _runtime()
    first = _Launch(_write_config(tmp_path), runtime)
    try:
        client, _csrf, _origin = _sign_in(httpx, first.url())
        owner = read_owner_record(runtime)
        assert owner is not None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(str(control_socket_path(runtime)))
        sock.sendall(json.dumps({"v": 2, "op": "prepare", "owner_pid": owner.pid,
                                 "lock_token": owner.token}).encode() + b"\n")
        assert json.loads(sock.recv(1024).split(b"\n")[0])["stage"] == "ready"
        sock.close()  # the caller vanishes before commit
        time.sleep(1.5)
        assert first.proc.poll() is None
        assert client.get("api/v1/overview", headers=FETCH).status_code == 200
    finally:
        first.stop()


@needs_loopback
def test_lost_commit_acknowledgement_is_resolved_by_observing_owner_exit(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    runtime = _runtime()
    first = _Launch(_write_config(tmp_path), runtime)
    lock = None
    try:
        client, _csrf, _origin = _sign_in(httpx, first.url())
        threading.Thread(target=first.proc.wait, daemon=True).start()
        real_read = launcher._read_line
        reads = {"n": 0}

        def lossy_read(sock):
            reads["n"] += 1
            if reads["n"] == 2:
                raise TimeoutError("acknowledgement lost")
            return real_read(sock)

        monkeypatch.setattr(launcher, "_read_line", lossy_read)
        outcomes: list[str] = []
        real_request = launcher.request_replacement
        monkeypatch.setattr(launcher, "request_replacement",
                            lambda d, o: outcomes.append(real_request(d, o)) or outcomes[-1])
        lock, replaced = acquire_ownership(runtime)
        assert outcomes == ["commit_unconfirmed"] and replaced is True
        assert first.proc.poll() is not None
        with pytest.raises(httpx.TransportError):
            client.get("api/v1/overview", headers=FETCH)
    finally:
        if lock is not None:
            lock.release()
        first.stop()


@needs_loopback
def test_replacement_during_an_active_mutation_on_a_held_config_lock(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    config = _write_config(tmp_path)
    runtime = _runtime()
    first = _Launch(config, runtime)
    lock = None
    held = ConfigFileLock(config)
    try:
        client, csrf, origin = _sign_in(httpx, first.url())
        headers = {**FETCH, "origin": origin, "x-uls-csrf": csrf}
        generation = client.get("api/v1/settings/general", headers=FETCH).json()["generation"]
        reviewed = client.post("api/v1/settings/general/validate", headers=headers,
                               json={"values": {"system.timezone": "UTC"}, "generation": generation}).json()
        assert held.acquire()
        result: dict[str, object] = {}

        def apply() -> None:
            response = client.post("api/v1/settings/general/apply", headers=headers, json={
                "values": reviewed["values"], "generation": reviewed["generation"],
                "candidate_hash": reviewed["candidate_hash"]})
            result["status"] = response.status_code
            result["done_at"] = time.monotonic()

        worker = threading.Thread(target=apply)
        worker.start()
        time.sleep(0.7)  # the apply is now blocked on the config lock inside A
        threading.Thread(target=first.proc.wait, daemon=True).start()
        timing: dict[str, float] = {}
        real_request = launcher.request_replacement

        def timed_request(directory, owner):
            started = time.monotonic()
            outcome = real_request(directory, owner)
            timing["round_trip"] = time.monotonic() - started
            held.release()  # let the in-flight mutation finish after the commit
            return outcome

        monkeypatch.setattr(launcher, "request_replacement", timed_request)
        lock, _replaced = acquire_ownership(runtime)
        exited_at = time.monotonic()
        worker.join(timeout=15)
        assert timing["round_trip"] < 1.5  # the control path stayed responsive
        assert result["status"] == 200  # the in-flight mutation finished, not lost
        assert result["done_at"] <= exited_at
        assert yaml.safe_load(config.read_text())["system"]["timezone"] == "UTC"
        from uls.settings.journal import JournalStore
        assert JournalStore(tmp_path / "workspace").unresolved() == []
    finally:
        if held.acquire():
            held.release()
        if lock is not None:
            lock.release()
        first.stop()


@needs_loopback
def test_hanging_browser_open_never_blocks_replacement(tmp_path):
    runtime = _runtime()
    hang = tmp_path / "hang.sh"
    hang.write_text("#!/bin/sh\nsleep 4\n")
    hang.chmod(0o700)
    first = _Launch(_write_config(tmp_path), runtime, browser=str(hang))
    lock = None
    try:
        first.wait_listening()
        time.sleep(0.5)  # webbrowser.open is now blocked inside A
        threading.Thread(target=first.proc.wait, daemon=True).start()
        started = time.monotonic()
        lock, replaced = acquire_ownership(runtime)
        assert replaced is True
        assert first.proc.poll() is not None
        assert time.monotonic() - started < launcher.OWNER_EXIT_WAIT_SECONDS
    finally:
        if lock is not None:
            lock.release()
        first.stop()
