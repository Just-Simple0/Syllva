# Syllva Local Settings GUI-3 worker plan

Date: 2026-09-30

Status: **Plan accepted 2026-10-01 (web Pro GO; Gemini GO (actual high; ultra gate pending)). Integration
and self-tests complete; independent final reviews will run in a separate chat.**

Parent: [`local-settings-web-gui.md`](local-settings-web-gui.md) §5.2, §6, §7, §8, §12 GUI-3;
[interaction mock](local-settings-web-gui-interaction-mock.md) §2 and §4–§5;
[GUI-2 worker plan](local-settings-gui-2-worker-plan.md); constraints from
[`knu-lms-api-semester.md`](knu-lms-api-semester.md).

## Assignment and boundary

- Worker: `gpt-6-luna` / **max**, the same continuous worker as GUI-1 and GUI-2.
- GUI-3 owns:
  - one Canvas connection profile (exact origin and verified account);
  - the `CANVAS_PAT` credential and its enroll, replace, and forget actions;
  - the Pause/Disable Sync toggle;
  - the renewable authorization lease;
  - bounded read-only term and course discovery;
  - the exact user-selected course registry.
- **No LMS content is read beyond course and term metadata.** There is no file, module, assignment,
  announcement, download, Drive archive, or intake work. That stays with the separate Canvas SOURCE
  bundle.
- Platforms: macOS stores credentials. On Linux the card is read-only, as in GUI-2. Windows is deferred
  (the user's decision) and GUI-1 still refuses to start there.
- Development and all tests use a fake keyring and a fake Canvas transport only. Any real Canvas call,
  including a KNU smoke test, needs separate explicit user approval naming the origin and account.
- No commit or push until final reviews and orchestrator acceptance.

## Dependencies on GUI-2 (must land first)

GUI-3 adds no new transaction machinery. It reuses these GUI-2 pieces unchanged:

- the role table (`credential_roles.py`), extended with one **profile-parametric** role;
- the slot adapters and HMAC state IDs (`credential_stores.py`);
- `StoreResolver` observers derived from the binding;
- `credential_service.py` enroll/replace/forget over the sealed journal (credential schemas v4: staging cleanup,
  reject branch, and canonical locator validation);
- the provider-check framework (transport allowlist, fixed result codes, bounds, and redaction);
- the credential routes' auth, body limits, and `config_generation` refresh semantics;
- the UI card patterns: write-only fields, cleared-on-submit, and a failed replacement keeping the old
  credential.

If GUI-2 review changes any of these interfaces, this plan follows them.

**Alignment with the GUI-2 review fixes:**

- **R1:** Canvas state IDs are role-scoped and slot-invariant (`HMAC(key, "state" || "canvas-pat:" +
  profile_id || value)`). The role is per profile, so another profile's slots can never satisfy a record.
- **R2:** every Canvas enroll, replace, and forget writes a new `credential_revisions."canvas:<profile_id>"`.
  A token replacement always changes config (`G1 != G0`), and recovery keeps GUI-2's unambiguous commit
  decision. **Lease-only renewal does not rotate the revision**, so it never triggers restart-required.
- **R4:** GUI-2's per-user binding admission (lock plus reservation keyed by the keyring service and
  `profile:<id>` account) covers every Canvas mutation, from any workspace.
- **R3:** `CANVAS_PAT` is keyring-only with no environment source and no legacy alias. After the
  profile is removed, the only effective binding is gone, so forget's delete is admitted once
  `canvas.profile` is absent from the candidate config. There is no Stop using action.
