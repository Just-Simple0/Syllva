# Operations and Recovery

[한국어](operations.ko.md) · [Operator Guide](README.md)

## Normal inspection

Start with:

```bash
uls doctor
uls status
uls jobs --limit 20
```

Then run bounded work deliberately:

```bash
uls sync --max-jobs 20
uls process --max-jobs 20
uls run --max-jobs 20
```

Use `uls retry <job-id>` only after you understand why the job failed. `uls reprocess <entity-id>` is an explicit operator action and should not be used to hide uncertain external state.

## Single active worker

Worker mutation commands share a local single-active-worker lock. If a second process reports that another worker is active, do not bypass the lock to make progress. Investigate the existing owner/process first.

This lock is local process safety, not a distributed lease.

## Schedulers

The repository includes:

- `deployment/macos/com.syllva.uls.plist`
- `deployment/windows/uls-task.xml`

Edit absolute paths and validate manual `uls run` behavior before registering a scheduler. Scheduled processes may not inherit your interactive shell environment, so credential availability must be handled deliberately through private OS/service mechanisms.

Do not enable the optional LMS schedule until its separate gates pass.

## Backups

Back up together:

- `config.yaml` (without embedded secrets);
- metadata-only `sources.json`;
- SQLite state using a safe SQLite backup mechanism.

Do not assume copying only the main SQLite file while WAL writes are active is a complete backup. Back up credentials separately using OS secret storage, not inside the Syllva archive.

## Restore

1. Stop worker schedulers and MCP processes.
2. Preserve the current state as a separate recovery copy.
3. Restore the selected config/state together.
4. Run `uls status` and `uls doctor`.
5. Perform a bounded source/context smoke check.
6. Reconcile Drive/Notion against the restored local provenance before enabling automation.

Restoring an older SQLite database does **not** roll back external providers or human approvals.

## Unknown external outcome

If a provider mutation times out or its response is lost, treat the result as unknown until readback resolves it. Never convert “request returned an error” directly into “the external write definitely did not happen.”
