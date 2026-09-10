# Phase 4–8 execution record

Updated: 2026-09-09. Active scope is **Phase4 only**; Phase5–8 and push are excluded.

Current implementation rev10 fixes both root-confirmed rev9 web blockers: incomplete bounded page coverage and false audit after human restoration during effect-marker persistence. Full tests pass **866 per Python3.11.16/3.14.7**; both exact126-file source-copy and actual attachment reconstructions pass **731 tests**. Root full-flow, crash/recovery and before/after probes, compilation and projection checks pass. Ruff188/mypy74 remain non-clean; no new normalized type errors.

**Gemini3.8Flash high: GO accepted** after251 full-output checks,5 exact source quotation checks and report provenance corrections. Independent731pytest/projection execution confirmed. **Codex native insane-review0.6.8 web: GO accepted** on verified Latest/매우 높음 at https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb . Normal web recovery finished in1254seconds/exit0, with independently reproduced fixes and no blocker. Both applicable GO verdicts and root verification now match: **Phase4 implementation complete**, local commit `3f190fc`. See [verification](phase4-verification.md) and latest handoff. All older checkpoints below are historical.

## Roles and gates

- Latest user correction: Sonnet review was a one-time request. Do not use Sonnet for future reviews; use Codex native insane-review and Gemini3.8Flashhigh only. The planning role is separate.

- Current Codex root: orchestration, specification interpretation, integration and verification.
- Claude Sonnet 5 high: each Phase plan draft. User explicitly requested restoring Sonnet login rather than substituting the planner.
- GPT-5.6 Luna max: implementation after the plan review gate.
- Independent reviewers: insane-review GPT-6 Pro and Gemini 3.8 Flash high. Neither receives the other's findings before submitting its own review.
- User subsequently authorized **very-high / extended** insane-review when Pro is unavailable. Resume with verified UI Latest + 매우 높음; do not invent a numeric model version if the UI omits it. Gemini high remains the second independent reviewer.
- Sequence per Phase: plan → dual plan review → fixes → implementation → direct verification → dual implementation review → fixes → completion record/commit.
- Both reviewers must return GO on the applicable revision. Root resolves disagreement by checking the frozen contract and reproducing the finding. An unavailable reviewer does not count as GO.
- No live provider/client validation is inferred from Fake tests. Missing external validation remains explicitly pending/deferred.

## Baseline

- Starting HEAD: `bfc592b` (Phase 3 handoff); implementation baseline `f4ab8b0`.
- `python3 -m pytest -q tests/`: 238 passed.
- `python3 scripts/lint_behavior_projection.py`: all projections match.
- Python AST syntax check: 108 source files passed.
- Initial local modifications: requested GPT-6 Pro configuration in CLAUDE.md and handoff.md.
- Work branch: `codex/phase4-8` (user-authorized continuation after the Pro quota fallback decision).
- Project `.venv` now contains the repository-declared dev/mcp/drive/notion/github/pdf extras. In that environment, baseline pytest is **238 passed**. Baseline Ruff: **190 findings**, saved `.review/baseline-ruff.txt`; baseline mypy: **76 errors in 22 files**, saved `.review/baseline-mypy.txt`. These are existing-source findings, not newly introduced Phase 4 errors, and must not be reported as passing checks.

## Execution status

| Phase | Scope | Status |
|---|---|---|
| 4 | Material Usage proposals, page ranges, human approvals, multi-material retrieval | Complete — rev10 dual GO and root verification accepted |
| 5 | Exam/Activity context, approved scope, provisional warnings | Inactive — outside current user scope |
| 6 | GitHub validation and exact submission ref retrieval | Inactive — outside current user scope |
| 7 | Client packages and support evidence | Inactive — outside current user scope |
| 8 | CLI/worker startup, schedulers, authenticated remote MCP, health and backup | Inactive — outside current user scope |

## External and prerequisite verification

Frozen §41 remains authoritative: C0 → M0 → VS0 → VS0-B → G. The handoff has no recorded PASS evidence for these stages. Provider API implementations, MCP transport/registry and CLI are scaffold stubs at baseline. Later plans must account for these dependencies instead of treating a successful Phase unit suite as completed deployment.

