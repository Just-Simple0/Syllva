# ULS desktop and remote operation

The checked-in profiles run the same `uls run` command on macOS and Windows.
No scheduler has been installed and no remote endpoint has been deployed by
this implementation. Configure and validate locally before registering a task.

## Local setup

Install Python 3.11+ and the package with `pip install -e ".[dev,mcp,drive,notion]"`.
The transport targets MCP Python SDK 2.2.x (`mcp>=2.2,<3`) and the native Notion
adapter targets notion-client 3.1+ (`>=3.1,<4`) with data-source APIs. Run:

```sh
uls --config /absolute/path/config.yaml init
uls --config /absolute/path/config.yaml doctor
uls behavior lint
```

`init` creates only missing files. Configure your actual Course/database/Drive
IDs. Relative config paths resolve against the config file, including workspace
and TLS paths. A Notion database must have exactly one data source in this profile.
Paginated/truncated or ambiguous graph data fails visibly instead of broadening
scope. Canonical source bodies remain in Drive; SQLite stores orchestration and
completed processing provenance. New source bindings come only from completed
worker records, never an arbitrary Notion URL.

Supply credentials through the process environment or a private service wrapper.
The runtime does not implicitly load `.env` files. Keep secret files outside the
checkout and out of scheduler definitions, client instructions and logs.

| Process | Required environment |
| --- | --- |
| Worker | `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| Local/remote MCP | `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`; `GITHUB_READ_TOKEN` for private repositories |
| Remote development bearer | `REMOTE_MCP_SECRET`, `REMOTE_MCP_EXPIRES_AT` (Unix epoch seconds) |

Use separate Notion connections with **Read content only** for MCP. Use separate
Drive credentials with `drive.readonly`; worker Drive credentials have write
scope. The factory rejects missing MCP credentials, equal Notion tokens and the
same resolved Drive credential path. It does not prove provider-side permission
grants from a filename; audit grants in the provider console. GitHub fine-grained
credentials need selected repositories and Contents/Metadata read only.

## Worker source registrations

`init` creates an empty `sources.json` in `system.workspace_dir`. Fill it using
`sources.example.json`. This metadata-only registration explicitly associates a
Drive transcript with a configured Course and its private Derived folder; the
worker does not guess cross-course identities from filenames. The source-bound
allocator chooses the Session ID. New Sessions need explicit title, date and a
Status option already defined in your Notion database. Existing unrelated
transcript pointers are not overwritten without matching durable provenance.

The native scheduler pipeline currently connects the previously implemented
transcript vertical slice. Other source kinds are rejected as configuration
errors, and pending operations without handlers become NEEDS_REVIEW. This does
not establish the previously deferred real PDF/Goodnotes/client validation.

```sh
uls --config /absolute/path/config.yaml sync
uls --config /absolute/path/config.yaml process --max-jobs 100
uls --config /absolute/path/config.yaml run --max-jobs 100
uls --config /absolute/path/config.yaml status
uls --config /absolute/path/config.yaml jobs
uls --config /absolute/path/config.yaml retry JOB_ID
uls --config /absolute/path/config.yaml reprocess ENTITY_ID
```

`sync` registers source versions for processing; `process` handles queued work;
`run` does both once and exits. Automatic retries are bounded and apply only to
transient/rate-limit failures. Partial stays Partial. `reprocess` is an explicit
local operator action that requeues a terminal job while preserving its identity
and previous processing records. All mutation commands take the same local
worker lock. In-flight jobs left by a crash require inspection; the system does
not infer success or blindly replay uncertain human-gated writes.

## Schedulers

Edit the absolute paths in `macos/com.syllva.uls.plist` or
`windows/uls-task.xml`. Both run one tick every ten minutes. Credentials must be
available to the scheduled process; desktop shell environment variables are not
automatically inherited by launchd or Task Scheduler. Use a private executable
wrapper that obtains credentials from your OS secret storage when needed.

On macOS, validate the edited file with `plutil -lint` and register the user
LaunchAgent using launchctl. On Windows, import the edited XML into Task
Scheduler for the current user; use least privilege and inspect Last Run Result.
Run `uls run` manually first. The scheduler contains no business logic. Windows
IgnoreNew complements the application's cross-platform process lock; neither is
a distributed lease. Only one Primary PC runs the ingestion worker.

## MCP profiles

`uls mcp local` serves stdio and reserves stdout for MCP protocol messages.
It uses the same RetrievalEngine as remote MCP. See `../clients/README.md`.

For development remote use, see `remote-mcp/README.md`. `uls mcp status` reports
configuration and durable worker status; it does not claim a bridge is running.
The authenticated `/health` endpoint checks the live bridge. `uls doctor --live`
performs read-only provider probes, not third-party AI client E2E.

## Backups and recovery

Back up `config.yaml`, metadata-only `sources.json`, and SQLite with Python's
SQLite online backup API (example in `backup_state.py`). Do not copy only the
main database file while WAL writes are active. Keep backups private and
encrypted. Back up credentials separately in the OS credential manager; they
must not enter this backup archive or a repository.

To restore, stop both schedulers and MCP processes, preserve the current state
as a separate recovery copy, then restore the selected database/config together.
Run `uls status`, `uls doctor`, and a source/context smoke check before enabling
the scheduler. Restart invalidates all ephemeral context/resolution handles.
Reconcile external Drive/Notion state after restoring older SQLite provenance;
never assume a local backup rolls back those providers or human approvals.

Remote retrieval is available only while the Primary PC, network and bridge are
online. There is no always-on mobile guarantee, and public sharing is never a
workaround. No human-approved Verified/Scope Confirmed decision is created by
these commands or exposed through MCP.
