# Hourly native Notion driver

This is the operating contract for a Codex heartbeat in the current project and task. The Python sidecar collects and projects; the heartbeat performs the official Notion connector operations. This is agent-driven synchronization, not a standalone unattended Python Notion client. No scheduler activation or live credential operation is authorized by this document.

## Fixed inputs and activation

Use `/Users/admin/Project/Syllva` and its `.review/venv311/bin/python` runtime. Read private local bindings from `.review/knu-lms-hourly/cloud-receipt.json`, last accepted projection/readback state, and the secret-free `config.json`/`auth-manifest.json`. Runtime metadata is mode0600 under a mode0700 directory. Never expose private records to review services. Compare the reviewed script hashes recorded at acceptance before execution; code drift stops the run for review, never triggers self-modification.

The run uses the private `knu-lms-semester-registry.v1` with the five verified
2026-2 academic courses and their exact origin, ID, name, code and term. The
separate observed extracurricular candidate remains outside `academic_expected`
and receives no child collection, projection or Notion write. Do not infer a
course from a title, add an unregistered course, mix semesters, or extend the
registry during a run. Only course/assignment/announcement/module metadata is
selected. No grades, submissions, attendance, original downloads, hidden
LearningX APIs or public sharing.

The hourly automation remains PAUSED until the final operational decision. The
preferred Aside branch reuses the already logged-in browser session for
same-origin read-only collection and does not read or enroll a token or Keychain
item. The legacy token branch remains separate: it requires its own valid
enrolled manifest and authorization, cannot authorize added courses, and is
never an automatic fallback from an Aside failure. Never run `enroll` from the
heartbeat or renew a token automatically.

## One complete run

1. Check acceptance hashes, the private semester registry, its canonical
   64-hex run hash, and the bound Aside tab/origin before execution. The Aside
   branch has no token or Keychain preflight and must not inspect `.env`, an
   auth manifest, or a Keychain item. The legacy token branch independently
   validates its fixed config/manifest, expiry and course scope before any
   credential or network operation.
2. Start `scripts/knu_lms_sync.py hold-lock --scope-hash <validated-hash>` in a retained local TTY process. Record the returned owner ID and keep its process alive through the whole run. If busy, do no dependent work. An interrupted durable reservation is never timed out, stolen or deleted; stop and request operator reconciliation. Before each mutation send `status` to the same live process and require the matching `lock_held` owner.
3. For the preferred branch, run the complete registry through the fixed
   Aside collector in the same runtime:

   ```text
   .review/venv311/bin/python scripts/knu_lms_probe.py collect --transport aside --aside-tab-id <private-bound-target-id> --registry <private-semester-registry.json> --owner-id <owner> --scope-hash <run-hash> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> --output <private-aside-snapshot.json>
   ```

   The collector checks the active owner/hash before starting the Aside
   subprocess and again after it returns. It requires one exact configured tab,
   same-origin bounded GETs, and a complete per-course result. It reads no
   Keychain item. Save metadata only in the private runtime. Do not reuse stale
   data to present a failed fetch as current. A failure leaves accepted state
   unchanged and causes no Notion mutation. The legacy token collector is a
   separate explicitly selected branch and cannot silently widen its manifest
   scope to the semester registry.
