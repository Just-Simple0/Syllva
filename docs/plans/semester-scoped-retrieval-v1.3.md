# Semester-scoped read-only retrieval v1.3

Status: proposed additive v1.3 plan; frozen v1.2 remains authoritative and unchanged.

## Goal

Allow the read-only MCP retrieval runtime to use one explicitly selected
`notion.semester_workspaces` row (initially `2026-2`) without copying those
provider IDs into the legacy seven global Notion fields and without creating
placeholder databases.

The existing v1.2 global retrieval composition remains the default.

## Configuration contract

Extend `retrieval` with:

```yaml
retrieval:
  notion_lane: legacy_global        # existing/default behavior
  semester: ""                     # must be empty in legacy_global mode
```

Semester mode is explicit:

```yaml
retrieval:
  notion_lane: semester_workspace
  semester: "2026-2"
```

Validation rules:

- `notion_lane` is exactly `legacy_global` or `semester_workspace`.
- `legacy_global` requires an empty `semester`; no hidden selector is kept.
- `semester_workspace` requires one valid semester string, exactly one
  `notion.semester_workspaces` row for it, and at least one configured Course
  whose Course Key belongs to that semester.
- The existing intake validation remains responsible for the required
  Courses/Sessions/Materials/current-intake provider IDs and duplicate-semester
  rejection.
- No semester lookup falls back to the legacy `*_db_id` fields.

## Runtime and adapter composition

`runtime.build_retrieval` keeps the current legacy construction when
`notion_lane=legacy_global`.

For `semester_workspace`, it resolves exactly the configured semester row and
constructs the read-only Notion reader with direct data-source IDs:

- `courses -> academic_courses_data_source_id`
- `sessions -> sessions_data_source_id`
- `materials -> materials_data_source_id`
- `material_usage -> material_usage_data_source_id` only when configured

The reader accepts an optional direct data-source map plus the selected
semester. In direct mode it never calls `databases.retrieve`, and a missing
kind never consults a legacy database ID.

The direct-mode reader enforces the selected semester before returning any
Course, Session, or Material row or resolution metadata:

- every Course row must have a canonical `Course Key` whose parsed semester
  equals the selected semester;
- every Session and Material row must have exactly one non-truncated `Course`
  relation;
- that relation must resolve to a live page in the selected Courses data
  source, and that Course must pass the same canonical `Course Key` semester
  check.

This guard applies to direct-ID fast paths as well as catalog/alias reads. In
particular, `resolve_entity(query="<session-id>", entity_type="session")`
cannot return resolved Session metadata before the Session's Course relation
has passed the selected-semester guard. A missing, malformed, ambiguous, or
cross-semester Course relation fails closed. The legacy reader path keeps its
existing behavior.

## Missing optional semester data sources

The current semester workspace has no required Exam or Activity mapping, and
Material Usage is an optional v1.3 extension.

- Missing `material_usage_data_source_id` does not block transcript-only
  Session retrieval. The engine records a `SOURCE_UNAVAILABLE` warning and
  omits material-usage-derived evidence. It does not interpret the omission as
  a verified empty Material Usage database.
- Direct Exam/Activity resolution or context requests fail with
  `SOURCE_UNAVAILABLE` while those mappings are absent.
- No optional-kind failure widens to the legacy lane.

This is a reduced-evidence fail-closed path: unavailable authority cannot
create evidence or capabilities.

## Doctor semantics

`uls doctor --live` probes the same selected retrieval lane as runtime.
Semester mode chooses a configured Course from the selected semester instead
of blindly using `config.courses[0]`. Empty legacy Notion IDs are therefore
irrelevant when the semester lane is explicitly selected.

The Drive probe remains read-only and unchanged.

## Invariants

- MCP search remains read-only.
- SOURCE / AI / USER ownership is unchanged.
- Human approval / Verified rules are unchanged.
- Frozen v1.2 files are not edited.
- The default config behavior remains the legacy global lane.
- There is no implicit semester selection and no cross-lane fallback.

## Acceptance checks

1. Existing legacy retrieval/config tests remain unchanged and pass.
2. Semester mode queries the exact configured direct data-source IDs and never
   calls Notion database discovery.
3. Invalid lane/semester, missing selected workspace, or selected semester with
   no configured Course fails validation before serving requests.
4. A cross-semester Course row in the selected Courses source fails closed,
   and an exact-ID Session whose Course relation resolves to a Course from a
   different semester cannot produce a successful public `resolve_entity`
   result.
5. Missing Material Usage yields transcript-only Session context with an
   explicit warning and no material-derived capability.
6. Missing Exam/Activity fails unavailable and never touches legacy IDs.
7. Live doctor probes a Course in the selected semester and does not require
   legacy Notion IDs in semester mode.
8. Focused config/adapter/runtime/retrieval tests, relevant contract tests,
   Ruff, mypy, Behavior Contract hash/projection checks, and the independent
   final review all pass before commit.
