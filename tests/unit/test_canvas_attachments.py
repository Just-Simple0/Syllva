"""P-E: Canvas attachment download -> private Drive -> canvas_drive_bindings (plan §6.2 r15)."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest

from uls.adapters.drive.worker import InMemoryDriveWorker
from uls.domain.errors import ProviderUnavailableError
from uls.intake.canvas_attachments import (
    AttachmentCandidate,
    CanvasAttachmentFetcher,
    FetchReport,
    attachment_fingerprint,
    eligibility,
    is_attachment_marker,
    marker_for,
)
from uls.settings.canvas_checks import CanvasCheckError
from uls.state.sqlite import SQLiteStateStore

pytestmark = pytest.mark.unit

ORIGIN = "https://canvas.example.edu"
COURSE_ID = 67535
FOLDER = "synthetic-upload"
PDF_A = b"%PDF-1.4 lecture A"
PDF_B = b"%PDF-1.4 exam B..."
STAMP = "2026-10-01T00:00:00Z"


class FakeFiles:
    """Course-scoped Canvas File objects and their bytes (no network)."""

    def __init__(self, files: dict[str, tuple[str, bytes]]) -> None:
        self.files = dict(files)
        self.stamps: dict[str, str] = {}
        self.metadata_calls: list[str] = []
        self.download_calls: list[str] = []
        self.fail_download: CanvasCheckError | None = None

    def file_metadata(self, course_id: int, file_id: str, deadline: float) -> Any:
        assert course_id == COURSE_ID
        self.metadata_calls.append(file_id)
        name, data = self.files[file_id]
        return {"id": int(file_id), "filename": name, "display_name": name, "size": len(data),
                "content-type": "application/pdf", "updated_at": self.stamps.get(file_id, STAMP),
                "url": f"{ORIGIN}/files/{file_id}/download?verifier=v"}

    def download(self, url: str, *, course_id: int, file_id: str, expected_size: int, deadline: float) -> bytes:
        self.download_calls.append(file_id)
        if self.fail_download is not None:
            raise self.fail_download
        return self.files[file_id][1]


class Harness:
    def __init__(self, tmp_path, files: dict[str, tuple[str, bytes]]) -> None:
        from tests.integration.test_intake_worker_preview import _folder

        self.state = SQLiteStateStore(tmp_path / "state.sqlite3")
        self.drive = InMemoryDriveWorker([_folder(FOLDER, "synthetic-semester")])
        self.reader = FakeFiles(files)
        self.token: str | None = "tok"
        harness = self

        @contextmanager
        def factory():
            yield harness.reader if harness.token else None

        self.fetcher = CanvasAttachmentFetcher(self.state, self.drive, origin=ORIGIN, reader_factory=factory,
                                               max_bytes=1_000_000)

    def run(self, candidates, budget: int = 10) -> FetchReport:
        report = FetchReport()
        self.fetcher.fetch_course(COURSE_ID, candidates, FOLDER, budget, report)
        return report

    def bindings(self) -> list[dict[str, Any]]:
        with self.state._transaction() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM canvas_drive_bindings ORDER BY attachment_id, observation_revision").fetchall()]

    def uploads(self) -> list[str]:
        return [e[1] for e in self.drive.events if e[0] == "create"]


def _announcement(revision: int = 1, files: dict[str, tuple[str, bytes]] | None = None, stamp: str = STAMP):
    files = files or {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)}
    return [AttachmentCandidate("announcement", "900", revision, fid, name, len(data), "application/pdf",
                                updated_at=stamp)
            for fid, (name, data) in files.items()]


def test_two_attachments_become_two_private_drive_files_and_two_bindings(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    report = h.run(_announcement())
    assert (report.downloaded, report.failed, report.skipped) == (2, 0, 0)
    rows = h.bindings()
    assert len(rows) == 2 and len(h.uploads()) == 2
    for row in rows:
        meta = h.drive.files[row["drive_file_id"]]
        assert meta.parents == (FOLDER,) and is_attachment_marker(meta.app_properties)
        assert row["byte_sha256"] == hashlib.sha256(h.drive.contents[row["drive_file_id"]]).hexdigest()
        assert (row["resource_kind"], row["resource_id"], row["observation_revision"]) == ("announcement", "900", 1)


def test_reobservation_and_an_edited_announcement_add_nothing(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    h.run(_announcement())
    h.reader.metadata_calls.clear()
    report = h.run(_announcement())                 # the same observation again
    report2 = h.run(_announcement(revision=2))      # the announcement was edited; the files are the same
    assert h.reader.metadata_calls == []
    assert report.unchanged == 2 and report2.unchanged == 2
    assert len(h.bindings()) == 2 and len(h.uploads()) == 2 and h.reader.metadata_calls == []


def test_a_replaced_attachment_gets_a_new_file_and_binding_and_the_old_binding_is_kept(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    h.run(_announcement())
    before = h.bindings()
    new_b = PDF_B + b" v2"
    h.reader.files["12"] = ("exam-b.pdf", new_b)
    h.reader.stamps["12"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", new_b)}))
    assert report.downloaded == 1 and report.unchanged == 1
    after = h.bindings()
    assert len(after) == 3 and all(row in after for row in before)
    newest = [r for r in after if r["observation_revision"] == 2]
    assert len(newest) == 1 and newest[0]["attachment_size"] == len(new_b)


def test_a_lost_upload_response_is_recovered_without_a_second_upload(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    candidates = _announcement(files={"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    first = h.run(candidates)
    assert first.codes["UPLOAD_UNCERTAIN"] == 1 and h.bindings() == [] and len(h.uploads()) == 1
    second = h.run(candidates)
    assert second.recovered == 1 and len(h.uploads()) == 1 and h.reader.download_calls == ["11"]
    [row] = h.bindings()
    assert row["drive_file_id"] == h.uploads()[0] and row["byte_sha256"] == hashlib.sha256(PDF_A).hexdigest()


def _marker(candidate, data: bytes = PDF_A, name: str = "lecture-a.pdf"):
    return marker_for(attachment_fingerprint(ORIGIN, COURSE_ID, candidate, name, len(data), STAMP))


def test_a_marker_file_without_a_recorded_canvas_download_is_never_bound(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    [candidate] = _announcement(files={"11": ("lecture-a.pdf", PDF_A)})
    h.drive.create_file_with_marker(FOLDER, "lecture-a.pdf", "application/pdf", PDF_A, dict(_marker(candidate)))
    report = h.run([candidate])
    assert report.codes["PROVENANCE_UNVERIFIED"] == 1 and h.bindings() == [] and h.reader.download_calls == []


def test_recovery_requires_the_drive_bytes_to_be_the_downloaded_canvas_bytes(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    [candidate] = _announcement(files={"11": ("lecture-a.pdf", PDF_A)})

    real = h.drive.create_file_with_marker
    forged = b"%PDF-1.4 FORGED".ljust(len(PDF_A), b"!")

    def lose(parent, name, mime, content, marker):     # other bytes land under the marker, response lost
        real(parent, name, mime, forged, marker)
        raise ProviderUnavailableError("lost")

    h.drive.create_file_with_marker = lose  # type: ignore[method-assign]
    h.run([candidate])
    del h.drive.create_file_with_marker
    report = h.run([candidate])
    assert report.codes["PROVENANCE_UNVERIFIED"] == 1 and h.bindings() == []


def test_two_marker_files_for_one_intent_are_a_conflict_and_bind_nothing(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    [candidate] = _announcement(files={"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    h.run([candidate])
    h.drive.create_file_with_marker(FOLDER, "lecture-a.pdf", "application/pdf", PDF_A, dict(_marker(candidate)))
    report = h.run([candidate])
    assert report.codes["MARKER_CONFLICT"] == 1 and h.bindings() == [] and len(h.reader.download_calls) == 1


def test_an_unseen_create_outcome_is_never_retried_with_a_second_create(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    candidates = _announcement(files={"11": ("lecture-a.pdf", PDF_A)})

    def boom(*_a, **_k):                                 # failed (or crashed) before anything landed
        raise ProviderUnavailableError("down")

    h.drive.create_file_with_marker = boom  # type: ignore[method-assign]
    assert h.run(candidates).codes["UPLOAD_UNCERTAIN"] == 1
    del h.drive.create_file_with_marker                  # the real create is available again ...
    for _ in range(2):
        report = h.run(candidates)                       # ... but this version is never created twice
        assert report.codes["UPLOAD_UNCERTAIN"] == 1
    assert h.uploads() == [] and h.bindings() == [] and h.reader.download_calls == ["11"]
    assert h.state.canvas_attachment_intent_counts() == {"UNCERTAIN": 1}


def test_a_new_canvas_version_with_the_same_bytes_creates_nothing(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    h.reader.stamps["11"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A)}, stamp="2026-10-05T00:00:00Z"))
    assert report.unchanged == 1 and len(h.uploads()) == 1 and len(h.bindings()) == 1


def test_a_shared_destination_folder_receives_no_upload(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.files[FOLDER] = replace(h.drive.files[FOLDER], is_publicly_shared=True)
    report = h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert report.codes["FOLDER_NOT_PRIVATE"] == 1 and h.uploads() == [] and h.bindings() == []
    assert h.state.canvas_attachment_intent_counts() == {}


def test_a_canvas_object_that_differs_from_the_listing_is_not_downloaded(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("other.pdf", PDF_A)})
    report = h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert report.codes["FILE_MISMATCH"] == 1 and h.reader.download_calls == [] and h.uploads() == []


def test_a_refused_redirect_downloads_and_binds_nothing(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.reader.fail_download = CanvasCheckError("DESTINATION_NOT_ALLOWED")
    report = h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert report.codes["DOWNLOAD_HOST_NOT_ALLOWED"] == 1 and h.uploads() == [] and h.bindings() == []


def test_a_module_file_item_is_resolved_through_the_files_api_and_bound_once(tmp_path) -> None:
    h = Harness(tmp_path, {"31": ("Chapter 03-Address.pdf", PDF_A)})
    item = AttachmentCandidate("module_item", "1003", 1, "31")
    assert h.run([item]).downloaded == 1
    h.reader.metadata_calls.clear()
    assert h.run([item]).unchanged == 1 and h.reader.metadata_calls == []
    [row] = h.bindings()
    assert (row["resource_kind"], row["attachment_filename"]) == ("module_item", "Chapter 03-Address.pdf")


def test_ineligible_attachments_are_skipped_before_any_request(tmp_path) -> None:
    h = Harness(tmp_path, {})
    candidates = [
        AttachmentCandidate("announcement", "900", 1, "1", "slides.pptx", 10, "application/vnd.ms-powerpoint"),
        AttachmentCandidate("announcement", "900", 1, "2", "big.pdf", 10_000_000, "application/pdf"),
        AttachmentCandidate("announcement", "900", 1, "3", "locked.pdf", 10, "application/pdf", locked=True),
        AttachmentCandidate("announcement", "900", 1, "4", "../x.pdf", 10, "application/pdf"),
    ]
    report = h.run(candidates)
    assert report.skipped == 4 and h.reader.metadata_calls == [] and h.uploads() == []
    assert dict(report.codes) == {"UNSUPPORTED_TYPE": 1, "TOO_LARGE": 1, "LOCKED": 1, "BAD_NAME": 1}


def test_the_budget_bounds_the_work_of_one_tick(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    report = FetchReport()
    left = h.fetcher.fetch_course(COURSE_ID, _announcement(), FOLDER, 1, report)
    assert left == 0 and report.downloaded == 1 and len(h.bindings()) == 1


def test_no_credential_means_no_request(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.token = None
    report = h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert report.codes["NO_CREDENTIAL"] == 1 and h.reader.metadata_calls == []


@pytest.mark.parametrize(("name", "size", "ctype", "locked", "expected"), [
    ("a.pdf", 5, "application/pdf", False, None),
    ("a.pdf", 5, "application/octet-stream", False, "UNSUPPORTED_TYPE"),
    ("main.c", 5, "application/octet-stream", False, None),
    ("data.csv", 5, "text/csv", False, None),
    ("a.pdf", True, "application/pdf", False, "TOO_LARGE"),
    ("a.pdf", 0, "application/pdf", False, "TOO_LARGE"),
    ("a/b.pdf", 5, "application/pdf", False, "BAD_NAME"),
    ("a.pdf", 5, "application/pdf", True, "LOCKED"),
    (None, 5, "application/pdf", False, "BAD_NAME"),
])
def test_eligibility(name, size, ctype, locked, expected) -> None:
    assert eligibility(name, size, ctype, locked, 100) == expected


def test_the_drive_create_failure_is_not_counted_as_a_binding(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})

    def boom(*_a, **_k):
        raise ProviderUnavailableError("down")

    h.drive.create_file_with_marker = boom  # type: ignore[method-assign]
    report = h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert report.failed == 1 and h.bindings() == []


def test_marker_files_without_a_binding_are_hidden_from_discovery(tmp_path) -> None:
    from uls.ingestion.discovery import _unbound_canvas_attachment

    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    [uploaded] = h.uploads()
    meta = h.drive.files[uploaded]
    assert _unbound_canvas_attachment(h.state, meta) is True
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    assert _unbound_canvas_attachment(h.state, meta) is False
    assert _unbound_canvas_attachment(h.state, replace(meta, app_properties={})) is False
    assert _unbound_canvas_attachment(object(), meta) is True        # a store that cannot answer hides it


def _observer(tmp_path, reader, *, collect: bool):
    from datetime import date

    from uls.intake.canvas_observer import CanvasObserver
    from uls.intake.classification.calendar import SemesterRange

    state = SQLiteStateStore(tmp_path / "obs.sqlite3")

    @contextmanager
    def factory():
        yield reader

    return CanvasObserver(
        state, origin=ORIGIN, verified_origin=ORIGIN, course_map={COURSE_ID: "2026-2_LMS67535-001"},
        reader_factory=factory, semester_for=lambda _k: SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config"),
        profile_provider=lambda: ORIGIN, principal_provider=lambda: "42", verified_principal="42",
        collect_attachments=collect)


def _reader(**kwargs):
    from tests.unit.test_canvas_observer import FakeReader

    announcements = [{"id": 900, "title": "중간고사 및 기출문제 공지", "posted_at": "2026-10-01T00:00:00Z",
                      "message": "<p>본문</p>",
                      "attachments": [{"id": 11, "filename": "exam.pdf", "size": 9, "content-type": "application/pdf",
                                       "url": f"{ORIGIN}/files/11/download?verifier=v"}, "junk"]}]
    modules = [{"id": 103, "name": "3주차", "position": 3, "items_count": 2, "items": [
        {"id": 1003, "title": "Chapter 03-Address.pdf", "type": "File", "content_id": 31},
        {"id": 1004, "title": "2026-09-16", "type": "ExternalTool"}]}]
    return FakeReader(modules=modules, announcements=announcements, **kwargs)


def test_a_complete_observation_hands_over_attachment_candidates_in_memory_only(tmp_path) -> None:
    observer = _observer(tmp_path, _reader(), collect=True)
    result = observer.observe_course(COURSE_ID)
    assert result.status == "COMPLETE"
    assert sorted((c.resource_kind, c.resource_id, c.attachment_id, c.filename) for c in result.attachments) == [
        ("announcement", "900", "11", "exam.pdf"), ("module_item", "1003", "31", None)]
    with observer._state._transaction() as connection:       # nothing of the body, URL or verifier is stored
        dump = repr([dict(r) for r in connection.execute("SELECT * FROM canvas_observations").fetchall()])
    assert "본문" not in dump and "verifier" not in dump and "download" not in dump


def test_attachments_are_not_collected_when_downloads_are_off(tmp_path) -> None:
    result = _observer(tmp_path, _reader(), collect=False).observe_course(COURSE_ID)
    assert result.status == "COMPLETE" and result.attachments == ()


def test_an_incomplete_observation_hands_over_no_candidates(tmp_path) -> None:
    reader = _reader()
    reader.modules.append({"id": 104, "position": 4})          # a malformed module: not provably complete
    outcome = _observer(tmp_path, reader, collect=True).observe_course(COURSE_ID)
    assert outcome.status == "INCOMPLETE" and outcome.attachments == ()


def _pdf_probe():
    from uls.intake.classification.pipeline import SourceProbe

    return SourceProbe(b"%PDF-1.4 slides", True)


def test_a_proven_canvas_binding_supplies_course_and_module_week() -> None:
    from tests.unit.test_intake_classification_pipeline import _classify

    from uls.intake.classification.taxonomy import Origin

    outcome = _classify("Chapter 03-Address.pdf", mime_type="application/pdf", origin=Origin.PROFESSOR_SOURCE,
                        canvas_course_key="2026-2_LMS67535-001", canvas_week=3, probe=_pdf_probe())
    assert (outcome.course_key, outcome.course_basis, outcome.week) == ("2026-2_LMS67535-001", "canvas_binding", 3)
    assert "CANVAS_WEEK_CONFLICT" not in outcome.notes


def test_a_conflicting_file_name_week_is_not_chosen_automatically() -> None:
    from tests.unit.test_intake_classification_pipeline import _classify

    from uls.intake.classification.pipeline import BLOCK_WEEK_CONFLICT
    from uls.intake.classification.taxonomy import Origin

    outcome = _classify("강의계획서_4주차.pdf", mime_type="application/pdf", origin=Origin.PROFESSOR_SOURCE,
                        canvas_course_key="2026-2_LMS67535-001", canvas_week=3, probe=_pdf_probe())
    assert outcome.week is None and "CANVAS_WEEK_CONFLICT" in outcome.notes
    assert outcome.kind is not None and BLOCK_WEEK_CONFLICT in outcome.stage_a_blockers()


def test_canvas_axes_are_ignored_without_a_proven_binding() -> None:
    from tests.unit.test_intake_classification_pipeline import _classify

    outcome = _classify("Chapter 03-Address.pdf", mime_type="application/pdf",
                        canvas_course_key="2026-2_LMS67535-001", canvas_week=3, probe=_pdf_probe())
    assert outcome.course_key is None and outcome.week is None


def test_a_lost_response_for_one_attachment_does_not_stop_the_next(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    h.drive.drop_next_create_response = True
    report = h.run(_announcement())
    assert report.codes["UPLOAD_UNCERTAIN"] == 1 and report.downloaded == 1 and len(h.bindings()) == 1


def test_eligibility_uses_the_drive_read_back_limit(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.max_bytes = len(PDF_A) - 1                    # recovery could not read the upload back
    fetcher = CanvasAttachmentFetcher(h.state, h.drive, origin=ORIGIN, reader_factory=lambda: None,  # type: ignore[arg-type]
                                      max_bytes=1_000_000)
    report = FetchReport()
    fetcher.fetch_course(COURSE_ID, _announcement(files={"11": ("lecture-a.pdf", PDF_A)}), FOLDER, 5, report)
    assert report.codes["TOO_LARGE"] == 1


def test_an_unsettled_earlier_version_blocks_or_dedups_the_next_version(tmp_path) -> None:
    """R-PE2-01: F1 landed but its response was lost; F2 (same bytes, newer updated_at) creates nothing."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    h.reader.stamps["11"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A)}, stamp="2026-10-05T00:00:00Z"))
    assert len(h.uploads()) == 1 and len(h.bindings()) == 1 and report.unchanged == 1
    assert h.state.canvas_attachment_intent_counts() == {"BOUND": 1, "SAME_BYTES": 1}


