# University Learning System v1.2 — Implementation Specification

**Status:** Frozen for v1.2 implementation  
**Parent architecture:** `university-learning-system-v1.2-design-frozen.md`  
**Architecture version:** v1.2  
**Implementation profile:** Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform  
**Primary desktop platforms:** macOS, Windows  
**Core language:** Python 3  
**Initial durable StateStore:** SQLite  
**Initial EphemeralStore:** In-memory TTL store  
**MCP capability model:** Read-only  
**Canonical file corpus:** Google Drive  
**Canonical academic graph/state:** Notion  
**Canonical project-code source:** GitHub exact ref

---

# 0. Purpose of This Specification

This document translates the frozen v1.2 architecture into implementation contracts.

The parent design remains authoritative for:

- source authority,
- SOURCE / AI / USER ownership,
- retrieval boundaries,
- human-gate policy,
- privacy,
- model/client independence,
- read-only MCP,
- local-primary topology,
- Goodnotes capability target,
- release-blocking safety invariants.

This document defines:

- repository/module layout,
- domain types,
- storage schemas,
- StateStore and EphemeralStore behavior,
- Drive/Notion/GitHub adapter contracts,
- Retrieval Engine interfaces,
- MCP tools and tool schemas,
- entity-resolution protocol,
- source chunk capabilities,
- freshness and stale-locator handling,
- client support-state semantics,
- Behavior Contract projection checks,
- credential separation,
- remote MCP transport/authentication,
- CLI and scheduler behavior,
- implementation order,
- contract/integration/E2E test requirements.

If this document conflicts with the frozen design, the frozen design wins.

## 0.1 Freeze completion notes

This frozen revision closes the final implementation-spec review gaps:

1. Locator grammar + typed containment are normative.
2. `job_key` derivation is deterministic.
3. source-to-entity allocation is retry-idempotent.
4. Automation Queue + HumanApprovalApplier define the full human-gate lifecycle.
5. `uls.select_resolution` is explicitly the concrete MCP projection of the frozen resolution protocol.
6. aliases use Rich text and Courses support aliases.
7. bounded LLM reranking defaults to opt-in (`false`).
8. client recovery from expired/stale ephemeral capabilities is part of the Behavior Contract.
9. single-user remote bearer authorization scope is explicitly documented.
10. Automation Queue `Decision` is human-owned; approval state promotion is split across `APPROVAL_READER` and `HUMAN_APPROVAL_APPLIER` with adapter-level guards and contract tests.

---

# 1. Carry-Forward Decisions from Architecture Review

The following implementation-level decisions are mandatory because they were intentionally deferred from the architecture review.

## 1.1 Ephemeral capability state

The MCP layer now has short-lived state for:

- `resolution_id`,
- `candidate_id`,
- `context_id`,
- context capability allowlists,
- capability TTL,
- optional multi-turn resolution metadata.

This state is **not academic authority** and is **not durable canonical state**.

v1.2 default:

```text
EphemeralStore
→ in-memory
→ process-local
→ TTL-bounded
→ restart invalidates all entries
```

A server restart therefore requires the client to repeat the original resolve/context call.

Shared Redis/database-backed ephemeral state is not required in v1.2.

## 1.2 Spike C0 proves connectivity only

A `Spike C0` PASS proves only:

```text
ChatGPT target environment
→ can connect/register remote MCP/App path
→ can discover a tool
→ can invoke trivial read-only tool
```

It does **not** prove:

- large context-package usability,
- multi-turn entity resolution,
- `context_id` chaining,
- source pagination,
- domain-policy correctness,
- production-quality ChatGPT support.

Those are validated later in VS0-B/client E2E.

## 1.3 Model-agnostic is a core/protocol property

The following are model-agnostic:

```text
ULS Core
Retrieval Engine
MCP tool contract
Behavior Contract
Context Package schemas
```

Individual client integrations receive one support status:

```text
VALIDATED
EXPERIMENTAL
DEPLOYMENT_DEFERRED
```

A client may be `DEPLOYMENT_DEFERRED` because of current product, plan, workspace, platform, or transport limitations without invalidating the ULS architecture.

## 1.4 Stale locators are never trusted against a new source version

If an enrichment/index was built from an older fingerprint:

```text
old source_version/hash
!=
current source_version/hash
```

then any absolute locator derived from that stale artifact is invalid for factual retrieval.

The implementation may reuse only symbolic routing hints such as:

- entity ID,
- topic,
- heading text,
- normalized term,
- section name.

It must re-resolve the locator against the **current normalized derivative**.

If re-resolution fails, the stale hint is discarded.

---

# 2. v1.2 Implementation Scope

v1.2 must implement enough to prove:

```text
source file
→ canonical Drive archive
→ normalized derivative
→ Notion graph/state
→ ULS Retrieval Engine
→ read-only MCP
→ AI client
→ grounded answer
```

The following are required in v1.2:

- one cross-platform Python package,
- Google Drive ingestion/archive,
- Notion graph/state integration,
- PDF normalization,
- normalized Markdown storage,
- entity identity/aliases,
- source versioning,
- SQLite StateStore,
- in-memory EphemeralStore,
- Retrieval Engine,
- read-only MCP tools,
- local MCP transport,
- remote authenticated MCP feasibility/profile where supported,
- Claude/ChatGPT client projections where supported,
- Behavior Contract + projection drift checks,
- Goodnotes Level-2 fallback,
- transcript vertical slice,
- Material Usage human gate,
- Exam/Activity policy retrieval,
- GitHub exact-ref retrieval,
- scheduler adapters,
- model-independent contract tests.

Not required:

- PostgreSQL,
- Redis,
- vector DB,
- embeddings index,
- distributed workers,
- cloud ingestion,
- full cross-PC takeover,
- custom web dashboard,
- MCP write tools,
- automatic human approvals,
- Goodnotes Level-3 geometry.

---

# 3. Repository Layout

```text
university-learning-system/
├─ pyproject.toml
├─ README.md
├─ CHANGELOG.md
├─ .env.example
├─ config.example.yaml
│
├─ contracts/
│  └─ study-behavior.md
│
├─ clients/
│  ├─ claude/
│  │  ├─ skills/
│  │  │  ├─ study-session/
│  │  │  │  └─ SKILL.md
│  │  │  ├─ explain-concept/
│  │  │  │  └─ SKILL.md
│  │  │  ├─ exam-prep/
│  │  │  │  └─ SKILL.md
│  │  │  ├─ activity-help/
│  │  │  │  └─ SKILL.md
│  │  │  └─ verify-source/
│  │  │     └─ SKILL.md
│  │  └─ package/
│  │
│  └─ chatgpt/
│     ├─ instructions/
│     │  └─ study-behavior.md
│     └─ app/
│
├─ src/
│  └─ uls/
│     ├─ cli/
│     │  ├─ main.py
│     │  └─ commands/
│     │     ├─ init.py
│     │     ├─ doctor.py
│     │     ├─ sync.py
│     │     ├─ process.py
│     │     ├─ run.py
│     │     ├─ status.py
│     │     ├─ jobs.py
│     │     ├─ retry.py
│     │     ├─ reprocess.py
│     │     └─ mcp.py
│     │
│     ├─ domain/
│     │  ├─ models.py
│     │  ├─ enums.py
│     │  ├─ ids.py
│     │  ├─ source_ref.py
│     │  ├─ provenance.py
│     │  ├─ errors.py
│     │  └─ contracts.py
│     │
│     ├─ ingestion/
│     │  ├─ discovery.py
│     │  ├─ classifier.py
│     │  ├─ revision_matcher.py
│     │  ├─ archive.py
│     │  └─ allocator.py
│     │
│     ├─ normalization/
│     │  ├─ pdf.py
│     │  ├─ goodnotes.py
│     │  ├─ transcript.py
│     │  ├─ activity.py
│     │  ├─ validators.py
│     │  └─ schemas.py
│     │
│     ├─ enrichment/
│     │  ├─ session.py
│     │  ├─ material.py
│     │  ├─ activity.py
│     │  ├─ exam.py
│     │  ├─ freshness.py
│     │  └─ schemas.py
│     │
│     ├─ retrieval/
│     │  ├─ engine.py
│     │  ├─ resolver.py
│     │  ├─ scope.py
│     │  ├─ authority.py
│     │  ├─ freshness.py
│     │  ├─ lexical.py
│     │  ├─ chunking.py
│     │  ├─ locators.py
│     │  ├─ context.py
│     │  ├─ capabilities.py
│     │  ├─ provenance.py
│     │  └─ schemas.py
│     │
│     ├─ mcp/
│     │  ├─ server.py
│     │  ├─ schemas.py
│     │  ├─ errors.py
│     │  ├─ tools/
│     │  │  ├─ ping.py
│     │  │  ├─ entity.py
│     │  │  ├─ material.py
│     │  │  ├─ session.py
│     │  │  ├─ concept.py
│     │  │  ├─ exam.py
│     │  │  ├─ activity.py
│     │  │  ├─ user_context.py
│     │  │  ├─ verify.py
│     │  │  └─ source_chunk.py
│     │  └─ transports/
│     │     ├─ local.py
│     │     └─ remote.py
│     │
│     ├─ adapters/
│     │  ├─ drive/
│     │  │  ├─ base.py
│     │  │  └─ google.py
│     │  ├─ notion/
│     │  │  ├─ base.py
│     │  │  └─ api.py
│     │  ├─ github/
│     │  │  ├─ base.py
│     │  │  └─ api.py
│     │  └─ llm/
│     │     ├─ base.py
│     │     └─ structured.py
│     │
│     ├─ state/
│     │  ├─ base.py
│     │  ├─ sqlite.py
│     │  ├─ models.py
│     │  └─ migrations/
│     │
│     ├─ ephemeral/
│     │  ├─ base.py
│     │  ├─ memory.py
│     │  └─ models.py
│     │
│     ├─ orchestration/
│     │  ├─ runner.py
│     │  ├─ jobs.py
│     │  ├─ retry.py
│     │  └─ locks.py
│     │
│     └─ config/
│        ├─ loader.py
│        ├─ schema.py
│        └─ validation.py
│
├─ deployment/
│  ├─ macos/
│  ├─ windows/
│  └─ remote-mcp/
│
├─ scripts/
│  ├─ project_behavior_contract.py
│  └─ lint_behavior_projection.py
│
└─ tests/
   ├─ unit/
   ├─ contract/
   ├─ integration/
   ├─ e2e/
   └─ fixtures/
```

