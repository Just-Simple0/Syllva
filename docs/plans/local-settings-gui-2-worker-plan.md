# Syllva Local Settings GUI-2 worker plan

> 현재 적용 안내 (2026-10-04): 아래 배정·리뷰·단계·권한 문구는 작성 당시의 역사 기록입니다. 새 작업은 프로젝트 `AGENTS.md`가 참조하는 현행 전역 정책과 `handoff.md`의 최신 재개 기준을 따릅니다. 과거 모델/강도·quota 예외·commit/push 허용을 새 작업으로 승계하지 않습니다. 원래 검토된 bytes와 해시는 당시 Git snapshot/리뷰 패키지의 근거로 유지하며, 이 안내를 추가한 현재 파일을 그 원본과 동일하다고 주장하지 않습니다.

Date: 2026-09-30

Status: **Plan accepted 2026-10-01 (web Pro r4 GO; Gemini ultra GO). Implementation and self-tests complete;
independent final reviews will run in a separate chat.**

Parent: [`local-settings-web-gui.md`](local-settings-web-gui.md) §5.3, §5.4, §6.2–§6.4, §7, §8, §12 GUI-2;
[interaction mock](local-settings-web-gui-interaction-mock.md) §4–§5; accepted
[GUI-1 worker plan](local-settings-gui-1-worker-plan.md) (commit `2f39f19`).

## Assignment and boundary

- Worker: `gpt-6-luna` / **max**, same continuous worker as GUI-1.
- Technical fit: secret-store transactions over the sealed GUI-1 journal, bounded upload handling,
  and read-only provider verification.
- GUI-2 owns credential controls for four fixed roles (Notion retrieval, Notion worker, Google Drive
  retrieval, Google Drive worker), their connection cards, explicit read-only provider tests,
  purpose separation, and credential-write `config_generation` refresh.
- GUI-2 reuses the reviewed credential stack. It does not add credential names, sources, keyring
  services, or file names to `ALLOWED_SOURCES`, `KEYRING_BINDINGS`, or `FILE_BINDINGS`. The runtime
  `CredentialResolver` never reads GUI-2 staging or backup slots; for Google paths this is enforced by
  one narrow resolver edit (R5, below).
- **Development and all automated tests use fake keyrings, temp secret directories, and fake provider
  transports only.** No real keyring entry, real protected file in the user's secrets directory, or real
  Notion/Google call is made. Any real-provider smoke test needs a separate, explicit user approval
  naming the provider, role, and credential to be used.
- No commit or push until final reviews and orchestrator acceptance.

## Platform scope

GUI-1 runs on macOS and Linux and refuses Windows. The existing keyring backend
(`_keyring_backend.explicit_os_keyring`) and protected-secret directory (`_secure_file.secrets_directory`)
support only macOS and Windows. Therefore:

- **macOS:** full GUI-2 (enroll, replace, forget, test).
- **Linux:** credential cards are read-only with the plain state `Not available on this computer:
  Syllva cannot store credentials securely here yet. Use environment variables instead.` Test actions
  still work for credentials already supplied through their declared source (environment or an
  external Google file path), because the resolver supports those on Linux.
- **Windows:** unchanged (GUI-1 refuses to start).

A Linux secret store (Secret Service) is out of scope and would need its own reviewed plan.

## Fixed credential roles

One code-owned table (`credential_roles.py`) is the only source of role identity. The browser sends
only the role slug in the route; it never supplies a name, source, service, account, or path.

| Role slug | Credential name | Journal binding (provider/profile/role) | Store | Active slot | Staging slot | Backup slot | Size limit | Consumed by |
|---|---|---|---|---|---|---|---|---|
| `notion-mcp` | `NOTION_MCP_TOKEN` | `notion/default/mcp` | keyring | `Syllva MCP` / `notion_mcp_token` (existing) | `…/notion_mcp_token.staging` | `…/notion_mcp_token.backup` | 4 KiB | MCP server at start |
| `notion-worker` | `NOTION_WORKER_TOKEN` | `notion/default/worker` | protected file | `notion_worker_token.secret` (existing) | `notion_worker_token.secret.staging` | `notion_worker_token.secret.backup` | 4 KiB | intake worker at start |
| `google-mcp` | `GOOGLE_MCP_CREDENTIALS_FILE` | `google/default/mcp` | protected file | `google_mcp_service_account.json` | `….json.staging` | `….json.backup` | 64 KiB | MCP server at start |
| `google-worker` | `GOOGLE_WORKER_CREDENTIALS_FILE` | `google/default/worker` | protected file | `google_worker_service_account.json` | `….json.staging` | `….json.backup` | 64 KiB | intake worker at start |

All protected files live in the existing `secrets_directory()`. The two Google active files are new
Syllva-owned names; the config field `google_mcp_credentials_path` / `google_worker_credentials_path`
points to them after enrollment (the declared source stays `environment` with a path override, as today).
GitHub and LLM keyring roles (§5.5) are deferred; see "Deferred".

Each role's config change is fixed:

- `notion-mcp`: `credentials.NOTION_MCP_TOKEN: keyring`.
- `notion-worker`: `credentials.NOTION_WORKER_TOKEN: file`.
- `google-*`: `google_<purpose>_credentials_path: <absolute active slot path>`. It also clears the
  legacy nested `google_drive.<purpose>_credentials_path`, so only one binding can select a file.
