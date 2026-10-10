# Local Settings GUI-4 — Academic bindings and automation

Date: 2026-10-04
Status: **Draft. Native issued GO for the bounded R7 architecture only. Complete GUI4 Native/Gemini PLAN and FINAL reviews, source-closure review, the pending human Overall-status choice, and implementation GO remain outstanding. Product implementation remains frozen.**

## Ownership and limits

- Scope: Academic semester/course bindings, intake-worker and study-note choices, bounded desired polling interval, truthful readiness dependencies, and durable desired/applied runtime fingerprints.
- Excluded: Canvas ingestion/collector, GUI-5, provider writes during Settings save, scheduler installation/activation, global checker changes.
- Continuing owner: actual model **gpt-6-luna / max**. Fit: Python configuration/runtime boundaries, provider identity verification, process lifecycle evidence, and security-focused contract tests.
- Skills actually used: **harness-frontend-design 0.1.1-candidate** for preserving the current Settings shell and UI states; **harness-security-advice 0.1.0-candidate** for the read-only R7 source audit; **harness-architecture-advice 0.1.0-candidate** for the execution-snapshot feasibility and lifecycle boundary.
- The orchestrator owns plan acceptance, material risk decisions, Native/Gemini reviews, implementation authorization, integration, and overall acceptance. The worker owns domain implementation, checks, and required review fixes after GO.
- This revision is design-only and updates only this worker plan. Product code, master GUI plan, interaction mock, frozen specifications/contracts, checker and its nine pins, global hooks/settings, unrelated dirty work, RESEARCH, and CLAUDE remain untouched.
- No real credentials, environment files, Keychain, provider, scheduler, Canvas collector, or external workspace are read or operated. Save never starts/restarts a worker, mutates a provider, or registers/enables an OS scheduler.

## Source findings

Authority: docs/plans/local-settings-web-gui.md §§5.6, 5.7, 6.4, 12; docs/plans/local-settings-web-gui-interaction-mock.md; GUI-2/GUI-3 plans; frozen design/specification and Behavior Contract.

- src/uls/config/schema.py stores Drive semester registries, Notion semester workspaces, local Course Keys, worker.enabled, worker.poll_interval_minutes, study_notes.enabled, and retrieval.notion_lane / retrieval.semester. worker.enabled=True is a schema default, not a user choice. CourseCfg has no Canvas profile/term/course binding. The human decision for GUI-4 is two independent scopes: an Academic active-semester field for academic/worker context and the existing retrieval lane/semester pair for MCP search.
- src/uls/config/intake.py::resolve_semester_workspace requires one exact Drive registry and Notion workspace per Course Key semester, university root, semester/upload/course folders, Recordings/Materials folders, Notion parent, and academic-courses/sessions/materials/file-intake/input-requests data sources. It validates structure, not provider identity. Portal and optional upload IDs are checked when present but are not required by this resolver.
- src/uls/config/validation.py checks structural mappings and only positive interval values. Preserve that global/core compatibility; add integer bounds only to the GUI Automation mutation validator.
- src/uls/settings/config_service.py supports General, Canvas registry/sync, and Advanced groups with generation/CAS preview/apply, but no Academic or Automation group.
- src/uls/settings/composition.py and launcher.py create the production Settings services; composition.py also has a separate fake-service branch. Academic verification/readiness must be injected through both branches, and the fake branch must never mint production-like Verified evidence. Include composition, launcher, and fake_mode in the implementation and direct tests.
- **R7 revision-source audit:** src/uls/config/schema.py and loader.py define credential_revisions as a random non-secret per-role value. src/uls/settings/credential_service.py::_patch generates it with secrets.token_hex(16) during Settings credential config transactions. It is a config-side GUI operation marker; CredentialStores.read() returns only active bytes and supplies no physical keyring/file version. src/uls/cli/credential_set.py::_notion_cli can replace a declared managed Notion file/keyring value under its admission/config locks without writing config.yaml or advancing credential_revisions; its Google CLI path is guidance for a configured key path, which can also be replaced out of band. Such replacement leaves the YAML config generation and GUI revision unchanged. The current tree therefore has no trusted, secret-free revision that covers every allowed managed-slot writer. Do not infer a physical credential version from config_generation, mtime/inode, a secret-derived identifier, or a new global/foreign index.
- Such a result describes only the credential/settings snapshot actually consumed by that execution. A one-shot worker is Stopped after confirmed exit, with its last result historical. A long-running process is Running with previous settings when its non-secret consumed-settings fingerprint differs from the current desired fingerprint. An out-of-band keyring/file replacement with unchanged YAML is invisible to that fingerprint, so an old process proof cannot mean the currently configured slot is Ready. Study-note submission MCP startup is credential-free and proves transport only; study-note processing depends separately on the intake worker and its mappings. A configured study_requests_data_source_id remains NOT_VERIFIED because its schema is not accepted.
- **R7 scoped architecture disposition (not product GO):** the original-bound Native review returned GO for this architecture only and accepted the six bounded conditions recorded below. `ResolvedCredentials` is the in-memory truth anchor for one composition; CLI already resolves it once and passes it into worker or retrieval builders. Proposed `src/uls/runtime_preflight.py` holds the same snapshot, immutable consumed mapping, provider clients, fixed purpose, and non-secret fresh result in a private, non-serialized PreparedRuntime. GUI Verify stores only a human-reviewed historical expected-principal/mapping record in H1; it is never current credential evidence. Each eligible composition authenticates the same clients and compares the fresh principal with that purpose's expected principal. Do not authorize runtime from CredentialService.test/effective or ProviderChecks.last_check.
- The worker boundary must cover both `build_worker` branches. Explicit GUI4 `academic.active_semester` opts the new semester consumer into fail-closed preflight; invalid/missing selected mappings cannot fall through to `NativeWorker`/`sources.json`. Configurations without that explicit opt-in retain existing legacy worker behavior and get no GUI4 Academic Ready claim. `legacy_global` remains usable outside the Academic gate; an explicitly selected semester retrieval lane has its own MCP read-purpose check. MCP preflight belongs only to that composition/lifetime, and later provider ACL/schema drift remains a runtime failure. Read-only principal/resource/schema checks do not prove every worker write; do not claim full write readiness, and preserve actual operation refusals as authoritative.
- **Consumer-map closure is provisional:** the scoped Native packet lacked complete direct imports and source consumers. Parent has added `src/uls/intake/composition.py`, `src/uls/adapters/drive/binding.py`, `src/uls/retrieval/engine.py`, `src/uls/state/reader.py`, `src/uls/study_notes/core.py`, and `src/uls/study_notes/store.py` to the complete source closure. The exact feature-specific resource inventory remains pending that complete source review; this draft does not claim it is exhaustive.
- **Six scoped Native R7 conditions retained:** (1) runtime authorization consumes one immutable `ResolvedCredentials` and exact mapping snapshot without re-reading via `CredentialService.test/effective`; (2) cover both worker branches and MCP composition without forcing legacy migration or gating `legacy_global`; (3) existing `ProviderChecks.last_check` remains historical, and each opted-in purpose checks its complete actual mappings; (4) principal equality is separate from capability and does not promise all writes; (5) the human-reviewed H1 principal is only the expected target and each runtime authenticates/compares afresh; (6) a private in-memory PreparedRuntime binds exact provider/profile/purpose, snapshot, mappings, and clients to one invocation and is neither serialized nor cross-reused.
- Such a result describes only the credential/settings snapshot actually consumed by that execution. A one-shot worker is Stopped after confirmed exit, with its last result historical. A long-running process is Running with previous settings when its non-secret consumed-settings fingerprint differs from the current desired fingerprint. An out-of-band keyring/file replacement with unchanged YAML is invisible to that fingerprint, so an old process proof cannot mean the currently configured slot is Ready. Study-note submission MCP startup is credential-free and proves transport only; study-note processing depends separately on the intake worker and its mappings. A configured study_requests_data_source_id remains NOT_VERIFIED because its schema is not accepted.
- src/uls/settings/provider_checks.py has explicit read-only Google-root and legacy Notion checks. Credential presence and those broad checks do not verify each Academic folder/data source, portal, or required Notion schema.
- The saved GUI-3 Canvas registry is the source for exact user-selected profile, term, and course IDs. GUI-4 must not infer identity from names, codes, labels, or term titles.
- GUI-3 supplies static Canvas evidence: CanvasService checks the saved profile identity and re-reads selected course IDs under the selected term before applying the registry. GUI-4 can enforce exact membership in that saved registry; it must not call the local join a fresh Canvas check or add Canvas calls to worker startup. Preserve GUI-3 lease/staleness states.
- MCP search uses the existing retrieval.notion_lane / retrieval.semester selector independently of Academic selection. Reuse the current semester-retrieval contract coverage; do not add an Academic gate to legacy_global or silently switch search scope when academic.active_semester changes.
- src/uls/intake/study_note_composition.py::install_study_notes composes only when enabled, requires one unambiguous active semester and intake-ready worker, and reports NOT_VERIFIED if study_requests_data_source_id is set because its schema is not accepted. It does not create schemas.
- src/uls/settings/status.py reports restart state as not_reported and has no worker-loaded fingerprint. Save, credential presence, and worker-object construction do not prove that a process loaded or is running those settings. Keep Not reported distinct from Stopped after a confirmed successful load, Running with the desired fingerprint, and Running with a previous fingerprint.
- src/uls/cli/main.py builds the worker for run/sync/process, invokes WorkerRunner.run_once, then closes it. src/uls/orchestration/runner.py returns already_running without acquiring the worker lock for a losing contender. A lock loser must not publish applied/running evidence.
- src/uls/intake/worker.py sets IntakeWorker.runner=self and has its own run_once lock/finally lifecycle; instrumentation only in WorkerRunner misses this path. Its current no-argument semester choice falls back to the first resolved workspace. For an explicitly GUI-managed academic.active_semester, use that exact value and fail closed if it is stale; preserve existing CLI/intake behavior for legacy configs missing the new field, while showing Not selected and withholding GUI Academic/worker readiness until explicit selection.
- src/uls/runtime.py composes the local study-note submission MCP without Google/Notion credentials. The MCP SDK server constructors only build server objects; src/uls/mcp/transports/local.py and remote.py own the actual transport lifecycle. A truthful startup report must be connected to successful transport entry/bind, not object construction or Settings status.
- The macOS launchd plist uses StartInterval=60; the Windows task XML repeats at PT1M. Worker commands are one bounded tick and exit. poll_interval_minutes has no consumer that changes either scheduler. GUI-4 cannot call this an applied cadence or provide a calculated next-run time.
- Retrieval defaults to the legacy global Notion lane. Choosing semester_workspace changes retrieval results and requires explicit review.

