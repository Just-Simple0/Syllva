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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl


DEFAULT_LOCK_TIMEOUT_SECONDS = 0.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_MALFORMED_LOCK_STALE_AFTER_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class _LockSnapshot:
    metadata: dict[str, Any]
    signature: tuple[int, int, int, int]


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
        self._fd: int | None = None

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
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o600,
                )
            except FileExistsError:
                stale = self._stale_lock_snapshot()
                if stale is not None and self._unlink_if_same_instance(stale):
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
                if not _try_advisory_lock(fd):
                    raise BlockingIOError("could not acquire the new worker lock")
                payload = json.dumps(metadata, sort_keys=True).encode("utf-8")
                os.lseek(fd, 0, os.SEEK_SET)
                _write_all(fd, payload)
                os.fsync(fd)
            except BaseException:
                # The advisory lock is still held here, so a cooperating
                # worker cannot replace this inode between identity checking
                # and cleanup.
                _unlink_path_if_fd_matches(self.path, fd)
                _unlock_advisory_lock(fd)
                os.close(fd)
                raise
            self._fd = fd
            self._token = token
            return True

    def release(self) -> None:
        """Release this instance's lock without deleting another owner's lock."""

        token = self._token
        if token is None:
            return
        fd = self._fd
        try:
            if fd is not None:
                snapshot = _snapshot_from_fd(fd)
                if snapshot is not None:
                    metadata = snapshot.metadata
                    if metadata.get("token") == token and metadata.get("pid") == os.getpid():
                        _unlink_path_if_fd_matches(self.path, fd)
        finally:
            if fd is not None:
                _unlock_advisory_lock(fd)
                os.close(fd)
            self._fd = None
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
        return self._stale_lock_snapshot() is not None

    def _stale_lock_snapshot(self) -> _LockSnapshot | None:
        """Return a verified stale instance, not merely a stale path."""

        snapshot = self._read_lock_snapshot()
        if snapshot is None:
            return None
        metadata = snapshot.metadata
        host = metadata.get("host", metadata.get("hostname"))
        pid = metadata.get("pid")
        local_host = socket.gethostname()
        if host == local_host and isinstance(pid, int) and not _pid_is_alive(pid):
            return snapshot

        # An interrupted writer can leave a malformed/empty file.  Reclaim it
        # only after a conservative age threshold.  Remote-host files are
        # never reclaimed, even when their contents are malformed.
        if host not in (None, local_host):
            return None
        age = time.time() - snapshot.signature[3] / 1_000_000_000
        return snapshot if not metadata and age >= self.malformed_stale_after else None

    def _read_lock_snapshot(self) -> _LockSnapshot | None:
        """Read metadata and inode/mtime twice to detect an intervening writer."""

        try:
            fd = os.open(self.path, os.O_RDWR)
        except (FileNotFoundError, OSError):
            return None
        try:
            return _snapshot_from_fd(fd)
        finally:
            os.close(fd)

    def _unlink_if_same_instance(self, snapshot: _LockSnapshot) -> bool:
        """Remove a snapshot only while holding its inode's advisory lock.

        A second worker cannot acquire the same stale instance while this
        method holds the descriptor lock.  If the path already names another
        inode, the descriptor/path identity check fails and that new owner's
        lock is left untouched.
        """

        try:
            fd = os.open(self.path, os.O_RDWR)
        except (FileNotFoundError, OSError):
            return False
        try:
            if not _try_advisory_lock(fd):
                return False
            current = _snapshot_from_fd(fd)
            if current is None:
                return False
            if current.signature != snapshot.signature or current.metadata != snapshot.metadata:
                return False
            return _unlink_path_if_fd_matches(self.path, fd)
        finally:
            _unlock_advisory_lock(fd)
            os.close(fd)


def _snapshot_from_fd(fd: int) -> _LockSnapshot | None:
    """Read one lock inode without reopening the path by name."""

    try:
        before = os.fstat(fd)
        metadata = _read_metadata_from_fd(fd)
        after = os.fstat(fd)
    except OSError:
        return None
    before_signature = _stat_signature(before)
    after_signature = _stat_signature(after)
    if before_signature != after_signature:
        return None
    return _LockSnapshot(metadata=dict(metadata), signature=after_signature)


def _read_metadata_from_fd(fd: int) -> dict[str, Any]:
    try:
        duplicate = os.dup(fd)
        try:
            os.lseek(duplicate, 0, os.SEEK_SET)
            with os.fdopen(duplicate, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {}
        finally:
            # ``fdopen`` owns and closes the duplicate on the normal path;
            # this is harmlessly a no-op then, and closes it if seek/setup
            # failed before the file object was created.
            try:
                os.close(duplicate)
            except OSError:
                pass
    except OSError:
        return {}
    return value if isinstance(value, dict) else {}


def _unlink_path_if_fd_matches(path: Path, fd: int) -> bool:
    """Unlink ``path`` only when it still names the supplied open inode."""

    try:
        path_stat = path.stat()
        fd_stat = os.fstat(fd)
    except (FileNotFoundError, OSError):
        return False
    if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
        return False
    try:
        os.unlink(path)
    except (FileNotFoundError, OSError):
        return False
    return True


def _try_advisory_lock(fd: int) -> bool:
    """Acquire a non-blocking descriptor lock on macOS/POSIX or Windows."""

    try:
        if os.name == "nt":
            # ``msvcrt.locking`` locks bytes, so ensure byte zero exists even
            # for a freshly-created empty file.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock_advisory_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("could not write worker lock metadata")
        view = view[written:]


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


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
