# Syllva (ULS v1.2) Handoff

Updated: 2026-09-19. Authority: AGENTS.md (roles/review policy), university-learning-system-v1.2-design-frozen.md,
university-learning-system-v1.2-implementation-spec-frozen.md (v1.2 core), docs/ux/intake-execution-contract.md
(rev10, additive, accepted-not-implemented). Git log + .review/* (gitignored) = source of truth for shipped work.
This file = active/resumable state only, no narrative history.

Branch: codex/protected-secret-file-and-credential-set. No push to protected branches without explicit user instruction.

## rev10 C-series order: C1 -> C4 -> C2 -> C3 -> C5 -> C6 -> C7 -> C8
Each: plan -> independent PLAN review -> implement -> test -> independent FINAL review -> commit.
Scope of each C-label: docs/ux/intake-execution-contract.md section 9 table.

C1 [DONE, commit 542e1d4]: durable study-note state/storage. study_note_heads/note_jobs/note_attempts/
note_request_references/note_artifacts, atomic attach_note_request/transition_note_attempt/
deactivate_study_note_head with TOCTOU guards. Tests: tests/unit/test_c1_state.py.

C4 [implemented, tested, PLAN not yet independently reviewed via correct channel -- see below]:
pre-canonical semester intake. Gap: classify_source_detailed (src/uls/ingestion/classifier.py) used a
timestamp regex narrower than the normalizer's actual grammar. Fix: added contains_timestamp_marker()
to src/uls/normalization/transcript.py, delegating to extract_timestamp_marks() (single source of
truth); classifier now calls it. Uncommitted: classifier.py, transcript.py, new
tests/unit/test_c4_classifier_timestamp_grammar.py (16 tests), new tests/unit/test_c4_route_intake_gate.py
(9 tests). Full pytest: 1488 passed, 3 skipped, 0 failed. Behavior Contract hash unchanged. git diff
--check clean. Plan: .review/c4-verification-plan.md.

IMPORTANT correction: an earlier pass in this session mistakenly used mcp__codex_app__create_thread /
send_message_to_thread (a Codex-app ChatGPT-project thread, consumes Codex's own message quota) instead
of the actual insane-review tool, and incorrectly recorded a "PLAN GO" from that wrong channel. That
result is invalid and must not be treated as a genuine independent review. Real insane-review PLAN+FINAL
for C4 has not been completed as of this update.

insane-review status: --model pro is not currently selectable in the ChatGPT UI (menu shows Pro but
selecting it does not take effect; actual state stays "GPT-5.6 Sol" / effort slider "very high"). Per
AGENTS.md this authorizes the fallback --model "very high" (Korean UI label: 매우 높음), recording the
actual model/effort and fallback reason (already done here). A repomix pack + send was in flight via
this fallback when this file was last updated; check .insane-review/response_*.md and
.insane-review/manifest_*.json for the outcome before resending. Never resend if a manifest exists.

Resume: get a genuine insane-review PLAN GO (and then FINAL GO) for C4 using --model "very high" with
the fallback reason recorded, using --include listing exactly: src/uls/ingestion/classifier.py,
src/uls/normalization/transcript.py, tests/unit/test_c4_classifier_timestamp_grammar.py,
tests/unit/test_c4_route_intake_gate.py, .review/c4-verification-plan.md,
docs/ux/intake-execution-contract.md. Then commit C4, then start C2.

C2 [not started]: reserve_session_entity/reserve_material_entity/apply_session_binding/
apply_material_binding as explicit named APIs (currently only generic C1 reserve_entity/
update_entity_reservation primitives exist, wrapped ad hoc in src/uls/intake/worker.py's
_reserve_session/_reserve_material/_ensure_folder_with_attempt); explicit section-13 DriveAdapter
marker-aware create/search/readback extension (currently a provider-neutral port inside worker.py, not
on the adapter); remove legacy N-lecture ID-suffix-guessing from the resolver (section 4.1 last
paragraph). Read intake-execution-contract.md sections 3.4 and 4.1-4.3 fully before planning.
Files to extend: src/uls/adapters/drive/{base,google,binding}.py, src/uls/intake/worker.py.

C3/C5/C6/C7/C8 [not started]: see intake-execution-contract.md section 9 table.

## insane-review usage (the only correct review channel)
Tool: python3 /Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/*/bin/pack_and_ask.py
Always use real repomix-attached code (--target + --include), never a Codex-app chat thread.
Requires sandbox_permissions=require_escalated on every call (network + browser access).
Run one call per attempt to completion; if it returns a session_id, poll it via tools.write_stdin in
code-mode (custom_exec) with numeric session_id/yield_time_ms as actual JS numbers -- calling the raw
top-level write_stdin tool directly errored with a type-parsing bug on numeric args in this session.
Never background with & (child process does not survive tool-call/session boundaries here).
On repomix network error, retry the exact same call with sandbox_permissions=require_escalated; the
earlier failure in this session was because that permission was omitted, not a real network outage.
If a manifest_*.json exists but no response, use --harvest <manifest_path>; never resend.
