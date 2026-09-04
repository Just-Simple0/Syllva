import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import ContextExpiredError
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.memory import MemoryEphemeralStore


def test_capability_allowlist_uses_numeric_locator_containment() -> None:
    store = MemoryEphemeralStore()
    capability = store.create_context_capability(
        ["COMP319-M03:p13-p27"],
        caller_scope="client-a",
        ttl_seconds=30,
        source_hash="hash-v1",
        source_version=1,
    )

    assert (
        store.authorize_locator(
            capability.context_id,
            "COMP319-M03:p18",
            "client-a",
            current_fingerprint=SourceFingerprint(1, "hash-v1"),
        )
        is True
    )
    assert store.authorize_locator(capability.context_id, "COMP319-M03:p30", "client-a") is False
    assert store.authorize_locator(capability.context_id, "COMP319-M99:p1", "client-a") is False
    assert store.authorize_locator(capability.context_id, "COMP319-M03:p18", "client-b") is False


def test_expired_capability_raises_context_expired() -> None:
    store = MemoryEphemeralStore()
    capability = store.create_context_capability(
        ["COMP319-M03:p1"], None, ttl_seconds=0, source_hash="hash-v1", source_version=1
    )

    with pytest.raises(ContextExpiredError) as error:
        store.authorize_locator(capability.context_id, "COMP319-M03:p1", None)
    assert error.value.code == "CONTEXT_EXPIRED"
    assert store.get_context_capability(capability.context_id) is None
