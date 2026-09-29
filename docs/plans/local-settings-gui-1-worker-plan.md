# Syllva Local Settings GUI-1 worker plan

Date: 2026-09-28

Status: **GUI-1 final-review fixes integrated; Gemini rereview GO; web Pro rereview open only on 5C,
now fixed (credential journal machines, role binding) and awaiting rereview. GUI-1 is unavailable on
Windows (pending platform-scope decision, see below).**

Parent: [`local-settings-web-gui.md`](local-settings-web-gui.md) and its interaction mock.

## Assignment and boundary

- Worker: `gpt-6-luna` / **max**.
- Technical fit: local HTTP session security, cross-process locking/CAS, and crash-safe multi-store recovery.
- GUI-1 owns the local settings shell, typed non-secret config service, shared config lock, journal/recovery shell, and read-only Overview/setup state.
- GUI-2–GUI-5 own credentials, provider connections, Canvas, academic/automation mutations, and Remote Access. GUI-1 exposes their status cards as deferred/read-only states only.
- No implementation starts before explicit acceptance of the parent plan and this worker plan. No provider call, credential read/write, live config write, deletion, commit, or push is part of this design stage.

## Concrete files and ownership

New GUI-1 files:

```text
src/uls/settings/__init__.py
src/uls/settings/app.py             Starlette app factory, routes, lifespan, safe errors
src/uls/settings/security.py       bootstrap/session/CSRF state and pure ASGI boundary
src/uls/settings/launcher.py        loopback socket reservation and Uvicorn lifecycle
src/uls/settings/config_service.py  group registry, snapshots, validation, diff, apply
src/uls/settings/status.py          Overview/setup predicates and redacted diagnostic port
src/uls/settings/journal.py         secret-free journal records and recovery discovery
src/uls/settings/static/index.html
src/uls/settings/static/app.js
src/uls/settings/static/styles.css
tests/contract/test_settings_http.py
tests/contract/test_settings_config.py
tests/contract/test_settings_cas.py
tests/contract/test_settings_journal.py
tests/contract/test_settings_ui.py
tests/unit/test_settings_launcher.py
```

Owned compatibility edits:

```text
src/uls/config/loader.py           parse a raw mapping through the existing typed loader
src/uls/config/mutation.py         shared raw snapshot/CAS/atomic config-file store
src/uls/config/__init__.py         export the shared mutation primitives
src/uls/orchestration/locks.py     generalize the existing lock primitive; retain LocalWorkerLock API
src/uls/cli/main.py                add `uls setup` and route status semantics without shell parsing
src/uls/cli/commands/setup.py      programmatic setup entry point
pyproject.toml                     make Starlette explicit in the existing MCP/web extra if needed
```

Existing files remain authoritative and are reused: `config/schema.py`, `config/validation.py`,
`config/credentials.py` for typed policy and redacted readiness metadata, the existing
`LocalWorkerLock` implementation/tests, `_secure_file.py`'s reviewed no-follow/atomic/fsync
patterns (after factoring neutral helpers without applying the secret-size limit), CLI `status()`
semantics, and the Starlette `TestClient` pattern in `tests/contract/test_mcp_runtime.py`.

## Exact GUI-1 settings groups

The shell has the parent plan's navigation groups, but the group registry rejects every field not
listed here. A rejected/deferred field is a fixed `FEATURE_DEFERRED` response; it is never silently
accepted as an arbitrary YAML path.

| Group | GUI-1 behavior | GUI-1 fields |
|---|---|---|
| Overview | Editable state summary | Durable config/readiness, pending journal operations, restart-required fingerprints, existing `status()` evidence. No provider call on page load. |
| General | Editable | `system.timezone`. `system.workspace_dir`, state/ephemeral backends, Behavior Contract version/path, and `mcp.read_only` are read-only. Active semester stays read-only until GUI-4 has verified mappings. |
| Storage roots / Notion parent | Read-only | Existing Drive/Notion IDs and semester bindings are displayed only; no provider verification or binding mutation. |
| Connections | Deferred | No credential fields, uploads, keyring/protected-file writes, or connection tests. |
| Academic | Deferred/read-only | Existing course and mapping data may be summarized; no Canvas discovery, course selection, or mapping write. |
| Automation | Deferred/read-only | Existing enabled state, poll interval, and applied/desired fingerprints may be shown; no toggle, scheduler, or restart action. |
| Remote Access | Deferred/read-only | Existing safe status/fingerprint only; no Remote MCP, OAuth-grant, tunnel, public URL, or credential mutation. |
| Advanced | Editable bounded values | `retrieval.concept_mode`, positive retrieval/context limits, TTLs, and `normalization.goodnotes_visual_fallback`. Provider credentials, remote TLS/OIDC/bearer values, human-owned academic fields, and `allow_provisional_material_usage` remain read-only/deferred. |

