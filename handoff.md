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

## C5 [ACTIVE -- large, mid-plan] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Contract: docs/ux/intake-execution-contract.md §5.3 (full text quoted in .review/c5-plan-review-prompt.md).
Confirmed genuinely unimplemented: `rg -n 'usage_slot_key|range_intent_heads|intent_generation' src`
-> zero matches.

**Status: plan round 1 sent to insane-review (GPT-5.6 Sol/매우 높음, ~132k token pack of
approval_identity.py + material_usage.py + base.py + sqlite.py) -> REVISE with substantial, specific,
code-grounded structural findings.** Full response saved at
.insane-review/response_src_20260919_131516_9401_a16c06.md (also in the review platform thread; see
manifest_src_20260919_131516_9401_a16c06.json for chat_url). Draft plan (needs revision before
resubmitting) at .review/c5-plan.md. Do NOT write implementation code before the plan is revised to
address every point below and gets a GO on a resubmitted plan review.

### Exact findings to fix in the plan before resubmitting (all reviewer-verified against real code):
1. **Shared identity dispatcher is required, not optional.** `_validate_phase4_queue_identity()` in
   base.py currently recomputes ALL of MATERIAL_USAGE/PAGE_RANGE/EXAM_SCOPE via the v1
   `derive_proposal_id()` unconditionally -- a v2 Queue row would be rejected by this EXISTING check
   before ever reaching a new v2 guard. Same problem in `upsert_proposal()` ->
   `_upsert_phase4_proposal()` (used for create input, existing-row retry, AND ambiguous-create
   recovery) and `_create_phase4_queue_once()`. All of these need to become one dispatcher: EXAM_SCOPE
   stays v1; MATERIAL_USAGE/PAGE_RANGE use v2 when `Proposal Envelope` is present, v1 integrity-only
   when absent (legacy row). Producer, HAA, and ApprovalReader must share this one dispatcher -- do not
   write three separate copies.
2. **Producer has no receipt/request context at all today.** `MaterialUsageProposalProducer`'s
   constructor takes no state store; `produce`/`run`/`propose()` take no request_id/receipt_id. C1's
   `study_note_heads` pattern (the thing this plan explicitly mirrors) stores `current_receipt_id` +
   `receipt_hash` too, not just `current_request_id`, and only bumps generation on a genuinely
   different receipt (same receipt = return existing generation), inside one `BEGIN IMMEDIATE`
   transaction. The plan must add this receipt/request wiring explicitly, matching that exact pattern,
   with "same receipt but producer output changed" as fail-closed conflict (not silently overwritten).
3. **"current Usage exists -> UPDATE" is semantically wrong.** The current producer's `create_usage`
   operation is NOT "no Usage exists yet" -- it's used even when a matching (unverified) Usage row
   already exists; `create_usage` means "approval will write Verified=True", `update_range` means
   "approval will change Start/End Page". A separate `is_new_usage`/row-creation flag governs whether a
   new row is actually created. The head's `current_usage_app_id` must be an identity/reconciliation
   guard, NOT an operation selector -- keep the existing operation-selection logic untouched and layer
   the head check alongside it.
4. **usage_slot_key excludes page range, but existing duplicate-identity logic includes it** (session,
   material, role, page_range). The plan needs an explicit slot-adoption step: when a head is first
   created for a slot, query the live graph for that (session, material, role) tuple only, and handle
   0 matches (create allowed), exactly 1 (adopt into head), or >1 (fail-closed as reconciliation-
   required, no arbitrary pick). Each new generation must re-verify the head's current Usage ID still
   matches the live graph.
5. **HAA needs a final pre-write generation re-check, not just an initial one**, to close a generation
   race: HAA already re-reads the Queue and re-verifies the exact physical row immediately before
   `_guarded_update()` (because graph/source/marker work can be slow) -- the new v2 generation-binding
   guard must run at that SAME final checkpoint, not only at the initial read, or a stale generation N
   proposal can still win a race against a newer N+1 that already updated the head.