- C0: record target client's authenticated remote MCP discovery and ping, or an explicit deployment-deferred outcome permitted by §41.
- M0: engine fixture through local MCP with domain schemas, provenance and no storage-generic tools.
- VS0: a real professor PDF through normalization, graph, retrieval and a supported client; source unchanged and VERIFY reaches source.
- VS0-B: second client semantics, capabilities, ambiguity and realistic context sizes; no VALIDATED status without evidence.
- G: record Goodnotes approach and Level-2 fallback evidence.
- Windows/macOS: scheduler definitions may be checked locally, but native Windows execution cannot be claimed from macOS tests.
- Provider writes and human approvals must use their designated authority paths. No automated human approval or public-share workaround.

## Tool recovery evidence

- Sonnet collaboration transport failed with `unreadable_encrypted_agent_task` twice. CLI fallback preserves the requested Sonnet 5 model.
- Claude OAuth was expired. User completed login; CLI reports authenticated. Sonnet plan restarted.
- Gemini CLI exposes `gemini-3.8-flash-high` and can run with its required local server permissions.
- insane-review dependencies, dedicated Chrome CDP and ChatGPT login passed checks.
- Installed insane-review 0.6.2 misreads hidden legacy model options in the September picker. `scripts/review_gpt6_pro.py` keeps its packaging and response workflow, adapting only active-model/effort verification. Live preflight verified header `6 Pro`, slider value 4/max 4; no prompt sent during preflight.
- Subsequent fresh-chat verification exposed a disabled Pro choice. The UI tooltip states: “한도에 도달했습니다. 2026년 9월 13일 후에 다시 시도하세요.” Thus the earlier selected `6 Pro` display is not proof that a new Pro review can execute. No GPT-6 review prompt was sent and no GPT-6 review verdict exists. User has been asked to restore access or explicitly choose a different review route; no silent substitution.

## Completion evidence to append

For each Phase, record plan revision, reviewed file set, review report paths/model verification, direct test outcomes, outstanding live checks and commit identifier. No Phase 4–8 completion evidence exists yet.

Phase 4 draft-rev1 Gemini report: `.review/phase4-plan-rev1-gemini.md` (7 blocking, 2 advisory findings; REVISE). Draft snapshot: `.review/phase4-plan-rev1.md`. Root reproduction/interpretation: `.review/phase4-root-findings.md`. Sonnet rev2: `.review/phase4-plan-rev2-sonnet.md`. Root integrated rev3 in the canonical plan, correcting incomplete dependency identity, same-role disjoint ranges, externally reachable unverify/role/Course revocation, transient-vs-stale failure handling and audit retry rules. Gemini rev3 report `.review/phase4-plan-rev3-gemini.md` returned **GO**; reviewed plan snapshot `.review/phase4-plan-rev3.md`. These local review snapshots are gitignored. No GPT verdict and no implementation gate passed.

## Historical resume point (superseded)

1. Await user's pending choice: restore a Pro-capable account, explicitly authorize a different insane-review model, or wait for quota recovery. Do not infer approval from elapsed time; no automatic scheduled wakeup has been created.
2. Recheck actual model/effort and availability. GPT-6 Pro `--preflight` currently denies with the observed quota tooltip and sends no prompt. The wrapper has passed Python syntax checks and live quota-denial verification; do not claim a successful end-to-end review yet.
3. Send canonical rev3 and relevant uncompressed specifications/code to insane-review in bounded complete review sections (the original 58-file pack was ~204k estimated tokens and was never sent). The primary file inventory is `.review/phase4-plan-primary-files.txt`; close any necessary dependency gaps per review scope, splitting rather than compressing.
4. Resolve findings, rerun both reviews as needed for changed revisions, then dispatch Luna max for implementation. Follow the per-Phase gates through 8.

No source implementation, commits or pushes occurred in this run. Changes are orchestration/handoff documents, the Phase 4 plan and the review UI compatibility wrapper. Baseline tests remain the recorded pre-change 238 pass; no new implementation test result is claimed.

### Phase 4 rev3 independent review recovered (2026-09-08)