- **Every** enroll, replace, forget and detach also writes `credential_revisions.<role>: <revision>`.
  The revision is a fresh random 128-bit hex value per operation, never derived from the secret (R2).
  Every operation, including forget and detach, sets a new value; the entry is never removed. The
  config candidate therefore always changes, and a services fingerprint always sees the change.

Forget and detach are defined against **effective resolution**, not only one key (R3; see
"Effective resolution").

## Concrete files and ownership

New GUI-2 files:

```text
src/uls/settings/credential_roles.py     fixed role table, slot locators, limits, config patch per role
src/uls/settings/credential_stores.py    keyring/protected-file slot adapters, state IDs, StoreResolver
src/uls/settings/credential_service.py   enroll/replace/forget/detach over the journal; recovery actions
src/uls/settings/provider_checks.py      bounded read-only Notion/Google checks with fixed error codes
tests/contract/test_settings_credential_roles.py
tests/contract/test_settings_credential_stores.py
tests/contract/test_settings_credential_service.py
tests/contract/test_settings_credential_http.py
tests/contract/test_settings_provider_checks.py
```

Owned compatibility edits:

```text
src/uls/config/credentials.py      reject reserved Google staging/backup locators at path resolution (R5)
src/uls/config/schema.py, loader.py  typed credential_revisions: {role_slug: hex} section (R2)
src/uls/config/_secure_file.py     write_secure_file(..., max_bytes=MAX_SECRET_BYTES) keyword (default
                                   unchanged); delete_secure_file() with the same dir/owner/no-follow checks
src/uls/config/_keyring_backend.py write_keyring_credential() (set + exact readback) and
                                   delete_keyring_credential(), shared by the CLI and GUI-2
src/uls/settings/credential_admission.py   role lock + pending-operation admission shared by GUI and CLI (R4)
src/uls/cli/credential_set.py      shared writers, run inside credential_admission (R4)
src/uls/settings/journal.py        StoreResolver-derived observers (closes the deferred GUI-1 item);
                                   canonical slot-locator validation; credential_detach action kind;
                                   per-action schema versions (R6)
src/uls/settings/security.py       per-route body limits (default 16 KiB; secret 8 KiB; Google 64 KiB + 1 KiB)
src/uls/settings/app.py            credential and connection routes
src/uls/settings/status.py         credential readiness rows from diagnose() metadata only
src/uls/settings/static/{index.html,app.js,styles.css}   connection cards
tests/contract/settings_ui_harness.cjs, tests/contract/test_settings_ui.py   new scenarios
tests/contract/test_settings_journal.py, test_settings_credential_journal.py   resolver API update
```

Reused without change: `credentials.py`'s allowlists (`ALLOWED_SOURCES`, `KEYRING_BINDINGS`, `FILE_BINDINGS`,
`GOOGLE_CREDENTIAL_PATH_MAX_BYTES`) and `CredentialResolver.diagnose()` apart from the R5 check,
`runtime.require_mcp_credentials`
and `google_service(read_only=True)`, the Notion client construction in `runtime.py`, `read_secure_file`,
`secrets_directory`, and the GUI-1 config lock/CAS primitives.

## Secret state identity

Journal records need pre/post state IDs for every store, but the parent plan forbids storing values or
deriving a user-visible revision from them. GUI-2 uses:

