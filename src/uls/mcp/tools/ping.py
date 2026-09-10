"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

ping = partial(invoke_read_only, 'uls.ping')