Academic identity verification and runtime observations are new explicit capabilities, not facts already supplied by Overview, a credential test, or a non-empty ID.

## Product contract

### Academic identity and mappings

1. Keep IDs in their current canonical locations: google_drive.semester_registries and notion.semester_workspaces. Do not duplicate them as another editable source of truth.
   Candidate merge is path-targeted: preserve other semesters, unrelated Course Keys, registry/workspace rows, and unknown YAML keys. Editing the active semester never prunes unselected rows or unknown fields. Removal is outside this bundle.
2. Add an exact Canvas binding for each local Course Key: saved Canvas profile ID, saved registry term ID, selected Canvas course ID. The tuple must exist in that exact saved GUI-3 registry. Reject duplicate, stale, cross-semester, and name-only joins.
3. The user explicitly selects the semester-to-Canvas-term registry and maps each exact Canvas course ID to a validated same-semester Course Key. Names, codes, sections, and term titles are display-only.
4. Academic editing covers every resolver-required Drive and Notion mapping for every selected Course Key. Use the current resolve_semester_workspace requirement set; never silently skip an incomplete course. Portal and optional upload IDs remain optional unless another accepted contract makes them mandatory.
5. A manually entered or discovered ID can be saved as Configured/Unverified after structural validation. A pasted ID, credential presence, stale check cache, or fake/demo service never establishes current runtime admission. GUI-3's saved Canvas registry is static evidence: its selection path verifies the profile and re-reads exact course IDs under the chosen term before apply; GUI-4 requires exact tuple membership and does not add Canvas calls to worker startup.
   - An explicit purpose Verify performs bounded read-only checks and yields historical metadata only: purpose, exact mapping identity, non-secret expected provider principal, checked time, verifier version, and observed kind/parent/schema result. This metadata is saved only inside the final H1 diff reviewed and applied by the human. The stored principal is an expected target for later continuity checks, never evidence that the current credential is still the same.
   - Current runtime admission requires a fresh authentication and exact mapping preflight from the same ResolvedCredentials snapshot and provider clients the composition will consume. Google checks principal/application identity plus exact configured folder identity, kind, parent, drive and ownership metadata. Notion checks the authenticated principal, exact selected data-source IDs, parent relationships, and code-owned accepted schemas. A read-only preflight does not prove all later worker writes will succeed; actual provider refusals and schema drift remain authoritative failures.
