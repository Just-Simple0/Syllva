# Getting Started

[한국어](getting-started.ko.md) · [User Guide](README.md)

This page assumes an operator has already connected the workspace. If you still need to install the repository or configure provider IDs and credentials, use the [Operator Guide](../operator-guide/README.md).

## 1. Open the semester dashboard

The dashboard is your normal home. The supported order is:

1. **Courses** — enter the course you want to study.
2. **Continue studying** — a small set of current study shortcuts, not an automatic promise of visit-history ranking.
3. **To DO** — unfinished personal tasks and assignment-like items.
4. **Calendar** — dated academic schedules, assignments, exams, and personal items.
5. **Files** — material/intake entry points and items that still need attention.

Do not expect every session to appear in global navigation. Open a course first, then choose a session from that course's session list.

## 2. Open a course and session

A course page should give you a path to its sessions and related materials. A session is the place to understand one class meeting or study unit in context.

When metadata is missing, Syllva should surface that uncertainty instead of inventing a date, session number, or course binding from a filename.

## 3. Add your first material

When the semester intake preview is configured:

1. Put the PDF, slide deck, transcript, or other supported material in the configured semester upload location.
2. Open **Files** and confirm the new file/intake entry.
3. Provide or confirm the target course and any required session/date/material intent.
4. Submit the request explicitly.
5. Check the request status until it is applied or asks for more input.

Submitting is a human action. The system status is not an approval checkbox you should edit to force progress.

## 4. Ask a first study question

Use a connected AI client and ask a question that names a course/session or otherwise gives enough scope. For example:

> Explain divide and conquer from the verified sources for Algorithm session 3. Include one worked example and show which source sections you used.

A good Syllva-backed answer should either provide bounded source-aware context or tell you that the available evidence is insufficient. Missing evidence should not be filled with confident guesses.

## 5. Know the beta boundary

Syllva 0.1.3 is not a hosted service or one-click consumer app. Depending on your environment, you may still need operator work for:

- Drive/Notion credentials and IDs;
- local MCP registration in your AI client;
- remote authentication/networking;
- LMS credentials and scheduler activation;
- live provider/client validation.

For everyday use after setup, continue with [Daily Use](daily-use.md).
