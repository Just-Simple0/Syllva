# CLI Reference

[한국어](cli.ko.md) · [Reference](README.md)

The `uls` console entry point is defined by the package. Most commands accept the repository/config defaults or an explicit `--config` path.

## Setup and diagnosis

```text
syllva init
syllva doctor
syllva status
syllva behavior lint
```

- `init` — create missing local config/state scaffolding.
- `doctor` — validate configuration and report readiness problems; live probes are separate/explicit.
- `status` — report local durable status/readiness without claiming an external bridge is running.
- `behavior lint` — validate client behavior projections against the shared contract.

## Worker

```text
syllva sync [--max-jobs N]
syllva process [--max-jobs N]
syllva run [--max-jobs N]
syllva jobs [--limit N]
syllva retry <job-id>
syllva reprocess <entity-id>
```

- `sync` — discover/register/project work without processing the full queue.
- `process` — process already discovered durable work.
- `run` — perform discovery plus processing once and exit.
- `jobs` — inspect durable jobs.
- `retry` — retry a known failed/retryable job after diagnosis.
- `reprocess` — explicit operator reprocessing of a known entity.

Worker mutations share the local single-active-worker lock.

## MCP

```text
syllva mcp local
syllva mcp remote
syllva mcp status
```

- `local` — stdio MCP server; stdout is protocol output.
- `remote` — checked-in remote-development transport/profile when configured.
- `status` — configuration/status report; not proof that a remote endpoint is reachable.

## Global config path

Examples in this repository commonly use:

```bash
syllva --config /ABSOLUTE/PATH/config.yaml doctor
syllva --config /ABSOLUTE/PATH/config.yaml mcp local
```

Use absolute paths in client/scheduler registrations to avoid dependence on the launch process's working directory.
