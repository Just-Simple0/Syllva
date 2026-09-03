---
name: activity-help
description: Help with an assignment/activity under official instructions as hard constraints.
behavior_contract_version: 1
behavior_contract_hash: sha256:83a0362614e634249f4b8204c7eb57009b54f01ebda749579b7caaf8d51bdc14
---

# Activity help (ACTIVITY intent)

Projection of `contracts/study-behavior.md`.

## When to use

The user asks about an assignment/activity — e.g. "HW2 요구사항 다시 확인해줘".

## Flow

1. Resolve the activity, then call `uls.get_activity_context`.
2. Treat official instructions as the highest-authority hard constraints.
3. For submitted code, use the exact GitHub `Submission Ref` returned — never current `main`.

## Rules

- Respect official instruction constraints in any generated advice/solution.
- Distinguish official SOURCE constraints from your AI suggestions.
- MCP is read-only; never attempt to commit/push or modify sources.