6. Keep structural save, historical expected-principal choice, and current runtime admission separate:
   - **Historical check transaction:** bind a new purpose-specific observation to validated pre-evidence candidate H0, saved config bytes/generation G0, exact mapping digest, fixed role set, observed non-secret principal, and verifier version. Do not include a credential-value revision. Add this historical expectation plus the backend-owned Automation choice evidence to final candidate H1 before rendering the final redacted diff; the human reviews and applies H1. Apply rechecks G0/H1 and mapping/choice digests under the existing config lock and commits the exact reviewed bytes. The resulting saved state says Configured/Previously checked, not current Verified/Ready. Mapping, purpose, or expected-principal changes invalidate the historical expectation and require a new reviewed H1.
   - **Runtime composition:** each eligible invocation resolves credentials exactly once, freezes its consumed mapping/config projection, constructs the provider clients from that same snapshot, authenticates the same clients, and compares fresh principal identity with the human-reviewed expected principal for that exact purpose. A private in-memory PreparedRuntime carries the ResolvedCredentials snapshot, immutable mappings, preflighted clients, purpose, and non-secret result metadata to the consumer; it is neither serialized nor derived from secret hashes. CredentialService.test/effective and ProviderChecks.last_check are not runtime authorization sources because they re-read backing slots or lack the exact mapping/purpose binding. Provider drift after preflight remains possible, so every actual provider operation must continue to fail closed.
   - **Runtime boundaries and compatibility:** worker preflight covers both build_worker branches. For GUI4-managed Academic execution, explicit academic.active_semester plus reviewed expected-principal metadata is the opt-in; a missing/stale registry or mapping fails closed and may not fall back to NativeWorker/sources.json. A legacy config without that opt-in keeps its existing worker behavior but receives no GUI4 Academic Ready claim. legacy_global remains usable without the Academic gate. A pre-GUI4 semester_workspace config remains loadable and behavior-compatible without automatic enrollment; GUI4 must not claim it is admitted until the user explicitly opts into its purpose-specific expected-principal flow. Study-note submission MCP is credential-free; only its actual transport lifetime is observed, while downstream Study Notes processing reuses the applicable worker-purpose runtime and its own mapping/schema gates.
   - **Transient status:** worker authorization belongs to one run after the actual worker lock is obtained; a lock loser, failed preflight, failed start, or object-only construction emits no success. MCP admission belongs only to that server composition/lifetime and is reported only after actual transport entry/bind; startup is not everlasting permission for later requests. After a one-shot worker exits, show Stopped and mark the preflight historical. An old active process is Running with previous settings when its consumed non-secret fingerprint differs from desired. A backing-slot change with unchanged config cannot be detected by that fingerprint, so never label an old process proof as current-slot Ready.
   - Provider failure or ambiguity leaves the saved config and historical expectation unchanged. Client requests cannot submit expected-principal, preflight, verified, or choice-confirmed fields. Persist no credential values, secret hashes, raw provider responses, or unnecessary account emails.
7. Use fixed verifier purposes and role sets, never a caller-selected credential role. Provide separate historical Verify routes and runtime admission for read-side retrieval versus intake-worker purposes. Retrieval uses only Google/Notion MCP read roles; intake uses only Google/Notion worker roles. A stored expected principal is purpose-bound; a runtime PreparedRuntime cannot cross purposes or be reused across compositions. Do not treat GUI credential-role revisions as physical value revisions. All discovery and verification calls are read-only; no page-load checks, provider writes, schema creation, or write probes.
8. **R7 architecture proposal after scoped Native GO; full PLAN/FINAL and human Overall-status decision remain pending:** no trusted secret-free physical-slot revision exists across Settings, CLI, and out-of-band writers, and the accepted proposal does not need one. Store only human-reviewed historical purpose/principal/mapping metadata in H1 as an expected identity target. Each eligible runtime composition freshly authenticates the exact consumed snapshot/client, compares the observed principal with that target, verifies its exact feature mappings, and holds an in-memory PreparedRuntime only for that composition. Persistent Academic state remains Configured/Previously checked; only a live execution can be admitted. No credential hash/value-derived ID, secret-derived store identity, global/foreign version index, or current-slot claim is allowed. The pending human choice is (A) save as Configured, run fresh execution preflight, and keep Overall at Confirmation needed until fresh execution evidence exists, or (B) defer new semester consumers pending a separate versioning design. Do not infer that choice. The exact stable Notion principal field and final managed/unmanaged boundaries also remain for complete PLAN review.
9. Never create Notion databases, pages, properties, or schemas. If read-only APIs cannot prove exact identity, parent/kind, and the accepted Notion schema, leave the mapping Configured/Unverified and readiness Partial/Blocked.
10. Add one academic.active_semester field as the sole explicit semester for GUI-managed Academic and worker context. Keep retrieval.notion_lane and retrieval.semester as an independent MCP search-scope selector. Never auto-sync, auto-switch, infer, or silently migrate one from the other. Preserve retrieval default legacy_global with an empty semester. A pre-GUI config without academic.active_semester remains loadable and keeps its existing CLI/intake compatibility behavior; the GUI displays Not selected and does not claim Academic or worker readiness until a user chooses and verifies a semester. Once explicitly set, a missing or stale semester fails closed without first-row or retrieval-scope fallback.
   An explicit retrieval-scope change has its own typed retrieval_scope group, reviewed diff, and readiness validation. Changing Academic semester alone does not modify retrieval. legacy_global remains usable without an Academic selection, Canvas join, Academic receipt, or Academic fingerprint input. For semester_workspace, require only the selected workspace's relevant verified read-side mappings; it may differ from academic.active_semester.
11. Human-owned fields, including Material Usage.Verified and Exam.Scope Confirmed, are never edited, defaulted, inferred, or promoted.

### Automation choices and desired interval

1. Require explicit Enabled or Disabled choices for both intake worker and study notes. A successful Apply includes backend-owned non-secret choice evidence in the final reviewed candidate, bound to both exact values; it cannot be appended after the final candidate hash/diff. Missing, stale, or mismatched evidence means Not checked. The worker.enabled=True default never completes Automation.
2. Explicit Disabled completes that feature's choice and needs no worker credentials; it is not Failed. Enabled remains distinct from readiness/runtime status.
3. In GUI Automation candidate validation only, when poll_interval_minutes itself changes, require an exact int (bool excluded) in [1, 1440]. Reject fractions, zero, negatives, and values above 1440. If only a worker/study-note toggle changes, preserve an existing positive legacy interval including its original YAML scalar bytes (for example, 0.50); do not reformat it or reject it solely for being fractional/out of range. Do not tighten global/core loading/runtime validation. A user editing an unsupported legacy interval gets actionable guidance to choose a supported value.
4. Store this as Desired interval only. No scheduler controller/readback exists: actual schedule and next run stay Not reported, with actionable manual scheduler setup guidance. Do not calculate next run, claim the scheduler uses the value, edit deployment files, register a task, or start a scheduler.
5. Save commits only reviewed config and choice evidence. It never launches, restarts, schedules, syncs, or contacts a provider. Where Settings lacks a process controller, provide a manual supported start/restart instruction.
6. Canvas scheduled sync remains outside GUI-4; its ingestion bundle is not accepted.

