"""Browser shell behavior: old-tab states, drafts, stepper, and static asset rules."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

STATIC = Path(__file__).resolve().parents[2] / "src" / "uls" / "settings" / "static"
HARNESS = Path(__file__).with_name("settings_ui_harness.cjs")
NODE = shutil.which("node")


def _scenario(name: str) -> dict:
    if NODE is None:
        pytest.skip("node is required to run the Settings UI harness")
    proc = subprocess.run([NODE, str(HARNESS), str(STATIC), name], check=False,
                          capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("scenario", ["credential_cancel", "credential_failure"])
def test_write_only_secret_cleared_on_cancel_and_failure(scenario):
    result = _scenario(scenario)
    assert result["focusOnOpen"] == "credential-secret"
    assert result["secretValue"] == "" and result["dialogHidden"]
    assert not result["secretInUrl"] and not result["secretInText"]
    assert result["secretPosts"] == (1 if scenario == "credential_failure" else 0)


def test_failed_credential_verification_focuses_row_and_retries_with_empty_secret():
    result = _scenario("credential_failure")
    assert result["focusedAfterCredential"] == "credential-heading-notion-mcp"
    assert "verification failed" in result["credentialStatus"].lower()
    assert result["retryLabel"] == "Try again"
    assert result["focusOnRetry"] == "credential-secret" and result["secretEmptyOnRetry"]


def test_successful_credential_save_focuses_live_status_row():
    result = _scenario("credential_success")
    assert result["focusedAfterCredential"] == "credential-status-notion-mcp"
    assert result["credentialStatus"] == "Credential saved and verified."


def test_failed_credential_replacement_offers_replacement_retry():
    result = _scenario("credential_replace_failure")
    assert result["focusedAfterCredential"] == "credential-heading-notion-mcp"
    assert result["retryLabel"] == "Retry replacement"
    assert result["focusOnRetry"] == "credential-secret" and result["secretEmptyOnRetry"]


@pytest.mark.parametrize("scenario", ["credential_session_replaced", "credential_session_expired", "credential_session_unreachable"])
def test_credential_post_session_end_keeps_alert_focus_and_sends_no_followup_request(scenario):
    result = _scenario(scenario)
    assert result["postCount"] == 1 and result["callsAfterPost"] == 0
    assert result["focusedId"] == "connection-heading" and result["connectionHidden"] is False
    assert result["connectionStateInert"] is False
    assert result["connectionStateAriaDisabled"] is None
    assert result["headingAncestorInert"] is False
    assert result["endedWorkAreasInert"] is True
    assert result["secretValue"] == "" and result["secretAnywhere"] is False
    assert result["callsAfterEnd"] == 0


@pytest.mark.parametrize(("scenario", "expected"), [
    ("google_file_missing", "Choose a service-account JSON file."),
    ("google_file_oversize", "Key file is larger than 64 KB."),
])
def test_google_file_errors_are_inline_associated_and_focused_without_post(scenario, expected):
    result = _scenario(scenario)
    assert result["focusOnOpen"] == "credential-file"
    assert result["fileError"] == "Error: " + expected and result["fileErrorVisible"]
    assert result["fileErrorId"] == "credential-file-error" and result["fileInvalid"] == "true"
    assert result["fileFocus"] == "credential-file" and result["filePosts"] == 0


def test_google_file_read_cancel_prevents_delayed_post_and_clears_secret():
    result = _scenario("google_file_cancel")
    assert result["cancelEnabledDuringRead"] and result["dialogHiddenAfterCancel"]
    assert result["filePostsAfterCancel"] == 0 and result["secretValueAfterCancel"] == ""


def test_google_file_read_error_uses_fixed_redacted_field_message():
    result = _scenario("google_file_read_error")
    assert result["fileError"] == "Error: The selected service-account file could not be read."
    assert result["fileErrorRedacted"] and result["fileFocus"] == "credential-file"
    assert result["filePosts"] == 0


def test_late_cancelled_file_read_cannot_overwrite_new_submission_cancel_state():
    result = _scenario("google_file_cancel_replace_race")
    assert result["cancelEnabledBeforeLateA"] and result["cancelEnabledAfterLateA"]
    assert result["dialogVisibleAfterLateA"] and result["dialogHiddenAfterCancelB"]
    assert result["postsAfterBothReads"] == 0 and result["secretFieldAfterBothReads"] == ""


def test_linux_card_names_environment_variable_and_disables_unset_test():
    result = _scenario("linux_card")
    assert "NOTION_MCP_TOKEN" in result["text"] and result["testDisabled"]
    assert "Provided by NOTION_MCP_TOKEN" not in result["text"]


def test_unset_environment_credential_is_not_labelled_provided():
    result = _scenario("credential_environment_absent")
    assert "Provided by" not in result["text"]
    assert result["testDisabled"]


def test_bad_canvas_origin_stays_inline_before_token_dialog():
    result = _scenario("canvas_origin")
    assert result["invalid"] == "true" and result["focus"] == "canvas-origin"
    assert result["dialogHidden"] and "Error:" in result["message"]


def test_canvas_forget_clears_profile_bound_discovery_selection_and_preview():
    result = _scenario("canvas_forget")
    assert result["previewBefore"]
    assert "https://canvas.example.edu" in result["forgetText"]
    assert "S•••••• N••" in result["forgetText"] and "User ID: 12345" in result["forgetText"]
    assert "this Mac's Keychain" in result["forgetText"] and "access lease" in result["forgetText"]
    assert result["courseAreaHidden"] and result["previewHidden"]
    assert result["courses"] == result["terms"] == ""
    assert result["count"] == "0 of 20 selected" and result["status"] == "Not configured"


def test_fake_canvas_forget_names_its_test_store():
    result = _scenario("canvas_forget_fake")
    assert "the fake test store" in result["forgetText"]
    assert "S•••••• N••" in result["forgetText"] and "User ID: 12345" in result["forgetText"]


def test_credential_forget_names_exact_role_and_fake_store():
    result = _scenario("credential_fake_forget")
    assert result["roleInForget"] is True
    assert result["fakeStoreInForget"] is True


def test_canvas_term_change_dialog_preserves_until_explicit_confirmation():
    result = _scenario("canvas_term_dialog")
    assert result["dialogOpen"] and result["cancelInitiallyFocused"] and result["panelInert"]
    assert result["topbarInert"] and result["fullBackgroundInert"]
    assert result["forcedBackgroundCalls"] == 0 and result["modalStayedOpenAfterForcedBackground"]
    assert result["connectionsStayedVisible"]
    assert result["previousTermRestored"] and result["selectionPreservedOnOpen"]
    assert result["shiftTabWrapped"] and result["tabWrapped"]
    assert result["cancelRestored"]
    assert result["confirmChangedTerm"] and result["confirmClearedSelection"] and result["confirmReturnedFocus"]
    assert "Term 21; 0 courses selected" in result["confirmedTermDraft"]
    assert result["confirmedTermBeforeunload"] == 1


def test_credential_dialog_traps_focus_through_its_active_canvas_link():
    result = _scenario("canvas_link_focus")
    assert result["dialogVisible"] and result["topbarInert"] and result["fullBackgroundInert"]
    assert result["forcedBackgroundCalls"] == 0 and result["modalStayedOpen"] and result["fullBackgroundStillInert"]
    assert result["tabFromCancelReachedLink"] and result["shiftTabFromLinkReachedCancel"]
    assert result["activeDialogLinkAllowed"]


def test_saved_canvas_registry_is_rendered_on_reopen_and_needs_fresh_discovery_to_review():
    result = _scenario("canvas_saved")
    assert result["savedTerm"] == "20" and result["savedTermLabel"] == "Saved term 20"
    assert result["savedTermDisabled"] is True
    assert "Saved course" in result["savedRows"] and "ID 10" in result["savedRows"]
    assert result["savedChecks"] == [{"id": "10", "checked": True, "disabled": True}]
    assert result["savedCount"] == "1 of 20 saved" and result["reviewDisabled"] is True
    assert result["savedCleanDraft"] == "" and result["savedCleanBeforeunload"] == 0


def test_canvas_selection_caps_forced_twenty_first_course_in_review_and_apply_bodies():
    result = _scenario("canvas_limit20")
    assert result["twentySelected"] and result["twentyFirstDisabled"] and result["twentyFirstStillDisabled"]
    assert result["selectedAfterForcedTwentyFirst"] == 20
    assert result["disabledAfterFailedRediscovery"] and result["twentySelectedAfterFailedRediscovery"] == 20
    assert result["twentyOneStillBlockedAfterFailure"]
    assert result["selectedAfterFreshRediscovery"] == 20 and result["twentyFirstDisabledAfterFreshRediscovery"]
    assert result["validationCourseCounts"] == [20, 20]
    assert result["applyCourseCounts"] == [20]


@pytest.mark.parametrize("scenario", ["canvas_failed_discover", "canvas_failed_test", "canvas_failed_renew"])
def test_failed_canvas_checks_invalidate_controls_but_preserve_the_draft(scenario):
    result = _scenario(scenario)
    assert result["termDisabledAfterFailure"] and result["checkboxDisabledAfterFailure"]
    assert result["reviewDisabledAfterFailure"]
    assert result["selectedAfterFailure"] == result["selectedAfterForcedEdit"] == 1
    assert result["selectionCountAfterFailure"].startswith("1 of 20")
    assert result["termAfterFailure"] == "20"
    assert result["validateCallsAfterForcedReview"] == result["validateCallsBeforeForcedEvents"] == 0


def test_busy_sync_does_not_discard_a_valid_late_discovery_response():
    result = _scenario("canvas_slow_discovery")
    assert result["disabledDuringRequest"] and result["checkboxesDisabledDuringRequest"]
    assert result["discoveryAccepted"] and result["reviewStillRequiresSelection"]


def test_canvas_apply_reads_saved_snapshot_and_keeps_other_unsaved_settings():
    result = _scenario("canvas_apply_readback")
    assert result["previewShown"]
    assert len(result["applyBodies"]) == 1
    assert result["applyBodies"][0]["course_ids"] == ["12"]
    assert result["savedTerm"] == "20" and result["savedTermLabel"] == "Saved term 20"
    assert "Second course" in result["savedRows"] and "ID 12" in result["savedRows"]
    assert result["savedChecks"] == [{"id": "12", "checked": True, "disabled": True}]
    assert result["savedCount"] == "1 of 20 saved" and result["reviewDisabled"] is True
    assert result["timezoneDraft"] == "Europe/Paris"
    assert "reloaded from Settings" in result["notice"]
    assert result["reopenedTerm"] == "20" and "Second course" in result["reopenedRows"]
    assert result["reopenedCount"] == "1 of 20 saved" and result["reopenedReviewDisabled"] is True


def test_canvas_apply_does_not_claim_readback_when_saved_snapshot_cannot_be_loaded():
    result = _scenario("canvas_apply_readback_failed")
    assert len(result["applyBodies"]) == 1
    assert "could not be confirmed" in result["notice"]
    assert "Confirming" in result["readbackPending"]
    assert result["readbackCount"] == "Checking saved selection…"
    assert result["readbackReviewDisabled"] is True


def test_canvas_selection_requires_at_least_one_course():
    result = _scenario("canvas_minimum_one")
    assert result["reviewDisabled"] and result["validateCalls"] == 0
    assert result["previewHidden"] and "0 selected courses" not in result["previewText"]


def test_fresh_discovery_invalidates_a_prior_review_and_blocks_its_apply():
    result = _scenario("canvas_review_invalidated")
    assert result["reviewShown"] is True
    assert result["reviewInvalidatedAfterDiscovery"] is True
    assert result["applyCallsAfterInvalidation"] == 0


def test_older_canvas_snapshot_cannot_replace_a_newer_response():
    result = _scenario("canvas_stale_snapshot")
    assert result["newerResponseVisible"] and result["olderResponseIgnored"]


@pytest.mark.parametrize(("scenario", "heading"), [
    ("replaced", "Settings session moved"),
    ("unreachable", "Settings session closed"),
    ("unreachable_poll", "Settings session closed"),
    ("expired", "Settings session expired"),
    ("not_found", "Settings session closed"),
    ("csrf_rejected", "Settings session closed"),
])
def test_old_tab_transitions_keep_drafts_and_drop_secrets(scenario, heading):
    out = _scenario(scenario)
    assert out["startedEnabled"] is True
    assert out["heading"] == heading
    assert out["connectionHidden"] is False and out["connectionRole"] == "alert"
    assert out["focusedId"] == "connection-heading"
    assert out["timezoneValue"] == "Europe/Paris" and out["timezoneReadOnly"] is True
    assert "Europe/Paris" in out["draftText"]
    assert out["secretValue"] == "" and out["secretAnywhere"] is False
    assert out["mutationsDisabled"] is True
    assert out["callsAfterEnd"] == 0
    assert out["beforeunload"] == 1
    assert out["csrfInUrl"] is False


def test_beforeunload_only_when_unsaved_entries_exist():
    out = _scenario("clean_replaced")
    assert out["heading"] == "Settings session moved"
    assert out["beforeunload"] == 0
    assert out["focusedId"] == "connection-heading"


@pytest.mark.parametrize("scenario", ["replaced", "unreachable_poll", "expired", "not_found", "csrf_rejected"])
def test_ended_tab_disables_every_control_and_makes_work_area_inert(scenario):
    out = _scenario(scenario)
    assert out["allButtonsDisabled"] is True
    assert out["inertRegions"] == ["setup-stepper", "overview-panel", "general-panel", "advanced-panel"]
    # Even force-dispatched clicks on recheck, stepper, nav cards and back
    # links make no request and change no step or panel state.
    assert out["callsAfterEnd"] == 0
    assert out["stepsUnchanged"] is True
    assert out["generalHiddenAfter"] is False


def test_every_field_error_is_associated_first_is_focused_and_each_clears_on_its_own_edit():
    out = _scenario("field_error")
    assert out["ariaInvalid"] == "true"
    assert out["describedBy"] == "max-candidate-entities-error"
    assert out["messageText"] == "Error: Enter a whole number from 1 to 500."
    assert out["messageFollowsInput"] is True
    assert out["focusedId"] == "max-candidate-entities"  # first invalid field in DOM order
    assert out["secondInvalid"] == "true" and out["secondDescribedBy"] == "max-candidate-chunks-error"
    assert out["thirdInvalid"] is None
    assert out["notice"] == "2 settings need attention. Each one is marked below."
    assert out["ariaInvalidAfterEdit"] is None and out["describedByAfterEdit"] is None
    assert out["messageRemoved"] is True
    assert out["secondStillInvalid"] == "true"


@pytest.mark.parametrize("scenario", ["edit_after_review", "revert_after_review"])
def test_input_change_after_review_invalidates_the_review(scenario):
    out = _scenario(scenario)
    assert out["previewShownAfterReview"] is True
    assert out["previewShownBeforeApply"] is False
    assert out["applyBodies"] == []
    assert out["notice"] == "Review the change before applying it."


def test_apply_sends_exactly_the_reviewed_candidate():
    out = _scenario("apply_reviewed")
    assert out["applyBodies"] == [{"values": {"system.timezone": "UTC"}, "generation": "g-Asia/Seoul",
                                   "candidate_hash": "c" * 64}]
    assert out["notice"] == "Saved. The settings file now contains the reviewed values."


def test_generation_change_after_review_requires_a_new_review():
    out = _scenario("generation_after_review")
    assert len(out["applyBodies"]) == 1
    assert out["applyCallsAfterConflict"] == 1
    assert out["previewShownAfter"] is False
    assert out["timezoneValue"] == "UTC"


def test_review_and_apply_are_busy_and_announced_while_requests_run():
    out = _scenario("busy")
    assert out["busyAttr"] == "true"
    assert out["submitDisabledWhileBusy"] is True and out["applyDisabledWhileBusy"] is True
    assert out["busyNotice"] == "Checking the change…" and out["noticeLive"] == "polite"
    assert out["validateCallsWhileBusy"] == 1
    assert out["busyAfter"] == "false" and out["submitDisabledAfter"] is False
    assert out["doneNotice"] == "Review ready. Check the change, then apply it."


def test_a_stale_validation_response_never_publishes_a_review():
    out = _scenario("edit_during_review")
    assert out["previewShown"] is False
    assert out["notice"] == "An entry changed while it was being checked. Review it again."
    assert out["applyCalls"] == 0


def test_readiness_uses_human_labels_and_folds_notes():
    out = _scenario("readiness")
    assert out["terms"] == ["AI client", "Retrieval credentials", "Source files saved", "Text extraction"]
    assert all("_" not in text and "note" not in text.lower() for text in out["terms"])
    assert out["details"][0] == "Not proven" + "Requires human confirmation through actual AI client use"
    assert all("_" not in text for text in out["details"])


def test_close_with_unsaved_entries_asks_first_and_cancel_keeps_session():
    out = _scenario("close_confirm")
    assert out["dialogVisible"] is True and out["dialogRole"] == "alertdialog"
    assert out["dialogFocus"] == "close-confirm-heading"
    assert out["callsWhileAsking"] == 0
    assert out["dialogAfterCancel"] is False and out["focusAfterCancel"] == "close-session"
    assert out["stillActive"] is True
    assert out["closeCalls"] == 1
    assert out["heading"] == "Settings session ended"
    assert out["beforeunload"] == 0


def test_close_without_unsaved_entries_closes_directly():
    out = _scenario("close_clean")
    assert out["dialogVisible"] is False and out["closeCalls"] == 1
    assert out["heading"] == "Settings session ended"


def test_csrf_travels_only_in_the_mutation_header():
    out = _scenario("replaced")
    assert out["mutationCsrfHeaders"] == ["csrf-test-token-value"]
    assert all("csrf-test-token-value" not in url for url in out["urls"])


def test_generation_conflict_keeps_dirty_values():
    out = _scenario("conflict")
    assert out["connectionHidden"] is True
    assert out["timezoneValue"] == "Europe/Paris" and out["timezoneReadOnly"] is False
    assert "changed elsewhere" in out["notice"]


def test_back_navigation_marks_dependent_steps_partial_without_clearing():
    out = _scenario("stepper")
    assert out["forwardStates"] == ["Ready"] * 6
    assert out["currentAfter"] == 1
    assert out["afterStates"] == ["Ready", "Ready", "Partial", "Partial", "Partial", "Partial"]
    assert out["timezoneValue"] == "Europe/Paris"


def test_idle_warning_then_local_expiry():
    out = _scenario("idle")
    assert out["warningVisible"] is True and out["warningFocused"] == "idle-warning-heading"
    assert out["heading"] == "Settings session expired"
    assert out["timezoneValue"] == "Europe/Paris"


def test_static_assets_use_no_storage_remote_assets_or_inline_script():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                      "serviceWorker", "innerHTML", "console.", "eval(", "postMessage"):
        assert forbidden not in script
    for text in (page, styles, script):
        # No remote assets; Canvas's address example and validated account
        # settings link are user-facing guidance, not fetched resources.
        assert not re.search(r'(?:src|href)="https?://', text)
        assert "@import" not in text
    assert "<script>" not in page and "onclick=" not in page
    # Every URL is relative to the per-launch prefix.
    assert '"/api' not in script and "'/api" not in script
    assert 'href="/' not in page.replace('href="#', "") and 'src="/' not in page
    assert 'role="alert"' in page and 'aria-live="polite"' in page
    for label in ("Settings session moved", "Unsaved entries (read-only; selectable)",
                  "Secret fields were cleared and are never retained or shown here."):
        assert label in page
    for message in ("A new Settings window was opened. This window can no longer save.",
                    "This window can no longer connect to Syllva Settings or save changes."):
        assert message in script


def test_deferred_groups_expose_no_mutation_controls():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    for group in ("Academic", "Automation", "Remote access"):
        card = re.search(rf'<article class="deferred-card"><span>{group}</span>.*?</article>', page)
        assert card and "button" not in card.group(0)
    assert 'id="credential-secret" type="password" data-secret' in page
    assert 'autocomplete="new-password"' in page
    assert 'id="credential-file-error"' in page
