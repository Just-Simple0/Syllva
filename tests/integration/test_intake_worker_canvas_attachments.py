"""P-E: worker integration of the Canvas attachment download path (plan §6.2 r15, pe-invariants)."""
from __future__ import annotations

import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _enable
from tests.integration.test_intake_worker_classification_pb2a import COURSE
from tests.integration.test_intake_worker_preview import _pdf_bytes, _system
from tests.unit.test_canvas_attachments import FakeFiles

from uls.intake.canvas_attachments import AttachmentCandidate, CanvasAttachmentFetcher

pytestmark = pytest.mark.integration

ORIGIN = "https://canvas.example.edu"
CANVAS_COURSE = 67535


@pytest.fixture(autouse=True)
def _no_promotion(monkeypatch):
    # AUTO execution is P-B2b's subject; here only download -> bind -> discover -> classify.
    from uls.intake.worker import IntakeWorker

    monkeypatch.setattr(IntakeWorker, "_promote_ready_plans", lambda self: None)


class _Observer:
    """A verified Canvas profile whose COMPLETE observation carries attachment candidates."""

    def __init__(self, attachments) -> None:
        self.attachments = attachments
        self.calls = 0

    def observe_course(self, course_id: int):
        self.calls += 1
        return SimpleNamespace(status="COMPLETE", course_key=COURSE, attachments=tuple(self.attachments))

    def profile_matches(self) -> bool:
        return True

    def principal_matches(self) -> bool:
        return True

    def current_proof(self, _course_key: str) -> str | None:
        return "test-proof"


def _wire(system, files, attachments, *, downloads: bool = True):
    worker = _enable(system)
    cfg = system["config"].intake.classification
    cfg.canvas_course_map = {CANVAS_COURSE: COURSE}
    cfg.canvas_observation_enabled, cfg.canvas_origin = True, ORIGIN
    cfg.canvas_attachment_download_enabled = downloads
    reader = FakeFiles(files)

    @contextmanager
    def factory():
        yield reader

    worker.canvas_observer = _Observer(attachments)
    worker.canvas_attachment_fetcher = CanvasAttachmentFetcher(
        system["state"], system["drive"], origin=ORIGIN, reader_factory=factory, max_bytes=1_000_000)
    return worker, reader


def _observe_module_week(state, week: int) -> None:
    state.record_canvas_observation(
        origin=ORIGIN, canvas_course_id=CANVAS_COURSE, resource_kind="module_item", resource_id="1003",
        observation_revision=1, title="Chapter 03-Address.pdf", module_name=f"{week}주차", module_week=week,
        item_type="File", updated_at=None, collection_complete=True)


def test_a_module_file_is_downloaded_bound_and_discovered_as_a_professor_source(tmp_path: Path) -> None:
    pdf = _pdf_bytes()
    with _system(tmp_path, name="unrelated.md") as system:
        worker, reader = _wire(system, {"31": ("Chapter 03-Address.pdf", pdf)},
                               [AttachmentCandidate("module_item", "1003", 1, "31")])
        _observe_module_week(system["state"], 3)
        result = worker.run_once()
        assert result["status"] == "ok", result
        assert result["readiness"]["classification"]["canvas_attachments"]["downloaded"] == 1
        state = system["state"]
        [created] = [e[1] for e in system["drive"].events if e[0] == "create" and e[3] == "Chapter 03-Address.pdf"]
        binding = state.get_canvas_drive_binding(created)
        assert binding is not None and binding["byte_sha256"] == hashlib.sha256(pdf).hexdigest()
        item = state.get_intake_item_by_provider_file("google_drive", created)
        assert item is not None
        assert (item.origin, item.inferred_course_key, item.inferred_week) == ("PROFESSOR_SOURCE", COURSE, 3)
        worker.run_once()                                   # re-observation: nothing new
        assert len([e for e in system["drive"].events if e[0] == "create"]) == 1
        assert reader.download_calls == ["31"]


