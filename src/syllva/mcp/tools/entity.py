"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

resolve_entity = partial(invoke_read_only, 'uls.resolve_entity')
select_resolution = partial(invoke_read_only, 'uls.select_resolution')
