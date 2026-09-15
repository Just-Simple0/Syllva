# Feature Status

[한국어](feature-status.ko.md) · [Reference](README.md)

This page gives one place to check whether a feature or document describes what Syllva **does today**, or what a design note proposes for a future version. Several UX and plan documents in this repository (especially under `docs/ux/` and `docs/plans/`) are explicitly labelled as next-version design proposals; this table exists because that distinction is easy to miss when reading a single document in isolation.

| Status | Meaning |
| --- | --- |
| **Available** | Implemented, released, and covered by the frozen v1.2 design/implementation specifications. Safe to rely on today. |
| **Limited preview** | Implemented and testable, but additive/opt-in and not yet the default or fully hardened path. Read the linked status note before depending on it. |
| **Design only (accepted, not built)** | A design document has passed review and is accepted as the target for a future version. No production code implements it yet; current v1.2 behavior is unchanged. |
| **Held / paused by default** | Implemented but intentionally not activated until an explicit operator decision (for example, a credential grant or scheduling opt-in). |

## Retrieval and search

| Feature | Status | Notes |
| --- | --- | --- |
| Read-only MCP search surface (`get_context` and related tools) | Available | The core release invariant; read-only in v1.2. See [MCP Tools](mcp-tools.md). |
| Legacy global Notion/Drive registry (seven global data sources) | Available | The v1.2 retrieval path described by the frozen specifications. |
| Semester-scoped native Notion workspaces + Drive semester registries | Limited preview | Implemented intake slice; see `docs/ux/intake-v1.3-preview.md`. The intake worker's own `readiness()` output explicitly marks the separate read-only MCP composition as `NOT_PROVEN_BY_INTAKE_PREVIEW` — successful intake does not by itself prove this data is retrievable through MCP. |
| Bounded LLM re-rank for concept search | Held / paused by default | Opt-in via `retrieval.allow_bounded_llm_rerank`; deterministic lexical/index retrieval is the default. |

## Intake and file handling

| Feature | Status | Notes |
| --- | --- | --- |
| Intake worker (sync/process/run, single active worker lock) | Available | Requires `worker.enabled: true` and its own Google/Notion credentials; see [Operator Guide: Intake and Notion](../operator-guide/intake-and-notion.md). |
| Bounded PDF text extraction | Available | Deterministic, byte/page/text-size bounded; produces `Ready`/`Partial`/`Needs Review`, never silently upgrades a partial result. |
| Single `+ 업로드` request flow, Automation Queue separation, in-Notion study UI | Design only (accepted, not built) | Accepted design at `docs/ux/intake-execution-contract.md` (rev10). The document itself states this explicitly: it fixes wording for a future specification revision and does not mean current v1.2 deployment is complete. |
| KNU/Canvas LMS probe/sync sidecar | Held / paused by default | Standalone scripts under `scripts/`; not a prerequisite for core retrieval. See [LMS Sync](../operator-guide/lms-sync.md) for the explicit paused-by-default safety rule. |

## Remote access

| Feature | Status | Notes |
| --- | --- | --- |
| Local MCP transport | Available | Default `mcp.mode: local`. |
| Remote MCP transport (bearer/OAuth, TLS) | Held / paused by default | Disabled by default (`remote_mcp.enabled: false`); `uls doctor` only checks its credentials when explicitly enabled. |

## How to read a document's own status label

Prefer a document's own explicit status line when the two disagree with this summary — this page is a map, not a replacement authority. Look for wording near the top of the document, for example:

- "상태: **설계 수용 완료 — 다음 버전 명세 개정안**" (design accepted — next-version specification revision) in `docs/ux/intake-execution-contract.md`.
- "This additive status note describes the implemented semester intake slice" in `docs/ux/intake-v1.3-preview.md`.
- "LMS support is optional and should be treated as a separately gated sidecar" in `docs/operator-guide/lms-sync.md`.

If a document under `docs/plans/` or `docs/ux/` has no explicit current/future label at all, treat it as an engineering record of past design/verification work rather than a description of current behavior, and confirm against the frozen `university-learning-system-v1.2-design-frozen.md` / `-implementation-spec-frozen.md` or the source code.
