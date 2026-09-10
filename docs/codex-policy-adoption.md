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

## Corrected-base Phase 4 handoff follow-up

- This follow-up supersedes the stale Phase 3-based handoff audit/plan. Correct base is Phase 4 already merged via PR1 into `main` at `e55705f829ade6a92d8140cf127be849a2d80b43` on 2026-09-10 09:32:55 KST. The policy follow-up started from HEAD `f974c7f` on `codex/syllva-project-agents`; the final branch state is the delivery merge commit recorded after commit, and the policy follow-up is not pushed.
- Parent merged `origin/main` with `--no-commit --no-ff`. Incoming Phase 4 code, tests, frozen documents, and merged `CLAUDE.md` are preserved as upstream content. The corrected package owns exactly `AGENTS.md`, `handoff.md`, and this appended record section.
- The initial f974 policy-adoption snapshot owned two files and recorded the prior `AGENTS.md` hash `ac1b2c7e20e6098282cc55128760ca9bb63623c84759da5b8ff138effddcad7e`. This corrected follow-up owns three files (`AGENTS.md`, `handoff.md`, and this record); the revised project instructions are 4,042 bytes with SHA-256 `935bb5bb36825e019cf0b0ba007335bcd878c23fca2e2102cf5823356ee55eb7`.
- Targeted product-rule audit found one wording conflict in the prior AGENTS human-only bullet. The final clarification states: human-owned approval/confirmation; AI and ordinary automation cannot independently approve or promote; for automated application, only the specified `HumanApprovalApplier` may apply the exact currently valid, attributable human-approved change after required policy, freshness, and identity checks. It cannot create human approval or claim Phase 5 implementation.
- The MCP rule remains scoped to the **MCP search surface (MCP 검색 표면)** as a read-only contract/scaffold boundary. The merged MCP modules are stubs; this record makes no deployed MCP or live-client validation claim.
- Corrected-base plan review: actual UI-verified `GPT-5.6 Sol / 매우 높음`, authorized review-period fallback because the user-verified Pro quota was exhausted, URL `https://chatgpt.com/c/6aa204d8-5eac-83ee-8580-a53a19f108e3`, response `/private/tmp/syllva-phase4-policy-plan-web/response_syllva-phase4-policy-plan-review_20260910_101555_99755_14faf7.md`, disposition `REVISE` with one wording blocker, resolved and accepted by Astra. This is separate from the superseded Phase 3 review and is not called Pro or treated as a permanent fallback.
- Final web review: actual UI-verified `GPT-5.6 Sol / 매우 높음`, disposition `GO`, 0 blockers; URL `https://chatgpt.com/c/6aa20756-c91c-83ee-a3d0-1d285da58da2`; response `/private/tmp/syllva-phase4-policy-final-web/response_syllva-phase4-policy-final-review_20260910_102634_1730_0d32f9.md`. The reviewer explicitly reread the human-only clarification against frozen implementation §15.2.1 and confirmed no ordinary automation authority was added; the approved applier remains constrained.
- Astra candidate and overall final acceptance: accepted based on the resolved corrected-base plan review, final `GO`, fresh full global-plus-project load hashes, exact upstream code/CLAUDE/frozen tree preservation, byte-exact historical tail, active handoff links, and scoped documentation checks. Gemini is N/A because there is no UI or approval-flow implementation change; no new phone or hook-runtime evidence was required, and prior observed-`pwd` hook evidence was reused.
- Post-change prompt-load acceptance is complete: one fresh Codex debug invocation loaded the entire global instructions (7,929 bytes, SHA-256 `2a6d50202b2dcf708f4321e5c81e3a7a0e4536e02030fc22e88713a025e4fac7`) and revised project instructions (4,042 bytes, SHA-256 `935bb5bb36825e019cf0b0ba007335bcd878c23fca2e2102cf5823356ee55eb7`) together. Only the human-only bullet differs from the f974 AGENTS reconstruction. Raw evidence: `/private/tmp/syllva-phase4-policy-load-evidence.json` and `/private/tmp/syllva-phase4-policy-prompt.json`.
- Exact upstream deviation paths are the three documents only; the historical handoff tail after `**아래는 과거 진행 이력이다.**` is byte-exact to `origin/main`. Fresh `git ls-remote` confirms `main` remains `e55705f829ade6a92d8140cf127be849a2d80b43`.
- Merged Phase 4 evidence preserved in `handoff.md`, `docs/plans/phase4-verification.md`, and `docs/plans/phase4-material-usage.md`: 866 tests on Python 3.11.16 and 3.14.7, 30 rev10 regressions per interpreter, 731 reconstruction tests, Ruff 188 findings, mypy 74 errors, provider-neutral/fake coverage, live validation deferred, and the non-CAS visibility limitation. Phase 5–8 remain unauthorized.
- Documentation self-checks completed: `git diff --check` passed; the historical handoff tail comparison passed; exact deviations from `origin/main` are the three owned documents; the revised `AGENTS.md` hash is `935bb5bb36825e019cf0b0ba007335bcd878c23fca2e2102cf5823356ee55eb7`. `git diff --cached --check origin/main` passed with exit 0, and `git diff --cached --name-only origin/main` returned exactly `AGENTS.md`, `docs/codex-policy-adoption.md`, and `handoff.md`; source, frozen documents, and merged upstream `CLAUDE.md` had no deviations.
