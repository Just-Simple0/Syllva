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

## C5 [ACTIVE -- large, plan round 5 submitted] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Do NOT write implementation code
before a plan round gets GO.

**Plan history** (.review/, gitignored; responses in .insane-review/, gitignored):
Round 1 (c5-plan.md) REVISE 11 findings -> Round 2 (c5-plan-v2.md) REVISE 6 blockers (A-G) -> Round 3
(c5-plan-v3.md) REVISE, CLOSED A/C/E, narrowed B/D/F/G -> Round 4 (c5-plan-v4.md) REVISE, CLOSED
B/D1/F, only D2 and G remain -> Round 5 (c5-plan-v5.md, addresses exactly D2+G) submitted, awaiting
result as of this handoff write.

**Only 2 items remain open, both narrow and well-scoped now:**
- **D2**: the local head/generation-claim design must durably RESERVE the slot (new
  `reservation_state`/`reserved_target_id`/`reserved_generation` columns on `range_intent_heads`,
  state machine NONE->RESERVED->BOUND or ->RECONCILE_REQUIRED->NONE) BEFORE calling
  `self._create_usage()` (material_usage.py:992) -- not just record `current_usage_app_id` after
  success. Mirrors the existing `provider_write_attempts`/`entity_reservations` PREPARED-before-
  external-call pattern already in this codebase (do not hold a SQLite write transaction open across
  the network call -- reviewer explicitly rejected that as a worse failure mode).
- **G**: the local head/reservation check alone cannot catch a Usage that entered the same
  (session, material, role) slot through a path the head never recorded (out-of-band edit, pre-C5
  Usage, etc.). Needs a SEPARATE, range-agnostic `_phase4_has_slot_sibling()` check (same shape as the
  existing exact-range `_phase4_has_sibling_duplicate()` at base.py:4603, but keyed on
  (session_id, material_id, role) only, using RAW rows not `_eligible_usage_scopes()`) added ALONGSIDE
  the existing exact-range check at THREE sites: `_apply_phase4()`'s initial check (base.py:3494),
  `_phase4_reconcile_target()`'s final pre-write re-check (base.py:3989-3990 -- confirmed this round,
  a distinct site from 3494), and the producer's create/update candidate path (already covered by v3's
  original Blocker G, now clarified as the same concept).

**Next action**: check the result of round 5 (session_id / manifest in .insane-review/ dated around
2026-09-19 16:0x). If GO, move to implementation (write the code exactly per the final plan --
v1 base scope + v2/v3/v4/v5's Blockers A-G -- then the full accumulated test list from all 5 plan
rounds, then a FINAL review, then commit). If REVISE again, the remaining gap will be narrower still;
keep iterating the same way (round 6 addressing only whatever's still open). Every round so far has
found genuinely real, non-cosmetic correctness issues by reading actual code -- this rigor is
appropriate for approval/identity-guard logic and should not be shortcut.

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