---

# 4. Dependency Boundaries

Core modules must not depend on client-specific SDKs or OS schedulers.

```text
domain/
ingestion/
normalization/
enrichment/
retrieval/
state/
ephemeral/
```

may depend only on application/domain abstractions.

Client packaging belongs under:

```text
clients/
```

Platform scheduling belongs under:

```text
deployment/
```

MCP transport specifics belong under:

```text
mcp/transports/
```

The Retrieval Engine must be callable directly from tests without launching an MCP server.

---

# 5. Configuration Contract

Example:

```yaml
system:
  timezone: Asia/Seoul
  workspace_dir: ~/.uls
  state_backend: sqlite
  ephemeral_backend: memory

worker:
  enabled: true
  poll_interval_minutes: 10

storage:
  normalized_derivatives: google_drive

google_drive:
  university_root_id: "..."
  inbox_root_id: "..."

notion:
  courses_db_id: "..."
  sessions_db_id: "..."
  materials_db_id: "..."
  material_usage_db_id: "..."
  activities_db_id: "..."
  exams_db_id: "..."
  automation_queue_db_id: "..."

normalization:
  schema_version: v1
  processor_version: "1.2.0"
  goodnotes_visual_fallback: true

retrieval:
  concept_mode: bounded_lexical
  max_candidate_entities: 20
  max_candidate_chunks: 12
  context_ttl_seconds: 900
  resolution_ttl_seconds: 900
  allow_bounded_llm_rerank: false  # opt-in; deterministic lexical/index retrieval is default

mcp:
  mode: local
  read_only: true

remote_mcp:
  enabled: false
  auth_mode: oauth_or_bearer
  public_unauthenticated: false

behavior_contract:
  version: 1
  path: contracts/study-behavior.md

courses:
  - course_key: "2026-1_COMP319-002"
    name: "알고리즘1"
    code: "COMP319"
    section: "002"
    semester: "2026-1"
```

Secrets remain outside committed configuration.

Example:

```text
GOOGLE_WORKER_CREDENTIALS_FILE=
GOOGLE_MCP_CREDENTIALS_FILE=
NOTION_WORKER_TOKEN=
NOTION_MCP_TOKEN=
GITHUB_READ_TOKEN=
LLM_API_KEY=
REMOTE_MCP_SECRET=
```

---

# 6. Core Domain Types

## 6.1 `SourceRef`

```python
@dataclass(frozen=True)
class SourceRef:
    provider: str
    file_id: str
    web_url: str | None = None
```

Canonical source identity:

```text
provider + file_id
```

`web_url` is never used as the canonical identity key.

## 6.2 `SourceFingerprint`

```python
@dataclass(frozen=True)
class SourceFingerprint:
    source_version: int
    source_hash: str
```

## 6.3 `Locator`

A Locator is a **parsed domain type**, not an opaque string. Capability enforcement must compare parsed fields, never string prefixes/substrings.

Canonical examples:

```text
COMP319-M03:p13
COMP319-M03:p13-p27
COMP319-S05:t00:31:20
COMP319-S05:t00:10:00-00:40:00
COMP319-M03:p13:user
COMP319-M03:p13-p27:user
```

### 6.3.1 Grammar

Normative grammar:

```text
locator        ::= entity-id ":" target [":" subtype]

target         ::= page-target
                 | time-target

page-target    ::= "p" page
                 | "p" page "-" "p"? page

time-target    ::= "t" timestamp
                 | "t" timestamp "-" timestamp

page           ::= positive-integer

timestamp      ::= hour ":" minute ":" second

subtype        ::= "source"
                 | "user"

hour           ::= 2DIGIT
minute         ::= 2DIGIT
second         ::= 2DIGIT
positive-integer ::= DIGIT1-9 *DIGIT
```

Additional constraints:

```text
00 <= minute <= 59
00 <= second <= 59
hour >= 00
page >= 1
range start <= range end
```

`entity-id` is parsed using the ULS Entity ID grammar and may not contain `:`.

The parser must reject:

```text
p0
p-1
p27-p13
t00:40:00-00:10:00
malformed timestamps
unknown subtype
extra path/URL syntax
```

### 6.3.2 Canonical AST

Conceptual types:

```python
@dataclass(frozen=True)
class PageLocator:
    entity_id: str
    start_page: int
    end_page: int
    subtype: Literal["source", "user"] | None = None

@dataclass(frozen=True)
class TimeLocator:
    entity_id: str
    start_seconds: int
    end_seconds: int
    subtype: Literal["source", "user"] | None = None
```

A single page/timestamp is normalized to:

```text
start == end
```

Canonical serialization is deterministic. Equivalent alternate input syntax must serialize to one canonical representation before hashing, logging, capability issuance, or comparison.

### 6.3.3 Containment

`requested` is contained by `allowed` only when all are true:

```text
same locator kind
same entity_id
requested.start >= allowed.start
requested.end <= allowed.end
subtype policy satisfied
```

Subtype policy is deny-by-default:

```text
allowed subtype == requested subtype
→ subtype match

allowed subtype is None AND requested subtype is None
→ match

allowed subtype is None AND requested subtype is "user"
→ DENY

allowed subtype is "source" AND requested subtype is None
→ DENY

allowed subtype is "user" AND requested subtype is None
→ DENY
```

Therefore:

```text
allow COMP319-M03:p13-p27
request COMP319-M03:p18
→ ALLOW

allow COMP319-M03:p13-p27
request COMP319-M03:p13:user
→ DENY

allow COMP319-M03:p13-p27:user
request COMP319-M03:p13:user
→ ALLOW

allow COMP319-S05:t00:10:00-00:40:00
request COMP319-S05:t00:31:20
→ ALLOW
```

Containment is always numeric/typed comparison after parsing. Raw string comparison is forbidden for authorization decisions.

### 6.3.4 Locator source role

A locator's `subtype` is not a substitute for source authority metadata.

`EvidenceItem.source_class` and `SourceRef` still determine whether content is professor SOURCE, USER source, transcript, official instruction, etc.

The locator subtype only constrains the specific representation authorized by a capability.

## 6.4 `EvidenceItem`

```text
source_class
entity_id
locator
fingerprint
authority
content
provenance
freshness
```

## 6.5 `ContextPackage`

All MCP domain tools return a versioned structured context package or a documented subtype.

---

# 7. Persistent StateStore Contract

```python
class StateStore(Protocol):
    def create_job(...): ...
    def get_job(...): ...
    def claim_job(...): ...
    def transition_job(...): ...
    def complete_job(...): ...
    def fail_job(...): ...

    def find_processed_source(...): ...
    def register_source_file(...): ...
    def register_source_version(...): ...
    def find_source_versions(...): ...

    def get_checkpoint(...): ...
    def set_checkpoint(...): ...

    def acquire_local_worker_lock(...): ...
    def release_local_worker_lock(...): ...
```

The initial implementation is SQLite.

StateStore contains durable orchestration state.

It must not store canonical academic source bodies.

---

# 8. SQLite Minimum Schema

Required logical tables:

```text
jobs
source_files
source_versions
processing_records
checkpoints
entity_allocations
schema_migrations
```

## 8.1 `jobs`