def test_the_fetch_step_writes_nothing_to_notion(tmp_path: Path) -> None:
    with _system(tmp_path, name="unrelated.md") as system:
        worker, _reader = _wire(system, {"11": ("exam.pdf", _pdf_bytes())},
                                [AttachmentCandidate("announcement", "900", 1, "11", "exam.pdf",
                                                     len(_pdf_bytes()), "application/pdf")])
        before = copy.deepcopy(system["notion"].data_sources)
        worker.run_once(sync=False, process=False)
        assert system["notion"].data_sources == before
        assert len([e for e in system["drive"].events if e[0] == "create"]) == 1


def test_an_unbound_upload_never_reaches_intake(tmp_path: Path) -> None:
    pdf = _pdf_bytes()
    with _system(tmp_path, name="unrelated.md") as system:
        worker, _reader = _wire(system, {"11": ("exam.pdf", pdf)},
                                [AttachmentCandidate("announcement", "900", 1, "11", "exam.pdf", len(pdf),
                                                     "application/pdf")])
        system["drive"].drop_next_create_response = True          # the upload lands, its response is lost
        worker.canvas_attachment_fetcher, fetcher = None, worker.canvas_attachment_fetcher
        worker.run_once()                                         # no fetcher this tick: nothing uploaded
        worker.canvas_attachment_fetcher = fetcher
        worker.run_once(sync=False, process=False)                # upload; response lost; no binding
        [created] = [e[1] for e in system["drive"].events if e[0] == "create"]
        assert system["state"].get_canvas_drive_binding(created) is None
        worker.canvas_attachment_fetcher = None
        worker.run_once()                                         # discovery without the fetcher
        assert system["state"].get_intake_item_by_provider_file("google_drive", created) is None
        worker.canvas_attachment_fetcher = fetcher
        worker.run_once()                                         # recovery binds it, then discovery sees it
        item = system["state"].get_intake_item_by_provider_file("google_drive", created)
        assert item is not None and item.origin == "PROFESSOR_SOURCE"
        assert len([e for e in system["drive"].events if e[0] == "create"]) == 1


def test_downloads_off_means_no_canvas_file_request(tmp_path: Path) -> None:
    with _system(tmp_path, name="unrelated.md") as system:
        worker, reader = _wire(system, {"11": ("exam.pdf", b"%PDF")},
                               [AttachmentCandidate("announcement", "900", 1, "11", "exam.pdf", 4, "application/pdf")],
                               downloads=False)
        worker.run_once()
        assert reader.metadata_calls == [] and [e for e in system["drive"].events if e[0] == "create"] == []


def test_a_binding_of_another_canvas_origin_is_no_provenance(tmp_path: Path) -> None:
    """R-PE2-04: after the Canvas origin changes, an old binding gives no PROFESSOR_SOURCE/course/week."""
    pdf = _pdf_bytes()
    with _system(tmp_path, name="unrelated.md") as system:
        worker, _reader = _wire(system, {"31": ("Chapter 03-Address.pdf", pdf)},
                                [AttachmentCandidate("module_item", "1003", 1, "31")])
        _observe_module_week(system["state"], 3)
        worker.run_once(sync=False, process=False)
        [created] = [e[1] for e in system["drive"].events if e[0] == "create"]
        system["config"].intake.classification.canvas_origin = "https://other.example.edu"
        worker.canvas_attachment_fetcher = None
        worker.run_once()
        item = system["state"].get_intake_item_by_provider_file("google_drive", created)
        assert item is not None and item.origin != "PROFESSOR_SOURCE"
        assert (item.inferred_course_key, item.inferred_week) == (None, None)


def test_readiness_has_every_attachment_counter_before_any_fetch(tmp_path: Path) -> None:
    """R-PEI-03: the counters exist (zero) even with downloads off and no fetch yet."""
    with _system(tmp_path, name="unrelated.md") as system:
        worker = _enable(system)
        block = worker._classification_readiness()["canvas_attachments"]
        assert block["status"] == "DISABLED"
        assert {k: block[k] for k in ("downloaded", "recovered", "unchanged", "skipped", "failed", "last_codes")} == {
            "downloaded": 0, "recovered": 0, "unchanged": 0, "skipped": 0, "failed": 0, "last_codes": {}}
