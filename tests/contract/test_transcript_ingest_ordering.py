import pathlib
import sys
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fixtures"))

from fake_drive import FakeDriveWriter
from fake_notion import FakeNotionReader, FakeNotionWriter
from uls.domain.enums import ProcessingStatus
from uls.domain.errors import SourceUnavailableError
from uls.domain.source_ref import SourceRef
from uls.ingestion.transcript_ingest import ingest_transcript
from uls.orchestration.jobs import derive_job_key
from uls.state.sqlite import SQLiteStateStore


class RecordingState:
    def __init__(self, store: SQLiteStateStore, events: list[object]) -> None:
        self.store = store
        self.events = events
        self.allocation_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def register_source_file(self, *args, **kwargs):
        self.events.append(("source_file", args, kwargs))
        return self.store.register_source_file(*args, **kwargs)

    def register_source_version(self, *args, **kwargs):
        self.events.append(("source_version", args, kwargs))
        return self.store.register_source_version(*args, **kwargs)

    def create_job(self, *args, **kwargs):
        job = self.store.create_job(*args, **kwargs)
        self.events.append("processing")
        return job

    def allocate_entity(self, *args, **kwargs):
        self.allocation_calls.append((args, kwargs))
        return self.store.allocate_entity(*args, **kwargs)

    def create_processing_record(self, *args, **kwargs):
        self.events.append("processing_record")
        return self.store.create_processing_record(*args, **kwargs)

    def complete_job(self, *args, **kwargs):
        result = self.store.complete_job(*args, **kwargs)
        self.events.append("ready" if result.status is ProcessingStatus.READY else "partial")
        return result

    def fail_job(self, *args, **kwargs):
        return self.store.fail_job(*args, **kwargs)


class IncompleteState:
    """Expose a complete SQLite store except for one required method."""

    def __init__(self, store: SQLiteStateStore, missing: str) -> None:
        self.store = store
        self.missing = missing

    def __getattr__(self, name: str):
        if name == self.missing:
            raise AttributeError(name)
        return getattr(self.store, name)


class NullAllocationState:
    def __init__(self, store: SQLiteStateStore) -> None:
        self.store = store

    def allocate_entity(self, *args, **kwargs):
        return None

    def __getattr__(self, name: str):
        return getattr(self.store, name)


class MismatchedAllocationState:
    def __init__(self, store: SQLiteStateStore) -> None:
        self.store = store

    def allocate_entity(self, *args, **kwargs):
        return "COMP319-S06"

    def __getattr__(self, name: str):
        return getattr(self.store, name)


class NonProcessingJobState:
    """Return an unusable job and make all recovery transitions ineffective."""

    def __init__(
        self,
        store: SQLiteStateStore,
        *,
        return_none: bool = False,
        reported_status: str | None = None,
    ) -> None:
        self.store = store
        self.return_none = return_none
        self.reported_status = reported_status

    def create_job(self, *args, **kwargs):
        values = dict(kwargs)
        values["status"] = ProcessingStatus.PENDING.value
        job = self.store.create_job(*args, **values)
        if self.return_none:
            return None
        return {"id": job.id, "status": self.reported_status}

    def transition_job(self, *args, **kwargs):
        return None

    def claim_job(self, *args, **kwargs):
        return None

    def __getattr__(self, name: str):
        return getattr(self.store, name)


class OrderedDrive(FakeDriveWriter):
    def write_staged_derived(self, source_ref, content):
        result = super().write_staged_derived(source_ref, content)
        self.events.append("stage")
        return result