```text
id TEXT PRIMARY KEY
job_key TEXT UNIQUE NOT NULL
operation TEXT NOT NULL
stage TEXT NOT NULL
status TEXT NOT NULL

course_key TEXT
source_file_id TEXT
source_hash TEXT
target_entity_id TEXT

attempt_count INTEGER NOT NULL DEFAULT 0
error_class TEXT
last_error TEXT

created_at TEXT NOT NULL
updated_at TEXT NOT NULL
completed_at TEXT
```

Allowed status:

```text
PENDING
PROCESSING
READY
PARTIAL
NEEDS_REVIEW
FAILED
```

### 8.1.1 `job_key` derivation

`job_key` is deterministic and is not implementation-defined.

Canonical inputs:

```text
source_file_id
source_hash
operation
processor_version
```

Canonical derivation:

```text
payload =
    utf8(source_file_id)
    + 0x1F
    + utf8(source_hash)
    + 0x1F
    + utf8(operation)
    + 0x1F
    + utf8(processor_version)

job_key = "sha256:" + lowercase_hex(SHA256(payload))
```

Rules:

- all four fields are required for source-processing jobs,
- `operation` uses the canonical enum value,
- `processor_version` must change whenever processing semantics that affect output change,
- volatile values such as current time, display filename, retry count, or temporary path are forbidden in the key,
- identical canonical inputs must produce the same key on macOS and Windows,
- `jobs.job_key UNIQUE` is the final database-level duplicate guard.

For non-source jobs where one or more canonical source fields do not naturally exist, the operation must define an explicit deterministic identity tuple in this specification before implementation; random job keys are forbidden for idempotent operations.

## 8.2 `source_files`

```text
source_file_id TEXT PRIMARY KEY
provider TEXT NOT NULL
provider_file_id TEXT NOT NULL
course_key TEXT NOT NULL
source_kind TEXT NOT NULL
original_filename TEXT
current_hash TEXT
canonical_entity_id TEXT
first_seen_at TEXT NOT NULL
last_seen_at TEXT NOT NULL
```

Unique:

```text
(provider, provider_file_id)
```

## 8.3 `source_versions`

```text
id TEXT PRIMARY KEY
source_file_id TEXT NOT NULL
source_hash TEXT NOT NULL
version INTEGER NOT NULL
canonical_entity_id TEXT NOT NULL
source_ref_json TEXT NOT NULL
first_seen_at TEXT NOT NULL
processor_version TEXT
```

Unique:

```text
(source_file_id, source_hash)
(source_file_id, version)
```

## 8.4 `processing_records`

```text
id TEXT PRIMARY KEY
job_id TEXT NOT NULL
operation TEXT NOT NULL
processor_version TEXT NOT NULL
input_hash TEXT
output_ref_json TEXT
started_at TEXT NOT NULL
finished_at TEXT
status TEXT NOT NULL
```

## 8.5 `checkpoints`

```text
provider TEXT NOT NULL
scope TEXT NOT NULL
checkpoint_value TEXT NOT NULL
updated_at TEXT NOT NULL

PRIMARY KEY(provider, scope)
```

## 8.6 `entity_allocations`

```text
course_key TEXT NOT NULL
entity_type TEXT NOT NULL
next_sequence INTEGER NOT NULL

PRIMARY KEY(course_key, entity_type)
```

Allocation occurs in a transaction.

### 8.6.1 Source-bound idempotent allocation

Entity allocation is **once per canonical source identity**, not once per processing attempt.

Algorithm:

```text
BEGIN IMMEDIATE TRANSACTION

source = load source_files row

if source.canonical_entity_id IS NOT NULL:
    return source.canonical_entity_id

sequence = entity_allocations.next_sequence
new_entity_id = allocate(course_key, entity_type, sequence)

UPDATE source_files
SET canonical_entity_id = new_entity_id
WHERE source_file_id = ?
  AND canonical_entity_id IS NULL

if update did not win:
    reload canonical_entity_id
    return existing value

increment entity_allocations.next_sequence

COMMIT

return new_entity_id
```

Requirements:

- retry after a crash reuses `source_files.canonical_entity_id`,
- the same source may never consume a second semantic entity ID merely because an ingestion/normalization job retried,
- Notion creation/upsert uses the same semantic ID and is itself idempotent,
- concurrent attempts must converge on one stored canonical entity ID,
- a revision of the same source entity reuses the existing entity ID and creates a new source version instead.

---

# 9. EphemeralStore Contract

## 9.1 Purpose

EphemeralStore holds temporary state required for safe multi-turn retrieval.

It stores only:

- resolution sessions,
- context capabilities,
- locator allowlists,
- expiry metadata.

It does not hold canonical source authority.

## 9.2 Interface

```python
class EphemeralStore(Protocol):
    def create_resolution(
        self,
        candidates: list[ResolutionCandidate],
        ttl_seconds: int,
    ) -> ResolutionHandle: ...

    def get_resolution(
        self,
        resolution_id: str,
    ) -> ResolutionHandle | None: ...

    def consume_resolution_choice(
        self,
        resolution_id: str,
        candidate_id: str,
    ) -> ResolvedEntity: ...

    def create_context_capability(
        self,
        allowed_locators: list[str],
        caller_scope: str | None,
        ttl_seconds: int,
    ) -> ContextCapability: ...

    def get_context_capability(
        self,
        context_id: str,
    ) -> ContextCapability | None: ...

    def authorize_locator(
        self,
        context_id: str,
        locator: str,
        caller_scope: str | None,
    ) -> bool: ...

    def purge_expired(self) -> int: ...
```

## 9.3 Default implementation

```text
MemoryEphemeralStore
```

Properties:

- process-local,
- thread-safe,
- monotonic-expiry based,
- cryptographically random opaque IDs,
- non-persistent,
- no synchronization between separate server instances.

## 9.4 TTL defaults

Default:

```text
resolution TTL = 15 minutes
context capability TTL = 15 minutes
```

Configurable.

TTL must be bounded.

No infinite capability lifetime.

## 9.5 Restart behavior

Server restart:

```text
all resolution_id invalid
all context_id invalid
```

Client receives:

```text
RESOLUTION_EXPIRED
CONTEXT_EXPIRED
```

and must repeat the original resolution/context call.

This is expected v1.2 behavior.

## 9.6 Local vs remote transport

Both local and remote MCP transports must use the same EphemeralStore instance **within one server process**.

v1.2 does not support multiple active MCP server instances requiring shared ephemeral state.

If multi-instance MCP becomes necessary:

```text
shared EphemeralStore
```

becomes a future requirement.

---

# 10. Entity Resolution Protocol

## 10.1 Input precedence

```text
exact entity ID
→ Course + sequence number
→ exact alias
→ deterministic metadata
→ constrained fuzzy candidates
```

## 10.2 Successful result

```json
{
  "status": "resolved",
  "entity": {
    "type": "session",
    "id": "COMP319-S05",
    "name": "05 · CPU Scheduling"
  }
}
```

## 10.3 Ambiguous result

```json
{
  "status": "ambiguous",
  "resolution_id": "res_...",
  "expires_at": "...",
  "candidates": [
    {
      "candidate_id": "cand_...",
      "entity_type": "session",
      "entity_id": "COMP319-S05",
      "label": "05 · CPU Scheduling",
      "reason": "exact alias: 5강"
    }
  ]
}
```

`candidate_id` is opaque/stable for the lifetime of `resolution_id`.

## 10.4 Follow-up selection

```json
{
  "resolution_id": "res_...",
  "candidate_id": "cand_..."
}
```

The server validates that the candidate belongs to that resolution handle.

Client display text must never be trusted as the selection identifier.

---

# 11. Ingestion Identity and Revision Matching

Decision order:

```text
same provider file ID + same hash
→ NO-OP

same provider file ID + new hash
→ AUTO new source version for same entity

new file ID + same hash
→ duplicate candidate; do not create duplicate entity

new file ID + new hash
→ revision candidate evaluation
```

Revision evidence:

- course identity,
- filename stem,
- embedded title,
- page count,
- prior source names,
- normalized document similarity.

Outcomes:

```text
NEW_ENTITY
PROPOSE_REVISION
NEEDS_REVIEW
```

A semantic similarity score alone cannot produce an automatic revision merge.

---

# 12. Drive Storage Contract

Example Material:

```text
Materials/M03/
├─ source/
│  ├─ original_v1.pdf
│  ├─ original_v2.pdf
│  └─ annotated.pdf
└─ derived/
   ├─ material.normalized.md
   └─ annotations.normalized.md
```

Rules:

- source versions are immutable from automation's perspective,
- derived files may be replaced only atomically after validation,
- old source versions remain accessible,
- public sharing is not enabled by automation,
- source/derived roles are never conflated.

---

# 13. Drive Adapter Contract

```python
class DriveAdapter(Protocol):
    def list_changes(...): ...
    def get_file_metadata(...): ...
    def get_folder_children(...): ...
    def download_file(...): ...
    def read_file(...): ...

    def create_folder(...): ...
    def move_file(...): ...
    def copy_file(...): ...

    def write_derived_file(...): ...
    def replace_derived_file_atomically(...): ...
```