- insane-review completed with **REVISE**, using verified `ChatGPT Latest (매우 높음 / Extended; UI version unspecified)` under the user's Pro fallback authorization.
- Original report: `.insane-review/response_Syllva_20260908_183435_98099_affd5b.md`; preserved review: `.review/phase4-plan-rev3-insane-review.md`.
- Conversation: https://chatgpt.com/c/6a9fd6b6-9154-83ee-ae16-890faea14044
- Five gate blockers: application-time canonical Proposal ID validation; overlap/multiple capability basis and sibling duplicate checks; explicit trusted source-class/Usage Role policy; exact Course relation lookup and Course Key validation; frozen-compatible optional Decision By lifecycle.
- Gemini rev3 GO does not override this REVISE. Phase 4 implementation remains gated pending resolution and independent re-review.
- Future insane-review runs must use the default repository ChatGPT project (omit `--no-project`). The diagnostic override was mistakenly retained in the rev3 run.
- Project organization repaired: old cached project redirected to home with “project not found”; the full available project list contained no Syllva project. Created `Syllva · eeb93c01` at https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4/project and updated the per-repo cache. Moved the recovered `Plan Review Verdict` conversation into it; verified the project lists the exact conversation ID. No review was resent.

### Phase 4 rev4 correction and re-review started

- Sonnet 5 high supplied full rev4 draft; preserved `.review/phase4-plan-rev4-sonnet.md`. Root integrated required corrections (explicit Role values, candidate validity before selection, distinct equal-chunk spans, Course page/key fields, mirror validation, supported Material classes, direct Material retrieval and correct review filenames).
- Canonical reviewed snapshot `.review/phase4-plan-rev4.md`, SHA256 `65d6164820cbe163b94af12d12c6fffc07692168a8f81d2ba959292731339474`.
- Independent AGY/Gemini review: `/tmp/syllva-phase4-plan-rev4-gemini.log`.
- Independent insane-review: `/tmp/syllva-phase4-plan-rev4-insane.log`, default Syllva project confirmed, Latest/very-high UI verified, 21 full files / approximately 117,397 tokens. Scope list `.review/phase4-plan-rev4-files.txt`; this re-review adds authority policy and graph Fake, and omits unchanged enrichment internals to stay within the full-code budget. Both frozen specifications remain included in full.
- Luna max read-only implementation preparation via Codex CLI: `/tmp/syllva-phase4-luna-prep.log` and final checklist `/tmp/syllva-phase4-luna-prep.md`. No implementation until plan gate resolution.
- Routed Gemini collaboration delegation again failed encrypted-task delivery; no work or review verdict resulted. AGY CLI is the working independent Gemini route.
- AGY/Gemini rev4 returned **GO**, report `.review/phase4-plan-rev4-gemini.md`; no blocking gaps found. Root notes review prose says PAGE_RANGE preserves `Verified=True`, while authoritative plan correctly preserves the existing bool value (including false); implementation follows the plan, not that shorthand. insane-review remains pending.
- Root reproduced baseline overlap order dependence in `.review/phase4-overlap-baseline-repro.txt`: revoked-first rejects, valid-first allows for identical current input. Rev4 implementation must remove this order dependence.
- Sonnet Phase5 draft preparation runs independently at `/tmp/syllva-phase5-sonnet-rev1.log`; it grants no implementation gate and explicitly treats Phase4 as a pending dependency.
- Luna max preparation completed, preserved `.review/phase4-luna-preparation.md`. Root additionally identified lower `MemoryEphemeralStore` all-containing-fingerprints coupling and `retrieval/freshness.py` page-1 fallback; implementation instructions require selected-entry authorization and no fabricated page evidence within rev4 requirements.
- Phase5 raw Sonnet rev1 preserved `.review/phase5-plan-rev1-sonnet.md`; root requires revision before formal review (mandatory Phase4 dependencies, full Exam identity/guard reuse, Course cross-checks, parent-scope follow-up revocation, truthful partial official instructions, actual supplied-vs-external behavior tests). Sonnet correction log `/tmp/syllva-phase5-sonnet-rev2.log`.
- Sonnet Phase5 rev2 received and preserved `.review/phase5-plan-rev2-sonnet.md`. It is not yet a canonical/approved replacement: multiple sections still refer to rev1, so root must consolidate it after Phase4 lands and verify full dependency identity (same session set alone cannot guarantee same Proposal ID when source/old-state/evidence differ), parent revocation, instruction completeness and typed protocols before formal review.
- Sonnet Phase6 independent draft preparation: `/tmp/syllva-phase6-sonnet-rev1.log`. Mandatory Phase4/5 predecessors and exact-ref/no-fallback requirements are explicit.
- Root found additional rev4 blockers during frozen §14.7 recheck; documented `.review/phase4-rev4-root-additional.md`. Target DB is not a Queue property; actual Source Ref/Course mirrors need explicit validation; audit ownership needs explicit guarded enforcement. Therefore Gemini rev4 GO alone is insufficient and implementation remains gated even if the pending reviewer later returns GO. Preserve current snapshot until report recovery, then integrate a new revision and re-review.

