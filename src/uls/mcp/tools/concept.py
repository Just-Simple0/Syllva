"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

search_concept = partial(invoke_read_only, 'uls.search_concept')