The normal domain adapter does not expose:

```text
delete_source()
overwrite_source()
make_public()
```

MCP retrieval code depends on a read-only Drive adapter interface.

---

# 14. Notion Exact Schemas

The six academic DBs remain.

An operational `Automation Queue` database is also required for human review. It is not an academic entity and is not a seventh source-of-truth domain object.

## 14.0 Alias storage rule

`Aliases` uses **Rich text**, not Multi-select.

Reason:

- aliases are entity-local free-form handles,
- they are not a low-cardinality taxonomy,
- using Multi-select would create unbounded database-wide options.

Canonical storage format in v1.2:

```text
alias 1 | alias 2 | alias 3
```

Parsing rules:

- separator is `|`,
- trim surrounding whitespace,
- ignore empty aliases,
- preserve original display case,
- matching may use a normalized comparison form,
- internal entity ID and canonical `Name` are always implicit aliases even if not repeated in the field.

Courses also have `Aliases`, enabling queries such as:

```text
알고리즘 5강
```

to resolve the Course before resolving the Session.

## 14.1 Courses

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| Aliases | Rich text | No |
| Course Key | Rich text | Yes |
| Code | Rich text | Yes |
| Section | Rich text | Yes |
| Semester | Select | Yes |
| Professor | Rich text | No |
| Status | Status | Yes |

## 14.2 Sessions

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| ID | Rich text | Yes |
| Aliases | Rich text | No |
| Course | Relation → Courses | Yes, exactly 1 |
| Session No | Number | Yes |
| Date | Date | Yes |
| Topics | Multi-select | No |
| Status | Status | Yes |
| Recording Folder | URL | No |
| Normalized Transcript | URL | No |
| Recording Status | Select | Yes |
| Material Usage | Relation → Material Usage | No |
| Activities | Relation → Activities | No |

## 14.3 Materials

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| ID | Rich text | Yes |
| Aliases | Rich text | No |
| Course | Relation → Courses | Yes, exactly 1 |
| Type | Select | Yes |
| Source Folder | URL | Yes |
| Original Filename | Rich text | Yes |
| Normalized Source | URL | No until normalized |
| Normalized Annotations | URL | No |
| Text Status | Status | Yes |
| Text Source | Select | Yes |
| Visual Dependency | Select | Yes |
| AI Priority | Select | Yes |
| Page Count | Number | No |
| Annotation Status | Select | No |
| Current Source Version | Number | Yes |
| Material Usage | Relation → Material Usage | No |

`Text Status` options:

```text
Pending
Processing
Ready
Partial
Needs Review
Failed
```

`Text Source`:

```text
Native
PDF Extract
OCR
Mixed
Manual
Unavailable
```

## 14.4 Material Usage

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| ID | Rich text | Yes |
| Session | Relation → Sessions | Yes, exactly 1 |
| Material | Relation → Materials | Yes, exactly 1 |
| Role | Select | Yes |
| Start Page | Number | No |
| End Page | Number | No |
| Scope Note | Rich text | No |
| Verified | Checkbox | Yes |
| Evidence | Rich text | No |
| Confidence | Select | No |
| Notes | Rich text | No |

## 14.5 Activities

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| ID | Rich text | Yes |
| Aliases | Rich text | No |
| Course | Relation → Courses | Yes, exactly 1 |
| Type | Select | Yes |
| Related Sessions | Relation → Sessions | No |
| Related Materials | Relation → Materials | No |
| Due | Date | No |
| Status | Status | Yes |
| Instructions Source | URL | No |
| Normalized Instructions | URL | No |
| Result Type | Select | Yes |
| Result Source | URL | No |
| Repository | URL | No |
| Repository Path | Rich text | No |
| Submission Ref | Rich text | No |
| PR | URL | No |
| Result Artifact | URL | No |

## 14.6 Exams

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| ID | Rich text | Yes |
| Aliases | Rich text | No |
| Course | Relation → Courses | Yes, exactly 1 |
| Type | Select | Yes |
| Date | Date | No |
| Included Sessions | Relation → Sessions | No |
| Status | Status | Yes |
| Scope Confirmed | Checkbox | Yes |
| Question Types | Multi-select | No |
| Source Notice | URL | No |

## 14.7 Automation Queue

`Automation Queue` is the human-review surface for proposals that automation may generate but may not finalize.

| Property | Type | Required |
|---|---|---|
| Name | Title | Yes |
| Proposal ID | Rich text | Yes |
| Proposal Type | Select | Yes |
| State | Status | Yes |
| Course | Relation → Courses | No |
| Target Entity ID | Rich text | No |
| Source Ref | Rich text | No |
| Source Hash | Rich text | No |
| Source Version | Number | No |
| Proposed Action | Rich text | Yes |
| Confidence | Select | No |
| Evidence | Rich text | No |
| Review Reason | Rich text | No |
| Decision | Select | Yes |
| Decision By | Rich text | No |
| Decision At | Date | No |
| Created | Created time | Yes |
| Updated | Last edited time | Yes |
| Applied At | Date | No |
| Last Error | Rich text | No |

`Proposal Type`:

```text
MATERIAL_REVISION
GOODNOTES_MATCH
MATERIAL_USAGE
PAGE_RANGE
EXAM_SCOPE
OTHER
```

`State`:

```text
PENDING_REVIEW
APPROVED
REJECTED
APPLIED
FAILED
SUPERSEDED
```

`Decision`:

```text
Pending
Approve
Reject
```

`Proposal ID` is unique at the application layer and idempotent for the proposal identity.

A proposal stores the relevant source fingerprint when its decision can become stale before application.

---

# 15. Notion Adapter and Human-Gate Guards

General adapter interface:

```text
find_entity_by_id
find_by_alias
create_entity
update_properties
write_source_metadata_region
write_ai_region
read_approval
```

`find_by_alias` must parse the §14.0 Rich-text alias format (`alias 1 | alias 2 | ...`) through the canonical alias parser; it must not treat the raw Rich-text field as one opaque alias string.

No general:

```text
write_user_region
```

## 15.1 Defensive write policy

The adapter/domain write boundary must reject:

```text
AUTOMATION + Material Usage.Verified=true
AUTOMATION + Exam.Scope Confirmed=true

AUTOMATION + Automation Queue.Decision=Approve
AUTOMATION + Automation Queue.Decision=Reject
AUTOMATION + Automation Queue.State=APPROVED
AUTOMATION + Automation Queue.State=REJECTED
AUTOMATION + Automation Queue.State=APPLIED
```

Automation Queue write ownership is deny-by-default.

`AUTOMATION` may write only:

```text
proposal creation/upsert
proposal metadata
Decision=Pending on creation
State=PENDING_REVIEW on creation
State=SUPERSEDED from a defined system invalidation path
State=FAILED from a defined application failure path
Last Error
```

`AUTOMATION` may never manufacture or overwrite a human decision.

Example:

```python
if actor == AUTOMATION and patch.get("Verified") is True:
    raise PolicyViolation("Verified=true is human-only")

if actor == AUTOMATION and patch.get("Scope Confirmed") is True:
    raise PolicyViolation("Scope Confirmed=true is human-only")

if actor == AUTOMATION and target_db == AUTOMATION_QUEUE:
    if "Decision" in patch and patch["Decision"] != "Pending":
        raise PolicyViolation("Automation Queue Decision is human-owned")
    if patch.get("State") in {"APPROVED", "REJECTED", "APPLIED"}:
        raise PolicyViolation("Automation cannot promote approval state")
```

These guards apply at the adapter/domain write boundary, not only in proposal-producing callers.

Human-approval application must use an explicitly separate code path.

## 15.2 Proposal and approval lifecycle

Proposal producers may include:

```text
revision matcher
Goodnotes matcher
Material Usage proposer
page-range proposer
Exam scope proposer
```

All human-gated proposals flow through:

```text
proposal producer
→ create/upsert Automation Queue item
→ State=PENDING_REVIEW
→ Decision=Pending

human reviewer
→ Decision=Approve | Reject

ApprovalReader
→ Decision=Approve → State=APPROVED
→ Decision=Reject  → State=REJECTED

HumanApprovalApplier
→ State=APPROVED → revalidate → APPLIED | FAILED | SUPERSEDED
```

State/field ownership is explicit:

```text
Decision
→ HUMAN-owned

State=PENDING_REVIEW
→ proposal-producer/system-owned

State=APPROVED / REJECTED
→ APPROVAL_READER-owned derivation from the human Decision field

State=APPLIED
→ HUMAN_APPROVAL_APPLIER-owned

State=SUPERSEDED
→ system invalidation path only

State=FAILED
→ defined application failure path only
```

Automation may create/update proposal metadata and system-owned states, but it may not manufacture a human `Decision`, and ordinary `AUTOMATION` may not directly write `APPROVED`, `REJECTED`, or `APPLIED`.

`ApprovalReader` is a distinct internal capability:

