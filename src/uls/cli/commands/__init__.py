"""Programmatic command entry points sharing the public CLI parser."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def execute_command(command: str, arguments: Sequence[str] = (), *,
                    config_path: str | Path = 'config.yaml') -> int:
    from uls.cli.main import main
    return main(['--config', str(config_path), command, *arguments])
