---
name: activity-help
description: Help with an assignment/activity under official instructions as hard constraints.
behavior_contract_version: 2
behavior_contract_hash: sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a
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
- Keep SOURCE/USER/AI/External provenance distinct: official/professor material is
  SOURCE, the learner's notes are USER, interpretation is AI, and general knowledge
  is External.
- Distinguish official SOURCE constraints from your AI suggestions.
- Typed official locator/evidence metadata identifies the Activity hard constraint while
  SOURCE/USER/AI/External provenance remains unchanged.
- If a direct official instruction conflicts with a professor recommendation or other
  source, the official instruction governs and the conflict is disclosed.
- If official Activity instructions are missing, partial, or truncated, disclose that
  instruction coverage is incomplete. Do not claim to have the complete instruction
  set or infer omitted requirements, including after serialization.
- MCP is read-only; never attempt to commit/push or modify sources.
