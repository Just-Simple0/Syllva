"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

get_source_chunk = partial(invoke_read_only, 'uls.get_source_chunk')