- **R4:** `uls credential set CANVAS_PAT` is refused (`credential_settings_only`, "Use Settings to
  connect Canvas"), because the keyring account depends on a verified profile. Any later CLI support
  must use `credential_admission`.
- **R5:** not applicable; there is no path-based Canvas source.
- **R6:** Canvas records use the v4 credential schemas; `config_apply` history stays v3.

## Identity and storage

**Origin.** The user enters a Canvas base URL, normalized to `https://host[:port]`.

- Refused: any scheme other than `https`, userinfo, a path other than `/`, a query, a fragment, IP
  literals, and single-label hosts.
- Hosts are lowercased and IDNA-encoded, and the default port is dropped.
- The fake transport can use a test origin; production code has no loopback exception.

**Profile.** `profile_id = "c" + sha256(origin + "\n" + canvas_user_id)[:32]` (hex). It is secret-free,
immutable, and visible to the browser. Changing the origin or account creates a new profile; an existing
secret is never rebound. GUI-3 supports **one active profile**. A second profile requires forgetting
the first, which keeps the UI and the readiness predicates simple.

**Credential.** `CANVAS_PAT` is added to `ALLOWED_SOURCES` with `{"keyring"}` only. It uses keyring
service `Syllva Canvas` and account `profile:<profile_id>`, plus staging `profile:<id>:staging` and backup
`profile:<id>:backup`. The account is derived in code from the profile ID and never read from YAML.

- `credentials.py` gets `canvas_keyring_locator(profile_id)` rather than a static
  `KEYRING_BINDINGS` entry.
- No runtime path resolves `CANVAS_PAT` in GUI-3, because there is no collector yet. It stays out of
  every generic `diagnose()` name set. If it is ever requested without a profile, the resolver reports
  the fixed `canvas_profile_required` diagnostic and never guesses an account.
- Journal binding: provider `canvas`, profile `<profile_id>`, role `pat`, giving the canonical role lock
  `canvas/<profile_id>/pat`.
- The GUI-2 locator validator derives the keyring locators from these three values.

**Config (non-secret, one new `canvas:` section).** The typed loader gains the section; unknown keys
are still preserved.

```yaml
canvas:
  profile:            # absent until enrollment's config_commit
    id: c1f...        # derived profile_id
    origin: https://canvas.example.edu
    user_id: "12345"  # verified Canvas user ID (string)
    display_name: "Student N."   # display only; never an identity
    credential: keyring
  sync_enabled: false
  registry:           # exact user selection; empty until saved
    term_id: "678"
    courses:
      - {course_id: "41921", term_id: "678", name: "Database Systems", code: "DB101"}
  lease:              # secret-free authorization lease
    issued_at: "2026-09-30T10:00:00Z"
    expires_at: "2026-10-30T10:00:00Z"
    binding_hash: <sha256 of profile id, origin, user_id, registry hash, resource policy>
```

The lease lives in config so that forgetting the profile removes it in the same config CAS as the
detach. Renewal is an ordinary config CAS write. GUI-4 must exclude the `lease` and display fields
from restart fingerprints.

## Actions and machines

All mutations hold the locks in this order: canonical role lock → journal record lock → config lock.
The journal machines, crash recovery, and the `OPERATION_IN_PROGRESS` rules are GUI-2's.

**Connect (enrollment).** The steps run in this order:

1. The user submits the origin and a PAT (one POST, 8 KiB body limit).
2. **Pre-verify, in memory only.** `GET {origin}/api/v1/users/self` with the submitted token proves the
   exact origin and account. This fixes `profile_id`. A 401 returns `INVALID_CREDENTIAL`; an outage
   returns `PROVIDER_UNAVAILABLE`. Nothing is stored and the field is cleared.
3. If another profile is already configured, the request fails with `PROFILE_EXISTS`.
4. Enrollment then runs:
   - `credential_stage` (keyring staging slot, readback);
   - re-verify with the staged value: `users/self` must return the same user ID;
   - `config_commit` writes `canvas.profile` with an empty registry and no lease, as an exact G0 → G1;
   - `credential_promote`;
   - `staging_cleanup` → `complete`.
5. If verification fails after staging, the `abandon` branch deletes the staged copy. **No step activates
   a token before the config CAS**, per GUI-2.

**Replace token.** It uses GUI-2's replacement machine. The new token must verify through `users/self`
as the **same** `user_id` at the same origin; a different account fails with `PROFILE_MISMATCH` (the
user must forget and reconnect). Replacement works when the current token is revoked or expired: only
the new token is checked, never the active one. On failure the old token is kept, which matches the
mock's failed-replacement state. The `config_commit` in replacement leaves the `canvas` config
unchanged except for the new `credential_revisions."canvas:<profile_id>"` value (GUI-2 R2), so the
record's G0 → G1 link still holds.

**Forget Canvas token** (`credential_forget`):

- `config_detach` removes `canvas.profile`, `canvas.registry`, and `canvas.lease` and sets
  `sync_enabled: false`, as one config CAS.
- `credential_delete` then removes the `profile:<id>` keyring entry (readback: absent).
- **No provider call is made.** Authorization comes only from the local profile record and the
  canonical keyring locator. A revoked token or an unreachable host cannot block it.
- If the delete fails, the mock's `Configuration detached. Old local credential is still present.`
  state appears, with `[Retry local deletion]`.
- Imported academic and source history is untouched (GUI-3 imports nothing).
- The confirmation dialog names the origin, the masked account, and "this Mac's Keychain", and states
  that the lease is removed and history is kept.

**Pause / Disable Canvas sync.** This is a GUI-1 config-group field (`canvas.sync_enabled`), separate
from the credential.

- Disabling is always allowed and never touches the token or the lease.
- **Enabling returns `FEATURE_DEFERRED`** until the Canvas SOURCE collector bundle is accepted, per
  parent §5.2. The card shows `Canvas sync is not available yet` and a disabled toggle.

**Renew access.** This is a config CAS that rewrites `canvas.lease`. Before any keyring access or
network call, it checks that the profile and registry are present and unchanged and computes
`binding_hash`.

- It then checks that the PAT is present in the keyring (existence only; the value is never returned).
- It performs one `users/self` call that must match `user_id`.
- The new lease runs at most 30 days from now.
- The token is never re-entered or shown.
- A 401 returns `Token rejected — replace the token`, and the lease is left as it was.
- Any registry, origin, or resource-policy change makes `binding_hash` stale, so the lease is invalid
  until renewed.

**Lease states.** `Active until <date>`, `Expires in N days` (7 days or fewer), `Expired`,
`Needs renewal — course selection changed`, and `Not issued`. The lease gates only the future
collector. In GUI-3 it is status only and never blocks settings actions.
It also does not affect setup Step 2: Canvas is `Ready` once a verified profile and a saved course
registry exist. An expired or missing lease shows as an operational badge (`Expired` or `Expires in N
days` in the warning style, with `[Renew access]` as the primary button) and does not turn the step back
to Partial.

## Read-only discovery and course registry

Discovery runs only after an explicit `Find my courses` click, using the stored PAT inside the backend.

- `GET /api/v1/users/self`
- `GET /api/v1/courses?enrollment_state=active&include[]=term&per_page=50`: at most 4 pages and 200
  courses. The term list is derived from each course's `term`, because the account terms endpoint needs
  admin rights.
- **Save selection** first re-reads every selected course with `GET /api/v1/courses/{id}?include[]=term`
  (at most 20 selected courses). The saved registry uses only re-read IDs and the term ID; names and
  codes are display-only. All selected courses must share the chosen term.

Bounds and safety, on top of GUI-2's check framework:

- **Outbound-destination contract (SSRF).** One function in `canvas_checks.py`,
  `open_canvas_connection(origin)`, is the only way to reach Canvas. Verify, replace, test, renew,
  discovery, selection re-reads, and pagination all go through it. It:
  1. resolves the host's A and AAAA records once;
  2. refuses if **any** returned address is loopback, private (RFC 1918 and ULA), link-local,
     unspecified, multicast, carrier-grade NAT, reserved, or otherwise not globally routable
     (`ipaddress.is_global` is false), or if IPv4-mapped IPv6 points into those ranges. A mixed public
     and forbidden answer is refused as a whole;
  3. connects to one validated address directly, with no re-resolution, so a DNS change between
     validation and connect cannot redirect the request;
  4. keeps normal TLS: SNI and certificate hostname verification use the origin host, not the IP.

  A refusal (`DESTINATION_NOT_ALLOWED`) happens **before** any request that carries the PAT. The
  user-supplied origin is validated this way before the first `users/self` call during connect.
- **GET only** to the profile's exact origin; any other method or host fails with `CHECK_NOT_READ_ONLY`.
- **No redirects are followed.** A 3xx is reported as `PROVIDER_UNAVAILABLE`. The `Authorization` header
  can never be sent to another origin.
- **Pagination** follows `Link: rel="next"` only when its origin equals the profile origin exactly
  and its normalized path is exactly `/api/v1/courses`. The query must contain exactly one
  `enrollment_state=active`, one `include[]=term`, and one `per_page=50`; its only optional key is
  one positive ASCII-decimal `page` bounded at 10000. Duplicate, missing, unknown, or conflicting
  parameters fail closed before any PAT-bearing request. Encoded path aliases and non-list endpoints
  are refused. `/api/v1/users/self` and `/api/v1/courses/{validated-id}` remain code-owned call sites
  and cannot be reached through a provider next link. Existing page/course/request/time caps and
  pinned exact-origin TLS checks apply unchanged. A provider using pagination outside this bounded
  contract receives a fixed safe failure; the client does not widen its scope to accommodate it.
- **Timeouts:** 10 s per request and 45 s per discovery. Response bodies are capped at 1 MiB each.
- **One discovery in flight per profile**, with a 10 s cooldown.
- **429** maps to `RATE_LIMITED` (no automatic retry).
- **Fixed codes:** `INVALID_CREDENTIAL`, `PERMISSION_MISSING`, `NOT_FOUND`, `PROVIDER_UNAVAILABLE`,
  `TIMEOUT`, `RATE_LIMITED`, `DESTINATION_NOT_ALLOWED`.
- **Responses carry only** course ID, term ID, term name, course name, and course code. They never
  include provider bodies, the token, or headers. Logs record the route template, result code, count,
  and duration.
- **Nothing discovered is persisted** except the saved selection. The discovered list lives in page
  memory for the selection step.

**Registry save** is a GUI-1 reviewed-candidate config apply of the `canvas.registry` group: validate
(re-read) → redacted diff → apply with CAS and readback. A changed registry marks the lease
`Needs renewal — course selection changed`. Only verified provider IDs become durable, and display names
never become identity, per mock §3. The registry does **not** create or modify `courses:`, Notion,
Drive, or Session data. Mapping selected Canvas courses to Syllva courses belongs to GUI-4.

## Migration boundary from the KNU sidecar

- `scripts/knu_lms_*` are not imported, called, modified, or activated. Their hourly driver, Keychain
  item, `knu-lms-semester-registry.v1` file, `api_code_verified` rows, and Aside transport stay with the
  KNU plan and are neither read nor deleted by Settings.
- GUI-3 does **not** automatically import the KNU registry or token. A KNU user connects through the
  generic Canvas card with the KNU origin, which creates a new profile, and selects courses again.
  Existing KNU artifacts remain for the user or a future reviewed migration to retire.
- Constraints carried over from the KNU plan: GET-only, no grades, submissions, attendance, or progress
  writes, no mark-read, no hidden LearningX or institution-private endpoints, and no browser or Aside
  fallback (a Canvas API failure stays a Canvas API failure). The token is not given a synthetic
  lifetime, and lease renewal is an explicit human action.
- Validating the lease and config before keyring or network access is kept as a rule for the future
  collector. GUI-3's renewal follows the same order.
- Product text says **Canvas LMS**. KNU is only the first live-tested instance, and no universal
  compatibility is claimed.

## Files and ownership

```text
new  src/uls/settings/canvas_profile.py      origin normalization, profile_id, lease, registry hash
new  src/uls/settings/canvas_checks.py       bounded GET-only Canvas transport + discovery
new  tests/contract/test_settings_canvas_profile.py
new  tests/contract/test_settings_canvas_checks.py
new  tests/contract/test_settings_canvas_service.py
new  tests/contract/test_settings_canvas_http.py
edit src/uls/config/credentials.py          CANVAS_PAT (keyring only) + canvas_keyring_locator()
edit src/uls/config/schema.py, loader.py    typed canvas: section (profile, sync_enabled, registry, lease)
edit src/uls/settings/credential_roles.py   profile-parametric canvas/pat role
edit src/uls/settings/credential_service.py same-account guard for replace; canvas config patches
edit src/uls/settings/config_service.py     canvas.sync_enabled and canvas.registry groups
edit src/uls/settings/status.py             Canvas step Ready = verified profile + saved course
                                            registry; the lease is shown as operational status only
edit src/uls/settings/app.py, static/*      Canvas card, discovery/selection, lease, routes
edit tests/contract/settings_ui_harness.cjs, test_settings_ui.py
```

## Routes

All routes use the GUI-1 prefix, session, Origin, CSRF, and Fetch-Metadata boundary, plus the GUI-2
body limits and generation refresh.

```text
GET  api/v1/canvas                     profile/lease/registry/sync metadata (no secrets)
POST api/v1/canvas/connect             {origin, secret, generation}           8 KiB
POST api/v1/canvas/replace             {secret, generation}                   8 KiB
POST api/v1/canvas/forget              {generation, confirm_profile_id}
POST api/v1/canvas/renew               {generation}
POST api/v1/canvas/discover            {}   -> term/course list (page memory only)
POST api/v1/connections/canvas/test    {}   -> users/self result code
POST api/v1/settings/canvas_registry/validate|apply   (GUI-1 reviewed-candidate flow)
POST api/v1/settings/canvas_sync/validate|apply      (disable only; enable -> FEATURE_DEFERRED)
```

## UI states (mock §2, §4, §5)

- **Initial:** `Not checked` with a URL field and `[Test & connect]`.
  - **Origin check first.** `[Test & connect]` validates the URL format on the client (https, host, no
    path, query, or userinfo) before opening anything. An invalid URL sets `aria-invalid` and an inline
    `Error: Enter your Canvas address, for example https://canvas.example.edu`, focuses the field, and
    does not open the dialog. The server's normalization and destination contract stay authoritative.
  - **Token dialog.** It opens the write-only token dialog → `[Verify account]`, with in-dialog
    instructions: `In Canvas, open Account → Settings → Approved Integrations → New Access Token, then
    paste the token here.`, plus a link to `<origin>/profile/settings` that opens in a new tab
    (`rel="noopener noreferrer"`; shown only for a valid origin; no token or session data in the URL).
- **Ready:**
  - origin;
  - masked account (`Student N. · Canvas user 12345`);
  - access `configured · verified <time>`;
  - term selector and course checkboxes after `[Find my courses]`:
    - **Discovery.** It sets `aria-busy` on the course area and announces `Finding your courses…` and
      then `Found 7 courses in 2 terms` (or the fixed error) in a polite live region.
    - **Term filter.** The list shows only the chosen term's courses. Changing the term with courses
      selected warns `Changing the term clears your 3 selected courses.` and clears them on confirm.
      The warning is a DOM `dialog` with initial focus on Cancel, the shared focus trap, and Escape
      cancellation. Opening it does not change the selected term or courses. Cancel/Escape restores
      the previous selector value and focus to the term selector; only confirmation changes the term
      and clears the unsaved selection. A stale pending response cannot reopen or apply the warning.
    - **Selection limit.** A counter shows `3 of 20 selected`; at 20 the remaining checkboxes are disabled.
    - **Empty state.** A term with no courses shows `No active courses in this term.`
    - **Labels.** Each course shows its name and code (`Database Systems · DB101`).
  - `[Save selection]`, `[Replace token]`, `[Renew access]`, a disabled `Pause Canvas sync` switch
    with the `not available yet` note, and `[Forget Canvas token…]`.
  - Successful registry Apply re-reads the server snapshot and displays its saved term, course IDs,
    labels, checkboxes and count, including after reopening Settings. A page-local unsaved edit is
    kept separate from the saved registry; an unrelated snapshot refresh does not discard it. A
    successful Apply invalidates its consumed review and discovery identifiers and permits a new
    review only from a fresh discovery. No stale preview or older in-flight response can overwrite
    the latest saved selection. The card remains Ready independently of lease issuance.
  - Forget uses the backend's already masked account display and user ID, the verified origin, and
    the platform-owned storage label (`this Mac's Keychain` on macOS). Fake demo uses an explicit
    test-store label and must not imply a real Keychain write. The lease-removal/history-retention
    wording, Cancel default, confirmation profile identity and secret clearing remain mandatory.
- **Invalid token:** `Canvas rejected this access token. Existing saved Canvas connection was not
  changed.` The field is cleared.
- **Destination not allowed (connect):** the token dialog closes, the token is cleared, and focus moves
  to the Canvas URL field. The field gets `aria-invalid` and the inline message `Error: Syllva can
  connect only to Canvas addresses on the public internet. This address points to a private or local
  network.` Nothing is stored.
- **Destination not allowed (test, discover, renew):** the card shows an `Error` badge with `Canvas
  address is no longer reachable safely: it now points to a private or local network. Nothing
  changed.` and `[Retry check]`. The saved profile, course list, and lease are unchanged.
- **Provider outage after a completed connection:** `Failed — provider unavailable, last successful
  check <time>` with `[Retry check]`. The profile and registry stay; the wizard does not go back a step.
- **Replace with a different account:** `This token belongs to a different Canvas account. Forget this
  connection first, then connect the other account.`
- **Lease states** as listed above, with `[Renew access]`.
- **Dialogs** (token, Forget, and term-change warning): focus moves into the dialog on open, `Esc`
  cancels, and focus returns to the opening button on close. The token and term dialogs are `dialog`;
  Forget is an `alertdialog` whose default focused button is `Cancel`.
- **Linux:** `Not available on this computer: Syllva cannot store a Canvas token securely here yet.`
  with no Connect, Replace, or Forget controls.
- **Partial:** journal recovery cards from GUI-2 (resume, restore, or retry deletion).
- Secret fields follow GUI-2 exactly: write-only, cleared on every outcome, never in drafts,
  `beforeunload` checks, URLs, or storage.

## Tests (fakes only)

**Profile and identity:**

- origin normalization accept and refuse cases, including IDNA, userinfo, IP literals, and non-https;
- destination contract with a fake resolver and connector, all with **no PAT-bearing request made**:
  - a forbidden A record (127.0.0.1, 10.0.0.5, 169.254.169.254, 100.64.0.1) is refused;
  - a forbidden AAAA record (::1, fd00::1, fe80::1, ::ffff:127.0.0.1) is refused;
  - a mixed public and private answer is refused;
  - when DNS changes between validation and connect, the connection still goes to the validated
    address (the connector is asserted never to re-resolve);
  - the TLS hostname check uses the origin host;
  - every entry point (connect, replace, test, renew, discover, re-read, next page) uses the contract;
  - the UI maps `DESTINATION_NOT_ALLOWED` on connect to the closed dialog, cleared token, and focused
    `aria-invalid` URL field, and on test/discover/renew to the card error badge with `[Retry check]`;
- `profile_id` is deterministic and changes when the origin or user changes;
- the lease `binding_hash` changes on registry, origin, or resource-policy change;
- the keyring locator is derived from the profile ID and the journal's canonical locator check refuses
  another profile's slot.

**Service with crash injection:** enroll, replace, and forget crash at every effect boundary and recover
in a fresh process using only the record and `StoreResolver`. Also covered:

- no token is active before the config CAS;
- replace with a different account is refused before staging;
- replace with a revoked active token succeeds and a failed replace keeps the old token byte-for-byte;
- forget with the fake transport raising on every call still completes, and no provider call is attempted;
- a second profile fails with `PROFILE_EXISTS`;
- enabling sync fails with `FEATURE_DEFERRED` and disabling sync never touches the keyring or the lease.

**Discovery and checks:**

- GET-only enforcement and exact-origin pagination (a cross-origin `next` link is refused);
- same-origin `next` links to assignments, files, modules, users/self, another course, or another
  API are refused before transport dispatch; missing active/term/per-page filters, duplicate or
  unknown parameters and encoded path aliases are refused; the exact allowed next page succeeds;
- a 3xx is not followed and `Authorization` never reaches another host;
- the page, course, byte, and time caps, and 401/403/404/429/5xx/timeout mapping;
- a sentinel token never appears in responses, logs, or the journal;
- the selection re-read rejects IDs not returned by Canvas and a mixed-term selection.

**Renew:** config and lease validation happens before keyring or network access (asserted call order);
the token is never returned; a 401 leaves the lease unchanged; renewal leaves `credential_revisions`
unchanged.

**HTTP and UI:** the GUI-1/GUI-2 boundary suite applied to every Canvas route; the forget
`confirm_profile_id` match; the card states listed above; secret clearing; and the mock text for
invalid, outage, and different-account cases.
Term dialog confirmation, cancel and Escape must preserve or clear the right state and restore focus
without a native browser dialog. Registry Apply/reopen readback must display saved IDs and count;
fresh discovery and stale-response rejection remain enforced. Forget must show origin, the masked
account, and the truthful platform/test storage label. Verify these with the DOM harness and the fake
in-app browser, distinguishing screenshot display proof from keyboard and race interaction proof.

**Regression:** full pytest, ruff on changed files, the mypy delta, and behavior-contract drift.

## Deferred

- **Canvas SOURCE → private Drive → intake:** files, modules, and downloads, and enabling sync.
- **GUI-4:** mapping Canvas courses to Syllva courses, Notion, and Drive; restart fingerprints.
- **Multiple Canvas profiles.**
- **Migrating or retiring KNU sidecar artifacts.**
- **Linux secret storage and Windows.**

## Material choices and risks for review

1. **Pre-verification with the submitted token happens in memory before staging.** `profile_id` depends
   on the verified account, so this is necessary; the token never touches disk or the keyring until
   verified.
2. **The lease is stored in config.yaml.** Forget removes it atomically with the profile. The cost is
   that renewal changes the config generation, so GUI-4 must exclude it from fingerprints.
   Renewal never rotates the credential revision.
2a. **SSRF is handled by one destination contract.** Every outbound call resolves the host once, admits
    only globally routable addresses, connects to that exact address, and keeps hostname TLS
    verification. A split-horizon or internal Canvas host is unreachable by design.
3. **One profile at a time.** This is simpler to build and review, and a user switching accounts must
   forget first.
4. **The term list is derived from course terms** rather than the admin terms endpoint. A term with no
   active enrollment for this user is invisible.
5. **Sync cannot be enabled in GUI-3.** The toggle's Pause/Disable semantics are real, but enabling
   waits for the collector bundle.
6. **There is no KNU auto-migration.** Users reconnect once, and the old KNU Keychain item and registry
   remain until someone retires them deliberately.
7. **Institution variance.** A PAT may be disallowed or courses restricted. The card then reports fixed
   codes without institution-specific workarounds.

Official references to confirm during implementation (docs-guide): Canvas REST `users/self`, the
`courses` list with `include[]=term` and `Link` pagination, the single course `GET`, and rate-limit
behavior.
