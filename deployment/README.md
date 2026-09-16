# Syllva desktop and remote operation

[한국어](README.ko.md)

The checked-in profiles support local Syllva operation on macOS and Windows. Syllva 0.1.3 is beta: scheduler/profile files in the repository are templates and implementation artifacts, not evidence that a scheduler or remote endpoint has already been installed in your environment.

## Local setup

Install Python 3.11+ and the package from the checkout:

```bash
pip install -e '.[dev,mcp,drive,notion,pdf]'
```

Then run:

```bash
uls --config /absolute/path/config.yaml init
uls --config /absolute/path/config.yaml doctor
uls behavior lint
```

`init` creates only missing local files. Configure the exact course, Notion, Drive, and optional GitHub identities you actually intend to use. Relative config paths resolve against the config file.

Canonical source bodies remain in source storage such as Drive; SQLite stores orchestration and completed processing provenance. New durable source bindings must come from validated workflow records rather than arbitrary URLs/titles.

## Credentials

Supply secrets through the process environment or a private service wrapper. The runtime does not implicitly load `.env` files. Keep secret files outside the checkout and out of scheduler definitions, client instructions, prompts, and logs.

| Process | Required environment when that provider path is enabled |
| --- | --- |
| Worker | `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| Local/remote MCP | `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`; `GITHUB_READ_TOKEN` for configured private repositories |
| Remote development bearer | `REMOTE_MCP_SECRET`, `REMOTE_MCP_EXPIRES_AT` |

Use separate Notion/Drive credentials for read-only MCP and worker mutation where the configured provider workflow requires different permissions. Provider-side grants must be audited in the provider console; a credential filename alone does not prove its scope.

## Source registrations

`uls init` creates an empty explicit source-registry path when configured. The metadata-only source registry associates a source with a configured course/location; it is not a place to paste source bodies or credentials.

The current beta also has a separate semester-intake preview. See [Operator: Semester Intake and Notion](../docs/operator-guide/intake-and-notion.md). Do not mix preview data-source identities with the legacy read-only retrieval registry or assume automatic fallback between them.

## Worker commands

```bash
uls --config /absolute/path/config.yaml sync --max-jobs 20
uls --config /absolute/path/config.yaml process --max-jobs 20
uls --config /absolute/path/config.yaml run --max-jobs 20
uls --config /absolute/path/config.yaml status
uls --config /absolute/path/config.yaml jobs --limit 20
uls --config /absolute/path/config.yaml retry JOB_ID
uls --config /absolute/path/config.yaml reprocess ENTITY_ID
```

- `sync` discovers/registers/projects work.
- `process` handles already discovered durable work.
- `run` performs both once and exits.
- mutation commands share the local single-active-worker lock.
- uncertain external writes require inspection/readback rather than blind replay.

## Desktop schedulers

Templates:

- `macos/com.syllva.uls.plist`
- `windows/uls-task.xml`

Edit absolute paths and run `uls run` manually before registering either scheduler. A scheduled process may not inherit your interactive shell environment; arrange credential access explicitly through private OS/service mechanisms.

On macOS, validate the edited plist with `plutil -lint` before registering it. On Windows, inspect the imported Task Scheduler configuration and Last Run Result. The scheduler contains no business logic; Syllva still enforces the local process lock.

### Windows: unattended execution while logged out

`windows/uls-task.xml` uses `LogonType=Password`, which runs whether or not the account is
interactively logged on (unlike `InteractiveToken`, which only runs during an active logon
session on that console). This requires the account password to be registered with Task
Scheduler; the checked-in XML intentionally does not (and must not) contain a password. Never
pass plaintext passwords directly on the command line, as process creation audit logging
(such as Windows Event ID 4688) can record command-line arguments in plain text. Instead,
use `/rp *` to prompt securely for the password at registration time:

```powershell
schtasks /create /tn "ULS" /xml "windows\uls-task.xml" /ru "DOMAIN\ServiceUser" /rp * /f
```

Task Scheduler stores the configured task credential as an encrypted LSA secret on disk, not
in the XML file. Prefer a dedicated low-privilege local account over a personal login for this
purpose, and rotate the password through the same secure prompt (or the equivalent Task
Scheduler UI / PowerShell `Register-ScheduledTask` credential prompt) rather than editing
the XML. If your environment truly only needs the task to run while a user is logged on,
keep `InteractiveToken` and remove the `UserId`/`Password` fields instead; document that
constraint for your operators.

Keep optional LMS scheduling paused until the separate LMS credential/connection/application gates pass. See [Operator: LMS Sync](../docs/operator-guide/lms-sync.md).

## MCP profiles

`uls mcp local` serves stdio and reserves stdout for MCP protocol messages. It uses the same retrieval policies as the configured server composition.

See [Client packages](../clients/README.md) and [Operator: MCP Clients](../docs/operator-guide/mcp-clients.md).

For development-only remote operation, see [remote-mcp/README.md](remote-mcp/README.md). `uls mcp status` reports configuration/durable status; it does not prove that a remote bridge is reachable. Read-only live probes and end-user client E2E are separate validation steps.

## Backups and recovery

Back up `config.yaml`, metadata-only source registration, and SQLite using a safe SQLite backup mechanism. Do not assume that copying only the main DB file during active WAL writes is complete.

Store credentials separately in OS secret storage. They must not enter the Syllva backup archive.

To restore:

1. stop schedulers and MCP processes;
2. preserve the current state as a separate recovery copy;
3. restore the selected config/state together;
4. run `uls status` and `uls doctor`;
5. perform a bounded source/context smoke check;
6. reconcile Drive/Notion against restored local provenance before reenabling automation.

Restoring local state does not roll back external providers or human approvals.

## Remote availability

Remote retrieval is available only while the configured machine, network route, credentials, and bridge are online. Public sharing is not a security workaround. Human-owned verification/scope decisions are not created merely by running these deployment commands.
