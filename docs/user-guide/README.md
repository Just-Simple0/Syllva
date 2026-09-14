# Syllva User Guide

[한국어](README.ko.md)

This guide is for the person **studying with Syllva**. It intentionally avoids most provider IDs, credential setup, scheduler configuration, and internal state-machine details. Those belong in the [Operator Guide](../operator-guide/README.md).

## Start here

1. [Getting started](getting-started.md) — understand the workspace and complete a first study loop.
2. [Daily use](daily-use.md) — dashboard, courses, sessions, materials, To DO, and calendar.
3. [Studying with AI](studying-with-ai.md) — ask for explanations, review, exam prep, and source verification.
4. [MCP and clients](mcp-and-clients.md) — what supported AI clients can and cannot do.
5. [Troubleshooting](troubleshooting.md) — common user-facing problems.

## The mental model

Your normal navigation is:

```text
Semester dashboard
  ├─ Courses
  │    └─ Course
  │         └─ Sessions
  │              └─ Session / materials / notes
  ├─ Continue studying
  ├─ To DO
  ├─ Calendar
  └─ Files
```

Syllva is not meant to turn every session into a global menu item. Sessions remain under their course, while the dashboard gives you only a small set of high-value entry points.

## Before you begin

A usable workspace must already have been provisioned and validated by an operator. If you cloned the repository yourself and have not configured Drive/Notion/provider connections, follow the [Operator Guide](../operator-guide/README.md) first.

Syllva 0.1.3 is beta software. A guide saying that a workflow is supported does not mean that a live external provider or AI client has already been validated in your environment.
