# ULS handoff
Updated 2026-09-21. Branch: `codex/protected-secret-file-and-credential-set`.

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

## External rollout still pending

GitHub Student status is approved; partner offers should unlock after ~72 hours. After obtaining a stable domain, replace Quick Tunnel with a Cloudflare Named Tunnel and fixed `mcp.<domain>`, update the Google OAuth callback once, then finish Claude/Codex/Gemini owner-login E2E. Quick Tunnel hostnames are disposable. No remaining local provider-health blocker is known.
