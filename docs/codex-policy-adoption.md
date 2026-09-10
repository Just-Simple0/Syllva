# Codex project-policy adoption record

## Scope and ownership

- Repository: Syllva / University Learning System v1.2.
- Baseline: `main` at `bfc592b4fc15df68df599bc8b0811cace521b1f7`.
- Work branch: `codex/syllva-project-agents`, created by the parent Codex and reused without recreation.
- Owned files: `AGENTS.md` and this document only.
- `CLAUDE.md` is Claude-specific and remains unchanged. The frozen design and implementation specifications are the only project authorities; `AGENTS.md` carries the project-specific rules needed by this worker.

## Classification and acceptance

This is a low-risk documentation-only change. It does not alter runtime code, schemas, data, authentication, public APIs, or user-facing UI flows.

Acceptance requires all of the following:

- Every project-specific invariant, repository rule, implementation sequence, layout constraint, and exact development command from the existing project guidance is preserved in `AGENTS.md`.
- `AGENTS.md` explicitly includes the model-agnostic, MCP-centered, local-primary, single-active-worker, cross-platform architecture and states that the product single-active-worker constraint does not imply Codex subagent concurrency.
- The exact qualifier `MCP 검색 표면` remains read-only; this wording does not broaden the rule to every MCP surface.
- No legacy Opus roles and no duplicated global model, effort, orchestration, review, notification, or approval policy are introduced.
- `CLAUDE.md` has no diff.
- A separate fresh post-change execution demonstrates that the global Codex instructions and project `AGENTS.md` are loaded together.
- The prior direct guard-script response and actual runtime hook invocation events are recorded separately; hooks/list health output is baseline status and is not a substitute for runtime invocation evidence.
- Final web review, task-specific checks, and explicit final Astra acceptance are recorded before commit.

## Worker and review record

- Selected worker: `gpt-5.6-luna`, high.
- Technical-fit reason: bounded documentation extraction with exact preservation and verification of project rules and commands.
- Independent plan review: verified `GPT-5.6 Sol (매우 높음)` using the authorized exhausted-Pro quota fallback; this was not Pro.
- Reviewer disposition: `REVISE`, with three bounded clarifications concerning single-active-worker wording, separated post-change evidence, and the worker fit record.
- Astra disposition: all three clarifications accepted and resolved. Record this as `REVISE resolved by Astra`, not reviewer `GO`.
- Review response: `/private/tmp/syllva-agents-plan-web/response_syllva-agents-plan-review_20260910_094356_96114_d7eca8.md`.
- Review URL: https://chatgpt.com/c/6aa1fd58-0bec-83e8-b70a-f454d2acb99d
- Gemini review: `N/A`; this bundle contains no UI, design, approval, error, or notification flows.
- Phone checks: excluded.

## Final review and acceptance

- Independent final web review: verified actual `GPT-5.6 Sol (매우 높음)`, disposition `GO`, 0 blockers.
- Final review URL: https://chatgpt.com/c/6aa1ff4f-8c5c-83ee-b793-7e1d8e6fc781
- Final review response: `/private/tmp/syllva-agents-final-web/response_syllva-agents-final-review_20260910_095220_96822_a42191.md`.
- The reviewer inspected the seven-file pack, independently recomputed the global/project hashes, and matched the recorded values.
- Optional precision accepted by Astra: the runtime record identifies the matched `pwd` command as exited 0; the hook itself is recorded as status `completed`, without inferring a hook exit code. This caused no scope or risk change and required no rereview.
- Candidate acceptance: Astra accepted the bundle based on the resolved plan review, final `GO`, preserved content and commands, byte-identical `CLAUDE.md`, fresh full global-plus-project prompt loading, hooks/list trust plus actual runtime events, and targeted documentation checks.
- Overall final acceptance: Astra accepted. No user decisions remain pending.

## Supplied hook and runtime evidence

- For `/Users/admin/Project/Syllva`, app-server hooks/list reported the user `PreToolUse` guard as enabled, trusted, and error-free with current hash `sha256:39ddc398947773407a6808b14f7748793b0f180218b0cc9517ec3b2e1684296f`.
- Fresh ephemeral read-only app-server runtime thread `01a088c5-c583-7bf3-b26a-df86c1900e85` ran exactly `pwd`. The user hook reported status `completed` in 63 ms. The matched `pwd` command `exec-fcfd1681-834d-4f43-ab6a-be2ee82542d3` exited 0 and confirmed the Syllva working directory. Raw evidence: `/private/tmp/syllva-hook-runtime.json`.
- This proves personal `PreToolUse` applies to the observed shell execution. It does not establish coverage for every path or blocked-command case.
- The prior direct guard script response was `exit0 {}` and is retained without rerun.
- Hook acceptance check complete: the supplied hooks/list trust/status readback and the actual runtime invocation evidence are both recorded; the bounded claim remains limited to the observed `pwd` shell execution and does not generalize to every path or blocked-command case.

## Post-change verification

- Fresh codex debug prompt-input loaded the exact complete global `AGENTS.md` and exact final project `AGENTS.md` in the same invocation.
- Global instructions: 7,929 bytes, SHA-256 `2a6d50202b2dcf708f4321e5c81e3a7a0e4536e02030fc22e88713a025e4fac7`.
- Project instructions: 3,718 bytes, SHA-256 `ac1b2c7e20e6098282cc55128760ca9bb63623c84759da5b8ff138effddcad7e`.
- The legacy role table is absent from the final project instructions.
- `CLAUDE.md` byte comparison against baseline commit `bfc592b4fc15df68df599bc8b0811cace521b1f7` is identical.
- Load acceptance and hook acceptance checks are complete. Raw evidence: `/private/tmp/syllva-agents-load-evidence.json` and `/private/tmp/syllva-agents-final-prompt.json`.

## Implementation and delivery status

- Plan review is complete and Astra accepted the three clarification fixes; implementation is authorized.
- The two owned documentation files are the only intended changes.
- Self-checks completed: `git diff --check` passed; required project invariant/command phrases and record fields were found; `CLAUDE.md` has no diff; git reports exactly the two intended new files and no other changes. The full application test suite was intentionally not run because this is documentation-only.
- Untracked-file whitespace checks completed: `git diff --no-index --check /dev/null AGENTS.md` and the equivalent command for `docs/codex-policy-adoption.md` both returned the expected exit 1 for a `/dev/null` comparison and emitted no whitespace errors.
- Staged whitespace check: `git diff --cached --check` passed with exit 0 for both staged files.
- Final web review and Astra final acceptance are complete.
- Approved commit subject: `Add project Codex instructions and policy adoption record`.
- Delivery branch: `codex/syllva-project-agents`.
- Delivery commit: the commit adding this record on `codex/syllva-project-agents`; exact SHA is reported from `git log` after delivery.
