"""Single-instance loopback launcher for Local Settings.

Process ownership rule: one per-user, owner-only single-instance lock is held
for the whole settings process lifetime. A second launch authenticates the
owner over same-user local IPC with a two-stage protocol:

1. prepare -> ready: authenticated and side-effect free; the owner keeps serving.
2. commit -> committed: the owner invalidates bootstrap/session/CSRF state,
   closes its mutation barrier, answers only SESSION_REPLACED while it drains
   in-flight mutations and the notification window, closes its listener, and
   exits on an independent timer.

The new launcher binds HTTP and mints a bootstrap capability only after it
observes the old process exit and acquires the released lock. Before commit it
fails closed at once (nothing was invalidated). After commit may have been
delivered, it resolves ambiguity only by observing owner exit/lock release.
Windows is refused before any runtime state or listener exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import importlib.util
import json
import os
import secrets
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uls.orchestration.locks import LocalFileLock

from .security import MutationBarrier, SessionSecurity, new_path_prefix

PROTOCOL_VERSION = 2
IPC_IO_TIMEOUT_SECONDS = 1.0
REPLACEMENT_DRAIN_SECONDS = 2.0
# In-flight mutations may wait up to the 5 s config-lock deadline.
MUTATION_DRAIN_SECONDS = 8.0
OWNER_EXIT_WAIT_SECONDS = 12.0
LOCK_RELEASE_POLL_SECONDS = 0.05
CLOSE_DRAIN_SECONDS = 0.5
EXPIRED_GRACE_SECONDS = 60.0
GRACEFUL_SHUTDOWN_SECONDS = 1
MAX_CONTROL_MESSAGE_BYTES = 1024
# macOS sun_path is 104 bytes including the terminator; Linux allows 108.
MAX_UNIX_SOCKET_PATH_BYTES = 100
RUNTIME_DIR_ENV = "ULS_SETTINGS_RUNTIME_DIR"
LOOPBACK_HOST = "127.0.0.1"

_IS_WINDOWS = os.name == "nt"
_DARWIN_SOL_LOCAL = 0
_DARWIN_LOCAL_PEERCRED = 0x001
_DARWIN_LOCAL_PEERPID = 0x002

REPLACEMENT_FAILED_MESSAGE = (
    "The current Settings session did not stop. A new session was not started.\n"
    "Close Settings, then run uls setup again."
)
PLATFORM_UNSUPPORTED_MESSAGE = (
    "Local Settings is not available on Windows yet. Your settings were not changed.\n"
    "Edit config.yaml and use the uls commands instead."
)
WEB_EXTRA_MISSING_MESSAGE = (
    "Local Settings needs its optional web components, which are not installed.\n"
    "Install them with: pip install 'university-learning-system[web]'"
)


class SettingsLaunchError(Exception):
    """A fail-closed launch refusal with a fixed user-facing message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OwnerRecord:
    pid: int
    token: str


def preflight() -> SettingsLaunchError | None:
    """Refuse unsupported platforms and missing dependencies before any side effect."""

    if _IS_WINDOWS:
        return SettingsLaunchError("PLATFORM_UNSUPPORTED", PLATFORM_UNSUPPORTED_MESSAGE)
    for module in ("starlette", "uvicorn"):
        if importlib.util.find_spec(module) is None:
            return SettingsLaunchError("WEB_EXTRA_MISSING", WEB_EXTRA_MISSING_MESSAGE)
    return None


