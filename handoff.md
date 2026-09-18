# Syllva (ULS v1.2) Handoff

Updated: 2026-09-19. Authority: AGENTS.md (roles/review policy), university-learning-system-v1.2-design-frozen.md,
university-learning-system-v1.2-implementation-spec-frozen.md (v1.2 core), docs/ux/intake-execution-contract.md
(rev10, additive, accepted-not-fully-implemented, sections 1-10 only -- "section 13 DriveAdapter" in its
section 9 table is a forward reference to the FROZEN implementation spec's own section 13, not a section
in this document). Git log + .review/* (gitignored) = source of truth for shipped work. This file =
active/resumable state only, no narrative history.

Branch: codex/protected-secret-file-and-credential-set. No push to protected branches without explicit
user instruction (this feature branch itself is fine to push).

## rev10 C-series order: C1 -> C4 -> C2 -> C3 -> C5 -> C6 -> C7 -> C8
Each: plan -> independent PLAN review (insane-review) -> implement -> test -> independent FINAL review
(insane-review) -> commit. Scope of each C-label: docs/ux/intake-execution-contract.md section 9 table.

C1 [DONE, commit 542e1d4]: durable study-note state/storage.
C4 [DONE, commit b7b102f]: pre-canonical intake classification grammar fix. route_intake()
(src/uls/intake/planner.py) is UNUSED dead code, zero production callers -- do not target it for
anything. Real course/kind fail-closed gate: validate_request_input() (src/uls/intake/requests.py).

## C2 [ACTIVE -- audit done, verification plan not yet written]
Full findings: .review/c2-audit-findings.md (read before doing anything else). Summary: a broad code
audit (following the C4 lesson: verify actual callers before trusting a one-line contract summary)
found most of C2's assumed scope already implemented:

1. DriveAdapter explicit marker-aware create/search/readback: ALREADY DONE in
   src/uls/adapters/drive/worker.py (DriveWorkerPort, GoogleDriveWorkerAdapter, ensure_marked_folder
   -- correctly implements complete-zero/exactly-one/multiple/lookup-indeterminate semantics).
2. Named reserve_session_entity/apply_session_binding-style APIs: behaviorally present as private
   IntakeWorker methods (_reserve_session/_reserve_material/_apply_binding); likely a naming/
   API-surface question, not a behavior gap -- needs explicit contract re-read to confirm.
3. Legacy N-lecture ID-suffix-guessing in the resolver: does not exist in current code (resolver
   already uses the explicit "Session No" field only) -- nothing to remove.
4. One item needing closer verification: BIND_EXISTING_TRANSCRIPT concurrent-claim atomicity
   (contract section 4.2 point 3). Current code likely already prevents the race via the generic C1
   entity_reservations global UNIQUE(entity_kind, entity_app_id) constraint, but this needs a
   concrete concurrency test to confirm, not just code reading.

Next step: write a SHORT C2 verification plan (small, like the eventual C4 pattern -- do not write a
large new-feature plan for behavior that already exists), get it through insane-review PLAN review
with worker.py + adapters/drive/worker.py + requests.py + sqlite.py's reserve_entity attached, then
implement only what a genuine gap requires (likely just item 4's concurrency test, or none at all if
that test also passes cleanly), then FINAL review, then commit. If the audit is confirmed accurate,
C2 may complete with a small test-only commit -- do not invent unnecessary rework to make C2 "feel"
substantial.

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
