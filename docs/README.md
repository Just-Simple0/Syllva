# Syllva Documentation Sources

[한국어](README.ko.md) · **[Read the official documentation site](https://just-simple0.github.io/Syllva/)**

This directory stores the version-controlled Markdown sources behind the public documentation site plus historical engineering records.

For normal reading and navigation, use the **official Starlight documentation site**. It provides search, sidebar navigation, language switching, and a single reader-facing hierarchy.

## Public documentation sources

| Section | Purpose |
| --- | --- |
| [`user-guide/`](user-guide/README.md) | Dashboard, daily study, file intake, AI use, troubleshooting |
| [`operator-guide/`](operator-guide/README.md) | Installation, provider configuration, intake, MCP, LMS, operations |
| [`concepts/`](concepts/README.md) | Architecture and trust model |
| [`reference/`](reference/README.md) | CLI, statuses, MCP tools |

Every public document in these sections has a Korean companion with the same basename plus `.ko.md`. The docs-site build validates those pairs before generating the bilingual Starlight content tree.

## Version note

Syllva is currently **0.1.3 beta** and is not a public package release. The frozen **1.2** documents at the repository root describe the core behavior/design protocol. Package version and protocol version are intentionally separate.

## Engineering records

- `plans/` — implementation plans, validation plans, and operational evidence.
- `ux/` — UX definitions, review material, and readback records.

These records remain in their original form for maintainers and reviewers. They are linked from the official site as an engineering archive but are not mixed into beginner navigation.
