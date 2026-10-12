---
name: verify-source
description: Verify a claim against normalized/original source evidence, not AI enrichment.
behavior_contract_version: 3
behavior_contract_hash: sha256:a2d19ec6f7eb22db3121651ea188f64805a9ec32ac7f8d681d40da1c46bec51b
---

# Verify a claim (VERIFY intent)

Projection of `contracts/study-behavior.md`.

## When to use

The user asks whether something is actually true — e.g. "교수님이 정말 시험에 나온다고
말했어?".

## Flow

1. Call `uls.verify_claim` (course_key + claim + optional entity_hint).
2. Base the verdict on the returned factual source evidence (normalized/original).
3. If necessary, fetch the original page/timestamp via `uls.get_source_chunk`.

## Rules

- Keep SOURCE/USER/AI/External provenance distinct: supplied professor/official
  evidence is SOURCE, the learner's notes are USER, interpretation is AI, and general
  knowledge is External.
- AI enrichment may only hint where to look; it can never satisfy verification.
- If the source does not support the claim, state that it is **not** supported.
- Cite the locator that supports (or fails to support) the claim.
- For Activity instructions, direct official constraints govern conflicting
  recommendations, while typed locator/evidence metadata remains separate from global
  SOURCE/USER/AI/External provenance.
- If official Activity instructions are missing, partial, or truncated, disclose
  incomplete coverage and never claim to have the complete instruction set or infer
  omitted requirements, including after serialization.

## Assignment instruction regions

Every evidence item carries `usage_policy` and `assignment_decision`. Use an item with
`usage_policy = NO_ANSWER_GENERATION` (assignment instructions, official Activity instructions, or
unresolved/stale assignment status) only to confirm that a requirement exists, where it comes from,
and to orient or summarize it. Never use it to write answers, code or submissions. A requirement
that is absent from a default context is not proof that it does not exist.
