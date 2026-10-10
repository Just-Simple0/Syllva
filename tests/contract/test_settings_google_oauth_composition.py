"""Composition and launcher wiring for the personal Google OAuth flow (P2 plan §4)."""
from __future__ import annotations

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
from tests.contract._settings_support import FETCH, HOST, NAVIGATE, write_config
from tests.contract.test_settings_credential_service import service

from uls.settings import composition, google_oauth, launcher
from uls.settings.security import SessionSecurity, new_path_prefix

pytestmark = pytest.mark.contract
REPO = Path(__file__).resolve().parents[2]
CLIENT = {"client_id": "synthetic-client.apps.googleusercontent.com", "client_secret": "synthetic-client-secret"}


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


def test_fake_composition_wires_fake_provider_and_verifier_without_real_transport(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real transport constructed")
    monkeypatch.setattr(google_oauth, "GoogleTokenExchanger", forbidden)
    monkeypatch.setattr(google_oauth, "GoogleAccountReader", forbidden)
    monkeypatch.setattr(google_oauth, "GoogleGrantVerifier", forbidden)
    write_config(tmp_path, google_oauth=dict(CLIENT))
    s = service(tmp_path)
    _token, security = SessionSecurity.issue()
    oauth = composition.build_google_oauth_service(
        s, s.config, authority=HOST, prefix=new_path_prefix(), security=security, opener=None, fake_mode=True)
    assert isinstance(oauth, google_oauth.GoogleOAuthFlowService)
    assert s.oauth_verifier == oauth.fake_provider.verifier
    assert oauth.exchanger == oauth.fake_provider.exchanger and oauth.opener == oauth.fake_provider.opener


def test_real_composition_installs_bounded_transports_and_grant_verifier(tmp_path):
    write_config(tmp_path, google_oauth=dict(CLIENT))
    s = service(tmp_path)
    _token, security = SessionSecurity.issue()
    opened: list[str] = []

    def opener(url: str) -> None:
        opened.append(url)
    prefix = new_path_prefix()
    oauth = composition.build_google_oauth_service(
        s, s.config, authority="127.0.0.1:4321", prefix=prefix, security=security, opener=opener)
    assert isinstance(oauth.exchanger, google_oauth.GoogleTokenExchanger)
    assert isinstance(oauth.account_reader, google_oauth.GoogleAccountReader)
    assert isinstance(s.oauth_verifier, google_oauth.GoogleGrantVerifier)
    assert oauth.redirect_uri == f"http://127.0.0.1:4321/{prefix}/oauth/google/callback"
    assert oauth.opener is opener and not oauth.blocked


def test_real_composition_without_browser_has_no_opener_and_begin_fails_closed(tmp_path, capsys):
    write_config(tmp_path, google_oauth=dict(CLIENT))
    s = service(tmp_path)
    token, security = SessionSecurity.issue()
    oauth = composition.build_google_oauth_service(
        s, s.config, authority="127.0.0.1:4321", prefix=new_path_prefix(), security=security, opener=None)
    assert oauth.opener is None
    assert security.consume_bootstrap(token)
    from uls.settings.config_service import SettingsServiceError
    with pytest.raises(SettingsServiceError) as error:
        oauth.begin("mcp", {"generation": s.config.load().generation, "replace": False})
    assert error.value.code == "BROWSER_REQUIRED"
    captured = capsys.readouterr()
    assert "code_challenge" not in captured.out + captured.err and "state=" not in captured.out + captured.err


class _StubSecurity:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.pending: str | None = None

    def lifecycle_pending(self) -> str | None:
        return self.pending

    def lifecycle_state(self) -> str:
        return "bootstrap_expired"

    def expire(self) -> None:
        self.events.append("expire")

    def replace(self) -> None:
        self.events.append("replace")


class _StubOAuth:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def invalidate_precommit(self) -> None:
        self.events.append("invalidate")


@pytest.mark.parametrize("path", ["replacement", "idle"])
def test_launcher_terminal_order_blocks_oauth_closes_barrier_and_drains_before_session_end(tmp_path, path):
    import asyncio

    async def scenario() -> list[str]:
        server = launcher.SettingsServer(tmp_path / "config.yaml", runtime_dir=tmp_path, lock=None,  # type: ignore[arg-type]
                                         replaced_previous=False, open_browser=False, emit_url=lambda _u: None)
        security = _StubSecurity()
        events: list[str] = []
        server.google_oauth = _StubOAuth(events)
        server.request_shutdown = lambda reason, delay: events.append("shutdown:" + reason)  # type: ignore[method-assign]
        held = asyncio.Event()
        release = asyncio.Event()

        async def committing() -> None:
            async with server.barrier.mutation():
                events.append("commit-entered")
                held.set()
                await release.wait()
                events.append("commit-finished")
        task = asyncio.create_task(committing())
        await held.wait()
        if path == "replacement":
            server._commit_replacement(security)  # type: ignore[arg-type]
        else:
            security.pending = "expired"
            server._check_lifecycle(security)  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        assert events[:2] == ["commit-entered", "invalidate"]
        assert server.barrier._closed and security.events == []
        from uls.settings.security import BarrierClosed
        with pytest.raises(BarrierClosed):
            async with server.barrier.mutation():
                pass
        release.set()
        await task
        await asyncio.sleep(0.05)
        return events + security.events
    events = asyncio.run(scenario())
    finish = "replace" if path == "replacement" else "expire"
    assert events == ["commit-entered", "invalidate", "commit-finished", "shutdown:" + ("replaced" if path == "replacement" else "expired"), finish]


@pytest.mark.parametrize("when", ["draining", "drained"])
def test_replacement_during_idle_termination_is_honoured_not_ignored(tmp_path, monkeypatch, when):
    import asyncio

    monkeypatch.setattr(launcher, "MUTATION_DRAIN_SECONDS", 0.02)

    async def scenario() -> list[str]:
        server = launcher.SettingsServer(tmp_path / "config.yaml", runtime_dir=tmp_path, lock=None,  # type: ignore[arg-type]
                                         replaced_previous=False, open_browser=False, emit_url=lambda _u: None)
        events: list[str] = []
        security = _StubSecurity()
        security.pending = "expired"
        server.google_oauth = _StubOAuth(events)
        server.request_shutdown = lambda reason, delay: events.append(f"shutdown:{reason}:{delay}")  # type: ignore[method-assign]
        held, release = asyncio.Event(), asyncio.Event()

        async def committing() -> None:
            async with server.barrier.mutation():
                held.set()
                await release.wait()
                events.append("commit-finished")
        task = asyncio.create_task(committing())
        await held.wait()
        server._check_lifecycle(security)  # type: ignore[arg-type]
        if when == "drained":
            release.set()
            await task
            await asyncio.sleep(0.1)
            assert security.events == ["expire"] and events[-1] == f"shutdown:expired:{launcher.EXPIRED_GRACE_SECONDS}"
        server._commit_replacement(security)  # type: ignore[arg-type]
        if when == "draining":
            await asyncio.sleep(0.1)
            assert security.events == [] and "commit-finished" not in events
            release.set()
            await task
        await asyncio.sleep(0.1)
        return events + security.events
    events = asyncio.run(scenario())
    assert f"shutdown:replaced:{launcher.REPLACEMENT_DRAIN_SECONDS}" in events
    assert "replace" in events
    if when == "draining":
        assert "expire" not in events and events.index("commit-finished") < events.index("replace")
    else:
        assert events.index("expire") < events.index("replace")


def test_launcher_terminal_order_keeps_waiting_beyond_one_drain_window(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(launcher, "MUTATION_DRAIN_SECONDS", 0.02)

    async def scenario() -> list[str]:
        server = launcher.SettingsServer(tmp_path / "config.yaml", runtime_dir=tmp_path, lock=None,  # type: ignore[arg-type]
                                         replaced_previous=False, open_browser=False, emit_url=lambda _u: None)
        events: list[str] = []
        from tests.contract._settings_support import FakeClock
        clock = FakeClock()
        token, security = SessionSecurity.issue(clock=clock)
        assert security.consume_bootstrap(token)
        server.google_oauth = _StubOAuth(events)
        server.request_shutdown = lambda reason, delay: events.append("shutdown:" + reason)  # type: ignore[method-assign]
        held, release = asyncio.Event(), asyncio.Event()

        async def committing() -> None:
            async with server.barrier.mutation():
                held.set()
                await release.wait()
                events.append("commit-finished")
        task = asyncio.create_task(committing())
        await held.wait()
        clock.now += 15 * 60 + 1  # idle elapsed while the commit is inside the barrier
        assert security.session_code(None) == "SESSION_EXPIRED"  # early request rejected, no transition
        epoch = security.session_epoch
        server._check_lifecycle(security)
        await asyncio.sleep(0.2)  # ten drain windows pass
        assert security.session_epoch == epoch and security.lifecycle_pending() == "expired"
        assert events == ["invalidate"]
        release.set()
        await task
        await asyncio.sleep(0.05)
        assert security.session_epoch == epoch + 1 and security.lifecycle_state() == "expired"
        return events
    assert asyncio.run(scenario()) == ["invalidate", "commit-finished", "shutdown:expired"]


class _Launch:
    def __init__(self, config: Path, runtime: Path, fake_root: Path) -> None:
        env = {**os.environ, launcher.RUNTIME_DIR_ENV: str(runtime), composition.FAKE_STORES_ENV: str(fake_root)}
        args = [sys.executable, "-m", "uls.cli.main", "--config", str(config), "setup", "--no-browser"]
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

    def line(self, prefix: str, timeout: float = 25.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line
        return None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)


@needs_loopback
@pytest.mark.skipif(os.name == "nt", reason="Local Settings POSIX runtime is unavailable on Windows")
def test_launcher_assembles_one_flow_service_and_completes_fake_flow_over_loopback():
    httpx = pytest.importorskip("httpx")
    root = Path(tempfile.mkdtemp(prefix="syllva-oauth-", dir="/tmp")).resolve()
    raw = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = str(root / "workspace")
    raw["behavior_contract"]["path"] = str(REPO / "contracts" / "study-behavior.md")
    raw["google_oauth"] = dict(CLIENT)
    config = root / "config.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    config.chmod(0o600)
    runtime = launcher.runtime_directory(root / "runtime")
    launch = _Launch(config, runtime, root)
    try:
        url = launch.line("http://127.0.0.1:")
        assert url, "".join(launch.stderr)
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        origin = f"{parts.scheme}://{parts.netloc}"
        client = httpx.Client(base_url=base, timeout=15.0)
        assert client.get(f"?{parts.query}", headers=NAVIGATE, follow_redirects=False).status_code == 303
        csrf = client.get("api/v1/session/csrf", headers=FETCH).json()["csrf_token"]
        headers = {**FETCH, "origin": origin, "x-uls-csrf": csrf}
        overview = client.get("api/v1/overview", headers=FETCH).json()
        assert overview["fake_mode"] is True and overview["google_oauth_configured"] is True
        generation = client.get("api/v1/settings/general", headers=FETCH).json()["generation"]
        begun = client.post("api/v1/google-oauth/worker/begin", headers=headers,
                            json={"generation": generation, "replace": False})
        assert begun.status_code == 200, begun.text
        flow_id = begun.json()["flow_id"]
        status = client.get(f"api/v1/google-oauth/worker/{flow_id}", headers=FETCH).json()
        assert status["status"] == "awaiting_commit", status
        commit = client.post("api/v1/google-oauth/worker/commit", headers=headers,
                             json={"flow_id": flow_id, "generation": generation, "replace": False})
        assert commit.status_code == 200, commit.text
        assert commit.json()["status"] == "complete"
        cards = client.get("api/v1/credentials", headers=FETCH).json()["cards"]
        worker = next(card for card in cards if card["role"] == "google-worker")
        assert worker["state"] == "configured" and worker["credential_type"] == "authorized_user"
        assert worker["storage_label"] == "the fake test store"
        # Lifecycle order: close invalidates OAuth before the session ends.
        generation = commit.json()["config_generation"]
        pending = client.post("api/v1/google-oauth/mcp/begin", headers=headers,
                              json={"generation": generation, "replace": False})
        assert pending.status_code == 200, pending.text
        assert client.post("api/v1/session/close", headers=headers, json={}).status_code == 200
        assert client.post("api/v1/google-oauth/mcp/commit", headers=headers, json={
            "flow_id": pending.json()["flow_id"], "generation": generation, "replace": False}).status_code == 401
        launch.proc.wait(timeout=15)
        assert launch.proc.returncode == 0, "".join(launch.stderr)
        stored = sorted(path.name for path in (root / "fake-secrets").iterdir())
        assert "google_worker_service_account.json" in stored
        assert "google_mcp_service_account.json" not in stored
        text = "".join(launch.stderr) + "\n".join(list(launch.lines.queue))
        assert "fake-refresh" not in text and "code_challenge" not in text
    finally:
        launch.stop()