### Desired/applied fingerprints and process state

Use a versioned deterministic SHA-256 of each service's canonical, non-secret effective-settings projection. Maintain a per-service consumed-input table and test it directly. Include only values the process actually consumes, exact Academic mapping and historical expected-principal reference where relevant, explicit semester/lane, feature toggles, and schema/verifier versions. A config generation or credential_revisions nonce may be shown as historical transaction context but is never a credential-value revision or runtime authority. Exclude display labels, lease fields, verification checked_at, credential values, secret hashes, tokens, secret paths, response bodies, and human-owned fields. Do not show raw fingerprints absent an accepted debugging need.

| Service projection | Inputs included | Inputs explicitly excluded |
|---|---|---|
| Intake one-shot | Exact selected Academic semester, immutable worker-resolved mappings, fixed purpose, expected-principal reference, feature choices, and worker settings actually read by that invocation | poll_interval_minutes (no current scheduler consumes it), GUI credential-role revisions, labels/display names, checked_at, runtime lease |
| MCP retrieval | retrieval.notion_lane and retrieval.semester plus the exact read-purpose mappings and expected-principal reference consumed by that lane | academic.active_semester, Canvas join, worker-role inputs, and Academic gates for legacy_global |
| Local study-note submission MCP | Only the settings used by runtime composition and the launched transport | Google/Notion credentials and downstream processing mappings this server does not consume, poll interval unless a consumer is added |

Do not claim a fingerprint covers a value absent from that service's projection. poll_interval_minutes remains desired configuration, not actual schedule or a worker-loaded input.

Desired derives only from the persisted config snapshot, never browser draft, and is reproducible from that saved generation after restart. Durable config is its source; no independently editable desired value can drift. Applied/start observations are process-originated history, not a durable credential proof. A private PreparedRuntime is never written to the sidecar. Save, preview, credential presence, config hash alone, and worker construction cannot emit successful admission.

- **Intake worker one-shot:** For GUI-managed config, build/validate config, credential, and workspace snapshot for the explicit academic.active_semester. When that field is absent in a legacy config, preserve the existing CLI/intake path but do not present GUI Academic/worker readiness as verified. Instrument both actual paths: src/uls/orchestration/runner.py::WorkerRunner.run_once and src/uls/intake/worker.py::IntakeWorker.run_once. IntakeWorker sets runner=self, so a WorkerRunner-only hook misses that path. Each reports only after its own acquire_local_worker_lock() succeeds; a loser returns already_running and reports nothing. Persist active execution after lock acquisition and terminal outcome in finally before unlock. Pre-lock failure reports no applied run. Abrupt exit leaves incomplete history, never proof the process remains running.
- **Study-note composition:** Report readiness only after install_study_notes completes checks. Existing prerequisite failure or unaccepted study_requests_data_source_id schema is Not verified/Blocked with a fixed safe reason. study_notes.enabled=True is not readiness.
- **Local study-note submission MCP:** src/uls/runtime.py composition or SDK server-object construction is not a start report. Report applied only when the actual local stdio/remote transport enters its serving lifecycle after successful bind/start. Its Running state proves submission transport availability only, never downstream worker processing readiness; this path does not consume Google/Notion credentials.
- **Retrieval MCP:** Report applied only after its actual consumed read-role credentials, selected retrieval workspace, provider identity, and server composition succeed and local/remote transport enters service. Keep per-process records; shutdown records stopped; bind/start failure cannot replace prior successful evidence.
- **Liveness storage:** Put the non-secret store at Path(state_path(config)).parent / "runtime-observations". Require a verified owner-only directory (0700), no symlink path components at creation/open, observations.json and writer.lock as owner-only regular files (0600), and a leases subdirectory (0700). The versioned observations schema contains only instance UUID, service enum, applied fingerprint/version, config generation, started/ended UTC timestamps, and phase/outcome enum. Do not serialize a lease filename/path; derive exactly leases/<validated-instance-uuid>.lock from the UUID. It contains no PID, cwd, credential value, provider response, or absolute path.
  Each process creates its derived leases/<instance-uuid>.lock with exclusive/no-follow creation and holds an exclusive POSIX flock file descriptor for its actual lifetime. On status read, derive and open that exact lease with no-follow and attempt a nonblocking exclusive flock: contention proves an active lease; acquiring the lock proves the prior process no longer holds it. Missing, unsafe, unreadable, or ambiguous evidence means Not reported, never “stopped” by assumption. A started row without a held lease is stopped/outcome-unknown after crash; the kernel releases flock on process death.
  Serialize observations.json updates under writer.lock, write a same-directory owner-only temporary file, fsync it, atomically replace observations.json, then fsync the runtime directory. First creation must durably create/fsync the runtime and leases directories and their parent before any applied/effect report. Record terminal state before releasing the instance lease. This is a macOS/Linux-only proposal using Python fcntl.flock; no Windows Settings support is implied. If the required no-follow/lock/fsync capability is unavailable, status remains Not reported and no applied success is emitted.

| Evidence | State |
|---|---|
| No successful load/start observation | Not reported / Not verified |
| Startup failed before serving | Failed to start; prior applied evidence unchanged |
| Confirmed active process whose same-snapshot preflight passed, fingerprint equals desired | Running on the admitted loaded snapshot; this does not prove the backing credential slot is still unchanged or grant everlasting permission |
| Confirmed active MCP process, applied differs from desired | Running with previous settings; restart required. Keep readiness separate and do not label it Ready |
| Confirmed active one-shot worker with older fingerprint | Running with previous settings; the current tick is not using the saved desired values. The next invocation loads them; do not invent a daemon restart action |
| Successful observation but no active lease | Stopped; show last successful load, not a currently applied/running state. A one-shot worker uses desired settings on its next invocation |
| Incomplete/crash record without lease | Stopped, outcome unknown; never Running or Ready |
| One-shot worker completed | Stopped after last run; show that run. Changed desired applies next invocation, not a daemon restart requirement |
| External scheduler has no verified readback | Schedule Not reported; next run unavailable; manual setup may be needed |

Only a process-originated successful same-snapshot preflight/load observation with a verifiable live lease can produce the admitted-running state. Missing observer, malformed/unsafe sidecar, failed preflight, lock loser, object construction, Settings Save, credential presence, or fake composition is Not reported/Not verified. A failed start preserves previous historical evidence; it cannot fabricate a new admitted fingerprint. Provider drift after preflight remains possible, and readiness never means every later worker write is guaranteed. Readiness is separate from Running.

### Readiness dependencies

Each node names its evidence and missing prerequisite. Ready is never inferred from a saved ID, credential presence, default toggle, or matching fingerprint alone.

