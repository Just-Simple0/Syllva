"""Cross-platform ownership/lock primitive coverage for _lms_platform.py.

Real Windows APIs cannot run on this test host, so the Windows branches are
exercised by monkeypatching the narrow ctypes-calling seams
(_windows_owner_sid / _windows_current_user_sid) and by injecting a fake
msvcrt module into sys.modules, while IS_WINDOWS is monkeypatched true. The
POSIX branches are exercised for real, unchanged from before this module
existed. The real Windows ctypes calls are verified by the project\'s
windows-latest CI matrix, not by this in-process test.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "_lms_platform.py"
SPEC = importlib.util.spec_from_file_location("lms_platform_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
fsplat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fsplat
SPEC.loader.exec_module(fsplat)


def test_owns_path_posix_matches_current_uid(tmp_path: Path) -> None:
    target = tmp_path / "owned"
    target.write_text("x", encoding="utf-8")
    assert fsplat.owns_path(target) is True


def test_owns_path_posix_rejects_stat_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert fsplat.owns_path(missing) is False


def test_owns_path_windows_matches_current_sid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)
    monkeypatch.setattr(fsplat, "_windows_owner_sid", lambda path: "S-1-5-21-SAME")
    monkeypatch.setattr(fsplat, "_windows_current_user_sid", lambda: "S-1-5-21-SAME")
    assert fsplat.owns_path(tmp_path / "any") is True


def test_owns_path_windows_rejects_mismatched_sid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)
    monkeypatch.setattr(fsplat, "_windows_owner_sid", lambda path: "S-1-5-21-OWNER")
    monkeypatch.setattr(fsplat, "_windows_current_user_sid", lambda: "S-1-5-21-OTHER")
    assert fsplat.owns_path(tmp_path / "any") is False


def test_owns_path_windows_fails_closed_on_readback_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise OSError("simulated ctypes failure")

    monkeypatch.setattr(fsplat, "_windows_owner_sid", _raise)
    monkeypatch.setattr(fsplat, "_windows_current_user_sid", lambda: "S-1-5-21-OTHER")
    assert fsplat.owns_path(tmp_path / "any") is False


def test_fchmod_and_chmod_are_noop_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("POSIX chmod/fchmod must not run on simulated Windows")

    monkeypatch.setattr(os, "chmod", _fail)
    monkeypatch.setattr(os, "fchmod", _fail)
    fsplat.chmod_if_supported(tmp_path, 0o700)
    fsplat.fchmod_if_supported(0, 0o600)


def test_fchmod_and_chmod_run_on_posix(tmp_path: Path) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    fsplat.chmod_if_supported(target, 0o700)
    assert (target.stat().st_mode & 0o777) == 0o700


def test_sync_directory_is_noop_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def _fail(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("directory open/fsync must not run on simulated Windows")

    monkeypatch.setattr(os, "open", _fail)
    fsplat.sync_directory(tmp_path)


def test_sync_directory_runs_on_posix(tmp_path: Path) -> None:
    fsplat.sync_directory(tmp_path)


def test_open_nofollow_rejects_symlink_on_posix(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(OSError):
        fsplat.open_nofollow(link, os.O_RDONLY)


def test_try_lock_and_unlock_use_msvcrt_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)
    calls: list[tuple[str, int]] = []

    def locking(fd: int, mode: int, _nbytes: int) -> None:
        calls.append(("lock" if mode == 1 else "unlock", fd))

    fake_msvcrt = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    lock_path = tmp_path / "lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert fsplat.try_lock_exclusive(fd) is True
        fsplat.unlock(fd)
    finally:
        os.close(fd)
    assert calls == [("lock", fd), ("unlock", fd)]


def test_try_lock_reports_failure_when_msvcrt_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def locking(_fd: int, _mode: int, _nbytes: int) -> None:
        raise OSError("simulated lock contention")

    fake_msvcrt = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    lock_path = tmp_path / "lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert fsplat.try_lock_exclusive(fd) is False
    finally:
        os.close(fd)


def test_try_lock_and_unlock_use_fcntl_on_posix(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert fsplat.try_lock_exclusive(fd) is True
        fsplat.unlock(fd)
    finally:
        os.close(fd)

