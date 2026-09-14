# Authenticated remote development profile

[한국어](README.ko.md)

The built-in remote profile is for **development validation**, not a production OAuth deployment.

It uses a short-lived bearer credential over direct TLS. A target client that requires OAuth/OIDC or another gateway must remain deployment-deferred until that external auth layer and client E2E have been configured and validated separately.

## Configuration

Representative `remote_mcp` configuration:

```yaml
remote_mcp:
  enabled: true
  auth_mode: oauth_or_bearer
  public_unauthenticated: false
  public_url: https://uls.example/mcp
  host: 127.0.0.1
  port: 8765
  tls_certfile: /private/path/fullchain.pem
  tls_keyfile: /private/path/privkey.pem
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
