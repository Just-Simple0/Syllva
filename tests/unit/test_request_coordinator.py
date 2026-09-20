from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from uls.intake.coordinator import RequestCoordinator


def row(number, created="2026-09-20T00:00:00Z"):
    return {"id": str(UUID(int=number)), "created_time": created, "Submitted": True,
            "Session": {"relation": [str(UUID(int=100))]}}


class Handler:
    def __init__(self, name, events, *, fail=False):
        self.name, self.events, self.fail = name, events, fail

    def validate_batch(self, snapshot):
        self.events.append((self.name, "validate"))
        return SimpleNamespace(epoch=snapshot.epoch, workspace=snapshot.workspace,
                               blocked_slots=frozenset(), workspace_blocked=False)

    def claim_ordered(self, batch):
        self.events.append((self.name, "claim"))
        if self.fail:
            raise RuntimeError("provider secret must never be reported")
        return batch

    def publish_pending(self, barrier):
        self.events.append((self.name, "publish"))
        return "published"


def coordinator(sources, handlers=None, **kwargs):
    def listing(source_id):
        value = sources[source_id]
        if isinstance(value, Exception):
            raise value
        return value
    return RequestCoordinator(workspace="semester", source_ids=tuple(sources),
                              list_records=listing, handlers=handlers or {},
                              clock=lambda: datetime(2026, 9, 20, tzinfo=UTC), **kwargs)


def test_complete_snapshot_orders_across_sources_and_cannot_be_mutated():
    original = row(3)
    worker = coordinator({"a": [original, row(2)], "b": [row(1)]})
    snapshot = worker.snapshot()
    original["Session"]["relation"].append("later mutation")
    assert [item.page_id for item in snapshot.rows] == [str(UUID(int=i)) for i in (1, 2, 3)]
    assert len(snapshot.rows[-1].values["Session"]["relation"]) == 1
    with pytest.raises(TypeError):
        snapshot.rows[0].values["Submitted"] = False
    assert snapshot.complete_sources == {"a", "b"}


@pytest.mark.parametrize("broken", [RuntimeError("private provider output"),
                                     [row(2), row(2)], [row(4, "not a timestamp")]])
def test_failed_or_ambiguous_source_cannot_authorize_publication(broken):
    events = []
    worker = coordinator({"a": [row(1)], "b": broken}, {"range": Handler("range", events)})
    snapshot = worker.snapshot()
    assert snapshot.complete_sources == {"a"}
    assert snapshot.failed_sources == {"b": "request_snapshot_unavailable"}
    barrier = worker.receive(snapshot)
    assert worker.publish(barrier)["status"] == "blocked"
    assert all(event[1] != "publish" for event in events)


def test_bounded_overflow_holds_all_workspace_publication():
    worker = coordinator({"a": [row(1), row(2)]}, max_requests=1)
    snapshot = worker.snapshot()
    assert snapshot.rows == ()
    assert worker.receive(snapshot).workspace_blocked


def test_all_claims_and_invalidations_precede_any_publication():
    events = []
    handlers = {name: Handler(name, events) for name in ("range", "notes")}
    worker = coordinator({"a": [row(1)]}, handlers)
    worker.publish(worker.receive(worker.snapshot()))
    assert events == [("range", "validate"), ("range", "claim"),
                      ("notes", "validate"), ("notes", "claim"),
                      ("range", "publish"), ("notes", "publish")]


def test_claim_failure_blocks_otherwise_successful_handler():
    events = []
    worker = coordinator({"a": [row(1)]}, {
        "range": Handler("range", events), "notes": Handler("notes", events, fail=True),
    })
    assert worker.publish(worker.receive(worker.snapshot()))["status"] == "blocked"
    assert all(event[1] != "publish" for event in events)


def test_composition_merges_local_inbox_before_provider_approval():
    from uls.intake.composition import merge_request_handler

    events = []
    original = coordinator(
        {"notion": [row(1)]}, {"range": Handler("range", events)},
        after_publish=lambda barrier: events.append(("approval", "apply")),
    )
    worker = SimpleNamespace(request_coordinators=[original])
    merged = merge_request_handler(
        worker, workspace="semester", name="notes", handler=Handler("notes", events),
        sources={"local-inbox": lambda source: [row(2)]},
    )
    assert worker.request_coordinators == [merged]
    snapshot = merged.snapshot()
    assert snapshot.complete_sources == {"notion", "local-inbox"}
    merged.publish(merged.receive(snapshot))
    assert events == [("range", "validate"), ("range", "claim"),
                      ("notes", "validate"), ("notes", "claim"),
                      ("range", "publish"), ("notes", "publish"), ("approval", "apply")]
    with pytest.raises(ValueError, match="duplicate"):
        merge_request_handler(worker, workspace="semester", name="notes",
                              handler=Handler("notes", events), sources={"another": lambda _: []})


def test_composition_merges_provider_after_local_inbox_and_keeps_approval():
    from uls.intake.composition import merge_request_handler

    events = []
    original = coordinator(
        {"local-inbox": [row(1)]}, {"notes": Handler("notes", events)},
    )
    worker = SimpleNamespace(request_coordinators=[original])
    merged = merge_request_handler(
        worker, workspace="semester", name="range", handler=Handler("range", events),
        sources={"notion": lambda source: [row(2)]},
        after_publish=lambda barrier: events.append(("approval", "apply")),
    )
    assert worker.request_coordinators == [merged]
    assert merged.source_ids == ("local-inbox", "notion")
    snapshot = merged.snapshot()
    assert snapshot.complete_sources == {"local-inbox", "notion"}
    merged.publish(merged.receive(snapshot))
    assert events == [
        ("notes", "validate"), ("notes", "claim"),
        ("range", "validate"), ("range", "claim"),
        ("notes", "publish"), ("range", "publish"),
        ("approval", "apply"),
    ]


def test_merged_workspace_source_failure_blocks_handlers_and_approval():
    from uls.intake.composition import merge_request_handler

    events = []
    original = coordinator(
        {"local-inbox": RuntimeError("inbox unavailable")},
        {"notes": Handler("notes", events)},
    )
    worker = SimpleNamespace(request_coordinators=[original])
    merged = merge_request_handler(
        worker, workspace="semester", name="range", handler=Handler("range", events),
        sources={"notion": lambda source: [row(2)]},
        after_publish=lambda barrier: events.append(("approval", "apply")),
    )
    snapshot = merged.snapshot()
    assert snapshot.failed_sources == {"local-inbox": "request_snapshot_unavailable"}
    result = merged.publish(merged.receive(snapshot))
    assert result["status"] == "blocked"
    assert all(event[1] != "publish" for event in events)
    assert ("approval", "apply") not in events
