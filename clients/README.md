# Syllva client packages

[한국어](README.ko.md)

All clients consume the same read-only MCP tools and server-enforced evidence scope. `contracts/study-behavior.md` is canonical for shared study behavior. Each instruction/skill projection declares its contract relationship, and `uls behavior lint` should pass before packaging.

Syllva 0.1.3 is beta. A checked-in client projection or config example means the integration shape exists; it does **not** automatically mean that a real end-user client has completed live domain E2E.

## Build a portable bundle

From the repository checkout:

```bash
python scripts/package_clients.py --output /absolute/path/uls-clients.zip
```

The archive includes the canonical behavior contract, Claude skills, ChatGPT instructions, local MCP config material, support metadata, and the E2E checklist. It does not include credentials or user configuration. Existing output files are not overwritten.

## Claude local MCP

Install Syllva with the `mcp,drive,notion` extras. Configure read-only provider credentials in the environment used by the MCP process. Adapt `claude/mcp-config.example.json` with absolute executable/config paths, then register that server in a Claude client that supports local MCP.

Copy individual `claude/skills/*` directories only to a Claude environment that supports the corresponding skill mechanism. Client capabilities vary; the repository does not treat every Claude surface as equivalent.

Current status: **EXPERIMENTAL** until a real-client domain E2E is recorded for the target environment.

## Codex desktop/CLI

Codex can use the same local stdio MCP server through its supported MCP configuration. See [Operator: MCP Clients](../docs/operator-guide/mcp-clients.md) for the command/TOML examples.

Validate the exact executable/config paths from the environment Codex will launch. Local configuration availability is not the same as a completed end-user E2E.

## ChatGPT remote MCP/App

Use `chatgpt/instructions/study-behavior.md` as the behavior projection only when paired with an authenticated MCP connection supported by the target ChatGPT environment.

The built-in remote profile is a short-lived **development bearer** integration, not OAuth. A target that requires OAuth or another supported gateway must remain **DEPLOYMENT_DEFERRED** until that external auth layer, account connectivity, and domain E2E are independently validated.

ChatGPT web does not read this checkout's local stdio configuration. Never paste credentials into ChatGPT instructions or prompts.

## Validation

See `support-matrix.json` and `e2e-checklist.md`. Automated SDK/protocol tests establish repository behavior, not real-client support.

A client profile should be called validated only after its complete target-environment E2E has been recorded. Offline primary machines cannot answer remote requests, and public/anyone-with-link sharing is not an acceptable workaround.
