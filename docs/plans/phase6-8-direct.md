# Phase 6–8 direct implementation

Started 2026-09-10 from the merged Phase 5 baseline. The current user explicitly
requests direct implementation without orchestration. This run uses the current
assistant as sole designer, implementer and verifier; delegated plan/final review
gates are bypassed for this run. Product security contracts still apply.

## Scope and acceptance

1. Phase 6 (§47): read-only GitHub repository/ref/tree/file adapter; Activity
   result evidence uses stored Submission Ref, never an implicit branch; explicit
   invalid-ref errors; exact provenance and bounded capability follow-up.
2. Phase 7 (§48): installable client projections, matching canonical contract
   version/hash, documented support matrix and manual client E2E checklist.
3. Phase 8 (§49): shared `uls run`, desktop scheduler definitions, functioning
   local MCP and authenticated remote profile using the same engine and separate
   read-only provider credentials; status/doctor, backup and offline guidance.

Risk: cross-component runtime/auth boundary. Defaults remain local, remote is
disabled until explicitly configured, no deployment/installation or public
sharing is performed. Frozen documents and CLAUDE.md remain unchanged.

## Ownership / stage

All assigned changes: current assistant. Stage: implementation accepted; local delivery.
Working branch: `codex/phase6-8-direct`, based on merged Phase 5 `f4c321e`.
No pending user decisions for repository implementation. Live credentials,
external client validation and Windows-host execution are external acceptance
evidence and cannot be inferred from local tests. No push/merge requested.

## Checks / disposition

- Baseline full suite: 995 passed on Python 3.14.
- Completed: exact GitHub/ref/tree/checksum and Activity revalidation tests;
  actual MCP SDK HTTP and stdio protocol tests; capability caller isolation;
  worker/CLI/lock/retry/Partial and durable reprocessing tests; native provider
  boundary integration and SDK media transfer; contract/client package and wheel
  installation checks. Final evidence: `phase6-8-verification.md`.
- Own review found and fixed: historical processing records being rewritten on
  reprocessing; job ordering ties; unrelated USER transcript pointer overwrite;
  enrichment records interfering with canonical source bindings; CLI selecting
  enrichment instead of ingestion for reprocessing. Related regressions pass.
- Independent web/Gemini reviews: deliberately not run, following the user's
  explicit instruction to bypass orchestration and implement directly.
- Delivery: local working branch only. No push, merge, scheduler registration,
  external provider mutation or production deployment is part of this run.
- Final acceptance: Phase 6–8 repository criteria pass. Both Python versions
  pass 1,051 tests; installed/tested wheel Python files exactly match the current
  source. The frozen authorities are unchanged. Live-release limits remain
  explicit in the verification record and handoff. Local implementation commit
  includes this record; retrieve its ID from the branch history.
