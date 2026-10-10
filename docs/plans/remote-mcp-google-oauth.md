# Remote MCP OAuth broker with Google login

> 현재 적용 안내 (2026-10-04): 아래 배정·리뷰·단계·권한 문구는 작성 당시의 역사 기록입니다. 새 작업은 프로젝트 `AGENTS.md`가 참조하는 현행 전역 정책과 `handoff.md`의 최신 재개 기준을 따릅니다. 과거 모델/강도·quota 예외·commit/push 허용을 새 작업으로 승계하지 않습니다. 원래 검토된 bytes와 해시는 당시 Git snapshot/리뷰 패키지의 근거로 유지하며, 이 안내를 추가한 현재 파일을 그 원본과 동일하다고 주장하지 않습니다.

Status: PLAN rev2, previous independent reviews returned REVISE; fixes below await targeted rereview.
Date: 2026-09-21.
Owner: Astra orchestration; implementation fit: `gpt-5.6-sol` high (auth/protocol integration).
Risk: high — authentication, authorization, public HTTP surface, token persistence, proxy boundary.

## Scope and acceptance

Extend the existing Remote MCP transport without reopening accepted C5/C6 behavior.
The same read-only MCP core must remain usable over local stdio and Remote MCP.

The new user flow is:

1. Register one Remote MCP URL in a compatible client.
2. The client discovers Syllva OAuth metadata and starts Authorization Code + PKCE.
3. Syllva redirects the browser to Google for upstream authentication.
4. Syllva accepts only the configured single owner's verified Google identity.
5. Syllva issues its own resource-bound MCP access/refresh tokens.
6. The client calls the existing eleven read-only MCP tools with the Syllva token.

Acceptance requires all of the following:

- MCP search remains read-only and C5/C6 code paths are unchanged.
- Google tokens are never accepted as Syllva MCP access tokens and are never forwarded to MCP clients.
- Syllva access tokens are bound to the exact configured MCP resource URI.
- Authorization Code + S256 PKCE is mandatory; codes, state, nonce, access tokens and refresh tokens are high-entropy and single-purpose.
- Only the configured verified Google owner may complete authorization.
- Local stdio remains unchanged.
- Existing `oidc`, `bearer`, and `oauth_or_bearer` profiles keep their current behavior.
- Direct TLS keeps its current security boundary. Cloudflare Tunnel is an additive, loopback-only edge profile.
- No token, Google client secret, OAuth code, nonce, or decoded ID-token payload is logged or committed.
- Restart/replay/expiry/revocation behavior fails closed.
- Standard MCP protected-resource metadata, authorization-server discovery, `resource` binding, and 401 challenge behavior are covered by tests.
- The narrow public OAuth control-plane carve-out is now explicitly recorded in both frozen Remote MCP authorities; `/mcp`, `/health`, and academic data remain authenticated.

## Protocol authority

The implementation targets MCP Authorization 2026-07-28, OAuth 2.1-style Authorization Code + PKCE, RFC 9728 Protected Resource Metadata, RFC 8707 Resource Indicators, and RFC 9207 authorization-server issuer identification. The installed MCP Python SDK 2.2.0 already provides the protocol routes and PKCE/token endpoint validation through `AuthSettings` and `OAuthAuthorizationServerProvider`; Syllva supplies the provider, persistence, Google callback, and transport boundary.

Google is only the upstream identity provider. It is not the authorization server for the MCP resource. This avoids token passthrough and lets Syllva issue an access token whose resource is exactly the configured Remote MCP URI.

### Canonical public identity

Configuration is canonicalized exactly once at startup and all protocol URLs are derived only from that immutable tuple:

- `public_origin = https://host[:port]` with no path/query/fragment;
- `resource_uri = public_origin + "/mcp"`;
- `authorization_server_issuer = public_origin`;
- `google_callback_uri = public_origin + "/oauth/google/callback"`.

`remote_mcp.public_url` may retain the existing input compatibility of either an HTTPS origin or the same origin plus `/mcp`, but both normalize to the tuple above. Discovery metadata, RFC 9207 `iss`, OAuth endpoint URLs, Google callback, database policy identity, and MCP token `resource` are generated only from this tuple. ASGI `scope.scheme`, request URL reconstruction, `Forwarded`, and `X-Forwarded-*` are never used to derive public identity.

If `/authorize` contains an RFC 8707 `resource`, it must equal the canonical `resource_uri` exactly after the same one-time parser/canonicalizer; mismatch raises `AuthorizeError(error="invalid_target")` before any Google redirect. Code exchange and refresh may never change resource, client, scope, subject, or policy fingerprint.

## Architecture

### New auth mode

