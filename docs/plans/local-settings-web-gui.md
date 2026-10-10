# Syllva Local Settings Web GUI — implementation plan

Date: 2026-09-27. Current status (2026-10-04): **GUI-1/2/3 implemented and integrated locally.
The accepted bounded GUI-2/3 bundle and the three later PR13 findings are separate; those later
findings remain unresolved. GUI-4 has an unaccepted draft and resumes after the newly requested
actual-material flow validation. GUI-5 remains paused. Current state and human scope are recorded
in `handoff.md` and `local-settings-web-gui-task-record.md`.**

Model/profile selection, skills, independent-review applicability and effort, safety, notifications,
and delivery permissions follow the current global Codex policies referenced by `AGENTS.md`.
Dated review outcomes and assignments below preserve historical observations, not current routing
or authorization. Earlier reviewed document bytes remain in their original packages/Git snapshots.

Supported platforms for GUI-1: macOS and Linux. **GUI-1 is unavailable on Windows**: `uls setup`
refuses to start there before creating any runtime state or listener. Windows replacement support is
not implemented and is not implied by this plan until the separate named-pipe, DACL/SID/PID, and
Windows reparse-point/owner-check work is designed, reviewed, and accepted (pending user decision).

## Task record

- Risk: high. This work joins credential enrollment, provider identity, configuration mutation,
  remote-access settings, and a new user-facing flow.
- Historical technical worker assignment: `gpt-6-luna` / max, selected for local HTTP session security,
  cross-process locking/CAS, and crash-safe multi-store recovery. The current orchestrator retains
  plan acceptance; resolve its identity from the installed global profile and runtime.
- Required new plan/final reviews follow current global policy: independent native web ChatGPT,
  plus independent Gemini ultra for material UI/design/flow changes. Historical Gemini high
  observations below are not evidence that a new ultra gate passed.
- Gemini plan review (2026-09-27): **REVISE**. This revision integrates its ten required findings.
- Web ChatGPT plan review (2026-09-28, historical): **REVISE** with required findings R1-R3.
  This revision integrated those findings; targeted rereview was pending at that checkpoint.
  Later scope-specific acceptance is recorded in the current task record.
- Interaction evidence for the user-facing flow is frozen separately in
  `docs/plans/local-settings-web-gui-interaction-mock.md` and must be included in UI-flow rereviews.
- Current implementation candidate under `scripts/knu_lms_*` is not accepted and must not be
  treated as this plan's implementation.
- No live credential write, provider mutation, scheduler activation, reconciliation apply,
  commit, or push is part of PLAN acceptance.

## 1. Product goal

Provide one nondeveloper-facing local control panel for Syllva setup and settings without
collapsing the system's existing security boundaries into one file or one credential.

The user launches **Syllva Settings** and works in a browser. The UI runs only on the local
machine and manages the existing authoritative stores behind typed operations:

```text
Browser on this computer
        |
        v
Syllva Local Settings server (on-demand, loopback only)
        |
        +-- config.yaml                         non-secret core config
        +-- OS Keychain / Credential Manager   interactive secrets
        +-- protected local secret files        unattended worker secrets
        +-- protected Google credential files  worker/read-only service accounts
        +-- Canvas connector manifests          non-secret provider bindings/leases
        +-- existing doctor/status APIs         readiness evidence
```

"One GUI" does **not** mean "one storage location." Secrets keep their provider- and
purpose-specific storage and are never copied into YAML, browser storage, logs, or MCP.

## 2. Invariants and non-goals

The GUI must not change the frozen ULS rules:

- MCP search remains read-only.
- SOURCE / AI / USER ownership remains distinct.
- `Verified`, exam-scope confirmation, and other human-owned academic approvals are not settings
  and are never promoted by this UI.
- worker credentials and read-only MCP credentials remain separate where the provider supports it.
- no public sharing is introduced for convenience.
- A provider ID may be saved as Configured/Unverified for setup, but only exact read-only identity evidence can make it a verified active academic binding. Credential presence and manual ID entry never establish readiness or authorize a consumer.
- the settings server is never exposed through Remote MCP or the Cloudflare Tunnel.

The first version is not a generic YAML editor, an arbitrary secret manager, a Notion schema
provisioner, or a Cloudflare-account administration console.

## 3. Launch and local security boundary

Add an explicit launcher such as `uls setup` (desktop shortcut can call the same entry point).
It starts an **on-demand** settings process, opens the default browser, and stops when the setup
session ends or idles out. It is a different app/process/port from Remote MCP.

One settings process owns a per-user, owner-only single-instance OS lock for its full lifetime. A
second `uls setup` first sends a same-user authenticated shutdown request to the current owner; the
owner immediately invalidates its bootstrap/session/CSRF state, rejects mutations, and enters a
bounded notification-only shutdown drain. During that drain, requests to the old port receive a fixed
`401 {"error":{"code":"SESSION_REPLACED"}}` with no application data; then the owner closes its
listener and exits. The new launcher waits until the old process has exited and released the lock
before it binds a port or creates any bootstrap capability. If the owner cannot be authenticated or
does not exit within the bounded wait, the new launch fails closed without issuing a bootstrap
capability. A successful replacement therefore leaves the old port closed and its session unusable
before the new session starts.

Security requirements:

1. Bind only to loopback. Prefer `127.0.0.1` on an OS-assigned random port; never `0.0.0.0`.
2. Generate a CSPRNG one-time `bootstrap_token` with at least 256 bits of entropy for each launch. It
   is memory-only, valid for at most 30 seconds, and bound to the exact process/port. The launcher
   opens exactly `http://127.0.0.1:<port>/?bootstrap=<token>`. Bootstrap consumption is the sole GET
   authentication-state exception in this application: the first valid launcher-opened top-level
   document navigation consumes the token, invalidates it immediately, creates independently
   generated CSPRNG session and CSRF capabilities, sets the session cookie, and returns a `303`
   redirect to `/` so the bootstrap capability is removed from the address bar and browser history.
   The redirect sets only the session cookie and carries no CSRF value.
   Require browser fetch metadata for bootstrap consumption to identify a top-level document
   navigation (`Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`, and `Sec-Fetch-Site: none` or
   `same-origin`); explicitly reject iframe/frame and subresource destinations. Refreshing, replaying,
   or guessing a bootstrap URL after consumption must fail closed.
