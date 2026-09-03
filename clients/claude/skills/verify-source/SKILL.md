---
name: verify-source
description: Verify a claim against normalized/original source evidence, not AI enrichment.
behavior_contract_version: 1
behavior_contract_hash: sha256:83a0362614e634249f4b8204c7eb57009b54f01ebda749579b7caaf8d51bdc14
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

- AI enrichment may only hint where to look; it can never satisfy verification.
- If the source does not support the claim, state that it is **not** supported.
- Cite the locator that supports (or fails to support) the claim.