The UI preserves the interaction mock's step order and states: resumable Storage → Canvas →
Academic → Automation → Remote → Check, `Partial` never becomes `Ready`, dirty non-secret input
survives a generation conflict, secret fields are absent in GUI-1, and pending journal entries show
`Partial` with `Resume repair` / `Leave as-is`. Back-navigation retains entered values and marks
dependent steps `Partial` until their predicates are revalidated.

## HTTP shell and session security

`launcher.py` first acquires a per-user, owner-only single-instance OS lock and holds it for the
settings process lifetime. If another process owns it, the new launcher authenticates the owner over
same-user local IPC. The old owner immediately invalidates bootstrap/session/CSRF state and rejects
mutations, then serves only a fixed `401 {"error":{"code":"SESSION_REPLACED"}}` with no application
data during a bounded notification drain before closing its listener and exiting. The new launcher
waits for observed process exit and lock release. Only after acquiring the lock may it reserve an OS
socket on exactly `127.0.0.1` with port `0` or create a bootstrap capability. If owner identity
cannot be verified or exit misses the bounded timeout, the new launch fails closed without issuing a
token. After the old process exits, its port is closed and its session cannot authenticate.

The replacement control channel is an owner-only local IPC endpoint in the private per-user Settings
runtime directory. On macOS/POSIX it is an `AF_UNIX` stream socket in a mode-`0700` directory with a
mode-`0600` socket; the server checks peer UID with `getpeereid` on macOS or `SO_PEERCRED` where
available, and checks the held lock's PID and random lock token before accepting `replace`. The
Windows design below is **not implemented**; GUI-1 refuses to run on Windows until it is accepted. On
Windows it would be a local named pipe whose DACL grants the current user's SID and SYSTEM; the server
checks the connecting PID with `GetNamedPipeClientProcessId` and the connecting process's `TokenUser`
SID against its own SID. If the platform cannot prove the peer identity or protect the endpoint, it
fails closed. The control request includes protocol version, current owner PID, and the lock token;
it never includes settings or credentials. Constants are `IPC_IO_TIMEOUT_SECONDS = 1.0`,
`REPLACEMENT_DRAIN_SECONDS = 2.0`, `OWNER_EXIT_WAIT_SECONDS = 5.0`, and lock-release polling every
`0.05` seconds. The replacement caller must observe both old-process exit and lock release within the
five-second deadline before binding HTTP or minting bootstrap state.

It then starts one Uvicorn `Config`/`Server` instance with no reload, no workers, no proxy-header trust,
and access logging disabled. It opens only
`http://127.0.0.1:<port>/?bootstrap=<one-time-token>`. `uls setup` does not accept a user-supplied
host or expose a remote/Remote MCP route.

`security.py` keeps bootstrap, session, CSRF, expiry, and shutdown state in process memory. It must:

- issue at least 256-bit CSPRNG bootstrap/session/CSRF values; accept bootstrap once, within 30 seconds,
  only for exact Host and top-level `navigate`/`document` fetch metadata, then return `303 /` with
  only the session cookie and no CSRF value;
- keep the server-side session record and expected CSRF value in memory only; after the clean redirect,
  the page fetches the CSRF value from authenticated same-origin `GET /api/v1/session/csrf` with
  `Cache-Control: no-store`, valid session cookie, and same-origin fetch metadata (plus exact Origin
  when present). The session handle is delivered only in the HttpOnly cookie; JavaScript keeps the
  CSRF value only in ephemeral page memory, never in a URL, localStorage, sessionStorage, IndexedDB,
  readable cookie, or log. Clear it on close, expiry, shutdown, or replacement;
