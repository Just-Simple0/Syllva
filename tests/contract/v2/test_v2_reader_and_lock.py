"""Read-only state access (part of C-02) and the exclusive DB lock (part of C-20).

C-02's other half (RetrievalService never imports StateWriter) lands with the
service in R1b-4; C-20's Shell protocol half lands in R2.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from syllva.library.dblock import DatabaseLock, DatabaseLocked
from syllva.library.paths import layout
from syllva.state.v2.database import Database, open_database

pytestmark = pytest.mark.contract

WRITES = [
    "INSERT INTO courses(course_key, name, semester, created_at) VALUES ('k', 'n', 's', 't')",
    "UPDATE courses SET name = 'x'",
    "DELETE FROM courses",
    "CREATE TABLE evil(x)",
    "DROP TABLE courses",
    "PRAGMA query_only = OFF",
]


@pytest.mark.parametrize("sql", WRITES)
def test_reader_refuses_every_write(v2_db: Database, sql: str) -> None:
    with v2_db.reader() as reader:
        if sql.startswith("PRAGMA"):
            reader.fetch_all(sql)  # turning query_only off cannot lift mode=ro
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.fetch_all(WRITES[0])
        else:
            with pytest.raises(sqlite3.OperationalError):
                reader.fetch_all(sql)


def test_reader_sees_committed_writer_data(v2_db: Database) -> None:
    v2_db.connection.execute("INSERT INTO courses(course_key, name, semester, created_at) VALUES ('k', 'n', 's', 't')")
    with v2_db.reader() as reader:
        row = reader.fetch_one("SELECT name FROM courses WHERE course_key = 'k'")
        assert row is not None and row["name"] == "n"


def test_second_core_cannot_open_the_database(tmp_path: Path) -> None:
    data = layout(tmp_path / "data")
    with open_database(data):
        with pytest.raises(DatabaseLocked) as info:
            open_database(data)
        assert info.value.code == "DB_LOCKED" and info.value.exit_code == 3
    with open_database(data):  # released on close
        pass


def test_lock_descriptor_is_not_inheritable(tmp_path: Path) -> None:
    lock = DatabaseLock(tmp_path / "db.lock").acquire()
    try:
        assert lock._fd is not None and os.get_inheritable(lock._fd) is False
    finally:
        lock.release()


def test_lock_is_released_when_the_holder_process_dies(tmp_path: Path) -> None:
    lock_path = tmp_path / "db.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                from pathlib import Path
                from syllva.library.dblock import DatabaseLock
                DatabaseLock(Path({str(lock_path)!r})).acquire()
                print("held", flush=True)
                time.sleep(60)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(DatabaseLocked):
            DatabaseLock(lock_path).acquire()
    finally:
        holder.kill()
        holder.wait(timeout=30)
    deadline = time.monotonic() + 10
    while True:
        try:
            DatabaseLock(lock_path).acquire().release()
            break
        except DatabaseLocked:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)
