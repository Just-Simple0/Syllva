# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Initial repository scaffold derived from the frozen v1.2 design and implementation
  specifications (repository layout §3, domain types §6, StateStore §7–8,
  EphemeralStore §9, Retrieval Engine §20, MCP tools §24, Behavior Contract §31).
- Fully implemented normative `Locator` grammar, canonical AST, serialization, and
  typed containment (spec §6.3).
- Domain enums, `SourceRef`, `SourceFingerprint`, provenance, and error taxonomy.
- StateStore / EphemeralStore protocol contracts and SQLite initial migration.
- Config schema and example config / `.env` templates.

### Phase 1 — Core Hardening (spec §42)
- `SQLiteStateStore`: repeatable migrations, job lifecycle, source files/versions,
  checkpoints, and source-bound idempotent entity allocation (§8.6.1, `BEGIN IMMEDIATE`).
- Deterministic `job_key` derivation (§8.1.1, 0x1F-separated SHA-256).
- `MemoryEphemeralStore`: process-local, TTL-bounded, restart-invalidating resolution
  handles and context capabilities; locator authorization via typed containment (§9, §25).
- Local single-worker lock with PID/host metadata and stale recovery (§40).
- Retry/rate-limit policy: error classes + bounded exponential backoff with jitter (§36).
- Config loader/schema/validation with required security checks (§5, §38).
- Notion human-gate write policy: defensive write guards (§15.1), Rich-text alias parsing
  (§14.0), Automation Queue state machine, `ApprovalReader`, and `HumanApprovalApplier`
  with stale-approval revalidation and self-approval prevention (§15.2, §33).
- Model-independent contract tests (release blockers §50.6–§50.9, §50.18–§50.22).
- Implemented by Codex (Luna, max) across two parallel tasks; gates verified by oversight.

### Phase 1 — Review hardening (dual independent review)
- Reviewed by insane-review (GPT-5.6 Sol, High) + AGY Gemini 3.8 Flash (High); oversight
  (Opus 4.8) adjudicated and independently reproduced each finding before/after fixes.
- Security/correctness fixes across four Codex fix rounds:
  - Automation Queue write guard identified by configured DB id, fail-closed on unknown
    targets (§15.1/§33); `HumanApprovalApplier` requires a real human `Decision By`
    (no machine self-approval); create/update Decision separation (§15.2).
  - `load_config` is fail-closed and strictly type-checks every boolean config field (§5/§27).
  - Context capabilities bind source fingerprints; `authorize_locator` is fail-closed and
    raises `LOCATOR_STALE` on mismatch, denies when no current fingerprint is supplied (§25).
  - Canonical `job_key` enforced at the StateStore boundary; `register_source_version`
    entity consistency (§8.1.1/§8.6.1); retry re-queue transitions + provider `Retry-After`
    honored uncapped (§36); truthy-bypass guard for human-only fields (§15.1).
  - Defined system-transition path restored for automation `SUPERSEDED`/`FAILED` (§15.1/§15.2).
  - Worker lock made TOCTOU-safe (fd advisory lock); ephemeral candidate-id leak/rollback
    and single-use resolution consumption (§9); domain `EphemeralStore` contract aligned to
    the fail-closed §25 signature with a contract-conformance test.
- Final state: 86 model-independent contract/unit tests passing; both reviewers GO.

## [1.2.0] — Frozen design baseline

- v1.2 architecture and implementation specifications frozen for implementation.