def runtime_directory(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the private per-user Settings runtime directory (mode 0700)."""

    if _IS_WINDOWS:
        raise SettingsLaunchError("PLATFORM_UNSUPPORTED", PLATFORM_UNSUPPORTED_MESSAGE)
    if override is not None:
        base = Path(override)
    elif os.environ.get(RUNTIME_DIR_ENV):
        base = Path(os.environ[RUNTIME_DIR_ENV])
    else:
        base = Path(tempfile.gettempdir()) / f"uls-settings-{os.getuid()}"
    base = base.expanduser().absolute()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = os.lstat(base)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SettingsLaunchError("RUNTIME_DIR_UNSAFE", "The Settings runtime folder is not a private folder.")
    if info.st_uid != os.getuid():
        raise SettingsLaunchError("RUNTIME_DIR_UNSAFE", "The Settings runtime folder is owned by another user.")
    os.chmod(base, 0o700)
    if os.lstat(base).st_mode & 0o077:
        raise SettingsLaunchError("RUNTIME_DIR_UNSAFE", "The Settings runtime folder is not private.")
    if len(os.fsencode(str(control_socket_path(base)))) > MAX_UNIX_SOCKET_PATH_BYTES:
        raise SettingsLaunchError(
            "RUNTIME_DIR_TOO_LONG", "The Settings runtime folder path is too long for a local socket.",
        )
    return base


def owner_lock_path(runtime_dir: Path) -> Path:
    return runtime_dir / "settings-owner.lock"


def control_socket_path(runtime_dir: Path) -> Path:
    return runtime_dir / "settings-control.sock"


def peer_identity(sock: socket.socket) -> tuple[int | None, int | None]:
    """Return (uid, pid) of the connected AF_UNIX peer, or None when unprovable."""

    if sys.platform == "darwin":
        try:
            raw = sock.getsockopt(_DARWIN_SOL_LOCAL, _DARWIN_LOCAL_PEERCRED, 256)
            _version, uid = struct.unpack_from("=II", raw)
        except (OSError, struct.error):
            return None, None
        try:
            pid = struct.unpack("=i", sock.getsockopt(_DARWIN_SOL_LOCAL, _DARWIN_LOCAL_PEERPID, 4))[0]
        except (OSError, struct.error):
            pid = None
        return uid, pid
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is not None:
        try:
            pid, uid, _gid = struct.unpack("=3i", sock.getsockopt(socket.SOL_SOCKET, peercred, 12))
        except (OSError, struct.error):
            return None, None
        return uid, pid
    return None, None


def read_owner_record(runtime_dir: Path) -> OwnerRecord | None:
    """Read the held lock's PID and random token from the private lock file."""

    path = owner_lock_path(runtime_dir)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return None
        payload = os.read(fd, 4096)
    finally:
        os.close(fd)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid, token = data.get("pid"), data.get("token")
    if type(pid) is not int or not isinstance(token, str) or not token:
        return None
    return OwnerRecord(pid=pid, token=token)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _send(sock: socket.socket, message: dict[str, Any]) -> None:
    sock.sendall(json.dumps(message).encode("utf-8") + b"\n")


def _read_line(sock: socket.socket) -> bytes:
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(256)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > MAX_CONTROL_MESSAGE_BYTES:
            raise OSError("control reply too large")
    return buffer.split(b"\n", 1)[0]


def _parse(reply: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(reply.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def request_replacement(runtime_dir: Path, owner: OwnerRecord) -> str:
    """Run prepare/commit with the verified owner.

    Raises SettingsLaunchError when the handover fails before commit could have
    been delivered (the owner stays fully usable). Returns "committed" when the
    owner acknowledged the commit, or "commit_unconfirmed" when the commit may
    have been delivered but no acknowledgement arrived; the caller must then
    decide only by observing owner exit and lock release.
    """

    if not hasattr(socket, "AF_UNIX"):
        raise SettingsLaunchError("REPLACEMENT_UNSUPPORTED", REPLACEMENT_FAILED_MESSAGE)
    path = control_socket_path(runtime_dir)
    try:
        info = os.lstat(path)
    except OSError:
        raise SettingsLaunchError("OWNER_UNREACHABLE", REPLACEMENT_FAILED_MESSAGE) from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise SettingsLaunchError("OWNER_UNVERIFIED", REPLACEMENT_FAILED_MESSAGE)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(IPC_IO_TIMEOUT_SECONDS)
    try:
        try:
            client.connect(str(path))
        except OSError:
            raise SettingsLaunchError("OWNER_UNREACHABLE", REPLACEMENT_FAILED_MESSAGE) from None
        uid, pid = peer_identity(client)
        if uid != os.getuid() or (pid is not None and pid != owner.pid) or (
            pid is None and sys.platform == "darwin"
        ):
            raise SettingsLaunchError("OWNER_UNVERIFIED", REPLACEMENT_FAILED_MESSAGE)
        try:
            _send(client, {"v": PROTOCOL_VERSION, "op": "prepare", "owner_pid": owner.pid,
                           "lock_token": owner.token})
            ready = _parse(_read_line(client))
        except OSError:
            raise SettingsLaunchError("OWNER_UNREACHABLE", REPLACEMENT_FAILED_MESSAGE) from None
        nonce = ready.get("nonce") if ready else None
        if not ready or ready.get("ok") is not True or ready.get("stage") != "ready" or not isinstance(nonce, str):
            raise SettingsLaunchError("OWNER_REFUSED", REPLACEMENT_FAILED_MESSAGE)
        try:
            _send(client, {"v": PROTOCOL_VERSION, "op": "commit", "nonce": nonce})
            answer = _parse(_read_line(client))
        except OSError:
            return "commit_unconfirmed"
    finally:
        client.close()
    if answer and answer.get("ok") is True and answer.get("stage") == "committed":
        return "committed"
    if answer and answer.get("ok") is False:
        raise SettingsLaunchError("OWNER_REFUSED", REPLACEMENT_FAILED_MESSAGE)
    return "commit_unconfirmed"


def acquire_ownership(
    runtime_dir: Path,
    *,
    announce: Callable[[str], None] = lambda _message: None,
    exit_wait_seconds: float = OWNER_EXIT_WAIT_SECONDS,
) -> tuple[LocalFileLock, bool]:
    """Acquire the single-instance lock, replacing a verified live owner.

    Returns (held lock, replaced_previous). Raises SettingsLaunchError without
    minting anything when the owner cannot be verified or does not exit.
    """

    lock = LocalFileLock(owner_lock_path(runtime_dir))
    if lock.acquire(timeout=0.0):
        return lock, False
    owner = read_owner_record(runtime_dir)
    if owner is None:
        raise SettingsLaunchError("OWNER_UNVERIFIED", REPLACEMENT_FAILED_MESSAGE)
    announce("Existing Settings session is being replaced…")
    request_replacement(runtime_dir, owner)
    deadline = time.monotonic() + exit_wait_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(owner.pid) and lock.acquire(timeout=0.0):
            announce("Previous Settings process exited. Opening a fresh session…")
            return lock, True
        time.sleep(LOCK_RELEASE_POLL_SECONDS)
    raise SettingsLaunchError("REPLACEMENT_TIMEOUT", REPLACEMENT_FAILED_MESSAGE)


def reserve_loopback_socket() -> socket.socket:
    """Bind exactly 127.0.0.1 on an OS-assigned port before any token exists."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK_HOST, 0))
        sock.listen(64)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


def bootstrap_url(port: int, prefix: str, token: str) -> str:
    return f"http://{LOOPBACK_HOST}:{port}/{prefix}/?bootstrap={token}"


def uvicorn_config(app: Any) -> Any:
    import uvicorn

    return uvicorn.Config(
        app,
        lifespan="off",
        access_log=False,
        log_config=None,
        log_level="warning",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        reload=False,
        workers=None,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


class ControlHandler:
    """Validates the two-stage replacement exchange for the current owner."""

    def __init__(self, lock_token: str, on_commit: Callable[[], None]) -> None:
        self._lock_token = lock_token
        self._on_commit = on_commit

    def prepare(self, payload: bytes, peer_uid: int | None) -> dict[str, Any]:
        """Authenticate a prepare request. Never changes any state."""

        if peer_uid is None or peer_uid != _current_uid():
            return {"ok": False, "code": "PEER_REJECTED"}
        request = _parse(payload)
        if not request or request.get("v") != PROTOCOL_VERSION or request.get("op") != "prepare":
            return {"ok": False, "code": "INVALID_REQUEST"}
        token = request.get("lock_token")
        if request.get("owner_pid") != os.getpid() or not isinstance(token, str) or not hmac.compare_digest(
            token.encode("utf-8"), self._lock_token.encode("utf-8"),
        ):
            return {"ok": False, "code": "OWNER_MISMATCH"}
        return {"ok": True, "stage": "ready", "nonce": secrets.token_hex(16)}

    def commit(self, payload: bytes, nonce: str) -> dict[str, Any]:
        """Commit only the prepared exchange on the same connection."""

        request = _parse(payload)
        submitted = request.get("nonce") if request else None
        if (
            not request or request.get("v") != PROTOCOL_VERSION or request.get("op") != "commit"
            or not isinstance(submitted, str)
            or not hmac.compare_digest(submitted.encode("utf-8"), nonce.encode("utf-8"))
        ):
            return {"ok": False, "code": "INVALID_REQUEST"}
        self._on_commit()
        return {"ok": True, "stage": "committed"}


def _current_uid() -> int | None:
    return os.getuid() if hasattr(os, "getuid") else None


def _open_browser(url: str) -> None:
    with contextlib.suppress(Exception):
        webbrowser.open(url)


class SettingsServer:
    """One loopback Uvicorn server plus its owner-only control channel."""

    def __init__(
        self,
        config_path: Path,
        *,
        runtime_dir: Path,
        lock: LocalFileLock,
        replaced_previous: bool,
        open_browser: bool,
        emit_url: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config_path = config_path
        self.runtime_dir = runtime_dir
        self.lock = lock
        self.replaced_previous = replaced_previous
        self.open_browser = open_browser
        self.emit_url = emit_url
        self.clock = clock
        self.shutdown_reason: str | None = None
        self.barrier = MutationBarrier()
        self._server: Any = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_at: float | None = None

    def request_shutdown(self, reason: str, delay: float) -> None:
        """Schedule shutdown on an independent event-loop task (earliest wins)."""

        at = self.clock() + delay
        if self._shutdown_at is not None and at >= self._shutdown_at:
            return
        if self.shutdown_reason is None or reason == "replaced":
            self.shutdown_reason = reason
        self._shutdown_at = at
        if self._shutdown_task is not None:
            self._shutdown_task.cancel()
        self._shutdown_task = asyncio.get_running_loop().create_task(self._shutdown_after(delay))

    async def _shutdown_after(self, delay: float) -> None:
        # Stop only after the notification window and every in-flight mutation.
        await asyncio.gather(asyncio.sleep(delay), self.barrier.wait_idle(MUTATION_DRAIN_SECONDS))
        if self._server is not None:
            self._server.should_exit = True

    async def serve(self) -> str:
        from .app import create_settings_app
        from .config_service import ConfigStore
        from .journal import JournalStore

        store = ConfigStore(self.config_path)
        loaded = store.load()
        journal = JournalStore(loaded.config.system.workspace_dir)
        sock = reserve_loopback_socket()
        port = sock.getsockname()[1]
        prefix = new_path_prefix()
        token, security = SessionSecurity.issue()
        self.security = security
        app = create_settings_app(
            store, journal, security, f"{LOOPBACK_HOST}:{port}",
            prefix=prefix, barrier=self.barrier,
            on_close=lambda: self.request_shutdown("closed", CLOSE_DRAIN_SECONDS),
            replaced_previous=self.replaced_previous,
        )
        import uvicorn

        self._server = uvicorn.Server(uvicorn_config(app))
        control = await self._start_control(security)
        serve_task = asyncio.create_task(self._server.serve(sockets=[sock]))
        try:
            while not self._server.started:
                if serve_task.done():
                    await serve_task
                    return "failed"
                await asyncio.sleep(0.02)
            url = bootstrap_url(port, prefix, token)
            if self.open_browser:
                # A daemon thread: a hanging browser command can never delay
                # replacement, shutdown, or process exit.
                threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
            else:
                self.emit_url(url)
            del token, url
            while not serve_task.done():
                self._check_lifecycle(security)
                await asyncio.sleep(0.1)
            await serve_task
        finally:
            if control is not None:
                control.close()
                with contextlib.suppress(Exception):
                    await control.wait_closed()
            _remove_socket(control_socket_path(self.runtime_dir))
            sock.close()
        return self.shutdown_reason or "stopped"

    def _check_lifecycle(self, security: SessionSecurity) -> None:
        state = security.lifecycle_state()
        if state == "bootstrap_expired":
            self.request_shutdown("bootstrap_expired", 0.0)
        elif state == "expired":
            self.request_shutdown("expired", EXPIRED_GRACE_SECONDS)

    def _commit_replacement(self, security: SessionSecurity) -> None:
        security.replace()
        self.barrier.close()
        self.request_shutdown("replaced", REPLACEMENT_DRAIN_SECONDS)

    async def _start_control(self, security: SessionSecurity) -> asyncio.AbstractServer | None:
        path = control_socket_path(self.runtime_dir)
        _remove_socket(path)
        token = self.lock.token
        if token is None:
            raise SettingsLaunchError("OWNER_LOCK_LOST", REPLACEMENT_FAILED_MESSAGE)
        handler = ControlHandler(token, lambda: self._commit_replacement(security))

        async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                raw_sock = writer.get_extra_info("socket")
                uid, _pid = peer_identity(raw_sock) if raw_sock is not None else (None, None)
                first = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=IPC_IO_TIMEOUT_SECONDS)
                ready = handler.prepare(first.rstrip(b"\n"), uid)
                writer.write(json.dumps(ready).encode("utf-8") + b"\n")
                await asyncio.wait_for(writer.drain(), timeout=IPC_IO_TIMEOUT_SECONDS)
                if ready.get("ok") is not True:
                    return
                second = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=IPC_IO_TIMEOUT_SECONDS)
                # The commit takes effect before the acknowledgement is written;
                # a lost acknowledgement is resolved by the caller observing exit.
                answer = handler.commit(second.rstrip(b"\n"), ready["nonce"])
                writer.write(json.dumps(answer).encode("utf-8") + b"\n")
                await asyncio.wait_for(writer.drain(), timeout=IPC_IO_TIMEOUT_SECONDS)
            except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
                pass
            finally:
                writer.close()

        old_umask = os.umask(0o077)
        try:
            server = await asyncio.start_unix_server(on_client, path=str(path), limit=MAX_CONTROL_MESSAGE_BYTES)
        finally:
            os.umask(old_umask)
        os.chmod(path, 0o600)
        return server


def _remove_socket(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        return
    if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
        with contextlib.suppress(OSError):
            os.unlink(path)


def run_setup(
    config_path: Path,
    *,
    open_browser: bool = True,
    runtime_dir: str | os.PathLike[str] | None = None,
    announce: Callable[[str], None] | None = None,
    emit_url: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one Settings process until it is closed, replaced, or expires."""

    say = announce or (lambda message: print(message, file=sys.stderr, flush=True))
    show = emit_url or (lambda url: print(url, flush=True))
    refusal = preflight()
    if refusal is not None:
        say(refusal.message)
        return {"status": "failed", "code": refusal.code}
    try:
        directory = runtime_directory(runtime_dir)
        lock, replaced_previous = acquire_ownership(directory, announce=say)
    except SettingsLaunchError as exc:
        say(exc.message)
        return {"status": "failed", "code": exc.code}
    try:
        server = SettingsServer(
            Path(config_path).expanduser().absolute(),
            runtime_dir=directory,
            lock=lock,
            replaced_previous=replaced_previous,
            open_browser=open_browser,
            emit_url=show,
        )
        try:
            reason = asyncio.run(server.serve())
        except KeyboardInterrupt:
            reason = "interrupted"
    finally:
        lock.release()
    return {"status": "stopped", "reason": reason}


__all__ = [
    "CLOSE_DRAIN_SECONDS", "IPC_IO_TIMEOUT_SECONDS", "LOCK_RELEASE_POLL_SECONDS",
    "MUTATION_DRAIN_SECONDS", "OWNER_EXIT_WAIT_SECONDS", "REPLACEMENT_DRAIN_SECONDS",
    "ControlHandler", "OwnerRecord", "SettingsLaunchError", "SettingsServer", "acquire_ownership",
    "bootstrap_url", "peer_identity", "preflight", "read_owner_record", "request_replacement",
    "reserve_loopback_socket", "run_setup", "runtime_directory", "uvicorn_config",
]
