# ULS handoff
Updated 2026-09-21.

Branch: `codex/protected-secret-file-and-credential-set`.
Do not touch unrelated `RESEARCH/`, secrets, or `CLAUDE.md`. No push or protected-branch merge is authorized.

## Accepted baseline

- C5/C6 core is accepted and committed at `a868039` (`feat: complete C5 integration and C6 study note flow`). Do not reopen it without a concrete transport regression.
- Search MCP remains read-only. Human Decision/Verified are never synthesized. Preserve SOURCE/AI/USER ownership.
- Behavior Contract remains v2, `sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a`.

## Remote MCP OAuth — accepted

Plan: `docs/plans/remote-mcp-google-oauth.md`.

Syllva now supports additive `mcp_oauth`: Syllva is the MCP OAuth authorization server and Google is only the upstream owner-login IdP. Local stdio remains supported. Public identity is one canonical issuer/resource/callback tuple. Access tokens are opaque; refresh tokens rotate by family/generation. DCR supports public and confidential clients. Policy changes permanently retire prior grants. OAuth state is fail-closed on owner/protection/0600 checks. `direct_tls` remains the default; `cloudflare_tunnel` is loopback-only HTTP behind public HTTPS. OAuth control-plane routes are tokenless; `/mcp` and `/health` remain authenticated.

Final auth corrections are accepted:
- A→B→A policy rollback cannot resurrect old codes/access/refresh tokens.
- trusted Google callback DB failures return fixed `server_error` to the prevalidated client redirect; untrusted state still returns local 400.
- runtime and doctor share the same protected OAuth state-boundary validation; insecure existing DBs are rejected, not repaired.
- metadata advertises only route-proven auth methods: token `none` / `client_secret_post` / `client_secret_basic`; revocation `client_secret_post`.

Review evidence:
- plan rereviews GO: web `GPT-5.6 Sol / 매우 높음` at `.insane-review/response_Syllva_20260921_004729_75488_0bd2b3.md`; Gemini high at `.review/remote-mcp-google-oauth-plan-rev2-gemini.md`.
- initial final web review REVISE: `.insane-review/response_Syllva_20260921_014057_77410_1cc973.md`; all four blockers fixed.
- targeted final web rereview GO using actual `GPT-5.6 Sol / 매우 높음`: `.insane-review/response_Syllva_20260921_020132_78097_67023a.md`.
- targeted Gemini high rereview GO: `.review/remote-mcp-google-oauth-final-rereview-gemini.md`.

Verification after final fixes:
- full suite: **1787 passed, 3 skipped, 2 unchanged warnings**
- contract: **98 passed**
- unit: **415 passed, 3 skipped**
- focused OAuth/OIDC/config/doctor/runtime: **81 passed**
- Behavior Contract/projection lint: clean
- OAuth/remote Mypy: clean
- relevant Ruff: clean
- `git diff --check`: clean
- broader repo Ruff and config Mypy still contain pre-existing unrelated debt.

## Next

Code acceptance is complete; live deployment has not been performed. Google Auth Platform project `syllva-academic` is prepared with External audience and the owner test user.

For the first live check: start a Cloudflare Quick Tunnel, use its exact public origin for `public_url`, register exactly `<public-origin>/oauth/google/callback` in the Google OAuth Web client, store `REMOTE_MCP_GOOGLE_CLIENT_SECRET` through the configured protected credential source, start `mcp_oauth + cloudflare_tunnel`, verify metadata + owner login, then register the `/mcp` URL in one target client. Never relax Host validation or trust forwarded headers to accommodate the tunnel.
