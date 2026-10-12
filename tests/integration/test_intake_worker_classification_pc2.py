"""P-C2: S2 (Jev Choice) integration in the discovery path (plan §3.2, §4)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.integration.test_intake_worker_classification_pb1 import _assign_request, _enable
from tests.integration.test_intake_worker_preview import _system

from uls.adapters.llm.typesafe_choice import (
    ChoiceAnswer,
    ChoiceResult,
    ClassifierError,
    RequestTooLarge,
)
from uls.intake.classification import s2

pytestmark = pytest.mark.integration

RAW = b"# Synthetic lecture\n\nContact me at prof@univ.ac.kr or https://lms.example/a?x=1. Binary search.\n"


def _probabilities(choice: str, top: float) -> dict[str, float]:
    rest = (1.0 - top) / (len(s2.OPTIONS) - 1)
    return {option: (top if option == choice else rest) for option in s2.OPTIONS}


class FakeClient:
    def __init__(self, choice="LECTURE_SLIDES", confidence=0.9, top=0.85, error: Exception | None = None,
                 too_large_times=0) -> None:
        self.choice, self.confidence, self.top, self.error = choice, confidence, top, error
        self.too_large_times = too_large_times
        self.calls: list[dict] = []
        self.attempts = 0

    def ask(self, state, questions):
        self.attempts += 1
        if self.attempts <= self.too_large_times:
            raise RequestTooLarge
        self.calls.append(dict(state))
        if self.error is not None:
            raise self.error
        probabilities = _probabilities(self.choice, self.top)
        return ChoiceResult("jev-1.13.0", {"kind": ChoiceAnswer(self.choice, self.confidence, probabilities)}, 10, 2)


def _bind(system, raw=RAW):
    system["state"].record_canvas_drive_binding(
        drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="module_file",
        resource_id="m1", observation_revision=1, attachment_id="att-1", attachment_filename="lecture.md",
        attachment_size=len(raw), byte_sha256=hashlib.sha256(raw).hexdigest())


def _setup(tmp_path: Path, client, *, bound=True, budget=50):
    cm = _system(tmp_path, raw=RAW)
    system = cm.__enter__()
    worker = _enable(system)
    system["config"].intake.classification.max_calls_per_tick = budget
    worker.s2_client = client
    if bound:
        _bind(system)
    return cm, system, worker


def _item(system):
    return system["state"].get_intake_item_by_provider_file("google_drive", system["source_id"])


def test_confident_answer_decides_the_kind_and_records_the_model_decision(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        item = _item(system)
        assert (item.classified_kind, item.classification_source) == ("LECTURE_SLIDES", "jev:jev-1.13.0")
        assert item.origin == "PROFESSOR_SOURCE" and len(client.calls) == 1
        record = system["state"].get_classification_record(item.classification_record_id)
        decision = json.loads(record.decision_json)
        assert decision["type"] == "model" and decision["prompt_version"] == s2.PROMPT_VERSION
        assert record.snapshot_sha256 and record.byte_sha256 == hashlib.sha256(RAW).hexdigest()
        sent = str(client.calls[0])
        assert "prof@univ.ac.kr" not in sent and "lms.example" not in sent and "<url>" in sent
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("kwargs,note", [
    ({"confidence": 0.79}, "LOW_CONFIDENCE"), ({"top": 0.6}, "LOW_CONFIDENCE"),
    ({"choice": "no_match"}, "CLASSIFIER_NO_MATCH"),
])
def test_uncertain_answers_fall_to_a_human_draft(tmp_path: Path, kwargs, note) -> None:
    cm, system, worker = _setup(tmp_path, FakeClient(**kwargs))
    try:
        worker.run_once()
        item = _item(system)
        assert item.classified_kind is None and item.classification_state == "HUMAN"
        assert note in _assign_request(system["notion"])["Suggestion Note"]
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("error,code,counter", [
    (ClassifierError("CLASSIFIER_TIMEOUT"), "CLASSIFIER_TIMEOUT", "transport"),
    (ClassifierError("CLASSIFIER_INVALID_RESPONSE"), "CLASSIFIER_INVALID_RESPONSE", "invalid"),
    (RuntimeError("secret provider text"), "CLASSIFIER_TRANSPORT", "transport"),
])
def test_provider_failures_fall_to_human_with_fixed_codes(tmp_path: Path, error, code, counter) -> None:
    cm, system, worker = _setup(tmp_path, FakeClient(error=error))
    try:
        worker.run_once()
        item = _item(system)
        note = _assign_request(system["notion"])["Suggestion Note"]
        assert item.classification_state == "HUMAN" and code in note and "secret" not in note
        assert worker._s2_tick[counter] == 1 and worker._s2_tick["calls"] == 1
        worker._sync_unlocked()      # the next tick's readiness reports the previous tick's counters
        readiness = worker._classification_readiness()
        assert readiness["s2_status"] == "DEGRADED"
        assert readiness["invalid_response_count" if counter == "invalid" else "transport_failure_count"] == 1
    finally:
        cm.__exit__(None, None, None)


def test_unbound_origin_is_never_sent(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client, bound=False)
    try:
        worker.run_once()
        item = _item(system)
        assert client.calls == [] and item.classification_state == "HUMAN" and item.origin == "UNKNOWN"
        assert "S2_ORIGIN_INELIGIBLE" in _assign_request(system["notion"])["Suggestion Note"]
    finally:
        cm.__exit__(None, None, None)


def test_binding_with_different_bytes_is_never_sent(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client, bound=False)
    try:
        system["state"].record_canvas_drive_binding(
            drive_file_id=system["source_id"], canvas_course_id=67535, resource_kind="module_file",
            resource_id="m1", observation_revision=1, attachment_id="att-1", attachment_filename="lecture.md",
            attachment_size=len(RAW), byte_sha256="f" * 64)
        worker.run_once()
        assert client.calls == [] and _item(system).origin == "UNKNOWN"
    finally:
        cm.__exit__(None, None, None)


def test_without_a_client_behaviour_is_the_p_b_baseline(tmp_path: Path) -> None:
    cm, system, worker = _setup(tmp_path, None)
    try:
        worker.run_once()
        assert "CLASSIFIER_DISABLED" in _assign_request(system["notion"])["Suggestion Note"]
        assert worker._classification_readiness()["s2_status"] == "DISABLED"
    finally:
        cm.__exit__(None, None, None)


def test_budget_exhaustion_defers_without_a_request_and_resumes(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client, budget=0)
    try:
        worker.run_once()
        item = _item(system)
        assert item.classification_state == "DEFERRED" and item.last_error_code == "CLASSIFIER_BUDGET_EXHAUSTED"
        assert client.calls == [] and system["notion"].data_sources["synthetic-requests"] == []
        worker.run_once()
        assert _item(system).classification_state == "DEFERRED"
        assert worker._classification_readiness()["deferred_items"] == 1
        system["config"].intake.classification.max_calls_per_tick = 5
        worker.run_once()
        item = _item(system)
        assert item.classification_state != "DEFERRED" and item.last_error_code is None
        assert item.classified_kind == "LECTURE_SLIDES" and len(client.calls) == 1
    finally:
        cm.__exit__(None, None, None)


def test_cache_hit_costs_no_call_and_no_budget(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        item = _item(system)
        probe = worker._source_probe(item)
        outcome = SimpleNamespace(origin=s2_origin(), course_key=None, week=None)
        binding = system["state"].get_canvas_drive_binding(item.provider_file_id)
        before = worker._s2_tick["calls"]
        result = worker._run_s2(item, outcome, probe, binding)
        assert result.status == "DECIDED" and len(client.calls) == 1 and worker._s2_tick["calls"] == before
    finally:
        cm.__exit__(None, None, None)


def s2_origin():
    from uls.intake.classification.taxonomy import Origin

    return Origin.PROFESSOR_SOURCE


def test_oversized_request_shrinks_the_excerpt_and_never_counts(tmp_path: Path) -> None:
    client = FakeClient(too_large_times=2)
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        assert len(client.calls) == 1 and _item(system).classified_kind == "LECTURE_SLIDES"
        assert worker._s2_last_tick["calls"] + worker._s2_tick["calls"] == 1
    finally:
        cm.__exit__(None, None, None)


def test_request_too_large_at_every_size_goes_to_human(tmp_path: Path) -> None:
    cm, system, worker = _setup(tmp_path, FakeClient(too_large_times=99))
    try:
        worker.run_once()
        assert "S2_REQUEST_TOO_LARGE" in _assign_request(system["notion"])["Suggestion Note"]
        assert _item(system).classification_state == "HUMAN"
    finally:
        cm.__exit__(None, None, None)


def test_human_decided_item_is_not_reclassified_by_the_model(tmp_path: Path) -> None:
    client = FakeClient(confidence=0.1)
    cm, _, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        worker.run_once()
        worker.run_once()
        assert len(client.calls) == 1      # HUMAN state is a stable S3 decision
    finally:
        cm.__exit__(None, None, None)


def test_a_user_authored_transcript_origin_is_never_sent(tmp_path: Path) -> None:
    from uls.intake.classification.taxonomy import Origin

    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        item = _item(system)
        outcome = SimpleNamespace(origin=Origin.USER_AUTHORED_TRANSCRIPT, course_key=None, week=None)
        result = worker._run_s2(item, outcome, worker._source_probe(item), None)
        assert result.status == "HUMAN" and result.notes == ("S2_ORIGIN_INELIGIBLE",) and len(client.calls) == 1
    finally:
        cm.__exit__(None, None, None)


def test_waiting_deferred_items_keep_their_calls_across_semesters(tmp_path: Path) -> None:
    import dataclasses

    client = FakeClient(choice="no_match")
    cm, system, worker = _setup(tmp_path, client, budget=1)
    try:
        worker.run_once()
        base = _item(system)
        probe = worker._source_probe(base)
        binding = system["state"].get_canvas_drive_binding(base.provider_file_id)
        outcome = SimpleNamespace(origin=s2_origin(), course_key=None, week=None)
        worker._s2_tick["calls"] = 0
        new_item = dataclasses.replace(base, intake_id="new-item", source_version=base.source_version + 7,
                                       classification_state="NONE")
        deferred_item = dataclasses.replace(base, intake_id="old-deferred", source_version=base.source_version + 8,
                                            classification_state="DEFERRED")
        before = len(client.calls)
        worker._s2_deferred_waiting = {"old-deferred"}      # a DEFERRED item of another semester is still waiting
        assert worker._run_s2(new_item, outcome, probe, binding).status == "DEFERRED"
        assert len(client.calls) == before                   # the single call is kept for it
        assert worker._run_s2(deferred_item, outcome, probe, binding).status == "HUMAN"
        assert len(client.calls) == before + 1
    finally:
        cm.__exit__(None, None, None)


def test_cached_answer_is_revalidated(tmp_path: Path) -> None:
    from uls.intake.worker import IntakeWorker

    good = {"choice": "LECTURE_SLIDES", "confidence": 0.9, "probabilities": _probabilities("LECTURE_SLIDES", 0.85)}
    assert IntakeWorker._s2_cached_answer(good) is not None
    for bad in (
        {**good, "probabilities": {**good["probabilities"], "LAB_MATERIAL": 0.9}},          # sum / unique top
        {**good, "probabilities": {**good["probabilities"], "no_match": float("nan")}},
        {**good, "confidence": 1.5}, {**good, "choice": "EXAM"},
    ):
        assert IntakeWorker._s2_cached_answer(bad) is None


def test_a_malformed_client_answer_is_a_fixed_invalid_code(tmp_path: Path) -> None:
    class Broken(FakeClient):
        def ask(self, state, questions):
            return SimpleNamespace(answers={})

    cm, system, worker = _setup(tmp_path, Broken())
    try:
        worker.run_once()
        assert "CLASSIFIER_INVALID_RESPONSE" in _assign_request(system["notion"])["Suggestion Note"]
    finally:
        cm.__exit__(None, None, None)


def test_item_processing_discards_its_wait_entry(tmp_path: Path) -> None:
    cm, system, worker = _setup(tmp_path, FakeClient(), budget=5)
    try:
        worker.run_once()
        item = _item(system)
        worker._s2_deferred_waiting = {item.intake_id, "other"}
        worker._classify_discovered_item(item, worker.workspaces[0], "cfg", "ws")
        assert worker._s2_deferred_waiting == {"other"}
    finally:
        cm.__exit__(None, None, None)


def test_only_active_semester_deferred_items_reserve_calls(tmp_path: Path) -> None:
    cm, system, worker = _setup(tmp_path, FakeClient(), budget=0)
    try:
        worker.run_once()                                      # budget 0: the item becomes DEFERRED
        item = _item(system)
        assert item.classification_state == "DEFERRED"
        assert system["state"].list_deferred_intake_ids([item.semester]) == [item.intake_id]
        assert system["state"].list_deferred_intake_ids(["no-such-semester"]) == []
    finally:
        cm.__exit__(None, None, None)


def test_a_model_decision_survives_human_reentry_without_a_new_call(tmp_path: Path) -> None:
    client = FakeClient()
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        first = _item(system)
        assert first.classified_kind == "LECTURE_SLIDES" and first.classification_record_id
        note = system["state"].get_intake_suggestion(first.intake_id)["suggestion_note"]
        worker.run_once()
        worker.run_once()
        again = _item(system)
        assert len(client.calls) == 1
        assert (again.classified_kind, again.classification_source, again.classification_record_id) == (
            first.classified_kind, first.classification_source, first.classification_record_id)
        assert system["state"].get_intake_suggestion(again.intake_id)["suggestion_note"] == note
    finally:
        cm.__exit__(None, None, None)


def test_a_low_confidence_note_survives_human_reentry(tmp_path: Path) -> None:
    client = FakeClient(confidence=0.5)
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        worker.run_once()
        item = _item(system)
        assert len(client.calls) == 1 and item.classified_kind is None
        assert "LOW_CONFIDENCE" in system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("error,code", [
    (ClassifierError("CLASSIFIER_TIMEOUT"), "CLASSIFIER_TIMEOUT"),
    (ClassifierError("CLASSIFIER_INVALID_RESPONSE"), "CLASSIFIER_INVALID_RESPONSE"),
    (RequestTooLarge(), "S2_REQUEST_TOO_LARGE")])
def test_a_failure_reason_survives_human_reentry(tmp_path: Path, error, code) -> None:
    client = FakeClient(error=error) if code != "S2_REQUEST_TOO_LARGE" else FakeClient(too_large_times=99)
    cm, system, worker = _setup(tmp_path, client)
    try:
        worker.run_once()
        item = _item(system)
        before = system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"]
        assert code in before
        worker.run_once()
        worker.run_once()
        assert system["state"].get_intake_suggestion(item.intake_id)["suggestion_note"] == before
    finally:
        cm.__exit__(None, None, None)
