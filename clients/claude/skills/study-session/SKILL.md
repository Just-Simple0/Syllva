---
name: study-session
description: Summarize / study a specific lecture session grounded in ULS course context.
behavior_contract_version: 1
behavior_contract_hash: sha256:83a0362614e634249f4b8204c7eb57009b54f01ebda749579b7caaf8d51bdc14
---

# Study a session (SESSION intent)

Projection of `contracts/study-behavior.md`. See that contract for the authoritative rules.

## When to use

The user asks about one specific lecture/session — e.g. "5강 정리해줘", "3번째 강의에서
CPU 스케줄링 부분 설명해줘".

## Flow

1. Resolve the session with `uls.resolve_entity` (course_hint + query, entity_type=session).
   If `ambiguous`, show candidates and confirm with `uls.select_resolution`.
2. Call `uls.get_session_context` with the resolved `session_id`.
3. Answer from the returned transcript/material chunks. Keep SOURCE / USER / AI distinct.
4. Use `uls.get_source_chunk` (with the returned `context_id`) only for authorized locators.

## Rules

- Never traverse Notion/Drive directly.
- Label professor SOURCE, USER notes, and your own AI inference separately.
- Surface page/timestamp locators when asked "where".
- On `CONTEXT_EXPIRED` / `LOCATOR_STALE`, recover per the Behavior Contract (retry once).
