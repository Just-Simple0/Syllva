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

## C5 [PLAN GO -- start implementation here] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. **Plan review is DONE (10 rounds,
final GO from .insane-review/response_src_20260919_163408_13196_f9e5da.md). Implementation has NOT
started yet.** The full final design is the accumulation of ALL of .review/c5-plan.md through
c5-plan-v10.md (10 files, gitignored) -- read v10 first (it's short, only the last 2 fixes), then
work backward through v9/v8/.../v1 for the full accumulated design; each version only restates what
changed, not the whole design.

**Final design summary** (see .review/c5-plan-v10.md + earlier versions for full detail):
- New SQLite tables: `range_intent_heads` (usage_slot_key PK; current_usage_app_id permanent binding;
  reservation_state IN ('NONE','RESERVED','DISPATCHED','RECONCILE_REQUIRED') transient; reserved_generation;
  dispatch_attempt_no; reserved_target_id) and `usage_proposal_outbox` (proposal_id PK; publish_state IN
  ('PREPARED','PUBLISHED','RECONCILE_REQUIRED'); UNIQUE(usage_slot_key, intent_generation)).
  `provider_write_attempts` gains one new nullable column `pre_dispatch_snapshot_json TEXT` (ALTER TABLE
  migration, matching the existing reservation_id/stage pattern) -- `ProviderWriteAttempt` model gains a
  matching `str | None = None` field (verify against the real uls/state/models.py, not reviewed in the
  plan-review packs).
- approval_identity.py gains: `derive_usage_slot_key`, `parse_usage_proposal_envelope` (tri-state:
  None=absent, value=valid, raises=present-but-invalid -- NEVER treat invalid as legacy), a separate
  strict v2 canonical serializer with `allow_nan=False` (do NOT touch the shared v1 `_sha256`),
  `derive_proposal_id_for_read` (EXAM_SCOPE always v1; M/P v2-if-envelope-present else v1-tolerant) and
  `derive_proposal_id_for_create` (EXAM_SCOPE always v1; M/P envelope MANDATORY, v2 always).
- base.py's `_validate_phase4_queue_identity()` internal `derive_proposal_id()` call becomes
  `derive_proposal_id_for_read` (its 17 external call sites need NO individual changes -- it's already
  the shared gate). `_upsert_phase4_proposal()`'s FIRST direct call (new candidate's expected ID) becomes
  `derive_proposal_id_for_create`; its other two calls (existing/recovered row comparison) stay
  `derive_proposal_id_for_read`. `_create_phase4_queue_once()`'s two calls split the same way (new
  properties=create, read_matching()'s found row=read).
- HumanApprovalApplier gains an optional `usage_intent_state: UsageIntentStateReader | None` constructor
  param (narrow read-only Protocol). A shared `_validate_v2_usage_generation(record)` helper is called at
  BOTH `_read_current()` (base.py:2280, the FIRST entry point, before any Queue-mutating side effect) AND
  `_phase4_approved_queue()` (base.py:2339, the final pre-write checkpoint) -- same helper, two call
  sites. Four-way legacy split: absent+non-APPLIED=deny; absent+APPLIED=unchanged v1 replay;
  present+valid=full v2 check (deny if no state reader configured); present+INVALID=unconditional deny
  regardless of state.
- Producer (material_usage.py): before the existing create_usage/update_range operation-selection logic
  (UNCHANGED -- create_usage still means "approval writes Verified=True", not "new row"), claim a
  generation via one `BEGIN IMMEDIATE` (receipt-aware: same receipt=reuse generation,
  different=receipt=bump, same receipt+different producer output=fail-closed ProposalConflictError,
  explicitly re-raised past any broad exception catch). Then a SEPARATE `RESERVED->DISPATCHED` CAS
  (only the winner calls `_create_usage()`; the loser polls read-only, never writes shared state) with a
  durable pre-dispatch multiset snapshot written atomically alongside it. After the real provider call:
  release/adopt/reconcile per the exact 3-way split in c5-plan-v10.md's final section (canonical
  MULTISET equality via the existing `_usage_rows_snapshot_equal`/`Counter` pattern, NOT raw provider-
  order string equality; adoption requires reusing `_only_exact_siblings_added()`'s subtraction PLUS an
  explicit `len(added)==1` check PLUS Verified-semantics match). `_queue_properties()` gains
  `Proposal Envelope`; `_validate_queue_properties()`'s allowed/required sets gain it too, final self-
  check uses `derive_proposal_id_for_create`.
- Slot cardinality (Blocker G, fully independent of the reservation mechanism): a NEW
  `_phase4_has_slot_sibling()` (range-agnostic (session,material,role) using RAW rows, NOT
  `_eligible_usage_scopes()`) added ALONGSIDE the existing exact-range `_phase4_has_sibling_duplicate()`
  at THREE sites: producer's candidate path, HAA's initial check (base.py:3494), and HAA's
  `_phase4_reconcile_target()` final pre-write re-check (base.py:3989-3990, confirmed a DISTINCT site
  from 3494). Must extract (session,material,role) independently of range-parsing so a malformed range
  doesn't silently drop the row from detection.
- `Proposal Envelope` added to: HAA's Phase4 supplied-vs-current comparison (base.py's Phase4 branch
  ~1074-1090, via a DIRECT `parse_usage_proposal_envelope` equality check -- NOT via the generic tuple
  near line 1134, which M/P never reaches), `enforce_write_policy`'s `immutable_queue_fields` set
  (~533-542), and producer's `_validate_queue_properties` allowed/required sets.

**Test list**: the FULL accumulated list across all 10 plan-review rounds (dozens of specific tests) --
do not skip any; each one is a direct regression for a real bug a reviewer found by reading actual code.

**Next action**: implement exactly per the above + full plan text in .review/c5-plan-v10.md (and
earlier versions as needed for full context), write ALL accumulated tests, self-verify (run pytest -m
contract and pytest -m unit locally), then get a separate FINAL review (not a plan review -- this
checks the actual diff, same insane-review pattern, same 4 files plus the new/changed test files) before
committing. Given the reservation/CAS design's complexity, budget for at least one FINAL-review REVISE
round on implementation details even though the plan itself is now fully GO'd.

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
