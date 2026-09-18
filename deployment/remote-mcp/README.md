# Authenticated remote development profile

[한국어](README.ko.md)

Syllva supports both **OIDC JWT Bearer authentication** (OIDC Resource Server mode) and a **short-lived bearer credential profile** for development.

In OIDC mode, incoming requests present standard OIDC ID Tokens (or RFC 9068 JWTs) signed by a trusted identity provider (Google, GitHub, Auth0, Cloudflare Access). Tokens are cryptographically validated against the IdP's JWKS and authorized against the single owner's `authorized_subject` (or `authorized_email` with `email_verified=true`). No static long-lived secrets are stored on disk or needed at runtime.

## Configuration

Representative `remote_mcp` configuration:

```yaml
remote_mcp:
  enabled: true
  auth_mode: oidc  # "oidc" (recommended) | "bearer" | "oauth_or_bearer"
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  tls_certfile: /private/path/fullchain.pem
  tls_keyfile: /private/path/privkey.pem
  oidc:
    issuer: https://accounts.google.com
    audience: your-client-id.apps.googleusercontent.com
    authorized_subject: your-sub-id
    authorized_email: your-email@example.com
```

Use a certificate trusted by the target client and an explicitly configured network route. The listener remains loopback unless you deliberately configure a different bind address. Syllva does not create DNS, firewall rules, tunnels, or a public endpoint for you.

## Bearer credential

Provision a random URL-safe token of at least 32 characters through private secret storage and set `REMOTE_MCP_EXPIRES_AT` to an absolute Unix timestamp no more than one hour in the future for this development profile.

Start:

```bash
uls --config /absolute/config.yaml mcp remote
```

Token values must not appear in config files, client instructions, command arguments, logs, or PR/issue text. Restart with a new credential after expiry; do not turn one development bearer into a silently perpetual credential.

## HTTP/TLS boundary

The checked-in profile expects direct TLS and authenticated endpoints. Exact Host/Origin/Authorization checks are part of the intended boundary. Forwarded headers are not a substitute for explicit trust configuration.

A TLS-terminating proxy that forwards plain HTTP changes the trust boundary and is intentionally not represented as a validated production architecture here.

The MCP app should remain a single process for the in-memory capability model; do not add multiple workers without redesigning that state model.

## Permissions

The bearer credential is high-value access to the configured user's Syllva corpus subject to engine policy. It is not a per-course ACL.

Use only the separate read-only Drive/Notion/GitHub credentials intended for MCP retrieval. The ingestion worker is not started by and is not exposed as an MCP tool.