### Phase4 rev5 (current)

- Rev4 insane-review returned REVISE with six blockers; preserved `.review/phase4-plan-rev4-insane-review.md`. Root accepts five substantive corrections and the evidence-pack omission of the real prepare_derivative defining file. Original report `.insane-review/response_Syllva_20260908_222819_11123_61ca88.md`.
- Rev5 canonical snapshot `.review/phase4-plan-rev5.md`, SHA256 `9131829781fc8c955b6dd523244c5b242e13e0d3d85a97c202fb626f27f952c7`.
- Corrected exact Queue mirrors/schema, canonical SourceRef identity excluding web_url, audit actor allowlists, issued full Usage range snapshot, explicit separate ApprovalGraphReader/ApprovalSourceReader, unknown target-write outcomes, and real prepare_derivative signature plus full defining file evidence.
- Review partition A = producer/approval/guard/recovery, list `.review/phase4-plan-rev5-a-files.txt` (14 files, ~105,029 tokens); B = retrieval/capability/ephemeral, list `.review/phase4-plan-rev5-b-files.txt` (21 files after DriveReader addition, ~103k tokens). Both include full frozen specs and complete identical rev5; files are never compressed. This avoids the missing-interface evidence from rev4 while keeping each pack below120k. Overall insane-review GO requires BOTH parts GO, plus independent full-plan Gemini GO.
- Current external review logs: `/tmp/syllva-phase4-plan-rev5-gemini.log`, `/tmp/syllva-phase4-plan-rev5-a-insane.log`; B will start after A send confirmation to avoid UI interference.
- Phase6 Sonnet raw draft preserved `.review/phase6-plan-rev1-sonnet.md`; not canonical/approved. It currently defers real GitHub API implementation and needs root scope correction before formal review; full Phase6/8 completion must not be claimed from Fakes alone.
- Gemini rev5 full-plan verdict **GO**, `.review/phase4-plan-rev5-gemini.md`. Both insane-review partitions remain pending.
- Phase6 raw rev1 needs correction: real GitHub API adapter was deferred despite §47 delivery; branch-as-submission reading conflicts with frozen design §35; mid-fetch Activity linkage revalidation omitted. Sonnet rev2 correction log `/tmp/syllva-phase6-sonnet-rev2.log`. No formal Phase6 gate yet.
- Root reproduced ordinary AUTOMATION/APPROVAL_READER audit-field acceptance (six cases), `.review/phase4-audit-field-baseline-repro.txt`, and committed target mutation followed by timeout falsely terminalized as FAILED/mutated=False, `.review/phase4-write-outcome-baseline-repro.txt`. These are explicit implementation acceptance regressions.
- Rev5 insane-review Part A returned REVISE: authoritative entity/normalized-URL-to-SourceRef binding operation is missing; physical Queue duplicate Proposal ID detection must be explicit. Report `.review/phase4-plan-rev5-a-insane-review.md`; root accepts both in `.review/phase4-rev5-a-root-response.md`. Part B remains pending; preserve rev5 until recovered, then integrate one revision.
- Phase6 Sonnet rev2 received (`.review/phase6-plan-rev2-sonnet.md`), now includes real read-only SDK adapter and commit/tag submission policy. It is raw/unapproved; statement calling Phase5 raw rev2 canonical is not authoritative.
- Phase7 Sonnet request failed with session usage limit: 'resets 3:40am (Asia/Seoul)' (observed Sept8 late evening; expected next morning). `/tmp/syllva-phase7-sonnet-rev1.log` contains ONLY this limit message, not a plan. Do not mark drafted or switch planning models silently. Continue Phase4 and prepared Phase5/6 work; retry authorized Sonnet planning after reset when needed. No login failure and no new login approval needed.

