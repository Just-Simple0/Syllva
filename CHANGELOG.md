# Changelog

[한국어](CHANGELOG.ko.md)

All notable repository changes are documented here. The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), but Syllva has not yet published a stable public package release.

## [Unreleased]

No public release is currently published.

## [0.1.3] — Beta repository baseline (unpublished)

### Changed

- Reframed the public project identity as **Syllva** while retaining `uls` as the CLI and University Learning System as the core system name.
- Set package metadata/runtime `__version__` to **0.1.3** and documented the release as beta/unpublished.
- Kept `PROTOCOL_VERSION = "1.2"`; package release version and frozen core protocol version are independent.
- Adopted the **MIT License**.
- Rewrote the public README around product purpose, user workflow, status, quick start, architecture, and trust boundaries.
- Added paired English/Korean public documentation (`*.md` + `*.ko.md`) for user, operator, concept, reference, client, and deployment paths.

### Current beta capabilities

- Model-neutral domain and retrieval core with source/provenance handling.
- SQLite durable orchestration state plus in-memory bounded capabilities.
- Transcript/PDF normalization paths and source freshness/authority checks.
- Material/session/exam/activity retrieval and proposal/enrichment infrastructure.
- Read-only MCP tool surface shared across supported client projections.
- Semester intake preview using explicit Drive/Notion identities and user-owned submit/cancel gates.
- Optional KNU/Canvas-oriented LMS sidecar with separate operational gating.
- macOS/Windows local scheduling templates and development remote-MCP profile.

### Validation posture

- Unit/contract/integration coverage is maintained in-repository.
- CI targets supported Python versions on macOS and Windows.
- Live provider/client validation remains environment-dependent.
- Claude local MCP remains experimental until target-environment domain E2E is recorded.
- ChatGPT remote MCP/App remains deployment-deferred until account/auth/client requirements are validated.

## Core implementation history

The following entries summarize the implementation path built against the frozen 1.2 protocol. Detailed plan/review evidence remains under `docs/plans/`, `docs/ux/`, git history, and the frozen specifications.

### Phase 1 — Core hardening

- Repeatable SQLite migrations, job lifecycle, source files/versions, checkpoints, source-bound idempotent allocation, local worker locking, retry/rate-limit policy, strict config validation, human-gate write guards, and model-independent contract tests.
- Hardened capability/source-fingerprint binding, job-key enforcement, fail-closed configuration, human approval separation, and TOCTOU-safe local lock behavior through independent review/fix rounds.

### Phase 2 — Transcript vertical slice

- Deterministic transcript normalization with verbatim body plus timestamp sidecar.
- Commit ordering that preserves Partial/Ready state honestly and records source/version provenance.
- Read-only provider interfaces for retrieval, entity resolution/selection, bounded session context, exact locator authorization, authority policy, freshness, and structured errors.

### Later implemented slices

- Enrichment/freshness support for sessions/materials.
- Material-usage proposal, guarded approval, recovery, and retrieval/capability work.
- Exam/activity context and capability lifecycle work.
- Additional phase 6–8 repository implementation and local validation recorded in the corresponding plans/evidence.
- v0.1.3 semester-intake preview, dashboard/UX integration records, and LMS sidecar work.

## Protocol 1.2 frozen design baseline

The repository historically used `1.2.0` package metadata while implementing a frozen **v1.2 design/implementation protocol**. Before public package publication, package versioning was reset to the beta line **0.1.3**. The v1.2 frozen documents remain authoritative protocol references; they are not a claim that Syllva has published a stable 1.2 product release.
