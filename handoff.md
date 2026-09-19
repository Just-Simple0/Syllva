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

## C5 [ACTIVE -- large, plan round 4 needed] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Contract: docs/ux/intake-execution-contract.md section 5.2-5.3. Confirmed genuinely unimplemented.
Do NOT write implementation code before a plan round gets GO -- this touches the approval/identity
guard chain and every round so far has found real correctness gaps only visible by reading actual code.

**Plan history** (all in .review/, gitignored; responses in .insane-review/, gitignored):
- Round 1 (c5-plan.md) -> REVISE, 11 findings.
- Round 2 (c5-plan-v2.md, addressed all 11) -> REVISE, 6 remaining blockers (A-F below), most round-1
  fixes confirmed correct.
- Round 3 (c5-plan-v3.md, addressed all 6 as Blockers A-G, G is a bonus contract-grounded finding) ->
  REVISE. **3 of 7 blockers now CLOSED (A, C, E). 4 remain, each narrower than before:**
  - **B (partial)**: adding "Proposal Envelope" to the Phase4 comparison tuple in
    `_assert_supplied_matches_current()` (base.py ~1074-1090) does NOT actually compare it -- that
    function's real equality check is `canonical_semantics_from_queue(candidate) != expected`, and
    `_validate_mirrors()` (the function that actually decides what's compared) does not look at
    Envelope at all. Real fix: when `supplied` has an Envelope, directly compare
    `parse_usage_proposal_envelope(supplied)` against `parse_usage_proposal_envelope(current)` for
    canonical equality (absent-in-supplied = allow partial-record calls as today; present-and-invalid =
    immediate PolicyViolation; present-but-current-is-legacy-absent = mismatch/deny). The other 2 B
    sites (enforce_write_policy's immutable set, producer's _validate_queue_properties allowed/required)
    were confirmed correct as planned.
  - **D (partial)**: the head CAS (conditional UPDATE ... WHERE intent_generation=?, rowcount==1) is
    confirmed correct and DOES stop a stale generation from overwriting a newer head. Two remaining
    gaps: (1) the plan's order (upsert outbox PREPARED, then CAS, return False on CAS failure) lets a
    stale PREPARED outbox row commit permanently, since a normal (non-exception) return from this
    codebase's SQLite transaction helper commits -- CAS must run first, or CAS failure must raise
    inside the transaction to force a rollback, and a test must assert "no new PREPARED row exists
    after a failed CAS". (2) Bigger: after a successful finalize, a newer generation can be claimed and
    the actual provider-side Usage creation (producer's real `self._create_usage()` call, confirmed to
    happen AFTER a comment that explicitly says its pre-check is done "without claiming CAS") can still
    run using the now-stale generation's data -- the head/outbox CAS protects the Queue/HAA layer but
    not the actual Drive/Notion Usage-row mutation itself. A durable slot-level reservation/ownership
    mechanism spanning PREPARED-through-provider-mutation is needed, not just a head-read-again pattern
    (which the reviewer flagged would just create another TOCTOU window).
  - **F (revise)**: call-site classification in `_upsert_phase4_proposal()` was wrong -- ALL THREE
    direct `derive_proposal_id()` calls there (expected-ID for the new candidate, existing stored row,
    and exception-recovered row) are actually candidate-vs-stored comparisons; only the FIRST is truly
    create-time-mandatory, the other two are legitimately read-time-tolerant (they're validating
    something already in the Queue). Sending all three to the create dispatcher re-breaks legacy-row
    reads. Additionally, `_create_phase4_queue_once()` (its own docstring: "guards every public Phase4
    Queue create") was missed entirely -- it calls `_validate_phase4_queue_identity(properties)` on the
    NEW candidate (must become create-time-mandatory) but also calls it again inside its internal
    `read_matching()` on an EXISTING found row (must stay read-time-tolerant) -- needs to split into two
    different calls, not one shared one. Also flagged as an open design decision (not yet a blocker):
    the public `Proposal` dataclass/`upsert_proposal()` convenience API doesn't have an Envelope field
    yet -- decide whether to add one or mark MATERIAL_USAGE/PAGE_RANGE unsupported through that path.
  - **G (revise, bigger than planned)**: the "any existing Usage in slot blocks create_usage" fix is
    correct AS FAR AS IT GOES but insufficient alone. Confirmed: `update_range` targets one EXPLICIT
    Usage ID and never checks whether OTHER Usage rows exist in the same (session, material, role) slot
    with a different range -- so a slot can still end up with 2+ live Usages via update_range even with
    the create-side fix in place. HAA's own sibling-duplicate check is ALSO exact-range-keyed (same gap,
    separately in base.py), so a same-slot-different-range Usage inserted between proposal creation and
    human approval isn't caught as a slot conflict by HAA either. Producer's slot query also must not
    rely on `_eligible_usage_scopes()` alone, since that function silently drops malformed/ambiguous raw
    rows from scope -- those must still count as "slot occupied, needs reconciliation", not be ignored.
    The real fix needs a SHARED (session, material, role) cardinality invariant enforced in: the create
    branch (as planned), the update_range branch (new), HAA's sibling check (new), and using raw rows
    for slot detection (not just eligible scopes).

**Next action**: write plan round 4 addressing exactly these 4 refinements (not a full rewrite -- A/C/E
are locked in, do not relitigate them), resubmit via the same insane-review pattern (--target src,
--include uls/domain/approval_identity.py,uls/proposal/material_usage.py,uls/adapters/notion/base.py,
uls/state/sqlite.py, paste the full round-3 disposition + revised plan into --prompt-file). Each round
so far has taken ~7-8 minutes end to end (132k-token pack + 매우 높음 reasoning) and found real,
non-cosmetic correctness gaps -- budget for at least one more round, possibly two, before this is
safe to implement. Full round 3 response saved at
.insane-review/response_src_20260919_152535_11474_6e21bf.md for exact wording if needed.

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
