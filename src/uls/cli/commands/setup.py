"""Programmatic entry point for ``uls setup``."""

from functools import partial

from . import execute_command

execute = partial(execute_command, "setup")
