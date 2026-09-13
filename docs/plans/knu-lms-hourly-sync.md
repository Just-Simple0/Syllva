# KNU LMS hourly sync sidecar — implementation record

Date: 2026-09-13  
Stage: R4 implementation complete; scheduler remains PAUSED pending separate credential enrollment and fresh authenticated acceptance  
Worker: Boyle  
Model/effort: gpt-5.6-luna / high — directly assigned fit for a bounded Python sidecar reusing the verified GET collector.

## Scope and ownership

The sidecar owns `scripts/knu_lms_sync.py`, `tests/unit/test_knu_lms_sync.py`, and this plan. It does not call Notion, SQL, launchd, cron, or live services directly. Root owns native connector schema/writes, receipts, `scripts/knu_lms_apply_lock.py`, and the Codex heartbeat. Existing `scripts/knu_lms_probe.py`, ULS core/MCP, frozen specifications, 2026-1 content, and existing course/PDF remain untouched.

The sidecar has four subcommands:

```text
collect   bounded Canvas collection through probe.run_probe(...)
snapshot  strict validation, canonicalization, stable identities, and hashing
project   deterministic native projection/change plan from a snapshot and root-supplied readback
enroll    separately authorized no-echo TTY Keychain enrollment; never run in this task
```

Planned callable seams are:

```python
collect(*, owner_id: str, scope_hash: str, now=None, opener=None, clock=time.monotonic) -> CanonicalSnapshot
validate_snapshot(raw: Mapping[str, Any], config: SyncConfig) -> CanonicalSnapshot
build_projection(snapshot: CanonicalSnapshot, prior: ProjectionState | None,
                 notion_readback: NativeReadback) -> ProjectionPlan
enroll_keychain(*, prompt, stdin, now=None, backend=None) -> dict[str, Any]
```

`project` emits JSON plus sanitized Markdown preview only. It has no Notion or SQL dependency and performs no cloud write.

## Canonical snapshot and projection

The selected resource set is the verified pilot course, assignments, bounded-window announcements, and complete modules/items. A snapshot is usable only when the probe reports `status=complete`, every selected resource is present, every item has an allowlisted shape, and stable source identities are unique. Incomplete, ambiguous, malformed, or duplicate data produces no projection and does not replace prior state.

Every identity is derived from the exact allowlisted origin, course ID, resource kind, and resource ID. Assignment row keys use the exact encoding `<origin>/courses/<positive-course-id>/assignments/<positive-assignment-id>`. Canonical JSON uses fixed key ordering, normalized text, deterministic resource ordering, and excludes fetched timestamps and other volatile values. Hashes are SHA-256 over that canonical form. Bodies, grades, submissions, download URLs, file content, and external-tool content are excluded. `due_at: null` remains null; the projection explicitly says LMS submission status is unknown and never infers unsubmitted or completion.

The native projection contains exactly one new 2026-2 schedule datasource under the 2026-2 root, with exactly three linked views: To DO, Calendar, and a course-filtered view. To DO and Calendar are linked views of that same datasource, never separate datasources and never mixed with 2026-1.

Datasource rows use exactly the root-approved schema: `이름`, `과목`, `유형`, `날짜`, `내 상태`, `내 메모`, `LMS 원문 URL`, `LMS 키`, and `수집 범위`. Source fields are updated only after exact-key and parent/course checks. `과목` comes from the verified exact-bound course page title when supplied by readback, while identity remains the stable source key. `수집 범위` is the fixed value `과제 메타데이터 · LMS 제출 상태 미수집`. `내 상태` initializes to `확인 전` only for a new row and is never changed thereafter; `내 메모` is always preserved. Null LMS deadlines remain empty, including a Notion DATE readback that omits `start` and `end`. Because the schema has no course-key property, the course view filters `LMS 키 STARTS WITH` the exact stable prefix `<origin>/courses/<verified-positive-course-id>/assignments/`; it never matches by title or display subject.