### Phase4 rev6 (current canonical, review pending)

- Rev5 Part B REVISE (3 blockers): stale revalidation page fallback path omitted from explicit changes; selected-entry lower ephemeral API + exported helper bypass unspecified; request-side include_provisional bool unenforced. Report `.review/phase4-plan-rev5-b-insane-review.md`. Both part reports accepted and integrated together.
- Canonical `.review/phase4-plan-rev6.md`, SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- New explicit SourceBindingResolver with trusted lookup/validated wrapper; physical Queue rows/unique lookup shared across all lifecycle consumers; same marker index in freshness; selected issued_entry lower-store API and no stripped compatibility helper path; strict ENGINE request bool.
- Independent logs `/tmp/syllva-phase4-plan-rev6-gemini.log`, `/tmp/syllva-phase4-plan-rev6-a-insane.log`, `/tmp/syllva-phase4-plan-rev6-b-insane.log`. A chat https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa01bbb-cd88-83ee-bf02-0e4af45c4264 . B dispatched after A send verification.
- No Phase4 source code edits yet. Luna implementation prompt `/tmp/syllva-phase4-luna-implementation-request.txt` must be updated from rev5 to CURRENT canonical rev6 before dispatch after all current gates resolve. Current branch codex/phase4-8; no commits/pushes.
- Gemini rev6 full-plan verdict GO, `.review/phase4-plan-rev6-gemini.md`; 0 blocking gaps. Both insane-review parts pending. B chat https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa01c6f-2d74-83e8-a1b4-bae7c4103f1b .


### Phase4 rev6 gate passed; implementation active

