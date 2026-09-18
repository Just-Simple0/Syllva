# Intake v1.3 preview implementation status

This additive status note describes the implemented semester intake slice. The frozen v1.2 design, `file-intake.md`, and `intake-execution-contract.md` remain the authorities for behavior and wording.

The current slice adopts the intake portions of C1, C2, C3, and C4, plus the rev10 C1 durable study-note state/storage contract:

- C1-intake: durable observations, requests, plans, jobs, provider write attempts, stage events, source versions, and source-to-derivative provenance.
- C1-study-note state/storage: reservation history and reconciliation evidence, durable Session heads, note jobs and attempts, request-to-attempt references, artifact metadata, and atomic restart/idempotency/concurrency guards. This is storage/state API support only; study-note generation, publishing, and user-facing status projection remain deferred to C6.
- C2: explicit current-semester Drive roots and course subtrees, compact private markers where the worker port supports them, metadata/privacy readback, recovery, and file-ID-preserving move.
- C3-intake: the five operational data sources, exact relation targets and option sets, ownership guards, and `Partial`/`Unavailable` status propagation.
- C4: pre-canonical upload discovery, explicit course/kind/date routing, and ambiguity requests before a file can be organized.

The five operational data sources are Academic Courses, Sessions, Materials, File Intake, and Input Request. They are separate from the existing seven global-ID retrieval data sources. Existing native course portal pages remain user-owned portals and links; the canonical current-semester rows and relations are provisioned separately.

Preview readiness is deliberately separate from custom MCP retrieval readiness. The intake worker validates the five current-semester data sources, provider bindings, source versions, and processing provenance; it does not supply or replace the seven global IDs consumed by the existing `build_retrieval(config.notion globals)` composition. A `NOT_PROVEN_BY_INTAKE_PREVIEW` retrieval lane remains visible until an independently connected read-only MCP client validates that legacy registry.

Input Request uses `Submitted` and `Cancelled` USER checkboxes. `Request Status` is a SYSTEM projection and never acts as approval. The initial SYSTEM draft contains only its required intake relation and explicit false checkboxes; it does not require a nonexistent submitted receipt. After a request is claimed, the worker freezes the durable USER hash and checks it before each dependent mutation.

The local commands are:

```text
uls --config <config.yaml> sync --max-jobs N     # discover and project, no processing
uls --config <config.yaml> process --max-jobs N  # process already discovered requests/jobs
uls --config <config.yaml> run --max-jobs N      # discover, claim Submitted requests, and process
```

`N` is an integer from 1 through 1000. `sync` discovers and projects current uploads, `process` handles durable work already queued, and `run` performs both phases in one bounded tick. A normal successful tick returns a JSON object with `status`, `discovered`, `processed`, `failed`, `needs_input`, and the worker readiness lanes; it returns `status: "already_running"` when another local worker owns the lock.

All three worker paths share the local single-active-worker lock. A normal `uls run` scans current Input Request pages for `Submitted=true` and `Cancelled=false`; users do not need a hidden manual claim command. Provider schema/parent readback must report verified before Notion writes are enabled.

The worker performs deterministic transcript normalization and bounded PDF text extraction. It preserves `Ready`, `Partial`, and unavailable outcomes, writes normalized source pointers only after full readback, and records processing provenance for the existing read-only MCP retrieval contract. It does not generate study notes or claim AI output when no AI provider is configured.

C5 proposal/envelope and HumanApprovalApplier work, plus C6 study-note generation, Drive staging, Notion AI-region writing, cancellation wiring, and user-facing status projection, remain deferred from this implementation slice. Human `Verified` and `Scope Confirmed` approvals remain human-owned, and the MCP search surface remains read-only.