6. **HAA needs an injected read-only state dependency.** Current HAA constructor only takes
   adapter/graph/source/config -- no state store today. Add a narrow read-only Protocol (e.g.
   `UsageProposalStateReader`), not a concrete SQLite coupling, to preserve base.py's provider-neutral
   boundary. Validation must chain: envelope.usage_slot_key == derive_slot(action's session/material/
   role) -> envelope.request_id/generation == head's current -> head's current proposal/target/
   operation == action -> outbox action/envelope bytes == Queue's stored bytes -> outbox published
   state.
7. **Add `Proposal Envelope` to the Queue immutable-field deny list** (base.py's existing immutable
   field list currently covers `Proposed Action` etc. but not yet this new property -- it needs the
   same "no post-creation caller mutation" protection).
8. **Do not reuse the existing `_sha256`/`_canonical_json_value` helpers as-is for v2.** They call
   `json.dumps()` without `allow_nan=False` today (contract requires "no non-finite numbers" but the
   CURRENT v1 helper doesn't enforce it) -- changing the shared v1 helper risks a v1 compat break. Add a
   separate strict v2 canonical serializer with `allow_nan=False` instead of modifying the shared one.
9. **2-table design (range_intent_heads + usage_proposal_outbox) confirmed correct** -- different
   lifecycles (mutable current-pointer vs. immutable per-generation publication history), don't merge.
   Closest existing precedent for the outbox half is `provider_write_attempts` (UNIQUE operation_key,
   PREPARED-before-external-write pattern) -- read that table's actual schema/usage before finalizing
   the outbox table. Add `UNIQUE(usage_slot_key, intent_generation)` too (not just the 3-column unique).
   `published` should mean "authoritative Queue readback confirmed the exact row", matching the
   existing strict-create pattern elsewhere (not "write call returned success") -- consider
   PREPARED/PUBLISHED/RECONCILE_REQUIRED instead of a boolean for clarity. HAA must require
   published=PUBLISHED only.
10. **Refine the "deny all envelope-less v1" rule.** `_apply_phase4()` already treats an APPLIED row as
    a terminal idempotent replay (no re-mutation, just returns). Split the rule: non-APPLIED legacy v1
    (no envelope) -> deny, "new proposal required" (as planned); already-APPLIED legacy v1 -> allow
    through EXISTING v1 identity validation as a terminal replay only (preserves both "APPLIED v1
    preserved" and existing idempotency -- don't accidentally break replay of already-done work).
11. **Test plan additions** (beyond what's already listed): same receipt + changed producer output =>
    fail-closed conflict, not silent overwrite; two concurrent new receipts => distinct monotonic
    generations; an old generation's retry after a newer head exists => never becomes current again;
    head advances between HAA's initial read and final pre-write checkpoint => zero target mutation;
    slot has 0/1/>1 live Usage rows at first head creation; existing-unverified-Usage + create_usage
    stays create_usage (not reinterpreted as update); published != PUBLISHED => deny; envelope
    slot/action cross-binding mismatch => deny; legacy APPROVED v1 => deny, APPLIED v1 => replay only;
    EXAM_SCOPE identity completely unchanged; NaN/Infinity strictly rejected by the new v2 serializer.

Reviewer's overall framing: architecture doesn't need to be scrapped, but the plan must explicitly cover
all 11 points above before the next plan-review round is GO-able. Do not start implementation until then.

Files to actually re-read in full before revising the plan (do not rely on this summary alone): the
exact bodies of `_validate_phase4_queue_identity()`, `upsert_proposal()`, `_upsert_phase4_proposal()`,
`_create_phase4_queue_once()`, `_apply_phase4()` in src/uls/adapters/notion/base.py, and the
`provider_write_attempts` table schema in src/uls/state/sqlite.py.

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
