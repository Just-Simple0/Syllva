# Syllva Local Settings Web GUI — implementation plan

Date: 2026-09-27. Status: **PLAN revision — Gemini high findings integrated; web ChatGPT review still pending**.

## Task record

- Risk: high. This work joins credential enrollment, provider identity, configuration mutation,
  remote-access settings, and a new user-facing flow.
- Technical worker assignment: `gpt-5.6-luna` / high, selected for the cross-component Python,
  local-security, and configuration-state-machine design. Astra/root retains plan acceptance.
- Required plan reviews: independent web ChatGPT review through `insane-review`; independent
  `gemini-3.8-flash-high` UI-flow review.
- Gemini plan review (2026-09-27): **REVISE**. This revision integrates its ten required findings.
- Web ChatGPT review has not been accepted: `insane-review` v0.6.8 currently fails closed before
  prompt submission because the current ChatGPT UI exposes no model/effort pill matching its Pro
  verifier. Do not substitute an unverified default model or call this plan accepted until that
  gate is recovered and rerun.
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
- provider/source identity is verified before it becomes a durable academic binding.
- the settings server is never exposed through Remote MCP or the Cloudflare Tunnel.

The first version is not a generic YAML editor, an arbitrary secret manager, a Notion schema
provisioner, or a Cloudflare-account administration console.

## 3. Launch and local security boundary

Add an explicit launcher such as `uls setup` (desktop shortcut can call the same entry point).
It starts an **on-demand** settings process, opens the default browser, and stops when the setup
session ends or idles out. It is a different app/process/port from Remote MCP.

Security requirements:

1. Bind only to loopback. Prefer `127.0.0.1` on an OS-assigned random port; never `0.0.0.0`.
2. Generate a high-entropy, memory-only one-time `bootstrap_token` for each launch. It is valid for
   at most 30 seconds and only for the exact process/port. The launcher opens exactly
   `http://127.0.0.1:<port>/?bootstrap=<token>`. The first valid top-level GET consumes the token,
   invalidates it immediately, sets an `HttpOnly; SameSite=Strict` session cookie, and returns a
   `303` redirect to `/` so the capability is removed from the address bar and browser history.
   Refreshing, replaying, or guessing a bootstrap URL after consumption must fail closed.
3. Validate `Host` on every request as the exact generated `127.0.0.1:<port>` value; reject
   `localhost`, alternate loopback names, forwarded-host headers, and external hosts. Top-level GET
   navigation does not require an `Origin` header because browsers may omit it. State-changing API
   requests require exact `Origin: http://127.0.0.1:<port>` plus the session cookie and CSRF header;
   `Referer` may be used only as a fail-closed origin fallback when `Origin` is absent. Disable CORS.
4. Unsafe requests require the local session plus a per-session CSRF token in a custom header. GET
   never changes state. Initial/top-level document navigations must be same-site documents and must
   never accept iframe/frame embedding.
5. Serve all HTML/CSS/JS locally. No CDN, analytics, external fonts, service worker, or remote
   script execution.
6. Use restrictive CSP, `frame-ancestors 'none'`, `Cache-Control: no-store`, no-referrer policy,
   and same-origin isolation headers where applicable.
7. Store no credential or reusable setup capability in `localStorage`, IndexedDB, telemetry, or
   browser-visible API responses after submission. The one-time bootstrap token is the sole narrow
   exception: it may appear only in the launch URL until the first consuming request and immediate
   `303` redirect described above; it must never be persisted or returned afterward.
8. Password/secret inputs are write-only. Existing values render only as `Configured` /
   `Not configured` / `Error`; the backend never returns them.
9. Apply an inactivity expiry and explicit `Close settings` action. The frontend warns before expiry
   when possible, then replaces the app with a clear `Settings session expired` screen rather than
   leaving the user with a generic connection error. `Close settings` invalidates the session and
   gracefully stops the settings process after returning an ended-session page. Session capabilities
   die with the process.
10. Protect the config write path with a local settings-process lock so two GUI sessions cannot
    concurrently mutate the same configuration.

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

The Academic page joins already-verified provider identities; it does not infer them from names.

- active semester;
- Canvas course selection and its verified provider IDs;
- Syllva course keys/names/codes/sections;
- Drive course-folder binding;
- Notion course portal/data-source binding;
- per-course readiness status.

Module position, `1주차` labels, file names, and LMS titles never auto-create Session No, lecture
date, completion state, or content approval.

### 5.7 Automation

Expose user-meaningful controls:

- intake worker enabled/disabled;
- poll interval within validated bounds;
- study-note feature enabled/disabled;
- Canvas scheduled sync only after its separate ingestion bundle is accepted;
- last run, next run, current lock/busy state, and actionable failure summary when available.

Turning a feature on must not silently run a live provider mutation during the settings POST.
Activation is saved first; the UI then presents the explicit start/restart action and readiness
requirements.

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
   secret readiness transitions (`Not configured -> Configured`) and restart impact. Never render
   submitted secret values, protected-file contents, or provider payloads in the diff.
