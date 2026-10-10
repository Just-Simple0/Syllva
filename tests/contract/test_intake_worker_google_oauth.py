"""Five public WORKER entrypoints gate on a fresh attestation before any effect (P2 plan §5)."""
from __future__ import annotations

import copy
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.integration.test_intake_worker_preview import _assign_request, _system

from uls.adapters.drive.worker import GoogleDriveWorkerAdapter
from uls.domain.errors import SourceUnavailableError
from uls.intake.attestation import ReconnectRequiredError, StaticAttestor, WorkerEntryAttestation
from uls.intake.identity import provider_binding_id
from uls.intake.worker import IntakeWorker
from uls.runtime import build_intake_worker

pytestmark = pytest.mark.contract


class _CountingDrive:
    """Wraps the in-memory Drive port and counts every provider-facing call."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []
        self.capabilities = inner.capabilities

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._inner, name)
        if not callable(value):
            return value

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return value(*args, **kwargs)
        return counted


def _snapshot(harness: dict[str, Any]) -> tuple[Any, Any, Any]:
    notion = copy.deepcopy(harness["notion"].data_sources)
    state = harness["state"]
    receipts = sorted(row.request_key for row in state.list_request_receipts())
    items = sorted(item.intake_id for item in state.list_intake_items())
    return notion, receipts, items


def _oauth_worker(harness: dict[str, Any], attestor: StaticAttestor) -> tuple[IntakeWorker, _CountingDrive]:
    drive = _CountingDrive(harness["drive"])
    worker = IntakeWorker(
        harness["config"], harness["state"], drive, harness["notion"],
        provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
        semester="2026-2", entry_attestor=attestor,
    )
    return worker, drive


def test_failed_attestation_blocks_every_entry_before_any_effect(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        gate = {"fail": True}
        attestor = StaticAttestor(failure=lambda: ReconnectRequiredError() if gate["fail"] else None)
        worker, drive = _oauth_worker(harness, attestor)
        before = _snapshot(harness)
        events_before = list(harness["notion"].events or [])

        result = worker.run_once()
        assert result["status"] == "failed" and result["code"] == "RECONNECT_REQUIRED"
        assert result["discovered"] == 0 and result["processed"] == 0
        with pytest.raises(ReconnectRequiredError):
            worker.sync()
        with pytest.raises(ReconnectRequiredError):
            worker.claim_request("missing-request-key")
        with pytest.raises(ReconnectRequiredError):
            worker.process_item("missing-intake-id")

        assert drive.calls == []
        assert _snapshot(harness) == before
        assert list(harness["notion"].events or []) == events_before
        assert harness["state"].acquire_local_worker_lock() is True
        harness["state"].release_local_worker_lock()
        assert attestor.calls == 4

        gate["fail"] = False
        ok = worker.run_once()
        assert ok["status"] == "ok" and ok["discovered"] >= 1
        assert attestor.calls == 5
        assert drive.calls, "a passing attestation lets discovery read Drive"
        item_id = next(iter(harness["state"].list_intake_items())).intake_id

        gate["fail"] = True
        request_rows = copy.deepcopy(harness["notion"].data_sources["synthetic-requests"])
        with pytest.raises(ReconnectRequiredError):
            worker.create_input_request(item_id)
        assert harness["notion"].data_sources["synthetic-requests"] == request_rows


def test_passing_attestation_is_one_per_entry_and_gate_precedes_lookup(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        worker, _drive = _oauth_worker(harness, attestor)
        assert worker.sync() >= 1
        assert attestor.calls == 1
        with pytest.raises(SourceUnavailableError):
            worker.claim_request("missing-request-key")
        assert attestor.calls == 2
        first = worker.run_once()
        assert first["status"] == "ok" and attestor.calls == 3
        assign = _assign_request(harness["notion"])
        assign.update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        second = worker.run_once()
        assert second["status"] == "ok" and attestor.calls == 4


def test_service_account_worker_without_attestor_is_unchanged(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        worker = harness["worker"]
        assert worker.entry_attestor is None
        assert worker.run_once()["status"] == "ok"


def test_build_intake_worker_runs_startup_gate_once_for_installers(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        from uls.config.credentials import ResolvedCredentials

        worker = build_intake_worker(
            harness["config"], ResolvedCredentials({}), state=harness["state"], drive=harness["drive"],
            notion=harness["notion"], provider_account_binding_id=provider_binding_id("google_drive", "o", "a"),
            semester="2026-2", entry_attestor=attestor,
        )
        assert worker.entry_attestor is attestor
        assert attestor.calls == 1
        failing = StaticAttestor(failure=ReconnectRequiredError)
        with pytest.raises(ReconnectRequiredError):
            build_intake_worker(
                harness["config"], ResolvedCredentials({}), state=harness["state"], drive=harness["drive"],
                notion=harness["notion"], provider_account_binding_id=provider_binding_id("google_drive", "o", "a"),
                semester="2026-2", entry_attestor=failing,
            )


class _RecordingService:
    def __init__(self) -> None:
        self.executed: list[str] = []
        service = self

        class _Files:
            def get(self, **kwargs: Any) -> Any:
                return SimpleNamespace(execute=lambda: service._record("get", kwargs))

            def list(self, **kwargs: Any) -> Any:
                return SimpleNamespace(execute=lambda: service._record("list", kwargs))

            def get_media(self, **kwargs: Any) -> Any:
                return SimpleNamespace()
        self._files = _Files()

    def _record(self, name: str, kwargs: Any) -> dict[str, Any]:
        self.executed.append(name)
        return {"id": "f1", "name": "n", "mimeType": "text/plain", "parents": ["p"], "ownedByMe": True,
                "trashed": False, "permissions": [{"type": "user", "role": "owner"}],
                "capabilities": {"canEdit": True, "canMoveItemWithinDrive": True}, "files": [], "shared": False}

    def files(self) -> Any:
        return self._files


def test_adapter_refuses_provider_calls_outside_a_valid_attestation_context() -> None:
    attestor = StaticAttestor(clock=time.monotonic)
    service = _RecordingService()
    adapter = GoogleDriveWorkerAdapter(service, attestor=attestor)
    with pytest.raises(ReconnectRequiredError):
        adapter.read_metadata("f1")
    with pytest.raises(ReconnectRequiredError):
        adapter.download("f1")
    with pytest.raises(ReconnectRequiredError):
        adapter.search_marker({"k": "v"})
    assert service.executed == []
    foreign = StaticAttestor().attest_entry()
    with pytest.raises(ReconnectRequiredError), adapter.attested(foreign):
        pass
    stale = WorkerEntryAttestation(attestor_id=attestor.attestor_id, role="worker", scope="s", generation=1,
                                   binding_id="b", issued_at=time.monotonic() - 3600)
    with pytest.raises(ReconnectRequiredError), adapter.attested(stale):
        pass
    with pytest.raises(ReconnectRequiredError), adapter.attested(None):
        pass
    assert service.executed == []
    with adapter.attested(attestor.attest_entry()):
        adapter.read_metadata("f1")
    assert service.executed == ["get"]
    with pytest.raises(ReconnectRequiredError):
        adapter.read_metadata("f1")


@pytest.mark.parametrize("field, value", [
    ("role", "mcp"), ("scope", "https://www.googleapis.com/auth/drive.readonly"), ("binding_id", "other-binding"),
    ("generation", 99), ("generation", 0),
])
def test_adapter_rejects_attestations_whose_fields_do_not_match_the_attestor(field, value) -> None:
    attestor = StaticAttestor(scope="https://www.googleapis.com/auth/drive", binding_id="binding-1")
    service = _RecordingService()
    adapter = GoogleDriveWorkerAdapter(service, attestor=attestor)
    good = attestor.attest_entry()
    forged = WorkerEntryAttestation(**{**good.__dict__, field: value})
    with pytest.raises(ReconnectRequiredError), adapter.attested(forged):
        pass
    assert service.executed == []
    with adapter.attested(good):
        adapter.read_metadata("f1")
    assert service.executed == ["get"]


class _MutationService(_RecordingService):
    def __init__(self) -> None:
        super().__init__()
        service = self
        files = self._files

        class _Files(type(files)):  # type: ignore[misc]
            def create(self, **kwargs: Any) -> Any:
                return SimpleNamespace(execute=lambda: service._record("create", kwargs))

            def update(self, **kwargs: Any) -> Any:
                return SimpleNamespace(execute=lambda: service._record("update", kwargs))
        self._files = _Files()


def test_adapter_mutations_preserve_the_reconnect_code_and_download_gates_every_chunk(monkeypatch) -> None:
    attestor = StaticAttestor()
    service = _MutationService()
    adapter = GoogleDriveWorkerAdapter(service, attestor=attestor)
    marker = {"syllva_marker": "m1"}
    with pytest.raises(ReconnectRequiredError):
        adapter.create_folder_with_marker("p", "name", marker)
    with pytest.raises(ReconnectRequiredError):
        adapter.create_file_with_marker("p", "name", "text/plain", b"x", marker)
    with pytest.raises(ReconnectRequiredError):
        adapter.move_file("f1", "p", "q")
    assert service.executed == []
    # Download: the context expires between chunks -> the second chunk never runs.
    chunks: list[int] = []

    class _Downloader:
        def __init__(self, output: Any, request: Any, chunksize: int) -> None:
            self.output = output

        def next_chunk(self, num_retries: int = 0) -> tuple[None, bool]:
            chunks.append(1)
            self.output.write(b"a")
            adapter._context.current = None  # context vanishes after the first chunk
            return None, False
    import googleapiclient.http
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", _Downloader)
    service._record = lambda name, kwargs: {**_RecordingService._record(service, name, kwargs), "size": "2"}  # type: ignore[method-assign]
    with adapter.attested(attestor.attest_entry()), pytest.raises(ReconnectRequiredError):
        adapter.download("f1")
    assert chunks == [1]


def test_real_adapter_and_worker_share_one_attestor_and_fail_closed_with_zero_effects(tmp_path: Path) -> None:
    import sqlite3

    with _system(tmp_path) as harness:
        failing = StaticAttestor(failure=ReconnectRequiredError)
        service = _MutationService()
        adapter = GoogleDriveWorkerAdapter(service, attestor=failing)
        worker = IntakeWorker(
            harness["config"], harness["state"], adapter, harness["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2", entry_attestor=failing,
        )
        state = harness["state"]
        db_path = Path(state.db_path)

        def db_dump() -> list[str]:
            with sqlite3.connect(db_path) as connection:
                return list(connection.iterdump())
        notion_before = copy.deepcopy(harness["notion"].data_sources)
        events_before = list(harness["notion"].events or [])
        dump_before = db_dump()
        assert worker.run_once()["code"] == "RECONNECT_REQUIRED"
        for call in (worker.sync, lambda: worker.claim_request("missing"), lambda: worker.process_item("missing")):
            with pytest.raises(ReconnectRequiredError):
                call()
        assert service.executed == []
        assert db_dump() == dump_before, "no job, plan, receipt, generation or item row changed"
        assert harness["notion"].data_sources == notion_before and list(harness["notion"].events or []) == events_before
        assert state.acquire_local_worker_lock() is True
        state.release_local_worker_lock()
        # Startup gate through the composition root with the same failing attestor.
        from uls.config.credentials import ResolvedCredentials
        with pytest.raises(ReconnectRequiredError):
            build_intake_worker(harness["config"], ResolvedCredentials({}), state=state, drive=adapter,
                                notion=harness["notion"], provider_account_binding_id=provider_binding_id("google_drive", "o", "a"),
                                semester="2026-2", entry_attestor=failing)
        assert service.executed == [] and db_dump() == dump_before


def test_second_entry_cannot_refresh_the_shared_credential_while_one_entry_runs(tmp_path: Path) -> None:
    import threading

    from uls.domain.errors import ProviderUnavailableError

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]
        hold, entered = threading.Event(), threading.Event()

        class _StallingDrive(_CountingDrive):
            def list_folder(self, folder_id: str) -> Any:
                entered.set()
                assert hold.wait(10)
                return inner.list_folder(folder_id)
        drive = _StallingDrive(inner)
        worker = IntakeWorker(
            harness["config"], harness["state"], drive, harness["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2", entry_attestor=attestor,
        )
        outcome: dict[str, Any] = {}
        runner = threading.Thread(target=lambda: outcome.update(run=worker.run_once()))
        runner.start()
        assert entered.wait(10)  # A is inside provider work with generation 1
        calls_during = attestor.calls
        with pytest.raises(ProviderUnavailableError):
            worker.claim_request("missing")  # B: refused before any refresh
        with pytest.raises(ProviderUnavailableError):
            worker.sync()
        assert worker.run_once()["status"] == "already_running"
        assert attestor.calls == calls_during, "B never refreshed the shared credential"
        hold.set()
        runner.join(10)
        assert outcome["run"]["status"] == "ok"
        assert attestor.calls == calls_during
        assert worker.sync() >= 0 and attestor.calls == calls_during + 1  # entries resume after A finished


def test_cold_start_with_rejected_attestation_creates_no_local_state(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        from uls.config.credentials import ResolvedCredentials
        config = copy.deepcopy(harness["config"])
        config.system.workspace_dir = str(tmp_path / "cold")
        with pytest.raises(ReconnectRequiredError):
            build_intake_worker(config, ResolvedCredentials({}), drive=harness["drive"], notion=harness["notion"],
                                provider_account_binding_id=provider_binding_id("google_drive", "o", "a"),
                                semester="2026-2", entry_attestor=StaticAttestor(failure=ReconnectRequiredError))
        assert not (tmp_path / "cold").exists()


def test_positive_entry_propagates_one_attestation_to_adapter_context_and_installers(tmp_path: Path) -> None:
    import contextlib

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        seen: list[tuple[str, int | None]] = []
        inner = harness["drive"]

        class _ContextDrive(_CountingDrive):
            current: WorkerEntryAttestation | None = None

            @contextlib.contextmanager
            def attested(self, attestation):
                previous, self.current = self.current, attestation
                try:
                    yield
                finally:
                    self.current = previous

            def list_folder(self, folder_id: str):
                seen.append(("list_folder", self.current.generation if self.current else None))
                return inner.list_folder(folder_id)
        drive = _ContextDrive(inner)
        from uls.config.credentials import ResolvedCredentials
        worker = build_intake_worker(
            harness["config"], ResolvedCredentials({}), state=harness["state"], drive=drive, notion=harness["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "o", "a"), semester="2026-2",
            entry_attestor=attestor,
        )
        assert attestor.calls == 1  # startup gate: exactly one attestation for both installers
        first = worker.run_once()
        assert first["status"] == "ok" and attestor.calls == 2
        generations = {generation for name, generation in seen if name == "list_folder"}
        assert generations == {2}, "every Drive read of the tick ran inside the tick's own attestation context"
        assert drive.current is None  # context released after the entry


def test_reconnect_during_a_tick_stops_the_tick_without_further_effects(tmp_path: Path) -> None:
    import contextlib

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]
        calls: list[str] = []

        class _RevokingDrive(_CountingDrive):
            current: WorkerEntryAttestation | None = None

            @contextlib.contextmanager
            def attested(self, attestation):
                if attestor.revoked:
                    raise ReconnectRequiredError()
                previous, self.current = self.current, attestation
                try:
                    yield
                finally:
                    self.current = previous

            def list_folder(self, folder_id: str):
                calls.append("list_folder")
                if len(calls) == 1:
                    attestor.revoked = True  # the SDK refresh was rejected mid-tick, after a valid entry proof
                if attestor.revoked:
                    raise ReconnectRequiredError()
                return inner.list_folder(folder_id)
        drive = _RevokingDrive(inner)
        worker = IntakeWorker(
            harness["config"], harness["state"], drive, harness["notion"],
            provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
            semester="2026-2", entry_attestor=attestor,
        )
        notion_before = copy.deepcopy(harness["notion"].data_sources)
        events_before = list(harness["notion"].events or [])
        result = worker.run_once()
        assert result["status"] == "failed" and result["code"] == "RECONNECT_REQUIRED"
        assert calls == ["list_folder"], "no provider call after the rejection"
        assert attestor.calls == 1
        assert harness["notion"].data_sources == notion_before
        assert list(harness["notion"].events or []) == events_before
        assert harness["state"].acquire_local_worker_lock() is True
        harness["state"].release_local_worker_lock()
        # Service-account composition (no attestor) is unaffected by the same provider sequence.
        assert harness["worker"].run_once()["status"] == "ok"


def _oauth_worker_with(harness: dict[str, Any], drive: Any, attestor: StaticAttestor) -> IntakeWorker:
    return IntakeWorker(
        harness["config"], harness["state"], drive, harness["notion"],
        provider_account_binding_id=provider_binding_id("google_drive", "synthetic-owner", "synthetic-oauth"),
        semester="2026-2", entry_attestor=attestor,
    )


class _CoordinatorSpy:
    workspace = "2026-2"

    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self) -> Any:
        self.calls += 1
        return {}

    def receive(self, snapshot: Any) -> Any:
        return snapshot

    def publish(self, barrier: Any) -> dict[str, Any]:
        return {"status": "ok"}


def test_reconnect_during_derivative_creation_stops_the_tick_before_further_jobs_or_coordinators(tmp_path: Path) -> None:
    from tests.integration.test_intake_worker_preview import _assign_request, _details_request

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]
        rejected = {"on": False, "creates": 0}

        class _RejectingDrive(_CountingDrive):
            def create_file_with_marker(self, *args: Any, **kwargs: Any) -> Any:
                rejected["creates"] += 1
                if rejected["on"]:
                    raise ReconnectRequiredError()  # SDK refresh rejected inside the create
                return inner.create_file_with_marker(*args, **kwargs)
        drive = _RejectingDrive(inner)
        worker = _oauth_worker_with(harness, drive, attestor)
        notion = harness["notion"]
        assert worker.run_once()["status"] == "ok"
        _assign_request(notion).update({"Course": ["synthetic-course-page-0"], "Submitted": True})
        assert worker.run_once()["status"] == "ok"
        _details_request(notion).update({"Course": ["synthetic-course-page-0"], "Kind": "TRANSCRIPT",
                                         "Actual Date": {"start": "2026-09-01", "end": None},
                                         "Session Mode": "NEW", "Submitted": True})
        spy = _CoordinatorSpy()
        worker.request_coordinators.append(spy)
        rejected["on"] = True
        events_before = len(notion.events or [])
        jobs_before = [(job.id, str(job.status)) for job in harness["state"].list_jobs(limit=1000)]
        third = worker.run_once()
        assert third["status"] == "failed" and third["code"] == "RECONNECT_REQUIRED"
        assert rejected["creates"] == 1 and spy.calls == 0, "no derivative retry, no coordinator phase after the rejection"
        jobs_after = [(job.id, str(job.status)) for job in harness["state"].list_jobs(limit=1000)]
        assert [status for _id, status in jobs_after if status in {"FAILED", "DONE", "COMPLETE"}] == \
            [status for _id, status in jobs_before if status in {"FAILED", "DONE", "COMPLETE"}], \
            "the rejection is not recorded as a generic job failure"
        assert all(event[0] != "create" or "derivative" not in str(event) for event in (notion.events or [])[events_before:])
        assert harness["state"].acquire_local_worker_lock() is True
        harness["state"].release_local_worker_lock()


def test_reconnect_during_layout_validation_returns_the_fixed_code(tmp_path: Path) -> None:
    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]
        rejected = {"on": False}

        class _RejectingDrive(_CountingDrive):
            def read_metadata(self, file_id: str) -> Any:
                if rejected["on"]:
                    raise ReconnectRequiredError()
                return inner.read_metadata(file_id)
        drive = _RejectingDrive(inner)
        worker = _oauth_worker_with(harness, drive, attestor)
        assert worker.run_once()["status"] == "ok"
        notion_before = copy.deepcopy(harness["notion"].data_sources)
        events_before = list(harness["notion"].events or [])
        rejected["on"] = True
        result = worker.run_once(sync=False)
        assert result["status"] == "failed" and result["code"] == "RECONNECT_REQUIRED"
        assert harness["notion"].data_sources == notion_before and list(harness["notion"].events or []) == events_before
        assert harness["state"].acquire_local_worker_lock() is True
        harness["state"].release_local_worker_lock()


def test_coordinator_phases_run_inside_the_same_entry_attestation(tmp_path: Path) -> None:
    import contextlib

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]

        class _ContextDrive(_CountingDrive):
            current: WorkerEntryAttestation | None = None

            @contextlib.contextmanager
            def attested(self, attestation):
                previous, self.current = self.current, attestation
                try:
                    yield
                finally:
                    self.current = previous
        drive = _ContextDrive(inner)
        worker = _oauth_worker_with(harness, drive, attestor)
        phases: list[tuple[str, int | None]] = []

        class _PhaseSpy:
            workspace = "2026-2"

            def _observe(self, phase: str) -> None:
                phases.append((phase, drive.current.generation if drive.current else None))

            def snapshot(self):
                self._observe("snapshot"); return {}

            def receive(self, snapshot):
                self._observe("receive"); return snapshot

            def publish(self, barrier):
                self._observe("publish"); self._observe("after_publish"); return {"status": "ok"}
        worker.request_coordinators.append(_PhaseSpy())
        assert worker.run_once()["status"] == "ok"
        assert [name for name, _g in phases] == ["snapshot", "receive", "publish", "after_publish"]
        assert {generation for _n, generation in phases} == {1}, "every coordinator phase ran under the tick's attestation"
        assert drive.current is None  # context released after the entry


def test_adapter_without_attestor_keeps_service_account_behaviour() -> None:
    service = _RecordingService()
    adapter = GoogleDriveWorkerAdapter(service)
    adapter.read_metadata("f1")
    with adapter.attested(None):
        adapter.read_metadata("f1")
    assert service.executed == ["get", "get"]


def test_real_after_publish_callback_runs_inside_the_tick_attestation(tmp_path: Path) -> None:
    """O1 (r9): the real RequestCoordinator.after_publish wiring, not a spy, runs under the entry context."""
    import contextlib

    from uls.intake.composition import merge_request_handler
    from uls.intake.coordinator import RequestCoordinator

    with _system(tmp_path) as harness:
        attestor = StaticAttestor()
        inner = harness["drive"]

        class _ContextDrive(_CountingDrive):
            current: WorkerEntryAttestation | None = None

            @contextlib.contextmanager
            def attested(self, attestation):
                previous, self.current = self.current, attestation
                try:
                    yield
                finally:
                    self.current = previous
        drive = _ContextDrive(inner)
        worker = _oauth_worker_with(harness, drive, attestor)
        observed: list[tuple[str, int | None, bool]] = []

        def after_publish(barrier):
            observed.append(("after_publish", drive.current.generation if drive.current else None,
                             attestor.generation == (drive.current.generation if drive.current else None)))
            return {"applied": 0}

        class _Handler:
            """Minimal RequestHandler that never blocks the barrier."""

            def validate_batch(self, snapshot):
                return SimpleNamespace(epoch=snapshot.epoch, workspace=snapshot.workspace,
                                       blocked_slots=frozenset(), workspace_blocked=False)

            def claim_ordered(self, batch):
                return batch

            def publish_pending(self, barrier):
                return "published"

        coordinator = merge_request_handler(
            worker, workspace="2026-2", name="synthetic", handler=_Handler(),
            sources={"synthetic-empty": lambda _source: []}, after_publish=after_publish,
        )
        assert isinstance(coordinator, RequestCoordinator)
        assert coordinator.after_publish is after_publish
        result = worker.run_once()
        assert result["status"] != "failed", result
        assert observed == [("after_publish", 1, True)], "the real approval callback ran once under the tick's attestation"
        assert drive.current is None  # context released after the entry


def test_service_account_live_composition_without_google_oauth_installs_no_attestor(tmp_path: Path, monkeypatch) -> None:
    """O2 (r9): the non-injected build_intake_worker path with a synthetic SA payload and google_oauth=None."""
    from types import MappingProxyType

    import google.auth
    import googleapiclient.discovery
    import notion_client

    from uls.adapters.notion.intake import NotionAPIWorker
    from uls.config.credentials import GoogleCredentialPayload, ResolvedCredentials

    sa = {"type": "service_account", "client_email": "synthetic@example.iam", "private_key_id": "k",
          "private_key": "synthetic-key", "project_id": "p", "client_id": "synthetic-sa-client"}
    loaded: list[Any] = []

    def load(info, scopes=None, **kwargs):
        assert dict(info) == sa and scopes == ["https://www.googleapis.com/auth/drive"]
        credentials = SimpleNamespace(info=info, scopes=scopes, client_id=info["client_id"], refreshes=0)
        loaded.append(credentials)
        return credentials, None
    monkeypatch.setattr(google.auth, "load_credentials_from_dict", load)

    class _LiveService(_RecordingService):
        about_calls = 0

        def about(self) -> Any:
            service = self

            class _About:
                def get(self, fields: str) -> Any:
                    assert fields == "user(permissionId)"
                    service.about_calls += 1
                    return SimpleNamespace(execute=lambda: {"user": {"permissionId": "synthetic-owner"}})
            return _About()
    built: list[_LiveService] = []

    def build(name, version, credentials, cache_discovery):
        assert (name, version, cache_discovery) == ("drive", "v3", False)
        service = _LiveService()
        service.credentials = credentials
        built.append(service)
        return service
    monkeypatch.setattr(googleapiclient.discovery, "build", build)

    clients: list[Any] = []

    class _Client:
        def __init__(self, *, auth: str, notion_version: str, timeout_ms: int) -> None:
            assert (auth, notion_version, timeout_ms) == ("synthetic-notion-token", "2025-09-03", 20_000)
            self.data_sources = SimpleNamespace(query=lambda **kw: {"results": [], "has_more": False})
            clients.append(self)
    monkeypatch.setattr(notion_client, "Client", _Client)

    with _system(tmp_path) as harness:
        config = harness["config"]
        assert config.google_oauth is None
        credentials = ResolvedCredentials(
            {"GOOGLE_WORKER_CREDENTIALS_FILE": "synthetic-path", "NOTION_WORKER_TOKEN": "synthetic-notion-token"},
            {"GOOGLE_WORKER_CREDENTIALS_FILE": GoogleCredentialPayload(
                info=MappingProxyType(sa), source_name="GOOGLE_WORKER_CREDENTIALS_FILE")},
        )
        worker = build_intake_worker(config, credentials, state=harness["state"], semester="2026-2")

        assert worker.entry_attestor is None
        assert len(loaded) == 1 and len(built) == 1 and len(clients) == 1
        assert built[0].about_calls == 1 and loaded[0].refreshes == 0
        assert worker.provider_account_binding_id == provider_binding_id(
            "google_drive", "synthetic-owner", "synthetic-sa-client")
        assert isinstance(worker.drive, GoogleDriveWorkerAdapter) and worker.drive._attestor is None
        assert isinstance(worker.notion.backend, NotionAPIWorker) and worker.notion.backend.client is clients[0]
        # Service-account adapters answer provider calls with no attestation context at all.
        worker.drive.read_metadata("f1")
        assert built[0].executed == ["get"]
        # Both installers ran under the (absent) startup context: with no C5 mappings and
        # study notes disabled neither publishes a READY extension.
        assert worker.request_extension_readiness.get("2026-2", {}).get("status") != "READY"
        assert worker.request_coordinators == []


def test_native_download_bound_is_enforced_before_an_oversized_chunk_is_buffered(monkeypatch) -> None:
    """P-B1 r3 O1: a single oversized response, multi-chunk growth past the bound and a short
    response are all refused; nothing beyond the bound is ever buffered."""

    from uls.domain.errors import SourcePartialError, SourceUnavailableError

    attestor = StaticAttestor()
    service = _MutationService()
    adapter = GoogleDriveWorkerAdapter(service, attestor=attestor, max_bytes=1_000)
    service._record = lambda name, kwargs: {**_RecordingService._record(service, name, kwargs), "size": "40"}  # type: ignore[method-assign]
    import googleapiclient.http

    def downloader_with(chunks: list[bytes]):
        sinks: list[Any] = []

        class _Downloader:
            def __init__(self, output: Any, request: Any, chunksize: int) -> None:
                self.output = output
                self.pending = list(chunks)
                sinks.append(output)

            def next_chunk(self, num_retries: int = 0) -> tuple[None, bool]:
                self.output.write(self.pending.pop(0))
                return None, not self.pending

        return _Downloader, sinks

    # One oversized chunk: refused at the write, buffer never exceeds the bound.
    downloader, sinks = downloader_with([b"x" * 41])
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", downloader)
    with adapter.attested(attestor.attest_entry()), pytest.raises(SourcePartialError):
        adapter.download("f1", max_bytes=40)
    assert sinks[0].tell() == 0
    # Several chunks that cross the bound at the third write: the third is refused.
    downloader, sinks = downloader_with([b"x" * 20, b"x" * 20, b"x" * 1])
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", downloader)
    with adapter.attested(attestor.attest_entry()), pytest.raises(SourcePartialError):
        adapter.download("f1", max_bytes=40)
    assert sinks[0].tell() == 40
    # A short response (fewer bytes than declared) is not a complete download.
    downloader, _ = downloader_with([b"x" * 39])
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", downloader)
    with adapter.attested(attestor.attest_entry()), pytest.raises(SourceUnavailableError):
        adapter.download("f1", max_bytes=40)
    # An exact match within the bound succeeds; the caller bound never widens the adapter ceiling.
    downloader, _ = downloader_with([b"x" * 20, b"x" * 20])
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", downloader)
    with adapter.attested(attestor.attest_entry()):
        assert adapter.download("f1", max_bytes=40) == b"x" * 40
    with adapter.attested(attestor.attest_entry()), pytest.raises(SourcePartialError):
        adapter.download("f1", max_bytes=10)  # declared 40 > caller bound 10: refused before reading
