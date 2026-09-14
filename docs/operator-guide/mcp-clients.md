# MCP Clients

[한국어](mcp-clients.ko.md) · [Operator Guide](README.md)

Syllva's MCP server is a read-only academic retrieval boundary. Client setup must preserve that boundary rather than giving the AI client worker credentials.

## Codex desktop/CLI — local stdio

Use an absolute executable and config path:

```bash
codex mcp add uls -- /ABSOLUTE/PATH/uls-venv/bin/uls \
  --config /ABSOLUTE/PATH/config.yaml mcp local
```

Equivalent TOML:

```toml
[mcp_servers.uls]
command = "/ABSOLUTE/PATH/uls-venv/bin/uls"
args = ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
```

Validate the executable/config paths from the same environment the client will launch.

## Claude — local MCP

A representative client configuration is:

```json
{
  "mcpServers": {
    "uls": {
      "command": "/ABSOLUTE/PATH/uls-venv/bin/uls",
      "args": ["--config", "/ABSOLUTE/PATH/config.yaml", "mcp", "local"]
    }
  }
}
```

The repository also contains Claude skill projections under `clients/claude/skills/`. Availability of local MCP/skills depends on the actual Claude client you use.

## ChatGPT / remote clients

Do not assume a web client can read your local checkout or local stdio configuration. The built-in remote profile is a **development bearer profile**, not an OAuth deployment. If a client requires OAuth or another gateway, keep that profile deployment-deferred until the external auth layer and client E2E are validated.

See `deployment/remote-mcp/README.md` for the checked-in remote development boundary.

## Process separation

Use the read-only MCP credential boundary documented in [Configuration](configuration.md). Do not launch the MCP server with worker write credentials as a shortcut.

`uls mcp local` reserves stdout for MCP protocol messages. Operational logs should not corrupt stdio protocol output.

## Validation

A profile becomes trustworthy only after the relevant client can complete domain-specific retrieval checks against the intended environment. SDK/protocol tests are necessary but not equivalent to real-client E2E.
