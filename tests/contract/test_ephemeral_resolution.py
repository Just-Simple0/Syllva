import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import InvalidCandidateError, ResolutionExpiredError
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
    with pytest.raises(ResolutionExpiredError) as error:
        store.consume_resolution_choice(first.resolution_id, first.candidates[0].candidate_id)
    assert error.value.code == "RESOLUTION_EXPIRED"


def test_consumption_and_expiry_release_candidate_ids() -> None:
    store = MemoryEphemeralStore()
    candidate = ResolutionCandidate("cand_reusable", "session", "COMP319-S05", "05")
    first = store.create_resolution([candidate], ttl_seconds=30)
    store.consume_resolution_choice(first.resolution_id, first.candidates[0].candidate_id)
    reused = store.create_resolution([candidate], ttl_seconds=30)
    assert reused.candidates[0].candidate_id == "cand_reusable"

    expired = store.create_resolution([ResolutionCandidate("cand_expired", "session", "COMP319-S06", "06")], ttl_seconds=0)
    assert store.purge_expired() >= 1
    fresh = store.create_resolution(
        [ResolutionCandidate("cand_expired", "session", "COMP319-S06", "06")], ttl_seconds=30
    )
    assert fresh.candidates[0].candidate_id == "cand_expired"
    assert store.get_resolution(expired.resolution_id) is None


def test_resolution_candidate_reservations_roll_back_on_creation_failure() -> None:
    store = MemoryEphemeralStore()
    candidate = ResolutionCandidate("cand_transactional", "session", "COMP319-S05", "05")

    with pytest.raises(TypeError):
        store.create_resolution([candidate, object()], ttl_seconds=30)  # type: ignore[list-item]

    assert "cand_transactional" not in store._candidate_ids
    retry = store.create_resolution([candidate], ttl_seconds=30)
    assert retry.candidates[0].candidate_id == "cand_transactional"