4. With official Notion tools, freshly verify the connected workspace, root privacy and semester identity, exact course binding and parent, datasource schema and its recorded containing toggle/semester ancestor, all view bindings/filters, and every previously bound row. Enumerate complete datasource rows including completed rows (not only To DO) and all pages/candidates needed to prove absence. Follow pagination; truncated/unavailable readbacks stop dependent writes. Missing formerly bound components are reconciliation, never permission to recreate them. First-time new assignment creation requires verified zero exact-key candidates with no ambiguous title/key candidates and no pending intent for that identity.
5. Fetch the course with discussion indicators and retrieve all child-block discussions including resolved discussions. Comments or pending suggestions in the source region block replacement. If their locations or availability are uncertain, conservatively block source-region replacement. A course-level comment may be preserved outside the region only when its attachment is proved; otherwise reconcile. Row bodies/comments and user properties are never rewritten.
6. Assemble the pure projection's readback using actual values and independently persisted last-applied values/hashes. Preserve source-field order only for presentation; hash canonical JSON. Normalize only documented connector formatting: exact canonical URL wrappers for `LMS 키`, omitted null date start/end, Unicode/line endings/trailing spaces in source blocks. Do not treat arbitrary missing fields as empty or discard meaningful user edits. Normalize UUID punctuation consistently for identity comparisons. Include tombstones for every missing previously bound row/course so the pure helper cannot classify it as newly absent. Pass the verified friendly course title separately from its API identity.
7. Run the semester `snapshot` and `project` commands with the same registry,
   owner and run hash, then pass the fresh readback. A conflict or any
   incomplete academic course stops dependent mutations; excluded candidates do
   not become a denominator or blocker. A whole-snapshot hash match alone does
   not permit skipping fresh USER/source conflict checks. The only renewable
   page region begins at the single exact `## 수집한 공지` and ends immediately
   before the single exact `## 학습 세션`. Require exactly one pair in the
   expected order and a hash matching the last accepted actual region; do not
   update the whole page or its child database tags. Apply only the plan's
	   SOURCE changes and validated new assignment rows. Existing `내 상태`,
	   `내 메모`, page bodies, learning sessions and completion remain untouched. A
   source edit by the user yields reconciliation.
   The registry project invocation must include the complete prior document and
   the KST observation date:

   ```text
   .review/venv311/bin/python scripts/knu_lms_sync.py project --registry <semester-registry.json> --owner-id <keeper-owner> --scope-hash <aside-readonly-registry-scope-hash> --snapshot <semester-snapshot.json> --readback <sanitized-notion-readback.json> --prior <semester-prior.json> --observed-on <YYYY-MM-DD> --output <plan.json>
   ```

   The prior document has one entry per academic course: retained accepted
   state for existing courses and explicit empty first-bootstrap state for new
   courses. Missing `--prior` or `--observed-on` is a fixed failure.
   For an existing, identity-bound course page that has no managed SOURCE
   hashes, use the explicit first-adoption helper before normal projection:
   `build_course_page_bootstrap_plan` requires the verified page identity,
   parent/privacy, full-body readback, comments readback, the existing body,
   and the `학습 세션` heading. An empty verified learning-session list is
   valid. It emits one bounded append before that heading and no prior hash.
   Persist the intent through the application journal, perform the single
   additive append, then call `verify_course_page_bootstrap` with the original
   body and semantic readback. Publish `last_applied_source_hash` only from
   the extracted SOURCE region beginning at `## 수집한 공지`, including
   `## 주차별 자료`, and ending before `## 학습 세션`, and
   publish the desired hash from the request; re-run normal projection. Duplicate
   headings, comments, missing body/identity, or an uncertain dispatch write
   zero and require reconciliation. Never invent a session or a prior hash.
8. Before each cloud change persist a durable intent containing owner, component identity, prior ID/hash, exact desired values/hash and status. After a tool returns, persist its returned ID/result immediately by merging that component's receipt. Verify the actual result before accepting it. A timeout/transport error after dispatch is indeterminate: record that state, stop dependent work and inspect the exact target later; never blindly retry a create. Schema or view drift is reconciliation, not automatic rebuilding. No deletion or re-parenting.
9. Freshly verify final row properties, source-region content, preserved USER content, datasource/view bindings and the five-section root layout. Publish accepted source values/hashes and announcement retention state only after verification; preserve prior seen announcements outside the collection window. Store both `last_applied_desired_region_hash` from the exact successful request and `last_applied_source_hash` from independently verified actual Notion readback. Notion removes presentation escapes and blank lines; the desired hash permits no-op only after the fresh actual hash matches the last actual hash. Never copy a desired hash into the actual-hash field or use either hash to waive semantic readback verification. Never replace accepted data with partial results. The original setup callout is dated historical evidence; do not claim each hourly run refreshes it unless a separately bound status region is implemented and verified. The user's To DO/calendar derive from the changed rows automatically.
10. Wait until every collect process and cloud request definitively ends. Only the still-live original keeper may receive `release`, mark its reservation completed and exit. On any uncertain in-flight operation, crash or missing keeper leave the durable reservation active and report reconciliation. An ordinary fully settled failure with no uncertain calls may cleanly release. Never clear a reservation by editing its JSON.

## Notification and recovery

Keep unchanged, successful or non-actionable runs quiet. Notify for newly collected meaningful changes, a newly actionable failure or required human action. Record only a secret-free stable failure/change signature and do not duplicate an unchanged failure notification every hour. Include a usable Notion link for changes and one concrete remedy for a failure. Do not claim phone delivery without a receipt.

The root driver persists only this mode0600 JSON document under the mode0700
runtime directory, for example
`.review/knu-lms-hourly/notification-state.json`:

```json
{"version":1,"last_signature":"<64 lowercase hex>|null","last_kind":"change|failure|null"}
```

`decide_notification` returns `notify`, `kind`, `signature`, `reason`, and the
next state. The root driver persists that returned state after the run reaches
a settled result.
The same signature and kind produce `notify=false`; a changed actionable result
produces `notify=true`. This state controls duplicate suppression only; the
existing Codex heartbeat and official notification integration remain the
delivery mechanism.

Expired/missing auth needs user enrollment, not token discovery. An unavailable connector, unverifiable sharing, missing prior binding, ambiguous create result, source edit/comment, changed script or interrupted reservation needs reconciliation. Preserve all prior accepted data while blocked. Do not broaden permissions, modify code, create replacement databases or automatically run reviews to clear these conditions.

The guard protects cooperating local runners. Fresh readback does not provide an atomic Notion compare-and-swap against simultaneous external user edits; keep changes narrowly scoped and recheck actual results. This limitation must not be described as absolute overwrite prevention.