| Node | Ready evidence | Otherwise |
|---|---|---|
| Canvas identity and Academic join | GUI-3 verified account/profile and exact saved term/course IDs; each selected course maps once to a valid same-semester Course Key without duplicate/stale/name-only joins. Use the GUI-3 registry as static evidence; do not imply a fresh Canvas provider check in worker startup | Partial/Not checked; manual labels/IDs do not verify the join |
| Academic mappings | Persisted state is Configured/Previously checked. A GUI4-managed execution is admitted only after same-snapshot principal equality and exact feature-specific mapping/kind/parent/schema checks for its fixed purpose | No historical identity, incomplete map, identity mismatch, or failed read check blocks that execution; no write-capability promise |
| MCP retrieval scope | legacy_global remains the default and is independent of Academic active semester and Canvas join. Explicit semester_workspace has its own fixed read-purpose expected principal and selected-workspace preflight | Existing legacy_global remains usable; semester-lane preflight failure blocks that composition |
| Worker choice | Explicit Enabled/Disabled evidence bound to saved values | Not checked regardless of default |
| Intake worker Enabled readiness | Explicit choice (not schema default), explicitly selected Academic semester for GUI-managed config, the full current resolve_semester_workspace worker-required ID set, purpose-bound same-snapshot read-only preflight, and current runtime admission | Blocked/Partial; a credential-presence check, manual ID, or default True cannot promote; no full write-capability claim |
| Worker Disabled | Explicit Disabled choice | Not checked without explicit choice |
| Study-note processing | Explicit choice; if enabled, install_study_notes succeeds for the selected Academic semester, its actual worker/read-only graph prerequisites pass, and no unaccepted study_requests_data_source_id schema blocks it | Disabled only after explicit choice; otherwise Blocked/Not verified |
| Local study-note submission MCP | Its own runtime composition and stdio transport actually start; no Google/Notion credential prerequisite. Show processing readiness separately | Not reported/Stopped/Failed based on its own process observation |
| Actual schedule | Scheduler readback would be needed; none exists in scope | Not reported; no next-run claim; manual action |
| Restart | Exact process-instance observation compared with desired | Not reported without proof; stopped is not running |
| Overall Check | Pending human selection between Configured + execution-preflight with Confirmation needed until fresh evidence, and deferring new semester consumers | Do not infer Overall Complete/Ready from stored history or a runtime fingerprint |

A transient provider outage does not erase saved Configured/Previously checked history, but the current composition fails closed. Show historical configuration evidence separately from current execution admission.

### UI and failure recovery

1. Preserve current Overview/cards/headings/spacing/buttons/status pills/focus/inline validation/reviewed diff/Apply confirmation. Add Academic and Automation inside the current shell; no redesign. Show Academic active semester and MCP retrieval scope as separate controls/cards with separate current values and consequences.
2. Academic is draft-first: choose saved Canvas term/course IDs, associate Course Keys, edit every required Drive/Notion mapping. Academic active semester controls Academic/worker context only. Retrieval remains legacy_global with empty semester unless the user independently changes and reviews its selector. Provider names are display aids. Verify is explicitly read-only and never runs on page load.
3. Preview lists structural errors, Configured/Previously checked values, historical expected principals, active-lane changes, invalidated history, and readiness/restart impact. For Verify-and-Apply, show and require review of the final H1 diff after backend-owned historical principal metadata and choice evidence have been included; the browser cannot stamp those fields. Apply uses the existing config lock, raw-byte generation/CAS check, validated candidate binding, transaction journal/recovery, atomic replacement, directory durability, and exact readback. Saved metadata remains historical, not current Verified/Ready. Stale generation preserves the draft and requires reload/re-preview, never silent overwrite.
4. Provider failure leaves saved config/receipts unchanged; preserve non-secret draft and show fixed retry guidance. Never expose raw provider bodies, credential values, or paths.
5. Mapping, purpose, or expected-principal changes stale only dependent historical evidence. Credential-role config nonces are not physical value revisions. Never carry an expected principal onto changed IDs or reuse it across purposes.
6. Both automation choices are deliberate before choice evidence is saved. Desired interval is separate from actual schedule. Disabled features do not start on Save.
7. Old running MCP fingerprint shows the service and Restart required with its supported manual action; do not invent a restart controller. One-shot worker shows last run and that the next scheduled/manual invocation must load new values.
8. Failed start or absent observer retains prior applied evidence/mismatch. Retry is explicit through a supported entry point.
9. Reuse GUI-1 session expiry/closed/inert behavior, safe errors, focus-to-heading, and draft preservation. No capabilities, credentials, raw responses, or verified account material in persistent browser storage/logs.
10. On interrupted Apply, classify the current raw config against the reviewed byte identities: current bytes equal final H1 mean committed and continue exact readback verification; bytes equal the exact original raw snapshot associated with generation G0 mean not committed and only existing journal-safe retry/resume is available; any third hash means Partial/manual review and no automatic overwrite or rollback. Do not promise the previous config survives a crash after atomic replacement.

## Candidate files and tests

This is a bounded candidate list for parent review; change only files required by GO and include all direct callers in the final package.

