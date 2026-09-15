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
