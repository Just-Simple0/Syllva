"""Data directory resolution and layout (implementation spec §3.1)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "SYLLVA_DATA_DIR"
_DIRECTORY_MODE = 0o700


@dataclass(frozen=True)
class DataLayout:
    """Every path Core uses under the data directory."""

    root: Path

    @property
    def lock_path(self) -> Path:
        return self.root / "db.lock"

    @property
    def db_path(self) -> Path:
        return self.root / "state.sqlite3"

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> DataLayout:
        """Create the directory tree (owner-only on POSIX). Idempotent."""

        for directory in (self.root, self.blobs_dir, self.tmp_dir, self.logs_dir, self.backups_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(directory, _DIRECTORY_MODE)
        return self


def default_data_dir(*, platform: str | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """OS-standard location (spec §3.1): macOS Application Support, Windows %APPDATA%, Linux XDG."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path(environ.get("HOME") or environ.get("USERPROFILE") or Path.home())
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Syllva"
    if platform.startswith("win"):
        appdata = environ.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / "Syllva"
    xdg = environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "syllva"


def resolve_data_dir(*, platform: str | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """``SYLLVA_DATA_DIR`` when set, otherwise the OS default. Always absolute."""

    environ = os.environ if environ is None else environ
    override = environ.get(DATA_DIR_ENV)
    path = Path(override) if override else default_data_dir(platform=platform, environ=environ)
    return path.expanduser().absolute()


def layout(root: Path | None = None) -> DataLayout:
    return DataLayout((root if root is not None else resolve_data_dir()).absolute())