def test_a_new_version_waits_while_an_earlier_create_is_unknown(tmp_path) -> None:
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})

    def boom(*_a, **_k):
        raise ProviderUnavailableError("down")

    h.drive.create_file_with_marker = boom  # type: ignore[method-assign]
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    del h.drive.create_file_with_marker
    h.reader.files["11"] = ("lecture-a.pdf", PDF_A + b"v2")
    h.reader.stamps["11"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A + b"v2")},
                                 stamp="2026-10-05T00:00:00Z"))
    assert report.codes["UPLOAD_UNCERTAIN"] == 1 and h.uploads() == [] and h.bindings() == []


def test_recovery_binds_the_revision_of_the_download_not_the_current_one(tmp_path) -> None:
    """R-PE2-02."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    h.run(_announcement(revision=1, files={"11": ("lecture-a.pdf", PDF_A)}))
    h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A)}))   # only the title changed
    [row] = h.bindings()
    assert row["observation_revision"] == 1 and len(h.uploads()) == 1


def test_a_binding_slot_conflict_is_a_fixed_code_and_marks_the_intent(tmp_path) -> None:
    """R-PE2-03: a second version under the same observation revision cannot take the slot."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    h.reader.files["11"] = ("lecture-a.pdf", PDF_A + b"v2")
    h.reader.stamps["11"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=1, files={"11": ("lecture-a.pdf", PDF_A + b"v2")},
                                 stamp="2026-10-05T00:00:00Z"))
    assert report.codes["BINDING_CONFLICT"] == 1 and len(h.bindings()) == 1 and len(h.uploads()) == 1


