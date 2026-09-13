# Authenticated remote development profile

The built-in profile uses a short-lived bearer credential over **direct TLS**.
It is development-only. It does not advertise OAuth or promise that a client
requiring OAuth can connect. Keep such a client DEPLOYMENT_DEFERRED until an
OAuth/OIDC gateway/provider has been configured and validated separately.

Configure the `remote_mcp` section:

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

Use a certificate trusted by the client and a hostname that reaches this
listener through a private network or explicitly configured network route.
The listener remains loopback unless you explicitly configure a different bind
address. No firewall, DNS, tunnel or public endpoint is created automatically.

Provision a random URL-safe token of at least 32 characters through private
secret storage and set `REMOTE_MCP_EXPIRES_AT` to an absolute Unix timestamp at
most one hour in the future. Start `uls --config /absolute/config.yaml mcp remote`.
Token values never belong in config, client instructions or command arguments.
Restart with a new credential after expiry; restart invalidates old ephemeral
capabilities. Do not set up an automatic perpetual extension of the same token.

All HTTP endpoints, including `/health`, require HTTPS, exact Host, an absent or
matching Origin, and one valid Authorization Bearer header. Forwarded headers are
not trusted. A TLS-terminating proxy that forwards plain HTTP is intentionally
unsupported by this built-in profile. The MCP app runs one process; do not add
multiple workers because capabilities are memory-only.

The single user's bearer credential can reach their ULS corpus, subject to the
engine's domain policies. It is a high-value secret, not a per-course ACL.
Use only separate read-only Drive/Notion/GitHub credentials in the MCP process.
The worker is neither started by nor reachable as an MCP tool.
