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

## C5 [IMPLEMENTATION IN PROGRESS -- final review round 2 submitted] PageRange/Usage producer v2 envelope
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Plan is fully GO'd (10 rounds, see
.review/c5-plan.md through c5-plan-v10.md). **Implementation of a deliberately PARTIAL, self-contained
foundational slice is done and self-tested (1504/1504 full suite passing, ruff/mypy clean). NOT YET
COMMITTED -- awaiting final-review round 2 result as of this handoff write** (submitted via
insane-review, large ~130k-token pack, expect 8-12+ min).

**What this slice contains (uncommitted, on disk in the worktree)**:
- New tables `range_intent_heads`/`usage_proposal_outbox` + new `pre_dispatch_snapshot_json` column
  on `provider_write_attempts` (src/uls/state/sqlite.py, src/uls/state/models.py) with full generation-
  claim + NONE->DISPATCHED CAS + release/bind/reconcile state-machine methods.
- New pure identity functions in src/uls/domain/approval_identity.py: derive_usage_slot_key,
  parse_usage_proposal_envelope (tri-state, strict duplicate-key rejection), build_usage_proposal_
  envelope, a separate strict v2 serializer, derive_proposal_id_v2/_for_read/_for_create.
- src/uls/adapters/notion/base.py: `_validate_phase4_queue_identity()`'s 17 call sites now dispatch
  through derive_proposal_id_for_read (confirmed backward-compatible, zero test changes needed for
  that alone). New `UsageIntentStateReader` Protocol + `HumanApprovalApplier`'s optional
  `usage_intent_state` param (defaults None, backward compatible). New `_validate_v2_usage_generation()`
  wired at both `_read_current()` and `_phase4_approved_queue()`. New range-agnostic
  `_phase4_has_slot_sibling()` (Blocker G) ACTIVE at all 3 designed sites -- this IS a live behavior
  change (a slot may hold at most one live Usage across all ranges now), and 4 existing contract tests
  were updated to assert the new SUPERSEDED/reconciliation-required outcome instead of the old
  "different range = unrelated" outcome (see git diff in the 3 modified test files).
- New test file tests/unit/test_c5_range_intent_state.py (24 tests covering the state machine +
  identity dispatch, all passing).

**Explicitly DEFERRED, NOT in this slice** (documented with NOTE comments at each site, confirmed by
insane-review round 1 as the correct call): the producer (MaterialUsageProposalProducer) does not yet
build/attach envelopes, claim generations, or use the reservation/outbox state-store methods --
`_create_usage()` is unchanged. Two enforcement points exist as CORRECT, working code
(derive_proposal_id_for_create, the "envelope-absent+non-APPLIED=deny" branch in
_validate_v2_usage_generation) but are NOT yet the ACTIVE default gate for every caller, because
activating them before the producer supplies envelopes breaks the entire existing v1 test suite (this
was empirically confirmed: flipping them on caused 346 and then ~360 test failures respectively before
being reverted to deferred-with-NOTE-comment). Wiring the producer to actually use these, then
activating both gates, is the next slice.

**Final-review history for this slice**: round 1 confirmed the defer decisions and Blocker G correct,
but found 4 real fail-closed gaps in the foundational mechanism itself (envelope tri-state was
actually broken for present-null values and didn't reject duplicate JSON keys; create-ID helper didn't
re-validate envelope shape; the dispatch CAS didn't bind to the claim's own target/operation so a
caller could substitute an arbitrary target; reconciliation/outbox functions trusted unbound external
proof). All 4 fixed, 8 new regression tests added, full suite re-verified (1504 passing). Round 2
submitted for confirmation.

**Next action**: check round 2's result (.insane-review/, dated ~2026-09-19 21:0x+). If GO: `git add`
exactly the 8 changed files (src/uls/adapters/notion/base.py, src/uls/domain/approval_identity.py,
src/uls/domain/errors.py, src/uls/state/models.py, src/uls/state/sqlite.py,
tests/contract/test_phase4_guarded_workflow.py, tests/contract/test_phase4_rev5_siblings.py,
tests/contract/test_phase4_rev8_approval.py, tests/unit/test_c5_range_intent_state.py -- confirm via
`git status --short` first), commit with a message summarizing this slice's scope (foundational
mechanism only, producer wiring deferred), push. Then start the NEXT slice: wire
MaterialUsageProducerProducer to build envelopes/claim generations/use reserve_usage_dispatch's CAS
around its existing `_create_usage()` call, thread request_id/receipt_id/receipt_hash into
`propose()`, add `Proposal Envelope` to `_queue_properties()`/`_validate_queue_properties()`'s
allowed/required sets, THEN activate the two deferred gates, THEN migrate/verify the existing producer-
level v1 tests against the new v2 path (this will likely require meaningfully more test rewriting than
this slice did, since producer-level tests construct proposals directly). This next slice needs its own
plan->review->implement->test->final-review cycle -- it is NOT covered by the already-GO'd plan rounds
1-10, which only covered a design, not this specific commit-boundary split; use the same rigor.
If REVISE again: fix precisely, re-test the full suite, resubmit -- do not skip self-testing before
resubmitting.

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
