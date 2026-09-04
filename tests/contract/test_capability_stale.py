import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import ContextExpiredError, LocatorStaleError
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.memory import MemoryEphemeralStore


def test_capability_fingerprint_must_match_current_source() -> None:
    store = MemoryEphemeralStore()
    capability = store.create_context_capability(
        ["COMP319-M03:p13-p27"],
        "client-a",
        30,
        "hash-v1",
        1,
    )
    current = SourceFingerprint(source_version=1, source_hash="hash-v1")
    assert (
        store.authorize_locator(
            capability.context_id,
            "COMP319-M03:p18",
            "client-a",
        )
        is False
    )
    assert (
        store.authorize_locator(
            capability.context_id,
            "COMP319-M03:p18",
            "client-a",
            current_fingerprint=current,
        )
        is True
    )
    with pytest.raises(LocatorStaleError) as error:
        store.authorize_locator(
            capability.context_id,
            "COMP319-M03:p18",
            "client-a",
            current_fingerprint=SourceFingerprint(2, "hash-v2"),
        )
    assert error.value.code == "LOCATOR_STALE"


def test_expiry_and_malformed_locator_are_structured_correctly() -> None:
    store = MemoryEphemeralStore()
    capability = store.create_context_capability(
        ["COMP319-M03:p1"], None, 0, source_hash="hash-v1", source_version=1
    )
    with pytest.raises(ContextExpiredError) as error:
        store.authorize_locator(capability.context_id, "COMP319-M03:p1")
    assert error.value.code == "CONTEXT_EXPIRED"

    live_store = MemoryEphemeralStore()
    live = live_store.create_context_capability(
        ["COMP319-M03:p1"], None, 30, source_hash="hash-v1", source_version=1
    )
    assert live_store.authorize_locator(live.context_id, "not-a-locator") is False