- Config: src/uls/config/schema.py and src/uls/config/loader.py. Add academic.active_semester, exact Canvas binding, historical non-secret purpose/principal/mapping metadata, and backend-owned choice evidence. Historical metadata is an expected-principal target, never current runtime proof. Do not add a physical credential revision. Do not edit src/uls/config/validation.py or tighten core loading; positive legacy intervals remain accepted and legacy configs are not auto-migrated.
- GUI interval validation: src/uls/settings/config_service.py only, on the Automation group mutation path; when the interval changes require exact int (bool excluded) and 1..1440. If only toggles change, preserve the original positive legacy interval scalar bytes in the final candidate. Add direct tests for 0.50 toggle-only byte preservation, unchanged out-of-range positive legacy values, and changed interval rejection/acceptance; do not rewrite global validation.
- Resolver: src/uls/config/intake.py is in scope to consume an explicitly selected academic.active_semester and exact immutable mapping snapshot for GUI4-managed runtime preflight. Preserve legacy CLI/intake behavior when the field is absent; do not force a migration. Preserve exact no-fallback behavior for an explicitly set but invalid value and keep retrieval.notion_lane / retrieval.semester independent.
- New src/uls/settings/academic_service.py: canonical mapping digest, explicit bounded read-only historical Verify, expected-principal metadata in a final reviewed H1, fixed safe errors, and no schema creation/page-load call. The result says Previously checked, not current Verified/Ready.
- New src/uls/runtime_preflight.py: runtime-neutral prepared-runtime boundary with a private non-serialized PreparedRuntime holding the exact ResolvedCredentials snapshot, immutable consumed mappings, preflighted provider clients, fixed purpose, and non-secret principal/result metadata. It imports no Settings web module or provider SDK; composition roots construct SDK clients once and pass the same clients through preflight and consumer code.
- Settings: src/uls/settings/config_service.py, src/uls/settings/app.py, src/uls/settings/status.py, src/uls/settings/composition.py, src/uls/settings/launcher.py, and src/uls/settings/fake_mode.py for typed preview/apply/read, purpose verification, choices, readiness, observations, and matching production/fake service interfaces. Preserve CAS/Origin/CSRF/redaction/journal. Fake/demo paths may not emit production Verified or applied evidence.
- Typed API: retain GET /api/v1/settings/{group}, POST /api/v1/settings/{group}/validate, and POST /api/v1/settings/{group}/apply. Keep retrieval-read and intake-worker Verify routes purpose-fixed; callers cannot choose arbitrary credential roles. Verify returns server-created historical expected-principal/mapping metadata, which must be included in final H1 and reviewed before Apply. Runtime admission is separate and uses the same snapshot/client that the consumer will use. GET Academic/Automation returns generation, typed saved values, Configured/Previously checked status, and safe check-time metadata without secrets. GET /api/v1/overview reports the selected Overall state and per-instance execution observations; human choice remains pending.
- Config fields: academic.active_semester is absent/unset until explicit selection; do not synthesize it into legacy config. courses[].canvas_binding stores exact profile_id/term_id/course_id; existing Drive/Notion IDs remain canonical. A proposed server-owned `verification_history` stores only purpose, non-secret expected principal, mapping digest, verifier version, checked_at, and bounded observed resource identity/kind/parent/schema summary. It contains no credential revision, secret hash, raw response, or authority to admit a runtime. The Automation Apply service derives a versioned digest of the exact Enabled/Disabled tuple plus original generation/transaction binding before final H1; the client cannot submit a choice-confirmed marker. On every read, compare the saved digest with the actual booleans; mismatch means Not checked.
- New runtime-neutral src/uls/runtime_fingerprints.py implements the exact runtime-observations directory/schema/locks above. It must not depend on the Settings web package.
- Runtime callers: src/uls/cli/main.py, src/uls/orchestration/runner.py, src/uls/worker.py, src/uls/runtime.py, src/uls/intake/worker.py, src/uls/intake/study_note_composition.py, src/uls/mcp/transports/local.py, and src/uls/mcp/transports/remote.py. Instrument both WorkerRunner.run_once and IntakeWorker.run_once because IntakeWorker.runner is self. Also instrument actual local study-note submission MCP startup without provider-credential prerequisites. Report only after successful worker lock acquisition or actual transport startup/bind. src/uls/mcp/server.py and src/uls/study_notes/mcp.py remain audit-only if transport lifecycle hooks provide sufficient proof.
- UI: src/uls/settings/static/index.html, app.js, and only necessary styles.css rules. No external assets/CDN/analytics/UI-5.
- Deployment files deployment/macos/com.syllva.uls.plist and deployment/windows/uls-task.xml are not changed. No scheduler install/enablement/interval rewrite.
- New/extended config and Academic contracts cover exact IDs, historical expected principals, bounded read-only verification, schema mismatch, fail-closed behavior, and per-composition proof without persistence or cross-purpose reuse.
- Runtime tests cover deterministic per-service projections, secret non-persistence, same-snapshot/no-second-read semantics, same-principal rotation versus different-principal access, provider ACL/schema drift, exact mappings by feature, cross-purpose rejection, preflight failure before consumer exposure, legacy branches, excluded display/lease/checked_at fields, derived lease path from UUID, startup success/failure, lock loser, terminal finally, abrupt exit, lease exit, multiple instances, and current/previous/stopped/unverified states.
- Extend settings composition/HTTP/UI and settings_ui_harness.cjs for CAS draft preservation, purpose-proof separation, Configured/Previously checked vs execution admission, final-H1 historical-metadata/choice review, explicit toggles, exact interval validation plus legacy scalar-byte preservation, no page-load provider call, no Save side effect, feature-gated readiness, scheduler limitation, stale history, old/stopped/unverified state, crash-hash recovery, focus/accessibility, safe recovery, and fake-composition non-promotion.
- Extend CLI/MCP tests only at changed entry points. Reuse existing resolver, Canvas registry, session, provider-check, study-note composition, and CLI tests where they prove the contract; avoid duplicates.
- Synthetic configs, fake keyrings, temporary fixtures, fake transports, and subprocesses only. No real env, Keychain, credentials, providers, or scheduled tasks.

**Plan-review disposition map:** R1 human-reviewed historical expected-principal metadata and choice bytes are in final H1 before Apply; R2 Save alone never activates a consumer, and any newly admitted execution needs its own fresh-purpose preflight; R3 apply recovery uses H1/G0/third-hash classification without rollback promises; R4 legacy_global and Academic selection remain independent and legacy configs are not migrated; R5 readiness is feature-gated against the exact composition/mapping closure; R6 historical Verify and runtime admission use fixed purposes/role sets with no cross-purpose reuse; R7 immutable runtime snapshots plus expected-principal equality replace physical-slot revision claims; R8 runtime.py, the proposed prepared-runtime boundary, production/fake Settings composition, and actual transport lifetimes are in the review inventory; R9 interval bounds apply only when that field changes and toggle-only writes preserve the raw legacy scalar. Adopt optional O1 derived lease names and O2 explicit consumed-setting projections as described above.

## Exact source and test inventory for review

The following is the closed candidate edit set after parent PLAN GO, unless a direct Native finding requires a separately bounded addition. The reviewer packet also includes the unchanged dependency/evidence paths below. Before implementation, compare every candidate edit path with the independently frozen nine checker pins. If any required edit path overlaps a pin, stop and report the path; never refresh or change a pin in this task.

Parent's preliminary manual check covered 22 candidate edit paths. `src/uls/runtime_preflight.py` is a new proposed path added after that comparison; the final closed set (currently 23 candidate product paths) still needs the required pre-implementation pin/path comparison. No pin has been refreshed.

**Candidate product edits:**

- src/uls/config/schema.py
- src/uls/config/loader.py
- src/uls/config/intake.py
- src/uls/settings/academic_service.py (new)
- src/uls/runtime_preflight.py (new; private PreparedRuntime and purpose-bound preflight interfaces, no Settings-web or provider-SDK imports)
- src/uls/settings/config_service.py
- src/uls/settings/app.py
- src/uls/settings/status.py
- src/uls/settings/composition.py
- src/uls/settings/launcher.py
- src/uls/settings/fake_mode.py
- src/uls/runtime_fingerprints.py (new)
- src/uls/runtime.py
- src/uls/cli/main.py
- src/uls/orchestration/runner.py
- src/uls/worker.py
- src/uls/intake/worker.py
- src/uls/intake/study_note_composition.py
- src/uls/mcp/transports/local.py
- src/uls/mcp/transports/remote.py
- src/uls/settings/static/index.html
- src/uls/settings/static/app.js
- src/uls/settings/static/styles.css

