"""A Canvas-sourced Material runs its AUTO plan even when its course has a recording calendar.

The calendar projection is part of a TRANSCRIPT decision only (plan §2.3); the live P-F run
showed a PROFESSOR_SOURCE EXAM PDF stopping at preflight with CALENDAR.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from tests.integration.test_intake_worker_canvas_attachments import CANVAS_COURSE, _wire
from tests.integration.test_intake_worker_classification_pb2a import COURSE
from tests.integration.test_intake_worker_preview import _pdf_bytes, _system

from uls.intake.canvas_attachments import AttachmentCandidate
from uls.intake.classification import SemesterRange, build_calendar

pytestmark = pytest.mark.integration


def test_a_professor_exam_pdf_is_not_stopped_by_its_course_calendar(tmp_path: Path) -> None:
    pdf = _pdf_bytes()
    with _system(tmp_path, name="unrelated.md") as system:
        worker, _reader = _wire(
            system,
            {"11": ("중간고사_기출문제지.pdf", pdf)},
            [
                AttachmentCandidate(
                    "announcement",
                    "900",
                    1,
                    "11",
                    "중간고사_기출문제지.pdf",
                    len(pdf),
                    "application/pdf",
                )
            ],
        )
        semester = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
        system["state"].replace_recording_calendar_current(
            COURSE,
            CANVAS_COURSE,
            build_calendar(COURSE, [], semester=semester, collection_complete=True),
            collection_complete=True,
        )
        for _ in range(3):
            worker.run_once()
        [created] = [
            e[1]
            for e in system["drive"].events
            if e[0] == "create" and e[3] == "중간고사_기출문제지.pdf"
        ]
        item = system["state"].get_intake_item_by_provider_file("google_drive", created)
        assert item is not None and item.origin == "PROFESSOR_SOURCE"
        assert (item.status, item.last_error) != (
            "RECONCILE_REQUIRED",
            "AUTO preflight failed: CALENDAR",
        )
        assert item.status == "ORGANIZED", (item.status, item.last_error)