- reject replay, iframe/frame/subresource bootstrap, guessed host/port, `localhost`, forwarded hosts,
  and every non-exact Host value;
- use an `HttpOnly; SameSite=Strict; Path=/` session cookie with no Domain; never put session/CSRF
  values in URLs, localStorage, sessionStorage, IndexedDB, telemetry, or logs;
- require exact same-origin `Origin` on mutations, use a strict same-origin `Referer` only when
  `Origin` is absent, and require a custom-header CSRF value on every mutation;
- allow normal same-host top-level GET without Origin, while making every GET except bootstrap
  authentication/application-state preserving;
- distinguish passive polling from explicit activity for inactivity renewal; implement the mock's
  expiry screen, explicit keepalive, and close-before-shutdown behavior;
- return structured `401 {"error":{"code":"SESSION_EXPIRED"}}` for an expired API session. The UI
  clears its in-memory CSRF value and secret inputs, retains dirty non-secret fields only in ephemeral
  page/JavaScript memory until close/reload, and shows the mock's expired-session state without
  persisting drafts;
- when the old process returns `401 SESSION_REPLACED`, or a request fails because the backend is
  refused/closed/unreachable, immediately transition the old tab to the mock's read-only moved/closed
  state. Drop the in-memory CSRF value and all secret field values; retain dirty non-secret values in
  the existing DOM as read-only, selectable text; disable Save/Next/Apply/Restart and every mutation
  control. Register a beforeunload guard only while dirty non-secret values remain. Announce with a
  `role=alert`/live region and focus the state heading. Treat fetch rejection as safe
  `SESSION_UNREACHABLE`; never leave an uncaught error or persist/retry the draft. Keep this separate
  from the existing `SESSION_EXPIRED` state;
- apply fixed body limits before parsing, no CORS, restrictive CSP with `frame-ancestors 'none'`,
  `Cache-Control: no-store`, no-referrer, and no external assets or service worker;
- log route templates, fixed error codes, and safe metadata only. Uvicorn access logs are disabled
  so the bootstrap query cannot enter an access-log line.

Use pure ASGI middleware for the exact security boundary and response headers; do not rely on
`BaseHTTPMiddleware` for security state. Routes are purpose-specific and versioned:

```text
GET  /api/v1/overview
GET  /api/v1/settings/{group}
GET  /api/v1/session/csrf
POST /api/v1/settings/{group}/validate
POST /api/v1/settings/{group}/apply
POST /api/v1/session/keepalive
POST /api/v1/session/close
```

No credential, Canvas, provider-test, Remote MCP, OAuth-grant, or scheduler endpoint is registered.

## Config snapshot, CAS, and atomic write

`ConfigStore` reads the raw config bytes once, computes `config_generation = sha256(raw_bytes)`,
parses a safe YAML mapping, and builds the existing `UlsConfig` through the existing loader and
validator. The browser receives only the typed allowlisted model, generation, safe status metadata,
and a redacted semantic diff.

Apply flow:

1. Validate the typed group patch outside the lock; reject unknown paths, read-only fields, provider
   fields, invalid booleans/numbers/URLs, and human-owned academic fields.
2. Exclusively create this operation's secret-free journal record in `prepare` state and build the
   candidate from the raw mapping, so unknown top-level sections and nested keys survive the edit.
3. Acquire this operation's journal-record lock, then the shared config lock derived from the exact
   config path. This config-only path has no credential-role lock.
4. Inside the config critical section, no-follow open and verify the target is the expected regular,
   owner-controlled file; reread raw bytes; recompute and compare the caller's expected generation;
   bind the validated patch to that exact raw generation; write an owner-only sibling temp file,
   fsync it, atomically replace the target, fsync the directory, reread, and validate the result
   before releasing the lock.
5. Return the readback generation and redacted diff. A mismatch returns `CONFIGURATION_CHANGED`
   without writing, terminates the already-created journal record as `resolved_without_change`,
   verifies no unresolved recovery entry remains for that operation, and leaves all dirty browser
   values intact. A write/readback failure leaves the old target intact where the OS permits and
   leaves a recoverable journal record.

