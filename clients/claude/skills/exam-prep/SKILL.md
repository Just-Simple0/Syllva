---
name: exam-prep
description: Prepare for an exam strictly within confirmed scope; label provisional scope.
behavior_contract_version: 1
behavior_contract_hash: sha256:83a0362614e634249f4b8204c7eb57009b54f01ebda749579b7caaf8d51bdc14
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

- The engine code-enforces the evidence boundary; never widen it in prose.
- Never claim scope is confirmed when it is provisional.
- AI cannot confirm exam scope — that is a human-only action.
