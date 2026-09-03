"""Cross-platform local single-worker lock (implementation spec §40).

The lock is deliberately a local coordination primitive.  It records the
owner's PID and hostname for diagnostics and only recovers a stale lock when a
dead owner is provably on this same host.  A lock left by another host is
never claimed automatically: that would turn a local lock into an unsafe
cross-device lease.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_LOCK_TIMEOUT_SECONDS = 0.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_MALFORMED_LOCK_STALE_AFTER_SECONDS = 24 * 60 * 60


class LocalWorkerLock:
    """An atomic lock-file guard for one Primary PC.

    ``O_CREAT | O_EXCL`` makes acquisition atomic on the local filesystem.
    ``timeout=None`` waits without a deadline; the default is non-blocking.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        lock_path: str | os.PathLike[str] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        malformed_stale_after: float = DEFAULT_MALFORMED_LOCK_STALE_AFTER_SECONDS,
        stale_after: float | None = None,
    ) -> None:
        if path is not None and lock_path is not None:
            raise TypeError("pass either path or lock_path, not both")
        selected_path = lock_path if lock_path is not None else path
        self.path = Path(selected_path if selected_path is not None else ".uls-worker.lock")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if malformed_stale_after < 0:
            raise ValueError("malformed_stale_after must not be negative")
        if stale_after is not None:
            malformed_stale_after = stale_after
        self.poll_interval = poll_interval
        self.malformed_stale_after = malformed_stale_after
        self._token: str | None = None

    @property
    def is_held(self) -> bool:
        return self._token is not None

    def acquire(self, timeout: float | None = DEFAULT_LOCK_TIMEOUT_SECONDS) -> bool:
        """Try to acquire the lock, recovering only safe local stale locks."""

        if self._token is not None:
            return True
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative or None")
        deadline = None if timeout is None else time.monotonic() + timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex

        while True:
            try:
                fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._can_recover_stale_lock():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                time.sleep(self._sleep_duration(deadline))
                continue
            except FileNotFoundError:
                # The parent can be removed by an external cleanup between
                # mkdir and open.  Recreate it and retry within the deadline.
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                continue

            metadata = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "hostname": socket.gethostname(),
                "acquired_at": time.time(),
                "token": token,
            }
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(metadata, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                raise
            self._token = token
            return True

    def release(self) -> None:
        """Release this instance's lock without deleting another owner's lock."""

        token = self._token
        if token is None:
            return
        try:
            metadata = self._read_metadata()
            if metadata.get("token") == token and metadata.get("pid") == os.getpid():
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            self._token = None

    def __enter__(self) -> "LocalWorkerLock":
        if not self.acquire():
            raise TimeoutError(f"local worker lock is already held: {self.path}")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()

    def _sleep_duration(self, deadline: float | None) -> float:
        if deadline is None:
            return self.poll_interval
        return max(0.0, min(self.poll_interval, deadline - time.monotonic()))

    def _read_metadata(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _can_recover_stale_lock(self) -> bool:
        metadata = self._read_metadata()
        host = metadata.get("host", metadata.get("hostname"))
        pid = metadata.get("pid")
        local_host = socket.gethostname()
        if host == local_host and isinstance(pid, int) and not _pid_is_alive(pid):
            return True

        # An interrupted writer can leave a malformed/empty file.  Reclaim it
        # only after a conservative age threshold.  Remote-host files are
        # never reclaimed, even when their contents are malformed.
        if host not in (None, local_host):
            return False
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        return not metadata and age >= self.malformed_stale_after


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "LocalWorkerLock",
]
