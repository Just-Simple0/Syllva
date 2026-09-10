---
name: exam-prep
description: Prepare for an exam strictly within confirmed scope; label provisional scope.
behavior_contract_version: 2
behavior_contract_hash: sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a
---

# Exam prep (EXAM intent)

Projection of `contracts/study-behavior.md`.

## When to use

The user asks about exam scope or practice — e.g. "중간고사 확정 범위에서 문제 내줘".

## Flow

1. Resolve the exam, then call `uls.get_exam_context`.
2. Inspect `scope.status`:
   - `confirmed` (`hard_boundary=true`): stay strictly inside the supplied evidence.
   - `provisional` (`hard_boundary=false`): say scope is **not yet confirmed** and treat
     context as provisional.
3. Generate study material / questions only from the supplied evidence boundary.

## Rules

- Keep SOURCE/USER/AI/External provenance distinct: supplied professor/official
  evidence is SOURCE, the learner's notes are USER, interpretation is AI, and general
  knowledge is External.
- The engine code-enforces the evidence boundary; never widen it in prose.
- Never claim scope is confirmed when it is provisional.
- AI cannot confirm exam scope — that is a human-only action.
- If direct official Activity instructions conflict with a professor recommendation,
  the official instruction governs and the conflict is disclosed.
- If an Activity context is supplied alongside exam work and its official
  instructions are missing, partial, or truncated, disclose incomplete coverage and
  never claim to have the complete instruction set or infer omitted requirements.