6. Write through a same-directory owner-only temporary regular file, fsync, atomic replace, and
   directory fsync. Refuse symlink/untrusted ownership targets.
7. Read the file back and validate again before reporting `Saved`.

Do not accept arbitrary JSON-pointer/YAML-path writes from the browser.

### 6.2 Secrets

Credential endpoints accept one supported credential role, never an arbitrary service/account or
path. Reuse code-owned credential allowlists/bindings and OS-native keyring checks.

- responses never contain the submitted or stored value;
- replace is a separate user action and verifies the new value before reporting success;
- deleting/forgetting a credential is separate from disabling the feature;
- failed config mutation after a successful safe credential write may leave an unused credential,
  but must not roll back by deleting an unknown prior secret. Report the exact partial state.

Credential actions that also update `config.yaml` return the **new** `config_generation` after
readback. The frontend updates its active generation without clearing dirty form inputs so a valid
credential enrollment does not make the user's still-open non-secret form immediately stale.

### 6.3 Multi-store transaction journal

Any user action spanning config plus keyring/protected-file/connector-manifest stores uses a small,
secret-free local transaction journal. The journal records an operation ID, action kind, exact fixed
credential/profile role, original config generation, candidate config hash, intended non-secret
binding, phase, and readback result. It never stores credential values or uploaded JSON.

The backend follows `prepare -> credential/binding write -> config apply -> readback -> complete` and
records each completed phase durably. If a later phase fails, the operation remains `Partial` with a
specific repair action. It must not guess whether a previous secret can be deleted or restored and
must never silently overwrite a concurrent config edit. Relaunching Settings detects unfinished
journal entries and offers `Resume repair` or `Leave as-is`; no timeout auto-repair is allowed.

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

## 7. HTTP/API surface

Use purpose-specific, versioned local routes rather than a generic admin API. Representative shape:

```text
GET  /api/v1/overview
GET  /api/v1/settings/<group>
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
and may never mutate state. Every state-changing route requires session + exact `Origin` (or strict
same-origin `Referer` fallback if `Origin` is absent) + custom-header CSRF + generation/candidate
binding where applicable. Provider live tests happen only after an explicit click and remain
read-only unless the action clearly says it is a worker/write-capability test.

`POST /api/v1/session/close` invalidates the in-memory session before returning an ended-session
document and schedules graceful shutdown. `POST /api/v1/remote-mcp/oauth-grants/reset` is a separate
high-impact endpoint with exact local SQLite database identity, explicit confirmation payload,
candidate/readback validation, and an audit event; it is not reachable through generic settings
apply or a GET request.

## 8. Disable, forget, and destructive actions

Settings changes must distinguish disabling a feature from removing a secret or local state.

- **Disable Canvas**: stops future collection; imported Drive/Notion/source history remains.
- **Forget Canvas token**: explicit confirmation, removes only the exact profile credential after
  the locally stored profile identity + exact code-owned keyring locator are rechecked; imported
  academic data remains. Live provider success is not required, and a revoked token or unavailable
  provider must not block forgetting the local credential.
- **Disable Remote MCP**: config only; does not claim to delete a Cloudflare tunnel or Google OAuth client.
- **Reset local OAuth grants**: separate high-impact operation with exact local database identity,
  candidate/readback, confirmation, and audit; clears only local authorization codes/tokens in the
  identified OAuth database and is not part of ordinary `Save`.
- Drive/Notion credential replacement never deletes academic content.

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

Course/semester bindings, worker/study-note toggles, restart-required state, readiness dependency graph.

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
- top-level same-host GET navigation succeeds without `Origin`, while mutations require exact
  same-origin `Origin`/fallback `Referer` plus CSRF;
- session close invalidates access and idle expiry produces a deliberate ended/expired flow;
- CSRF is required for every mutation;
- secrets never appear in GET responses, error JSON, logs, HTML, local browser storage, or config YAML;
- no external asset requests are emitted by the UI;
- concurrent config edit/generation mismatch fails closed;
- unrelated/unknown valid config keys survive GUI edits byte-semantically or round-trip semantically;
- credential-driven config writes return the new generation and do not clear dirty non-secret form state;
- multi-store journal recovery exposes `Partial` and never auto-deletes/rolls back an unknown prior secret;
- redacted diff output contains no submitted secret or protected-file content;
- symlink/untrusted config/credential targets fail closed;
- failed validation leaves config bytes unchanged;
- atomic write + readback preserves a valid config;
- credential replace/forget applies only to the exact code-owned binding;
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
- restart-required behavior is explicit and `Save` does not silently restart services;
- first-run step 1 cannot request course-specific mappings before Canvas course selection exists;
- local OAuth-grant reset is reachable only through its dedicated confirmed/audited endpoint.

Add UI-flow tests for first-run success, invalid credential, unavailable provider, partially configured
connection, replacement confirmation, pause-vs-forget semantics, stale form generation with dirty
input preserved, unfinished transaction recovery, session timeout/close, restart-required state, and
keyboard/error accessibility. Use fakes for ordinary tests; live provider smoke tests are separate
and opt-in.

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
