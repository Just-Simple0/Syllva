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

## C5 [ACTIVE -- large, plan round 3 pending] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Confirmed genuinely unimplemented.

**Plan history**: round 1 (.review/c5-plan.md) -> REVISE, 11 findings (response:
.insane-review/response_src_20260919_131516_9401_a16c06.md). Round 2 (.review/c5-plan-v2.md,
addressing all 11) -> REVISE, 6 remaining blockers, most prior fixes confirmed correct (response:
.insane-review/response_src_20260919_151421_11240_fa3b08.md). Round 3 plan written
(.review/c5-plan-v3.md) addressing all 6 round-2 blockers -- **submission to review round 3 is the
next action** (not yet sent/confirmed as of this handoff write). Do NOT write implementation code
before a plan round gets GO.

**Round 2's 6 blockers, now addressed in c5-plan-v3.md as Blockers A-G** (read c5-plan-v3.md in full
before continuing -- this is a condensed pointer, not a substitute):
- A: envelope parsing must be tri-state (absent / present-valid / present-INVALID), not a boolean --
  present-but-malformed must NEVER be treated as legacy v1, on any Queue state including APPLIED.
- B: the v2 plan targeted the wrong immutable-field location. Real fix needs 3 sites: the Phase4-only
  comparison loop at base.py ~1084-1090 (not the generic tuple near 1134, which MATERIAL_USAGE/
  PAGE_RANGE never reaches), enforce_write_policy()'s immutable_queue_fields set (~base.py:533-542),
  and material_usage.py's _validate_queue_properties() allowed/required sets (~1643-1717).
- C: HAA's _read_current() (base.py:2280, the FIRST entry point) also needs the v2 generation check,
  not just the final _phase4_approved_queue() checkpoint -- HAA can mutate Queue (clear markers, arm
  PREPARED marker) between those two points.
- D: BEGIN IMMEDIATE generation-claim alone doesn't stop a stale generation from overwriting a head
  after a newer one already finalized -- needs a second finalize_generation() step doing a conditional
  UPDATE ... WHERE intent_generation=? with rowcount==1 required (C1's attach_note_request() pattern).
  Also: "same receipt, different output" must be detected by comparing actual outbox action/envelope
  BYTES, not receipt_hash (receipt_hash only proves same input, not same producer output).
- E: legacy-vs-v2 split is 4-way not 3-way: envelope-absent+non-APPLIED=deny,
  envelope-absent+APPLIED=unchanged v1 replay, envelope-present-valid=full v2 check,
  envelope-present-INVALID=unconditional deny regardless of state (ties to A).
- F: read-time dispatch (legacy-tolerant, envelope absence OK) and create-time dispatch (v2 MANDATORY
  for new MATERIAL_USAGE/PAGE_RANGE, envelope absence is an error) must be two separate functions, not
  one reused dispatcher -- otherwise new envelope-less rows could keep being created forever.
- G: real, contract-mandated behavior gap found independent of C5: material_usage.py's duplicate-Usage
  check (candidate_identity ~line 848) includes page_range, so a second Usage for the same session/
  material/role but a DIFFERENT range currently creates a brand new Usage with zero warning today. The
  contract itself (§5.2: "같은 slot에 이미 여러 Usage가 있으면 ... 관계 정리 확인 대상으로 둔다",
  "CREATE: 현재 slot의 Usage가 없을 때만 허용한다") mandates the fix: before material_usage.py's
  create_usage/is_new_usage branch (~line 919), check for ANY existing Usage in the (session, material,
  role) slot regardless of range; if one exists and doesn't exactly match the candidate, return the
  existing reconciliation-required warning shape instead of creating a second Usage.

**Next action**: submit .review/c5-plan-v3.md for insane-review round 3 (same pattern as rounds 1-2 --
--target src --include the same uls/domain/approval_identity.py,uls/proposal/material_usage.py,
uls/adapters/notion/base.py,uls/state/sqlite.py list, paste the round-2 disposition + full v3 plan
into --prompt-file since .review/*.md is gitignored). Expect ~130k token pack, 5-7+ minute wait at
"매우 높음" effort based on rounds 1-2's timing. Once GO, implement exactly per the final plan, write
the tests already listed across all three plan versions, get a FINAL review (not just plan review),
then commit.

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
