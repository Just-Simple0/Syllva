"""Create a new SQLite online backup without overwriting any existing file."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def backup(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve(strict=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Backup destination must be a new file')
    with destination.open('xb'):
        pass
    with (sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as origin,
          sqlite3.connect(destination) as target):
        origin.backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
