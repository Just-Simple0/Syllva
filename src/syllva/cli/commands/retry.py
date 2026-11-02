"""Programmatic entry point for `syllva retry`; shares parser and runtime."""
from functools import partial

from . import execute_command

execute = partial(execute_command, 'retry')
