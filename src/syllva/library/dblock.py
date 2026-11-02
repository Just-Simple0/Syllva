"""Exclusive database lock (implementation spec §2.4).

Only the process holding this lock may open ``state.sqlite3``. The lock is an
OS-level advisory lock on ``db.lock`` (``fcntl.flock`` on POSIX,
``msvcrt.locking`` on Windows): the kernel releases it when the holder exits or
crashes, so there is no stale-lock recovery to get wrong. A second Core that
cannot take the lock must exit with ``DB_LOCKED`` (exit code 3).

The descriptor is opened non-inheritable, so agent child processes never get
a handle to it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Self


class DatabaseLocked(RuntimeError):
    """Another process holds the database lock."""

    code = "DB_LOCKED"
    exit_code = 3


class DatabaseLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> Self:
        if self._fd is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(fd, False)
        try:
            _lock(fd)
        except OSError as exc:
            os.close(fd)
            raise DatabaseLocked(f"database lock is held by another process: {self.path}") from exc
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            pass  # diagnostics only; the lock itself is what matters
        self._fd = fd
        return self

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
