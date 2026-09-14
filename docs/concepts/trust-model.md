# Trust Model

[한국어](trust-model.ko.md) · [Concepts](README.md)

Syllva is designed to keep different kinds of information from silently changing meaning as they move through an AI workflow.

## SOURCE / AI / USER

- **SOURCE** — material treated as evidence under the configured authority/freshness rules.
- **AI** — generated explanation, enrichment, proposal, or study aid.
- **USER** — user-owned notes, choices, completion state, approvals, and other human state.

An AI output does not become SOURCE just because it sounds correct. A USER note does not become authoritative course evidence merely because it is useful.

## Human gates

Fields such as verification or scope confirmation can be human-owned. The protection must exist at the write boundary, not merely as a prompt instruction saying “please do not change this field.”

## Partial is not complete

Extraction, pagination, provider failures, or incomplete course results can produce partial states. Syllva should preserve that incompleteness rather than silently promoting it to a complete result.

## Provenance

Useful academic answers need a path back to their evidence. Retrieval results therefore carry source identity/location information so a client can show where a claim came from.

## Freshness

An old derivative may no longer represent a changed source. Freshness checks prevent stale generated/normalized material from silently outranking the current source.

## Capability boundaries

A bounded retrieval capability authorizes only its intended scope/current source state. It is not a reusable master token for arbitrary source access.

## Fail closed

When identity, authority, scope, or external outcome is ambiguous, Syllva prefers an explicit error/choice/reconciliation state over widening access or guessing success.

This can feel more conservative than a simple “chat with all my files” system, but the conservatism is deliberate: academic context is more useful when the user can tell what the system actually knows.
