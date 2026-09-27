# ULS handoff
Updated 2026-09-28. Repo: `/Users/admin/Project/Syllva`. Branch: `codex/protected-secret-file-and-credential-set`.

Do not touch unrelated `RESEARCH/`, secrets, or `CLAUDE.md`. No push/merge to protected branches is authorized.

## Accepted baseline

- C5/C6 core: `a868039`.
- Remote MCP Google OAuth: `216e996`; prior web/Gemini final rereviews GO.
- Semester-scoped retrieval v1.3: `2489fac` plus later handoff commits; full suite previously 1793 passed / 3 skipped.
- MCP search remains read-only. Preserve SOURCE/AI/USER and human-owned academic approvals.
- `syllva.dev` / `mcp.syllva.dev` Cloudflare fixed-domain route works.
- Claude remote MCP OAuth + `uls.ping`: passed.
- Codex remote MCP OAuth + `uls.ping`: passed.
- Antigravity/Gemini remote MCP `uls.ping`: still unproven/deferred.

## Live academic state

- Notion/Drive live doctor checks are green.
- 2026-2 currently has 0 Sessions, 0 Materials, 0 File Intake, 0 Input Request; upload folder is empty.
- Current `SOURCE_UNAVAILABLE` is caused by no real Session, not provider outage. Never fabricate one.

## Canvas API / intake candidates

Current `scripts/knu_lms_*` work and related docs/tests are checkpointed for continuity but are **not accepted** as a released Canvas/LMS design. Keep them separate from GUI work and inactive until separately accepted.

Two independent plans remain:
- `docs/plans/knu-lms-api-semester.md`: generic Canvas direction; exact collection must require `api_code_verified`; real Canvas file -> bounded exact bytes/hash -> private Drive -> existing intake. PDF may prove SOURCE -> Material only. Session requires a real transcript-compatible source plus USER-owned Session inputs.
- `docs/plans/knu-lms-reservation-reconciliation.md`: stale reservation recovery requires semantic owner/scope/run binding; no hash-only reclaim or timeout takeover.

No live Canvas credential/provider write, Drive import, reconciliation apply, Notion write, or push is authorized for these candidates.

## Local Settings Web GUI PLAN — current active task

Files:
- `docs/plans/local-settings-web-gui.md`
- `docs/plans/local-settings-web-gui-interaction-mock.md`

Goal: one localhost-only nondeveloper GUI for config, Canvas, Drive, Notion, automation, and Remote MCP settings while keeping config/secrets/provider bindings in their existing authoritative stores.

Plan now fixes:
- single-use <=30s bootstrap URL -> consumed HttpOnly/SameSite session -> 303 clean URL;
- exact Host on all requests; GET navigation may omit Origin; mutations require same-origin + CSRF;
- explicit session close + inactivity-expiry UX;
- typed config mutation preserving unknown sections, redacted diff, generation conflict handling;
- credential writes return new `config_generation` without clearing dirty form state;
- secret-free multi-store transaction journal with explicit Partial recovery;
- Google service-account files use 64 KiB credential-file limit;
- fixed Canvas `CANVAS_PAT` keyring role/service/profile locator;
- revoked/unreachable Canvas tokens remain locally forgettable;
- dedicated local OAuth-grant reset endpoint;
- Canvas Pause/Disable Sync is separate from Forget Token;
- resumable stepper: root storage/Notion parent -> Canvas -> Academic course mappings;
- restart-required is explicit and never a hidden Save side effect.

Gemini UI/flow plan review:
- actual model: `gemini-3.8-flash-high`.
- initial verdict: REVISE with R1-R10.
- all R1-R10 were integrated.
- targeted rereview verdict: **GO**; mock artifact requirement satisfied; no new blocker.

Web ChatGPT plan review:
- **NOT completed / NOT sent**.
- Desired reviewer is **GPT-5.6 Sol + Extra High (`매우 높음`)**. Do not call this Pro.
- `insane-review` v0.6.8 fails before submission because its old model/effort selector verifier no longer matches the current ChatGPT UI. This is not a quota failure.
- Pack: `.insane-review/pack_Syllva_20260927_212502_89012_2d2625.md`.
- Prompt: `.insane-review/local-settings-plan-review-prompt.txt`.
- Aside successfully opened the logged-in ChatGPT UI, selected GPT-5.6 Sol, and visibly verified Extra High 4/4, but the review was not uploaded/sent before Aside stopped.
- Codex in-app browser reached ChatGPT while logged out; Google authentication was not completed. Do not ask for or paste passwords into chat.

PLAN status: **pending required web ChatGPT review**. Do not implement GUI-1 until that gate is complete.

## Current blocker / fresh-session recovery

Repeated error:

`stream disconnected before completion: ChatGPT web turn is missing cwd in trusted Codex environment context`

The local repo/worktree is normal. The failure is consistent with the Codex <-> ChatGPT web bridge receiving trusted environment metadata without an explicit `cwd`. Passing `workdir` to individual shell calls does not repair that higher-level web-turn context.

Start the next Codex session directly rooted at `/Users/admin/Project/Syllva`. Verify the new session exposes that path as `cwd` before retrying browser/web review. If the same error reproduces immediately in a fresh rooted session, treat it as a Codex product/runtime bug rather than a Syllva, Cloudflare, Google, repomix, or model-selection failure.

## Resume order

1. Confirm fresh-session `cwd=/Users/admin/Project/Syllva` and that the stream error is gone.
2. Run the required web ChatGPT PLAN review using the existing pack and prompt; visibly verify GPT-5.6 Sol + Extra High.
3. If REQUIRED findings exist, update the plan and run a targeted web rereview; rerun Gemini only if UI/flow changes materially.
4. After both plan gates GO, start GUI-1 only: secure local settings shell/session, typed config snapshot + redacted diff/apply, transaction journal, resumable Overview/setup shell.
5. Keep Canvas ingestion/reconciliation candidates inactive until their own acceptance path is complete.

## Checkpoint note

The 2026-09-28 commit explicitly requested by the user is a continuity checkpoint before opening a fresh Codex session. It preserves current candidates and plans; it does **not** constitute plan/final acceptance, web-review completion, deployment approval, or push authorization.