src/uls/config/validation.py is a compatibility dependency and is explicitly not edited. GUI-only integer/range enforcement belongs in src/uls/settings/config_service.py.

**Unchanged implementation dependencies included for audit:**

- src/uls/config/validation.py
- src/uls/config/credentials.py
- src/uls/settings/journal.py
- src/uls/settings/security.py
- src/uls/settings/credential_service.py
- src/uls/settings/credential_roles.py
- src/uls/settings/provider_checks.py
- src/uls/settings/canvas_service.py
- src/uls/settings/canvas_checks.py
- src/uls/settings/__init__.py
- src/uls/settings/demo.py
- src/uls/mcp/server.py
- src/uls/study_notes/mcp.py
- src/uls/study_notes/config.py
- src/uls/study_notes/handler.py
- src/uls/orchestration/locks.py
- src/uls/adapters/drive/worker.py
- src/uls/intake/registry.py
- src/uls/intake/identity.py
- src/uls/adapters/drive/base.py
- src/uls/adapters/drive/google.py
- src/uls/adapters/notion/base.py
- src/uls/adapters/notion/api.py
- src/uls/adapters/notion/guarded.py
- src/uls/adapters/notion/intake.py
- src/uls/intake/composition.py
- src/uls/adapters/notion/usage_range.py
- src/uls/adapters/drive/binding.py
- src/uls/retrieval/engine.py
- src/uls/state/base.py
- src/uls/state/reader.py
- src/uls/state/sqlite.py
- src/uls/study_notes/core.py
- src/uls/study_notes/store.py

**Candidate changed/new tests:**

- tests/contract/test_settings_academic_service.py (new)
- tests/contract/test_runtime_preflight.py (new)
- tests/contract/test_runtime_fingerprints.py (new)
- tests/contract/test_settings_config.py
- tests/contract/test_settings_http.py
- tests/contract/test_settings_ui.py
- tests/contract/settings_ui_harness.cjs
- tests/contract/test_settings_composition.py
- tests/unit/test_settings_launcher.py
- tests/contract/test_mcp_runtime.py
- tests/contract/test_worker_cli.py
- tests/integration/test_intake_worker_preview.py
- tests/integration/test_native_runtime.py
- tests/integration/test_study_note_composition.py
- tests/integration/test_study_note_lifecycle_wiring.py
- tests/unit/test_study_note_cli.py

**Unchanged test/support dependencies included for audit and reuse:**

- tests/contract/_settings_support.py
- tests/contract/test_settings_cas.py
- tests/contract/test_settings_journal.py
- tests/contract/test_settings_provider_checks.py
- tests/contract/test_settings_canvas_service.py
- tests/contract/test_settings_credential_roles.py
- tests/fixtures/fake_drive.py
- tests/fixtures/fake_notion.py
- tests/unit/test_config_validation.py
- tests/unit/test_intake_registry.py
- tests/unit/test_semester_retrieval_config.py
- tests/unit/test_local_worker_lock.py

The credential-role definitions are not planned to change. Reuse the previously accepted `credential_roles.py` and `test_settings_credential_roles.py` source/test evidence; add direct Academic-service assertions for the fixed purpose-to-role mapping and proof non-reuse. Whether the unchanged role test belongs in a fresh passing source record or should be cited as prior accepted evidence is a parent packaging decision. Do not modify, rename, or silently omit it to satisfy a checker.

The final Native/Gemini bundle is the complete current-source union from the parent-owned manifest, with complete files and per-file SHA-256 partitioned by the orchestrator according to measured size; do not assume a fixed partition count, and keep each partition within the 120k-token cap. This plan edit changes the plan hash; the parent-owned manifest and source package must be regenerated/rehashed by the orchestrator before review. Include runtime.py, production/fake composition, launcher, both actual transport callers, unchanged MCP SDK constructors, large Notion/SQLite dependencies needed to audit the call graph, and every changed direct test. Do not substitute counts or excerpts for source files.

**Authority files included in every complete plan/final source union:**

- docs/plans/local-settings-web-gui.md
- docs/plans/local-settings-web-gui-interaction-mock.md
- docs/plans/local-settings-gui-1-worker-plan.md
- docs/plans/local-settings-gui-2-worker-plan.md
- docs/plans/local-settings-gui-3-worker-plan.md
- docs/plans/local-settings-gui-23-review-corrections.md
- university-learning-system-v1.2-design-frozen.md
- university-learning-system-v1.2-implementation-spec-frozen.md
- contracts/study-behavior.md

The parent’s final manifest adds every actually changed source/test path before sending, with current file hashes and dependency closure. No source count substitutes for complete files.

Acceptance evidence must include: manual IDs remain unverified; exact registry/Course Key joins and duplicates/stale/cross-semester rejection; every mapping from the complete consumer source closure; optional portal/upload not accidentally made mandatory; fake read-only principal/resource/kind/parent/schema success and mismatch with no write/schema-create; historical expected-principal metadata appears only in reviewed H1; same-snapshot fresh identity matches the expected principal or blocks; same-principal rotation can pass while a different principal with identical resource access fails; provider ACL/schema drift and later mutation refusal remain fail-closed; no second backing-source read, no cross-purpose reuse, and no consumer exposure after failed preflight; default True is Not checked; explicit Disabled completes only its feature; interval endpoints 1/1440 pass and bool/fraction/0/negative/1441 fail; desired schedule stays distinct from actual/next-run; lock loser/preflight failure emits no admitted run; terminal outcome precedes unlock; crash is not Running after lease exit; MCP object/bind failure emits no success; legacy worker and legacy_global compatibility are preserved without GUI4 Ready claims; all active/old/stopped/failed/unknown cases remain distinct; human-owned fields and read-only MCP are unchanged; UI proves no page-load check or Save side effect, stale-draft recovery, focus/live announcement, session-close invariants, and redaction.
Acceptance also proves that editing one semester preserves all other semester/workspace/course rows and unknown YAML keys through preview, apply, failure, and readback.

Direct regression evidence additionally covers:

- H0-to-H1 historical-check flow: purpose, non-secret expected principal, mapping identity,
  checked_at, and explicit-choice evidence are present in the exact final H1 bytes before the final
  redacted diff; any post-review change is rejected and requires a new diff. Client-supplied
  expected-principal/history/choice fields are rejected. H1 metadata is not current runtime proof.
- Configured save has no start side effect. Under option A only, an explicit later GUI4-managed
  invocation performs fresh same-snapshot preflight and may admit that one composition; persistent
  Academic state remains Configured/Previously checked. Failed preflight never exposes the
  consumer. Under option B new semester consumers remain deferred.