3. Validate `Host` on every request as the exact generated `127.0.0.1:<port>` value; reject
   `localhost`, alternate loopback names, forwarded-host headers, and external hosts. Top-level GET
   navigation does not require an `Origin` header because browsers may omit it. State-changing API
   requests require exact `Origin: http://127.0.0.1:<port>` plus the session cookie and CSRF header;
   `Referer` may be used only as a fail-closed origin fallback when `Origin` is absent. Disable CORS.
4. Session and CSRF values are independently generated with at least 256 bits of CSPRNG entropy.
   The server-side session record and expected CSRF value exist only in server memory for the process
   lifetime. The session handle is delivered only in the `HttpOnly; SameSite=Strict; Path=/` cookie,
   with no `Domain` attribute. After the clean `303` redirect, the page makes an authenticated
   same-origin `GET /api/v1/session/csrf`; that endpoint returns the per-session CSRF value with
   `Cache-Control: no-store`; it requires the valid session cookie and same-origin fetch metadata,
   plus an exact `Origin` when present. Client code keeps it only in ephemeral page/JavaScript memory,
   never in a URL, localStorage, sessionStorage, IndexedDB, a readable cookie, or logs. Unsafe requests
   require the session cookie plus this value in a custom header. Closing, expiry, process shutdown, or
   replacement invalidates both server-side values; the page clears its in-memory CSRF value when the
   session ends. Apart from bootstrap consumption in item 2, GET never changes authentication or
   application state. Initial/top-level document navigations must be same-site documents and must
   never accept iframe/frame embedding.
5. Serve all HTML/CSS/JS locally. No CDN, analytics, external fonts, service worker, or remote
   script execution.
6. Use restrictive CSP, `frame-ancestors 'none'`, `Cache-Control: no-store`, no-referrer policy,
   and same-origin isolation headers where applicable.
7. Store no credential or capability in `localStorage`, sessionStorage, IndexedDB, telemetry, or logs.
   The authenticated `GET /api/v1/session/csrf` in item 4 is the sole post-bootstrap API response
   allowed to deliver a reusable capability; it is same-origin, `no-store`, and its value remains
   only in ephemeral page/JavaScript memory. The bootstrap token is the sole launch-URL exception: it
   may appear only until the first consuming request and immediate `303` redirect, and is never
   persisted or returned afterward.
8. Password/secret inputs are write-only. Existing values render only as `Configured` /
   `Not configured` / `Error`; the backend never returns them.
9. Apply an inactivity expiry and explicit `Close settings` action. Passive/background requests such
   as Overview polling, provider-status refresh, or retry timers do **not** renew inactivity. Only an
   explicit user interaction that reaches the authenticated UI, including the explicit `Stay signed
   in locally` action, may renew it. The frontend warns before expiry when possible, then replaces the
   app with a clear `Settings session expired` screen rather than leaving the user with a generic
   connection error. `Close settings` invalidates the session and gracefully stops the settings
   process after returning an ended-session page. An API `401 SESSION_EXPIRED` clears the in-memory
   CSRF value and secret inputs while keeping dirty non-secret values only in ephemeral page memory
   until close/reload; no draft is persisted or retried. Session capabilities die with the process.
   If the backend returns `401 SESSION_REPLACED` or a fetch fails because the old backend is closed or
   unreachable, immediately show the interaction mock's read-only moved/closed screen. Clear the
   in-memory CSRF value, clear every secret input value and all secret values retained by page code, and
   retain dirty non-secret values in the existing DOM as read-only, selectable text. Disable
   Save/Next/Apply/Restart and every other mutation control. Add a
   beforeunload warning only while dirty non-secret values remain. Announce the state through a
   `role=alert`/live region and move focus to its heading. Distinguish the fixed `SESSION_REPLACED`
   response from `SESSION_EXPIRED`; map an unreachable backend to the same safe closed state without
   throwing an uncaught error or persisting/retrying the draft.
10. Settings access/error logging is secret-minimal from the first bootstrap request onward: never log
    raw query strings, request bodies, cookies, authorization headers, CSRF headers, bootstrap tokens,
    or submitted credential material. Log only route templates, fixed error codes, and safe metadata.
11. Protect every config mutation with one cross-process OS/file lock shared by all config-writing GUI
    and CLI services. The lock covers the final raw-byte reread, expected-generation comparison,
    validated candidate binding, atomic replace, directory fsync, and readback. A Python/thread-local or
    settings-process-only lock is insufficient.

Use the Starlette/uvicorn family already present in the MCP dependency path rather than creating
a second web stack. Keep the settings application in its own adapter/package so core domain,
retrieval, state, and intake modules do not depend on the web UI.

## 4. Information architecture

The primary navigation is intentionally small:

1. **Overview** — readiness, problems, last successful live checks, restart-required state.
2. **Connections** — Canvas, Google Drive, Notion, optional GitHub/AI credentials.
3. **Academic** — active semester, provider-verified courses, Drive/Notion semester bindings.
4. **Automation** — intake worker and study-note feature toggles/status.
5. **Remote Access** — Remote MCP, Cloudflare-Tunnel profile, Google OAuth owner setup.
6. **Advanced** — expert limits and compatibility settings.

First launch uses a resumable stepper over the same settings rather than a separate configuration
model. Completion is derived from durable non-secret config/readiness, never from browser storage:

