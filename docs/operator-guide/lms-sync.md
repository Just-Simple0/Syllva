# LMS Sync

[한국어](lms-sync.ko.md) · [Operator Guide](README.md)

LMS support is an optional, read-only sidecar. For the verified 2026-2 KNU
semester rollout, use the bounded Canvas API path below. The older single-course
token path and the Aside collector remain available for compatibility with older
records and tests.

## Safety rule: paused by default

Keep LMS scheduling **paused** until all of the following are true:

- the user has explicitly authorized the credential use;
- the exact five-course registry and API scope hash are verified;
- the credential is current and valid for the intended window;
- a read-only collection succeeds for all five academic courses;
- the target Notion/application mapping has been validated;
- the operator has reviewed the first result before enabling recurrence.

Do not enroll credentials or activate a heartbeat merely because repository
scripts exist.

## 2026-2 Canvas API preparation and enrollment

The registry must be the existing private `knu-lms-semester-registry.v1` with
five `academic_import: true` courses whose `id`, `name`, `code`, and `term` have
been verified. The non-academic candidate stays in the registry for audit
binding but is excluded from the API denominator and result.

Prepare the secret-free binding with an issued/expiry window no longer than 30
days:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py prepare-semester-api \
  --registry <semester-registry.json> \
  --issued-at <RFC3339> --expires-at <RFC3339> --confirm PREPARE
```

Then enroll once from a local TTY:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py enroll-semester-api \
  --registry <semester-registry.json> --confirm ENROLL
```

Enrollment prompts with terminal echo disabled and stores the PAT in the
explicit OS-native Keychain backend under the stable semester account. The
token is not accepted as an argument and is not written to config, the
manifest, logs, or repository files. Config and manifest validation happens
before Keychain access and binds the exact registry hash, semester, origin,
resource list, backend, API scope hash, and issued/expiry window.

Canvas token permissions are not narrowed by this flow. The client enforces
the read-only boundary: it calls the existing `probe.run_probe` only with
bounded GET requests for course identity, assignments, announcements, and
modules. Files, grades, submissions, attendance, downloads, and unregistered
courses are outside the API resource policy.

## Bounded semester collection

Start or verify the normal reservation, then collect through the enrolled API
binding:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py collect-semester-api \
  --registry <semester-registry.json> --owner-id <owner> \
  --scope-hash <canvas-api-readonly-scope-hash> \
  --output <semester-api-snapshot.json>
```

The collector checks the owner and exact API scope before Keychain access and
again after all five calls. Each course is identity-validated by
`probe.run_probe` against the registry's ID, name, code, and term. The output
uses the existing semester snapshot shape with
`provenance: canvas-api-readonly`; incomplete academic coverage blocks
projection.

Run the pure validation and projection with the same explicit transport:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py snapshot \
  --transport canvas-api-readonly --registry <semester-registry.json> \
  --owner-id <owner> --scope-hash <canvas-api-readonly-scope-hash> \
  --input <semester-api-snapshot.json> --output <validated-snapshot.json>
.review/venv311/bin/python scripts/knu_lms_sync.py project \
  --transport canvas-api-readonly --registry <semester-registry.json> \
  --owner-id <owner> --scope-hash <canvas-api-readonly-scope-hash> \
  --snapshot <validated-snapshot.json> --readback <readback.json> \
  --prior <prior.json> --observed-on <YYYY-MM-DD> --output <plan.json>
```

## Interrupted reservation recovery

An active `active-run.json` blocks API enrollment and collection, even when no
process currently holds the OS lock. Use the operator-only two-step path:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py reconcile-check \
  --owner-id <old-owner> --scope-hash <old-scope-hash>
```

Review the sanitized candidate and local evidence to confirm that every prior
operation is settled. Only after that human decision, apply the exact candidate:

```text
.review/venv311/bin/python scripts/knu_lms_sync.py reconcile-apply \
  --owner-id <old-owner> --scope-hash <old-scope-hash> \
  --candidate-hash <candidate-hash> --confirm-settled yes
```

Apply reacquires the OS lock, recomputes the candidate, rejects drift, and
audit-completes the old record. It never reclaims by age, deletes state, or
replaces the record. A heartbeat must never run either reconciliation command
or manufacture the human confirmation. Start a fresh normal reservation only
after successful audit completion.

## Compatibility and data behavior

The legacy `enroll`, single-course token collector, and Aside registry
collector remain callable for existing bindings. Aside is not the transport
for the 2026-2 API rollout, and an Aside failure is never an automatic reason
to widen or replace the API binding.

- Keep course identity explicit; names are not sufficient durable bindings.
- Preserve partial/failed course results rather than turning one incomplete
  course into a successful semester.
- Do not invent dates for assignments that have no date.
- Do not silently change user completion/submission state.

Detailed evidence and driver rules live under `docs/plans/`.
