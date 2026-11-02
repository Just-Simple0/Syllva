"""Programmatic entry point for ``syllva setup``."""

from functools import partial

from . import execute_command

execute = partial(execute_command, "setup")
