import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import json
import os
import socket
from types import SimpleNamespace

import uls.orchestration.locks as locks_module
from uls.orchestration.locks import LocalWorkerLock


def test_stale_recovery_does_not_unlink_a_replaced_lock_instance(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    path.write_text(json.dumps({"pid": 999_999_999, "host": "old-host", "token": "old"}), encoding="utf-8")
    lock = LocalWorkerLock(path, malformed_stale_after=0)
    snapshot = lock._read_lock_snapshot()
    assert snapshot is not None

    path.write_text(
        json.dumps({"pid": os.getpid(), "host": "new-owner", "token": "new"}),
        encoding="utf-8",
    )
    assert lock._unlink_if_same_instance(snapshot) is False
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "new"


def test_stale_recovery_serializes_new_owner_attempt_before_delete(tmp_path, monkeypatch) -> None:
    path = tmp_path / "worker.lock"
    path.write_text(
        json.dumps({"pid": 999_999_999, "host": socket.gethostname(), "token": "old"}),
        encoding="utf-8",
    )
    reclaimer = LocalWorkerLock(path)
    new_owner = LocalWorkerLock(path)
    snapshot = reclaimer._stale_lock_snapshot()
    assert snapshot is not None

    original_unlink = locks_module._unlink_path_if_fd_matches
    attempted = False

    def attempt_new_owner_before_delete(target, fd):
        nonlocal attempted
        if target == path and not attempted:
            attempted = True
            # The reclaimer has already verified the stale inode and holds its
            # descriptor lock.  A competing worker cannot replace that inode
            # in the check-to-delete interval.
            assert new_owner.acquire(timeout=0) is False
        return original_unlink(target, fd)

    monkeypatch.setattr(locks_module, "_unlink_path_if_fd_matches", attempt_new_owner_before_delete)
    assert reclaimer._unlink_if_same_instance(snapshot) is True
    assert attempted is True

    assert new_owner.acquire(timeout=0) is True
    new_owner.release()
    assert not path.exists()


def test_dead_local_owner_can_be_recovered(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    path.write_text(
        json.dumps({"pid": 999_999_999, "host": socket.gethostname(), "token": "old"}),
        encoding="utf-8",
    )
    lock = LocalWorkerLock(path)
    assert lock.acquire() is True
    assert lock.is_held is True
    lock.release()
    assert not path.exists()


def _install_fake_msvcrt(monkeypatch) -> None:
    """Real Windows APIs cannot run here; a fake msvcrt lets the os.name
    == 'nt' branches in _try_advisory_lock/_unlock_advisory_lock execute for
    real, exercising the actual acquire/release/reclaim control flow (not
    just an isolated helper) the way PR review discovered the real Windows
    CI runner was hitting a genuine bug in."""

    def locking(fd, mode, _nbytes):
        del fd, mode

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=0))


def test_acquire_release_cycle_succeeds_on_simulated_windows(tmp_path, monkeypatch) -> None:
    """Regression for a real bug PR #9 CI found: Windows cannot delete a
    file while this process still holds it open (no FILE_SHARE_DELETE),
    so release() must close its descriptor before unlinking there."""
    monkeypatch.setattr(locks_module, "_IS_WINDOWS", True)
    _install_fake_msvcrt(monkeypatch)
    path = tmp_path / "worker.lock"
    lock = LocalWorkerLock(path)
    assert lock.acquire() is True
    lock.release()
    assert not path.exists()
    # The bug this guards against left a stale lock file behind, which made
    # every subsequent acquire on the same host/pid fail forever.
    second = LocalWorkerLock(path)
    assert second.acquire() is True
    second.release()
    assert not path.exists()


def test_dead_owner_reclaim_succeeds_on_simulated_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(locks_module, "_IS_WINDOWS", True)
    _install_fake_msvcrt(monkeypatch)
    path = tmp_path / "worker.lock"
    path.write_text(
        json.dumps({"pid": 999_999_999, "host": socket.gethostname(), "token": "old"}),
        encoding="utf-8",
    )
    lock = LocalWorkerLock(path)
    assert lock.acquire() is True
    assert lock.is_held is True
    lock.release()
    assert not path.exists()