The one native course page projection contains `LMS 원문`, `과제`, `수집한 공지`, `주차별 자료`, `학습 세션`, and a separate USER `내 메모` block. Existing Course A and the new pilot Course B are different identities: root reuses exactly one existing exact-bound B page, and creates a page only when verified B is absent; a second B page is never created. `주차별 자료` renders only the 15 actual validated pilot modules. Every module must have a valid shape, a non-boolean integer position whose exact set is `1..15`, the exact name `<n>주차`, and complete validated items; duplicate/nonpositive positions, wrong names, or invalid shapes reject the projection. A validated actual module with no items still renders its toggle labeled `등록된 자료 없음 · 수집 시점 기준`; the prohibition is against fabricating a module or toggle from a missing position or count assumption. Module completion/state is omitted entirely from snapshots rendered for users and from change plans. `content_id` is retained only as opaque module-item metadata and is never treated as a file ID. For a validated module item, the only derived LMS launch URL is `https://canvas.knu.ac.kr/courses/<verified-positive-course-id>/modules/items/<validated-positive-item-id>`; an external `content_id` never becomes a URL or file identity. No assignment or week subpages are created.

All LMS-derived names, titles, labels, URLs, and other text are untrusted. The renderer applies explicit Notion/Markdown escaping, removes control/newline injection, and keeps untrusted text out of source markers and structural delimiters before emitting any preview. The SOURCE region uses the exact `수집한 공지` caption, KST posted-date prefix, week guidance text, line-separated `<details>`/`<summary>` blocks, and TAB-indented children required by the native renderer.

Source-owned blocks carry a versioned marker and two hashes: `last_applied_source_hash` is the normalized actual Notion readback, while `last_applied_desired_region_hash` is the normalized exact prior request. The driver first requires the current actual hash to match the accepted actual hash, preserving USER edits and conflict detection. It then returns `noop` when the desired hash matches the prior request hash or the current actual hash; otherwise it proposes a replacement. This handles Notion connector normalization such as removed blank lines, terminal newline differences, and escaped rich text without guessing equivalence or rewriting meaningful USER characters. Comments anchored in or attached to the region also force reconciliation. USER edits, comments, completion state, memo text, root headings, and child tags are preserved. For every cloud component, root applies the recovery protocol: durable identity/receipt and fresh readback classify `absent`, `exactly one verified`, or `ambiguous`; an indeterminate write is never retried by creation, and ambiguity causes no write. This covers the datasource, all three linked views, the exact-bound course page, and its SOURCE region. Conflicts, missing bindings, ambiguous candidates, unexpected parents, or privacy changes produce a reconciliation plan and no overwrite or deletion. Root may pass persisted receipt tombstones in `missing_bindings`; a missing previously bound assignment or course then remains a conflict and is never recreated.

## Credential preparation and hourly handoff

Before any Keychain read or network request, `collect` validates a secret-free `.review/knu-lms-hourly/config.json` containing the exact origin `https://canvas.knu.ac.kr`, the expected course name/code/term triple, selected resource scope, fixed local service/account labels, backend identifier, and timezone-aware `expires_at`. The config is preparation, not approval. Missing, expired, origin-mismatched, triple-mismatched, malformed, or out-of-scope config fails with fixed diagnostic codes. Defaults are fixed service `Syllva KNU LMS`, account `canvas.knu.ac.kr/course/<verified-positive-course-id>`, and an explicit expiry no more than 30 days after issuance; service/account are not CLI overrides and expiry is never silently extended.

The only reader is an explicit `keyring.backends.macOS.Keyring` instance on macOS. Because the installed class reads `KEYCHAIN_PATH` during initialization, the sidecar immediately sets `instance.keychain = None` and verifies that value before any credential operation, forcing the system default Keychain. It never uses `with_properties`, `from_env`, global keyring initialization, auto-selection, environment backend selection/properties, alternate backends, subprocess credential handoff, or fallback. The dedicated frozen test runtime prerequisite is `keyring==25.7.0` in `.review/venv311`; `pyproject.toml` remains unchanged. Token values exist only in process memory and never appear in argv, environment, files, logs, diagnostics, or review artifacts. Raw backend exceptions are caught without serializing or logging their text and converted to fixed diagnostics. The existing one-shot token is not inspected, stored, reused, or extended.

The dedicated runtime directory is `.review/knu-lms-hourly/` with mode `0700`; `config.json` and `auth-manifest.json` are secret-free regular files owned by the current user with exact mode `0600`, written through same-directory temporary files, `os.replace`, and parent-directory fsync, and refuse symlink targets. Failed temporary writes are retained for diagnosis. Both `read_enrolled_token` and `enroll_keychain` require timezone-aware `issued_at <= now < expires_at` before Keychain access, prompting, reservation, or manifest writes. `enroll` validates origin/course scope before prompting, reads the new token with a no-echo TTY prompt, and prepares the exact fixed service/account Keychain write through the explicit macOS backend only after separate user authorization. No Keychain read or write, including enrollment verification, occurs during this task. If a future Keychain write succeeds but manifest publication fails, the result is fixed `enrollment_incomplete`; there is no collect/auth fallback and no automatic rollback deletion. The heartbeat remains `PAUSED` until enrollment and a fresh authenticated complete run are separately authorized and accepted. `enroll` accepts no arbitrary backend, service, or account arguments.

