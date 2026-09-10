---
name: explain-concept
description: Explain a course concept using bounded lexical/index retrieval across the course.
behavior_contract_version: 2
behavior_contract_hash: sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a
---

# Explain a concept (CONCEPT intent)

Projection of `contracts/study-behavior.md`.

## When to use

The user asks to explain/understand a concept across the course — e.g.
"Big-O를 강의 기준으로 쉽게 설명해줘".

## Flow

1. Call `uls.search_concept` (course_key + concept). Textbook only when requested/needed.
2. If the candidate set is too broad or low-confidence, ask the user to narrow the concept.
3. Explain at the requested level, grounded in the returned course candidates.

## Rules

- Keep SOURCE/USER/AI/External provenance distinct: supplied professor/official
  evidence is SOURCE, the learner's notes are USER, interpretation is AI, and general
  knowledge is External.
- v1.2 CONCEPT retrieval is bounded lexical/index retrieval — no full-corpus semantic dump.
- Label course SOURCE vs. AI inference vs. clearly-marked external knowledge.
- Do not overstate what a source proves.
- When Activity instructions appear in supplied context, official constraints govern
  conflicting recommendations; typed constraint metadata does not change global
  SOURCE/USER/AI/External provenance.
- If those official instructions are missing, partial, or truncated, disclose
  incomplete coverage and do not claim to have the complete instruction set or infer
  omitted requirements, including after serialization.