```text
AUTOMATION
APPROVAL_READER
HUMAN_APPROVAL_APPLIER
```

`APPROVAL_READER` may read the human-owned `Decision` and derive the corresponding approval `State`, but it may never mutate `Decision` itself.

### 15.2.1 `HumanApprovalApplier`

The approval applier is a separate application capability.

Conceptual authority contexts:

```text
AUTOMATION
HUMAN_APPROVAL_APPLIER
```

`HUMAN_APPROVAL_APPLIER` is not selected by a caller-supplied free-form string.

Likewise, `APPROVAL_READER` is an internal capability and is not caller-selectable through arbitrary request data.

Neither capability is exposed as a general MCP tool.

An approval application requires:

```text
valid Proposal ID
proposal State == APPROVED
Decision == Approve
current target still exists
proposal not already APPLIED/REJECTED/SUPERSEDED
source fingerprint still valid where applicable
proposed relationship/action still structurally valid
```

Only then may the applier perform the human-authoritative mutation.

Examples:

```text
MATERIAL_USAGE approval
→ may set Verified=true for exactly the approved relation

EXAM_SCOPE approval
→ may set Scope Confirmed=true for exactly the approved scope

MATERIAL_REVISION approval
→ may bind the new source version to the approved existing Material
```

The approval applier records:

```text
Decision By
Decision At
Applied At
```

and transitions the proposal to `APPLIED`.

### 15.2.2 Stale approval handling

Approval is not an unconditional future write authorization.

Before application, revalidate:

```text
source hash/version changed?
target entity changed/deleted?
proposal superseded?
relationship no longer valid?
```

If the approved proposal is no longer safely applicable:

```text
APPROVED
→ SUPERSEDED
or
→ FAILED
```

The system must not silently apply an approval generated against stale source state.

### 15.2.3 Rejection

```text
Decision = Reject
→ State = REJECTED
```

Rejected proposals cannot later be applied unless a new proposal with a new Proposal ID is created.

### 15.2.4 Required approval tests

```text
automation Verified=true → DENY
automation Scope Confirmed=true → DENY

approval applier without approved Proposal ID → DENY
approval applier with stale/superseded proposal → DENY
approval applier with wrong target/action → DENY

approved current MATERIAL_USAGE proposal → ALLOW exact Verified=true mutation
approved current EXAM_SCOPE proposal → ALLOW exact Scope Confirmed=true mutation

reapplying APPLIED proposal → idempotent/no duplicate mutation
rejected proposal → DENY
```

---

# 16. Normalized Markdown Contract

Required schemas:

```text
uls.material.v1
uls.annotation.v1
uls.transcript.v1
uls.activity.v1
```

Front matter includes:

```yaml
schema: uls.material.v1
entity_id: COMP319-M03
course_key: 2026-1_COMP319-002
source_ref:
  provider: google_drive
  file_id: "..."
  web_url: "..."
source_hash: sha256:...
source_version: 2
processor_version: "1.2.0"
normalized_at: "..."
status: ready
```

Allowed status:

```text
pending
processing
ready
partial
needs_review
failed
```

Page locator example:

```text
COMP319-M03:p13
```

Timestamp locator example:

```text
COMP319-S05:t00:31:20
```

---

# 17. Commit Ordering

For derived output:

```text
job = PROCESSING
→ write staged derivative
→ validate
→ atomically publish
→ update Notion metadata/reference
→ persist processing record
→ job = READY last
```

If only partial extraction is valid:

```text
job = PARTIAL
Notion Text Status = Partial
derivative status = partial
```

Never:

```text
partial derivative
→ READY
```

without a later successful validation/reprocessing event.

---

# 18. Freshness Contract

Every persistent enrichment stores:

```text
based_on_source_version
based_on_source_hash
processor_version
```

Current source fingerprint is checked before enrichment enters factual context.

Outcome:

```text
MATCH
→ FRESH

MISMATCH
→ STALE
```

STALE:

- may contribute symbolic routing hints,
- cannot contribute factual evidence,
- cannot provide an absolute locator directly against the current source.

---

# 19. Stale Locator Revalidation

This is mandatory.

Given:

```text
stale index built on M03 source v1
topic = "Master theorem"
old locator = M03:p23
current source = v2
```

the engine must not do:

```text
read current M03:p23
```

merely because the stale index points there.

Instead:

```text
stale artifact
→ discard old absolute locator
→ retain symbolic hint ("Master theorem", heading, entity)
→ search current normalized derivative/index
→ resolve current locator
→ validate current fingerprint
→ retrieve current chunk
```

If no current locator can be resolved:

```text
stale hint discarded
```

This rule applies to page numbers and timestamp ranges.

---

# 20. Retrieval Engine Contract

Main service:

```python
class RetrievalEngine:
    def resolve_entity(...): ...
    def get_material_context(...): ...
    def get_session_context(...): ...
    def search_concept(...): ...
    def get_exam_context(...): ...
    def get_activity_context(...): ...
    def get_user_context(...): ...
    def verify_claim(...): ...
    def get_source_chunk(...): ...
```

The engine is callable directly in tests.

The MCP layer is a thin schema/transport facade over it.

---

# 21. Source Authority Policy

Intent-aware policy functions:

```text
policy_for_session()
policy_for_concept()
policy_for_exam()
policy_for_activity()
policy_for_user_note()
policy_for_verify()
```

Examples:

## SESSION

Primary evidence:

```text
professor transcript
professor material
verified Material Usage
```

## EXAM

First filter:

```text
Scope Confirmed
```

then source authority.

## ACTIVITY

Official instructions are supplied as the highest-authority constraint set.

## VERIFY

AI enrichment cannot satisfy factual verification.

## USER_NOTE

USER source can be primary for questions about the user's own notes.

---

# 22. CONCEPT Retrieval v1.2

No vector DB or embedding index.

Pipeline:

```text
resolve Course
→ exact aliases
→ Topics
→ compact Content Index
→ normalized headings
→ lexical matching
→ bounded candidate set
→ optional bounded LLM rerank
→ page/timestamp chunk selection
```

Limits:

```text
max candidate entities
max candidate chunks
max rerank input size
```

are configuration-controlled.

The optional LLM reranker receives only bounded candidate metadata/snippets.

It may not receive the whole course corpus merely to find relevance.

If confidence remains low:

```text
return broader candidate entities
or
ask user to narrow the concept
```

Semantic/vector search is v1.5+.

---

# 23. MCP Server Capability Model

v1.2 MCP is read-only.

Allowed:

```text
resolve
retrieve
search
verify
bounded chunk follow-up
```

Not exposed:

```text
Drive writes
Notion writes
GitHub writes
Verified mutation
Scope Confirmed mutation
USER note mutation
source deletion
public sharing
```

Generic storage methods such as:

```text
drive.read_arbitrary
notion.search_arbitrary
```

are not normal MCP tools.

---

# 24. MCP Tool Schemas

The exact serialization format follows the selected MCP SDK, but domain semantics are frozen here.

## 24.1 `uls.ping`

Purpose:

```text
Spike C0 connectivity only
```

Input:

```json
{}
```

Output:

```json
{
  "ok": true,
  "service": "uls",
  "protocol_version": "1.2"
}
```

No academic data.

## 24.2 `uls.resolve_entity`

Input:

```json
{
  "course_hint": "COMP319",
  "query": "5강",
  "entity_type": "session"
}
```

Output:

```text
resolved
or
ambiguous with resolution_id/candidates
```

## 24.3 `uls.select_resolution`

This tool is the concrete MCP projection of the frozen design's multi-turn `resolution_id + candidate_id` resolution flow. It is a read-only protocol helper and does not expand MCP authority beyond the frozen design.


Input:

```json
{
  "resolution_id": "res_...",
  "candidate_id": "cand_..."
}
```

Returns resolved entity or expiry/error.

## 24.4 `uls.get_material_context`

Input:

```json
{
  "material_id": "COMP319-M03",
  "query": "핵심 내용",
  "include_user_annotations": true
}
```

Returns:

```text
ContextPackage
context_id
allowed locator metadata
```

## 24.5 `uls.get_session_context`

Input:

```json
{
  "session_id": "COMP319-S05",
  "query": "5강 정리",
  "include_provisional": false
}
```

Returns:

- session metadata,
- transcript chunks,
- verified material chunks,
- allowed USER annotation context,
- source fingerprints,
- provenance,
- `context_id`.

## 24.6 `uls.search_concept`

Input:

```json
{
  "course_key": "2026-1_COMP319-002",
  "concept": "Big-O",
  "include_textbook": false
}
```

Returns bounded candidate context under §22.

## 24.7 `uls.get_exam_context`

Input:

```json
{
  "exam_id": "COMP319-E01",
  "query": "중간고사 문제 내줘"
}
```

Engine must code-enforce the **provided evidence boundary**.

Output includes:

```text
scope.status
scope.hard_boundary
allowed sources
warnings
context_id
```

## 24.8 `uls.get_activity_context`