The root-owned heartbeat may run `collect`, `snapshot`, and `project` for this pilot hourly. `collect` derives the KST current day and sends the reviewed probe a bounded 31-date request whose inclusive date arguments cover the current day through the next-day boundary (`start_date = KST day - 29 days`, `end_date = KST day + 1 day`); this is a current-day-covering boundary, not a claim of exactly the last 31 completed calendar days, and it never requests files. Announcement links are derived only as `https://canvas.knu.ac.kr/courses/<verified-positive-course-id>/discussion_topics/<validated-positive-announcement-id>` after validating positive integer IDs; raw LMS URLs are never accepted as input. `project` merges prior seen announcements by stable ID, updates seen IDs, and retains prior records that fall outside the current window with `last_seen` metadata; retained records are revalidated against the same origin/course/context and their routes are re-derived. Absence from the current window never deletes a record. The heading is exactly `수집한 공지` and does not imply a complete archive. A complete changed result emits the full canonical snapshot and projection plan. Unchanged results are quiet. Incomplete fetches, credential failures, identity mismatches, or manual conflicts retain prior state and block dependent writes.

Before any future cloud readback or mutation, the driver holds an OS lock at `.review/knu-lms-hourly/apply.lock` using `fcntl.flock(LOCK_EX | LOCK_NB)` for the complete readback → intent/journal → cloud operation → readback cycle. `hold-lock` retains the lock until stdin receives an explicit `release` or EOF; a competing runner returns fixed `run_busy` without cloud access. Root proves the holder is alive through a nonempty `status` write to the owning TTY session; missing/ended holder or failed lock proof blocks writes. Enrollment uses the same lock, and a future driver holds it for its entire cycle. Root owns this protocol and its receipts.

The root driver boundary is recorded in `docs/plans/knu-lms-hourly-driver.md`. It supplies fresh parent/schema/view/privacy/pagination readbacks, persisted binding tombstones, and the two source-region hashes before invoking this sidecar's projection; the sidecar does not invent connector receipts or perform recovery writes.

Before a future Keychain `SET`, enrollment atomically publishes `auth-manifest.json` with `state=pending`, even when an older enrolled manifest exists. Collection accepts only `state=enrolled` with the exact config binding and unexpired `expires_at`. If Keychain `SET` succeeds but manifest publication fails, the state is fixed `enrollment_incomplete`; the old manifest cannot authorize the changed secret, collection remains blocked, and there is no automatic rollback deletion or auth fallback.

## Implemented semester extension

The sidecar now accepts an explicit `knu-lms-semester-registry.v1` document and
validates each registered course independently. `CourseSpec` preserves the exact
origin, positive LMS ID, name, code, term, verification state, and
`academic_import` flag. Only academic entries contribute to `academic_expected`;
excluded candidates remain observable without child collection or projection.
`validate_semester_snapshot` retains course coverage and fixed partial/failed
states, so one incomplete course cannot become a semester success. A registry
scope hash includes its canonical contents, transport class, and bounded resource
policy.

The executable commands are:

```text
python scripts/knu_lms_probe.py collect --transport aside --aside-tab-id <bound-tab> --registry <registry.json> --owner-id <owner> --scope-hash <run-hash> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
python scripts/knu_lms_sync.py snapshot --registry <registry.json> --owner-id <owner> --scope-hash <run-hash> --input <sanitized-semester.json> --output <semester-snapshot.json>
python scripts/knu_lms_sync.py project --registry <registry.json> --owner-id <owner> --scope-hash <aside-readonly-registry-scope-hash> --snapshot <semester-snapshot.json> --readback <sanitized-notion-readback.json> --prior <semester-prior.json> --observed-on <YYYY-MM-DD> --output <plan.json>
```

