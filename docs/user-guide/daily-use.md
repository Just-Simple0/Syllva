# Daily Use

[한국어](daily-use.ko.md) · [User Guide](README.md)

## Dashboard

Use the semester dashboard as the single top-level study entry point.

- **Courses**: enter the current semester's course pages.
- **Continue studying**: keep a small number of useful session shortcuts (maximum three in the current UX definition).
- **To DO**: view unfinished tasks and assignment-like items.
- **Calendar**: view dated academic/personal items, including completed items when the view is configured that way.
- **Files**: review new materials, intake requests, and items needing clarification.

## Courses and sessions

Navigate **Dashboard → Course → Session**. Session records stay in the Sessions data source and are filtered by the exact course relation. This keeps navigation usable as the semester grows.

A session may reference source material, AI-generated study notes, and user notes, but those categories should remain distinguishable. Treat a generated explanation as a study aid, not as a replacement for the original source.

## Adding materials

When intake is enabled, the normal user flow is:

```text
Semester upload location
    → Files / File Intake
    → Input Request
    → explicit submit
    → request status
```

You may be asked for course, session/date, material type, or intent. Give the missing information rather than trying to encode it into a filename and expecting Syllva to infer everything.

### User-controlled actions

- **Submit**: confirms that the request should be processed.
- **Cancel**: tells the worker not to continue the request.

Processing/status fields are system-owned projections. Do not edit a status value to impersonate an approval or successful completion.

## To DO and calendar

The Notion workspace can show To DO and Calendar as separate views over a shared user-editable schedule source. This avoids duplicate entries.

- Use **To DO** for unfinished actionable items.
- Use **Calendar** for items with dates, including academic schedules, assignments, exams, and personal plans.
- Do not invent an exam date because a course appears to need one.
- User completion remains a user action unless a separate integration explicitly owns that field.

## If processing needs help

Common outcomes include:

- **Needs Input** — add the missing course/session/date/intent information.
- **Reconcile Required** — an operator must verify an uncertain external write/readback result.
- **Failed** — ask the operator to inspect the diagnostic cause before retrying.
- **Cancelled** — the request will not continue unless you intentionally submit a new/valid request flow.

For user-facing fixes, see [Troubleshooting](troubleshooting.md). For worker commands and recovery, use the [Operator Guide](../operator-guide/README.md).
