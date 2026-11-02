"""Data directory resolution (implementation spec §3.1)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from syllva.library.paths import default_data_dir, layout, resolve_data_dir

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        ("darwin", {"HOME": "/Users/u"}, Path("/Users/u/Library/Application Support/Syllva")),
        ("win32", {"USERPROFILE": "C:/Users/u", "APPDATA": "C:/Users/u/AppData/Roaming"}, Path("C:/Users/u/AppData/Roaming/Syllva")),
        ("linux", {"HOME": "/home/u"}, Path("/home/u/.local/share/syllva")),
        ("linux", {"HOME": "/home/u", "XDG_DATA_HOME": "/data"}, Path("/data/syllva")),
    ],
)
def test_os_default_locations(platform: str, environ: dict[str, str], expected: Path) -> None:
    assert default_data_dir(platform=platform, environ=environ) == expected


def test_env_override_wins_and_is_absolute(tmp_path: Path) -> None:
    assert resolve_data_dir(environ={"SYLLVA_DATA_DIR": str(tmp_path / "x")}) == tmp_path / "x"


def test_layout_creates_owner_only_tree(tmp_path: Path) -> None:
    data = layout(tmp_path / "data").ensure()
    for directory in (data.root, data.blobs_dir, data.tmp_dir, data.logs_dir, data.backups_dir):
        assert directory.is_dir()
        if os.name != "nt":
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert data.db_path.name == "state.sqlite3" and data.lock_path.name == "db.lock"
