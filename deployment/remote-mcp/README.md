# Authenticated Remote MCP profiles

[한국어](README.ko.md)

Syllva supports standards-based **MCP OAuth with Google login**, the existing **OIDC JWT Bearer** resource-server mode, and a **short-lived bearer credential** development profile.

For general remote clients, `mcp_oauth` is the browser-login profile: the client registers the MCP URL, discovers Syllva's OAuth endpoints, and Syllva uses Google only to authenticate the configured single owner. Google tokens are never MCP credentials; Syllva issues its own short-lived, resource-bound access token and rotating refresh token for the exact `/mcp` resource.

In OIDC mode, incoming requests present standard OIDC ID Tokens (or RFC 9068 JWTs) signed by a trusted identity provider (Google, GitHub, Auth0, Cloudflare Access). Tokens are cryptographically validated against the IdP's JWKS and authorized against the single owner's `authorized_subject` (or `authorized_email` with `email_verified=true`). No static long-lived secrets are stored on disk or needed at runtime.

## Configuration

Representative `remote_mcp` configuration:

```yaml
remote_mcp:
  enabled: true
  auth_mode: mcp_oauth  # "mcp_oauth" | "oidc" | "bearer" | "oauth_or_bearer"
  edge_mode: cloudflare_tunnel  # direct_tls is the backward-compatible default
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  oauth:
    google_client_id: your-google-web-client.apps.googleusercontent.com
    authorized_email: owner@example.com
```

Store the Google OAuth Web client secret as `REMOTE_MCP_GOOGLE_CLIENT_SECRET` using an allowed environment or OS-keyring source. Do not put it in YAML. The exact Google Web redirect URI is `<public-origin>/oauth/google/callback`.

`edge_mode: direct_tls` remains the default and preserves the existing certificate/key path. `edge_mode: cloudflare_tunnel` is allowed only with `mcp_oauth`; it requires a loopback listener and accepts local plaintext HTTP only from a loopback peer while the public URL remains HTTPS. Forwarded headers are not trusted to reconstruct public identity. If a tunnel rewrites the origin `Host`, configure the tunnel to send the exact public host instead of weakening Syllva's Host check.

Example direct-TLS OIDC compatibility profile:

```yaml
remote_mcp:
  enabled: true
  auth_mode: oidc
  edge_mode: direct_tls
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  tls_certfile: /private/path/fullchain.pem
  tls_keyfile: /private/path/privkey.pem
  oidc:
    issuer: https://accounts.google.com
    audience: your-client-id.apps.googleusercontent.com
    authorized_email: owner@example.com
```

## Bearer credential

Provision a random URL-safe token of at least 32 characters through private secret storage and set `REMOTE_MCP_EXPIRES_AT` to an absolute Unix timestamp no more than one hour in the future for this development profile.

Start:

```bash
uls --config /absolute/config.yaml mcp remote
```

Token values must not appear in config files, client instructions, command arguments, logs, or PR/issue text. Restart with a new credential after expiry; do not turn one development bearer into a silently perpetual credential.

## OAuth and HTTP/TLS boundary

Only OAuth protocol control-plane routes (discovery, authorize, token, registration/revocation, and the Google callback) are reachable before an MCP access token exists. They expose no academic data and still pass the Host/Origin/edge checks. `/mcp`, `/health`, and all eleven retrieval tools remain authenticated.

The OAuth store lives under `system.workspace_dir` as `remote-oauth.sqlite3` with protected local permissions. Authorization codes and MCP access/refresh tokens are stored by digest. Changing the configured owner, Google client ID, public issuer/resource, or fixed scope policy invalidates existing grants.

The MCP app should remain a single process for the in-memory capability model; do not add multiple workers without redesigning that state model.

## Permissions

The bearer credential is high-value access to the configured user's Syllva corpus subject to engine policy. It is not a per-course ACL.

Use only the separate read-only Drive/Notion/GitHub credentials intended for MCP retrieval. The ingestion worker is not started by and is not exposed as an MCP tool.
