# Syllva Documentation

[한국어](README.ko.md)

This directory contains two different kinds of material:

1. **Public documentation** for people who want to use or operate Syllva.
2. **Engineering records** under `plans/` and `ux/` that preserve design/review history.

If you are new to the project, start with the public documentation below rather than the review records.

## Choose your path

| I want to… | Start here |
| --- | --- |
| Use the Notion workspace and study with Syllva | [User Guide](user-guide/README.md) |
| Install/configure providers, workers, MCP, LMS, or schedulers | [Operator Guide](operator-guide/README.md) |
| Understand why Syllva is designed this way | [Concepts](concepts/README.md) |
| Look up exact commands, states, or MCP tools | [Reference](reference/README.md) |

Every public guide in these sections has a Korean companion with the same basename plus `.ko.md`.

## Version note

Syllva is currently **0.1.3 beta** and is not a public package release. The frozen **v1.2** documents at the repository root describe the core behavior/design protocol. Package version and protocol version are intentionally separate.

## Engineering records

- `plans/` — implementation plans, validation plans, and operational evidence.
- `ux/` — UX definitions, review material, and readback records.

These files are useful to maintainers and reviewers, but they are not the supported beginner path. Historical records are preserved in their original language and form unless a later maintenance task explicitly revises them.