`Storage roots/Notion parent -> Canvas -> Academic scope -> Automation -> Remote Access (optional) -> Check`.

Step 1 collects only provider/workspace roots that do not depend on course identity. Course-specific
Drive folders, Notion course portals, and other per-course mappings are deferred to **Academic scope**
after Canvas course selection exists. A user may leave and relaunch setup without losing already
saved steps; secret fields themselves are never repopulated.

Each step has a durable completion predicate independent of transient live-check health:

- **Storage roots / Notion parent** — durable configured values and live verification are shown as
  separate facts. Keep the existing `legacy_global` retrieval path usable without new Academic fields.
  Worker credentials are required only when the corresponding feature is explicitly enabled.
- **Canvas** — the Canvas profile identity, selected term, and selected course registry are durably
  bound for the active setup path.
- **Academic scope** — the saved Canvas term/course IDs join exact Course Keys, and every mapping
  required by the explicitly enabled feature path is present. Manual or discovered IDs may be saved
  as Configured/Unverified, but that state is not verified readiness and cannot be consumed by
  semester retrieval or worker processing.
- **Automation** — each feature has an explicit durable Enabled/Disabled choice; the schema default
  `worker.enabled=True` is not a user choice. Enabled intake/study processing additionally requires
  the exact selected-semester mappings and purpose-specific worker readiness. Disabled features need
  no worker credentials and keep their existing mappings editable.
- **Remote Access** — either a valid Remote MCP setup is durably configured, or the user explicitly
  chooses `Skip / keep Remote MCP disabled`. Skipping completes this optional step without enabling
  public remote access.
- **Check** — show structural setup, verified binding, runtime state, and transient health separately.
  A transient provider outage does not erase a previously verified receipt, but an unverified mapping
  or absent process observation never becomes Ready by inference.

Every card distinguishes four facts instead of one ambiguous "connected" badge:

- saved configuration;
- credential present;
- last live verification result + timestamp;
- feature enabled/disabled.

Allowed base status words are `Ready`, `Partial`, `Blocked`, `Failed`, `Disabled`, `Not checked`, and
`Unsupported`. `Restart required` is a separate modifier/banner, not a connection status. Do not
report `Ready` from a saved ID or token presence alone. `Partial` means durable setup exists but one
or more required checks or stores are incomplete; `Blocked` means a named prerequisite is missing;
`Failed` means a completed check returned an error.

## 5. Settings taxonomy

### 5.1 General

Editable:

- timezone;
- current/active academic semester selection once a semester mapping exists.

Visible but system-managed/read-only in v1:

- workspace directory after initialization;
- state backend, ephemeral backend, normalized-derivative backend;
- Behavior Contract version/path;
- normalization schema/processor version;
- MCP `read_only=true`.

Changing the workspace directory after data exists is a migration, not a settings edit, and is
therefore outside this GUI version.

### 5.2 Generic Canvas LMS connection

Replace product-facing KNU terminology with **Canvas LMS**. KNU remains only the first live-tested
instance. Do not claim universal Canvas compatibility; institutions may disable PAT creation or
restrict standard API endpoints.

User-visible fields/actions:

- Canvas base URL, HTTPS origin only;
- `Connect` / `Replace access token`;
- `Pause / Disable Canvas sync` as a non-secret feature toggle;
- `Forget Canvas token` as a separate destructive credential action with explicit confirmation;
- verified Canvas account identity (masked/display-safe metadata only);
- Canvas terms/courses discovered by standard REST API;
- user selection of the semester/term and courses Syllva should use;
- authorization-lease status and `Renew access` without token re-entry.

Credential model:

- one connection profile represents one exact Canvas origin + verified Canvas account;
- the PAT is entered once and saved only after `/api/v1/users/self` verifies the exact origin/account;
- add a fixed credential role `CANVAS_PAT` to the code-owned credential allowlist. Its only allowed
  source is `keyring`, with service name `Syllva Canvas`. After verification, derive an immutable
  secret-free `profile_id` from canonical origin + Canvas user ID and use `profile:<profile_id>` as
  the keyring account locator. The browser can see the profile ID but never the PAT or keyring value;
- the PAT is not scoped to one semester and is not assigned a fake 30-day token lifetime;
- a separate secret-free authorization lease, maximum 30 days by default, binds the profile,
  origin, verified account, selected resource policy, and current course/term registry;
- renewal verifies the same binding and credential presence without reading the token back into
  the browser;
- changing origin/account creates a new profile rather than silently rebinding an old secret.

Connection test sequence is read-only and explicit after the user presses `Test connection`:

`origin validation -> /api/v1/users/self -> bounded course/term discovery -> user selection ->
exact selected-course re-read -> save binding`.

The connector uses only standard Canvas REST endpoints in its first release. Hidden LearningX or
institution-specific private APIs are out of scope.

Forgetting a Canvas token is authorized against the **local** profile record and exact keyring
locator. It must not require a successful provider call: an expired/revoked token or unreachable
Canvas host is a primary reason the user may need to forget or replace it. A 401/403/unreachable
live check may be shown as evidence but cannot deadlock local credential removal.

The source-import/data-path work remains a separate reviewed bundle. This GUI may enable or show
Canvas sync only after that bundle is accepted; it does not make an unaccepted LMS collector live.

### 5.3 Google Drive

Show two separate credentials because the existing security model requires separate purposes:

- **Read-only retrieval credential** (MCP);
- **Worker credential** (intake writes).

For nondevelopers, use file-picker/upload controls rather than asking for environment variables.
The browser sends the selected service-account JSON only over the authenticated loopback session.
The backend validates bounded JSON and provider identity, writes it to a fixed protected
Syllva-owned credential path with owner-only permissions, verifies it, and configures the path.
The browser receives only readiness metadata. Never return or echo JSON contents/private keys.