def test_a_changed_attachment_is_a_new_observation_revision(tmp_path) -> None:
    """R-PE2-03: the observer bumps the revision when only the attached file changes."""
    reader = _reader()
    observer = _observer(tmp_path, reader, collect=True)
    first = observer.observe_course(COURSE_ID)
    reader.announcements[0]["attachments"][0]["size"] = 10
    reader.announcements[0]["attachments"][0]["updated_at"] = "2026-10-05T00:00:00Z"
    second = observer.observe_course(COURSE_ID)
    rev = {c.resource_id: c.observation_revision for c in first.attachments if c.resource_kind == "announcement"}
    rev2 = {c.resource_id: c.observation_revision for c in second.attachments if c.resource_kind == "announcement"}
    assert rev2["900"] == rev["900"] + 1
    third = observer.observe_course(COURSE_ID)                     # nothing changed: same revision
    assert {c.resource_id: c.observation_revision for c in third.attachments
            if c.resource_kind == "announcement"}["900"] == rev2["900"]


def test_collecting_attachments_off_never_changes_a_revision(tmp_path) -> None:
    reader = _reader()
    observer = _observer(tmp_path, reader, collect=False)
    observer.observe_course(COURSE_ID)
    reader.announcements[0]["attachments"][0]["size"] = 10
    observer.observe_course(COURSE_ID)
    latest = observer._state.latest_canvas_observation(ORIGIN, COURSE_ID, "announcement", "900")
    assert latest["observation_revision"] == 1 and latest["attachment_signature"] is None


