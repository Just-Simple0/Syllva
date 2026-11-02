"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

get_user_context = partial(invoke_read_only, 'uls.get_user_context')