Add `remote_mcp.auth_mode: mcp_oauth`. Do not change the semantics of the existing three modes.

`mcp_oauth` uses the MCP SDK authorization-server surface:

- `/.well-known/oauth-protected-resource/mcp`
- `/.well-known/oauth-authorization-server`
- `/authorize`
- `/token`
- `/register` for the initial interoperability profile
- `/revoke`
- `/oauth/google/callback` as the Syllva-owned upstream return route
- `/mcp` remains the only academic-data MCP endpoint
- `/health` remains authenticated

The OAuth metadata/control-plane routes expose no academic data. The retrieval surface remains authenticated. The required narrow carve-out is now explicitly recorded in the frozen Remote MCP authority: only the enumerated OAuth protocol routes are tokenless; transport Host/Origin/edge checks still apply to every route.

Dynamic Client Registration is retained initially because MCP Python SDK 2.2.0 supports it and cross-client compatibility is the current delivery goal. A later client-id-metadata-document profile can replace DCR when the supported client/SDK matrix is verified; that is outside this task.

### Upstream Google leg

`GoogleOAuthBrokerProvider.authorize()` stores a short-lived, one-time authorization transaction and redirects to Google using:

- `response_type=code`
- exact configured Google OAuth Web client ID
- redirect URI `<public origin>/oauth/google/callback`
- scopes `openid email`
- `prompt=select_account` so a multi-account browser can explicitly choose the configured owner instead of repeatedly auto-selecting a different signed-in account
- high-entropy `state` and `nonce`
- upstream S256 PKCE

The callback validates state before exchanging the code, performs the Google token exchange over HTTPS with redirects disabled and strict size/time bounds, validates the ID token signature/issuer/audience/expiry/nonce, and requires the configured email with `email_verified=true`. Google access/refresh tokens are discarded after identity validation.

Callback failures are protocol-defined. If upstream state is valid, Google denial/cancel or owner mismatch consumes the one-time transaction and redirects only to the already validated MCP client redirect URI using `error=access_denied`, a fixed non-sensitive `error_description`, the original MCP client `state`, and `iss=authorization_server_issuer`. Provider/internal failures use the appropriate fixed OAuth error without leaking upstream payloads. If upstream state is missing, malformed, expired, already consumed, or unknown after restart, Syllva has no trusted client return context and instead returns a safe local HTTP 400 browser page instructing the user to restart login from the MCP client; it never redirects using untrusted query data.

The callback then creates a one-use Syllva authorization code bound to the original MCP client ID, exact redirect URI, scopes, MCP PKCE challenge, resource and owner subject. The browser is redirected to the MCP client's registered redirect URI with the code, original client state and Syllva issuer identity.

### Syllva token leg

The SDK validates the MCP client's PKCE verifier and client authentication. The provider exchanges the one-use Syllva code for:

- opaque access token: 15-minute default TTL;
- opaque rotating refresh token: 30-day default TTL;
- fixed `uls:read` scope;
- exact resource equal to `remote_mcp.public_url` canonicalized as the `/mcp` resource;
- owner subject and Syllva issuer claims used to derive `caller_identity`.

Refresh rotates both access and refresh tokens. Reuse of a rotated/revoked/expired token fails closed. Access and refresh tokens are stored only as SHA-256 digests. Authorization codes are stored only as digests. Client secrets created by DCR must be recoverable for SDK client authentication, so client registration records are stored only in the protected local OAuth database and are never logged.

Every authorization transaction/code/access token/refresh family is also bound to an immutable `authorization_policy_fingerprint`: SHA-256 over canonical JSON containing `authorization_server_issuer`, `resource_uri`, Google client ID, normalized configured owner email, fixed scope set and the OAuth policy-format version. Token verification and refresh recompute the current fingerprint; mismatch immediately rejects the access token and revokes the affected refresh family. Therefore changing owner, Google client ID, issuer/resource, or scope policy invalidates pre-change grants on restart instead of inheriting them.

Refresh tokens have a random `family_id` and monotonically increasing generation. A successful rotation atomically marks the presented generation consumed, issues the next refresh generation, and issues the access token. Presentation of any already-consumed ancestor is a replay signal: in the same SQLite transaction Syllva revokes the entire family, including the current refresh descendant and every still-active access token issued by that family. Concurrent double-use is intentionally conservative: one request may commit a rotation, while the losing reuse revokes the family. Crash-after-commit before the token response likewise causes the old-token retry to revoke the family and require a new browser login; it never leaves a silently usable attacker descendant.

### Persistence and restart behavior

