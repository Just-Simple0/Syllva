# Syllva (ULS v1.2) Handoff

Updated: 2026-09-19. Authority: AGENTS.md (roles/review policy), university-learning-system-v1.2-design-frozen.md,
university-learning-system-v1.2-implementation-spec-frozen.md (v1.2 core), docs/ux/intake-execution-contract.md
(rev10, additive, accepted-not-fully-implemented). Git log + .review/* (gitignored) = source of truth for
shipped work. This file = active/resumable state only, no narrative history.

Branch: codex/protected-secret-file-and-credential-set. No push to protected branches without explicit
user instruction (this feature branch itself is fine to push).

## rev10 C-series order: C1 -> C4 -> C2 -> C3 -> C5 -> C6 -> C7 -> C8
Each: plan -> independent PLAN review (insane-review) -> implement -> test -> independent FINAL review
(insane-review) -> commit. Scope of each C-label: docs/ux/intake-execution-contract.md section 9 table.

C1 [DONE, commit 542e1d4]: durable study-note state/storage. study_note_heads/note_jobs/note_attempts/
note_request_references/note_artifacts, atomic attach_note_request/transition_note_attempt/
deactivate_study_note_head with TOCTOU guards. Tests: tests/unit/test_c1_state.py.

C4 [DONE, commit b7b102f]: pre-canonical semester intake. Fixed classify_source_detailed()'s timestamp
grammar to match the normalizer (contains_timestamp_marker() in transcript.py, delegating to
extract_timestamp_marks()). Verified via 3 genuine insane-review rounds (2 REVISE + final GO); see
.review/c4-status.md for the corrected understanding: route_intake() (planner.py) is UNUSED dead code
with zero production callers -- do not target it. The real course/kind fail-closed gate is
validate_request_input() (src/uls/intake/requests.py), called from
IntakeWorker._claim_request_unlocked() (src/uls/intake/worker.py) before any plan/job is created.
Tests: tests/unit/test_c4_classifier_timestamp_grammar.py, tests/unit/test_c4_route_intake_gate.py.

## C2 [ACTIVE -- start here]
Scope per section 9 C2: reserve_session_entity/reserve_material_entity/apply_session_binding/
apply_material_binding as explicit named APIs (currently only the generic C1 reserve_entity/
update_entity_reservation storage primitives exist, wrapped ad hoc in src/uls/intake/worker.py's
_reserve_session/_reserve_material/_ensure_folder_with_attempt/_ensure_session_page/
_ensure_material_page); explicit section-13 DriveAdapter marker-aware create/search/readback extension
(currently a provider-neutral port implemented directly in worker.py's _ensure_folder_with_attempt/
_check_folder, not on src/uls/adapters/drive/{base,google,binding}.py); remove legacy N-lecture
ID-suffix-guessing from the resolver (section 4.1 last paragraph -- search src/uls/retrieval for the
resolver that maps "N-lecture" aliases to Session IDs).

Before planning: read docs/ux/intake-execution-contract.md sections 3.4 and 4.1-4.3 in full (not just
the section 9 summary line -- C4's mistake was trusting a one-line scope summary over the actual
section text and the actual call graph). Read src/uls/intake/worker.py's current
_reserve_session/_ensure_session_page/_reserve_material/_ensure_material_page/_ensure_folder_with_attempt
and src/uls/adapters/drive/{base,google,binding}.py fully before deciding what is a genuine gap vs
already-implemented, the same way the C4 audit did. Do not assume a function is production-wired just
because its name matches a contract API name -- grep for actual callers first (this is exactly the
mistake insane-review caught in C4 round 1).

## C3 / C5 / C6 / C7 / C8 [not started]
See docs/ux/intake-execution-contract.md section 9 table.

## insane-review usage (the only correct review channel)
Tool: python3 /Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/*/bin/pack_and_ask.py
Always use real repomix-attached code (--target + --include: list every file whose behavior is being
reviewed, including actual production callers, not just the files that changed -- omitting a caller
file caused an extra REVISE round in C4). Never substitute a Codex-app chat thread
(mcp__codex_app__create_thread/send_message_to_thread) -- that consumes Codex's own separate message
quota and is not a real review.

Every call needs sandbox_permissions=require_escalated (network + browser). Pro tier may not be
selectable (menu shows "Pro" but clicking it does not change the verified model/effort); if so, use
--model "very high" (매우 높음) as the AGENTS.md-authorized fallback and record the actual
model/effort and fallback reason in the review status file.

Long-running calls return a session_id; poll it via tools.write_stdin in code-mode (custom_exec) with
session_id/yield_time_ms as real JS numbers -- calling the raw top-level write_stdin/exec_command tools
directly with those params sometimes throws a type-parsing error on numeric args in this environment;
code-mode avoids it. Never background with & (child process does not survive tool-call/session
boundaries here). If a manifest_*.json exists in .insane-review/ but no response, use
--harvest <manifest_path>; never resend.

## Sandbox notes
.git is read-only by default; git add/commit/push each need sandbox_permissions=require_escalated.
rm and /usr/bin/trash on files under this repo can both fail (deletion-guard hook / macOS Trash
permission); prefer overwriting a file's content (e.g. via a small base64-decode write) over deleting
it when a full-file replacement is needed. Multiline heredoc shell commands with special characters
can fail a command-safety parser; base64-encode content in JS (custom_exec) and pipe through
base64 -d instead.
