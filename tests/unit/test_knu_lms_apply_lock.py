"""Meaningful process-death and ownership checks; no live runtime/credentials."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

FILE = Path(__file__).resolve().parents[2] / "scripts" / "knu_lms_apply_lock.py"
spec = importlib.util.spec_from_file_location("apply_reservation_test", FILE)
assert spec is not None and spec.loader is not None
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
SCOPE = "a" * 64


def test_exclusion_and_clean_release(tmp_path: Path) -> None:
    first = lock.Reservation.begin(tmp_path / "runtime", SCOPE)
    try:
        with pytest.raises(lock.ReservationError, match="run_busy"):
            lock.Reservation.begin(first.runtime, SCOPE)
        lock.ensure_participant(first.runtime, first.owner_id, SCOPE)
        with pytest.raises(lock.ReservationError, match="binding_mismatch"):
            lock.ensure_participant(first.runtime, "b" * 32, SCOPE)
        with pytest.raises(lock.ReservationError, match="binding_mismatch"):
            lock.ensure_participant(first.runtime, first.owner_id, "b" * 64)
        first.complete()
    finally:
        first.close()
    second = lock.Reservation.begin(first.runtime, SCOPE)
    second.complete()
    second.close()


def test_close_without_complete_cannot_admit_next_writer(tmp_path: Path) -> None:
    first = lock.Reservation.begin(tmp_path / "runtime", SCOPE)
    first.close()
    with pytest.raises(lock.ReservationError, match="interrupted_reservation_blocked"):
        lock.Reservation.begin(first.runtime, SCOPE)
    with pytest.raises(lock.ReservationError, match="keeper_stopped"):
        first.complete()


def test_killed_process_releases_os_lock_but_blocks_next_owner(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    code = (
        "import importlib.util,sys;from pathlib import Path;"
        "s=importlib.util.spec_from_file_location('holder',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "m.hold_cli(Path(sys.argv[2]),sys.argv[3],sys.stdin,sys.stdout)"
    )
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(FILE), str(runtime), SCOPE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        assert json.loads(child.stdout.readline())["status"] == "lock_held"
        child.kill()
        child.wait(timeout=5)
        with pytest.raises(lock.ReservationError, match="interrupted_reservation_blocked"):
            lock.Reservation.begin(runtime, SCOPE)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_symlink_record_is_not_followed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = tmp_path / "untouched.json"
    target.write_text("untouched")
    (runtime / "active-run.json").symlink_to(target)
    with pytest.raises(OSError):
        lock.Reservation.begin(runtime, SCOPE)
    assert target.read_text() == "untouched"


def test_invalid_existing_record_blocks_instead_of_resetting(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    path = runtime / "active-run.json"
    path.write_text('{"state":"active"}')
    path.chmod(0o600)
    with pytest.raises(lock.ReservationError, match="reservation_invalid"):
        lock.Reservation.begin(runtime, SCOPE)
    assert path.read_text() == '{"state":"active"}'


def test_full_reservation_cycle_on_simulated_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the entire begin/complete/close path (not just backend
    selection) with IS_WINDOWS simulated true, so a Unix-only primitive
    anywhere in this call chain fails this test instead of only failing on a
    real Windows runner much later."""
    monkeypatch.setattr(lock.fsplat, "IS_WINDOWS", True)
    monkeypatch.setattr(lock.fsplat, "_windows_owner_sid", lambda path: "S-1-5-21-SAME")
    monkeypatch.setattr(lock.fsplat, "_windows_current_user_sid", lambda: "S-1-5-21-SAME")

    def locking(fd, mode, _nbytes):
        del fd, mode

    fake_msvcrt = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    runtime = tmp_path / "runtime"
    reservation = lock.Reservation.begin(runtime, SCOPE)
    try:
        assert (runtime / "active-run.json").exists()
        reservation.complete()
    finally:
        reservation.close()
    second = lock.Reservation.begin(runtime, SCOPE)
    second.complete()
    second.close()