def test_bindings_and_intents_of_another_origin_are_never_reused(tmp_path) -> None:
    """R-PE2-04: the same numeric ids under another Canvas origin are another attachment."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    other = CanvasAttachmentFetcher(h.state, h.drive, origin="https://other.example.edu",
                                    reader_factory=h.fetcher._factory, max_bytes=1_000_000)
    report = FetchReport()
    other.fetch_course(COURSE_ID, _announcement(files={"11": ("lecture-a.pdf", PDF_A)}), FOLDER, 5, report)
    # Not "unchanged": the other origin's intent/binding is not reused; its own Canvas object is read.
    # The numeric slot is taken, so nothing is uploaded or bound for it (fail closed).
    assert report.unchanged == 0 and report.codes["BINDING_CONFLICT"] == 1 and len(h.reader.download_calls) == 2
    assert len(h.uploads()) == 1 and [row["origin"] for row in h.bindings()] == ["https://canvas.example.edu"]


def test_switching_downloads_off_and_on_never_bumps_a_revision(tmp_path) -> None:
    """H-PE3-01: ON -> OFF -> ON with unchanged attachments keeps the observation revision."""
    from datetime import date

    from uls.intake.canvas_observer import CanvasObserver
    from uls.intake.classification.calendar import SemesterRange

    reader = _reader()
    state = SQLiteStateStore(tmp_path / "toggle.sqlite3")

    @contextmanager
    def factory():
        yield reader

    def observer(collect: bool) -> CanvasObserver:
        return CanvasObserver(
            state, origin=ORIGIN, verified_origin=ORIGIN, course_map={COURSE_ID: "2026-2_LMS67535-001"},
            reader_factory=factory,
            semester_for=lambda _k: SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config"),
            profile_provider=lambda: ORIGIN, principal_provider=lambda: "42", verified_principal="42",
            collect_attachments=collect)

    for collect in (True, False, True):
        assert observer(collect).observe_course(COURSE_ID).status == "COMPLETE"
    for kind, resource in (("announcement", "900"), ("module_item", "1003")):
        latest = state.latest_canvas_observation(ORIGIN, COURSE_ID, kind, resource)
        assert latest["observation_revision"] == 1 and latest["attachment_signature"] is not None


def test_an_announcement_attachment_without_name_or_size_costs_no_request(tmp_path) -> None:
    """R-PEI-01."""
    from uls.intake.canvas_attachments import candidate_from_announcement

    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    candidate = candidate_from_announcement({"id": 11}, "900", 1)
    assert candidate is not None
    report = h.run([candidate])
    assert report.skipped == 1 and h.reader.metadata_calls == [] and h.uploads() == []


@pytest.mark.parametrize("name", ["exam\u0085.pdf", "exam\u202egdp.pdf", "a\u200b.pdf"])
def test_unicode_control_and_format_characters_are_bad_names(name) -> None:
    """R-PEI-02."""
    assert eligibility(name, 5, "application/pdf", False, 100) == "BAD_NAME"


def test_a_settled_earlier_create_is_counted_as_recovered(tmp_path) -> None:
    """H-PEI-02."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A)})
    h.drive.drop_next_create_response = True
    h.run(_announcement(files={"11": ("lecture-a.pdf", PDF_A)}))
    h.reader.stamps["11"] = "2026-10-05T00:00:00Z"
    report = h.run(_announcement(revision=2, files={"11": ("lecture-a.pdf", PDF_A)}, stamp="2026-10-05T00:00:00Z"))
    assert report.recovered == 1 and report.unchanged == 1


def test_an_unreadable_state_for_one_attachment_does_not_stop_the_next(tmp_path) -> None:
    """H-PEI-01."""
    h = Harness(tmp_path, {"11": ("lecture-a.pdf", PDF_A), "12": ("exam-b.pdf", PDF_B)})
    real = h.state.list_canvas_attachment_intents

    def flaky(**key):
        if key["attachment_id"] == "11":
            raise RuntimeError("locked")
        return real(**key)

    h.state.list_canvas_attachment_intents = flaky  # type: ignore[method-assign]
    report = h.run(_announcement())
    assert report.codes["STATE_UNAVAILABLE"] == 1 and report.downloaded == 1
