# Syllva (ULS v1.2) Handoff

Updated: 2026-09-19. Authority: AGENTS.md (roles/review policy), university-learning-system-v1.2-design-frozen.md,
university-learning-system-v1.2-implementation-spec-frozen.md (v1.2 core, frozen -- do not edit),
docs/ux/intake-execution-contract.md (rev10, additive, accepted-not-fully-implemented, sections 1-10 only).
Git log + .review/* (gitignored) = source of truth for shipped work. This file = active/resumable
state only, no narrative history.

Branch: codex/protected-secret-file-and-credential-set. No push to protected branches without explicit
user instruction (this feature branch itself is fine to push).

## rev10 C-series: C1 -> C4 -> C2 -> C8 -> C3 -> C7 -> C5 (active) -> C6
Each: plan -> independent PLAN review (insane-review) -> implement -> test -> independent FINAL review
(insane-review) -> commit.

C1 [DONE, 542e1d4]: durable study-note state/storage substrate (study_note_heads/note_jobs/
note_attempts/note_request_references/note_artifacts). C6 builds behavior on top of this.
C4 [DONE, b7b102f]: classifier timestamp-grammar fix. route_intake() (src/uls/intake/planner.py) is
UNUSED dead code -- never target it. Real gate: validate_request_input() (src/uls/intake/requests.py).
C2 [DONE, 970d34d]: ensure_marked_folder() private-ownership fix (src/uls/adapters/drive/worker.py).
C8 [DONE, 02bebff]: poll_interval_minutes default 10 -> 1 minute.
C3 [DONE, 00067fb]: Automation Queue Proposal Envelope documented as an operator setup prerequisite
(docs/operator-guide/intake-and-notion.md + .ko.md). No code location exists to attach a schema
registration to (Queue has no INTAKE_SCHEMAS-equivalent); C5 owns the actual v2 envelope logic.
C7 [DONE, 1b17805]: semester dashboard / personal schedule-todo linked view. Discovery: the real
Notion-native dashboard satisfying this exact requirement was already built and GO-reviewed on
2026-09-13, BEFORE this C-series (docs/ux/dashboard-native-application.md, dashboard-native-readback.md,
review-20260913-native-dashboard.md) -- a prior audit missed this by only grepping src/. Documented as
a "Live status" note in the same two operator-guide files, carefully scoped to NOT claim the 2026-09-13
applied section order equals the current rev10 §7 order (they differ), and NOT claim the future
worker-mapping gap is closed. Zero src/ code required or changed for this contract row.

## C5 [ACTIVE -- large, plan round 7 submitted, D2 only] PageRange/Usage producer v2 envelope
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Do NOT write implementation code
before a plan round gets GO.

**Plan history**: rounds 1-6 closed Blockers A, B, C, D1, E, F, G (all CONFIRMED CLOSED, round 6
response: .insane-review/response_src_20260919_160522_12406_3981e1.md). Round 6 confirmed D2-2's core
split (permanent current_usage_app_id vs transient reservation_state) matches the real code, but found
6 precise state-machine wording/race gaps in D2-1/D2-3. **Round 7 (c5-plan-v7.md) addresses exactly
those 6, submitted, awaiting result as of this handoff write.**

**Round 7's 6 fixes** (all narrow refinements of the SAME reservation-before-provider-write design, no
new design categories):
1. Drop `BOUND` as a stored `reservation_state` value entirely -- both success and positive
   reconciliation go directly to `reservation_state='NONE'` + `current_usage_app_id=<target>` in one
   CAS. "Bound" is descriptive prose only, never a stored enum value (round 6 found this contradicted
   D2-2's own "terminal always returns to NONE" rule).
2. The `RESERVED->DISPATCHED` CAS LOSER must make ZERO writes to the shared head row -- only the
   dispatch OWNER may ever write `DISPATCHED -> RECONCILE_REQUIRED`. (Round 6's core race: a loser
   seeing an empty readback while the owner's create is still in-flight must not poison the shared
   state by flipping it to RECONCILE_REQUIRED.)
3. Terminal-CAS idempotency: if `current_usage_app_id` is already the exact target this caller expected
   (someone else's CAS already won), treat as success even if this caller's own CAS affects 0 rows.
4. Direct release to `NONE` needs BOTH `ProviderWriteNotAppliedError` AND an authoritative exact-old/
   no-row-exists re-read -- matching HAA's own existing "old"-confirmation standard exactly (round 6:
   the exception alone is not proof; if the readback instead shows the row DOES exist, adopt it as
   success instead of releasing empty).
5. `resolve_range_intent_reconciliation()` needs the `verified_readback` durably persisted against a
   `dispatch_attempt_key` FIRST (reusing `provider_write_attempts`'s actual PREPARED-ledger shape, not
   just accepting a bare unpersisted argument) -- matching how
   `record_reservation_no_mutation_reconciliation()` actually gates release in the real precedent.
6. Crash recovery must use a DURABLY stored pre-dispatch baseline snapshot (written in the SAME
   `RESERVED->DISPATCHED` CAS, before `_create_usage()` is called), not an in-memory `before_rows`
   argument that a crash would lose -- `_capture_owned_usage_snapshot()`'s existing check stays but
   needs a durable baseline to compare against after a real restart.

**Everything else (A, B, C, D1, E, F, G, and D2-2's core split) is closed and unchanged across rounds
1-7.**

**Next action**: check round 7's result (.insane-review/, dated ~2026-09-19 16:1x-16:2x). This has
been a 7-round plan-review cycle for one function (`_create_usage()`'s concurrency safety) -- if round
7 closes it, move straight to implementation (full plan v1 base + all of A-G across rounds, the
complete accumulated test list, implement, self-test, a separate FINAL review, then commit). If still
REVISE, the gap will almost certainly be even narrower (round 6 said "no new design categories needed,
range does not need to grow") -- keep iterating the same way rather than restarting.

## C6 [not started, depends only on C1 which is done] Study note generation/storage/dashboard
C1 built the durable storage substrate only. Still missing entirely: study-request input
template/model + worker wiring, evidence selection per mode (confirmed-lecture.v1 policy), an actual
AI generator adapter call (StudyNoteGenerator port -- nothing exists; SessionEnrichmentGenerator/
MaterialEnrichmentGenerator from the unrelated Phase 3 enrichment feature are a pattern to reference,
not reusable code), Drive staging under derived/ai-study-notes/<note_key>/ with artifact_role=
AI_STUDY_NOTE, an AI-region writer for study notes (check whether write_ai_region() in
src/uls/adapters/notion/{base,guarded}.py already supports a second distinct named region or needs
extending), finite retry/backoff (max 3, 1/5/15 min), and the §6.5 dashboard status table. Plan as its
own sub-sequence (request intake -> evidence selection -> generator+staging -> AI-region publish ->
retry/dashboard) given its size.

## insane-review usage (the only correct review channel)
Tool: python3 /Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/*/bin/pack_and_ask.py
--target takes exactly ONE directory; --include takes ONE comma-separated glob list (relative to
--target). Every call needs sandbox_permissions=require_escalated (network + browser) -- a --pack-only
dry run without it can fail with an npx network error even though the escalated version works fine.
.review/*.md files are gitignored and repomix silently drops them from the pack even if listed in
--include -- paste that content directly into --prompt-file instead, or attach the real non-gitignored
source files. Large packs (~130k+ tokens, e.g. 4 full source files for a plan review) can take 5-6+
minutes end to end at "매우 높음" effort -- poll patiently with repeated tools.write_stdin({session_id,
chars:"", yield_time_ms:~28000}) calls in code-mode; empty output on a poll does not mean it failed,
check .insane-review/manifest_*.json (chat_url) and whether a matching response_*.md file has appeared
yet before assuming something broke. Pro tier may not be selectable (menu shows "Pro" but doesn't
change the verified model/effort); use --model "매우 높음" as the AGENTS.md-authorized fallback. If a
manifest exists but no response, use --harvest <manifest_path>; never resend. Always paste verbatim
search commands + full results as evidence text for any "confirmed by search" claim -- a bare claim
was rejected as insufficient more than once this session (C8, C3, C7 all had at least one REVISE round
triggered partly by under-evidenced claims).

## Sandbox notes
.git is read-only by default; git add/commit/push each need sandbox_permissions=require_escalated.
rm is blocked entirely (use /usr/bin/trash, which itself may also need require_escalated for some
paths). apply_patch on repo files can intermittently fail; the reliable fallback for editing an
existing file is: base64-encode new/replacement content in JS (custom_exec, using a hand-rolled
UTF-8+base64 encoder -- this JS isolate has no Buffer/TextEncoder/btoa), pipe through 'base64 -d' via
exec_command (> to overwrite, >> to append). For small in-place string replacement, write a tiny
python3 script (str.replace() on an exact substring, print OK or a MISMATCH count) and run it via
exec_command. Multiline heredoc shell commands with special characters can fail a command-safety
parser; avoid heredocs for file content. Never touch config.yaml (user's own local gitignored config)
or the frozen v1.2 spec doc's example values without being explicitly asked.
