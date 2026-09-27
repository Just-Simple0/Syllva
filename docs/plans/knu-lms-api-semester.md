# KNU Canvas API semester + source-intake plan

Date: 2026-09-27. Status: **design revision only; current candidate implementation is not accepted**.

This plan replaces the over-broad candidate with one bounded feature bundle.
Interrupted reservation recovery is specified separately in
`knu-lms-reservation-reconciliation.md` and is not part of this bundle.

## Goal

Use the Canvas REST API as the only LMS collection transport for the exact five
2026-2 academic courses already marked `verification_state=api_code_verified`,
discover real professor-provided files, and connect an eligible source to the
existing private Drive intake without weakening ULS source/approval rules.

The first live acceptance target is deliberately small:

1. all five academic courses pass exact ID/name/code/term validation;
2. one real, supported Canvas source is discovered and downloaded read-only;
3. the exact bytes are archived privately in Drive with Canvas provenance and
   then discovered by the existing intake worker;
4. the normal USER-owned Input Request flow decides course/kind/session details;
5. if the real source is a transcript-compatible source, the existing worker
   may create or bind one real Session and retrieval must prove it end-to-end;
6. if only PDF/material sources exist, the bundle may create a real Material,
   but it must **not** fabricate a Session merely to make the Session count nonzero.

## Fixed scope

- Academic denominator: exactly the five `academic_import=true` registry rows
  whose verification state is exactly `api_code_verified`.
- The observed non-academic candidate remains excluded from child collection,
  source import, projection, and acceptance counts.
- Canvas methods are GET-only. No grades, submissions, attendance, progress
  mutation, mark-read, hidden LearningX endpoints, or Canvas writes.
- Aside/browser collection is not a fallback. A Canvas API failure stays a
  Canvas API failure.
- Existing hourly driver semantics remain the accepted operational contract
  until this bundle passes plan review, implementation review, and live readback.
- MCP search remains read-only. SOURCE/AI/USER ownership and human approvals are unchanged.

## Credential and authorization design

Separate the durable secret from the time-bounded human authorization.

- The Canvas PAT is entered once through a local no-echo TTY and stored only in
  the explicit OS-native Keychain/Credential Manager item for the semester.
- The token itself is not given a synthetic 30-day lifetime by ULS. It remains
  enrolled until the user rotates/revokes it or the provider rejects it.
- A secret-free authorization lease binds the exact registry hash, origin,
  transport, fixed resource allowlist (`course`, `assignments`, `announcements`,
  `modules`, `files`), Keychain account identifier, and API scope hash. Default
  lease maximum: 30 days.
- Lease renewal is a separate explicit human action. It revalidates the same
  binding and existing credential presence but does not display, rewrite, or
  require re-entry of the token.
- Any registry/origin/resource/scope change requires a new reviewed binding;
  token absence or provider rejection requires re-enrollment/rotation.
- Config/lease validation occurs before Keychain access or network requests.

This gives the desired one-time credential setup while retaining an explicit,
renewable authorization boundary.

## Canvas collection contract

For each academic course, validate the course object first, then collect bounded
paginated metadata for assignments, announcements, modules/module items, and
course files. Module position or a name such as `1주차` remains presentation
metadata only and is never promoted to Session No, lecture date, completion, or
learning status.

Initial source eligibility is intentionally narrow:

- the module item is `type=File` and exposes a stable Canvas `content_id`;
- that ID resolves through the course-scoped Files API and belongs to the exact
  validated course;
- the file is currently accessible to the authenticated student;
- MIME/type and size are within the existing deterministic intake support
  (initially PDF and transcript-compatible text/markdown only);
- the file is not a duplicate candidate already imported under the same Canvas
  source identity and content hash.

The first bundle does not bulk-import every course asset. Unsupported or
unlinked files remain metadata candidates for later policy expansion.

Official Canvas API facts used by this design:

