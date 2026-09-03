import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import InvalidCandidateError
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.ephemeral.models import ResolutionCandidate


def test_candidate_from_another_resolution_is_rejected() -> None:
    store = MemoryEphemeralStore()
    candidate = ResolutionCandidate("input", "session", "COMP319-S05", "05")
    first = store.create_resolution([candidate], ttl_seconds=30)
    second = store.create_resolution([candidate], ttl_seconds=30)

    with pytest.raises(InvalidCandidateError) as error:
        store.consume_resolution_choice(second.resolution_id, first.candidates[0].candidate_id)
    assert error.value.code == "INVALID_CANDIDATE"

    resolved = store.consume_resolution_choice(
        first.resolution_id, first.candidates[0].candidate_id
    )
    assert resolved.entity_id == "COMP319-S05"