Returns:

- official instructions,
- constraint metadata,
- related course sources,
- result source,
- exact GitHub ref where relevant,
- context capability.

## 24.9 `uls.get_user_context`

Read-only USER notes/annotation retrieval under an explicit user-related query.

## 24.10 `uls.verify_claim`

Input:

```json
{
  "course_key": "...",
  "claim": "...",
  "entity_hint": "COMP319-S05"
}
```

Returns factual source evidence.

AI enrichment may locate candidate evidence but never satisfy the final verification result by itself.

## 24.11 `uls.get_source_chunk`

Input:

```json
{
  "context_id": "ctx_...",
  "locator": "COMP319-M03:p18"
}
```

The server must authorize the locator against the context capability.

Failure:

```text
CONTEXT_EXPIRED
POLICY_DENIED
LOCATOR_NOT_ALLOWED
LOCATOR_STALE
SOURCE_UNAVAILABLE
```

An arbitrary source locator without a valid context capability is rejected.

---

# 25. Context Capability Contract

A context-producing tool creates:

```text
context_id
allowed locator set/ranges
fingerprints
caller scope where available
expiry
```

Example internal representation:

```json
{
  "context_id": "ctx_...",
  "allowed": [
    {
      "entity_id": "COMP319-M03",
      "locator_range": "p13-p27",
      "source_hash": "sha256:...",
      "source_version": 2
    }
  ],
  "expires_at": "..."
}
```

Authorization checks:

1. context exists,
2. context not expired,
3. caller scope matches where enforced,
4. locator lies within allowlist/range,
5. current source fingerprint matches capability fingerprint,
6. requested role/source class remains allowed.

If fingerprint changed after issuance:

```text
LOCATOR_STALE
```

Client must repeat the parent context call.

---

# 26. Retrieval Context Size

The engine should return narrow context.

Recommended configurable budgets:

```text
max evidence items
max characters/tokens per evidence item
max total context size
max follow-up chunks
```

The exact defaults are implementation-tunable and are not architecture invariants.

The engine must preserve provenance when truncating.

---

# 27. Read-Only Credential Separation

## Worker credentials

Write-capable where required:

```text
Drive read/write
Notion read/write
GitHub read
```

## MCP credentials

Where provider supports it:

```text
Drive read-only
Notion read-only
GitHub read-only
```

This is the default required security profile.

If a provider cannot provide separate RO credentials:

- document it,
- use application-level read-only adapter,
- do not expose write methods to MCP,
- test that MCP call graph cannot reach write adapters.

Credentials are never stored in:

```text
Skills
ChatGPT instructions
committed config
logs
```

---

# 28. Remote MCP Transport and Authentication

Remote MCP is optional per client profile but mandatory for a client that cannot consume local MCP.

Minimum:

```text
HTTPS/TLS
authenticated
read-only tools
no unauthenticated public endpoint
```

Preferred:

```text
OAuth/OIDC
```

Development-only fallback:

```text
short-lived bearer token over TLS
```

Long-lived shared tokens are discouraged.

Remote transport must not expose the worker credential.

## 28.1 Single-user authorization scope

v1.2 is a single-user system and does not implement fine-grained per-course authorization.

Therefore, possession of a valid remote MCP bearer credential may permit retrieval across the user's ULS corpus, subject to the domain retrieval policies.

Consequences:

- bearer credentials are high-value secrets,
- development bearer credentials must be short-lived,
- logs must never include them,
- they must not be embedded in Skills/Instructions,
- per-course/user ACLs are a future feature, not an implied v1.2 guarantee.

---

# 29. Remote Availability Contract

When Retrieval Engine/remote bridge run on the Primary PC:

```text
Primary awake + network online + bridge online
→ remote retrieval available

Primary asleep/offline
→ remote retrieval unavailable
```

This is an accepted v1.2 limitation.

Always-on mobile/web study is not guaranteed by the local-primary profile.

A later always-on retrieval host may be introduced without moving:

- canonical Drive source,
- Notion authority,
- ingestion worker,
- SQLite worker state

to the cloud.

---

# 30. Client Support Matrix

Each client integration has:

```text
VALIDATED
EXPERIMENTAL
DEPLOYMENT_DEFERRED
```

Example:

| Client Profile | Status | Meaning |
|---|---|---|
| Claude local MCP | Experimental/Validated | depends on completed E2E |
| ChatGPT remote MCP/App | Experimental/Validated/Deployment Deferred | depends on C0/VS0-B |
| Future MCP client | Deployment Deferred | no E2E yet |

`VALIDATED` requires domain-tool E2E, not merely `uls.ping`.

`Spike C0 PASS` alone is insufficient for `VALIDATED`.

---

# 31. Behavior Contract

Canonical:

```text
contracts/study-behavior.md
```

It contains at least:

- intent mapping,
- tool-selection rules,
- SOURCE / AI / USER response labeling,
- provisional-scope wording,
- conflict handling,
- missing-evidence behavior,
- external-knowledge labeling,
- VERIFY behavior,
- ambiguity/clarification behavior,
- recovery behavior for expired/stale ephemeral capabilities.

Required recovery rules:

```text
RESOLUTION_EXPIRED
→ repeat parent entity-resolution call
→ obtain new resolution_id/candidate set

CONTEXT_EXPIRED
→ repeat parent context-producing tool call
→ obtain new context_id
→ retry dependent chunk request once

LOCATOR_STALE
→ repeat parent context-producing tool call
→ let Retrieval Engine resolve against current source fingerprint
→ do not reuse old locator directly
```

Client projections should retry these recovery flows at most once automatically before surfacing the failure to the user.

The Behavior Contract is not responsible for enforcing source access.

---

# 32. Behavior Contract Projection

Client projections:

```text
Claude Skills
ChatGPT Instructions/App
```

must declare:

```text
behavior_contract_version
behavior_contract_hash
```

Example:

```yaml
behavior_contract_version: 1
behavior_contract_hash: sha256:...
```

CI script computes canonical contract hash and compares projections.

Failure:

```text
projection version/hash mismatch
→ CI FAIL
```

Manual client-specific syntax changes must not silently modify the semantic rules.

---

# 33. Goodnotes v1.2

Required production level:

```text
Level 2 — page-level annotated visual
```

Normalization output may include:

```text
USER annotation text
page visual required flag
annotated PDF source locator
confidence
```

Fine-grained geometry is optional.

If OCR/math parsing is unreliable:

```text
do not fabricate
→ preserve annotated page locator
```

---

# 34. GitHub Adapter Contract

Read-only:

```text
validate_repository()
validate_ref()
list_tree()
read_file()
read_at_ref()
```

Submitted work:

```text
Submission Ref
```

is evidence.

Never silently substitute:

```text
current main
```

for the stored exact ref.

---

# 35. LLM Adapter Contract

LLM is used for enrichment and optional bounded reranking.

Operations may include:

```text
enrich_session
enrich_material
parse_activity_requirements
propose_material_usage
propose_goodnotes_match
propose_exam_scope
rerank_bounded_concept_candidates
```

Structured outputs include:

```text
output
evidence
confidence
proposal/fact classification
model/provider provenance
based_on fingerprint
```

LLM cannot mutate human-only fields.

---

# 36. Retry and Rate-Limit Policy

Error classes:

```text
TRANSIENT
RATE_LIMITED
PERMANENT
AMBIGUOUS
POLICY_DENIED
```

## TRANSIENT

Retry with exponential backoff + jitter.

## RATE_LIMITED

- honor `Retry-After` where available,
- otherwise exponential backoff + jitter,
- bounded attempts.

## PERMANENT

Fail without pointless retry.

## AMBIGUOUS

Transition to:

```text
NEEDS_REVIEW
```

or return ambiguity to interactive caller.

## POLICY_DENIED

No retry.

Recommended default processing retry limit:

```text
3 attempts
```

Provider-specific overrides allowed.

Infinite retry forbidden.

---

# 37. Logging and Observability

Structured log fields:

```text
timestamp
level
operation
job_id
entity_id
request_id
context_id when safe
provider
duration_ms
attempt
error_class
```

Never log:

- credentials,
- bearer tokens,
- full OAuth payloads,
- unnecessary whole source bodies,
- raw user notes where metadata suffices.

MCP request logs should record tool name/result class, not full sensitive content by default.

---

# 38. CLI Contract

Required:

```text
uls init
uls doctor

uls sync
uls process
uls run

uls status
uls jobs
uls retry <job-id>
uls reprocess <entity-id>

uls mcp local
uls mcp remote
uls mcp status

uls behavior lint
```

Optional convenience commands may be added.

Not required:

```text
uls worker takeover
uls reconcile --full
```

## `uls doctor`

Checks:

- config schema,
- StateStore,
- Drive worker credentials,
- Drive MCP credentials when configured,
- Notion worker credentials,
- Notion MCP credentials when configured,
- GitHub read capability,
- local MCP server startability,
- remote MCP config consistency,
- Behavior Contract projection hashes.