- Apply interruption at replace/readback: H1 is recognized as committed and read back, the original
  raw config at G0 is recognized as not committed, and a third hash remains Partial without
  automatic overwrite/rollback.
- Feature matrix: legacy_global works with no Academic field or proof; semester_workspace requires
  only its own selected read-side evidence; enabled worker and study processing require their exact
  resolver/composition dependencies; Disabled preserves mappings and does not require worker roles.
- Retrieval-read and intake-worker PreparedRuntime objects cannot be reused across purposes or role
  sets. Synthetic environment/external-file credentials prove only the exact snapshot composition
  whose fresh principal/mapping preflight passed; they do not create durable current proof. The fake
  Settings composition cannot emit production admission or applied state.
- Runtime status is tied to a process-originated fingerprint and held lease: no observer is
  Not reported, confirmed exit is Stopped, an active old process is Running with previous settings,
  and a lock loser/object-only construction emits no applied record. Lease paths are derived from a
  validated UUID; serialized path tampering cannot redirect status reads.
- Projection tests vary every consumed input and confirm non-consumed labels, lease fields,
  checked_at, and poll_interval_minutes do not alter the affected worker fingerprint.
- A positive legacy interval such as YAML scalar 0.50 survives a toggle-only Apply byte-for-byte;
  changing that field rejects bool/fraction/out-of-range and accepts exact integers 1 and 1440.

After implementation, run affected focused tests, owning Ruff/type checks, and necessary UI syntax/harness checks. Reuse valid GUI-1/2/3 evidence; broaden only when changed call paths justify it. Report exact commands/results, candidate SHAs, unchanged GUI1–3/checker-pin hashes, and every Unsupported/Not reported condition. Never call partial provider/scheduler evidence full readiness.

## Complete Native/Gemini source packages

Both independent PLAN and FINAL reviews are required because this bundle changes provider identity/readiness and user-facing settings. The worker does not operate Chrome or send packets; the orchestrator owns the installed Native/Gemini workflow, source audit, original-response recovery, and disposition.

Provide a complete current-source union, not counts, split to fit the parent’s 120k-token budget with one shared manifest and per-file SHA-256:

1. **Authority/interaction:** this plan; local-settings-web-gui.md; local-settings-web-gui-interaction-mock.md; accepted GUI-1/2/3 plans; frozen design/specification and Behavior Contract sections for ownership, Academic, intake, MCP read-only, automation, restart.
2. **Academic/config:** every changed schema/loader/validator/intake/settings/provider-verification source and direct caller, plus direct Academic/config/provider tests/fixtures.
3. **Runtime/lifecycle:** every changed fingerprint/observation, runtime/CLI/runner/MCP/study-note source and direct caller, plus lifecycle, concurrency, crash, lock, CLI/MCP tests.
4. **Settings/UI:** every changed Settings route/status/UI and direct config/readiness dependencies, plus HTTP/UI harness/tests and changed contracts.
5. **Integration/source audit:** complete diff, file inventory, shared manifest/hashes, exact check output, fixtures, unchanged frozen/checker-pin evidence, cross-pack interfaces. Include every changed source/test even if referenced in another pack.

For review fixes, the same owner changes only finding scope, reruns affected checks, freezes new SHAs, and supplies the complete updated source union for parent-owned rereview. PLAN/FINAL GO is not overall acceptance, checker/global adoption, scheduler activation, commit, or push.

## Resolved human decision and remaining PLAN checks

1. **Academic semester and retrieval scope are separate. Human decision received:** add one academic.active_semester for Academic/worker context; keep retrieval.notion_lane and retrieval.semester as a separate explicit MCP search-scope selector. Do not synchronize or change the existing retrieval default legacy_global plus empty semester. A retrieval change has its own diff and readiness checks. This resolves and withdraws the prior single-authority proposal.
2. **Historical Verify transaction:** H0 binds the pre-history user candidate; G0 is the exact saved generation/raw-byte identity. A purpose-fixed bounded check uses one credential snapshot and exact mapping to observe a non-secret principal and resource/schema result. Backend-generated history plus choice evidence is added to final H1 before the redacted diff and human review. Apply rechecks G0/H1 and mapping/choice identity using existing session/CAS/journal semantics; no credential-value revision is involved. Each later runtime composition freshly authenticates the exact snapshot it will consume and compares it to the H1 expected principal.
3. **Runtime sidecar:** proposed path is Path(state_path(config)).parent / "runtime-observations", with owner-only directory, observations.json, writer.lock, and per-instance leases/<uuid>.lock derived from the instance UUID, not serialized as a path. Use POSIX flock on supported macOS/Linux, kernel release on crash, atomic replace/fsync, and Not reported on unsafe or unsupported observation. Native PLAN must confirm actual worker-lock and MCP-transport success callbacks are sufficient; unsupported platforms remain unverified.
4. **Desired interval:** persist a bounded GUI-edited value only; validate 1–1440 when that field changes. Toggle-only apply must retain the raw legacy scalar bytes. The actual fixed scheduler cadence and next run remain Not reported with manual setup instructions. No scheduler controller, daemon, or OS scheduler mutation is added.
5. **Study-note local MCP:** its submission transport does not consume Google/Notion credentials. Report actual transport startup independently; downstream processing readiness depends separately on the configured worker and role-specific checks.
6. **Course portal:** current resolver treats portal and optional upload as optional while GUI-4 lists a portal binding. Recommendation: allow edit/verification when configured, but do not block intake unless frozen contract or parent makes it mandatory.
7. **Study-request schema:** until accepted through the normal specification path, configured study_requests_data_source_id stays Not verified and blocks Study Notes processing readiness. No schema creation or guessing.
8. **Pending human Overall-status choice:** (A) save bindings as Configured, require fresh exact-snapshot preflight for each opted-in execution, and keep Overall at Confirmation needed until fresh execution evidence exists; or (B) defer new semester consumers pending a separate versioning design. This plan has not inferred the answer. Native R7 architecture GO does not settle this UX or grant implementation authorization. The exact stable Notion principal field and complete feature-specific mapping inventory remain for the full source-closed PLAN review.
9. **Role-test evidence reuse:** no CredentialRole definition change is proposed. Reuse the previously accepted `credential_roles.py` and `test_settings_credential_roles.py` review/test evidence while new Academic-service tests cover fixed purpose-to-role routes. Parent must decide whether the unchanged direct role test is repackaged as a current source dependency or cited as accepted prior evidence; do not modify, rename, or silently omit it. Include the complete intake/composition, Drive binding, retrieval engine, state reader, and study-note core/store closure before claiming all feature mappings were enumerated.

No product file changes before parent Native and Gemini PLAN GO plus implementation authorization.
