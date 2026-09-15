"""Regression tests for CapabilityManager expiry bookkeeping cleanup.

``CapabilityManager`` keeps its own upper-layer bookkeeping
(``_bindings``/``_followups``) alongside the lower ``MemoryEphemeralStore``'s
TTL-bounded contexts.  Before this fix, nothing ever removed a
``context_id`` from those two dicts once the underlying context expired,
so a long-running MCP process leaked memory proportional to the number of
ever-issued capabilities, not the number of currently-live ones.

These tests use a fake monotonic clock (rather than sleeping) so expiry
can be advanced deterministically and instantly.
"""

from __future__ import annotations

import pathlib
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.errors import ContextExpiredError, PolicyDeniedError
from uls.domain.models import PageLocator
from uls.domain.source_ref import SourceRef
from uls.ephemeral.memory import MemoryEphemeralStore
from uls.retrieval.capabilities import CapabilityManager
from uls.retrieval.schemas import CapabilityBinding


class _FakeClock:
    """Deterministic stand-in for time.monotonic()."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    return clock


def _simple_binding(entity_id: str = "COMP319-S05") -> CapabilityBinding:
    return CapabilityBinding(
        entity_id=entity_id,
        locator=PageLocator(entity_id, 1, 5),
        source_hash="hash-v1",
        source_version=1,
        source_class="professor_transcript",
        source_ref=SourceRef("google_drive", "file-1"),
    )


def test_purge_expired_evicts_bookkeeping_for_expired_context(fake_clock: _FakeClock) -> None:
    store = MemoryEphemeralStore()
    manager = CapabilityManager(store, ttl_seconds=60)
    capability = manager.issue([_simple_binding()])
    context_id = capability.context_id

    assert manager.bindings_for(context_id) is not None
    assert manager.active_context_count == 1

    fake_clock.advance(61)
    dropped = manager.purge_expired()

    assert dropped >= 1
    assert manager.active_context_count == 0
    assert manager.bindings_for(context_id) is None


def test_authorize_on_expired_context_lazily_evicts_bookkeeping(fake_clock: _FakeClock) -> None:
    store = MemoryEphemeralStore()
    manager = CapabilityManager(store, ttl_seconds=60)
    capability = manager.issue([_simple_binding()])
    context_id = capability.context_id

    fake_clock.advance(61)

    with pytest.raises(ContextExpiredError):
        manager.authorize(
            context_id,
            "COMP319-S05:p1",
            candidate_validator=lambda _: None,
        )

    # Even without an explicit purge_expired() call, a single failed
    # access to an expired context must not leave it in bookkeeping
    # forever -- this is the lazy half of the leak fix.
    assert manager.active_context_count == 0
    assert manager.bindings_for(context_id) is None


def test_issue_opportunistically_purges_expired_contexts_without_manual_call(
    fake_clock: _FakeClock,
) -> None:
    """Reproduces the reported leak: a caller that only ever calls issue()
    (never purge_expired()) must still see bounded bookkeeping growth once
    contexts start expiring, because issue() purges opportunistically.
    """

    store = MemoryEphemeralStore()
    manager = CapabilityManager(store, ttl_seconds=1)

    for _ in range(49):
        manager.issue([_simple_binding()])
    assert manager.active_context_count == 49

    fake_clock.advance(2)

    # The 50th issue() call crosses the opportunistic-purge interval and
    # must reconcile away every context that already expired, even though
    # the caller never invoked purge_expired() directly.
    manager.issue([_simple_binding()])

    assert manager.active_context_count == 1


def test_max_active_contexts_blocks_growth_when_nothing_has_expired(
    fake_clock: _FakeClock,
) -> None:
    store = MemoryEphemeralStore()
    manager = CapabilityManager(store, ttl_seconds=900, max_active_contexts=3)

    for _ in range(3):
        manager.issue([_simple_binding()])

    with pytest.raises(PolicyDeniedError):
        manager.issue([_simple_binding()])

    assert manager.active_context_count == 3


def test_max_active_contexts_recovers_once_old_contexts_expire(
    fake_clock: _FakeClock,
) -> None:
    """The cap must not become a permanent wall: once old contexts expire,
    issue() should opportunistically reclaim the space itself rather than
    requiring a caller to remember to call purge_expired() first.
    """

    store = MemoryEphemeralStore()
    manager = CapabilityManager(store, ttl_seconds=1, max_active_contexts=3)

    for _ in range(3):
        manager.issue([_simple_binding()])

    fake_clock.advance(2)

    capability = manager.issue([_simple_binding()])

    assert manager.active_context_count == 1
    assert manager.bindings_for(capability.context_id) is not None


class _DelayedCreationStore:
    """Wraps a real ephemeral store, delaying create_context_capability().

    This reproduces the review-reported race: an old two-lock issue()
    implementation checks capacity, releases the lock, does the (here
    artificially slow) lower-store creation, then re-acquires the lock to
    register bookkeeping.  Delaying creation widens that gap so concurrent
    callers reliably race through the check before any of them registers.
    """

    def __init__(self, real: MemoryEphemeralStore, delay: float) -> None:
        self._real = real
        self._delay = delay

    def create_context_capability(self, *args, **kwargs):
        time.sleep(self._delay)
        return self._real.create_context_capability(*args, **kwargs)

    def get_context_capability(self, context_id: str):
        return self._real.get_context_capability(context_id)

    def purge_expired(self) -> int:
        return self._real.purge_expired()


def test_concurrent_issue_never_overshoots_max_active_contexts() -> None:
    """Regression: capacity check-then-register used to happen across two
    separate lock acquisitions, so N callers racing the (slow) lower-store
    creation could all pass the check before any of them registered,
    overshooting max_active_contexts.  The check and the reservation must
    happen atomically in one critical section.
    """

    real_store = MemoryEphemeralStore()
    manager = CapabilityManager(real_store, max_active_contexts=1)
    manager.ephemeral = _DelayedCreationStore(real_store, delay=0.05)

    concurrency = 8
    start_gate = threading.Event()
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def worker() -> None:
        start_gate.wait()
        try:
            manager.issue([])
            outcome = "ok"
        except PolicyDeniedError:
            outcome = "denied"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for thread in threads:
        thread.start()
    start_gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert outcomes.count("ok") == 1
    assert outcomes.count("denied") == concurrency - 1
    assert manager.active_context_count == 1
    assert manager.active_context_count <= manager.max_active_contexts


def test_concurrent_issue_failure_releases_the_reservation() -> None:
    """A reservation claimed during the capacity check must be released if
    lower-store creation subsequently fails, so a failed issuance never
    permanently shrinks capacity for the next caller.
    """

    real_store = MemoryEphemeralStore()
    manager = CapabilityManager(real_store, max_active_contexts=1)

    class _BoomOnce:
        def __init__(self) -> None:
            self.calls = 0

        def create_context_capability(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated lower-store failure")

        def get_context_capability(self, context_id: str):
            return real_store.get_context_capability(context_id)

        def purge_expired(self) -> int:
            return real_store.purge_expired()

    boom = _BoomOnce()
    manager.ephemeral = boom

    with pytest.raises(RuntimeError):
        manager.issue([])
    assert boom.calls == 1
    assert manager.active_context_count == 0

    manager.ephemeral = real_store
    capability = manager.issue([_simple_binding()])
    assert manager.active_context_count == 1
    assert manager.bindings_for(capability.context_id) is not None
