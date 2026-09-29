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
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


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
        assert not re.search(r"https?://", text)
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
    for group in ("Connections", "Academic", "Automation", "Remote access"):
        card = re.search(rf'<article class="deferred-card"><span>{group}</span>.*?</article>', page)
        assert card and "button" not in card.group(0)
    assert 'type="password"' not in page
