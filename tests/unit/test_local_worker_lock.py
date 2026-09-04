import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import json
import os
import socket

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