- Independent plan gates: insane-review Part A GO and Part B GO, plus Gemini full-plan GO. Reports `.review/phase4-plan-rev6-{a,b}-insane-review.md` and `.review/phase4-plan-rev6-gemini.md`. No blockers remain on the reviewed canonical SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`.
- Both ChatGPT reviews used the repository project and UI-verified Latest / 매우 높음. No forced answer.
- Luna max implementation dispatched on the existing branch, prompt `/tmp/syllva-phase4-luna-implementation-request.txt`, log `/tmp/syllva-phase4-luna-implementation.log`, final report `/tmp/syllva-phase4-luna-implementation.md`. Implementation gate is not a completion/release gate. Root verification and both independent implementation reviews remain required before a local commit. No push authorized.
- Optional review refinements for implementation QA: Queue APPLIED audit commits then raises must replay idempotently; use shared validated page index; preserve exact Course cardinality and dereference checks.
- Phase5/6 Sonnet raw drafts remain unapproved; Phase7 planning awaits Sonnet session reset (03:40 Asia/Seoul), not login repair. Continue original Phase4–8 scope.


### User scope update: Phase4 only

User explicitly narrowed active work to Phase4 only. Finish current Luna Phase4 implementation, root verification, independent implementation review and fixes. Do not start Phase5–8 planning/implementation or retry Sonnet Phase7 quota automatically. Existing future-phase drafts are retained as inactive reference, not active work.


### Phase4 implementation checkpoint (2026-09-09)

- Luna max remains active; corrected CLI invocation uses `--approve-for-me` without redundant `--sandbox` (CLI forbids combining them; approve-for-me itself uses workspace-write). Session71738, CLI session ID01a0817c-91e1-7b11-86d4-3e9feb0ad08a, log `/tmp/syllva-phase4-luna-implementation.log`. No final report yet.
- Source edits in progress: domain range/Course/approval identity, trusted Drive binding, ephemeral selector, retrieval/config/freshness, strict fixture migration and guarded Notion writer. Do not run broad tests or reviews against transient half-replaced files.
- Root independent partial checks:4 selected-entry/page tests plus7 strict-request bool tests passed; `/tmp/test_syllva_phase4_root.py`, `.review/phase4-root-partial-verification.txt`. This is NOT full verification.
- Root intermediate findings are `.review/phase4-root-inflight-findings.md` (9 numbered observations so far). Some runtime-confirmed: malformed relation filtering, empty typed SourceRef, malformed page markers, omitted-range sentinel, manager+bare-role bypass, UUID Role write and unguarded region forwarding. Some static/pending final recheck: Queue null/missing mirrors, duplicate raw Usage identity, runtime config fallback and policy revocation. Luna has not been directly notified because its one-shot CLI stdin is closed; re-read these after Luna finishes and correct before external implementation reviews. Root has not modified source concurrently.
- Implementation review prompt drafts `/tmp/syllva-phase4-implementation-a-prompt.txt`, `...-b-prompt.txt`; rebuild complete evidence manifests from final code/tests and split further if >120k tokens. Do not reuse plan manifests without adding all new implementation dependencies.
- Active scope remains Phase4 ONLY per user's latest narrowing. Future Phase5/6 root checklists and raw Sonnet drafts are inactive references. Do not start or retry Phase7 planning.


### Root regression steering checkpoint

- First Luna run deliberately interrupted after baseline migration (239 full-suite passes) to deliver root findings; process31313 exited, unified session71738 closed. Root temporary regression suite found17 failures/11 passes, report `.review/phase4-root-regressions-first-run.txt`. Do not mistake baseline239 for Phase4 acceptance.
- Continuing same working tree with Luna max corrections: prompt `/tmp/syllva-phase4-luna-fixes-request.txt`, log `/tmp/syllva-phase4-luna-fixes.log`, final report `/tmp/syllva-phase4-luna-fixes.md`. The prompt explicitly includes all11 root finding groups and requires all28 temporary regressions plus permanent broader Phase4 tests. No active first-run implementer remains. Root still owns final verification and independent implementation reviews.
- AGY actual model ID verified: `gemini-3.8-flash-high`, run CLI with escalation (read-only service/log initialization needs host access), --mode plan --sandbox --effort high; no permission-bypass flags.
- User scope Phase4 ONLY remains in force.

### Root correction verification progress

- Second Luna run active, unified session74789, log `/tmp/syllva-phase4-luna-fixes.log`. Use latest `exec`/`codex` records to inspect progress; raw log tail often repeats cumulative diff rendering and is not a reliable stage indicator.
- Root regression suite NOW28/28 passes; report `.review/phase4-root-regressions-second-run.txt`. Additional canonical Queue null-mirror probes for Course/Source Ref/Source Hash/Source Version/Target Entity ID all deny. Broader approval/producer/recovery tests and final reviews still pending; no completion claim.
- `.review/venv311` installed using host Python3.11.16 with .[dev]. Freeze `.review/phase4-python311-freeze.txt`. Actual import fails due preexisting private NotionReader.__protocol_attrs__ dependency; recorded as finding12 in `.review/phase4-root-inflight-findings.md`. Root will apply/verify portable optional-protocol fix if second Luna run does not include it, then run full suite on both3.11 and3.14.
- Current remaining root review focuses on full identity/current-dependency recovery, committed-then-raised target/audit, producer post-LLM freshness and bounded inputs, all strict writer entry points and physical duplicate ambiguity. Original11 root finding groups were explicitly delivered to the second Luna run; finding12 was appended afterward and may not have been read by it.

- Root applied finding12 compatibility correction in Notion base declarations only while Luna continued behavioral fixes: TYPE_CHECKING-only optional Material enrichment declaration replaces private Protocol metadata mutation. Python3.11 domain/session reader tests34/34 pass, `.review/phase4-python311-reader-tests.txt`. This is the first root source edit during the implementation runs; preserve it. Remaining behavioral edits still belong to active Luna correction run. Final full suites on both versions remain required.

- Root finding13 runtime reproduction exposed human Decision revocation during source reads still allowing target mutation. Root added immediate pre-write Queue uniqueness/type/semantic/APPROVED+Approve revalidation and strict pre-audit type/semantic checks. Permanent test `tests/contract/test_phase4_prewrite_approval.py`:5 cases pass on both3.14 and3.11; new test Ruff clean. Preserve these small non-overlapping root Notion changes and test in active Luna correction run.

- Root added independent engine-level regression file `tests/contract/test_phase4_followup_revocation.py`:8 post-issue revocations (Verified, Role, same-authority raw Type, full-range narrowing with requested page still inside, Course Key, same-key different Course page, registered source-ref rebind, malformed duplicate Usage ID) and4 overlapping-basis tests (revoked Verified/old fingerprint, both orders). All12 pass on3.14 and3.11. New file Ruff clean. These supplement Luna's shared regression coverage and are root-owned tests; preserve them.
