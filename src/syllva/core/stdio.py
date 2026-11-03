"""``syllva core --stdio``: the Core side of the Shell protocol (implementation spec §2).

Sequence (§2.2): nothing listens until a valid ``hello``; then lock + migrate the
library, bind the UI server on ``127.0.0.1:0``, mint the first bootstrap token
(window ``main``) and answer ``ready``. Afterwards the loop serves ``ping``,
``session.bootstrap`` and ``shutdown``; stdin EOF or 30 s without ``ping`` ends
the process (§2.4). Exit codes follow §2.5.

Channel integrity (R2 plan rev 2, item 8): at start the real stdout fd is
duplicated for the protocol writer and fd 1 is pointed at stderr, so no library
``print`` or warning can corrupt the JSON Lines stream. Protocol lines are never
logged — only their type and size (§13.1). ``shell_secret`` stays in a
``Secret`` in this process' memory (§2.2, C-20).
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from syllva import __version__
from syllva.core.logging import configure, log_event
from syllva.core.secret import Secret
from syllva.library.dblock import DatabaseLocked
from syllva.library.paths import layout
from syllva.state.v2.database import Database, open_database

PROTOCOL_VERSION = 1
MAX_LINE = 64 * 1024
PING_TIMEOUT_S = 30.0
PLATFORMS = ("darwin", "win32", "linux")
LOOPBACK_HOST = "127.0.0.1"
WINDOW_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

EXIT_OK, EXIT_PROTOCOL, EXIT_DB_LOCKED, EXIT_MIGRATION, EXIT_SHELL_GONE = 0, 2, 3, 4, 5

# Origins the relayed webview presents (design rev 5, errata E25; finalized by the R2-0 spike).
APP_ORIGINS: dict[str, tuple[str, ...]] = {
    "darwin": ("syllva://app",),
    "linux": ("syllva://app",),
    "win32": ("http://syllva.app", "https://syllva.app"),
}


class ProtocolViolation(Exception):
    pass


def bind_loopback(host: str = LOOPBACK_HOST) -> socket.socket:
    """C-13: the UI server binds exactly 127.0.0.1 on an OS-assigned port."""

    if host != LOOPBACK_HOST:
        raise ValueError("the UI server binds 127.0.0.1 only")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK_HOST, 0))
        sock.listen(64)
    except BaseException:
        sock.close()
        raise
    return sock


def _b64decode(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Hello:
    secret: Secret
    shell_version: str
    platform: str


def parse_hello(message: dict[str, Any]) -> Hello:
    if message.get("t") != "hello" or message.get("v") != PROTOCOL_VERSION:
        raise ProtocolViolation("expected hello v1")
    try:
        secret = Secret(_b64decode(message.get("secret_b64", "")))
    except ValueError as exc:
        raise ProtocolViolation("bad secret") from exc
    version, platform = message.get("shell_version"), message.get("platform")
    if not isinstance(version, str) or not 0 < len(version) <= 64 or platform not in PLATFORMS:
        raise ProtocolViolation("bad hello fields")
    return Hello(secret, version, str(platform))


class Channel:
    """JSON Lines over the inherited stdin and a private duplicate of the original stdout."""

    def __init__(self, stdin: BinaryIO, out_fd: int) -> None:
        self._stdin = stdin
        self._out = out_fd
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        data = json.dumps({"v": PROTOCOL_VERSION, **message}, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        with self._lock:
            os.write(self._out, data)
        log_event("PROTO_OUT", t=message.get("t"), size=len(data))

    def read(self) -> dict[str, Any] | None:
        line = self._stdin.readline(MAX_LINE + 1)
        if not line:
            return None
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            raise ProtocolViolation("line too long or unterminated")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolViolation("not json") from exc
        if not isinstance(message, dict) or not isinstance(message.get("t"), str):
            raise ProtocolViolation("not a message")
        log_event("PROTO_IN", t=message["t"], size=len(line))
        return message


@dataclass
class CoreState:
    hello: Hello
    db: Database
    port: int
    stop: threading.Event = field(default_factory=threading.Event)
    handlers: dict[str, Callable[[dict[str, Any]], None]] = field(default_factory=dict)


def _isolate_stdout() -> int:
    """Return a private fd for the protocol and point fd 1 (and sys.stdout) at stderr."""

    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return protocol_fd


class _UiServer:
    def __init__(self, app: Any, sock: socket.socket) -> None:
        import uvicorn

        self._server = uvicorn.Server(uvicorn.Config(
            app, lifespan="off", access_log=False, log_config=None, log_level="critical",
            proxy_headers=False, server_header=False, date_header=False,
        ))
        self._sock = sock
        self._thread = threading.Thread(target=self._run, name="ui-server", daemon=True)

    def _run(self) -> None:
        self._server.run(sockets=[self._sock])

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and time.monotonic() < deadline and self._thread.is_alive():
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
        self._sock.close()


def run_stdio(stdin: BinaryIO | None = None) -> int:
    out_fd = _isolate_stdout()
    channel = Channel(stdin or sys.stdin.buffer, out_fd)
    try:
        first = channel.read()
        if first is None:
            return EXIT_SHELL_GONE
        hello = parse_hello(first)
    except ProtocolViolation:
        return EXIT_PROTOCOL

    data = layout()
    configure(data.root / "logs")
    log_event("CORE_START", core_version=__version__, platform=hello.platform)
    try:
        db = open_database(data)
    except DatabaseLocked:
        channel.send({"t": "error", "code": "DB_LOCKED", "message": "another Core holds the library"})
        log_event("CORE_DB_LOCKED")
        return EXIT_DB_LOCKED
    except Exception as exc:  # noqa: BLE001 - reported by code, never with content
        channel.send({"t": "error", "code": "MIGRATION_FAILED", "message": type(exc).__name__})
        log_event("CORE_MIGRATION_FAILED", error=type(exc).__name__)
        return EXIT_MIGRATION

    from syllva.ui.http import create_app
    from syllva.ui.sessions import SessionStore

    sessions = SessionStore()
    sock = bind_loopback()
    port = int(sock.getsockname()[1])
    app = create_app(sessions, port=port, allowed_origins=APP_ORIGINS[hello.platform])
    server = _UiServer(app, sock)
    server.start()
    channel.send({"t": "ready", "port": port, "bootstrap": sessions.issue_bootstrap("main"), "core_version": __version__})
    log_event("CORE_READY", port=port)
    try:
        return _loop(channel, sessions)
    finally:
        server.stop()
        db.close()
        log_event("CORE_STOP")


def main_stdio() -> int:
    """Process entry: run the protocol, then leave without interpreter finalization.

    The stdin reader thread may still block in ``readline``; normal finalization would
    abort on its buffer lock. Everything (UI server, database, log file) is closed by
    ``run_stdio`` before this point, so ``os._exit`` loses nothing.
    """

    code = run_stdio()
    sys.stderr.flush()
    os._exit(code)


def run_headless() -> int:
    """Development/CI mode (§6.7): no Shell, so no shell_secret and no Human-Proof."""

    data = layout()
    configure(data.root / "logs")
    try:
        db = open_database(data)
    except DatabaseLocked:
        sys.stderr.write("the library is in use by another Core\n")
        return EXIT_DB_LOCKED
    from syllva.ui.http import create_app
    from syllva.ui.sessions import SessionStore

    sessions = SessionStore()
    sock = bind_loopback()
    port = int(sock.getsockname()[1])
    server = _UiServer(create_app(sessions, port=port, allowed_origins=(f"http://127.0.0.1:{port}",)), sock)
    server.start()
    # The token goes to the developer's terminal only (stderr); it is never logged.
    sys.stderr.write(f"headless UI: http://127.0.0.1:{port}/bootstrap#{sessions.issue_bootstrap('main')}\n")
    log_event("CORE_HEADLESS", port=port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        server.stop()
        db.close()


def _loop(channel: Channel, sessions: Any) -> int:
    inbox: queue.Queue[dict[str, Any] | None | ProtocolViolation] = queue.Queue()

    def reader() -> None:
        while True:
            try:
                message = channel.read()
            except ProtocolViolation as exc:
                inbox.put(exc)
                return
            inbox.put(message)
            if message is None:
                return

    threading.Thread(target=reader, name="stdin", daemon=True).start()
    last_ping = time.monotonic()
    while True:
        try:
            item = inbox.get(timeout=1.0)
        except queue.Empty:
            if time.monotonic() - last_ping > PING_TIMEOUT_S:
                log_event("CORE_PING_TIMEOUT")
                return EXIT_SHELL_GONE
            continue
        if item is None:
            return EXIT_SHELL_GONE
        if isinstance(item, ProtocolViolation):
            log_event("CORE_PROTOCOL_VIOLATION")
            return EXIT_PROTOCOL
        kind = item["t"]
        if item.get("v") != PROTOCOL_VERSION or kind == "hello":
            log_event("CORE_PROTOCOL_VIOLATION", t=kind)
            return EXIT_PROTOCOL
        if kind == "ping":
            last_ping = time.monotonic()
            seq = item.get("seq")
            channel.send({"t": "pong", "seq": seq if isinstance(seq, int) else None})
        elif kind == "shutdown":
            channel.send({"t": "bye"})
            return EXIT_OK
        elif kind == "session.bootstrap":
            label = item.get("window_label")
            if not isinstance(label, str) or not WINDOW_LABEL.fullmatch(label):
                channel.send({"t": "error", "code": "BAD_WINDOW_LABEL", "message": "invalid window label"})
                continue
            channel.send({"t": "session.bootstrap_ok", "window_label": label, "bootstrap": sessions.issue_bootstrap(label)})
        else:
            channel.send({"t": "error", "code": "UNKNOWN_TYPE", "message": "unknown message type"})


__all__ = ["APP_ORIGINS", "bind_loopback", "main_stdio", "parse_hello", "run_headless", "run_stdio"]