Use one SQLite database under `system.workspace_dir`, e.g. `remote-oauth.sqlite3`, created as a regular file with mode `0600`. It stores clients, one-use authorization codes, access-token digests, refresh-token family/generation state, authorization-policy fingerprints and revocation/expiry state. All token/code/rotation/replay transitions use transactions.

The Google browser transaction (`state`, nonce, upstream PKCE verifier and original authorization parameters) is intentionally short-lived and process-local. A process restart during browser login invalidates that login attempt; the client restarts authorization. It never authorizes a partial prior attempt.

DCR records have two states. `PENDING_OWNER` registrations have a ten-minute TTL and never consume a durable-client slot. Admission is bounded to 32 pending records; when full, a new valid registration evicts the oldest pending record that has not completed owner authorization, so 32 attacker-created never-authorized registrations cannot permanently block the next legitimate registration. Starting an authorization request does not promote the record. Only a successful verified-owner Google callback promotes the exact client to `OWNER_AUTHORIZED`. Durable clients are capped separately at 32; because promotion requires the configured owner, an unauthenticated caller cannot exhaust durable slots. Expired pending records are lazily pruned on registration/authorization and at startup.

The provider persists the complete SDK-approved security identity of every DCR client: client ID, nullable client secret and its expiry, `token_endpoint_auth_method`, exact redirect URI set, grant types, response types, scope and issue time. `get_client()` reconstructs the same `OAuthClientInformationFull`; Syllva adds no confidential-client assumption. Public clients registered with `token_endpoint_auth_method=none` use PKCE and the SDK's public-client token semantics; secret-bearing clients use the registered SDK authentication method. Code exchange additionally binds exact client ID, redirect URI, PKCE challenge, resource, scopes, owner and policy fingerprint. Client secrets are the only DCR field that must remain recoverable and are protected by the local `0600` database boundary.

## Configuration and secrets

Add a nested `remote_mcp.oauth` configuration containing only non-secret values:

- `google_client_id`
- `authorized_email`
- `access_token_ttl_seconds` (default 900, bounded)
- `refresh_token_ttl_seconds` (default 2,592,000, bounded)
- `authorization_ttl_seconds` (default 600, bounded)
- fixed read scope remains code-owned, not user-configurable

Add `REMOTE_MCP_GOOGLE_CLIENT_SECRET` to the credential resolver. Allowed sources are environment and macOS keyring; the fixed keyring binding is owned by code. It is required only for `mcp_oauth` and is resolved once at the CLI composition root.

Do not read a real `.env` file during implementation or tests.

## Edge/TLS profiles

Add `remote_mcp.edge_mode` with two explicit values.

Schema default is `edge_mode: direct_tls`. An existing config that omits the field follows the current direct-TLS validation/startup/doctor path with the same meaning; only an explicit `cloudflare_tunnel` value changes the local listener boundary.

`direct_tls` preserves the current behavior unchanged: local ASGI scheme must be HTTPS, TLS cert/key are required, Host and optional Origin must match the public URL exactly, `workers=1`, and `proxy_headers=False`.

`cloudflare_tunnel` is for the first local-Mac deployment:

- Syllva listener must bind loopback only (`127.0.0.1` or `::1`), plaintext HTTP locally;
- `public_url` must still be HTTPS;
- `proxy_headers=False`; Syllva never trusts `X-Forwarded-*` to establish identity or scheme;
- ASGI client peer must be loopback;
- external Host and optional Origin must match `public_url` exactly;
- local ASGI `scope.scheme == "http"` is accepted only in this edge mode and only when `scope.client` is a strict loopback peer (`127.0.0.1` or `::1`); any non-loopback peer or local HTTPS/forwarded-scheme substitution outside the declared matrix fails closed;
- Cloudflare Tunnel is the only intended path from public HTTPS to the loopback listener;
- no local TLS cert/key are required in this mode;
- `workers=1` is unchanged.

The boundary is represented explicitly in tests. Direct TLS is never silently downgraded when `cloudflare_tunnel` is absent.

The transport and bearer-auth boundaries are separate. `mcp_oauth` uses a new edge-boundary wrapper that applies scheme/peer/Host/Origin checks to every route but does not demand an MCP bearer token on OAuth control-plane routes. The existing `AuthenticatedApp` remains unchanged for `oidc`, `bearer`, and `oauth_or_bearer`.

## Caller identity

The SDK's token verifier protects `/mcp`, but Syllva still needs the existing `caller_identity` context for capability isolation. For the `mcp_oauth` profile a retrieval-only identity wrapper behaves as follows: if `/mcp` has no bearer token, it delegates untouched so the SDK emits the RFC 9728 challenge; if a bearer token is present and the local verifier accepts it, it derives `remote:oauth:<sha256(issuer + ':' + subject)>`, sets the context variable only while delegating that request, then resets it in `finally`; invalid tokens delegate without identity so the SDK emits the standard failure. OAuth control-plane routes never enter this identity wrapper. `/health` uses the same local OAuth-token verifier but exposes no retrieval data.

