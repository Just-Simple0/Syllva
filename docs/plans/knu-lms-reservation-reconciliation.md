# KNU LMS interrupted-reservation reconciliation

Date: 2026-09-27. Status: **separate design revision; current candidate is not accepted**.
Risk: safety-sensitive operational state recovery. Gemini is N/A because this
bundle adds no user-facing UI/design flow.

## Problem

The 2026-2 KNU LMS sidecar has an `active-run.json` left `active` after the
original keeper disappeared. The OS lock is currently free, so there is no
supported way to resume or start a new run: `Reservation.begin()` correctly
fails with `interrupted_reservation_blocked`. The driver contract explicitly
requires operator reconciliation and forbids timeout, deletion, silent theft,
or editing `active-run.json` by hand. This bundle adds the bounded operator
commands described below.

Frozen ULS v1.2 documents do not define this optional LMS sidecar recovery.
This change therefore stays in the sidecar/runtime layer and does not alter the
frozen design, MCP surface, SOURCE/AI/USER ownership, approval semantics, or
retrieval behavior.

## Scope and ownership

This recovery work is independent of the Canvas API/source-intake bundle. It
must be reviewed, implemented, tested, and accepted separately. The human
reconciliation decision remains outside automation.

Owned files:

- `scripts/knu_lms_apply_lock.py`
- `scripts/knu_lms_sync.py` only if needed to expose the operator CLI
- `tests/unit/test_knu_lms_apply_lock.py`
- `tests/unit/test_knu_lms_sync.py` only for the exposed CLI contract
- `docs/operator-guide/lms-sync.md`
- this plan and concise `handoff.md` status

No `.env`, Keychain, Canvas token, provider write, Notion write, scheduler
activation, protected-branch push, or unrelated `RESEARCH/` change is in scope.

## Proposed contract

Add a two-step, operator-owned recovery path. It is never invoked by the normal
heartbeat and never treats elapsed time as proof of abandonment.

1. `reconcile-check` is read-only. It acquires the reservation lock
   non-blockingly and requires the exact current `active` owner/scope.
2. Every required evidence document is parsed and semantically validated
   **before** its hash can enter the candidate. A matching SHA-256 alone is not
   evidence that a document belongs to this interrupted run.
3. Run-bound evidence must identify the exact stale owner/scope/run identity.
   Scope-bound evidence must identify the exact accepted scope/config/projection
   generation it claims to describe. Any owner/scope/generation disagreement,
   absent required identity field, or ambiguous legacy artifact fails closed.
4. Only after semantic validation does the checker bind canonical evidence
   summaries and SHA-256 identities into a deterministic `candidate_hash`.
   Missing, malformed, symlinked, wrong-owner/mode, changing, or semantically
   unrelated evidence fails closed.
5. A human operator reviews that candidate together with the evidence that all
   prior collection/cloud operations are settled. Automation cannot infer this
   decision from age, a free lock, or successful prior status alone.
6. `reconcile-apply` requires the exact old owner ID, exact scope hash, exact
   `candidate_hash`, and the fixed explicit confirmation
   `--confirm-settled RECONCILE_SETTLED`, supplied only after the current human
   decision. It reacquires the OS lock, recomputes the entire candidate, and
   rejects any drift or current lock holder before changing state.
7. The apply step atomically transitions the existing record to `completed`
   while preserving its original identity/start metadata and adding audit
   fields such as `completion_kind=operator_reconciled`, `reconciled_at`, and
   `reconciliation_candidate_hash`. It does not delete or replace the record.
   Only after this durable transition can a later normal `Reservation.begin()`
   create a new owner.

The operator guide states that `reconcile-apply` is a manual exceptional action
requiring a current explicit human decision. The accepted heartbeat driver is
not rewritten by this candidate; its existing interrupted-run fail-closed rule
remains authoritative. The heartbeat must never call reconciliation, generate
approval, or convert a missing keeper into approval.

## Evidence binding

The candidate binds the exact `active-run.json` plus the private evidence needed
to decide whether the prior run is settled. The evidence set may include the
apply/source-alignment journals and accepted plan/readback state, but each file
needs a type-specific validator. A generic `{}` document can never qualify.

At minimum, the implementation must prove:

- `active-run.json`: exact active `owner_id`, exact `scope_hash`, preserved start identity;
- apply/source-alignment journals: exact stale owner and scope, plus their own
  operation/generation identity where the format provides it;
- acceptance/projection/readback artifacts: exact scope/config/projection identity
  expected by the stale run, with no substitution from a later run;
- cross-file references agree with one another before any file hash is accepted.

If an existing legacy artifact cannot prove the required semantic binding, the
correct result is `reconciliation_evidence_unbound`; the operator must resolve
that evidence gap rather than allowing a hash-only candidate.

File contents stay local. A web review receives code/tests and sanitized schema
examples only, never the private runtime evidence.

The check/apply implementation must use no-follow regular-file reads, ownership
and mode checks consistent with the existing runtime helpers, canonical JSON,
and same-directory atomic replacement/fsync for the final record. Any evidence
change between check and apply invalidates the candidate.

## Required verification

Focused tests must prove:

- `reconcile-check` performs no mutation and rejects a busy lock;
- exact active owner/scope are required;
- generic `{}` evidence and an evidence file from another owner/scope/run are rejected;
- a source-alignment journal whose owner differs from `active-run.json` is rejected;
- legacy evidence with no required semantic identity is rejected rather than
  silently grandfathered;
- wrong confirmation, wrong/stale candidate hash, evidence drift, missing or
  symlink evidence, and malformed records all fail without state change;
- `reconcile-apply` preserves the old audit identity and marks the durable
  operator-reconciled completion exactly once;
- a new reservation is admitted only after successful reconciliation;
- the normal hold/status/release path and process-death blocker remain intact;
- no Keychain, browser, Canvas/Notion request, or secret value is touched by
  either reconciliation command.

Run focused pytest, Ruff and targeted mypy in `.review/venv311`, then broader LMS
unit tests if focused checks pass. Run `git diff --check`.

No independent web-review result exists for this candidate. The review
environment itself was healthy; the prior attempt to upload nonpublic repository
content to the external ChatGPT review service was rejected by automatic
approval review. Do not describe that event as a browser/CDP failure and do not
commit this bundle without an authorized review route.

## Live application gate

Implementation acceptance does not itself authorize changing the current
private reservation. After the command is reviewed and tested, present the
concrete `reconcile-check` result to the user. Execute `reconcile-apply` only
after an explicit current user decision for this exact stale reservation. Then
start a fresh keeper and continue the previously authorized read-only 2026-2
Canvas collection.
