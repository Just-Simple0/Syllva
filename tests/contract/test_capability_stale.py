import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.domain.errors import ContextExpiredError, LocatorNotAllowedError, LocatorStaleError
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.capabilities import CapabilityBinding, authorize_locator, issue_context_capability


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


def test_public_retrieval_authorization_requires_current_role_validation() -> None:
    store = MemoryEphemeralStore()
    capability = issue_context_capability(
        store,
        [
            CapabilityBinding(
                entity_id="COMP319-M03",
                locator="COMP319-M03:p13-p27",
                source_hash="hash-v1",
                source_version=1,
                source_class="professor_material",
            )
        ],
    )
    current = SourceFingerprint(1, "hash-v1")

    with pytest.raises(LocatorNotAllowedError):
        authorize_locator(store, capability.context_id, "COMP319-M03:p18", None, current)
    with pytest.raises(LocatorNotAllowedError):
        authorize_locator(
            store,
            capability.context_id,
            "COMP319-M03:p18",
            None,
            current,
            role_validator=lambda binding: False,
        )


def test_public_retrieval_authorization_allows_valid_role_and_source_class() -> None:
    store = MemoryEphemeralStore()
    capability = issue_context_capability(
        store,
        [
            CapabilityBinding(
                entity_id="COMP319-M03",
                locator="COMP319-M03:p13-p27",
                source_hash="hash-v1",
                source_version=1,
                source_class="professor_material",
            )
        ],
    )
    current = SourceFingerprint(1, "hash-v1")
    seen: list[str] = []

    def validate_role(binding: CapabilityBinding) -> bool:
        seen.append(binding.source_class)
        return binding.source_class == "professor_material"

    assert authorize_locator(
        store,
        capability.context_id,
        "COMP319-M03:p18",
        None,
        current,
        role_validator=validate_role,
    ) is True
    assert seen == ["professor_material"]
