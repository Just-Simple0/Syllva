---
name: explain-concept
description: Explain a course concept using bounded lexical/index retrieval across the course.
behavior_contract_version: 1
behavior_contract_hash: sha256:83a0362614e634249f4b8204c7eb57009b54f01ebda749579b7caaf8d51bdc14
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

- v1.2 CONCEPT retrieval is bounded lexical/index retrieval — no full-corpus semantic dump.
- Label course SOURCE vs. AI inference vs. clearly-marked external knowledge.
- Do not overstate what a source proves.
