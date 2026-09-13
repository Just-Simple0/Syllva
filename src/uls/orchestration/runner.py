"""One bounded scheduler tick using the same core command on both platforms."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from uls.domain.enums import JobStatus
from uls.domain.errors import PolicyDeniedError, ProviderRateLimitedError, ProviderUnavailableError
from uls.orchestration.retry import next_backoff, should_retry


class WorkerRunner:
    def __init__(self, state: Any, handlers: Mapping[str, Callable[[Any], Any]], *,
                 discover: Callable[[], int] | None = None,
                 sleep: Callable[[float], Any] = time.sleep) -> None:
        self.state, self.handlers = state, dict(handlers)
        self.discover, self.sleep = discover, sleep

    def run_once(self, *, sync: bool = True, process: bool = True,
                 max_jobs: int = 100) -> dict[str, Any]:
        if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
            raise ValueError('max_jobs must be 1–1000')
        if not self.state.acquire_local_worker_lock():
            return {'status': 'already_running', 'processed': 0, 'discovered': 0}
        try:
            discovered = self.discover() if sync and self.discover is not None else 0
            processed = failed = partial = needs_review = 0
            for _ in range(max_jobs if process else 0):
                job = self.state.claim_job()
                if job is None:
                    break
                handler = self.handlers.get(job.operation)
                if handler is None:
                    self.state.transition_job(job.id, JobStatus.NEEDS_REVIEW,
                                              error_class='PERMANENT', last_error='No handler for operation')
                    needs_review += 1
                    continue
                try:
                    result = handler(job)
                    current = self.state.get_job(job.id)
                    if current.status == JobStatus.PROCESSING:
                        status = getattr(result, 'status', result)
                        if status not in {JobStatus.READY, JobStatus.PARTIAL, JobStatus.NEEDS_REVIEW}:
                            raise ValueError('Handler did not return an explicit terminal status')
                        self.state.complete_job(job.id, status=status)
                        current = self.state.get_job(job.id)
                    if current.status not in {JobStatus.READY, JobStatus.PARTIAL, JobStatus.NEEDS_REVIEW}:
                        raise ValueError('Handler left an invalid terminal status')
                    processed += 1
                    partial += current.status == JobStatus.PARTIAL
                    needs_review += current.status == JobStatus.NEEDS_REVIEW
                except Exception as exc:  # noqa: BLE001 - persist safe failure state and release worker lock
                    error_class = ('POLICY_DENIED' if isinstance(exc, PolicyDeniedError) else
                                   'RATE_LIMITED' if isinstance(exc, ProviderRateLimitedError) else
                                   'TRANSIENT' if isinstance(exc, ProviderUnavailableError) else 'PERMANENT')
                    current = self.state.get_job(job.id)
                    if current.status == JobStatus.PROCESSING:
                        self.state.fail_job(job.id, error_class=error_class,
                                            last_error='Worker operation failed; inspect source and configuration')
                    if current.status not in {JobStatus.READY, JobStatus.PARTIAL} and should_retry(error_class, job.attempt_count):
                        self.sleep(next_backoff(job.attempt_count))
                        self.state.requeue_job(job.id, error_class, last_error='Retryable provider failure')
                    else:
                        failed += 1
            return {'status': 'failed' if failed else 'needs_review' if needs_review else 'ok',
                    'discovered': discovered, 'processed': processed, 'partial': partial,
                    'failed': failed, 'needs_review': needs_review}
        finally:
            self.state.release_local_worker_lock()
