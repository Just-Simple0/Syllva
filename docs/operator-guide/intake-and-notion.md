# Semester Intake and Notion

[한국어](intake-and-notion.ko.md) · [Operator Guide](README.md)

The v0.1.3 beta includes a **preview** semester intake lane. Preview means the code path exists and is tested in-repository; it does not mean your live Drive/Notion workspace is provisioned automatically.

## Current-semester model

The intake preview uses five operational Notion data sources per configured semester:

1. Academic Courses
2. Sessions
3. Materials
4. File Intake
5. Input Request

These are separate from the existing global-ID data sources used by the legacy read-only retrieval lane.

## User-facing intake flow

```text
Drive semester upload location
  → File Intake observation
  → Input Request
  → user Submitted=true
  → worker claim/process
  → Applied / Needs Input / Reconcile Required / Failed / Cancelled
```

`Submitted` and `Cancelled` are user-owned checkboxes. `Request Status` is system-owned. Do not build an operator shortcut that writes `Request Status=Applied` as a substitute for actual processing/readback.

## Worker commands

```bash
uls sync --max-jobs 20
uls process --max-jobs 20
uls run --max-jobs 20
uls jobs --limit 20
```

- `sync` discovers/projects current work without processing it.
- `process` handles durable work already discovered.
- `run` performs both in one bounded tick.
- All worker paths share the local single-active-worker lock.

The current CLI accepts bounded `--max-jobs` values. Choose a small batch during first live validation.

## Notion safety

Before enabling writes, validate:

- exact parent/data-source IDs;
- expected schema/property names and relation targets;
- that human-owned fields are not writable through the automation path;
- privacy/ownership requirements for created pages;
- readback after mutations before claiming success.

If a provider response is lost or ambiguous, re-read external state and reconcile. Do not blindly create a duplicate page or repeat a mutation whose outcome is unknown.

## Dashboard UX

The intended semester dashboard order is:

1. Courses
2. Continue studying (up to three real shortcuts in the current UX definition)
3. To DO
4. Calendar
5. Files

Sessions remain under their course rather than becoming global navigation items.

## No implicit lane fallback

If intake-preview identities are incomplete, do not silently fall back to a different retrieval or legacy write path and call the result successful. Keep readiness for intake and readiness for read-only retrieval as separate facts.
