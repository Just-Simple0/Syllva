#!/usr/bin/env python3
"""Cooperative, crash-persistent reservation for the local LMS apply cycle."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, NoReturn, TextIO

RUNTIME = Path(__file__).resolve().parents[1] / ".review" / "knu-lms-hourly"


class ReservationError(Exception):
    """Only a fixed code is suitable for public diagnostics."""


def prepare_directory(runtime: Path) -> None:
    for candidate in (runtime.parent, runtime):
        if candidate.is_symlink():
            raise ReservationError("runtime_path_invalid")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not runtime.is_dir() or runtime.stat().st_uid != os.getuid():
        raise ReservationError("runtime_path_invalid")
    os.chmod(runtime, 0o700)


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
            raise ReservationError("reservation_invalid")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            raw = json.load(stream)
        if (not isinstance(raw, dict) or raw.get("version") != 1
                or raw.get("state") not in {"active", "completed"}
                or not isinstance(raw.get("owner_id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", raw["owner_id"])
                or not isinstance(raw.get("scope_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", raw["scope_hash"])):
            raise ReservationError("reservation_invalid")
        return raw
    except (ValueError, UnicodeError) as exc:
        raise ReservationError("reservation_invalid") from exc
    finally:
        os.close(fd)


def atomic_record(path: Path, payload: dict[str, Any]) -> None:
    """Publish metadata durably; a failed temporary write is kept for diagnosis."""
    if path.is_symlink():
        raise ReservationError("reservation_path_invalid")
    fd, temporary = tempfile.mkstemp(prefix=".reservation-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if path.is_symlink():
        raise ReservationError("reservation_path_invalid")
    os.replace(temporary, path)
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


class Reservation:
    def __init__(self, runtime: Path, fd: int, owner_id: str, scope_hash: str) -> None:
        self.runtime = runtime
        self.fd: int | None = fd
        self.owner_id = owner_id
        self.scope_hash = scope_hash

    @classmethod
    def begin(cls, runtime: Path, scope_hash: str) -> Reservation:
        if not re.fullmatch(r"[0-9a-f]{64}", scope_hash):
            raise ReservationError("scope_hash_invalid")
        prepare_directory(runtime)
        fd = os.open(runtime / "apply.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ReservationError("lock_path_invalid")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReservationError("run_busy") from exc
            previous = read_record(runtime / "active-run.json")
            if previous is not None and previous["state"] != "completed":
                raise ReservationError("interrupted_reservation_blocked")
            owner_id = uuid.uuid4().hex
            atomic_record(runtime / "active-run.json", {
                "version": 1, "state": "active", "owner_id": owner_id,
                "scope_hash": scope_hash,
                "started_at": dt.datetime.now(dt.UTC).isoformat(),
            })
            return cls(runtime, fd, owner_id, scope_hash)
        except BaseException:
            os.close(fd)
            raise

    def complete(self) -> None:
        if self.fd is None:
            raise ReservationError("keeper_stopped")
        record = ensure_participant(self.runtime, self.owner_id, self.scope_hash)
        atomic_record(self.runtime / "active-run.json", {
            **record, "state": "completed",
            "completed_at": dt.datetime.now(dt.UTC).isoformat(),
        })

    def close(self) -> None:
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


def ensure_participant(runtime: Path, owner_id: str, scope_hash: str) -> dict[str, Any]:
    record = read_record(runtime / "active-run.json")
    if (record is None or record["state"] != "active"
            or record["owner_id"] != owner_id or record["scope_hash"] != scope_hash):
        raise ReservationError("reservation_binding_mismatch")
    return record


def _emit(output: TextIO, payload: dict[str, Any]) -> None:
    output.write(json.dumps(payload, sort_keys=True) + "\n")
    output.flush()


def hold_cli(runtime: Path, scope_hash: str, stdin: TextIO, output: TextIO) -> int:
    reservation = Reservation.begin(runtime, scope_hash)
    try:
        _emit(output, {"status": "lock_held", "owner_id": reservation.owner_id})
        while True:
            command = stdin.readline()
            if command == "":
                _emit(output, {"status": "interrupted_reservation_blocked"})
                return 3
            ensure_participant(runtime, reservation.owner_id, scope_hash)
            if command.strip() == "release":
                reservation.complete()
                _emit(output, {"status": "lock_released"})
                return 0
            if command.strip() == "status":
                _emit(output, {"status": "lock_held", "owner_id": reservation.owner_id})
            else:
                _emit(output, {"status": "lock_held", "error": "unknown_command"})
    finally:
        reservation.close()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ReservationError("argument_error")


def main() -> int:
    parser = _Parser()
    parser.add_argument("--scope-hash", required=True)
    try:
        args = parser.parse_args()
        return hold_cli(RUNTIME, args.scope_hash, sys.stdin, sys.stdout)
    except ReservationError as exc:
        _emit(sys.stdout, {"status": "failed", "error": str(exc)})
        return 2
    except (OSError, KeyboardInterrupt, ValueError):
        _emit(sys.stdout, {"status": "failed", "error": "reservation_unavailable"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