`locks.py` will extract a purpose-neutral `LocalFileLock` from the existing cross-platform
`LocalWorkerLock` behavior: atomic create, advisory descriptor lock, PID/host metadata, and stale
recovery only for a provably dead owner on the same host. `LocalWorkerLock` keeps its current public
API and tests. Every config-writing GUI/CLI path must use the same `ConfigFileLock`; a Python lock or
settings-process lock is not sufficient. Lock order is always sorted credential/profile role locks
(when applicable) → per-operation journal-record lock → config lock; config-only operations skip the
role lock. Release in reverse order and never acquire an earlier lock while holding a later one.

The config writer preserves YAML data semantically, including unrelated keys, but PyYAML output may
rewrite comments, quoting, and layout. This is a material review choice; comment-preserving YAML is
out of GUI-1 scope unless the independent review requires a different dependency/design.

## Journal and recovery shell

Use a code-owned owner-only journal directory under the canonical configured workspace, with one
exclusive `0600` record per operation, updated by atomic same-directory replacement, file fsync, and
directory fsync. Writers never rewrite a shared journal index or a stale snapshot. The record contains
only:

```text
schema_version, operation_id, action_kind, fixed role/group, original_generation,
candidate_hash, intended non-secret binding, phase, per-effect pre/post state IDs,
staging/backup/version locator identity, readback result, next repair action
```

It never contains credential values, uploaded JSON, keyring content, or arbitrary caller paths.
Cross-process owner-only role locks are keyed by `(provider, profile identity, credential role)`;
multi-role operations acquire them in sorted canonical order. While holding a role lock, a new
  operation checks for an unresolved matching record and fails with `OPERATION_IN_PROGRESS` if one
  exists. The record keeps that role reserved across process exit; `Leave as-is` does not resolve the
  reservation. It releases only after verified completion or an explicit disposition proving no
  uncertain side effect remains. Different roles use independent records and may proceed concurrently.

Before every authoritative side effect, persist an intent with the exact expected pre-state, target
version/locator, and applicable config generation. Perform one effect, read its authoritative store
back, then persist the observed post-state/version. Never group side effects into one phase. Fixed lock
order is sorted credential/profile role locks → per-operation journal-record lock → shared config lock;
config-only operations skip the role lock. Release in reverse order.

GUI-1 implements `config_apply` phases (`prepare`, `candidate_validated`, `locked`, `committed`,
`readback`, `complete`). Before atomic config replacement, persist its intent with the original
generation and candidate hash; after locked readback, persist the observed generation. If a crash
occurs before the post-state record, recovery compares the raw config hash: candidate generation means
the write committed and needs verification; original generation means no write occurred and requires
an explicit resume; any other generation stays `Partial` without overwriting concurrent edits. Crash
injection covers the replacement/readback boundary. GUI-1 also validates parent-plan schemas for
future credential `enrollment`, `replacement`, and `forget` records but rejects live credential
actions until GUI-2/GUI-3 own those stores and state machines. Fake-store recovery tests cover each
recorded side-effect boundary, including promotion-before-config-commit, without registering provider
routes or touching credentials. Recovery scans unfinished entries on launch and offers only the
recorded repair/leave action; no timeout guesses rollback or deletes anything.

## Tests and acceptance evidence

- `test_settings_http.py`: bootstrap fetch metadata/replay/303 cleanup; exact Host/Origin/Referer;
  authenticated same-origin CSRF handoff after 303; no-store and no URL/storage/cookie/log leakage;
  CSRF unusable after close/expiry/replacement; exact `401 SESSION_EXPIRED` response with dirty
  non-secret values retained only in page memory; GET-vs-mutation rules; cookie flags; no
  CORS/external assets; CSP/body limits; passive-vs-explicit expiry renewal; fixed redacted logs.
- `test_settings_config.py`: group allowlist, typed validation, read-only/deferred rejection,
  redacted diff, unknown-key preservation, invalid-candidate byte stability, and no secret-shaped
  values in API responses.
- `test_settings_cas.py`: two real processes starting from one generation; exactly one commit, one
  generation conflict, no overwrite/corruption, and no unresolved journal entry for the losing
  operation; symlink/untrusted-target rejection; atomic write readback. Reuse existing lock tests
  instead of duplicating worker-lock coverage.
