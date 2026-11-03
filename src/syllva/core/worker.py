"""The single intake worker of a Core process (spec §11.2, "Single-active-worker").

One daemon thread runs ``LocalIntake.recover()`` once and then ``run_pending()``
whenever it is kicked (after an upload, a confirmation or a replacement) or every
``idle_s`` seconds. The database lock (spec §2.4) guarantees no other process
runs a worker on the same library.
"""

from __future__ import annotations

import threading

from syllva.core.logging import log_event
from syllva.intake.v2 import LocalIntake


class IntakeWorker:
    def __init__(self, intake: LocalIntake, *, idle_s: float = 30.0) -> None:
        self._intake = intake
        self._idle = idle_s
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="intake-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def kick(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=10)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            recovered = self._intake.recover()
            if recovered:
                log_event("INTAKE_RECOVERED", count=recovered)
        except Exception as exc:  # noqa: BLE001 - keep the worker alive; the cause is logged by class
            log_event("INTAKE_WORKER_ERROR", error=type(exc).__name__)
        while not self._stop.is_set():
            try:
                done = self._intake.run_pending()
                if done:
                    log_event("INTAKE_PROCESSED", count=len(done))
            except Exception as exc:  # noqa: BLE001
                log_event("INTAKE_WORKER_ERROR", error=type(exc).__name__)
            if self._wake.wait(self._idle):
                self._wake.clear()  # cleared before the next pass, so a kick during the pass is not lost
