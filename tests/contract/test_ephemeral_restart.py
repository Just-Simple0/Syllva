import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import ContextExpiredError, ResolutionExpiredError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.ephemeral.models import ResolutionCandidate


def test_new_memory_store_invalidates_resolution_and_context_ids() -> None:
    first = MemoryEphemeralStore()
    resolution = first.create_resolution(
        [ResolutionCandidate("input", "session", "COMP319-S05", "05")], ttl_seconds=30
    )
    context = first.create_context_capability(["COMP319-M03:p1"], None, ttl_seconds=30)

    restarted = MemoryEphemeralStore()
    assert restarted.get_resolution(resolution.resolution_id) is None
    assert restarted.get_context_capability(context.context_id) is None
    with pytest.raises(ResolutionExpiredError):
        restarted.consume_resolution_choice(
            resolution.resolution_id, resolution.candidates[0].candidate_id
        )
    with pytest.raises(ContextExpiredError):
        restarted.authorize_locator(context.context_id, "COMP319-M03:p1", None)
