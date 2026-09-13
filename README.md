# University Learning System (ULS) — v1.2

**Status:** Phase 6–8 repository implementation and local validation complete; live release validation deferred
**Architecture style:** Model-agnostic · MCP-centered · Local-primary · Single-active-execution · Cross-platform
**Primary desktop platforms:** macOS, Windows
**Core language:** Python 3

ULS is a personal academic knowledge and retrieval system. It lets you ask natural
study questions through multiple AI clients (Claude, ChatGPT, future MCP clients)
without manually assembling context.

> **ULS determines what context is allowed and relevant.**
> **AI clients reason over the context ULS provides.**

## Core principles

- **Drive** stores original sources and deterministic normalized derivatives.
- **Notion** stores the academic graph/state/verification, not canonical large-text bodies.
- **GitHub** stores version-controlled project source at exact refs.
- **Retrieval Engine** owns scope, authority, freshness, source selection, and provenance.
- **MCP** is the model-neutral retrieval boundary and is **read-only** in v1.2.
- **Skills/instructions** govern behavior; the Retrieval Engine governs data access.
- `SOURCE` / `AI` / `USER` ownership zones remain distinct.
- Normalization is not summarization; `Partial` is never silently `Ready`.
- Human confirmation (`Verified`, `Scope Confirmed`) cannot be promoted by AI.

See the authoritative documents:

- [`university-learning-system-v1.2-design-frozen.md`](university-learning-system-v1.2-design-frozen.md)
- [`university-learning-system-v1.2-implementation-spec-frozen.md`](university-learning-system-v1.2-implementation-spec-frozen.md)

If code conflicts with the frozen design, the frozen design wins.

## Layout

```text
contracts/        model-neutral Behavior Contract
clients/          Claude / ChatGPT projections of the Behavior Contract
src/uls/          Python core (domain, retrieval, mcp, adapters, state, ...)
deployment/       macOS launchd / Windows Task Scheduler / remote-mcp profiles
scripts/          behavior-contract hashing + projection lint
tests/            unit / contract / integration / e2e / fixtures
```

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e '.[dev,mcp,drive,notion,pdf]'
uls init
uls doctor
```

Edit the generated config and metadata-only `sources.json`, and provide separate
execution/MCP credentials through the process environment. The runtime does not
automatically load `.env` files. `doctor` reports missing configuration until
provider IDs and credentials are configured.

See [getting started](docs/user-guide/getting-started.md), [desktop/remote setup](deployment/README.md),
and [client packaging](clients/README.md). The `pdf` extra is required for PDF material preview.
The LMS sidecar syncs Canvas/KNU assignments and materials on an hourly schedule; its default
scheduler state is paused and it requires a separate `CANVAS_ACCESS_TOKEN` in the process environment.
Real client E2E, Windows-host operation, and the authenticated remote profile (development bearer
credentials only, not OAuth) remain unvalidated in the live environment.

## First setup path

Read [the beginner setup guide](docs/user-guide/getting-started.md) before adding
provider IDs or credentials. `uls init` creates local state and an empty explicit
transcript registry; it does not watch Drive, upload files, or create Notion pages.
Setup time depends on separate execution, MCP, Drive, and Notion configuration gates.
Run `uls doctor` and `uls behavior lint` after configuration, then use the local
MCP or a supported client for read-only retrieval. The LMS sidecar uses an explicit
semester registry and sanitized metadata snapshots; a partial course never becomes
a complete semester result. Its default scheduler state is paused and it does not
enroll credentials or activate a heartbeat.

Daily workflows, supported inputs, and honest MCP/client boundaries are in
[daily use](docs/user-guide/daily-use.md), [MCP and clients](docs/user-guide/mcp-and-clients.md),
and [troubleshooting](docs/user-guide/troubleshooting.md).

## CLI

```text
uls init | doctor
uls sync | process | run
uls status | jobs | retry <job-id> | reprocess <entity-id>
uls mcp local | uls mcp remote | uls mcp status
uls behavior lint
```

## Validation order (frozen)

`Spike C0 → Spike M0 → VS0 → VS0-B (cross-client) → Spike G (Goodnotes)`

## Security posture

- Read-only MCP surface; least-privilege read-only credentials where the provider supports it.
- No public / "anyone with the link" sharing to make retrieval work.
- Human-only fields guarded at the write boundary, not only at the LLM caller.
- Secrets never committed to source control or embedded in client projections.
