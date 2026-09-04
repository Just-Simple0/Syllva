import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain import contracts as domain_contracts
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.state.sqlite import SQLiteStateStore


def test_memory_ephemeral_store_conforms_to_domain_protocol() -> None:
    assert isinstance(MemoryEphemeralStore(), domain_contracts.EphemeralStore)


def test_sqlite_state_store_conforms_to_domain_protocol() -> None:
    store = SQLiteStateStore(":memory:")
    try:
        assert isinstance(store, domain_contracts.StateStore)
    finally:
        store.close()


def test_authorize_locator_contract_exposes_current_fingerprint() -> None:
    contract_signature = inspect.signature(
        domain_contracts.EphemeralStore.authorize_locator
    )
    implementation_signature = inspect.signature(MemoryEphemeralStore.authorize_locator)

    assert "current_fingerprint" in contract_signature.parameters
    assert (
        contract_signature.parameters["current_fingerprint"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert set(contract_signature.parameters) == set(implementation_signature.parameters)


def test_create_context_capability_contract_exposes_fingerprint_inputs() -> None:
    parameters = inspect.signature(
        domain_contracts.EphemeralStore.create_context_capability
    ).parameters

    assert {"source_hash", "source_version", "source_fingerprint", "fingerprints"} <= set(
        parameters
    )
