import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.domain.enums import JobStatus
from uls.orchestration.retry import DEFAULT_MAX_ATTEMPTS, ErrorClass
from uls.state.sqlite import SQLiteStateStore


COURSE_KEY = "2026-1_COMP319-002"


def _create(store, source_id: str):
    return store.create_job(
        operation="normalize",
        stage="normalization",
        source_file_id=source_id,
        source_hash=f"hash-{source_id}",
        processor_version="1.2.0",
    )


def test_create_claim_transition_complete_and_fail(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    ready = _create(store, "ready-source")
    claimed = store.claim_job(ready.id)
    assert claimed is not None
    assert claimed.status is JobStatus.PROCESSING
    assert claimed.attempt_count == 1
    completed = store.complete_job(ready.id)
    assert completed.status is JobStatus.READY

    failed = _create(store, "failed-source")
    assert store.claim_job(failed.id).status is JobStatus.PROCESSING
    result = store.fail_job(failed.id, ErrorClass.PERMANENT, "provider rejected input")
    assert result.status is JobStatus.FAILED
    assert store.claim_job(failed.id) is None
    store.close()


def test_processing_and_failed_jobs_can_requeue_only_when_retryable(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    processing = _create(store, "processing-source")
    assert store.claim_job(processing.id) is not None
    requeued = store.requeue_job(processing.id, ErrorClass.TRANSIENT)
    assert requeued.status is JobStatus.PENDING
    assert requeued.attempt_count == 1

    failed = _create(store, "retry-source")
    assert store.claim_job(failed.id) is not None
    store.fail_job(failed.id, ErrorClass.TRANSIENT)
    requeued_failed = store.requeue_job(failed.id, ErrorClass.RATE_LIMITED)
    assert requeued_failed.status is JobStatus.PENDING

    permanent = _create(store, "permanent-source")
    assert store.claim_job(permanent.id) is not None
    closed = store.requeue_job(permanent.id, ErrorClass.POLICY_DENIED)
    assert closed.status is JobStatus.FAILED
    store.close()


def test_retry_budget_keeps_job_failed_after_default_max_attempts(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite")
    job = _create(store, "bounded-source")
    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        claimed = store.claim_job(job.id)
        assert claimed is not None
        assert claimed.attempt_count == attempt + 1
        job = store.fail_job(job.id, ErrorClass.TRANSIENT)
        job = store.requeue_job(job.id, ErrorClass.TRANSIENT)
        if attempt < DEFAULT_MAX_ATTEMPTS - 1:
            assert job.status is JobStatus.PENDING
        else:
            assert job.status is JobStatus.FAILED
    assert store.claim_job(job.id) is None
    store.close()