- `test_settings_journal.py`: owner/mode checks and no secret fields; two concurrent cross-process
  writers preserve both independent records; a second operation for an unresolved credential/profile
  role fails with `OPERATION_IN_PROGRESS`; fixed lock-order coverage; crash injection after every
  authoritative side effect and before its post-state record, including credential promotion before
  config commit. Verify exact version/generation recovery, `Partial`/resume/leave behavior, and no
  unsupported provider repair.
- `test_settings_ui.py`: mock-aligned stepper/status text including replacement and closed-session
  screens; for both `SESSION_REPLACED` and fetch rejection, assert no uncaught error, CSRF and all
  secret values cleared, dirty non-secret DOM values retained read-only/selectable, mutations disabled,
  and alert/live-region announcement with focus on the heading. Assert beforeunload is enabled only
  while dirty non-secret values remain. Also cover dependent-step `Partial` on back-navigation,
  in-memory draft retention on `SESSION_EXPIRED`, deferred groups, and no browser storage or remote
  asset references.
- `test_settings_launcher.py`: port-0 loopback reservation, exact bootstrap URL, no access-log
  query leakage, and graceful shutdown using fakes; double-launch test starts A with a live session and
  a tab client containing dirty non-secret and secret inputs, then starts B and observes A exit/lock
  release before B receives a bootstrap. Assert the tab transitions to moved/closed, retains the
  non-secret DOM values read-only/selectable, clears CSRF and secret values, and cannot mutate; prove
  A's port/session are unusable and B shows the replacement notice. While A drains, its tab receives
  `SESSION_REPLACED`; after A exits, test the closed-port/fetch-rejection path too. In both cases
  verify the detailed accessibility and beforeunload assertions in `test_settings_ui.py`. Also test
  timeout/owner-verification failure issues no new bootstrap.

After implementation, run the focused GUI-1 tests, existing lock/config/security tests, contract
tests, Ruff, targeted mypy, Behavior Contract drift checks, and `git diff --check`. A real browser
launch and macOS process-race run remain required integration evidence (Windows is unsupported in
GUI-1); no live provider or
credential smoke test is part of GUI-1 acceptance.

## Material choices and limitations for independent review

1. GUI-1 assumes `uls init` has already created an existing config; it does not silently create a
   new config skeleton or migrate workspace data. If first-launch initialization is required, it is
   a separate accepted change.
2. Only General timezone and bounded Advanced limits are mutable in GUI-1. Provider, Canvas,
   academic, automation, and Remote Access writes remain explicitly deferred.
3. Existing status semantics are reused through a redacted diagnostic port. GUI-1 does not resolve
   secret values or load environment secret files; credential presence is `Not checked`/deferred
   until GUI-2 supplies the approved store adapters.
4. The settings process is single-instance and on-demand. Multiple processes may read, but all config
   writers serialize through the shared OS/file lock; distributed locking and scheduler control are
   out of scope.
5. The GUI is an operational settings surface, not an academic dashboard: it cannot edit source,
   Notion content, USER notes, approvals, MCP tools, or public sharing.

