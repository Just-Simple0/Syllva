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

## C5 [ACTIVE -- large, plan round 6 submitted, only D2 open] PageRange/Usage producer v2 envelope
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Do NOT write implementation code
before a plan round gets GO.

**Plan history** (.review/, gitignored; responses in .insane-review/, gitignored): rounds 1-5
progressively closed Blockers A, B, C, D1, E, F, G (all CONFIRMED CLOSED as of round 5's response,
.insane-review/response_src_20260919_155733_12134_517f20.md). **Only Blocker D2 remains, now split
into 3 precise sub-items, addressed in round 6 (c5-plan-v6.md), submitted, awaiting result as of this
handoff write.**

**D2's 3 sub-items** (the durable slot-reservation-before-provider-write design from round 4/5):
- **D2-1**: same-generation/same-target "idempotent retry" must NOT call `_create_usage()`
  (material_usage.py:1022, confirmed to have zero existing-row/ID-collision checks of its own) a
  second time -- needs a SEPARATE `RESERVED -> DISPATCHED` CAS so only one caller ever dispatches the
  actual provider create; the loser does a readback instead of re-dispatching.
- **D2-2**: `BOUND` must not permanently block a legitimate NEXT generation (e.g. update_range after a
  successful create) -- v5's design conflated "permanent slot->Usage binding" with "transient
  in-flight provider attempt state". Fix: `current_usage_app_id` is now explicitly PERMANENT (persists
  across generations like C1's `current_receipt_id`), while `reservation_state` is explicitly
  TRANSIENT (always returns to `NONE` once a create attempt reaches ANY terminal outcome). Also
  clarified: `update_range`'s actual page-range provider mutation happens later in HAA's existing
  marker/`_guarded_update` protocol (unchanged by D2) -- the producer-side reservation only ever
  guards `_create_usage()` dispatch, never the range-change mutation itself.
- **D2-3**: release-to-`NONE` criteria were too weak -- must match this codebase's existing strict
  no-effect standard (only a trusted `ProviderWriteNotAppliedError` releases directly to `NONE`;
  anything else -> `RECONCILE_REQUIRED`, matching HAA's existing `_guarded_update`/marker pattern and
  the `entity_reservations`/`provider_write_attempts` precedent). A new range-intent-scoped
  `resolve_range_intent_reconciliation()` function is needed (confirmed: the existing
  `record_reservation_no_mutation_reconciliation()` is scoped specifically to `entity_reservations`
  rows via an explicit `reservation_id` and cannot be reused unchanged for `range_intent_heads`).

**G is fully closed** (round 5) -- one implementation-detail note carried forward for whenever this is
coded: the new `_phase4_has_slot_sibling()` helper must extract (session, material, role) from raw
rows independently of page-range parsing, NOT via `material_usage_identity()`'s first three fields
(which drops rows with malformed ranges entirely, defeating the point).

**Next action**: check round 6's result (.insane-review/, dated ~2026-09-19 16:0x-16:1x). If GO, move
to implementation: write the code per the fully-accumulated plan (v1 base scope + all of Blockers A-G
as closed across rounds 1-6), the complete test list (also accumulated across all 6 plan rounds), then
a FINAL review (separate from plan review), then commit. This has been an unusually long plan-review
cycle (6 rounds) precisely because it touches the approval/identity guard chain and every round found
real, non-cosmetic correctness gaps by reading actual code -- that rigor is appropriate here and should
not be shortcut once implementation starts either (the FINAL review must be equally thorough).

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
