"""P-B3a: worker integration of the metadata-only Canvas observation (plan §4, §6.3)."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _enable
from tests.integration.test_intake_worker_classification_pb2a import (
    COURSE,
    MATCHED_NAME,
    RAW,
    _auto_plans,
    _complete_calendar,
    _item,
)
from tests.integration.test_intake_worker_preview import _system

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_promotion(monkeypatch):
    from uls.intake.worker import IntakeWorker

    monkeypatch.setattr(IntakeWorker, "_promote_ready_plans", lambda self: None)


class _Observer:
    def __init__(self, fail_on: set[int] | None = None, matches: bool = True) -> None:
        self.calls: list[int] = []
        self.fail_on = fail_on or set()
        self.matches = matches

    def observe_course(self, course_id: int):
        self.calls.append(course_id)
        if course_id in self.fail_on:
            raise RuntimeError("provider down")

    def profile_matches(self) -> bool:
        return self.matches


def test_observation_is_off_by_default_even_with_an_observer(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        observer = _Observer()
        worker.canvas_observer = observer
        system["config"].intake.classification.canvas_course_map = {1: COURSE, 2: "2026-2_X-001"}
        worker.run_once()
        assert observer.calls == []


def test_observation_runs_bounded_rotating_and_never_blocks_the_tick(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        cfg = system["config"].intake.classification
        cfg.canvas_observation_enabled, cfg.max_canvas_courses_per_tick = True, 2
        cfg.canvas_course_map = {10: COURSE, 20: "2026-2_B-001", 30: "2026-2_C-001"}
        observer = _Observer(fail_on={10})
        worker.canvas_observer = observer
        result = worker.run_once()
        assert result["status"] == "ok" and result["discovered"] == 1   # Drive intake still ran
        assert observer.calls == [10, 20]
        worker.run_once()
        assert observer.calls[2:] == [30, 10]                          # rotation continues


def test_a_revoked_canvas_mapping_stops_using_the_recorded_calendar(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["config"].intake.classification.canvas_course_map = {}   # mapping withdrawn
        worker.run_once()
        item = _item(system)
        assert item.calendar_match == "AMBIGUOUS" and _auto_plans(system["state"]) == []


def test_a_foreign_canvas_profile_stops_using_the_recorded_calendar(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.canvas_observer = _Observer(matches=False)
        worker.run_once()
        assert _item(system).calendar_match == "AMBIGUOUS" and _auto_plans(system["state"]) == []


def test_a_second_canvas_course_for_the_same_key_stops_using_the_calendar(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["config"].intake.classification.canvas_course_map = {67535: COURSE, 99999: COURSE}
        worker.run_once()
        assert _item(system).calendar_match == "AMBIGUOUS" and _auto_plans(system["state"]) == []


def test_a_verified_mapping_still_matches_and_plans(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        worker.canvas_observer = _Observer()
        worker.run_once()
        assert _item(system).calendar_match == "MATCHED" and len(_auto_plans(system["state"])) == 1


def test_an_observed_calendar_drives_the_match_end_to_end(tmp_path: Path) -> None:
    from contextlib import contextmanager

    from tests.unit.test_canvas_observer import FakeReader

    from uls.intake.canvas_observer import CanvasObserver
    from uls.intake.classification import SemesterRange

    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        origin = "https://canvas.example.edu"
        cfg = system["config"].intake.classification
        cfg.canvas_course_map = {67535: COURSE}
        days = {1: "2026-09-03", 2: "2026-09-10", 3: "2026-09-17"}
        reader = FakeReader(modules=[
            {"id": 100 + w, "name": f"{w}주차", "position": w, "items_count": 1,
             "items": [{"id": 1000 + w, "title": day, "type": "ExternalTool"}]}
            for w, day in days.items()])

        @contextmanager
        def factory():
            yield reader

        from datetime import date

        semester = SemesterRange(date(2026, 9, 1), date(2026, 12, 20), "config")
        worker.canvas_observer = CanvasObserver(
            system["state"], origin=origin, verified_origin=origin, course_map={67535: COURSE},
            reader_factory=factory, semester_for=lambda _key: semester)
        cfg.canvas_observation_enabled, cfg.canvas_origin = True, origin
        worker.run_once()
        assert system["state"].recording_calendar_complete(COURSE)
        assert _item(system).calendar_match == "MATCHED" and len(_auto_plans(system["state"])) == 1


def test_new_observation_stops_when_auto_is_switched_off(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        cfg = system["config"].intake.classification
        cfg.canvas_observation_enabled, cfg.canvas_course_map = True, {10: COURSE}
        observer = _Observer()
        worker.canvas_observer = observer
        cfg.enabled = False
        worker.run_once()
        assert observer.calls == []


def test_an_ineligible_calendar_is_closed_in_the_state_so_a_restored_mapping_needs_a_new_observation(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        cfg = system["config"].intake.classification
        assert system["state"].recording_calendar_complete(COURSE)
        saved, cfg.canvas_course_map = cfg.canvas_course_map, {}
        worker.run_once()
        assert not system["state"].recording_calendar_complete(COURSE)         # closed, not only ignored
        cfg.canvas_course_map = saved                                           # the mapping comes back
        worker.run_once()
        assert not system["state"].recording_calendar_complete(COURSE)         # no stale proof is revived


def test_observation_enabled_without_an_observer_has_no_live_profile_evidence(tmp_path: Path) -> None:
    with _system(tmp_path, name=MATCHED_NAME, raw=RAW) as system:
        worker = _enable(system)
        _complete_calendar(system["state"])
        system["config"].intake.classification.canvas_observation_enabled = True
        worker.run_once()
        assert _item(system).calendar_match == "AMBIGUOUS" and _auto_plans(system["state"]) == []
