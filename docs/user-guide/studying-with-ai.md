# Studying with AI

[한국어](studying-with-ai.ko.md) · [User Guide](README.md)

Syllva is most useful when the AI client can ask the retrieval layer for **bounded academic context** instead of receiving an unstructured dump of everything you own.

## Ask scoped questions

Good questions identify enough context for the system to resolve the target safely.

### Explain a concept

> From the verified sources for Operating Systems session 4, explain virtual memory. Use one concrete address-translation example and cite the source sections you relied on.

### Review a session

> Review Database Systems session 2. Separate definitions, examples, and items I should practice. Do not treat my notes as authoritative source text.

### Prepare for an exam

> Using only the current confirmed exam scope, make a study checklist. Mark topics with incomplete source coverage instead of filling the gaps yourself.

### Verify a claim

> Verify the statement “X” against the current course sources. If the evidence is insufficient or conflicting, say so and show the relevant source locations.

### Work on an activity

> Help me solve this activity using the allowed course context. Explain the reasoning, but keep the source facts and my own answer clearly separated.

## What to expect from Syllva-backed answers

A connected client should respect the shared behavior contract and server-enforced scope. In practical terms:

- source claims should be traceable to returned source/provenance information;
- stale or incomplete material should not silently become current complete evidence;
- ambiguity may require you to choose a course/session/material candidate;
- AI prose should remain distinguishable from source text and user-owned notes;
- when evidence is missing, abstention or a request for more material is preferable to a confident guess.

## Follow-up questions

Some retrieval flows issue bounded capabilities for follow-up access. Those capabilities are tied to scope/current source state and may expire or become invalid when the underlying source changes. If a follow-up can no longer be authorized, ask the client to resolve/retrieve the current context again rather than bypassing the check.

## What AI clients cannot do through read-only MCP

The current MCP retrieval surface is not an upload/approval interface. A client should not use it to:

- upload or move Drive files;
- create or approve Notion verification fields;
- mark human verification/scope confirmation on your behalf;
- force a failed intake request into a success state.

Those operations, where supported, belong to separate operator/worker boundaries.
