# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Initial repository scaffold derived from the frozen v1.2 design and implementation
  specifications (repository layout §3, domain types §6, StateStore §7–8,
  EphemeralStore §9, Retrieval Engine §20, MCP tools §24, Behavior Contract §31).
- Fully implemented normative `Locator` grammar, canonical AST, serialization, and
  typed containment (spec §6.3).
- Domain enums, `SourceRef`, `SourceFingerprint`, provenance, and error taxonomy.
- StateStore / EphemeralStore protocol contracts and SQLite initial migration.
- Config schema and example config / `.env` templates.

## [1.2.0] — Frozen design baseline

- v1.2 architecture and implementation specifications frozen for implementation.
