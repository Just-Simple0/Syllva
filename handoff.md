# ULS handoff
Updated 2026-09-27. Branch: `codex/protected-secret-file-and-credential-set`.

Do not touch unrelated `RESEARCH/`, secrets, or `CLAUDE.md`. No push or protected-branch merge is authorized.

## Accepted baseline

- C5/C6 core: `a868039` (`feat: complete C5 integration and C6 study note flow`).
- Remote MCP Google OAuth: `216e996` (`feat: add OAuth remote MCP transport`), final web/Gemini rereviews GO.
- Search MCP remains read-only; preserve SOURCE/AI/USER ownership and human-owned Decision/Verified.
- Behavior Contract v2: `sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a`.

## Semester-scoped read-only retrieval v1.3 — accepted, committed locally

Plan: `docs/plans/semester-scoped-retrieval-v1.3.md`.

Implemented additive opt-in retrieval for one exact `notion.semester_workspaces` row. Frozen v1.2 `legacy_global` remains the default. `semester_workspace` uses direct Notion data-source IDs only; no discovery/copy/fallback to legacy IDs.

Boundary rules:
- Courses, Sessions, and Materials fail closed on missing/malformed/cross-semester Course identity.
- Exact Session-ID public resolution validates the Session Course relation before returning metadata.
- Missing optional Material Usage permits transcript-only Session context with explicit `SOURCE_UNAVAILABLE`; it cannot authorize material evidence/capabilities.
- Missing semester Exam/Activity mappings fail unavailable without touching legacy IDs.
- `doctor --live` selects a Course from the configured retrieval semester.

Review evidence:
- initial plan web review REVISE: `.insane-review/response_Syllva_20260921_190954_82376_9ec887.md`; exact Session-ID boundary fixed.
- targeted plan rereview GO: `.insane-review/response_Syllva_20260921_192104_85196_ca0a96.md`.
- final implementation web review GO: `.insane-review/response_Syllva_20260921_195551_97741_6fc369.md` (`GPT-5.6 Sol / Pro`).
- Gemini: N/A; this slice has no user-facing UI/design/flow change.

Verification:
- full suite: **1793 passed, 3 skipped**
- unit: **415 passed, 3 skipped**
- contract: **98 passed**
- focused semester/config/integration/contract tests: passing
- Behavior Contract hash unchanged; projection lint clean; `git diff --check` clean
- targeted Mypy for `cli/main.py`, `adapters/notion/api.py`, `config/validation.py`: clean with `--follow-imports=skip`; full repo Mypy retains pre-existing debt

Local `config.yaml` is explicitly set to `retrieval.notion_lane: semester_workspace`, `semester: 2026-2`. Host `uls doctor` and `uls doctor --live` are both `status: ok`: `remote_profile`, `live_notion_read`, and `live_drive_read` all pass. The configured Drive root is the accessible `School` folder, and the `2026-2` folder resolves directly under it. Python 3.14's official `Install Certificates.command` was run, so the default CA path now exists and no `SSL_CERT_FILE` override is required.

## External rollout — current

- `syllva.dev` is active on Cloudflare. Named Tunnel `syllva-mcp` publishes `mcp.syllva.dev` to `http://127.0.0.1:8765`; local `public_url` is `https://mcp.syllva.dev/mcp`.
- Google Web OAuth redirect URI is updated to `https://mcp.syllva.dev/oauth/google/callback`.
- Remote MCP is listening on `127.0.0.1:8765`; public OAuth discovery returns 200 with issuer `https://mcp.syllva.dev`; unauthenticated `/health` returns the expected 401.
- Claude: OAuth connected; `uls.ping` E2E passed (`service=uls`, `protocol_version=1.2`).
- Codex: `syllva-live` registered, OAuth login completed, and `uls.ping` E2E passed (`service=uls`, `protocol_version=1.2`).
- Antigravity: global `syllva-live` config points to the fixed endpoint and Antigravity account auth succeeds, but Gemini 3.8 Flash High `uls.ping` E2E is **not proven**. Bounded attempts time out without a final tool result; one resumed attempt reported failure / protocol N/A. Do not mark this client complete yet.

## Current live retrieval state

- `uls doctor --live` remains green for Notion and Drive.
- The configured 2026-2 Sessions data source ID/schema is correct, but both the Syllva reader and raw Notion API return **0 Session rows**.
- Current 2026-2 operational counts: File Intake 0, Input Request 0, Sessions 0, Materials 0; the configured Drive upload folder also has 0 children; `worker.enabled=false`.
- Therefore current Session resolution returns `SOURCE_UNAVAILABLE` because no Session exists yet, not because of a provider outage or wrong Sessions data-source ID.

Next: put a real source into the semester intake flow, submit its Input Request, enable/run the bounded intake worker, then rerun Session resolution/context E2E after the first Session row exists. Separately finish Antigravity MCP ping diagnosis. Never record the Tunnel token in chat or repo.
