"""Direct tool entry points sharing transport validation and safe errors."""
from functools import partial

from . import invoke_read_only

verify_claim = partial(invoke_read_only, 'uls.verify_claim')