Google credential files use the existing `GOOGLE_CREDENTIAL_PATH_MAX_BYTES` limit (64 KiB), not the
generic 4 KiB secret-value writer limit. Implement this with a dedicated service-account writer or
a reviewed `write_secure_file(..., max_bytes=...)` path that keeps the same regular-file, owner,
permissions, symlink, atomic-replace, and readback checks.

The two credential slots must remain distinct and pass the existing credential-separation check.

User-visible non-secret settings:

- university/root Drive folder selection or exact ID/URL;
- semester root and upload folder;
- optional course-specific upload folders;
- read-only `Test retrieval access` and worker-capability `Test intake access` actions.

Provider discovery may help select folders, but a display name alone never becomes a durable ID.

### 5.4 Notion

Separate:

- read-only MCP token -> OS keyring;
- worker token -> protected unattended secret file.

User-visible settings include current semester parent/workspace and the existing Courses,
Sessions, Materials, File Intake, Input Request, plus optional extension datasource bindings.
Prefer verified discovery/selection after credentialed read-only inspection; keep exact-ID manual
entry under Advanced for recovery. Do not automatically create or rebuild schemas in this plan.

### 5.5 Optional GitHub and AI credentials

Expose only when the related feature is available:

- GitHub read token -> keyring;
- generic LLM API key -> keyring.

Do not imply that an LLM key is needed for MCP-client-generated study notes when the configured
study-note path does not use it.

### 5.6 Academic settings

The Academic page uses exact saved Canvas registry IDs and does not infer identity from names. It may
save structurally valid manual or discovered provider IDs as Configured/Unverified. Only an explicit
read-only provider verification can make an exact mapping a verified active binding.

- Academic active semester for course and worker context;
- the independent MCP retrieval lane/semester selector (`retrieval.notion_lane` and
  `retrieval.semester`), shown separately with its own reviewed diff; changing Academic semester
  never changes retrieval scope, and retrieval defaults remain `legacy_global` with an empty semester;
- Canvas course selection and its verified provider IDs;
- Syllva course keys/names/codes/sections;
- all Drive and Notion mappings required by the enabled feature path, including the current
  `resolve_semester_workspace` worker-required set when intake/study processing is enabled;
- optional portal/upload mappings when configured, without making them prerequisites unless an
  accepted consumer contract requires them;
- per-course readiness status.

Readiness is purpose-specific: `legacy_global` retrieval remains independent of Academic selection,
Canvas joins, and Academic receipts; `semester_workspace` retrieval requires only the selected
workspace's verified read-side mappings. Enabled intake/study processing additionally requires the
explicit Academic semester, full current worker resolver mappings, and separate worker-role evidence.
Credential presence and syntactically valid IDs alone never promote readiness. Existing configs with
no Academic active-semester field remain loadable and are not silently migrated; the GUI shows
Not selected and withholds Academic/worker readiness until the user chooses and verifies one.

Module position, `1주차` labels, file names, and LMS titles never auto-create Session No, lecture
date, completion state, or content approval.

### 5.7 Automation

Expose user-meaningful controls:

- intake worker enabled/disabled;
- a desired poll interval, validated to exact integer 1–1440 only when the interval itself changes;
  toggle-only edits preserve an existing positive legacy value and its YAML scalar bytes;
- study-note feature enabled/disabled;
- Canvas scheduled sync only after its separate ingestion bundle is accepted;
- last run, next run only when an external scheduler provides verified readback, current lock/busy
  state, and actionable failure summary when observed. Otherwise schedule/next run is Not reported.

Turning a feature on must not silently run a live provider mutation during the settings POST.
Activation is saved first; the UI then presents the explicit start/restart action and readiness
requirements. Saving never starts a process or scheduler. Manual provider IDs remain
Configured/Unverified until the purpose-specific read-only check succeeds.

### 5.8 Remote Access

Make the recommended path the default UI:

- Remote MCP enabled;
- `mcp_oauth`;
- Cloudflare Tunnel edge mode;
- public MCP URL;
- Google OAuth Web client ID;
- authorized owner email;
- Google OAuth client secret -> keyring;
- generated redirect URI shown read-only with copy button;
- local/remote health and OAuth discovery status.

Cloudflare tunnel/account creation remains an external-infrastructure wizard/status check until a
separately reviewed Cloudflare management integration exists. The GUI must not ask for or persist a
Tunnel token merely to edit Syllva settings.

Direct TLS, OIDC, development bearer credentials, host/port, certificate paths, and TTL knobs live
under **Advanced / developer settings** with the existing validation unchanged. Public unauthenticated
MCP is never offered as a normal toggle.

## 6. Mutation model and persistence

### 6.1 Non-secret config

Treat the existing config file as canonical for core non-secret settings.

1. Read raw config bytes and expose a non-secret settings model plus a `config_generation` hash.
   Preserve unknown/unrelated valid config sections and keys that are outside the GUI's typed
   mutation surface; applying one GUI group must not rebuild the file from only fields the GUI knows.
2. The browser submits a typed, allowlisted patch and the generation it edited.
3. The backend constructs the complete typed config in memory and runs existing validation plus
   group-specific invariants.
4. If the file generation changed, reject with `Configuration changed; reload` rather than
   overwriting concurrent manual/operator edits. The frontend preserves all dirty non-secret form
   values and offers a reload/reapply flow rather than discarding them.
5. Before final apply, show a redacted semantic diff containing only allowlisted non-secret changes,
   secret readiness transitions (`Not configured -> Configured`) and restart impact. For GUI-4
   verified bindings, server-issued receipts and choice evidence are included before the final
   candidate hash/diff is generated; the user reviews that final candidate, and Apply cannot append
   fields afterward. Never render submitted secret values, protected-file contents, or provider
   payloads in the diff.