- `GET /api/v1/courses/:course_id/files` returns a paginated course file list.
- `GET /api/v1/courses/:course_id/files/:id` returns the course-scoped File object.
- module items may be `type=File` and expose the linked content through `content_id`.
- `GET /courses/:course_id/files/:file_id/download` downloads an accessible file.

Sources: Canvas Files API and Modules API:
`https://canvas.instructure.com/doc/api/all_resources.html` and
`https://canvas.instructure.com/doc/api/modules.html`.

## Download safety

- Start only from the exact configured Canvas HTTPS origin and validated course/file IDs.
- Bound pagination, total candidates, request count, wall-clock time, declared size,
  streamed bytes, and redirect count.
- A cross-origin signed download redirect may be followed only over HTTPS and
  must never receive the Canvas Authorization header.
- Hash the exact downloaded bytes before any Drive write. A short, changed,
  oversized, malformed, or indeterminate download fails closed.
- Do not persist provider download URLs or bearer material in logs/snapshots.

## Drive archive and provenance bridge

Canvas bytes become a professor SOURCE only after an exact private archive copy
is created and read back successfully in the registered semester/course Drive
tree. Use a dedicated compact Drive marker for Canvas-imported SOURCE; do not
reuse the AI-study-note marker.

The bridge records a durable source tuple containing at least:

`semester, course_id, canvas_file_id, module_id/item_id when present, original_name,
mime_type, provider_updated_at, byte_size, sha256, archived_drive_file_id, imported_at`.

The Canvas source key is stable across retrievals. The Drive object is the
private immutable archive used by ULS normalization/retrieval, while the Canvas
identity remains provenance. Exact byte readback and private-owner/share checks
are required before the archive is exposed to intake.

For this first bundle, an already imported Canvas source with a changed content
hash is **not automatically refreshed**. It stops with reconciliation/versioning
required. A later design must preserve one semantic source/entity across Canvas
revisions without destructively overwriting the previous Drive version.

## Existing intake boundary

After successful archive/readback, place the archive in the registered
course-specific upload location when one exists; otherwise use the registered
semester upload location and let the normal ASSIGN_COURSE/Input Request path
resolve the course. Normal discovery then creates/reuses the File Intake identity.
Do not bypass Input Request, directly mark an item Ready, or write Session fields
from LMS module metadata.

- PDF -> existing MATERIAL_PDF path; course can be proposed from the registered
  course folder, while material role remains USER input.
- Transcript-compatible text/markdown -> existing TRANSCRIPT path; actual date,
  NEW/EXISTING Session choice, exact existing Session relation, and optional
  actual Session No remain USER input under the current intake contract.

Therefore a successful PDF import proves real SOURCE -> Material, not Session.
Session E2E is accepted only when a real transcript-compatible source exists and
the USER-owned request supplies the required real Session data. Otherwise the
system reports the missing Session-producing source and stops without fabrication.

## Implementation slices after plan acceptance

1. Remove the current candidate's generic `verification_state="verified"`
   acceptance and restore exact `api_code_verified` for academic API collection.
2. Restore the accepted hourly-driver semantics in the eventual implementation
   patch; keep API candidate instructions in this plan/operator addendum until
   final acceptance.
3. Split credential enrollment from renewable authorization lease.
4. Add bounded course Files + module-linked File discovery/download to the API probe.
5. Add the Canvas-to-private-Drive archive/provenance bridge with idempotent
   same-hash reuse and changed-hash fail-closed behavior.
6. Wire archive output only into the existing intake discovery surface.
7. Add focused contract/unit/integration tests, including redirect credential
   stripping, byte limits, duplicate/revision handling, provenance preservation,
   excluded-course denial, and no Session inference from week/module position.
8. Only after review, perform a bounded live five-course metadata read and import
   one eligible source. No live mutation is part of plan acceptance.

## Acceptance evidence

Plan review must confirm frozen-contract compatibility before implementation.
Implementation acceptance then requires focused pytest, Ruff, targeted mypy,
`git diff --check`, and independent web ChatGPT review under an authorized
sanitized/private-code review route. No commit or push occurs before those gates.
