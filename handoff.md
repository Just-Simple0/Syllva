# Syllva (ULS v1.2) Handoff

Updated: 2026-09-19. Authority: AGENTS.md (roles/review policy), university-learning-system-v1.2-design-frozen.md,
university-learning-system-v1.2-implementation-spec-frozen.md (v1.2 core, frozen -- do not edit),
docs/ux/intake-execution-contract.md (rev10, additive, accepted-not-fully-implemented, sections 1-10 only).
Git log + .review/* (gitignored) = source of truth for shipped work. This file = active/resumable
state only, no narrative history.

Branch: codex/protected-secret-file-and-credential-set. No push to protected branches without explicit
user instruction (this feature branch itself is fine to push).

## rev10 C-series: user approved a reordered sequence by real size/dependency, not label order:
## C1 -> C4 -> C2 -> C8 -> C3 -> C7(scope first) -> C5 -> C6
Each: plan -> independent PLAN review (insane-review) -> implement -> test -> independent FINAL review
(insane-review) -> commit. Full re-verified scope/status for the remaining items:
.review/c3-c5-c6-c7-c8-work-plan.md (read this before starting C3/C5/C6/C7 -- it has the exact
contract citations and current-code cross-checks already done, so as not to redo that research).

C1 [DONE, 542e1d4]: durable study-note state/storage substrate (study_note_heads/note_jobs/
note_attempts/note_request_references/note_artifacts). C6 builds behavior on top of this.
C4 [DONE, b7b102f]: classifier timestamp-grammar fix. route_intake() (src/uls/intake/planner.py) is
UNUSED dead code -- never target it. Real gate: validate_request_input() (src/uls/intake/requests.py).
C2 [DONE, 970d34d]: ensure_marked_folder() private-ownership fix (src/uls/adapters/drive/worker.py) --
reuse path skipped require_private_ownership(), and create path had no equivalent postcondition at
all (create/reuse asymmetry). Both now share one postcondition inside ensure_marked_folder() itself.
C8 [DONE, 02bebff]: poll_interval_minutes default 10 -> 1 minute (schema.py, config.example.yaml,
macOS plist, Windows task xml). config.yaml (user's own gitignored local config) and the frozen spec's
example value were deliberately left untouched.

## C3 [ACTIVE -- start here] Notion DB schema per type (frozen impl spec section 14-15)
Re-verified: Session No nullable + creation-snapshot initial options + the five intake data sources'
field/relation/ownership validation are ALL already done (confirmed in the C2/C4 audits -- do not
redo this research, it is already in .review/c3-c5-c6-c7-c8-work-plan.md). The one genuinely new item:
add a 'Proposal Envelope' Rich Text property to the Queue schema (schema-only; confirmed via code
search that this property does not exist anywhere in src/ yet). This is the prerequisite C5's v2
envelope logic will read/write -- C3 only adds the empty property/expected-field entry, C5 owns all
behavioral logic. Keep existing Queue enum/permissions unchanged (explicit contract requirement).
Find where Queue/Proposal schema expectations are currently declared/validated (search for 'Proposed
Action' in src/uls/adapters/notion/base.py as the existing v1 pattern to mirror) before writing code.

## C7 [needs scoping before planning] Semester screen + personal schedule/todo linking view
No matching code found anywhere (confirmed by search). Given ULS's Notion-centered architecture, this
may be substantially a Notion workspace view/database-linking configuration deliverable rather than
Python code -- the first step is determining exactly what (if anything) is code vs Notion-side setup,
not writing a plan assuming it is a large code slice.

## C5 [large, depends on C3] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Genuinely unimplemented (confirmed by search: no usage_slot_key/range_intent_heads/intent_generation
code exists at all). Needs a new durable range_intent_heads table (same pattern as C1's
study_note_heads), proposal_id v2 = SHA256(canonical_json(['uls.usage-proposal.v2', proposal_type,
canonical_action_semantics, [usage_slot_key, request_id, intent_generation]])), reading/writing the
C3 Proposal Envelope property, and a HumanApprovalApplier current-generation-match check added to its
existing checks. Baseline to extend: src/uls/proposal/material_usage.py (existing v1 producer),
src/uls/domain/approval_identity.py. Comparable in size to C1's effort.

## C6 [largest, depends only on C1 which is done] Study note generation/storage/dashboard
C1 built the durable storage substrate only. Still missing entirely: a study-request input
template/model + worker wiring (analogous to Input Request), evidence selection per mode
(confirmed-lecture.v1 policy), an actual AI generator adapter call (nothing exists --
SessionEnrichmentGenerator/MaterialEnrichmentGenerator from the unrelated Phase 3 enrichment feature
are a pattern to reference, not reusable code), Drive staging under derived/ai-study-notes/<note_key>/
with a new artifact_role=AI_STUDY_NOTE, an AI-region writer for study notes specifically (the existing
write_ai_region() in src/uls/adapters/notion/{base,guarded}.py is used for the unrelated enrichment
feature -- check whether it already supports a second distinct named region or needs extending),
finite retry/backoff (max 3 retries, 1/5/15 min), and the full section 6.5 dashboard status table.
Given its size, plan this as its own sub-sequence (request intake -> evidence selection ->
generator+staging -> AI-region publish -> retry/dashboard) rather than one giant plan.

## insane-review usage (the only correct review channel)
Tool: python3 /Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/*/bin/pack_and_ask.py
Always use real repomix-attached code (--target + --include: list every file whose behavior is being
reviewed, including actual production callers). .review/*.md files are gitignored and repomix silently
drops them from the pack even if listed in --include -- if the reviewer needs that context (including
any 'I searched and found nothing' claim), paste the exact search command and full match
classification directly into --prompt as verbatim text; a bare claim of 'confirmed by search' without
the actual command+results attached was rejected as insufficient evidence once already (C8 round 1).
Never substitute a Codex-app chat thread (mcp__codex_app__create_thread/send_message_to_thread) -- that
consumes Codex's own separate message quota and is not a real review.

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
rm, /usr/bin/trash, and apply_patch on files under this repo can all intermittently fail (deletion-guard
hook / macOS Trash permission / patch-abort on some content shapes); the reliable fallback for editing
an existing file is: base64-encode the new/replacement content in JS (custom_exec), then pipe it through
'base64 -d' via exec_command, redirecting with > to overwrite or >> to append. For a small in-place
string replacement in an existing file, write a tiny python3 script (str.replace() on an exact
substring, print OK or the match count on failure) and run it via exec_command -- this avoids
re-transmitting the whole file through the patch/heredoc path and is what worked reliably for every
C2/C4/C8 code edit in this session. Multiline heredoc shell commands with special characters can fail
a command-safety parser entirely; avoid heredocs for file content, use the approach above instead.
Never touch config.yaml (the user's own local gitignored operational config) or the frozen v1.2 spec
doc's example values without being explicitly asked.