6. Acquire the shared cross-process config lock before the final raw-byte reread. Inside that critical
   section, recompute the generation, compare it to the caller's expected generation, bind the already
   validated candidate to that exact generation, then write through a same-directory owner-only
   temporary regular file, fsync, atomic replace, and directory fsync. Refuse symlink/untrusted
   ownership targets.
7. Read the file back and validate again while still holding the lock before reporting `Saved`, then
   release the lock. All other GUI/CLI config writers must use this same primitive.

GUI-4 keeps structurally valid Configured/Unverified mappings on a separate Apply path without proof;
changed mappings invalidate their old receipts in that reviewed candidate and do not unblock
semester consumers. In the verified path, the server proof binds the pre-receipt candidate, original
generation, transaction, exact mapping/purpose, provider identity, and credential-role revisions.
The backend adds the receipt and a non-circular digest of the explicit Enabled/Disabled choice tuple,
computes final candidate H1, and presents H1's redacted diff. Apply rechecks and commits those exact
bytes; any draft, proof, or choice change requires a fresh preview/review.

After an interrupted replace, compare authoritative raw config bytes with both the exact reviewed H1
and the original raw snapshot for its generation. H1 means committed and requires exact readback;
the original means not committed and permits only journal-safe recovery; a third hash is Partial and
requires manual review. Do not promise rollback or that old bytes survive a crash after replacement.

Do not accept arbitrary JSON-pointer/YAML-path writes from the browser.

### 6.2 Secrets

Credential endpoints accept one supported credential role, never an arbitrary service/account or
path. Reuse code-owned credential allowlists/bindings and OS-native keyring checks.

- responses never contain the submitted or stored value;
- replace is a separate user action and verifies a staged new value before the active known-good value
  can be replaced;
- deleting/forgetting a credential is separate from disabling the feature;
- a new enrollment may leave an unused staged credential after a later config failure, but recovery
  must never guess whether an unknown prior secret can be deleted or restored. Report the exact
  partial state;
- replacement and forget follow the action-specific transaction machines below rather than a generic
  credential-first order.

Credential actions that also update `config.yaml` return the **new** `config_generation` after
readback. The frontend updates its active generation without clearing dirty form inputs so a valid
credential enrollment does not make the user's still-open non-secret form immediately stale.

### 6.3 Multi-store transaction journal

Every operation has its own durable, secret-free record in a code-owned owner-only journal directory.
Create records exclusively with mode `0600`; update only that operation's record through a
symlink-safe same-directory atomic replacement, file fsync, and directory fsync. Concurrent writers
never replace a shared journal index or stale whole-journal snapshot, so one operation cannot erase
another's record. Each record contains an operation ID, action kind, exact fixed credential/profile
role, original config generation, candidate config hash, intended non-secret binding, phase, exact
staging/backup/version locator identities, per-effect pre/post state IDs, and readback result. It never
stores credential values or uploaded JSON.

Operations acquire cross-process owner-only locks for each affected `(provider, profile identity,
credential role)`, in sorted canonical order, then that operation's cross-process journal-record
lock, then the shared config lock. Config-only operations skip the role lock. This is the sole lock
order; release in reverse order and never acquire an earlier lock while holding a later one. Under the
role lock, a new operation checks for an unresolved record for that exact role. If one exists, it fails
with `OPERATION_IN_PROGRESS`; disjoint roles may proceed concurrently. A role remains reserved across
process exit while its record is unresolved, including after the user chooses `Leave as-is`. Hold the
role lock through each active operation/recovery; after a crash the OS lock is released, and the
unresolved record continues to reserve the role until recovery reacquires it. Resolve it only by
verified completion or a terminal `resolved_without_change` disposition proving that no uncertain side
effect remains.

Before each authoritative store effect, durably record its intent, exact expected pre-state, target
version/locator, and applicable config generation. Perform only that one effect, read the authoritative
store back, then durably record the observed post-state/version. Never combine multiple store effects
under one journal phase. Recovery compares these recorded identities with exact readbacks; it never
infers success from a phase label alone.

Use action-specific state machines:

- **New enrollment:** `prepare -> stage new credential -> verify staged credential -> config/binding
  CAS -> promote/activate -> readback -> complete`. Journal each store effect with the pre/post
  protocol above. A failure after staging may leave only that exact new operation-owned credential
  orphaned; the UI reports it and offers deterministic cleanup/repair.
- **Replacement:** `prepare -> stage new credential separately -> verify staged credential -> acquire
  shared config lock and revalidate expected generation/candidate -> preserve the exact old active
  credential in an operation-owned protected backup -> journal intent and promote staged credential ->
  read back and journal its exact active version -> journal intent and commit the prepared non-secret
  binding by config CAS -> read back and journal the config generation -> read back all stores -> journal
  intent and delete backup -> verify deletion -> complete`. Never combine credential promotion and
  config commit as one effect. If recovery finds the exact staged version active while config still
  matches the original generation, the recorded repair is to restore the exact operation-owned backup;
  if config matches the candidate generation and the staged version is active, continue post-commit
  verification and backup cleanup. Any other version/generation combination remains `Partial` for
  explicit inspection; recovery never guesses. The current known-good credential remains recoverable
  until successful post-commit readback. Google service-account replacement uses a protected staging
  file and atomic promotion; a syntactically valid but provider-invalid upload never replaces the
  current file. Temporary backup/staging locators are code-owned and never caller selectable or
  browser-visible.
- **Forget:** `prepare -> under shared config lock CAS the non-secret binding/profile/lease into a state
  that no longer depends on the credential -> read back detached state -> delete the exact fixed local
  credential -> readback -> complete`. If deletion fails, the safe partial state is explicitly
  `configuration detached; old local credential still present; retry deletion`; config must never keep
  referencing a credential that was already deleted.

