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
.review/c3-c5-c6-c7-c8-work-plan.md (has exact contract citations and current-code cross-checks for
C5/C6/C7 already done, so as not to redo that research; C3/C8 sections there are now stale/done).

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
C3 [DONE, 00067fb]: three of four contract-row items (Session No nullable, creation-snapshot initial
options, five intake sources' schema validation) were already done (confirmed in C2/C4 audits). The
remaining item, "Queue Proposal Envelope Rich text 추가", has no code location to attach to: no code
anywhere provisions Notion schemas, and Queue has no declarative schema/validator like the five intake
sources' INTAKE_SCHEMAS (contract §5.3 explicitly assigns the v2 envelope's actual read/write/
validation/Reader-HAA logic to C5). Shipped as a documentation-only "Next-version prerequisite" note
in docs/operator-guide/intake-and-notion.md (+ .ko.md) telling operators to manually add the property
ahead of C5. Zero src/ changes. GO after 2 wording-precision REVISE rounds (see .review/c3-review-*
and .review/c3-followup-prompt.md for the exact evidence/fixes, if ever needed for context).

## C7 [ACTIVE -- start here, needs scoping before planning] Semester screen + personal schedule/todo linking view
No matching code found anywhere (confirmed by search in the prior C-series audit). Given ULS's
Notion-centered architecture, this may be substantially a Notion workspace view/database-linking
configuration deliverable rather than Python code -- the first step is determining exactly what (if
anything) is code vs Notion-side setup, not writing a plan assuming it is a large code slice. Re-read
the actual contract section for C7's row (search docs/ux/intake-execution-contract.md §9 for the C7
row's exact spec citation) before starting, per the same "never trust the one-line summary alone"
lesson that applied to every prior C-slice (C2-C3 in particular: the summary line for C3's Queue item
looked like a code task but wasn't).

## C5 [large, depends on C3 which is done] PageRange/Usage producer v2 envelope + generation-bound proposal identity
Genuinely unimplemented (confirmed by search: no usage_slot_key/range_intent_heads/intent_generation
code exists at all). Needs a new durable range_intent_heads table (same pattern as C1's
study_note_heads), proposal_id v2 = SHA256(canonical_json(['uls.usage-proposal.v2', proposal_type,
canonical_action_semantics, [usage_slot_key, request_id, intent_generation]])), reading/writing the
new Proposal Envelope Rich Text property (now documented as a manual operator prerequisite by C3;
C5 must itself add readback/validation of its presence/content -- there is still no schema-readiness
check for Queue, C5 owns building one if it needs it), and a HumanApprovalApplier current-generation-
match check added to its existing checks. Baseline to extend: src/uls/proposal/material_usage.py
(existing v1 producer, includes _queue_properties()/_validate_queue_properties()), src/uls/domain/
approval_identity.py. Comparable in size to C1's effort. Full contract text: §5.3 (already quoted in
git log for commit 00067fb / .review/c3-review-prompt.md).

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
reviewed, including actual production callers). --target takes exactly ONE directory; --include takes
ONE comma-separated glob list (relative to --target), not repeated flags. .review/*.md files are
gitignored and repomix silently drops them from the pack even if listed in --include -- if the
reviewer needs that context (including any "I searched and found nothing" claim or a contract/frozen-
spec excerpt), either (a) attach the real non-gitignored source file directly, or (b) write a small
scratch evidence .md file INSIDE a real (non-gitignored) directory being packed (e.g.
docs/operator-guide/zz<slug>.md), pack it, then delete it with require_escalated /usr/bin/trash before
committing (plain rm is blocked by a deletion-guard hook; trash itself can also need require_escalated
here). A bare claim of "confirmed by search" without the actual command+results attached was rejected
as insufficient evidence more than once (C8 round 1, C3 round 1) -- always paste verbatim commands
and full results.

Every call needs sandbox_permissions=require_escalated (network + browser). Pro tier may not be
selectable (menu shows "Pro" but clicking it does not change the verified model/effort); if so, use
--model "매우 높음" (very high) as the AGENTS.md-authorized fallback and record the actual model/effort
and fallback reason in the review status file/commit message.

Long-running calls return a session_id; poll it via tools.write_stdin in code-mode (custom_exec) with
session_id/yield_time_ms as real JS numbers -- calling the raw top-level write_stdin/exec_command tools
directly with those params sometimes throws a type-parsing error on numeric args in this environment;
code-mode avoids it. Never background with & (child process does not survive tool-call/session
boundaries here). If a manifest_*.json exists in .insane-review/ but no response, use
--harvest <manifest_path>; never resend.

## Sandbox notes
.git is read-only by default; git add/commit/push each need sandbox_permissions=require_escalated.
rm is blocked entirely by a deletion-guard hook (use /usr/bin/trash instead, which itself may still
need sandbox_permissions=require_escalated for files this sandbox profile restricts). apply_patch on
files under this repo can also intermittently fail (patch-abort on some content shapes); the reliable
fallback for editing an existing file is: base64-encode the new/replacement content in JS (custom_exec,
using a hand-rolled UTF-8+base64 encoder -- this JS isolate has no Buffer/TextEncoder/btoa), then pipe
it through 'base64 -d' via exec_command, redirecting with > to overwrite or >> to append. For a small
in-place string replacement in an existing file, write a tiny python3 script (str.replace() on an
exact substring, print OK or a MISMATCH count on failure) and run it via exec_command -- this avoids
re-transmitting the whole file through the patch/heredoc path and is what worked reliably for every
C2/C3/C4/C8 code/docs edit in this session. Multiline heredoc shell commands with special characters
can fail a command-safety parser entirely; avoid heredocs for file content, use the approach above.
Never touch config.yaml (the user's own local gitignored operational config) or the frozen v1.2 spec
doc's example values without being explicitly asked.