class NonAtomicOnlyDrive:
    """A deliberately invalid writer used to prove no write fallback exists."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_derived_file(self, *args, **kwargs):
        self.calls.append("write_derived_file")

    def publish_derived(self, *args, **kwargs):
        self.calls.append("publish_derived")


class StageFailureDrive(FakeDriveWriter):
    def write_staged_derived(self, source_ref, content):
        self.events.append("stage_attempt")
        raise RuntimeError("staged derivative write failed")


class ValidationFailureDrive(OrderedDrive):
    def validate_derived(self, staged_ref):
        self.events.append("validate")
        raise RuntimeError("staged derivative validation failed")

    def replace_derived_file_atomically(self, staged_ref):
        result = super().replace_derived_file_atomically(staged_ref)
        self.events.append("publish")
        return result


def _run(raw: str):
    events: list[object] = []
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader, events=events)
    drive = OrderedDrive(events=events)
    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        state = RecordingState(store, events)
        result = ingest_transcript(
            raw,
            entity_id="COMP319-S01",
            course_key="2026-1_COMP319-002",
            source_ref=SourceRef("google_drive", "transcript-05"),
            source_hash="transcript-hash-v1",
            source_version=1,
            processor_version="1.2.0",
            state_store=state,
            drive_writer=drive,
            notion_writer=notion,
            now="2026-09-04T00:00:00+09:00",
        )
        job = store.get_job(result.job.id)
        record = store.find_processed_source(source_file_id="transcript-05", source_hash="transcript-hash-v1")
        source_version_row = store.connection.execute(
            """
            SELECT source_file_id, source_hash, version, canonical_entity_id,
                   source_ref_json, processor_version
            FROM source_versions
            WHERE source_file_id = ? AND source_hash = ?
            """,
            ("transcript-05", "transcript-hash-v1"),
        ).fetchone()
        assert len(state.allocation_calls) == 1
        store.close()
    return result, events, job, record, notion_reader, source_version_row


def _event_names(events: list[object]) -> list[str]:
    names: list[str] = []
    for event in events:
        candidate: str | None = None
        if isinstance(event, str):
            candidate = event
        elif isinstance(event, tuple) and event:
            if event[0] in {"stage", "validate", "publish"}:
                candidate = str(event[0])
            elif event[0] == "notion":
                candidate = "notion"
        if candidate is not None and (not names or names[-1] != candidate):
            names.append(candidate)
    return names


@pytest.mark.parametrize("missing", ["state_store", "notion_writer"])
def test_missing_commit_participant_fails_before_derivative_publish(missing: str) -> None:
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    drive = FakeDriveWriter()
    store = None

    try:
        if missing != "state_store":
            with TemporaryDirectory() as directory:
                store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
                with pytest.raises(ValueError):
                    ingest_transcript(
                        "[00:00:01] transcript",
                        entity_id="COMP319-S05",
                        course_key="2026-1_COMP319-002",
                        source_ref=SourceRef("google_drive", "transcript-missing-participant"),
                        source_hash="transcript-hash-missing-participant",
                        source_version=1,
                        processor_version="1.2.0",
                        state_store=store,
                        drive_writer=drive,
                        notion_writer=None,
                        now="2026-09-04T00:00:00+09:00",
                    )
                assert drive.events == []
                assert drive.derived == {}
                store.close()
                store = None
        else:
            with pytest.raises(ValueError):
                ingest_transcript(
                    "[00:00:01] transcript",
                    entity_id="COMP319-S05",
                    course_key="2026-1_COMP319-002",
                    source_ref=SourceRef("google_drive", "transcript-missing-participant"),
                    source_hash="transcript-hash-missing-participant",
                    source_version=1,
                    processor_version="1.2.0",
                    state_store=None,
                    drive_writer=drive,
                    notion_writer=notion,
                    now="2026-09-04T00:00:00+09:00",
                )
            assert drive.events == []
            assert drive.derived == {}
    finally:
        if store is not None:
            store.close()


def test_commit_order_ready_is_last() -> None:
    result, events, job, record, notion_reader, source_version_row = _run(
        "[00:00:01] 정상 transcript"
    )

    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "processing",
        "source_file",
        "source_version",
        "stage",
        "stage",
        "validate",
        "publish",
        "notion",
        "processing_record",
        "ready",
    ]
    assert _event_names(events) == [
        "processing",
        "stage",
        "validate",
        "publish",
        "notion",
        "processing_record",
        "ready",
    ]
    assert result.status is ProcessingStatus.READY
    assert job.status is ProcessingStatus.READY
    assert record is not None and record.status is ProcessingStatus.READY
    assert notion_reader.sessions["COMP319-S01"]["Recording Status"] == "Ready"
    source_file_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "source_file"
    )
    assert source_file_event[2]["canonical_entity_id"] is None
    assert source_version_row is not None
    assert source_version_row["source_file_id"] == "transcript-05"
    assert source_version_row["source_hash"] == "transcript-hash-v1"
    assert source_version_row["version"] == 1
    assert source_version_row["canonical_entity_id"] == result.entity_id
    assert source_version_row["processor_version"] == "1.2.0"


@pytest.mark.parametrize("missing", ["register_source_file", "register_source_version"])
def test_incomplete_state_store_fails_before_derivative_publish(missing: str) -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        state = IncompleteState(store, missing)
        with pytest.raises(ValueError, match=missing):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S05",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", "transcript-incomplete-state"),
                source_hash="transcript-hash-incomplete-state",
                source_version=1,
                processor_version="1.2.0",
                state_store=state,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        store.close()

    assert drive.events == []
    assert drive.derived == {}


@pytest.mark.parametrize("entity_id", [None, "COMP319-S05"])
@pytest.mark.parametrize("allocation_mode", ["missing", "none"])
def test_allocator_gate_is_required_for_both_entity_id_paths(
    entity_id: str | None,
    allocation_mode: str,
) -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        state = (
            IncompleteState(store, "allocate_entity")
            if allocation_mode == "missing"
            else NullAllocationState(store)
        )
        with pytest.raises(ValueError, match="allocate_entity"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id=entity_id,
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", "transcript-allocator-gate"),
                source_hash="transcript-hash-allocator-gate",
                source_version=1,
                processor_version="1.2.0",
                state_store=state,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        assert drive.events == []
        assert drive.derived == {}
        store.close()


def test_caller_entity_id_must_match_allocator_result() -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_file_id = "transcript-allocator-mismatch"
    source_hash = "transcript-hash-allocator-mismatch"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        state = MismatchedAllocationState(store)
        with pytest.raises(ValueError, match="does not match"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S05",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=state,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.FAILED
        store.close()

    assert drive.events == []
    assert drive.derived == {}


def test_wrong_caller_entity_id_cannot_seed_source_canonical_binding() -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_file_id = "transcript-canonical-binding"
    source_hash = "transcript-hash-canonical-binding"
    requested_entity_id = "COMP319-S99"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        with pytest.raises(ValueError, match="does not match"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id=requested_entity_id,
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=store,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )

        source_row = store.connection.execute(
            "SELECT canonical_entity_id FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
        assert source_row is not None
        assert source_row["canonical_entity_id"] == "COMP319-S01"
        assert source_row["canonical_entity_id"] != requested_entity_id
        assert store.connection.execute(
            "SELECT COUNT(*) FROM source_versions WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0] == 0
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.FAILED
        store.close()

    assert drive.events == []
    assert drive.derived == {}


def test_allocator_binds_source_and_durable_version_without_caller_entity_id() -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_file_id = "transcript-allocator-owned"
    source_hash = "transcript-hash-allocator-owned"

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        result = ingest_transcript(
            "[00:00:01] transcript",
            entity_id=None,
            course_key="2026-1_COMP319-002",
            source_ref=SourceRef("google_drive", source_file_id),
            source_hash=source_hash,
            source_version=1,
            processor_version="1.2.0",
            state_store=store,
            drive_writer=drive,
            notion_writer=notion,
            now="2026-09-04T00:00:00+09:00",
        )

        source = store.get_source_file(source_file_id)
        versions = store.find_source_versions(
            source_file_id=source_file_id,
            source_hash=source_hash,
        )
        assert result.status is ProcessingStatus.READY
        assert result.entity_id == "COMP319-S01"
        assert source is not None and source.canonical_entity_id == result.entity_id
        assert len(versions) == 1
        assert versions[0].canonical_entity_id == result.entity_id
        assert store.connection.execute(
            "SELECT next_sequence FROM entity_allocations WHERE course_key = ? AND entity_type = ?",
            ("2026-1_COMP319-002", "S"),
        ).fetchone()[0] == 2
        store.close()


def test_bound_source_reprocessing_reuses_existing_canonical_entity_id() -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_file_id = "transcript-idempotent-reprocess"
    source_hash = "transcript-hash-idempotent-reprocess"

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        first = ingest_transcript(
            "[00:00:01] first transcript",
            entity_id=None,
            course_key="2026-1_COMP319-002",
            source_ref=SourceRef("google_drive", source_file_id),
            source_hash=source_hash,
            source_version=1,
            processor_version="1.2.0",
            state_store=store,
            drive_writer=drive,
            notion_writer=notion,
            now="2026-09-04T00:00:00+09:00",
        )
        second = ingest_transcript(
            "[00:00:01] first transcript",
            entity_id=None,
            course_key="2026-1_COMP319-002",
            source_ref=SourceRef("google_drive", source_file_id),
            source_hash=source_hash,
            source_version=1,
            processor_version="1.2.0",
            state_store=store,
            drive_writer=drive,
            notion_writer=notion,
            now="2026-09-04T00:00:00+09:00",
        )

        source = store.get_source_file(source_file_id)
        assert first.entity_id == second.entity_id == "COMP319-S01"
        assert source is not None and source.canonical_entity_id == first.entity_id
        assert store.connection.execute(
            "SELECT next_sequence FROM entity_allocations WHERE course_key = ? AND entity_type = ?",
            ("2026-1_COMP319-002", "S"),
        ).fetchone()[0] == 2
        assert len(store.find_source_versions(source_file_id=source_file_id)) == 1
        store.close()


@pytest.mark.parametrize(
    ("return_none", "reported_status"),
    [
        (True, None),
        (False, None),
        (False, ProcessingStatus.PENDING.value),
    ],
)
def test_non_processing_created_job_cannot_enter_stage_or_publish(
    return_none: bool,
    reported_status: str | None,
) -> None:
    drive = FakeDriveWriter()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_file_id = "transcript-non-processing-job"
    source_hash = f"transcript-hash-non-processing-{return_none}-{reported_status}"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        state = NonProcessingJobState(
            store,
            return_none=return_none,
            reported_status=reported_status,
        )
        with pytest.raises(SourceUnavailableError, match="PROCESSING"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S01",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=state,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.PENDING
        store.close()

    assert drive.events == []
    assert drive.derived == {}


def test_partial_is_consistent_across_job_notion_and_derivative() -> None:
    result, events, job, record, notion_reader, _ = _run("[00:61:01] malformed timestamp")

    assert events[-1] == "partial"
    assert result.status is ProcessingStatus.PARTIAL
    assert result.transcript.front_matter.status.value == "partial"
    assert job.status is ProcessingStatus.PARTIAL
    assert record is not None and record.status is ProcessingStatus.PARTIAL
    assert notion_reader.sessions["COMP319-S01"]["Recording Status"] == "Partial"


def test_ingest_session_patch_contains_only_schema_14_2_fields() -> None:
    _, _, _, _, notion_reader, _ = _run("[00:00:01] 정상 transcript")

    session = notion_reader.sessions["COMP319-S01"]
    assert "Text Status" not in session
    assert "Source Hash" not in session
    assert "Source Version" not in session


def test_fake_notion_rejects_material_fields_on_a_session() -> None:
    reader = FakeNotionReader()
    writer = FakeNotionWriter(reader)

    with pytest.raises(ValueError):
        writer.update_session_metadata("COMP319-S05", {"Text Status": "Ready"})


def test_non_atomic_writer_is_rejected_without_invoking_fallbacks() -> None:
    drive = NonAtomicOnlyDrive()
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader)
    source_hash = "transcript-hash-v1"
    source_file_id = "transcript-05"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        with pytest.raises(SourceUnavailableError):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S01",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=store,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.FAILED
        store.close()

    assert drive.calls == []


def test_staged_validation_failure_marks_job_failed() -> None:
    events: list[object] = []
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader, events=events)
    drive = ValidationFailureDrive(events=events)
    source_hash = "transcript-hash-validation-failure"
    source_file_id = "transcript-validation-failure"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        with pytest.raises(RuntimeError, match="validation failed"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S01",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=store,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.FAILED
        store.close()

    assert _event_names(events)[:2] == ["stage", "validate"]
    assert "publish" not in events


def test_staged_write_failure_marks_job_failed() -> None:
    events: list[object] = []
    notion_reader = FakeNotionReader()
    notion = FakeNotionWriter(notion_reader, events=events)
    drive = StageFailureDrive(events=events)
    source_hash = "transcript-hash-stage-failure"
    source_file_id = "transcript-stage-failure"
    job_key = derive_job_key(source_file_id, source_hash, "TRANSCRIPT_INGEST", "1.2.0")

    with TemporaryDirectory() as directory:
        store = SQLiteStateStore(pathlib.Path(directory) / "state.sqlite")
        with pytest.raises(RuntimeError, match="staged derivative write failed"):
            ingest_transcript(
                "[00:00:01] transcript",
                entity_id="COMP319-S01",
                course_key="2026-1_COMP319-002",
                source_ref=SourceRef("google_drive", source_file_id),
                source_hash=source_hash,
                source_version=1,
                processor_version="1.2.0",
                state_store=store,
                drive_writer=drive,
                notion_writer=notion,
                now="2026-09-04T00:00:00+09:00",
            )
        job = store.get_job(job_key=job_key)
        assert job is not None and job.status is ProcessingStatus.FAILED
        store.close()

    assert events == ["stage_attempt"]