Missing/invalid `/mcp` tokens receive the SDK's 401 standards-compliant Bearer challenge containing the protected-resource metadata URI. Insufficient scope is 403. Public OAuth control-plane routes never set a retrieval caller identity.

## Doctor and composition behavior

`mcp_oauth` resolves zero `REMOTE_MCP_SECRET` / `REMOTE_MCP_EXPIRES_AT` values in both `uls doctor` and `uls mcp remote`, exactly like OIDC-only mode does for the legacy development bearer. It instead requires and diagnoses `REMOTE_MCP_GOOGLE_CLIENT_SECRET` plus the non-secret OAuth config. Doctor validates the canonical public tuple, edge-mode matrix, writable/protected OAuth state directory, Google client ID/owner configuration, and—under `--live` only—the upstream Google discovery/JWKS reachability without initiating a user login.

## Planned files

Primary implementation:

- `src/uls/mcp/transports/oauth.py` — store, provider, Google upstream exchange/validation, token verifier and callback.
- `src/uls/mcp/transports/remote.py` — additive OAuth/edge composition and boundary wrapper.
- `src/uls/config/schema.py`, `loader.py`, `validation.py`, `credentials.py` — typed config and secret routing.
- `src/uls/cli/main.py` — one-time credential resolution/composition.
- `config.example.yaml`, `deployment/remote-mcp/README.md`, `.ko.md` — operator config and exact flow.
- `handoff.md` — compact resumable status.

Tests:

- OAuth provider/store/replay/expiry/rotation/Google callback unit tests.
- Remote MCP contract tests for metadata/discovery/challenge/resource binding/read-only tool invocation and caller isolation.
- Config/doctor/credential tests for mode-specific secret resolution and direct-vs-tunnel validation.
- Existing-config regression proving missing `edge_mode` means `direct_tls` and preserves legacy OIDC/bearer behavior.
- Canonical tuple tests proving origin and origin+`/mcp` inputs converge to one issuer/resource/callback and request/forwarded headers cannot change advertised URLs.
- Owner/Google-client/public-identity config-change tests proving old access/refresh grants fail immediately through the policy fingerprint.
- Refresh family tests covering normal rotation, concurrent double-use, ancestor replay, crash-after-commit response loss, and family-wide access-token revocation.
- DCR tests covering public and secret-bearing clients, exact redirect/client/resource binding, pending TTL/eviction, and successful legitimate registration after 32 never-authorized registrations.
- Google browser tests covering account selection, denial, owner mismatch, expired/tampered state and safe client-vs-local error returns.
- OAuth route tests proving discovery/authorize/token/register/callback pass only the edge boundary while `/mcp` and `/health` remain authenticated.

No C5/C6 provider, study-note, approval or retrieval-domain source file is in this implementation set.

## Verification and delivery

Before commit:

1. Focused OAuth/config/remote tests, Ruff and focused Mypy.
2. Contract and unit marker suites; full suite if focused checks pass.
3. Behavior Contract hash/projection lint to prove no projection drift.
4. Independent web ChatGPT plan review before implementation and final review after tests.
5. Independent `google-antigravity/gemini-3.8-flash` high plan/final flow review because login/error/approval UX is user-facing.
6. Targeted rereviews for any required auth/flow fixes.
7. Update `handoff.md`, then commit the accepted transport bundle on the current work branch. No push or protected-branch merge without separate authorization.

## Live rollout after code acceptance

The first live check is intentionally after implementation acceptance:

1. Start a temporary Cloudflare Quick Tunnel to the loopback Syllva port.
2. Read the generated `https://<random>.trycloudflare.com` public origin.
3. The exact Google OAuth Web redirect URI becomes `https://<random>.trycloudflare.com/oauth/google/callback`.
4. Register that exact redirect URI in the existing `syllva-academic` Google OAuth Web client and set its client ID in non-secret config; store its client secret through the protected credential path.
5. Start Syllva in `mcp_oauth + cloudflare_tunnel`, verify metadata and an end-to-end owner login, then register the MCP URL in one target client.

Before the live login, read back the actual Cloudflare origin request Host. If Quick Tunnel rewrites it to the loopback origin, start/configure `cloudflared` with the supported origin Host override so Syllva receives the exact public Host; do not relax Syllva's Host validation or trust forwarded headers as a workaround.

Quick Tunnel URLs are temporary, so a stable domain/tunnel is required before treating client registration as durable production configuration.
