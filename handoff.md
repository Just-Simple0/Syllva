# ULS handoff
Updated 2026-09-20. Branch: codex/protected-secret-file-and-credential-set; upstream origin/same branch.
C5 foundation accepted and committed: 14ffda98238bf125bc2202a226e646a102cd9bcf.
Authorities: AGENTS.md, frozen v1.2 design/implementation specs, docs/ux/intake-execution-contract.md rev10.
Preserve secrets/config.yaml/frozen specs and unrelated untracked RESEARCH/. No pending code edits.

## Completed
C1 542e1d4; C4 b7b102f; C2 970d34d; C8 02bebff; C3 00067fb; C7 1b17805.
C5 foundation 14ffda9; full C5 is NOT complete. Next: C5 producer integration -> C6.
Actual intake gate: validate_request_input(); route_intake() is unused.

## C5 foundation
Strict v2 envelope/ID; immutable receipt ledger; generation claim/bind; canonical outbox;
durable invocation ownership; physical row proof/adoption; proof-required audit; exact RESOLVED recovery.
Atomic adoption and operation-specific baseline checks precede persistence/ownership.
Incomplete migrated binding or unknown proof mode fails closed.
All v2 public/wrapper calls require GuardedNotionWriter, including proof-backed replay.
Final Queue state/decision/marker-phase and full-payload signature checks precede marker dispatch.
HELD pre-write failures release; MUTATING ambiguity remains locked. Typed no-effect releases only
after durable exact-baseline proof. Marker lifecycle preserves terminal reasons and human Decision.
No generic crashed HELD/MUTATING unlock, expiry recovery, or legacy reserve/reconcile bypass.

## Accepted evidence
Full pytest1677 passed/3 skipped/2 dependency warnings, no exclusions; focused160.
Own3 C5 tests Ruff/Mypy pass. Full Ruff223->218, Mypy134->134; zero introduced diagnostics.
Parent hash12/diff/projection checks passed. Evidence: .review/c5-worker-notes.md (newest first),
.review/c5-lint-type-delta.json, .review/c5-final-freeze-sha256.json.
Plan: .insane-review/response_prompt_20260920_125328_26490_70531b.md,
.review/c5-gemini-explicit-plan.md; design .review/c5-correction-plan-review.md.
Final state GO: .insane-review/response_Syllva_20260920_141455_31651_70e6bb.md.
Final HAA GO: .insane-review/response_Syllva_20260920_151138_34564_650995.md.
Final Gemini high GO: .review/c5-gemini-final-r9.md. All required findings closed.
Web actual GPT-5.6 Sol / Very high, explicitly user-authorized despite Pro UI failure; never call it Pro.
Worker Hooke 01a0bb23-6589-7653-b24b-6f483143195e: gpt-5.6-luna max (state/concurrency fit).
Gemini Noether 01a0bcf1-4701-7223-93ed-b2c3f315a8b5: explicit gemini-3.8-flash high.

## Next slices
C5 producer: thread request/receipt/hash; build envelopes; claim/bind/publish durable outbox;
activate mandatory envelope + legacy-unapplied denial; migrate producer tests.
Also owns active/submitted/cancel/hash freshness and fully provider-qualified slot identity.
Historical receipt dedup is already in foundation. Plan/review this slice before implementation.
C6: request worker, confirmed-lecture.v1 evidence, StudyNoteGenerator, Drive AI_STUDY_NOTE
staging, named AI-region publish, retries (3;1/5/15m), dashboard statuses. Plan separately.

## Resume
.venv/bin/python -m pytest tests/ -q (JWT installed; no historical exclusions).
Review engine: /Users/admin/.codex/plugins/cache/gptaku-codex/insane-review-codex/0.6.8/bin/pack_and_ask.py
Use --model '매우 높음'; split full-code state/HAA packets (~100k/~110k), no compression.
If sent, harvest manifest; never resend. Saved Chrome background; browser/network/git writes need escalation.
Keep this file concise English; replace stale status. Commit only assigned changes on the feature branch.
