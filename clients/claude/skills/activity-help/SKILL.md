---
name: activity-help
description: Help with an assignment/activity under official instructions as hard constraints.
behavior_contract_version: 3
behavior_contract_hash: sha256:a2d19ec6f7eb22db3121651ea188f64805a9ec32ac7f8d681d40da1c46bec51b
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

## Assignment instruction regions

Every evidence item carries `usage_policy` and `assignment_decision`. Use an item with
`usage_policy = NO_ANSWER_GENERATION` (assignment instructions, official Activity instructions, or
unresolved/stale assignment status) only to confirm that a requirement exists, where it comes from,
and to orient or summarize it. Never use it to write answers, code or submissions. A requirement
that is absent from a default context is not proof that it does not exist.
