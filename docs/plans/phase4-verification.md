# Phase 4 implementation verification

Updated 2026-09-09. Active scope is **Phase4 only**. Implementation rev10 passes root verification. Codex native web and Gemini independent reviews are both GO on rev10; root accepts both and Phase4 implementation is complete in local commit `3f190fc`. Sonnet review was a one-time user request and must not be called for future reviews. Phase5–8 and push remain outside scope.

Rev9 web review completed normally with two root-confirmed blockers: partially missing bounded ranges could apply, and human restoration during observed-effect marker persistence could be incorrectly audited. Rev10 adds complete-range proof and full post-marker reconciliation before the final Queue check and audit. Prior revision GO verdicts are historical.

## Resulting behavior

- A bounded Material Usage producer validates trusted Session/Material graph and source bindings before model invocation and again before persistence. It creates unverified Usage records and canonical human-review Queue proposals; source drift prevents stale proposals.
- MATERIAL_USAGE and PAGE_RANGE apply only from the unique, currently approved Queue record. Canonical proposal semantics, caller mirrors, exact logical and physical identities, both source dependencies and target snapshots are checked across provider reads and writes.
- The existing Queue `Last Error` reserved marker now distinguishes `prepared` intent from `effect_observed`. Actual target invocation and trusted exact-desired readback precede persistence and independent verification of the observed-effect marker. Only durable observed-effect evidence permits audit-only recovery. A prepared-only restart never attributes a later human desired-state edit to the proposal.
- Ambiguous target outcomes retain the marker and do not allow blind replay over human changes. Only trusted `ProviderWriteNotAppliedError` plus exact-old revalidation permits safe retry. Live mutate-then-raise with trusted desired readback can still complete; uncertain effect-marker persistence remains conservative. Phase-less, malformed or unknown markers fail closed.
- Both normal approval and reconciliation validate the complete strict Material page index. Every page in a bounded range must have current strict single-page evidence. Valid p39–40 and p40 proposals in 40-page material apply once; absent p41 and invalid, duplicate, contradictory, gapped or unmarked page evidence remain denied.
- After observed-effect marker persistence, normal apply and audit-only recovery repeat strict target/dependency reconciliation before the final unique Queue check. Human restoration, sibling conflicts and source/graph drift retain the marker and require reconciliation without replay or completion audit.
- Initial retrieval and follow-up chunk access revalidate current Usage, Role, verification policy, Material Type, full range, Course, source identity and fingerprint. Capabilities remain paired with retained evidence through content budgets. Revocation removes authority while independently valid overlapping Usage remains supported.
- Target physical ambiguity is scoped to the relevant basis. Unrelated malformed Usage rows cannot poison independent work; raw exact-tuple siblings still block regardless of ID/Verified validity. Navigation-only SourceRef changes preserve identity.

## Root verification

| Check | Result |
| --- | --- |
| Full pytest, Python3.11.16 | **866 passed** |
| Full pytest, Python3.14.7 | **866 passed** |
| New rev10 regression contracts | **30 passed per interpreter** |
| Compilation, Behavior Contract projections, git whitespace | Passed |
| Producer → Queue → approval → retrieval → replay → revocation | Passed |
| Both operations through the same complete flow at p40 and p39–40 | Passed; one target mutation each |
| Prepared crash → external desired → fresh applier, both operations | APPROVED/reconciliation required; zero target writes and no completion audit |
| Exact126-file source reconstruction | **731 passed** |
| Actual repomix attachment reconstructed in isolation | **731 passed**; no omitted, extra or mismatched lines |
| Six Behavior projections in both isolated reconstructions | Passed |
| Ruff | **188 findings**; baseline190, rev8=186 |
| Mypy | **74 errors**; baseline76, no new normalized errors |

Static checks are not clean. Rev9 adds two conservative catch/readback branches for unknown effect-marker persistence outcomes. The new test files pass Ruff. Root evidence is in `.review/phase4-root-rev10-results.json`, the referenced command logs, and `.review/phase4-root-rev10-static-diff.json`.

Rev9 adds37 regression cases. The first full integration run was834passed/2failed on each interpreter: the old rev3 `test_inconclusive_target_outcome_never_audits_success_or_overwrites_human[unavailable-*]` expected APPLIED on a later desired read even though no effect was observed during the target attempt. Root strengthened that expectation to APPROVED/reconciliation-required, no target replay and no completion audit. The divergent-state SUPERSEDED denial is unchanged. This correction prevents the independently reproduced crash/attribution defect rather than weakening a denial test. Final rev9 full runs passed836 tests each. Rev10 adds30 permanent cases, all passing on both interpreters; against sealed rev9 source they produce16 expected failures and14 passes. Current full runs pass866 tests each.

