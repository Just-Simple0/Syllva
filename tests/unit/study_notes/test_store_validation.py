import pytest

from uls.study_notes.config import StudyNoteConfig
from uls.study_notes.core import StudyNoteSubmissionCore
from uls.study_notes.handler import StudyNoteHandler
from uls.study_notes.identity import CONFIRMED_LECTURE, PROVISIONAL_SELECTED, TRANSCRIPT_ONLY
from uls.study_notes.store import DifferentPayloadReplayError, StudyNoteStore
from uls.study_notes.validation import validate_draft_structure


@pytest.mark.parametrize(
    ("mode", "selected"),
    [
        (TRANSCRIPT_ONLY, ["COMP319-M03"]),
        (CONFIRMED_LECTURE, ["COMP319-M03"]),
        (PROVISIONAL_SELECTED, None),
        (PROVISIONAL_SELECTED, []),
    ],
)
def test_request_rejects_mode_shape_before_persistence(tmp_path, mode, selected):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        with pytest.raises(ValueError):
            core.request_study_note(
                idempotency_key="shape", caller_context="client", session_id="COMP319-S05",
                evidence_mode=mode, selected_materials=selected,
            )
        assert store.list_client_requests() == []
    finally:
        store.close()


@pytest.mark.parametrize("mode", [TRANSCRIPT_ONLY, CONFIRMED_LECTURE])
def test_empty_selected_materials_is_equivalent_to_absent_for_non_provisional_modes(tmp_path, mode):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        created = core.request_study_note(
            idempotency_key="empty-selection", caller_context="client", session_id="COMP319-S05",
            evidence_mode=mode, selected_materials=[],
        )
        replay = core.request_study_note(
            idempotency_key="empty-selection", caller_context="client", session_id="COMP319-S05",
            evidence_mode=mode, selected_materials=None,
        )
        assert replay == created
        row = store.get_client_request(created["client_request_id"])
        assert row is not None and row["selected_materials_json"] == "[]"
    finally:
        store.close()


def test_head_claim_does_not_publish_claimed_and_prepared_claim_clears_waiting_status(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        request = core.request_study_note(
            idempotency_key="claim", caller_context="client", session_id="COMP319-S05",
            evidence_mode=TRANSCRIPT_ONLY,
        )
        ref = request["client_request_id"]
        store.mark_client_request_head_claimed(
            ref, head_generation=1, session_provider_page_id="physical-session",
        )
        status = core.get_study_note_status(ref, caller_context="client")
        assert status["status"] == "PENDING"
        assert status["head_generation"] == 1
        assert "note_key" not in status

        store.set_client_request_status(ref, "WAITING_CONTEXT", reason="evidence unavailable")
        store.mark_client_request_claimed(
            ref, note_key="note", request_reference_id="reference",
            head_generation=1, attempt_no=1,
        )
        status = core.get_study_note_status(ref, caller_context="client")
        assert status["status"] == "CLAIMED"
        assert status["note_key"] == "note" and status["attempt_no"] == 1
        assert "reason" not in status
    finally:
        store.close()


def test_terminal_cancel_is_record_only_for_worker(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        core = StudyNoteSubmissionCore(store, config=StudyNoteConfig(enabled=True))
        request = core.request_study_note(
            idempotency_key="terminal", caller_context="client", session_id="COMP319-S05",
            evidence_mode=TRANSCRIPT_ONLY,
        )
        ref = request["client_request_id"]
        store.set_client_request_status(ref, "READY", artifact_link="https://example.invalid/note")
        core.cancel_study_note_request(ref, caller_context="client")
        handler = StudyNoteHandler(
            state=object(), store=store, engine=object(), drive_staging=object(), notion_bridge=object(),
            workspace="semester", clock=lambda: "2026-09-20T00:00:00+00:00", local_source_id="local",
            session_course_key=lambda _: "course", session_provider_page_id=lambda _: "physical",
            session_derived_folder_id=lambda _: "folder", template_version="template",
            generator_config_version="generator",
        )
        handler._apply_cancellations()
        status = core.get_study_note_status(ref, caller_context="client")
        assert status["status"] == "READY"
        assert status["cancel_requested"] is True
        assert status["artifact_link"] == "https://example.invalid/note"
    finally:
        store.close()


def test_prepared_context_is_immutable_and_exact_replay_is_idempotent(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        context = {"coverage": "FULL", "locators": ["COMP319-M03:p2"]}
        store.save_prepared_context(
            note_key="note", attempt_no=1, evidence_manifest_hash="manifest", context=context,
        )
        store.save_prepared_context(
            note_key="note", attempt_no=1, evidence_manifest_hash="manifest", context=context,
        )
        with pytest.raises(DifferentPayloadReplayError):
            store.save_prepared_context(
                note_key="note", attempt_no=1, evidence_manifest_hash="changed", context=context,
            )
        with pytest.raises(DifferentPayloadReplayError):
            store.save_prepared_context(
                note_key="note", attempt_no=1, evidence_manifest_hash="manifest",
                context={**context, "coverage": "PARTIAL"},
            )
    finally:
        store.close()


def test_reject_draft_consumes_reject_budget_once_per_draft(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    try:
        request = store.create_or_replay_client_request(
            idempotency_key="one", caller_context="client", payload_hash="payload",
            session_id="COMP319-S05", evidence_mode="transcript-only.v1",
            selected_materials=None, learner_request=None,
        )
        grant = store.issue_grant(
            client_request_id=request["client_request_id"], session_provider_page_id="physical",
            note_key="note", attempt_no=1, evidence_manifest_hash="manifest",
            head_generation=1, caller_context="client", ttl_seconds=3600,
        )
        draft, created = store.consume_grant_and_insert_draft(
            grant_id=grant["grant_id"], draft_text="invalid",
        )
        assert created is True
        assert store.reject_draft_once(draft["draft_id"], "bad structure") == 1
        assert store.reject_draft_once(draft["draft_id"], "bad structure") == 1
        assert store.get_reject_count(note_key="note", attempt_no=1) == 1
    finally:
        store.close()


def test_validator_uses_canonical_locator_parser_and_manifest_membership():
    base = (
        "# 학습 목표\nSOURCE {locator}\n"
        "# 핵심 개념\nAI 설명\n"
        "# 예제\nSOURCE {locator}\n"
        "# 오개념\n범위를 혼동하지 않기\n"
        "# 연습문제\n<details><summary>풀이</summary>AI 풀이</details>"
    )
    accepted = validate_draft_structure(
        base.format(locator="COMP319-M03:p2"),
        manifest_locators=["COMP319-M03:p2"],
    )
    assert accepted.accepted

    malformed = validate_draft_structure(
        base.format(locator="COMP319-M03:p0"),
        manifest_locators=["COMP319-M03:p2"],
    )
    assert not malformed.accepted
    assert "COMP319-M03:p0" in malformed.invalid_locators

    outside = validate_draft_structure(
        base.format(locator="COMP319-M03:p3"),
        manifest_locators=["COMP319-M03:p2"],
    )
    assert not outside.accepted
    assert "COMP319-M03:p3" in outside.invalid_locators
