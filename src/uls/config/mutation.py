"""Shared cross-process config-file locking and crash-safe replacement."""

from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path
from typing import Self

from uls.orchestration.locks import LocalFileLock

CONFIG_LOCK_WAIT_SECONDS = 5.0
MAX_CONFIG_BYTES = 2 * 1024 * 1024


class ConfigLockTimeout(TimeoutError):
    """The shared config lock was not acquired before its bounded deadline."""


class ConfigFileLock:
    """The one cross-process lock used by config writers for an exact path."""

    def __init__(self, config_path: str | os.PathLike[str], *, timeout: float | None = CONFIG_LOCK_WAIT_SECONDS) -> None:
        path = Path(config_path).expanduser().absolute()
        # Match the journal binding and share one lock through directory aliases
        # (notably /tmp -> /private/tmp on macOS). Keep the final entry unresolved
        # so the config reader still rejects a symlink target with O_NOFOLLOW.
        path = Path(os.path.realpath(path.parent)) / path.name
        lock_path = path.with_name(f".{path.name}.uls-config.lock")
        self.config_path = path
        self._lock = LocalFileLock(lock_path)
        self.timeout = timeout

    @property
    def is_held(self) -> bool:
        return self._lock.is_held

    def acquire(self) -> bool:
        return self._lock.acquire(timeout=self.timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> Self:
        if not self.acquire():
            raise ConfigLockTimeout("configuration is being changed by another process")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()


def read_config_bytes(path: str | os.PathLike[str]) -> bytes:
    """Read one owner-controlled regular file without following symlinks."""

    target = Path(path).expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("configuration target must be a regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("configuration target is not owned by the current user")
        if info.st_size > MAX_CONFIG_BYTES:
            raise ValueError("configuration file exceeds the supported size")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise ValueError("configuration file exceeds the supported size")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def verify_config_target(path: str | os.PathLike[str]) -> None:
    """Verify the current config entry is a user-owned regular file."""

    read_config_bytes(path)


def atomic_replace_config(path: str | os.PathLike[str], payload: bytes) -> None:
    """Durably replace a config file through a private sibling temporary."""

    target = Path(path).expanduser().absolute()
    if not payload or len(payload) > MAX_CONFIG_BYTES:
        raise ValueError("configuration replacement has an unsupported size")
    verify_config_target(target)
    parent = target.parent
    temp = parent / f".{target.name}.uls-tmp-{os.getpid()}-{secrets.token_hex(8)}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("configuration write returned no data")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.replace(temp, target)
        if os.name != "nt":
            dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def wait_for_config_lock_release(path: str | os.PathLike[str], *, deadline: float) -> bool:
    """Check lock availability until a monotonic deadline, without blocking."""

    lock = ConfigFileLock(path, timeout=0.0)
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            return True
        time.sleep(0.05)
    return False


__all__ = [
    "CONFIG_LOCK_WAIT_SECONDS",
    "MAX_CONFIG_BYTES",
    "ConfigFileLock",
    "ConfigLockTimeout",
    "atomic_replace_config",
    "read_config_bytes",
    "verify_config_target",
    "wait_for_config_lock_release",
]