Aside runs `aside repl` with a fixed controlled script. It confirms one exact
configured tab and the allowlisted origin, reads response bodies through a bounded
`getReader()` stream, sanitizes JSON in the browser process, and emits one bounded
base64 sentinel frame. Python validates only that sanitized frame. Missing or stale
root reservation blocks the subprocess before any GET; a postflight failure
discards the result and emits no snapshot/project/apply plan. Fixture provenance is
synthetic and never apply-ready. Token enrollment, Keychain access, provider writes,
and heartbeat activation remain outside this sidecar.

The shared 2026-2 schedule datasource and its To DO/캘린더 views are emitted once;
course-qualified filtered views use `LMS 키 STARTS WITH <origin>/courses/<id>/assignments/`.
The existing source/user ownership, dual source hashes, retained announcement
validation, exact launch routes, and root-owned durable reservation are unchanged.

## Acceptance tests

Unit tests import the keyring package as needed but use fake probe results, clocks, credential readers, Keychain backends, and Notion readbacks; they contact no provider and never call the real macOS Keychain.

- strict complete-resource validation, exact key encoding, stable identity construction, duplicate rejection, canonical ordering, deterministic hashes/Markdown, and exclusion of bodies, grades, submissions, URLs, and module state;
- Course A/B binding tests proving exact-one reuse, verified-zero-only creation, and no title-based matching;
- preservation of `due_at: null`, explicit LMS submission-unknown wording, opaque `content_id`, exact 15-module positions/names, sanitized probe count fields, actual empty-module toggles labeled `등록된 자료 없음 · 수집 시점 기준`, exact module-item and announcement launch URLs, and no fabricated weeks;
- exactly one 2026-2 datasource, exactly three linked views, correct To DO/Calendar filters, course filtering, and zero 2026-1 references;
- new-row `확인 전` initialization, immutable `내 상태`, preserved `내 메모`, source hash replacement, USER conflict reporting, exact parent checks, and no deletion;
- manifest preflight ordering proving expired/mismatched auth stops before Keychain or network access;
- datasource/view/course-page recovery descriptors and absent/exact-one/ambiguous handling after simulated indeterminate writes;
- fixed nonblocking lock ownership, `run_busy`, TTY `status`/`release` proof, and write blocking when the holder is absent;
- pending-before-Keychain-set state, exact config binding, fixed `enrollment_incomplete`, and no old-manifest authorization after partial enrollment;
- last-31-day announcement merge, stable-ID updates, out-of-window retention, `last_seen`, prior-record route validation, KST announcement-date prefixes, exact `수집한 공지` caption, and no deletion/archive claim;
- Notion rich-text LMS-key normalization accepting only an identical display/target wrapper, null DATE readback omission, friendly exact-bound course titles, fixed source-range text, dual source-region hashes, connector-normalization no-op, and meaningful USER-edit conflict;
- macOS-only explicit backend enforcement, explicit `keychain=None` verification, a `KEYCHAIN_PATH` sentinel test without environment dumping, no global/property-based backend initialization, no fallback, fixed diagnostics, no raw backend error leakage, no-echo enrollment behavior, fixed service/account enforcement, mode/atomic/no-symlink manifest handling, and no secret in output or subprocess arguments;
- changed/no-op projection behavior and no state mutation or apply plan after incomplete collection.

Demonstrate the final `snapshot` and `project` CLIs with verified local snapshot input and a sanitized local readback fixture before acceptance. Use the frozen runtime `.review/venv311/bin/python` for all checks, for example:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py snapshot --input <verified-local-snapshot.json>
.review/venv311/bin/python scripts/knu_lms_sync.py project --registry <semester-registry.json> --owner-id <keeper-owner> --scope-hash <aside-readonly-registry-scope-hash> --snapshot <semester-snapshot.json> --readback <sanitized-notion-readback.json> --prior <semester-prior.json> --observed-on <YYYY-MM-DD>
```

The registry project command requires both `--prior` and `--observed-on`.
`--prior` contains one entry for every academic course: an accepted retained
announcement state for an existing course, or an explicit empty first-bootstrap
state for a newly introduced course. Omitting either flag is a fixed CLI
failure; a partial prior cannot authorize a semester projection.

Run focused pytest, Ruff, strict mypy, and synthetic subprocess/no-echo checks with that runtime. No live Canvas request, Keychain read/write, Notion write, scheduler activation, commit, or push is part of worker acceptance.

Official credential reference: [Python keyring documentation](https://keyring.readthedocs.io/en/latest/index.html) and [keyring backend configuration/API](https://github.com/jaraco/keyring#configuring).
