# ULS v1.3 request execution addendum

Status: C5 and C6 core designs accepted; implementation in progress.
Date: 2026-09-20. This extension does not modify the frozen v1.2 specifications.
The v1.2 release contract remains the default for existing deployments. C5 requires
explicit semester mappings and verified schema readback before activation.

## C5: human range requests

This revision adopts the USAGE_RANGE input and approval semantics in
`docs/ux/intake-execution-contract.md` sections 2.2 and 5.1–5.3. SOURCE/AI/USER
ownership and human Decision/Verified ownership remain unchanged. Producing a
proposal is deterministic from validated human input and source evidence; it does
not require a model provider.

The physical slot is the provider-qualified Course, Session and Material page IDs
plus Usage Role. Current application identities and course relations must also
validate. Never substitute an application ID for a physical relation UUID.

Requests require two matching normalized USER-field observations at least one
second apart. Persist the immutable receipt and authorship; SYSTEM status changes
do not alter the USER hash. Completely enumerate and order new inputs by UTC
creation time and canonical page UUID before applying approvals. Cancellation,
withdrawal, edits and missing known requests from a complete source invalidate
only the matching current generation. Unknown listing results are not absence.
UNKNOWN advances the input generation without creating a Usage or proposal and
never revokes an already verified Usage.

CREATE claims an unsealed producer intent. The complete slot must be empty both at
baseline acquisition and immediately before dispatch. Persist candidate application
ID and dispatch lineage before creating Verified=false. Recovery requires that exact
candidate identity and physical proof; matching content alone cannot establish the
outcome of an ambiguous in-flight invocation. Do not release ambiguous ownership.
Once identity is proven, atomically seal the target, canonical v2 action/envelope
and proposal outbox. A committed proposal is never rebound. Publish exact immutable
outbox bytes and require exact Queue readback before reporting review readiness.

Every provider dispatch rechecks the durable head, original submitted request and
a complete current range-request enumeration. Newly submitted competing requests
defer that slot until ingestion; an unresolved target or incomplete listing blocks
the affected scope. A request snapshot is scheduling evidence, not authorization.
Notion cannot atomically combine these reads with a write: a later edit may race an
already-started request. Readback records the actual partial effect, stops subsequent
effects and never claims a cancellation reversed an existing write.

Only the existing human approval path may promote Verified. Unapplied legacy
Usage/Range proposals require a new v2 request and human decision. Applied legacy
history remains subject to existing freshness checks. Human Decision and terminal
history are preserved; EXAM behavior is not changed by this extension.

## Runtime compatibility

The existing sync/process/run runner remains the sole worker-lock owner. Sync-only
does not apply approvals or publish notes. Under that lock, enabled handlers receive
one complete immutable snapshot, apply claims/invalidation, then publish behind a
shared barrier. Overflow or incomplete work cannot be treated as an empty success.

Semester configuration adds optional `material_usage_data_source_id`,
`automation_queue_data_source_id` and `study_requests_data_source_id`. C5 requires
its own complete mappings and the explicit `c5-range-v1` schema profile. Existing
five-source intake remains valid under its original profile. Never infer these
mappings from the separate global retrieval registry. Missing C6 configuration does
not disable C5. This document does not authorize live schema or credential changes.

## C6: conversational generation and save

The user chose conversational MCP generation AND save. The connected AI generates
from bounded ULS evidence and submits an untrusted AI draft; there is no required
worker-owned model API, CLI subprocess or manual paste. The existing search MCP
remains read-only. A separately enabled submission boundary queues note-only work;
only the worker may publish to providers. No submission promotes human-owned
Verified/Confirmed fields.

Client-origin request references are opaque local IDs, explicitly tagged
`client_submitted`, never presented or dereferenced as Notion page IDs. Their Session
head still uses the same physical Session identity as Notion-origin requests.
Actual client transport scope is pending user selection. Detailed C6 implementation
acceptance, live client testing and deployment must be recorded separately.

The transport-neutral core accepts authenticated caller context. A local stdio
adapter is an interim entry point, not a claim of remote-client support. Requests
use caller-scoped idempotency keys and immutable input hashes. Status and cancellation
use the client request ID; status exposes the prepared note, attempt and grant IDs.
Evidence reads return only the worker-prepared bounded package. A grant binds the
caller, request, Session, generation, attempt, manifest and expiry. Consuming a grant
and saving its draft is atomic; identical retries replay, conflicting drafts reject.

Canonical evidence modes are `transcript-only.v1` (default), `confirmed-lecture.v1`
and `provisional-selected.v1`. Normalize internal aliases before hashing/storage.
Confirmed mode records the complete eligible verified Lecture Slides/Professor Notes
membership before applying budgets; no eligible material means waiting for usage
confirmation, never silent transcript-only fallback. Provisional mode includes only
explicit selections and preserves their actual verification status. Missing selected
evidence stays Partial. The manifest binds mode, dependencies and input/template
versions; unrelated materials do not invalidate transcript-only notes.

Journal every C6-store/main-state boundary with an immutable operation identity.
Recover exact matching outcomes, retry only proven absent operations and block
ambiguity. A single worker lock does not provide cross-store crash atomicity or
exclude concurrent client cancellation. Receive all committed input/cancellations
before publication, then recheck current input, head and evidence at each dispatch.

Drive staging uses an AI_STUDY_NOTE marker, immutable body and manifest hashes, and
verified private-parent readback. Persist marker/intent before the first create.
Recover a lost response by exact marker and content proof; absence after an ambiguous
call never authorizes another create. The dedicated Session AI block bridge preserves
Phase-3 schema and compares the last published content hash before replacement.
An unknown initial block ID after append blocks further creation for that region.
Preserve the staged artifact/link and report reconciliation required, without
overwriting USER edits or promising atomic Notion compare-and-swap.

Transient pipeline failures allow initial dispatch plus three retries at 1/5/15
minutes. Verified staging is reused. Unknown write effects override retry scheduling.
Draft rejection is separately capped: the third invalid draft fails that attempt.
Automated checks verify structure, provenance labels, allowed locators and required
sections; they do not prove factual or teaching quality. A real connected-model
sample must be qualitatively reviewed before claiming teaching-quality readiness.

## Acceptance evidence

C5 and C6 core plans: independent web GPT-5.6 Sol / Very high and Gemini 3.8 Flash /
high accepted the corrected designs. Implementation and final review remain pending. See compact
`handoff.md` for current code, checks, evidence paths and commit state. A passing
local test suite is not proof of live provider activation or client readiness.
