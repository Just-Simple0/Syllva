# Syllva Operator Guide

[한국어](README.ko.md)

This guide is for the person who installs Syllva, configures external providers, runs workers, registers MCP clients, and decides when optional automation is safe to enable.

Syllva 0.1.3 is beta software. Prefer explicit validation and fail-closed operation over making a partially configured system appear ready.

## Recommended path

1. [Installation](installation.md)
2. [Configuration](configuration.md)
3. [Semester Intake and Notion](intake-and-notion.md)
4. [MCP Clients](mcp-clients.md)
5. [LMS Sync](lms-sync.md) — optional
6. [Operations and Recovery](operations.md)

## Operator responsibilities

An operator is responsible for:

- keeping secrets out of the repository, prompts, and client instructions;
- validating exact Drive/Notion/GitHub identities before enabling writes or retrieval;
- keeping worker and MCP credentials separated where required;
- running `uls doctor` and relevant read probes before activation;
- keeping the LMS scheduler paused until its credential/connection gates pass;
- treating uncertain external write outcomes as reconciliation work, not automatic success;
- preserving human-owned verification/scope decisions.

## Version model

- **Package:** 0.1.3 beta, unpublished.
- **Core behavior protocol:** 1.2, frozen.

Do not infer package maturity from the protocol number.