Each journal sub-state is durable. If a later effect fails, the operation remains `Partial` with one
phase-specific repair action. Recovery uses only recorded version/locator identities and readbacks,
never guesses which previous secret existed, and never silently overwrites a concurrent config edit.
Relaunching Settings detects unfinished records and offers `Resume repair` or `Leave as-is`; no
timeout auto-repair is allowed.

The journal is recovery evidence, not a second source of truth. Successful completion requires
readback from every authoritative store named by the operation, after which the compact journal
record may be retained for diagnostics without secrets.

### 6.4 Restart semantics

Each setting declares one of:

- `live`: next operation reads the new value safely;
- `worker restart required`;
- `Remote MCP restart required`;
- `setup restart required`.

The first implementation does not restart services as a side effect of `Save`. The Overview page
shows a persistent `Restart required` banner and a separate explicit restart/start action where a
reviewed cross-platform service controller exists.

For every restart-scoped service, define a stable fingerprint over exactly the settings that service
loads. Settings records/derives the **desired** fingerprint from the saved config. The worker and
Remote MCP process report or durably record the **applied/loaded** fingerprint only from the
process that successfully loaded the settings and reached its actual execution boundary: after a
one-shot worker acquires its worker lock, or after an MCP transport successfully enters service.
Constructing a worker/server, Settings Save, credential presence, or a matching config hash cannot
report applied. The setup process exposes its own startup fingerprint directly.
`Restart required` is derived from desired != the fingerprint loaded by a confirmed active
restart-scoped service, never from an in-browser dirty flag. It clears only after observing that
service successfully running with the desired fingerprint. A failed restart retains the mismatch and
fixed error state; a service that is stopped is distinct from one running the previous fingerprint.
Show `Not reported`, `Stopped`, `Running with current settings`, and
`Running with previous settings` as distinct states; a previous fingerprint never receives a
Ready label. For a one-shot worker, an active older run is Running with previous settings and the
next invocation loads desired values; after it exits, show Stopped with last-load details and do not
invent a daemon restart requirement. If a restart-scoped MCP process is stopped, show Stopped plus the
explicit start action and desired/last-loaded difference. Readiness remains separate from process
state. Failed or unobservable startup does not replace the last successful observation or fabricate a
current one.

For every GUI-2/GUI-3 credential change, state whether the new credential is consumed live or include
a non-secret credential revision in the affected service's desired/applied fingerprint. Never derive
or expose that revision from the credential value itself.

## 7. HTTP/API surface

Use purpose-specific, versioned local routes rather than a generic admin API. Representative shape:

```text
GET  /api/v1/overview
GET  /api/v1/settings/<group>
GET  /api/v1/session/csrf
POST /api/v1/settings/<group>/validate
POST /api/v1/settings/<group>/apply
POST /api/v1/session/close

POST /api/v1/credentials/<fixed-role>/set
POST /api/v1/credentials/<fixed-role>/replace
POST /api/v1/credentials/<fixed-role>/forget

POST /api/v1/connections/canvas/test
POST /api/v1/connections/canvas/save
POST /api/v1/connections/google/<purpose>/test
POST /api/v1/connections/notion/<purpose>/test
POST /api/v1/remote-mcp/test
POST /api/v1/remote-mcp/oauth-grants/reset
POST /api/v1/doctor/live
```

Every request requires the exact generated `Host`. A top-level GET/document request may omit
`Origin`; it still requires a valid consumed-bootstrap session (except the one-time bootstrap GET)
and may never mutate application state. The one-time bootstrap document GET is the sole GET allowed
to mutate authentication state by consuming the bootstrap token and creating the in-memory session.
Every state-changing route requires session + exact `Origin` (or strict
same-origin `Referer` fallback if `Origin` is absent) + custom-header CSRF + generation/candidate
binding where applicable. Provider live tests happen only after an explicit click and remain
read-only unless the action clearly says it is a worker/write-capability test.

All request bodies have fixed route-specific limits enforced before parsing. Ordinary JSON mutation
and confirmation bodies use a small bounded limit; secret-value routes use the smallest bound suitable
for their credential type, while Google service-account upload alone may use the existing 64 KiB
credential-file limit.

`POST /api/v1/session/close` invalidates the in-memory session before returning an ended-session
document and schedules graceful shutdown. `POST /api/v1/remote-mcp/oauth-grants/reset` is a separate
high-impact endpoint with exact local SQLite database identity, explicit confirmation payload,
candidate/readback validation, and an audit event; it is not reachable through generic settings
apply or a GET request.

## 8. Disable, forget, and destructive actions

Settings changes must distinguish disabling a feature from removing a secret or local state.

- **Disable Canvas**: stops future collection; imported Drive/Notion/source history remains.
- **Forget Canvas token**: explicit confirmation first CAS-detaches the exact Canvas credential binding
  and invalidates/removes the associated authorization lease, then deletes only the exact profile
  credential after the locally stored profile identity + exact code-owned keyring locator are
  rechecked. Imported academic/source history remains. Live provider success is not required, and a
  revoked token or unavailable provider must not block forgetting the local credential. A delete
  failure leaves the profile detached with the old local credential still present and offers retry.
- **Forget Drive credential**: confirmation names the exact retrieval or worker role and fixed protected
  file/credential slot. It detaches only that role, then removes that exact local credential. Drive
  files, durable academic bindings, and imported/source history remain unless a separately reviewed
  action says otherwise.
- **Forget Notion credential**: confirmation names the exact retrieval or worker role and fixed keyring
  or protected-file slot. It detaches only that role, then removes that exact local credential. Notion
  pages/databases, durable academic bindings, and imported/source history remain.
