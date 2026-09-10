"""Programmatic entry point for `uls jobs`; shares parser and runtime."""
from functools import partial

from . import execute_command

execute = partial(execute_command, 'jobs')