- `state_id(slot) = "absent"` when the slot is empty, otherwise
  `"h:" + HMAC-SHA256(local_state_key, "state" || role_slug || value_bytes)` (R1). The ID is
  **slot-invariant within a role**: the same value has the same ID in its active, staging, and backup
  slots. This lets the sealed links hold (stage post-state = promote's intended state; active
  pre-state = backup's intended state; restore = backup). It differs across roles, so a record for one
  role cannot be satisfied by another role's slot.
- `local_state_key` is 32 random bytes in a new protected file `settings_state.key` in
  `secrets_directory()`, created once through `write_secure_file` and read through `read_secure_file`.
  Without it the IDs cannot be tested against guessed values offline.
- Config state IDs stay the GUI-1 raw-byte SHA-256 generations.

State IDs never leave the backend, and are **never used for purpose separation** (see "Verification").
Routes return only readiness metadata. The credential **revision** for GUI-4's desired/applied
fingerprint is the config's `credential_revisions.<role>` value (random, not value-derived).

## Transaction machines (sealed GUI-1 journal; credential kinds at schema v4)

**2026-10-02 corrective proposal:** the provider purpose-pair admission and preparse response bounds
in `local-settings-gui-23-review-corrections.md` qualify the admission/verification sections below.
This proposal awaits the recorded human scope decision and native corrective PLAN gate; it is not
an implemented or accepted change. Frozen documents remain authoritative. Historical different-role
concurrency wording below applies only to independent provider domains after the correction.

Every mutation runs as:

```text
role lock (canonical, from binding) -> journal record lock -> config lock
```

The role and record locks are held for the whole operation. The config lock is taken at the points the
machine requires it and always for alternate-branch effects. Each effect runs through
`JournalOperation.run_effect` with observers supplied by the code-owned `StoreResolver` (below). The
journal already enforces effect order, record-linked config generations, branch proofs, guard rereads,
and the operation-lock requirement.

**Choosing the machine.** The service reads the active slot's state under the role lock:

- `absent` → **enrollment**. The active slot must be empty, so an unknown prior secret is never
  overwritten.
- occupied → **replacement**, even when config does not reference the slot yet (for example an orphan
  left by an earlier failed forget). The backup preserves the unknown value, so restore stays possible.

**Enrollment** (`credential_enrollment`): `credential_stage` (write staging slot, readback) →
provider verification (not a store effect; see "Verification") → `config_commit` (role's config patch,
exact G0 → G1 CAS) → `credential_promote` (copy staged bytes to active slot, readback) → delete the
staging slot → `complete`. If verification fails or the provider is unreachable, the service takes the
`abandon` branch (`staged_delete`) in the same operation; the active slot and config never change.

**Replacement** (`credential_replacement`): `credential_stage` → verification → `credential_backup`
(copy current active bytes to backup slot) → `credential_promote` → `config_commit` → `backup_delete`
→ `complete`. Verification failure before backup takes a cleanup-only path: the staging slot is deleted
and the record resolves `resolved_without_change` (the machine's primary branch has only its verified
stage, so GUI-2 adds a staging cleanup step described under "Journal additions"). A crash after promote
with config still at G0 recovers through the sealed `restore` branch. When config already matches the
candidate, recovery continues with `backup_delete`.

**Forget** (`credential_forget`): `config_detach` (remove the role's config key, exact G0 → G1) →
`credential_delete` (active slot to `absent`, readback). A delete failure leaves the mock's
`Configuration detached. Old local credential is still present.` state with `[Retry local deletion]`.

**Detach** (`credential_detach`, new kind): only for a **config-declared external Google file path**
(top-level or legacy nested) that Syllva does not own. It runs `config_detach` only, and the external
file is never read, modified, or deleted. The UI labels this `Stop using this credential`.
Environment-supplied credentials have no detach action (see "Effective resolution").

**Config effect.** The config effect writes the role's fixed patch through the GUI-1 config primitive
(`read_config_bytes` → candidate → `atomic_replace_config` → readback) under the shared config lock, with
unknown keys preserved. `candidate_hash` is computed and validated with the existing typed loader before
the record is created, exactly like `ConfigStore.apply`.

**Replacement always changes config (R2).** The candidate always carries a new
`credential_revisions.<role>`, so a replacement whose source or path is unchanged still has
`G1 != G0`. The distinct-generation check stays, and recovery keeps an unambiguous commit decision:
config at G0 means not committed (restore branch); config at G1 means committed (continue with
`backup_delete`); anything else is manual review. The random revision also makes a reused G1 from an
earlier operation impossible.

### Effective resolution (R3)

Before `config_detach` is admitted, and again under the config lock before any credential deletion, the
service computes the role's **effective source** from the candidate config plus the Settings process
environment. It uses the same rules as `CredentialResolver`: declared source with the environment
default, and for Google the top-level path, then the legacy nested path, then the environment variable.

- **Notion forget.** Removing the declaration falls back to `environment`. The managed slot is then no
  longer referenced, so deletion may proceed. If the environment variable is set, the card says
  `Notion will now use NOTION_MCP_TOKEN from the environment.`
- **Google forget.** The candidate clears both the top-level and the legacy nested key. Deletion is
  refused (`CREDENTIAL_STILL_SELECTED`) while **any** effective binding of **either** Google role resolves
  (by realpath) to the file being deleted, including an environment path.
- **Stop using (detach)** is offered only for **config-declared external Google paths** (top-level or
  nested). It is admitted only if the candidate leaves no effective path for that role. Otherwise it
  fails with `DETACH_INEFFECTIVE`, naming the environment variable to remove.
- **Environment-sourced credentials get no Stop using action.** The card shows `Provided by
  NOTION_MCP_TOKEN. Remove it from the environment to stop using it.` Syllva cannot durably suppress an
  environment source without a new resolver state, which is out of scope.
- **Limitation.** The check uses the Settings process environment. Services started with a different
  environment are outside what Settings can prove, and the card says so in one line.

### Shared admission with the CLI (R4)

Credential slots are per user (one Keychain and one `secrets_directory()`), while journals are per
workspace. Admission therefore lives in a **per-user credential namespace that does not depend on the
workspace**:

- **Location.** `secrets_directory()/admission/`, mode `0700`, with the same owner/no-follow checks as
  the secrets folder.
- **Key.** One lock and one reservation per **physical credential binding**:
  `sha256(store_kind || canonical locator)` of the active slot. The keyring key is service plus
  account; the file key is the realpath in the secrets folder. Staging and backup slots share their
  active slot's key.
- **Lock.** `binding-<key>.lock` (`LocalFileLock`). It is acquired without blocking; a busy lock fails
  with `credential_busy`.
- **Reservation.** `binding-<key>.reservation` is a small secret-free JSON record holding the
  operation ID, action kind, role slug, workspace journal directory (realpath), config path, and
  creation time. It is created exclusively (`O_EXCL`, 0600) **before** the workspace journal record and
  removed only after that record reaches a terminal phase. A reservation therefore outlives process
  exit as long as its record is unresolved.

`credential_admission(role)` (`src/uls/settings/credential_admission.py`) works the same for every
caller:

1. Acquire the binding lock.
2. If a reservation exists, open the journal it names. When that record is unresolved or unreadable,
   or the journal can't be found, refuse with `OPERATION_IN_PROGRESS` and name the workspace. When the
   record is terminal, remove the stale reservation.
3. Still holding the lock, also refuse when the caller's own workspace journal holds an unresolved
   record for the role.

The callers:

- **GUI** operations create the reservation and hold the lock for the whole machine. The fixed order is
  binding lock → role lock → record lock → config lock.
- **CLI** `uls credential set` holds the lock from before it reads the existing state, through the
  overwrite decision, the prompt, the write, and the readback. It never creates a reservation, because
  its write is a single effect done under the lock.
- **Recovery** uses a separate entry point, `credential_recovery_admission(operation_id)` (R4a).
  Holding the binding lock, it admits the call only when all of these hold:
  - the reservation exists, is complete, and names exactly this operation ID;
  - the record's binding derives the same physical binding key;
  - the reservation's workspace journal directory and config path equal the recovering caller's, by
    realpath and the config directory identity from GUI-1.

  It is the **only** path that may proceed past an unresolved record, and only for that exact record. It
  keeps the binding lock through role lock → record lock → config lock until the effect's readback is
  recorded. Every other caller (a new GUI operation, CLI, or recovery of a different record) is still
  refused with `OPERATION_IN_PROGRESS`.

**Reservation durability (R4b).** Publication order is fixed:

1. On first use, create `admission/` (0700, verified) and fsync both it and its parent.
2. Write the reservation to an exclusive temp file in `admission/` with `schema`, all fields, and a
   `complete: true` marker, then fsync the file.
3. Create the reservation with `link(temp, reservation)` (atomic, and it fails if one exists), unlink
   the temp, and fsync `admission/`.
4. Only then create the workspace journal record and run any effect.

Release order is also fixed: the record first becomes durably terminal (GUI-1 record fsync), then the
reservation is unlinked and `admission/` fsynced. A crash in between leaves a reservation whose record
is terminal, which step 2 of admission clears.

Admission fails closed (`OPERATION_IN_PROGRESS`, `[Inspect]`) on uncertain evidence. That covers a
reservation that is unreadable, partial, lacks `complete`, has an unknown schema, fails owner or mode
checks, or names a missing journal. It also covers a leftover reservation temp file. Such evidence is
cleared only by an explicit Settings repair that shows the named workspace and record; no timeout or
guess clears it.

A different workspace or config path can no longer write the same physical slot while another
operation holds it or leaves it unfinished.

### Reserved Google locators (R5)

One shared, code-owned predicate, `is_reserved_secret_locator(path)` in `_secure_file.py`, defines
every non-active name in the secrets folder. It returns true when the path's realpath parent is
`secrets_directory()` (or its `admission/` subfolder) and any of these holds:

- the name ends in `.staging` or `.backup`;
- the name starts with `.tmp_`. This covers `write_secure_file`'s actual `.tmp_<target>_<pid>`
  temporaries, including those of staging and backup targets.
- the name ends in `.lock` or `.reservation`.

Its users:

- **The resolver.** One narrow edit to `CredentialResolver._diagnose_one` for
  `GOOGLE_CREDENTIAL_PATH_NAMES` calls the predicate after choosing a path from a path override or the
  environment and before `read_secure_file`. A match is refused with `google_credential_reserved_path`.
- **GUI-2 writers** assert that their temporary names satisfy it. A test pins
  `write_secure_file`'s temp-name format to the predicate so a future rename cannot escape it.

The allowlists stay unchanged, and active slots and user-managed paths elsewhere are unaffected.

### Journal additions (owned edits to `journal.py`)

1. **Code-owned observers.** `switch_branch` and `run_effect` take a `StoreResolver` instead of
   caller-built `observe`/`guards` mappings. The journal derives each observer from the record's own
   binding: `config` → config generation of `binding.config_path`; `active`/`staged`/`backup` → state IDs
   of `binding.store_locator`/`staging_locator`/`backup_locator`. Tests supply a fake resolver
   implementing the same interface.
2. **Canonical locators.** For credential kinds, `validate_record` requires `store_locator`,
   `staging_locator`, `backup_locator` to equal the role table's derivation from
   `(provider, profile, role)`, so a record can never target another slot.
3. **Staging cleanup on the primary branch.** Enrollment and replacement gain a final
   `staged_delete`/`staging_cleanup` effect with a fixed `absent` intended state, so `complete` means no
   staged secret is left behind. Replacement also gains a pre-backup `reject` branch
   (`credential_stage` → `staging_cleanup`) admitted with branch proof "config original, active at recorded
   pre-state, backup absent".
4. **`credential_detach`** action kind: effects `("config_detach",)`, same record links.

**Per-action schema versions (R6).** The global `SCHEMA_VERSION` becomes an allowlist of
`(action_kind, schema_version)` pairs:

- `config_apply` stays at **v3**, unchanged, so live GUI-1 history (completed and pending) still reads,
  validates, recovers, and is never reported as unreadable;
- the credential kinds and `credential_detach` are **v4**, and new records use v4;
- v3 credential records were never created outside tests and are refused.

A regression keeps a GUI-1-produced completed record and a pending v3 `config_apply` record readable
and recoverable after the upgrade, and credential admission is not blocked by them.

## Verification (stage + verify)

Structural checks run locally before anything is staged:

- **Notion token:** UTF-8, 1–4096 bytes, no whitespace or control characters.
- **Google service account:** at most 64 KiB, parses as a JSON object with
  `type == "service_account"`, the fields `client_email`, `private_key`, `private_key_id`, `project_id`,
  and `token_uri` present, and `token_uri` on Google's host. It must load with
  `google.oauth2.service_account.Credentials.from_service_account_info` without any network call.

Purpose separation is checked before staging, and again during verification:

- **Notion (R1).** The new token is compared **in backend memory** (constant-time) with the peer
  purpose's **effective** value. The peer is resolved once through `CredentialResolver.diagnose()` with
  its declared source, including an environment-declared peer. Role-scoped state IDs are never used
  here, because they differ across roles by design.
- **Google.** The new `client_email` and `private_key_id` must differ from the peer's effective payload,
  resolved the same way (managed slot, external path, or environment path). The existing
  path-inequality check still holds.
- The peer value lives only for the comparison and is never logged or persisted.
- Otherwise the change fails with `CREDENTIAL_PURPOSE_CONFLICT` (`Retrieval and worker must use
  different credentials.`).

The live check runs once per enroll or replace, only because the user pressed `Verify and save`. It
uses the staged value, never the active one, and the same read-only calls as the connection test below.

| Result | Machine outcome |
|---|---|
| `VERIFIED` | continue |
| `INVALID_CREDENTIAL`, `PERMISSION_MISSING` | reject path; nothing active changes |
| `PROVIDER_UNAVAILABLE`, `TIMEOUT` | reject path; the UI says the provider could not be reached and nothing changed |

## Read-only provider checks

`provider_checks.py` exposes one function per `(provider, purpose)` over an injectable transport. It
uses fakes in all tests.

**Notion (both purposes).** `GET /v1/users/me`, then for each configured database ID (Courses,
Sessions, Materials, File Intake, Input Request) `GET /v1/databases/{id}` or the data-source equivalent
for the pinned `notion_version`. The worker purpose additionally reports whether each database is
reachable. It infers write permission only from returned metadata and never tries a write.

**Google Drive.** Built from `google_service(payload, read_only=True)` for both purposes: the worker
test uses the worker credential but still requests the read-only scope. It calls
`files.get(fileId=<root/inbox/upload folder>, fields="id,trashed,capabilities(canAddChildren,canListChildren)")`
and reports `canAddChildren` as `Worker can add files: yes/no` without creating anything.

Bounds and safety:

- **Method allowlist.** GET or `files.get` only; the transport wrapper refuses any other method or
  endpoint (`CHECK_NOT_READ_ONLY`).
- **Time and volume limits.** 10 s per request, 30 s per check, at most 8 requests per check, and one
  check in flight per role. A 5 s cooldown per role per session limits repeated checks.
- **Runs off the event loop.** Checks run in a worker thread under the GUI-1 mutation barrier, so
  replacement and shutdown still work.
- **Fixed result codes.** `VERIFIED`, `INVALID_CREDENTIAL` (401), `PERMISSION_MISSING` (403 or a resource
  not shared), `NOT_FOUND` (configured ID missing), `PROVIDER_UNAVAILABLE` (5xx or network), `TIMEOUT`,
  `NOT_CONFIGURED`.
- **No provider data in responses.** A response carries the code, a plain message, the check time, and
  per-resource `reachable: yes/no` keyed by our own labels ("Courses database"). It never includes
  provider response bodies, exception text, token or key material, service-account JSON, or the
  `client_email`, which is shown only masked (`syl…@…iam.gserviceaccount.com`).
- **Logging.** Only route template, role slug, result code, and duration. The provider client libraries
  run with their loggers at WARNING and a redaction filter; a test asserts no secret sentinel appears in
  captured logs.
- **Nothing persisted.** Test results are kept in process memory only and shown as `last check 19:20`.

## HTTP routes

All routes sit under the GUI-1 per-launch prefix and inherit its boundary: exact Host, the session
cookie, Fetch-Metadata checks, exact Origin (or the strict Referer fallback), the `X-ULS-CSRF` header on
every POST, explicit-activity renewal, and the mutation barrier. The role slug is one of the four
fixed values; anything else returns `404`.

```text
GET  api/v1/credentials                        card metadata for all four roles
POST api/v1/credentials/{role}/set             enroll (or replace when the active slot is occupied)
POST api/v1/credentials/{role}/replace         replace; refused unless Syllva manages the active slot
POST api/v1/credentials/{role}/forget          body {generation, confirm_role}
POST api/v1/credentials/{role}/detach          body {generation, confirm_role}; external sources only
POST api/v1/connections/{provider}/{purpose}/test   read-only check of the current credential
POST api/v1/recovery/{operation_id}/{action}   extended: resume | leave | restore | retry_delete
```

- **Secret bodies.**
  - **Notion:** `application/json` `{"generation": "…", "secret": "…"}`, 8 KiB limit.
  - **Google:** `application/json` whose body is exactly the service-account file bytes, with the
    generation in the `X-ULS-Generation` header; 65 KiB limit, so the file limit is 64 KiB.
  - **Enforcement.** Limits are checked before parsing. Duplicate JSON keys are rejected, and any key
    other than the listed ones fails with `INVALID_REQUEST`.
- **Confirmation.** `confirm_role` must equal the role slug the UI displayed in the confirmation dialog.
- **Card metadata.** `GET credentials` returns, per role:
  - `state`: `not_configured` | `configured` | `external` | `unsupported_platform` | `error` | `partial`
  - `source`: `keyring` | `file` | `environment` | `external_file`
  - `managed`: whether Syllva owns the slot
  - `last_check`: in-memory result code and time
  - `pending_operation`: ID and repair action
  - `takes_effect`: `MCP restart` or `worker restart`

  It never includes values, state IDs, file contents, or the paths of external files.
- **Response.** A mutation returns the terminal status, the fixed result code, and the **new
  `config_generation`** after readback. That generation comes from the config effect when one ran,
  otherwise from the current generation.
- **Errors.** Fixed codes, plain messages, and field association (`{field: "secret", code, message}`)
  reusing the GUI-1 structured-error format. They never echo the submitted value.

**config_generation refresh (§6.2).** On success the frontend:

1. replaces its stored generation for every group with the returned one;
2. keeps all dirty non-secret inputs;
3. drops any open reviewed candidate for every group, because its candidate hash was built on the
   previous generation and would now fail as `CONFIGURATION_CHANGED`;
4. announces `Credential saved. Review any open changes again.` only if a review was dropped.

Credential writes touch only the `credentials` section and the two Google path keys, none of which is
editable in any GUI-1 group, so a user's pending General or Advanced edits are never overwritten.

## UI states (per interaction mock §4–§5)

Each provider card shows its retrieval and worker rows separately. Row states:

- **`Not configured`**: `[Configure]`.
- **`Configured`** (Syllva-managed): `[Test]`, `[Replace]`, `[Forget…]`.
- **`Provided outside Settings`**: an environment variable or an external Google file. It offers
  `[Test]` and `[Use a Syllva-managed credential instead]` (enrollment). `[Stop using this credential…]`
  (detach) appears only for a config-declared external Google file. For an environment variable the row
  shows `Provided by NOTION_MCP_TOKEN. Remove it from the environment to stop using it.`
- **`Checking…`**: the row's buttons are disabled, `aria-busy` is set, and a polite announcement is made.
- **Check result**:
  - `Verified 19:20`
  - `Invalid credential — the provider rejected it.`
  - `Access missing — share the Courses database with this integration.`
  - `Provider unavailable — nothing changed; try again later.`
- **`Failed` replacement** (mock §4): `Replacement credential was rejected. Current credential is still
  active. The submitted secret was cleared.` with `[Retry replacement]` and `[Test current credential]`.
- **`Partial`** (mock §5): the recovery card from the journal, with the machine-specific action.
- **`Not available on this computer`** (Linux): no mutation controls.

Secret inputs are write-only:

- **Notion field:** `type=password`, `autocomplete=new-password`, `data-secret`, `spellcheck=false`.
- **Google:** a file input (`accept=application/json`); the file is read into an `ArrayBuffer` only at
  submit and sent as the request body.
- **Clearing:** the field and file input are cleared, and references dropped, immediately after every
  submit (success or failure), on `Cancel`, and when the session ends through the GUI-1 `endSession` path.
- **Never stored or shown:** secret values are not kept in `state`, not echoed back into the DOM, not
  included in the draft list, and not placed in any URL or storage.
- **Dirty-state rule:** secret fields never count as dirty non-secret input, so they never trigger the
  `beforeunload` guard or appear in ended-session drafts.

**Forget confirmation** (`alertdialog`, focus trapped) names the exact role and store, for example
`Notion retrieval token in this Mac's Keychain`, and states what is preserved: `Notion pages, databases,
course bindings and imported history stay.` Cancel returns focus to `[Forget…]`.

The mock needs small clarifications for these GUI-2-only states (environment/external source, Linux,
provider unavailable during verification, and the Google file upload). They are added to mock §4.

## Tests and acceptance evidence

### Implementation guidance from the Gemini ultra plan review (not defects)

- **Detach has its own dialog.** `Stop using this credential…` opens a dedicated `alertdialog`, separate
  from Forget. It says that only the Settings entry pointing to the credential is removed, and that the
  environment variable or user-managed key file is not read, changed, or deleted. Its confirm button
  reads `Stop using`, never `Forget` or `Delete`.
- **Linux cards name the exact environment variable** (`NOTION_MCP_TOKEN`, `NOTION_WORKER_TOKEN`,
  `GOOGLE_MCP_CREDENTIALS_FILE`, `GOOGLE_WORKER_CREDENTIALS_FILE`). `[Test]` is disabled with the reason
  `Set NOTION_MCP_TOKEN, then reopen Settings` when the declared source reports it unset.
- **Focus flow:**
  - `[Configure]` and `[Replace]` move focus to the secret field.
  - `[Cancel]` clears the field and returns focus to the button that opened it.
  - A failed verification focuses the row heading and announces the result.
  - `[Retry replacement]` and `[Try again]` focus the empty secret field.
  - After success, focus moves to the row's status text.
- **The Google file input is `data-secret`**, so the GUI-1 `endSession` path clears it. The page checks
  size on the client before upload (over 64 KiB → inline field error `Key file is larger than 64 KB`,
  nothing sent). The server limit remains authoritative.

All tests use a fake keyring backend (injected through the store factory, never by patching
`sys.platform`), a temp secrets directory owned by the test user, and fake provider transports.

**Stores** (`test_settings_credential_stores.py`):

- write, read, and delete for keyring and file slots, with readback;
- a 64 KiB Google limit (65 536 accepted, 65 537 refused before write) and a 4 KiB Notion limit;
- symlinked, foreign-owned, or group-readable directories and files refused;
- state IDs are slot-invariant within a role (staging = active = backup for the same value), different
  across roles, never equal to a plain hash of the value, and `absent` when empty;
- an enrollment promote and a replacement backup/restore pass the sealed links with real IDs (R1);
- the state key created 0600 once;
- CLI `credential set` works through the shared writers inside `credential_admission`. It fails with
  `credential_busy` while a GUI operation holds the binding, and with `OPERATION_IN_PROGRESS` while an
  unresolved record reserves it. In an interleaving where the CLI waits at its prompt while a GUI forget
  starts, the GUI is refused, so a new CLI value is never deleted (R4).
- **Two workspaces (R4).** Workspace A runs a GUI replacement of `notion-mcp`. While it is active, a
  GUI operation and a CLI write from workspace B (different config and journal) are refused. After A's
  process is killed mid-operation, B (new operation, CLI, and recovery of a fake record ID) is still
  refused. A's own Settings then **actually resumes or restores** A's operation through
  `credential_recovery_admission`, to its terminal state. B is refused throughout and proceeds
  afterwards. Recovery with a wrong operation ID, a different binding, or another workspace's identity
  is refused (R4a).
- **Publication boundaries (R4b).** Crash injection:
  - **During the reservation write**, before link: no reservation exists, and the temp makes admission
    fail closed until repaired.
  - **After link, before the directory fsync**: the reservation is either fully present or absent.
  - **After the reservation, before the record**: a reservation names a missing record. Admission fails
    closed and is never silently cleared.
  - **After the record turns terminal, before unlink**: the next admission clears the stale reservation.
  - **Corrupt, partial, or foreign-owned reservations** fail closed.
  - **Ordering:** a mocked effect asserts the reservation and admission-directory fsync happen before
    journal record creation.
- **Crash-left temp (R5).** A crash-left `.tmp_google_worker_service_account.json.staging_<pid>` in the
  secrets folder, supplied through both the environment variable and a path override, diagnoses as
  `google_credential_reserved_path` and is never read.

**Roles** (`test_settings_credential_roles.py`):

- the table matches `ALLOWED_SOURCES`, `KEYRING_BINDINGS`, `FILE_BINDINGS`, and `GOOGLE_CREDENTIAL_PATH_NAMES`;
- staging and backup slots are never readable by `CredentialResolver`: a `.staging`/`.backup` Google
  path supplied through the environment or a path override diagnoses as
  `google_credential_reserved_path` (R5);
- canonical locator validation rejects a record pointing at another role's slot.

**Service with crash injection** (`test_settings_credential_service.py`):

- **Every effect boundary** (`before_`, `after_`, `after_*_recorded`) for enrollment, replacement,
  forget, and detach. A fresh process recovers using only the record and the `StoreResolver`, and each
  point ends in exactly one of:
  - completed;
  - the sealed restore/abandon/reject branch;
  - `manual_review` with every store untouched.
- **Enrollment never activates a credential before the config CAS.**
- **A rejected replacement keeps the old active credential** byte-for-byte and leaves staging absent.
- **Replacement after promote with config G0** restores the exact backup.
- **A forget delete failure** leaves the detached state and `retry_delete` works.
- **Concurrency and drift:**
  - two concurrent operations on one role → `OPERATION_IN_PROGRESS`; different roles proceed;
  - an orphaned occupied active slot routes to replacement.
- **Purpose separation:** a Notion duplicate token and a Google identical `client_email` are refused
  before staging.
- **config_generation:** the new generation is returned after readback, and unknown config keys are
  preserved.

**Provider checks** (`test_settings_provider_checks.py`):

- **Result mapping:** each fake response (200, 401, 403, 404, 500, connection reset, slow response past
  the timeout) maps to its fixed code.
- **Read-only:** the transport records only GET/`files.get` calls; a write attempt raises
  `CHECK_NOT_READ_ONLY`.
- **Bounds:** the per-check request cap and the one-check-in-flight and cooldown limits are enforced.
- **Redaction:** a secret sentinel in the provider's error body or exception text never appears in the
  response, the logs, or the journal.
- **Separation:** the worker Drive check uses the read-only scope.

**HTTP** (`test_settings_credential_http.py`):

- every route requires session, Origin, CSRF, prefix, and Fetch-Metadata;
- an unknown role returns 404;
- body limits apply per route (Notion 8 KiB, Google 65 KiB, others 16 KiB), and duplicate or extra JSON
  keys are refused;
- `confirm_role` must match;
- responses and logs never contain the submitted value or any state ID;
- `GET credentials` metadata is redacted;
- Linux returns `unsupported_platform` and refuses mutations;
- the new generation is returned.

**UI harness:**

- secret fields are cleared after success, failure, cancel, and session end;
- secrets are never in drafts or `beforeunload`;
- a failed replacement shows the mock text and keeps non-secret fields;
- a generation refresh keeps dirty inputs and drops open reviews;
- the forget dialog focus trap and preserved-content text;
- test busy state and announcements;
- the Linux read-only card;
- no secret in any URL or `fetch` except the one POST body.

**Regression:** the full suite, `pytest -m contract`, `ruff` on changed files, the mypy delta, and
behavior-contract drift.

**Review-driven regressions:**

- **R1:** Notion separation is refused when the peer is environment-declared with the same value.
- **R2:** a second replacement with an unchanged source/path still gets `G1 != G0` through the revision,
  and recovers unambiguously at every boundary.
- **R3:**
  - Google forget is refused while a legacy nested path or an environment path still selects the file;
  - Notion forget with an environment token reports the environment fallback;
  - detach of an environment credential is not offered, and detach that leaves an effective path fails
    with `DETACH_INEFFECTIVE`.
- **R6:** v3 `config_apply` records (completed and pending) remain readable and recoverable alongside v4
  credential records.

A real-browser smoke runs against fakes only. The launcher gets a test-only fake store factory through a
`ULS_SETTINGS_FAKE_STORES` developer flag, which is refused unless the tests or `--no-browser` are in use.
In fake mode every page shows a persistent banner: `Test mode — fake credential stores. Nothing is saved
to this computer's Keychain or secrets folder.`

The screenshot recipe always uses temporary roots for everything Settings touches: a temp `config.yaml`
whose `workspace_dir` (and therefore the journal) is under the temp root, a temp
`ULS_SETTINGS_RUNTIME_DIR`, and the fake secrets directory. That isolation, not `--no-browser` alone, is
what proves the real stores are untouched.

## Deferred

- **GUI-3:** Canvas profile/origin/account identity, the `CANVAS_PAT` keyring binding, PAT
  enroll/replace/forget and revoked-token recovery, the authorization lease, and term/course discovery.
  GUI-2 builds the role table and store layer so Canvas adds one role row plus its provider check.
- **GUI-4:** desired/applied fingerprints and restart state. GUI-2 only shows per-role `Takes effect when
  MCP / the worker next starts` and records the revision in `credential_revisions`. Drive folder
  and Notion database ID selection or discovery (§5.3/§5.4 non-secret settings) also moves to GUI-4 with
  academic bindings; GUI-2 tests against the IDs already in config.
- **GUI-5:** Remote MCP secrets (`REMOTE_MCP_SECRET`, `REMOTE_MCP_GOOGLE_CLIENT_SECRET`).
- **Later:** GitHub/LLM keyring roles (§5.5), a Linux Secret Service store, and Windows.

## Material choices and risks for independent review

1. **Role-scoped, slot-invariant HMAC state IDs** keyed by a local random key. Journals stay
   secret-free, the sealed copy links hold, and state can be re-derived after a crash. Losing the key
   turns every pending record into `manual_review`. Purpose separation uses in-memory comparison, not
   IDs.
2. **Occupied active slot always uses replacement.** This avoids ever overwriting an unknown secret, at
   the cost of a backup copy for orphaned slots.
3. **Staging cleanup and a pre-backup reject branch** are added to the credential schemas (v4), so
   `complete` and rejection both mean no secret copy is left behind. `config_apply` stays v3.
4. **Effective-resolution detach and forget.** Syllva never deletes or edits files it does not own,
   never deletes a Google file any effective binding still selects, and offers no "stop using" for
   environment credentials. The check sees only the Settings process environment.
4a. **Every credential operation writes `credential_revisions`.** Replacement always changes config,
    and GUI-4 reuses the revision as the fingerprint input.
4b. **The CLI takes part in GUI admission.** `uls credential set` can be refused while a GUI operation
    or an unresolved record holds the role.
5. **Live verification is required before activation.** A provider outage blocks enrolling or replacing
   a credential; there is no "save anyway". This makes onboarding depend on provider availability.
6. **Linux cannot store credentials** until a reviewed Secret Service store exists; the cards are
   read-only there.
7. **Worker capability comes from Drive metadata** (`canAddChildren`) rather than a write probe. It can
   miss permission changes at write time, but it never creates provider content.
8. **`google_*_credentials_path` may already point to a user-managed file.** GUI-2 treats that as external
   and never takes it over silently.
9. **Test-only fake store flag** for browser evidence without touching the real Keychain.

Official references to confirm during implementation (docs-guide):

- Notion API `users/me` and database retrieve for the pinned version;
- Google Drive `files.get` `capabilities` fields;
- `google-auth` `from_service_account_info`;
- the `keyring` macOS backend `delete_password`.
