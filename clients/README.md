# ULS client packages

All clients consume the same read-only tools and server-enforced evidence scope.
`contracts/study-behavior.md` is canonical. Each instruction/skill declares its
version and hash. Run `uls behavior lint` before packaging.

Build a portable bundle from this checkout:

```sh
python scripts/package_clients.py --output /absolute/path/uls-clients.zip
```

The archive includes the canonical contract, five Claude skills, ChatGPT
instructions, local MCP config template, support matrix and E2E checklist. No
credentials or user config are included. Existing output files are not overwritten.

## Claude local MCP

Install the Python package with the `mcp,drive,notion` extras. Configure read-only
provider credentials in the environment used by the MCP process. Adapt
`claude/mcp-config.example.json` with absolute executable/config paths, then add
that server configuration in your client's supported MCP settings. Copy the
individual `claude/skills/*` directories to the skill location supported by your
Claude client. Not every Claude interface supports local MCP or skills.

## ChatGPT remote MCP

Use `chatgpt/instructions/study-behavior.md` as the instruction projection and an
authenticated HTTPS MCP connection only if your target environment supports it.
The built-in short-lived bearer profile is a development integration, not OAuth.
A client requiring OAuth must remain `DEPLOYMENT_DEFERRED` until an OAuth
provider/gateway is configured and independently validated. Instructions alone
cannot create a connector or grant access. CODEX desktop/CLI can use local stdio
or streamable HTTP MCP servers through its supported local configuration. ChatGPT
web uses a separate remote connection setting whose availability must be checked
for the account and target environment; it does not read this checkout's local
configuration. Never paste credentials into client instructions.

## Validation

See `support-matrix.json` and `e2e-checklist.md`. Automated SDK tests establish
protocol behavior, not real client support. A profile becomes VALIDATED only
after the complete domain E2E checklist has recorded evidence. Offline Primary
PCs cannot answer remote requests; do not use public sharing as a workaround.