- **Disable Remote MCP**: config only; does not claim to delete a Cloudflare tunnel or Google OAuth client.
- **Reset local OAuth grants**: separate high-impact operation with exact local database identity,
  candidate/readback, confirmation, and audit; clears only local authorization codes/tokens in the
  identified OAuth database and is not part of ordinary `Save`.
- Drive/Notion credential replacement verifies the staged replacement while the current credential
  remains active/recoverable and never deletes academic content.

No GUI action offers database reset, source deletion, public-sharing enablement, or automated
academic approval.

## 9. Diagnostics UX

Reuse the existing `uls status`, `uls doctor`, and `uls doctor --live` semantics through shared Python
services rather than shelling out and parsing console text.

Overview shows a dependency-oriented checklist such as:

```text
Canvas          Ready         live check 19:20
Drive retrieval Ready         read-only credential + root verified
Drive worker    Blocked       worker credential missing
Notion retrieval Ready
Notion worker   Ready
Academic scope  Ready         5 selected courses
Intake worker   Disabled
Remote MCP      Ready         https://mcp.example.dev/mcp
AI client E2E   Not checked   requires actual client use
```

Provider payloads and secrets never appear in error details. Map fixed backend error codes to concise
remediation text. Keep `Not checked` distinct from failure.

## 10. Accessibility and interaction criteria

- keyboard-complete navigation, visible focus, semantic labels, and form error association;
- do not communicate connection state by color alone;
- every async test/save operation has progress, completion, and retry state;
- async save/test results use an announced `role="status"` / `aria-live="polite"` region without
  stealing focus; destructive confirmation dialogs trap focus and expose `aria-modal="true"`;
- preserve form input after non-secret validation errors; clear secret fields after submit;
- show exactly what a destructive action affects and what it preserves;
- no surprise live-provider call on page load;
- novice path uses task language (`Canvas`, `Google Drive`, `Remote access`) instead of env-var names;
- expert/internal IDs are hidden behind Advanced but remain inspectable for recovery.

## 11. Compatibility and migration

- Existing valid `config.yaml` remains loadable without using the GUI.
- Existing CLI commands remain supported. GUI backend services should be shared with CLI code where
  possible so validation/credential policy does not fork.
- Existing KNU-specific LMS candidate is not promoted. Product-facing implementation creates a
  generic Canvas connector. Any KNU sidecar migration requires an explicit **secret-free preview**
  showing source profile/origin/course IDs and destination bindings, followed by a separate apply;
  no automatic import or secret copying occurs on first launch.
- Existing accepted hourly LMS driver stays unchanged until the generic Canvas ingestion bundle is
  separately reviewed and accepted.
- Existing Remote MCP endpoint and remote clients must not gain any settings/admin route.

## 12. Implementation bundles after PLAN acceptance

Keep reviewable ownership boundaries; do not recreate the previous 1,300-line mixed candidate.

### GUI-1 — Local settings shell and safe config service

On-demand loopback server, bootstrap/session/CSRF boundary, static shell, Overview, typed config
snapshot/validate/redacted-diff/apply, unknown-key preservation, generation conflict, atomic secure
write, transaction journal/recovery shell, resumable first-run state, and shared doctor/status service.

### GUI-2 — Credential controls and core provider connections

Keyring/protected-file service reuse, 64-KiB-bounded Google credential upload staging,
Notion/Google connection cards, live read-only test actions, credential separation, and
credential-write `config_generation` refresh semantics.

### GUI-3 — Generic Canvas connection

Canvas profile/origin/account identity, fixed `CANVAS_PAT` keyring binding, PAT
enrollment/replace/forget including revoked-token recovery, renewable authorization lease,
bounded term/course discovery, exact user-selected registry, and migration boundary from KNU sidecar.
No LMS source download/import in this bundle.

### GUI-4 — Academic and automation settings

Academic active semester and exact course bindings; independent MCP retrieval scope; worker/study-note
toggles; desired interval; purpose-specific verification; truthful stopped/current/previous/unreported
runtime observations and restart state; feature-gated readiness dependency graph.

### GUI-5 — Remote MCP settings

Recommended Cloudflare + MCP OAuth wizard, OAuth client secret storage, derived redirect URI,
status/live checks, and advanced compatibility profiles. No Cloudflare account mutation.

The separate Canvas SOURCE -> private Drive -> intake plan begins only after GUI-3 is accepted.
Interrupted-reservation reconciliation remains its own safety-sensitive bundle.

## 13. Verification plan

Security/contract tests must prove at least:

- non-loopback and wrong Host/Origin requests are rejected;
- guessed port without bootstrap/session cannot read settings; the one-time bootstrap is consumed,
  replay fails, and successful bootstrap redirects to a clean URL;
- bootstrap consumption accepts only a launcher-compatible top-level document navigation and rejects
  iframe/frame/subresource attempts; bootstrap/session/CSRF capabilities are independently generated;
- after the clean redirect, only an authenticated same-origin `GET /api/v1/session/csrf` returns the
  session's CSRF value with `Cache-Control: no-store`; it appears only in ephemeral page/JavaScript
  memory, never in a URL, persistent browser storage, readable cookie, or logs, and becomes unusable
  after close, expiry, or replacement;
- a double-launch race invalidates and stops the first process, waits for its exit/lock release, and
  only then issues the second bootstrap; after replacement the old port is closed and its session
  cannot authenticate;
- instance A's old tab handles `401 SESSION_REPLACED` during the bounded drain and closed-port/fetch
  failure after exit without an uncaught exception. Both paths clear CSRF and all secret values, preserve
  dirty non-secret values as read-only selectable DOM content, disable mutations, arm beforeunload only
  when such values remain, announce/focus the state accessibly, and reject every mutation;
- Settings access/error logs contain no bootstrap query capability, raw query string, cookie, CSRF
  header, request body, or submitted credential material;
