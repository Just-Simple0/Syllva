# Project instructions

These instructions apply to the University Learning System (ULS) v1.2 repository. They contain repository-specific constraints only. The frozen design and implementation specification are the project authorities:

- `university-learning-system-v1.2-design-frozen.md`
- `university-learning-system-v1.2-implementation-spec-frozen.md`

When project code conflicts with the frozen design or implementation specification, the frozen documents take precedence.

`CLAUDE.md` is Claude-specific. It remains unchanged and is not a general project authority or a required source for importing agent roles. No legacy Claude/Opus role assignment belongs in these project instructions.

Model/effort selection, orchestration, reviews, and safety follow the applicable global Codex `AGENTS.md`, normally at `~/.codex/AGENTS.md`; this file only adds project rules.

## Project architecture

- ULS is a personal academic knowledge and retrieval system.
- The architecture is model-agnostic, MCP-centered, local-primary, single-active-worker, and cross-platform.
- `Single-active-worker` is a ULS product/runtime architecture constraint. It does not imply or restrict Codex subagent concurrency, which follows the separate Codex work-management policy.
- The core language is Python 3.
- ULS decides which context is allowed and relevant; AI clients reason over the context ULS provides.

## Release invariants

- The **MCP search surface (MCP 검색 표면)** is **read-only** in v1.2.
- Preserve the `SOURCE` / `AI` / `USER` ownership distinction. AI output must not be relabeled as `SOURCE`, and USER content must not be automatically overwritten.
- Normalization is not summarization. `Partial` must never silently become `Ready`.
- `Material Usage.Verified = true` and `Exam.Scope Confirmed = true` are human-only; AI and automation cannot promote them.
- The engine performs freshness validation. Stale enrichment is excluded from factual evidence.
- `get_source_chunk` accepts only locators in the allowlist of a pre-issued context capability.
- Do not use public (`anyone-with-link`) sharing for search convenience.

## Repository layout

```text
contracts/        model-neutral Behavior Contract (study-behavior.md)
clients/          Claude Skills / ChatGPT Instructions projections
src/uls/          Python core (domain, retrieval, mcp, adapters, state, ephemeral, ...)
deployment/       macOS launchd / Windows Task Scheduler / remote-mcp
scripts/          Behavior Contract hash and drift linting
tests/            unit / contract / integration / e2e / fixtures
```

## Frozen implementation order

`Spike C0 → Spike M0 → VS0 → VS0-B (cross-client) → Spike G (Goodnotes)`

Then follow Phase 1 (Core Hardening) through Phase 8 (Desktop Automation & Remote MCP) in the order specified by the frozen implementation specification sections 42–49.

## Development commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -m contract        # model-independent contract tests (release blockers)
pytest -m unit
ruff check src tests
mypy

python scripts/project_behavior_contract.py --print   # canonical Behavior Contract version/hash
python scripts/lint_behavior_projection.py            # projection drift check (CI)
```

## Implementation rules

- Core modules (`domain/`, `ingestion/`, `normalization/`, `enrichment/`, `retrieval/`, `state/`, and `ephemeral/`) must not depend on client SDKs or OS schedulers.
- The Retrieval Engine must be directly callable in tests without an MCP server.
- Never include credentials or tokens in commits, Skills/Instructions, or logs.
- A change that violates a frozen contract requires a specification revision.
