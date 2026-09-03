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
- Model-independent contract tests (release blockers §50.6–§50.9, §50.18–§50.22): 32 passing.
- Implemented by Codex (Luna, max) across two parallel tasks; gates verified by oversight.

## [1.2.0] — Frozen design baseline

- v1.2 architecture and implementation specifications frozen for implementation.
