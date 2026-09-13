# MCP and AI Clients

[한국어](mcp-and-clients.ko.md) · [User Guide](README.md)

Syllva exposes academic retrieval through MCP so different AI clients can use the same server-enforced context rules. Client instructions/skills influence behavior, but the Retrieval Engine remains responsible for data access, scope, freshness, and provenance.

## Current beta support

| Client/profile | User-facing status |
| --- | --- |
| Claude local MCP | Experimental; local stdio/config templates and skills exist, real-client domain E2E still needs recording |
| Codex desktop/CLI local MCP | Local stdio registration path is documented; validate in your environment |
| Generic MCP SDK | Experimental; protocol tests are not the same as end-user client validation |
| ChatGPT remote MCP/App | Deployment deferred until account connectivity and required auth flow are validated |

The repository may contain configuration examples before a specific client has completed live validation. Treat the status above as more important than the existence of a config file.

## Read-only tool surface

The current server includes tools for ping, entity resolution/selection, material/session/concept/exam/activity/user context, claim verification, and bounded source-chunk retrieval. Tool names and exact inputs belong in the [MCP Tool Reference](../reference/mcp-tools.md).

From a user perspective, remember one rule: **MCP retrieval is for asking and checking, not for silently editing your academic workspace.**

## Client setup

Client registration, absolute executable/config paths, credentials, and remote networking are operator tasks. See [Operator: MCP Clients](../operator-guide/mcp-clients.md).

Do not paste provider tokens into prompts, instruction files, or chat messages to make a client connect.
