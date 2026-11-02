"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

get_material_context = partial(invoke_read_only, 'uls.get_material_context')
