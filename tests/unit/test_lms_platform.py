"""Cross-platform ownership/lock primitive coverage for _lms_platform.py.

Real Windows APIs cannot run on this test host, so the Windows branches are
exercised by monkeypatching the narrow ctypes-calling seams
(_windows_owner_sid / _windows_default_owner_sid) and by injecting a fake
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
    monkeypatch.setattr(fsplat, "_windows_default_owner_sid", lambda: "S-1-5-21-SAME")
    assert fsplat.owns_path(tmp_path / "any") is True


def test_owns_path_windows_rejects_mismatched_sid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)
    monkeypatch.setattr(fsplat, "_windows_owner_sid", lambda path: "S-1-5-21-OWNER")
    monkeypatch.setattr(fsplat, "_windows_default_owner_sid", lambda: "S-1-5-21-OTHER")
    assert fsplat.owns_path(tmp_path / "any") is False


def test_owns_path_windows_fails_closed_on_readback_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise OSError("simulated ctypes failure")

    monkeypatch.setattr(fsplat, "_windows_owner_sid", _raise)
    monkeypatch.setattr(fsplat, "_windows_default_owner_sid", lambda: "S-1-5-21-OTHER")
    assert fsplat.owns_path(tmp_path / "any") is False


def test_fchmod_and_chmod_are_noop_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsplat, "IS_WINDOWS", True)

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("POSIX chmod/fchmod must not run on simulated Windows")

    monkeypatch.setattr(os, "chmod", _fail)
    # os.fchmod does not exist as an attribute on real Windows at all.
    monkeypatch.setattr(os, "fchmod", _fail, raising=False)
    fsplat.chmod_if_supported(tmp_path, 0o700)
    fsplat.fchmod_if_supported(0, 0o600)


@pytest.mark.skipif(fsplat.IS_WINDOWS, reason="POSIX mode bits are not meaningful on Windows")
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


@pytest.mark.skipif(fsplat.IS_WINDOWS, reason="os.O_NOFOLLOW does not exist on Windows")
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


@pytest.mark.skipif(not fsplat.IS_WINDOWS, reason="diagnostic for the real Windows ctypes SID path only")
def test_windows_sid_helpers_match_for_a_self_created_file(tmp_path: Path) -> None:
    """Real end-to-end check (no mocking) on an actual Windows host: the
    real ctypes calls must agree that a file this same process just
    created is owned by this process, using the exact same TokenOwner vs
    file-owner-SID comparison owns_path() performs in production.

    This was a real regression once: comparing TokenUser (a personal SID)
    against the file's owner SID always failed on GitHub's windows-latest
    runner, because that runner's Administrator-context token stamps new
    files with the BUILTIN Administrators group SID (S-1-5-32-544), not
    the signed-in user's personal SID. TokenOwner matches what NTFS
    actually assigns.
    """
    current = fsplat._windows_default_owner_sid()
    assert current, "default-owner SID lookup returned empty"
    target = tmp_path / "owned"
    target.write_text("x", encoding="utf-8")
    owner = fsplat._windows_owner_sid(target)
    assert owner == current, f"owner={owner!r} current={current!r}"