Official API references used for the server boundary: [Starlette applications](https://starlette.dev/applications/),
[Starlette middleware](https://starlette.dev/middleware/), [Starlette requests](https://starlette.dev/requests/),
[Starlette responses](https://starlette.dev/responses/), [Uvicorn settings](https://uvicorn.dev/settings/),
and [Uvicorn lifespan](https://uvicorn.dev/concepts/lifespan/).

## Implementation record (2026-09-29)

Accepted optional guidance applied: (a) the same-user IPC mechanism, peer checks, and constants above
are implemented in `launcher.py` (`AF_UNIX` socket in a `0700` runtime directory, socket `0600`,
macOS `LOCAL_PEERCRED`/`LOCAL_PEERPID`, Linux `SO_PEERCRED`, lock PID + random lock token,
`IPC_IO_TIMEOUT_SECONDS=1.0`, `REPLACEMENT_DRAIN_SECONDS=2.0`, `OWNER_EXIT_WAIT_SECONDS=12.0`,
0.05 s polling); (b) a config apply that loses the generation CAS after creating its record
terminates it as `resolved_without_change`, and the two-process CAS test asserts no unresolved
recovery entry remains.

Deviations from the plan text, each with its reason:

- One extra purpose-specific route, `POST /api/v1/recovery/{operation_id}/{resume|leave}`, backs the
  mock's `Resume repair` / `Leave as-is` buttons. It is CSRF/Origin protected like every mutation.
- **Pending platform-scope decision (Windows).** Named-pipe/DACL/SID/PID replacement and Windows
  reparse-point/owner checks are not implemented. Until the user decides whether full Windows
  support is required, `uls setup` refuses to run on Windows before it creates any runtime state,
  lock, socket, or listener, and tells the user to edit `config.yaml` and use the `uls` commands.
- `LocalFileLock` is an alias of the unchanged `LocalWorkerLock` (plus a read-only `token`
  property) rather than an extracted base class, so the worker lock API and tests are untouched.
- `uls setup --no-browser` prints the one-time URL to stdout instead of opening a browser, and
  `ULS_SETTINGS_RUNTIME_DIR` overrides the runtime directory. Both exist for headless review and
  tests; the runtime path is length-checked for the `AF_UNIX` limit.
- Browser-behavior tests run the real `app.js` in a Node minimal-DOM harness
  (`tests/contract/settings_ui_harness.cjs`); a real in-app browser smoke covered bootstrap,
  save, replacement, and close manually.
- An explicit `resume` for a record whose config is still at the original generation returns
  `NO_COMMIT_TO_RESUME`: the journal stores hashes only, so the user reviews and applies again.
- The settings process exits when its bootstrap is unused for 30 s, 60 s after idle expiry, and
  0.5 s after `Close Settings`.

Known limitation: the old tab shows "Settings session moved" only when it reaches the old process
during the 2 s drain. With 30 s passive polling it usually reaches a closed port and shows the
mock's "Settings session closed" state, which has the same safe behavior.

GUI-2/3 note (web reviewer optional item): each credential change record will state whether the
change takes effect live or only contributes to the desired/applied fingerprint that the running
service reports after restart.

## Final-review fix record (2026-09-29)

- Per-launch namespace: HTML, static assets, APIs, and the session cookie (`Path=/<prefix>/`) live
  under an unpredictable per-launch prefix; any other prefix answers `404 SESSION_NOT_FOUND`. The
  page treats `SESSION_NOT_FOUND`, `CSRF_REJECTED`, `HOST_REJECTED`, and any bare 401 as the safe
  ended state. The document and static assets require a session; the bootstrap request is the only
  exemption and needs exactly one `bootstrap` query key plus exactly one `Sec-Fetch-Mode: navigate`,
  `Sec-Fetch-Dest: document`, and `Sec-Fetch-Site: none|same-origin`. Duplicate Fetch-Metadata
  headers are rejected everywhere.
- Replacement protocol v2: `prepare` -> `ready` (authenticated, no state change) then `commit` ->
  `committed`. Commit takes effect before its acknowledgement; a caller that loses the
  acknowledgement waits for owner exit and lock release. Shutdown runs on an independent task that
  waits for the notification drain and the mutation barrier (`MUTATION_DRAIN_SECONDS=8.0`), which is
  why `OWNER_EXIT_WAIT_SECONDS` rose from 5 to 12 seconds. Config/status work runs in worker
  threads; the browser opens on a daemon thread.
- Apply is bound to the reviewed candidate: `validate` returns normalized values, generation, and
  `candidate_hash`; `apply` requires that hash and rejects any other patch with `REVIEW_STALE`.
  The page drops the review on any input change.
- Journal schema v2: action-specific schemas fix binding keys and effect order; records carry the
  canonical config binding (`config_path`, `config_dir_id`); a transition validator enforces
  immutable identities and effect evidence, allowed phase transitions, terminal immutability, and
  "complete implies every effect verified". `run_effect` resumes an intent only from its persisted
  pre-state. Recovery refuses another config target (`OPERATION_OTHER_TARGET`) and records
  `completed_then_superseded` when a verified change was later overwritten. A config-lock timeout
  before any side effect resolves the record as `resolved_without_change`.
- Setup readiness: every step GUI-1 cannot prove is `Partial` (input missing or no explicit choice
  saved) or `Not checked` (saved but unverifiable here), with a plain reason. Local runtime
  health is reported separately; overall Ready requires every step proven.
- Structured field errors `{field, code, message}` for every invalid field, persistent inline
  messages bound with `aria-invalid`/`aria-describedby`, focus on the first invalid field, and a
  busy state that disables review/apply and announces progress.
- Port-reuse regression runs two in-process apps behind one host:port and one shared cookie store
  (ASGI switch); a real browser with forced OS port reuse was not automated.

## 5C fix record (2026-09-29, journal schema v3)

- Credential schemas follow the accepted parent-plan machines. Enrollment: `credential_stage`
  (stage + verify) -> `config_commit` (config/binding CAS) -> `credential_promote`; promotion can
  never precede a verified CAS. A mutually exclusive `abandon` branch (`credential_stage` ->
  `staged_delete`) removes only the operation-owned staged copy while the CAS is unverified.
  Replacement: `credential_stage` -> `credential_backup` -> `credential_promote` -> `config_commit`
  -> `backup_delete`; the mutually exclusive `restore` branch (`... credential_promote` ->
  `credential_restore` -> `backup_delete`) is allowed only when promotion is verified and the config
  commit is not. Forget: `config_detach` -> `credential_delete`.
- Schema links bind states across effects: the backup preserves exactly the version that promotion
  replaces, promotion activates exactly the staged version, a restore reactivates exactly the backed
  up version from exactly the promoted one, and every deletion must read back `absent`.
  Completion requires every effect of the selected branch verified, so success needs verified
  backup deletion.
- The role-lock key is derived from the immutable binding (`provider/profile/role`); record
  validation and creation reject any other key set (`ROLE_BINDING_MISMATCH`), so overlapping
  operations on the same binding role cannot use different locks.
- `replacement_recovery_action` chooses `restore_backup`, `continue_commit`, or
  `manual_review` from recorded version IDs and current readbacks only.
- Optional item: a per-group edit epoch discards a validation response that returns after the
  entry changed, so a stale preview is never published.

## 5C rereview fix record (2026-09-29)

- Branch admission is readback-bound. `switch_branch` requires the record's canonical role lock
  and the bound config file's lock, reads the named stores, and persists an immutable
  `branch_proof`. Enrollment `abandon` needs config exactly at the original generation, no
  promotion record, and the staged slot at the recorded staged version. Replacement `restore`
  needs the active slot at the recorded promoted version, config exactly original, and the backup at
  the recorded backup version. Otherwise it fails with `BRANCH_PROOF_FAILED` and nothing changes.
- An unapplied intent left outside the new branch becomes `verified_not_applied` (post = pre)
  only when the proof shows its store at the intent's pre-state; any other out-of-branch intent or
  readback mismatch blocks the switch and completion. `replacement_recovery_action` returns
  `manual_review` for every pre-commit continuation when config is not exactly original;
  `enrollment_recovery_action` returns `continue_commit` once the config write is on disk.
- Config effects are bound to the record: `config_commit` (enrollment, replacement),
  `config_detach` (forget), and `config_replace` require pre-state = `original_generation` and
  intended state = `candidate_hash`, checked before the store is read or written. Credential records
  require distinct SHA-256 generations.

## 5C round-2 fix record (2026-09-29)

- Branch transitions are sealed: generic `JournalOperation.update()` no longer accepts `branch`,
  `branch_proof`, or `planned_effects`, and the transition validator rejects any branch change,
  proof change, or new `verified_not_applied` outside `switch_branch`, which commits through its own
  write path after the lock and readback checks.
- Every alternate-branch effect (`staged_delete`; `credential_restore`; `backup_delete` after a
  restore) requires the canonical role lock and the bound config lock for the whole call and rereads
  its guards first: config still at the original generation, plus the backup version before a
  restore and the restored active version before backup deletion. The effect's own store is checked
  through its linked pre-state. A mismatch fails with `BRANCH_GUARD_FAILED` before any store is
  touched. `enrollment_recovery_action` and `replacement_recovery_action` (now taking
  `backup_state`) return `manual_review` on a selected branch whenever current readbacks differ.
- Deferred to GUI-2 (accepted optional): branch/guard observers are still supplied by the caller;
  GUI-2 will derive them from code-owned store resolvers keyed by the binding once real credential
  stores exist.