It cannot by itself prove a third-party AI client UX.

---

# 39. Scheduler Adapters

After retrieval vertical slices pass:

```text
macOS
→ launchd
→ uls run

Windows
→ Task Scheduler
→ uls run
```

Same core command.

No business logic in scheduler definitions.

Local overlapping scheduled runs must be prevented by local process lock.

---

# 40. Local Worker Lock

v1.2 protects against overlapping work on the same Primary PC.

Implementation options:

```text
SQLite lock row
or
OS-independent lock file
```

Requirement:

- atomic acquisition,
- stale lock recovery strategy,
- PID/host metadata for diagnostics,
- no cross-device lease claim.

Distributed locking is out of scope.

---

# 41. Implementation Validation Order

The order is frozen.

## Stage C0 — ChatGPT Remote MCP Feasibility

Minimum:

```text
remote authenticated read-only MCP
└─ uls.ping()
```

Acceptance:

- target environment can register/connect,
- tool discovery succeeds,
- trivial invocation succeeds,
- remote transport mechanism recorded,
- authentication mechanism recorded,
- plan/workspace/surface limitations recorded.

C0 PASS means **connectivity only**.

It does not mean ChatGPT support is `VALIDATED`.

If unavailable:

```text
ChatGPT profile = DEPLOYMENT_DEFERRED
```

and core development may continue.

## Stage M0 — MCP Domain Contract Reality Check

Minimum:

```text
hard-coded Material fixture
→ Retrieval Engine
→ get_material_context()
→ local MCP
→ low-friction MCP client
```

Acceptance:

- domain tool schema works,
- ContextPackage works,
- locator provenance survives,
- no storage-generic tool is needed.

## Stage VS0 — Real Material Vertical Slice

```text
one professor PDF
→ Drive source
→ normalize
→ Notion Material
→ Retrieval Engine
→ MCP
→ supported AI client
```

Acceptance:

- no duplicate ingestion,
- original unchanged,
- normalized page locators present,
- grounded answer without manual attachment,
- VERIFY path reaches source.

## Stage VS0-B — Cross-Client Domain Compatibility

Where second client is available:

- same domain tool semantics,
- same evidence boundary,
- same provenance schema,
- context capabilities work,
- multi-turn ambiguity works,
- larger real ContextPackage works.

Only after this may the second client be marked `VALIDATED`.

## Stage G — Goodnotes Spike

Choose:

```text
A granular diff
B text + visual hybrid
C visual primary
```

Level 2 must work.

---

# 42. Phase 1 — Core Hardening

Implement:

- config validation,
- StateStore,
- EphemeralStore,
- migrations,
- idempotent jobs,
- source versions,
- local lock,
- retry model,
- logging,
- human-field guards.

Acceptance:

- migration repeatable,
- deterministic `job_key` duplicate job rejected,
- source-bound entity allocation reuses existing `canonical_entity_id` on retry,
- capability TTL works,
- restart invalidates ephemeral state,
- partial state consistent,
- automation human-only writes rejected,
- Automation Queue proposal lifecycle is idempotent,
- approved proposal is revalidated before human-authoritative mutation,
- macOS/Windows unit suite passes.

---

# 43. Phase 2 — Transcript Vertical Slice

Implement:

- Alt source ingestion,
- normalized transcript,
- timestamps,
- Session metadata,
- aliases,
- `get_session_context`.

Acceptance:

- timestamps preserved,
- transcript not silently corrected,
- stale transcript enrichment filtered,
- “5강 정리” resolves via alias/Session No,
- context capability authorizes only returned transcript/material ranges.

---

# 44. Phase 3 — Enrichment and Freshness

Implement:

- Session Summary,
- Topics,
- Content Index,
- Professor Emphasis,
- Professor Examples,
- Exam Signals,
- Likely Confusions,
- source fingerprint metadata.

Acceptance:

- explicit/inferred separation,
- evidence locators,
- stale enrichment removed from factual evidence,
- stale absolute locators are re-resolved or discarded.

---

# 45. Phase 4 — Material Usage and Human Gate

Implement:

- Material Usage proposals,
- page-range proposals,
- review/approval path,
- Verified state,
- multi-material Session retrieval.

Acceptance:

- AI cannot set Verified true,
- adapter guard blocks prohibited write,
- Material Usage proposal enters Automation Queue,
- only approved/current proposal can be applied through HumanApprovalApplier,
- stale/superseded approval is denied,
- confirmed relation immediately affects retrieval source set,
- unverified relation behavior follows configured intent policy.

---

# 46. Phase 5 — Exam and Activity

Implement:

- Exam scope,
- Activity instructions,
- Exam/Activity context tools,
- provisional state,
- user-facing warnings.

Acceptance:

- engine restricts **supplied Exam evidence** to confirmed scope,
- unconfirmed scope labeled provisional in context schema,
- Exam scope proposal enters Automation Queue,
- only approved/current scope proposal can set `Scope Confirmed=true`,
- official Activity instructions have highest supplied constraint authority,
- client Behavior Contract tests ensure prose does not mislabel external/pretrained knowledge as supplied course evidence.

---

# 47. Phase 6 — GitHub Retrieval

Implement:

- repo validation,
- exact ref validation,
- tree/read at ref,
- Activity result linkage.

Acceptance:

- exact Submission Ref used,
- invalid ref fails visibly,
- current branch not substituted,
- MCP remains read-only.

---

# 48. Phase 7 — Client Packaging

Implement supported client projections:

```text
Claude Skills/package
ChatGPT Instructions/App
```

as deployment capability permits.

Acceptance:

- Behavior Contract hash/version matches,
- client support status recorded,
- no client-specific policy enters Retrieval Engine,
- unsupported client remains Deployment Deferred rather than causing architectural workaround.

---

# 49. Phase 8 — Desktop Automation and Remote MCP Profile

Implement:

- `launchd`,
- Task Scheduler,
- local MCP startup,
- remote authenticated MCP profile,
- basic status/health,
- local backup guidance.

Acceptance:

- same `uls run`,
- same Retrieval Engine,
- remote MCP uses RO credentials,
- offline Primary limitation documented,
- public sharing never used as workaround.

---

# 50. Model-Independent Contract Test Matrix

The following are release blockers.

## 50.1 Exam scope

Given:

```text
Scope Confirmed=true
Included Sessions=S01,S02
S03 out of scope
```

Expect:

```text
S01/S02 evidence allowed
S03 course evidence excluded
```

## 50.2 Unconfirmed exam

Expect:

```text
scope.status=provisional
hard_boundary=false
```

## 50.3 Material Usage

Given:

```text
M03 Verified=false
```

Expect default confirmed-only retrieval excludes it unless intent explicitly allows provisional use.

## 50.4 Freshness

Given:

```text
current source v3
enrichment based on v2
```

Expect:

```text
enrichment=STALE
not factual evidence
```

## 50.5 Stale locator

Given:

```text
old locator p23
current source fingerprint differs
```

Expect:

```text
old p23 not directly read
symbolic re-resolution attempted
```

## 50.6 Capability allowlist

Given:

```text
context allows M03:p13-p27
```

Expect:

```text
M03:p18 → ALLOW
M03:p30 → DENY
M99:p1 → DENY
```

## 50.7 Capability expiry

Expired context:

```text
get_source_chunk
→ CONTEXT_EXPIRED
```

## 50.8 Restart invalidation

MemoryEphemeralStore restart:

```text
old resolution_id → invalid
old context_id → invalid
```

## 50.9 Resolution integrity

Candidate from a different resolution handle:

```text
→ POLICY_DENIED / INVALID_SELECTION
```

## 50.10 Human gate

Automation attempts:

```text
Verified=true
Scope Confirmed=true
```

must fail.

## 50.11 Partial

Partial normalized derivative must never result in job/Notion Ready.

## 50.12 GitHub

Stored Submission Ref must be used exactly.

## 50.13 MCP write absence

MCP tool registry must contain no write/approval/delete/public-share tool.

## 50.14 Behavior projection drift

Hash mismatch:

```text
→ CI FAIL
```

## 50.15 Concept retrieval bounds

Vector/embedding backend absent.

Expect:

```text
bounded lexical/index path succeeds
or asks for narrowing
```

No hidden full-corpus model dump.


## 50.16 Locator parser

Valid canonical cases:

```text
M03:p13
M03:p13-p27
S05:t00:10:00-00:40:00
M03:p13:user
```

Malformed/reversed ranges are rejected.

Canonical serialization must be stable across platforms.

## 50.17 Locator containment

Required:

```text
allow M03:p13-p27
request M03:p18
→ ALLOW

allow M03:p13-p27
request M03:p30
→ DENY

allow M03:p13-p27
request M03:p13:user
→ DENY

allow M03:p13-p27:user
request M03:p13:user
→ ALLOW

allow S05:t00:10:00-00:40:00
request S05:t00:31:20
→ ALLOW
```

Containment uses parsed numeric fields only.

