"""Complete request snapshots and a shared worker-tick publication barrier.

The caller owns the local worker lock. Barriers only order work; handlers must
still check current request/source authority immediately before provider writes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID, uuid4


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RequestRow:
    page_id: str
    source_id: str
    created_time: datetime
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_id", str(UUID(self.page_id)))
        if self.created_time.tzinfo is None:
            raise ValueError("request created_time must have a timezone")
        object.__setattr__(self, "created_time", self.created_time.astimezone(UTC))
        object.__setattr__(self, "values", _freeze(self.values))


@dataclass(frozen=True)
class RequestSnapshot:
    epoch: str
    workspace: str
    rows: tuple[RequestRow, ...]
    complete_sources: frozenset[str]
    failed_sources: Mapping[str, str]
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "complete_sources", frozenset(self.complete_sources))
        object.__setattr__(self, "failed_sources", _freeze(self.failed_sources))


@dataclass(frozen=True)
class RequestBarrier:
    epoch: str
    workspace: str
    enabled_handlers: frozenset[str]
    blocked_slots: frozenset[str]
    workspace_blocked: bool


class RequestHandler(Protocol):
    def validate_batch(self, snapshot: RequestSnapshot) -> Any: ...

    def claim_ordered(self, batch: Any) -> Any: ...

    def publish_pending(self, barrier: RequestBarrier) -> Any: ...


class RequestCoordinator:
    """One workspace, only explicitly accepted and ready handlers.

    A failed or overflowing source never becomes an empty successful snapshot.
    All handlers receive the same immutable observation before any publication.
    """

    def __init__(
        self,
        *,
        workspace: str,
        source_ids: Sequence[str],
        list_records: Callable[[str], Sequence[Mapping[str, Any]]],
        handlers: Mapping[str, RequestHandler],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_requests: int = 10_000,
        after_publish: Callable[[RequestBarrier], Any] | None = None,
    ) -> None:
        if not workspace or not source_ids or len(set(source_ids)) != len(source_ids):
            raise ValueError("workspace and unique request sources are required")
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.workspace = workspace
        self.source_ids = tuple(source_ids)
        self.list_records = list_records
        self.handlers = dict(handlers)
        self.clock = clock
        self.max_requests = max_requests
        self.after_publish = after_publish

    def snapshot(self) -> RequestSnapshot:
        rows: list[RequestRow] = []
        complete: set[str] = set()
        failures: dict[str, str] = {}
        seen: set[str] = set()
        for source_id in self.source_ids:
            try:
                source_rows: list[RequestRow] = []
                source_seen: set[str] = set()
                for raw in self.list_records(source_id):
                    created = raw.get("created_time")
                    if not isinstance(created, str):
                        raise TypeError("missing request creation time")
                    row = RequestRow(
                        page_id=raw["id"], source_id=source_id,
                        created_time=datetime.fromisoformat(created),
                        values=raw,
                    )
                    if row.page_id in seen or row.page_id in source_seen:
                        raise ValueError("duplicate request identity")
                    source_seen.add(row.page_id)
                    source_rows.append(row)
                    if len(rows) + len(source_rows) > self.max_requests:
                        raise ValueError("request snapshot exceeds bound")
                rows.extend(source_rows)
                seen.update(source_seen)
                complete.add(source_id)
            except Exception:  # noqa: BLE001 - provider values never enter errors
                failures[source_id] = "request_snapshot_unavailable"
        rows.sort(key=lambda row: (row.created_time, row.page_id))
        return RequestSnapshot(
            str(uuid4()), self.workspace, tuple(rows), frozenset(complete),
            failures, self.clock(),
        )

    def receive(self, snapshot: RequestSnapshot) -> RequestBarrier:
        if snapshot.workspace != self.workspace:
            raise ValueError("snapshot workspace mismatch")
        blocked = bool(snapshot.failed_sources) or (
            snapshot.complete_sources != frozenset(self.source_ids)
        )
        slots: set[str] = set()
        for handler in self.handlers.values():
            try:
                batch = handler.validate_batch(snapshot)
                if batch.epoch != snapshot.epoch or batch.workspace != self.workspace:
                    raise ValueError("validation batch identity mismatch")
                result = handler.claim_ordered(batch)
                if result.epoch != snapshot.epoch or result.workspace != self.workspace:
                    raise ValueError("claim result identity mismatch")
                slots.update(batch.blocked_slots)
                slots.update(result.blocked_slots)
                blocked = blocked or batch.workspace_blocked or result.workspace_blocked
            except Exception:  # noqa: BLE001 - fail closed; no provider payload logging
                blocked = True
        return RequestBarrier(
            snapshot.epoch, self.workspace, frozenset(self.handlers),
            frozenset(slots), bool(blocked),
        )

    def publish(self, barrier: RequestBarrier) -> dict[str, Any]:
        if barrier.workspace != self.workspace or barrier.enabled_handlers != frozenset(self.handlers):
            raise ValueError("publication barrier identity mismatch")
        if barrier.workspace_blocked:
            return {"status": "blocked", "reason": "request_barrier_incomplete"}
        results: dict[str, Any] = {}
        for name, handler in self.handlers.items():
            # A failure stops later publication; the caller still owns the lock.
            results[name] = handler.publish_pending(barrier)
        if self.after_publish is not None:
            results["approvals"] = self.after_publish(barrier)
        return {"status": "ok", "handlers": results}