- top-level same-host GET navigation succeeds without `Origin`, while mutations require exact
  same-origin `Origin`/fallback `Referer` plus CSRF;
- passive/background polling does not renew inactivity; explicit keepalive/user activity can; session
  close invalidates access and idle expiry produces a deliberate ended/expired flow;
- CSRF is required for every mutation;
- submitted/stored credential material never appears in GET responses, error JSON, logs, HTML,
  persistent browser storage, or config YAML; the CSRF value appears only in the dedicated no-store
  session handoff and ephemeral page memory;
- no external asset requests are emitted by the UI;
- a real two-process config race starting from the same generation permits exactly one writer to
  commit under the shared OS/file lock; the loser gets the generation-conflict flow without overwrite;
- unrelated/unknown valid config keys survive GUI edits byte-semantically or round-trip semantically;
- credential-driven config writes return the new generation and do not clear dirty non-secret form state;
- two concurrent journal writers retain both independent operation records; overlapping operations on
  the same credential/profile role fail with `OPERATION_IN_PROGRESS` until the earlier operation is
  resolved, while lock acquisition follows the fixed role-lock -> journal-record-lock -> config-lock
  order;
- journal crash injection runs after every authoritative side effect and before its durable post-state,
  including credential promotion before config commit; recovery follows exact recorded version IDs and
  config generations, and never guesses or silently rolls back an unknown secret;
- redacted diff output contains no submitted secret or protected-file content;
- symlink/untrusted config/credential targets fail closed;
- failed validation leaves config bytes unchanged;
- atomic write + readback preserves a valid config;
- credential replacement verifies staging before active promotion and retains the prior known-good
  value until commit/readback succeeds; credential forget detaches config/lease before exact deletion;
- Canvas Forget removes its exact authorization lease with the credential; Drive/Notion Forget names
  the exact role/store and preserves academic/source content;
- valid Google service-account JSON up to `GOOGLE_CREDENTIAL_PATH_MAX_BYTES` can use the protected
  credential path while oversize input fails before write;
- Google worker/MCP credential separation is preserved;
- Canvas origin/account/registry mismatch fails before enrollment/use;
- Canvas `CANVAS_PAT` uses only the fixed code-owned keyring role/locator;
- revoked/unreachable Canvas credentials can still be forgotten through exact local-profile confirmation;
- lease renewal never sends the existing PAT to the browser and never changes scope silently;
- Canvas week/module metadata cannot create Session number/date/completion;
- disabled/unaccepted Canvas ingestion cannot be activated from the GUI;
- settings routes are absent from Remote MCP;
- human-owned academic approval fields are absent from the settings mutation surface;
- restart-required is derived from desired-vs-applied service fingerprints, survives Settings
  close/relaunch, clears after a successful external/explicit restart onto the desired fingerprint,
  remains after failed restart, and handles multiple saves before restart; `Save` never silently
  restarts services;
- applied fingerprints originate only from successful process loads at the worker-lock or MCP
  serving boundary; stopped, previous-fingerprint, and unreported states remain distinct from
  readiness, and no state is inferred from Save, credentials, or object construction;
- Academic active semester and MCP retrieval scope have separate values/diffs; legacy_global remains
  usable without Academic selection, while semester_workspace requires only its own read-side proof;
- manual provider IDs can be saved as Configured/Unverified but cannot be consumed as verified
  bindings; worker/study readiness follows enabled-feature dependencies and explicit user choices;
- GUI-only interval edits reject non-integer/out-of-range changes while toggle-only saves preserve
  legacy positive YAML interval bytes; scheduler cadence and next run remain Not reported without
  verified scheduler readback;
- first-run step 1 cannot request course-specific mappings before Canvas course selection exists;
- first-run durable completion predicates are deterministic: disabled optional Remote Access can be
  skipped without enabling it, worker credentials block only enabled write/intake features, and a
  transient live-provider failure does not erase durable step completion;
- local OAuth-grant reset is reachable only through its dedicated confirmed/audited endpoint.

Add UI-flow tests for first-run success, invalid credential, unavailable provider, partially configured
connection, replacement confirmation, pause-vs-forget semantics, stale form generation with dirty
input preserved, unfinished transaction recovery, stepper back-navigation with dependent steps marked
`Partial` and values retained, structured `SESSION_EXPIRED` handling, session timeout/close,
restart-required state, and keyboard/error accessibility. Use fakes for ordinary tests; live provider
smoke tests are separate and opt-in.

Run focused pytest, contract tests, Ruff, targeted mypy, Behavior Contract drift checks, and
`git diff --check`. Final implementation requires both independent web ChatGPT and Gemini high reviews,
with targeted rereview for security/UI-flow fixes.

## 14. PLAN acceptance questions for independent reviewers

Reviewers should specifically judge:

1. whether a loopback browser UI can be made sufficiently resistant to CSRF/DNS-rebinding/local-port
   attacks without exposing it through Remote MCP;
2. whether typed config generation + atomic writes avoid corrupting/manual-overwriting `config.yaml`;
3. whether credential upload/enrollment preserves the existing worker-vs-MCP separation and never
   returns secrets to JavaScript;
4. whether the generic Canvas profile/lease model removes KNU/semester hard-coding without weakening
   provider identity;
5. whether the settings taxonomy exposes all meaningful user settings while keeping frozen/internal
   invariants read-only;
6. whether disconnect/reset flows communicate preservation vs deletion clearly;
7. whether the implementation bundles are small enough to review independently.
8. whether bootstrap/session lifecycle and GET-vs-mutation Origin rules are implementable without
   either locking out a normal browser launch or weakening CSRF/DNS-rebinding resistance;
9. whether multi-store partial failures can be repaired without guessing secret rollback state;
10. whether the concrete interaction mock keeps first-run prerequisites, credential deletion, and
    restart/partial-state communication understandable for a nondeveloper.