## 50.18 `job_key` determinism

Same canonical tuple:

```text
source_file_id
source_hash
operation
processor_version
```

must yield identical `job_key` across process restart and macOS/Windows.

Changing any canonical field changes the key.

Volatile metadata must not affect the key.

## 50.19 Entity allocation retry idempotency

Simulate:

```text
source has no canonical_entity_id
→ allocate M03
→ persist canonical_entity_id
→ crash before later stage
→ retry
```

Expect:

```text
reuse M03
do not allocate M04
```

Concurrent attempts must converge on one canonical ID.

## 50.20 Automation Queue lifecycle

Required scenarios:

```text
proposal creation retry → one Proposal ID/item
Pending → Approve → Approved → Applied
Pending → Reject → Rejected
Approved + stale source → SUPERSEDED/FAILED, no mutation
Applied proposal replay → idempotent
Rejected proposal → cannot apply
```

## 50.21 Human approval authority

Required:

```text
AUTOMATION + Material Usage.Verified=true → DENY
AUTOMATION + Exam.Scope Confirmed=true → DENY

AUTOMATION + Automation Queue.Decision=Approve → DENY
AUTOMATION + Automation Queue.Decision=Reject → DENY
AUTOMATION + Automation Queue.State=APPROVED → DENY
AUTOMATION + Automation Queue.State=REJECTED → DENY
AUTOMATION + Automation Queue.State=APPLIED → DENY

APPROVAL_READER + human Decision=Approve
→ may derive State=APPROVED

APPROVAL_READER + human Decision=Reject
→ may derive State=REJECTED

APPROVAL_READER + Decision mutation
→ DENY

HUMAN_APPROVAL_APPLIER without valid approved proposal
→ DENY

HUMAN_APPROVAL_APPLIER with exact valid current proposal
→ ALLOW exact approved mutation only
```

The tests must demonstrate that no ordinary automation path can satisfy all preconditions of `HumanApprovalApplier` by self-approving its own proposal.

## 50.22 Alias parsing

Rich-text aliases:

```text
5강 | 5번째 강의 | CPU Scheduling
```

must parse deterministically without creating Notion Select/Multi-select options.

Course aliases participate in course-first resolution.

## 50.23 Expiry/stale recovery contract

Client projection tests must verify:

```text
RESOLUTION_EXPIRED → re-resolve once
CONTEXT_EXPIRED → recreate parent context once
LOCATOR_STALE → recreate parent context, never direct old-locator retry
```

---

# 51. Integration Tests

Use fake/in-memory adapters for most integration tests.

Required fakes:

```text
FakeDriveAdapter
FakeNotionAdapter
FakeGitHubAdapter
FakeLLMAdapter
MemoryEphemeralStore
SQLiteStateStore(temp db)
```

Optional live smoke tests:

- test Google Drive folder,
- test Notion workspace,
- test GitHub repo,
- test remote MCP endpoint.

Live tests must not be required for every CI run.

---

# 52. Client E2E Tests

For each supported client profile:

```text
[ ] MCP connection works
[ ] resolve entity works
[ ] ambiguity round-trip works
[ ] domain context call works
[ ] context package is usable
[ ] get_source_chunk chaining works
[ ] provenance can be surfaced
[ ] course evidence boundary matches server output
[ ] no arbitrary Drive/Notion browsing required
```

These are manual/external-runtime tests where CI cannot reproduce the client.

C0 alone does not satisfy this checklist.

---

# 53. Error Taxonomy

Domain/API errors should be structured.

Examples:

```text
ENTITY_NOT_FOUND
ENTITY_AMBIGUOUS
RESOLUTION_EXPIRED
INVALID_CANDIDATE
CONTEXT_EXPIRED
LOCATOR_NOT_ALLOWED
LOCATOR_STALE
POLICY_DENIED
SOURCE_UNAVAILABLE
SOURCE_PARTIAL
STALE_ENRICHMENT
INVALID_SUBMISSION_REF
PROVIDER_RATE_LIMITED
PROVIDER_UNAVAILABLE
```

MCP tools return safe structured errors rather than raw stack traces.

---

# 54. Security Acceptance

Release blockers:

- remote MCP never unauthenticated/public,
- MCP registry read-only,
- MCP credentials least privilege where provider permits,
- worker credentials not reused remotely where separation is available,
- public Drive sharing not enabled automatically,
- capability IDs are random/opaque,
- capability TTL bounded,
- capability allowlist enforced server-side,
- secrets excluded from logs,
- human-only fields guarded at write boundary,
- source deletion absent from normal automation adapter,
- exact GitHub ref enforced,
- locator containment is parsed/type-safe; raw string-prefix authorization is forbidden,
- approved human-gated mutations require a valid current Proposal ID,
- Automation Queue `Decision` is human-owned and automation-immutable,
- ordinary automation cannot directly write Queue `APPROVED`, `REJECTED`, or `APPLIED`,
- `APPROVAL_READER` can derive approval state but cannot mutate `Decision`,
- bearer credential corpus-wide scope is documented as a single-user v1.2 limitation.

---

# 55. Cross-Platform Acceptance

Required:

```text
Python core tests
SQLite migrations
MemoryEphemeralStore tests
retrieval contract tests
CLI parsing/config loading
```

must pass on:

```text
macOS
Windows
```

OS-specific code is limited to deployment adapters.

---

# 56. Definition of v1.2 Done

v1.2 is complete when:

1. Frozen architecture contracts are implemented.
2. C0 has a recorded outcome for ChatGPT target environment.
3. At least one MCP client is `VALIDATED`.
4. A real PDF VS0 works end to end.
5. Retrieval policies pass model-independent tests.
6. `get_source_chunk` capability chaining is enforced.
7. Ephemeral restart/expiry behavior is tested.
8. Partial/Ready consistency is enforced.
9. Human-gate write protections are tested.
10. stale enrichment and stale locator behavior are tested.
11. Session/transcript retrieval works.
12. Goodnotes Level-2 fallback works.
13. Material Usage/Exam/Activity policies work.
14. GitHub exact-ref retrieval works for project activities.
15. Behavior Contract projections pass drift lint.
16. supported client profiles have recorded support status.
17. remote retrieval never requires public file sharing.
18. macOS/Windows core test profiles pass.
19. Locator parser/containment tests pass.
20. deterministic `job_key` tests pass.
21. source-bound entity allocation retry tests pass.
22. Automation Queue proposal/approval lifecycle tests pass.
23. aliases use Rich text and Course aliases participate in resolution.
24. bounded LLM rerank remains opt-in by default.
25. Automation Queue `Decision` is proven automation-immutable and approval-state ownership tests pass.

A second AI client is highly desirable but is not required to declare the **core** implementation correct if current external product capability prevents that client from consuming the MCP contract.

In that case its status is:

```text
DEPLOYMENT_DEFERRED
```

not silently `VALIDATED`.

---

# 57. Explicit v1.2 Non-Goals

```text
PostgreSQL implementation
Redis/shared EphemeralStore
multi-instance MCP server
distributed workers
cloud ingestion
always-on remote retrieval guarantee
vector DB
embedding semantic search
custom Concepts ontology
MCP write tools
automatic USER note rewriting
automatic Verified=true
automatic Scope Confirmed=true
Goodnotes Level-3 geometry requirement
full cross-PC takeover
full reconciliation engine
custom web dashboard
```

---

# 58. Implementation Freeze Checklist

The following checklist has been incorporated into this frozen specification. Any future implementation change that violates a checked contract requires an implementation-spec revision:

```text
[x] repository/module boundaries are acceptable
[x] SQLite schema is sufficient
[x] EphemeralStore default/TTL/restart behavior is acceptable
[x] SourceRef/fingerprint/locator types are acceptable
[x] exact Notion schemas are acceptable
[x] human-gate adapter guard is acceptable
[x] bounded CONCEPT retrieval is acceptable
[x] MCP tool list and schemas are acceptable
[x] get_source_chunk capability model is acceptable
[x] credential separation/auth profile is acceptable
[x] client support-state semantics are acceptable
[x] C0/M0/VS0/VS0-B/G ordering is acceptable
[x] stale-locator revalidation contract is acceptable
[x] Behavior Contract drift lint is acceptable
[x] test matrix is sufficient
[x] Locator grammar and containment are fully specified
[x] deterministic job_key derivation is specified
[x] source-bound entity allocation is retry-idempotent
[x] Automation Queue and HumanApprovalApplier lifecycle are specified
[x] select_resolution is documented as the concrete read-only resolution-flow projection
[x] Aliases use Rich text and Courses include aliases
[x] bounded LLM rerank defaults to false
[x] capability-expiry recovery behavior is defined
[x] single-user bearer credential corpus-wide scope is documented
[x] Automation Queue Decision/approval-state ownership is guarded and contract-tested
```

Status:

```text
Frozen for v1.2 implementation
```

---

**End of University Learning System v1.2 — Implementation Specification**