## Frozen §45 acceptance mapping

| Criterion | Permanent contract coverage |
| --- | --- |
| AI cannot set Verified true | `test_human_gate_writes.py` |
| Adapter guard blocks prohibited writes | `test_phase4_writer_boundary.py`, `test_phase4_remaining_contracts.py` |
| Material Usage proposal enters Queue | `test_phase4_guarded_workflow.py` |
| Only current approved proposals apply | `test_phase4_rev3_approval.py`, `test_phase4_rev4_approval.py`, `test_phase4_rev8_approval.py`, `test_phase4_rev9_recovery.py` |
| Queue lifecycle and idempotency | `test_automation_queue_lifecycle.py`, `test_phase4_rev9_pages.py` |
| Confirmed relation immediately affects retrieval | `test_phase4_guarded_workflow.py::test_guarded_approval_refreshes_same_engine_capability` |
| Unverified evidence follows intent/config | `test_get_session_context.py`, `test_phase4_remaining_contracts.py`, `test_phase4_rev4_retrieval.py` |

All paths in the table are under `tests/contract/`. Root additionally exercised producer-generated p40 proposals, both operations, same-engine retrieval, replay and revocation in `.review/phase4-rev10-root-page-range-smoke.py`.

## Independent integrated review gate

The approved plan remains revision6, SHA256 `901c3e046ede6b39f9863d84bf4aeb41b4bbf3b4244ff9280e6940d01a768058`. Both frozen specifications are unchanged. The plan's historic UNAPPROVED header is retained; subsequent dual plan GO is recorded in the handoff.

Sealed manifest `.review/phase4-integrated-review-files-rev10.txt` and hashes `.review/phase4-integrated-review-hashes-rev10.json` cover126 complete files with source/test transitive dependencies, SQL, configuration and all Behavior projections. Compared with rev9,123 files are identical, the shared Notion adapter is changed, and two regression files are added. Attachment `.insane-review/pack_Syllva_20260909_203041_77349_0961a8.md` is complete with no source compression or removal. Actual attachment contents and731 isolated tests were audited before upload: `.review/phase4-rev10-packed-content-audit.json`.

- **Web, GO:** user-requested Codex native `insane-review-codex0.6.8`, verified **ChatGPT Latest / 매우 높음**, at https://chatgpt.com/c/6aa13845-5fdc-83e9-a3e9-b3e32c1e9bdb . Pro is disabled in the observed UI; the explicitly authorized fallback applies. Numeric model version is not exposed and is not invented. The same independent reviewer receives the new complete attachment; prior source coverage may be reused only after exact content equality is verified, with full review of changes and integrated effects. Native packing/attachment/bound-response/harvest are retained, with a runtime visible-menu compatibility adapter and no forced early answer. Normal final recovery completed in1254seconds/exit0. The reviewer independently ran731tests, compileall and all projections, reproduced both former blockers and additional combined recovery/sibling cases, and found no blocker. Log `.review/phase4-rev10-insane-review.log`; full report `.review/phase4-rev10-insane-review-final.md`. Root verified coverage equality and accepted the result.
- **Gemini3.8Flash high, GO:** fresh independent subagent Descartes `01a085ef-7f18-7b41-9c12-6678da68018b`, completed all251 pages of the sealed126-file snapshot. Root verified every full output and5 source quotations. Reviewer independently ran731 tests and projection lint successfully. Reporting corrections distinguish isolated mypy68 from full-workspace74 and workspace reproduction from sealed pytest imports; static checks remain non-clean. See `.review/phase4-rev10-gemini-{final.md,addendum.md,audit.json}`. Reviewer closed; Gemini GO accepted.

Reviewers do not receive each other's verdicts. Historical Gemini/Sonnet results do not satisfy the current gate. Sonnet agents are closed and further review calls are prohibited by the latest user instruction. Earlier reviews, corrections and the normally recovered rev9 web REVISE remain in `handoff.md`.

Optional web suggestions add combined regression cases already independently reproduced as fail-closed; they are non-blocking and require no implementation correction. Root final acceptance is `.review/phase4-rev10-final-acceptance.json`; all126 reviewed file hashes still match the working tree.

## Practical limits

Tests use provider-neutral fakes and isolated local reconstructions. They do not establish live provider SDK/client behavior, authenticated remote MCP deployment, cross-provider atomicity or multi-worker safety. The accepted design uses a single active worker and conservative reconciliation for ambiguous writes. Frozen §41 prerequisite/live validation stages remain deferred as recorded in the execution handoff; no new external PASS or VALIDATED claim is made.
