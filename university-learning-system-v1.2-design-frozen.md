# University Learning System v1.2 — Design Specification

**Status:** Frozen for v1.2 implementation  
**Supersedes:** `University Learning System v1.1 — Design Specification`  
**Architecture style:** Model-agnostic · MCP-centered · Local-primary · Single-active-worker · Cross-platform  
**Primary desktop platforms:** macOS, Windows  
**Core implementation language:** Python 3

---

# 0. Revision Summary

v1.2 changes the retrieval control plane introduced in v1.1.

v1.1 correctly moved canonical normalized derivatives from Notion to Google Drive and made end-to-end retrieval a release requirement. However, query-time policy enforcement still depended on a specific AI client directly traversing Notion/Drive connected sources.

v1.2 removes that dependency.

The major architectural changes are:

1. **ULS owns retrieval policy and execution.**
   - Entity resolution, scope filtering, source authority, freshness checks, chunk selection, and provenance are executed by the ULS Retrieval Engine.
   - AI clients no longer traverse Notion/Drive directly as the authoritative retrieval mechanism.

2. **MCP becomes the model-neutral retrieval boundary.**
   - Claude, ChatGPT, and future MCP-capable clients consume the same ULS domain tools.
   - Client-specific integrations become thin projections over the same backend contract.

3. **Skills / instructions are separated from safety enforcement.**
   - The ULS Retrieval Engine decides what data is allowed.
   - Client skills/instructions define how the model should study, explain, verify, and present results.

4. **The MCP surface is read-only in v1.2.**
   - Retrieval clients cannot delete sources, overwrite files, modify USER content, approve `Verified`, or confirm exam scope.

5. **Retrieval Manifest is demoted from required transport to optional deterministic cache.**
   - Notion remains the canonical graph/state layer.
   - The Retrieval Engine may traverse the graph directly.
   - Manifests may accelerate repeated resolution but are disposable.

6. **ChatGPT and Claude become client projections rather than architectural centers.**
   - The ULS Core, Retrieval Engine, and MCP contract are model-agnostic.
   - Actual client availability is deployment-dependent and may vary by plan, workspace, surface, or product capability.
   - Failure of one client integration does not invalidate the ULS retrieval architecture itself.

7. **Remote-client uncertainty is tested before broad implementation.**
   - ChatGPT remote MCP feasibility is validated before the ordinary local-MCP vertical slice.
   - Client-specific support is treated as an external deployment capability, not an assumed invariant.

8. **Concept retrieval is deliberately bounded in v1.2.**
   - v1.2 uses aliases, Topics, Content Index, headings, and lexical matching.
   - Optional LLM reranking may operate only on an already bounded candidate set.
   - embedding/vector semantic retrieval is deferred.

9. **Fine-grained source reads are capability-bound.**
   - `uls.get_source_chunk` may read only locators previously authorized by a policy-checked context call.

10. **Behavior projections are versioned and checked for drift.**
    - Claude Skills and ChatGPT Instructions carry the canonical Behavior Contract version/hash and are linted in CI.

The following established invariants remain unchanged:

- SOURCE / AI / USER separation,
- Google Drive as canonical original-file and normalized-derivative corpus,
- GitHub as canonical project-code source,
- Notion as graph/state/verification layer,
- normalization is not summarization,
- no destructive source overwrite or automatic source deletion,
- USER-managed content is not overwritten by automation,
- AI cannot set `Material Usage.Verified = true`,
- AI cannot set `Exam.Scope Confirmed = true`,
- `Partial` never silently becomes `Ready`,
- provenance and locators are preserved,
- source conflicts are surfaced instead of silently reconciled.

---

# 1. Purpose

University Learning System (ULS) is a personal academic knowledge and retrieval system.

Its purpose is to let the user ask natural study questions through multiple AI clients without manually assembling context.

Examples:

```text
"5강 정리해줘"
"Big-O를 강의 기준으로 쉽게 설명해줘"
"중간고사 확정 범위에서 문제 내줘"
"HW2 요구사항 다시 확인해줘"
"교수님이 정말 시험에 나온다고 말했어?"
"내가 13페이지에 뭐라고 필기했지?"
```

The target flow is:

```text
Lecture / transcript / slides / textbook / assignments / Goodnotes / code
                            ↓
              Original + normalized corpus
                            ↓
                Academic graph and state
                            ↓
                  ULS Retrieval Engine
                            ↓
                     MCP interface
                            ↓
             Claude / ChatGPT / future client
                            ↓
        Explain / Study / Practice / Exam / Verify
```

The two top-level goals are:

1. **Natural retrieval:** the user can ask questions without manually attaching all relevant files.
2. **Source integrity:** the system preserves what came from professor sources, user sources, AI inference, and external knowledge.

---

# 2. Core Architectural Principle

v1.2 adopts the following rule:

> **ULS determines what context is allowed and relevant.**  
> **AI clients reason over the context ULS provides.**

This replaces the v1.1 model:

```text
AI client
→ search Notion
→ follow Drive links
→ interpret scope/policy itself
```

with:

```text
AI client
→ call ULS domain tool
→ ULS resolves graph/scope/freshness/authority
→ ULS returns bounded context
→ AI reasons over returned context
```

This distinction is the central architectural change in v1.2.

---

# 3. System Roles

| Component | v1.2 Role | Canonical Authority |
|---|---|---|
| Alt | Lecture recording/transcript capture | Raw transcript source |
| Goodnotes | USER annotation authoring | Annotated user source |
| Google Drive | Original sources + canonical normalized derivatives | File/source truth |
| GitHub | Version-controlled project source | Project-code truth |
| Notion | Academic graph, relations, verification, learning state, compact enrichment/index | Graph/state truth |
| Python Core | Ingestion, normalization, enrichment, orchestration | Processing engine |
| SQLite StateStore | Active local execution state | Local orchestration truth |
| **ULS Retrieval Engine** | Entity resolution, policy, freshness, chunking, provenance | Retrieval-policy authority |
| **ULS MCP Server** | Model-neutral retrieval/capability interface | Tool boundary |
| Behavior Contract | Model-neutral study workflow rules | Behavior contract |
| Claude client integration | Skill/package projection | Client UX |
| ChatGPT client integration | Instructions/App projection | Client UX |
| Future AI clients | MCP-compatible client projection | Client UX |

Core split:

> **Drive = original sources + deterministic normalized derivatives**  
> **GitHub = version-controlled project source**  
> **Notion = academic graph + human state + compact index/enrichment**  
> **Retrieval Engine = allowed context + source policy**  
> **MCP = model-neutral access boundary**  
> **AI clients = reasoning + teaching interface**

---

# 4. Authority Model

## 4.1 Original source authority

| Data | Canonical Source |
|---|---|
| Professor lecture PDF | Google Drive source |
| Goodnotes annotated PDF | Google Drive source |
| Lecture recording | Google Drive source |
| Raw Alt transcript export | Google Drive source |
| Textbook/reference PDF | Google Drive source |
| Assignment/practice/exam instructions | Google Drive source |
| Small practice output | Google Drive source |
| Project-style source code | GitHub exact ref |
| Large datasets/models/artifacts | Google Drive source |

## 4.2 Canonical normalized derivatives

| Data | Canonical Location |
|---|---|
| Normalized professor material | Drive `derived/*.normalized.md` |
| Normalized transcript | Drive `derived/*.normalized.md` |
| Normalized activity instructions | Drive `derived/*.normalized.md` |
| Normalized Goodnotes annotation representation | Drive `derived/annotations.normalized.md` |

A normalized derivative is authoritative only as the deterministic normalized representation of a particular source version.

It never outranks the original source for verification.

## 4.3 Notion authority

Notion remains authoritative for:

- Course identity,
- Session identity,
- Material identity,
- Activity identity,
- Exam identity,
- Material Usage relationships,
- user-confirmed `Verified`,
- user-confirmed `Scope Confirmed`,
- natural-language aliases,
- learning state,
- compact content indices,
- persistent AI enrichment clearly labeled as AI,
- USER-authored Notion regions.

Notion is not the canonical large-text store.

---

# 5. Enforcement Model

v1.2 explicitly distinguishes four enforcement levels.

```text
CODE_ENFORCED
INSTRUCTION_ENFORCED
MANUAL_ACCEPTANCE
FUTURE_ENFORCED
```

## 5.1 CODE_ENFORCED

`CODE_ENFORCED` means that ULS code controls the **set, authority, freshness, and provenance of evidence/context supplied to the model**.

It does **not** mean ULS can fully control what a generative model says from its own pretrained knowledge after receiving that context.

Examples:

- automation cannot set `Verified=true`,
- automation cannot set `Scope Confirmed=true`,
- MCP cannot modify USER content,
- MCP cannot delete/overwrite canonical sources,
- confirmed Exam scope filtering for supplied course evidence,
- official Activity instructions are returned as the highest-authority supplied constraint set,
- stale enrichment exclusion from current factual context,
- exact GitHub `Submission Ref`,
- source-version/hash freshness checks,
- `Partial` cannot become `Ready` without successful validation.

## 5.2 INSTRUCTION_ENFORCED

`INSTRUCTION_ENFORCED` governs how the model uses and describes the context after ULS has bounded the supplied evidence.

Examples:

- do not present out-of-scope/pretrained knowledge as if ULS supplied it,
- respect official Activity constraints in generated advice/solutions,
- distinguish professor source / USER source / AI inference in prose,
- explicitly label external knowledge,
- explain source conflicts instead of pretending they agree,
- maintain the requested teaching style,
- do not overstate what a source proves.

These rules are represented in the model-neutral Behavior Contract and projected into client-specific skills/instructions.

## 5.3 MANUAL_ACCEPTANCE

Examples:

- Claude UX works in the supported client,
- ChatGPT UX works in the supported client,
- tool invocation is natural enough for actual study,
- response grounding is understandable to the user.

## 5.4 FUTURE_ENFORCED

Potential future controls:

- centralized multi-user authorization,
- distributed worker leases,
- advanced retrieval gateway policy,
- cross-device state coordination.

---

# 6. SOURCE / AI / USER Contract

Every academic artifact belongs to one or more clearly labeled ownership zones:

```text
SOURCE
AI
USER
```

## SOURCE

Direct source content or deterministic extraction.

Examples:

- professor PDF text,
- lecture transcript,
- assignment instructions,
- exam notice,
- source locators,
- page/timestamp metadata.

## AI

Model-derived interpretation.

Examples:

- summary,
- inferred importance,
- likely confusion,
- parsed requirements,
- study map,
- concept explanation.

## USER

User-authored or user-confirmed content.

Examples:

- Goodnotes annotations,
- My Notes,
- Questions,
- Weak Areas,
- review state,
- `Material Usage.Verified`,
- `Exam.Scope Confirmed`.

Hard rules:

- AI output is never relabeled as SOURCE.
- USER writing is never silently rewritten.
- Goodnotes is a USER source, not professor SOURCE.
- AI may read USER context but cannot mutate USER content through the retrieval path.

---

# 7. Google Drive Layout

Recommended structure:

```text
University/
└─ 2026-1/
   └─ COMP319-002/
      ├─ Recordings/
      │  └─ S05/
      │     ├─ source/
      │     │  ├─ recording-01.m4a
      │     │  └─ transcript-original.md
      │     └─ derived/
      │        └─ transcript.normalized.md
      │
      ├─ Materials/
      │  └─ M03/
      │     ├─ source/
      │     │  ├─ original_v1.pdf
      │     │  ├─ original_v2.pdf
      │     │  └─ annotated.pdf
      │     └─ derived/
      │        ├─ material.normalized.md
      │        └─ annotations.normalized.md
      │
      ├─ Textbooks/
      │  └─ M20/
      │     ├─ source/original_v1.pdf
      │     └─ derived/material.normalized.md
      │
      ├─ Activities/
      │  └─ A02/
      │     ├─ source/instructions.pdf
      │     ├─ result/
      │     │  └─ solution.py
      │     └─ derived/instructions.normalized.md
      │
      └─ Exams/
         └─ E01/
            └─ source/notice.pdf
```

Principles:

- `source/` contains provider/user originals.
- `derived/` contains reproducible machine-generated derivatives.
- Original source versions are immutable from automation's perspective.
- Derived outputs may be regenerated atomically.
- Historical source versions remain traceable.

---

# 8. Canonical Source Reference

v1.2 removes ad-hoc `drive://...`, `drive:...`, and URL-only identity.

External files use a structured reference.

Example:

```json
{
  "provider": "google_drive",
  "file_id": "1ABCDEF...",
  "web_url": "https://drive.google.com/..."
}
```

Canonical identity:

```text
provider + file_id
```

`web_url` is navigational metadata only.

Equivalent domain concept:

```text
SourceRef
├─ provider
├─ file_id
└─ web_url
```

GitHub references additionally include:

```text
repository
repository_path
ref
```

For submitted work, `ref` is the exact `Submission Ref`.

---

# 9. Academic Identity and Natural-Language Handles

Course key:

```text
{semester}_{course_code}-{section}
```

Example:

```text
2026-1_COMP319-002
```

Entity IDs:

```text
COMP319-S05
COMP319-M03
COMP319-A02
COMP319-E01
```

Natural-language resolution must not rely only on semantic search.

Example Session:

```text
ID: COMP319-S05
Name: 05 · CPU Scheduling

Aliases:
- 5강
- 5번째 강의
- CPU Scheduling
```

Example Material:

```text
ID: COMP319-M03
Name: Algorithmic Analysis II

Aliases:
- Lec3
- Algorithmic Analysis II
- 알고리즘 분석 2
```

Alias rules:

- stable user-facing handles are stored in Notion,
- exact ID / Session No / aliases are preferred over fuzzy interpretation,
- ambiguous resolution returns candidates instead of guessing.

---

# 10. Ingestion and Revision Matching

Course identity is deterministic from the course-specific Inbox.

```text
Inbox/
└─ COMP319-002/
   ├─ Materials/
   ├─ Goodnotes/
   ├─ Sessions/
   ├─ Activities/
   └─ Exams/
```

The user does not need to rename professor downloads into internal IDs.

## Material revision decision

```text
same provider file ID + same hash
→ NO-OP

same provider file ID + new hash
→ AUTO: new version of same Material

new file ID + same hash
→ duplicate candidate; do not create duplicate Material

new file ID + new hash
→ revision-candidate evaluation
```

Revision candidate evidence may include:

- filename stem,
- embedded document title,
- page count,
- prior source filename,
- course,
- document similarity.

Decision policy:

```text
clearly unrelated/new
→ new Material

probable existing-Material revision
→ PROPOSE

ambiguous
→ NEEDS_REVIEW
```

A semantic similarity score alone never automatically merges a new file into an existing Material version history.

---

# 11. Normalization Contract

Normalization is not summarization.

```text
ORIGINAL SOURCE
      ↓
NORMALIZATION
      ↓
CANONICAL NORMALIZED DERIVATIVE
      ↓
RETRIEVAL / ENRICHMENT
```

Every derivative carries provenance:

```text
schema
entity_id
course_key
source_ref
source_hash
source_version
processor_version
normalized_at
status
```

Normalization status is consistent across job state, Notion status, and derivative metadata:

```text
Pending
Processing
Ready
Partial
Needs Review
Failed
```

Hard invariant:

> `Partial` may be usable for explicitly limited operations, but it is never silently represented as `Ready`.

---

# 12. Goodnotes Contract

Goodnotes remains an explicitly high-risk extraction area.

Capability levels:

### Level 1 — Extracted annotation text

Examples:

- short handwritten Korean,
- searchable text layer,
- obvious labels.

### Level 2 — Page-level annotated visual

The system preserves the annotated page as a USER visual source even when granular OCR is unreliable.

### Level 3 — Fine-grained geometry

Examples:

- arrow-to-formula mapping,
- precise underline/circle alignment,
- structured handwritten proof parsing.

v1.2 success requires Level 2.

Level 3 remains best-effort/future.

Fallback:

```text
reliable annotation text
→ normalized USER text

complex/uncertain annotation
→ page-level USER visual locator

uncertain geometry
→ preserve uncertainty
```

---

# 13. Notion Knowledge Model

The six academic databases remain:

1. Courses
2. Sessions
3. Materials
4. Material Usage
5. Activities
6. Exams

Operational review surfaces may exist separately.

Notion stores:

- identity,
- aliases,
- relations,
- human verification state,
- source references,
- content index,
- compact AI enrichment,
- USER learning state.

Notion does not store the canonical full normalized corpus.

---

# 14. ULS Retrieval Engine

The Retrieval Engine is a first-class subsystem.

Responsibilities:

```text
entity resolution
scope enforcement
relationship traversal
source authority
freshness validation
chunk selection
provenance assembly
conflict metadata
context packaging
```

It reads:

```text
Notion graph/state
+
Drive normalized derivatives
+
Drive original source when verification requires it
+
GitHub exact refs when code is required
```

It returns bounded context packages to MCP tools.

The Retrieval Engine—not the AI client—is responsible for determining the allowed source set.

---

# 15. Retrieval Policy Components

## 15.1 Resolver

Resolves:

```text
course
session
material
activity
exam
concept candidate
```

Resolution priority:

1. exact internal ID,
2. explicit Course + number,
3. exact alias,
4. deterministic metadata match,
5. constrained fuzzy candidate search,
6. ambiguous → return stable candidates instead of guessing.

Ambiguous resolution returns a stable multi-turn handle:

```json
{
  "status": "ambiguous",
  "resolution_id": "res_123",
  "candidates": [
    {
      "candidate_id": "cand_1",
      "entity_id": "COMP319-S05",
      "label": "05 · CPU Scheduling"
    }
  ]
}
```

After the user chooses a candidate, the client confirms through the same resolution flow using:

```text
resolution_id + candidate_id
```

The client must not reconstruct the selected entity from display text alone.

## 15.2 Scope

Applies intent-specific hard/provisional boundaries.

## 15.3 Authority

Chooses which source classes may serve as factual evidence.

## 15.4 Freshness

Compares current source fingerprint against enrichment fingerprint.

## 15.5 Chunking and Concept Retrieval

Structured intents such as SESSION, EXAM, ACTIVITY, USER_NOTE, and VERIFY primarily use graph relations and explicit locators.

`CONCEPT` retrieval in v1.2 is intentionally narrower than a full semantic-RAG system.

Candidate selection order:

```text
Course scope
→ exact aliases / Topics
→ compact Content Index
→ normalized headings / keywords
→ lexical matching over bounded metadata/text windows
→ optional bounded LLM rerank of candidate metadata
→ selected page/timestamp chunks
```

Rules:

- full-corpus vector search is not required,
- embeddings/vector DB are not required,
- an LLM reranker may not receive the entire course corpus merely to decide relevance,
- supplemental textbook/reference content is included only when requested or required,
- broad semantic chunk retrieval using embeddings/vector indexes is deferred to v1.5+.

The v1.2 promise is therefore **bounded index/lexical retrieval**, not general semantic search over arbitrary corpora.

## 15.6 Provenance

Every factual context item includes enough metadata to trace it back to its source.

---

# 16. Freshness Contract

Persistent enrichment must record the source state it was derived from.

Example:

```json
{
  "based_on": {
    "source_version": 2,
    "source_hash": "sha256:..."
  },
  "processor_version": "1.2.0"
}
```

If current source fingerprint differs:

```text
enrichment = STALE
```

Rules:

- stale enrichment may be used as routing/index information,
- stale enrichment is excluded from current factual evidence,
- the Retrieval Engine performs this comparison before returning context,
- the AI client is not asked to perform hash/version comparison itself.

---

# 17. Source Authority

Default academic evidence order:

```text
Professor Official Material
        ↓
Professor Transcript
        ↓
Official Assignment / Exam Notice
        ↓
USER Source / USER Notes
        ↓
Supplemental Textbook / Reference
        ↓
AI Enrichment
        ↓
External General Knowledge
```

Intent modifies this policy.

Examples:

### VERIFY

AI enrichment cannot satisfy verification evidence.

### USER_NOTE

USER source may be primary.

### EXAM

Confirmed scope is applied before ordinary source ranking.

### ACTIVITY

Official instructions are hard constraints.

Conflicting sources are returned with conflict metadata rather than silently reconciled.

---

# 18. Retrieval Modes

Primary intents:

```text
SESSION
CONCEPT
EXAM
ACTIVITY
USER_NOTE
VERIFY
```

## SESSION

```text
resolve Session
→ normalized transcript
→ verified Material Usage
→ allowed provisional sources if requested
→ relevant USER annotations
```

## CONCEPT

```text
resolve Course
→ aliases / Topics / Content Index
→ normalized headings + lexical candidate matching
→ optional bounded candidate rerank
→ relevant Session/Material page or timestamp chunks
→ supplemental textbook only when needed/requested
```

v1.2 does not guarantee embedding-based semantic retrieval. If no bounded candidate can be established confidently, the system may return broader candidate entities or ask the user to narrow the concept.

## EXAM

```text
resolve Exam
→ inspect Scope Confirmed

confirmed
→ hard boundary

unconfirmed
→ provisional context only
```

## ACTIVITY

```text
official instructions
→ hard constraints
→ related learning sources
→ current result
→ exact GitHub Submission Ref when applicable
```

## USER_NOTE

```text
USER note/annotation context
→ supporting course source
```

## VERIFY

```text
candidate claim
→ normalized source evidence
→ original PDF/audio/annotated page when necessary
```

---

# 19. MCP as the Model-Neutral Boundary

v1.2 uses MCP as the primary AI-client integration boundary.

The MCP server exposes **domain tools**, not generic storage tools.

Do not expose:

```text
notion.search
notion.read
drive.search
drive.read
```

as the normal client interface.

Expose:

```text
uls.resolve_entity
uls.get_material_context
uls.get_session_context
uls.search_concept
uls.get_exam_context
uls.get_activity_context
uls.get_user_context
uls.verify_claim
uls.get_source_chunk
```

This keeps storage implementation and academic policy behind ULS.

---

# 20. MCP Tool Semantics

## `uls.resolve_entity`

Input:

```text
course hint
natural-language entity handle
optional entity type
```

Output:

```text
resolved entity
or
explicit ambiguity candidates with stable resolution_id/candidate_id
```

Follow-up selection uses the returned stable identifiers, not display text matching.

## `uls.get_material_context`

Returns:

- Material metadata,
- selected normalized chunks,
- source version/hash,
- locators,
- relevant user annotations when requested,
- provenance.

## `uls.get_session_context`

Returns:

- Session metadata,
- relevant transcript chunks,
- verified Material Usage chunks,
- permitted provisional relations,
- user annotation references,
- professor signals,
- provenance.

## `uls.search_concept`

Returns bounded course-source candidates for the concept.

## `uls.get_exam_context`

Code-enforces:

- `Scope Confirmed`,
- Included Sessions,
- verified Material Usage,
- freshness,
- source authority.

## `uls.get_activity_context`

Code-enforces:

- official instruction constraints,
- related sources,
- exact submission ref when applicable.

## `uls.verify_claim`

Uses AI enrichment only as a locator hint.

Evidence is returned from normalized/original sources.

## `uls.get_source_chunk`

`uls.get_source_chunk` is a detail-fetch/pagination tool for a source set that has **already passed retrieval policy**.

It requires a context capability created by a prior policy-checked call such as:

```text
get_material_context
get_session_context
get_exam_context
get_activity_context
verify_claim
```

Example:

```text
get_session_context(...)
→ context_id = ctx_abc123
→ allowed_locators = {
    COMP319-M03:p13-p27,
    COMP319-S05:t00:10:00-00:40:00
  }

get_source_chunk(
  context_id = ctx_abc123,
  locator = COMP319-M03:p18
)
→ allowed
```

An arbitrary locator outside the issued allowlist is rejected.

```text
get_source_chunk(
  context_id = ctx_abc123,
  locator = COMP319-M99:p1
)
→ POLICY_DENIED
```

Context capabilities:

- are scoped to the caller/session where practical,
- contain or reference an allowlist of permitted locators,
- expire after a bounded lifetime,
- do not grant generic Drive/Notion read access.

This prevents `get_source_chunk` from becoming a policy bypass.

---

# 21. Context Package Contract

Example Exam context:

```json
{
  "entity": {
    "type": "exam",
    "id": "COMP319-E01",
    "title": "중간고사"
  },
  "scope": {
    "status": "confirmed",
    "hard_boundary": true
  },
  "sources": [
    {
      "source_class": "professor_material",
      "entity_id": "COMP319-M03",
      "locator": "COMP319-M03:p13-p27",
      "source_version": 2,
      "source_hash": "sha256:...",
      "authority": "professor_source",
      "content": "..."
    }
  ],
  "professor_signals": [
    {
      "kind": "explicit_exam_signal",
      "locator": "COMP319-S05:t00:31:20",
      "content": "..."
    }
  ],
  "user_context": [
    {
      "kind": "weak_area",
      "topic": "Master theorem"
    }
  ],
  "warnings": []
}
```

The AI client does not independently traverse Notion/Drive to reconstruct these boundaries.

---

# 22. Read-Only MCP Capability Model

The v1.2 MCP server is read-only.

Allowed capabilities:

```text
resolve
search
retrieve
verify
```

Not exposed:

```text
delete source
overwrite source
modify Notion
rewrite USER notes
set Verified
set Scope Confirmed
commit/push Git
```

Background ingestion/enrichment writes and interactive AI retrieval are separate trust domains.

---

# 23. Credential Separation

Least-privilege credential separation is the default v1.2 security posture.

## Worker credential

May require:

```text
Drive read/write
Notion read/write
GitHub read where needed
```

Used only by background/local processing.

## MCP retrieval credential

Where the provider supports it, the MCP retrieval path **must** use a separate read-only credential or read-only OAuth scope:

```text
Drive read-only
Notion read-only
GitHub read-only
```

It must not inherit worker write privileges merely for implementation convenience.

This reduces the blast radius of:

- prompt injection,
- client mistakes,
- MCP misuse,
- remote exposure.

Where provider limitations prevent true credential-level read-only separation:

1. document the limitation,
2. keep the MCP application surface read-only,
3. deny write operations at adapter and domain boundaries,
4. include a security test proving the remote/tool surface cannot invoke write paths.

Credential secrets are never committed to source control or embedded in client projection files.

---

# 24. Behavior Contract

v1.2 introduces a model-neutral Behavior Contract as a first-class artifact.

Canonical artifact:

```text
contracts/study-behavior.md
```

It defines:

- when to call each ULS tool,
- how to distinguish SOURCE / USER / AI,
- how to label provisional scope,
- how to handle conflicts,
- when external knowledge is allowed,
- how VERIFY mode works,
- how evidence locators should influence the answer,
- how to behave when ULS returns ambiguity or missing evidence.

This contract governs behavior, not storage access.

---

# 25. Skills / Client Instructions

Client-specific skills/instructions are projections of the Behavior Contract.

Conceptual layout:

```text
contracts/
└─ study-behavior.md

clients/
├─ claude/
│  ├─ skills/
│  │  ├─ study-session/
│  │  ├─ explain-concept/
│  │  ├─ exam-prep/
│  │  ├─ activity-help/
│  │  └─ verify-source/
│  └─ package/
│
└─ chatgpt/
   ├─ instructions/
   │  └─ study-behavior.md
   └─ app/
```

Canonical rules are not maintained independently in both clients.

Client projections may differ in syntax/package format but must preserve the same semantic contract.

## 25.1 Projection drift control

The canonical Behavior Contract carries a version and content hash.

Example projection metadata:

```text
behavior_contract_version: 1
behavior_contract_hash: sha256:...
```

Claude and ChatGPT projections must declare the version/hash they were generated or reviewed against.

CI/lint must fail when:

```text
projection hash/version
!=
canonical contract hash/version
```

unless the projection is explicitly regenerated/reviewed.

This prevents long-lived client-specific Skills/Instructions from silently drifting away from the canonical behavior rules.

---

# 26. Skill vs MCP Responsibility

Hard design rule:

> **Skill / Instructions = how the model should behave.**  
> **Retrieval Engine / MCP = what data the model is allowed to receive.**

Examples:

Skill responsibility:

- teach at the requested level,
- distinguish professor statement from inference,
- show uncertainty,
- choose useful answer structure,
- ask the user when entity resolution is ambiguous.

MCP responsibility:

- exclude out-of-scope exam content,
- exclude unverified relations when policy requires,
- reject stale factual enrichment,
- select exact submission ref,
- prevent write capabilities,
- return bounded source context.

Safety boundaries must not depend only on Skills.

---

# 27. Client Abstraction

ULS does not assume one permanent AI client.

Supported client categories:

```text
Claude-compatible MCP client
ChatGPT-compatible MCP/App client
future MCP-capable clients
```

The following belong to deployment/client integration, not the academic core:

- whether the client uses local or remote MCP,
- plan/workspace requirements,
- plugin/app packaging,
- UI differences,
- exact skill/instruction syntax.

The ULS MCP contract remains stable across clients.

---

# 28. MCP Transport Model

The Retrieval Engine and tool implementation are transport-independent.

Conceptually:

```text
              ULS Retrieval Engine
                       │
                  MCP Tool Layer
                       │
          ┌────────────┴────────────┐
          │                         │
     Local transport          Remote transport
          │                         │
   local MCP client          web/remote client
```

Possible uses:

```text
Local MCP
→ local development/testing
→ Claude/local MCP-capable clients

Remote authenticated MCP
→ ChatGPT-compatible App where supported
→ Claude web/remote use where supported
→ future clients
```

Client support is a **deployment capability**, not a core architectural invariant.

The ULS Core and MCP contract remain model-agnostic even if a specific client, plan, workspace, or surface cannot consume the MCP endpoint.

## 28.1 Remote transport baseline

Remote MCP is a thin authenticated retrieval facade.

Minimum requirements:

- HTTPS/TLS transport,
- authenticated access,
- OAuth/OIDC when required or well-supported by the target client,
- for development-only profiles, a short-lived bearer credential over TLS may be used,
- secrets are never committed to Git or embedded in generated Skills/Instructions,
- unauthenticated public MCP endpoints are forbidden.

Authentication details may vary by client, but the remote facade must preserve the same read-only tool contract.

It does not move ingestion/normalization/state ownership to the cloud.

---

# 29. Local-Primary Execution

The academic processing system remains:

> **Local-primary / Single-active-worker / Cross-platform**

Primary PC:

- discovers/archives sources,
- normalizes,
- enriches,
- updates Notion,
- maintains SQLite execution state.

The Retrieval Engine may run with the primary application.

A remote MCP facade may be added where a client requires remote access.

## 29.1 v1.2 availability contract

If the Retrieval Engine and remote bridge run on the local Primary PC:

- remote retrieval is available only while the Primary PC is powered on, awake, network-connected, and the authenticated MCP bridge is reachable,
- a sleeping/offline Primary PC makes remote retrieval unavailable,
- mobile/web clients cannot be promised always-on retrieval in this deployment profile,
- this is an accepted v1.2 limitation, not a data-integrity failure.

If always-on remote study later becomes a requirement, an always-on retrieval host may be introduced without moving canonical ingestion/state ownership to the cloud.

Remote retrieval does not imply:

- cloud ingestion,
- cloud canonical state,
- multi-worker orchestration.

---

# 30. Retrieval Manifest

Retrieval Manifest becomes optional in v1.2.

Role:

```text
deterministic compiled cache
```

Not:

```text
required AI transport
source of truth
policy authority
```

Canonical graph remains Notion.

Canonical content remains Drive/GitHub.

Retrieval execution remains Python.

A manifest may cache:

- resolved relations,
- current source refs,
- fingerprints,
- common context plans.

The system must remain correct if all manifests are deleted and rebuilt.

---

# 31. Privacy and Sharing

Hard privacy rule:

> **ULS must not enable public or “anyone with the link” sharing merely to make AI retrieval work.**

Requirements:

- Drive sources/derivatives remain account/private where possible,
- MCP authenticates retrieval access,
- remote bridge access is authenticated,
- retrieval logs avoid unnecessary full source bodies,
- tokens/credentials are never logged,
- public sharing is not an accepted workaround for client integration failure.

---

# 32. Job and Processing Status

Common outcome model:

```text
PENDING
PROCESSING
READY
PARTIAL
NEEDS_REVIEW
FAILED
```

The same semantic outcome is representable in:

- StateStore job result,
- Notion `Text Status`,
- normalized derivative front matter.

Example:

```text
job = PARTIAL
Material.Text Status = Partial
derivative.status = partial
```

This closes the previous `Partial` representation gap.

---

# 33. Human-Gate Write Protection

The existing human authority rules remain.

```text
Material Usage.Verified = true
Exam.Scope Confirmed = true
```

are human-only.

Defense occurs at the write boundary, not only at the LLM caller.

Automation write APIs must reject attempts to set these fields true outside explicit human-approval application paths.

The MCP retrieval server has no write API at all.

---

# 34. Retrieval Manifest / Enrichment Fingerprints

Any cached retrieval structure or persistent AI enrichment that depends on source content includes:

```text
source_version
source_hash
processor_version
```

This enables deterministic stale detection.

The AI client is never responsible for deciding freshness by itself.

---

# 35. GitHub Contract

GitHub remains canonical only for project-like code requiring version control.

Rule:

> **Branch is workspace; exact commit/tag is evidence.**

For submitted work:

```text
Submission Ref
```

is required where available.

The Retrieval Engine reads:

```text
repository
repository path
exact ref
```

and returns bounded code context.

It never silently substitutes current `main` for a submitted ref.

---

# 36. v1.2 Technical Validation Order

v1.2 burns down the least-controlled external assumptions first.

## Spike C0 — ChatGPT Remote MCP Feasibility

Purpose:

> Validate the most uncertain client/deployment assumption before building the academic retrieval stack.

Minimum server:

```text
authenticated read-only remote MCP
└─ uls.ping()
   → {"ok": true}
```

No Drive, Notion, normalization, or academic policy logic is required.

Acceptance:

```text
[ ] target ChatGPT environment can register/connect to the MCP/App path
[ ] tool discovery succeeds
[ ] one trivial tool invocation succeeds
[ ] required remote transport/tunnel model is recorded
[ ] authentication mechanism is recorded
[ ] plan/workspace/surface/mobile limitations are recorded
```

Outcome policy:

```text
PASS
→ ChatGPT may be treated as a supported v1.2 client profile.

FAIL because current product capability is unavailable
→ ULS architecture remains valid.
→ ChatGPT first-class support becomes deployment-deferred.
→ Claude/other MCP clients may continue.
```

Failure of C0 must not be “fixed” by making Drive files public or weakening authentication.

## Spike M0 — MCP Contract Reality Check

Minimum fixture:

```text
one hard-coded Material
→ Retrieval Engine
→ get_material_context()
→ local MCP
→ one low-friction MCP-capable AI client
```

Goal:

> Prove that an AI client can call a ULS domain tool and receive grounded academic context.

No full ingestion stack is required for this spike.

## VS0 — Real Material Vertical Slice

```text
one professor PDF
→ Drive source archive
→ normalization
→ Drive normalized derivative
→ Notion Material metadata
→ Retrieval Engine
→ MCP
→ supported AI client
→ grounded answer without manual attachment
```

## VS0-B — Cross-Client Contract Compatibility

Where two supported clients are available, use the same MCP tool contract with the second client.

Acceptance:

- same ULS tool semantics,
- same evidence/context boundary,
- same provenance structure,
- no client-specific logic leaked into the Retrieval Engine.

The answer wording does not need to be identical.

A client unavailable because of current product/plan capability is documented as deployment-deferred rather than treated as a core-engine failure.

## Spike G — Goodnotes

Run immediately after the core retrieval vertical slice.

Choose:

```text
A. granular diff
B. text + page visual hybrid
C. page visual primary
```

v1.2 accepts B or C if Level-2 visual fallback is reliable.

---

# 37. Model-Independent Contract Tests

The most important retrieval policies must be testable without Claude or ChatGPT.

Example:

```text
Given:
Exam E01
Scope Confirmed = true
Included Sessions = S01, S02
M03 relation Verified = false

When:
get_exam_context(E01)

Expect:
S01/S02 allowed
out-of-scope Session excluded
M03 excluded from confirmed evidence
scope.status = confirmed
```

Freshness example:

```text
source version = 3
enrichment based_on version = 2

Expect:
enrichment marked stale
excluded from factual evidence
```

This separates system correctness from model behavior.

---

# 38. Client E2E Acceptance

Client E2E validates integration, not academic policy logic.

For each supported client:

```text
[ ] client connects to ULS MCP
[ ] entity can be resolved naturally
[ ] domain tool is selected appropriately
[ ] response uses returned course context
[ ] provenance can be surfaced when requested
[ ] ambiguous entity resolution results in clarification
[ ] client does not need direct arbitrary Drive/Notion traversal
```

These checks are external/manual acceptance where CI cannot reproduce the client runtime.

---

# 39. Release-Blocking Safety Invariants

1. Original sources are never automatically deleted.
2. Original source versions are never destructively overwritten.
3. USER-managed content is not overwritten by automation.
4. `Partial` never silently becomes `Ready`.
5. AI/automation cannot set `Material Usage.Verified = true`.
6. AI/automation cannot set `Exam.Scope Confirmed = true`.
7. MCP retrieval is read-only.
8. For a confirmed Exam, Retrieval Engine code restricts the **course evidence/context supplied to the model** to the confirmed scope.
9. For an Activity, Retrieval Engine code supplies official instructions as the highest-authority constraint set and excludes policy-disallowed context.
10. Model obedience in generated prose remains instruction-enforced; models must not present pretrained/out-of-boundary knowledge as if ULS supplied it.
11. Stale enrichment is excluded from current factual evidence before context is supplied.
12. Submitted GitHub analysis uses exact `Submission Ref` where available.
13. `uls.get_source_chunk` accepts only locators authorized by an issued context capability/allowlist.
14. Identical processing is idempotent.
15. `Ready` is committed last.
16. Professor source, USER source, AI inference, and external knowledge remain distinguishable.
17. Missing evidence is not represented as course evidence.
18. Retrieval cache/manifest never becomes source authority.
19. Public file sharing is not enabled merely for AI retrieval.
20. Retrieval policy correctness can be tested without an AI client.
21. Where provider support exists, remote MCP uses separate least-privilege read-only credentials.
22. Behavior Contract projections are version/hash checked to prevent silent client drift.

---

# 40. Explicit v1.2 Non-Goals

The following are not required for v1.2:

```text
distributed multi-worker execution
PostgreSQL StateStore implementation
cloud ingestion worker
custom vector database / pgvector RAG
semantic Concepts ontology
fully automatic Goodnotes geometry extraction
AI write access through MCP
automatic USER-note rewriting
automatic Exam scope confirmation
automatic Material Usage verification
full cross-PC takeover/reconciliation workflow
custom web dashboard
multiple simultaneous primary workers
```

A remote authenticated MCP facade may be implemented where needed for client access, but it is not a cloud migration of the academic processing system.

---

# 41. Final System Layers

```text
L0 — CAPTURE
Alt
Goodnotes
Professor downloads
User-created files

        ↓

L1 — FILE CORPUS
Google Drive
├─ source/
└─ derived/

GitHub
└─ exact-ref project source

        ↓

L2 — ACADEMIC GRAPH / STATE
Notion
├─ Courses
├─ Sessions
├─ Materials
├─ Material Usage
├─ Activities
├─ Exams
├─ verification
├─ aliases
├─ learning state
└─ compact enrichment/index

        ↓

L3 — PROCESSING
Python Core
├─ ingestion
├─ normalization
├─ enrichment
└─ SQLite orchestration state

        ↓

L4 — RETRIEVAL CONTROL PLANE
ULS Retrieval Engine
├─ resolver
├─ scope
├─ authority
├─ freshness
├─ chunking
└─ provenance

        ↓

L5 — MODEL-NEUTRAL INTERFACE
ULS MCP Server
└─ read-only domain tools

        ↓

L6 — AI CLIENTS
├─ Claude
│  └─ Skill/package projection
├─ ChatGPT
│  └─ Instructions/App projection
└─ future MCP-capable clients
```

---

# 42. Frozen Principles for v1.2

> **ULS, not the AI client, owns retrieval policy.**

> **MCP is the model-neutral retrieval boundary.**

> **ULS Core and the MCP contract are model-agnostic; actual client availability is deployment-dependent.**

> **AI clients are replaceable reasoning interfaces where their runtime supports the required MCP contract; they are never data-policy authorities.**

> **Drive stores original sources and deterministic normalized derivatives.**

> **Notion stores academic graph/state/verification, not canonical large-text bodies.**

> **GitHub stores version-controlled project source at exact refs.**

> **Skill/instructions govern behavior; the Retrieval Engine governs data access.**

> **Safety-critical source boundaries are enforced in Python where technically possible.**

> **The v1.2 MCP surface is read-only.**

> **SOURCE / AI / USER remain distinct.**

> **Normalization is not summarization.**

> **Partial is not Ready.**

> **Human confirmation cannot be promoted by AI.**

> **Goodnotes Level-2 page-visual fallback is sufficient for v1.2.**

> **Local-primary remains the execution model; client transport does not redefine canonical data ownership.**

> **Model-independent contract tests prove retrieval correctness before client E2E tests.**

> **ChatGPT remote-MCP feasibility is tested before broad academic implementation, because client capability is an external deployment dependency.**

> **v1.2 CONCEPT retrieval is bounded index/lexical retrieval; embedding/vector semantic retrieval is deferred.**

> **Fine-grained source reads require a previously issued context capability and cannot bypass policy.**

> **Code controls the evidence/context supplied to the model; client instructions govern how the model represents and obeys those boundaries in prose.**

---

# 43. Design Status

This v1.2 architecture is **Frozen for implementation specification**.

The freeze includes the following final decisions:

1. ULS Retrieval Engine owns scope, authority, freshness, source selection, and provenance.
2. MCP is the model-neutral AI-client boundary.
3. MCP remains read-only in v1.2.
4. ULS Core is model-agnostic; actual Claude/ChatGPT availability is deployment-dependent.
5. Behavior Contract and retrieval/data policy remain separate responsibilities.
6. Behavior Contract client projections are version/hash checked for drift.
7. Retrieval Manifest is optional cache, not required transport or authority.
8. `Partial` is consistently represented across processing state, Notion state, and derivative metadata.
9. revision matching and natural-language aliases are part of the architecture.
10. ambiguous entity resolution uses stable `resolution_id` / `candidate_id` handles.
11. private/account-scoped source access is required; public sharing is not an integration workaround.
12. remote MCP requires authenticated HTTPS and least-privilege read-only credentials where provider capabilities permit.
13. local-primary remote retrieval is available only while the Primary/bridge is online; always-on mobile/web retrieval is not a v1.2 guarantee.
14. v1.2 CONCEPT retrieval uses aliases/Topics/Content Index/headings/lexical matching with optional bounded reranking; vector semantic retrieval is deferred.
15. `get_source_chunk` is capability-bound to locators authorized by a prior policy-checked context call.
16. code-enforced guarantees apply to the evidence/context supplied to the model, not total control of model-generated prose.
17. validation order is `Spike C0 → Spike M0 → VS0 → cross-client compatibility where available → Spike G`.
18. client feasibility failures caused by current product/plan capability do not invalidate the ULS core architecture; they are recorded as deployment limitations.

Changes to these frozen architectural principles require a new design revision.

The next artifact is the v1.2 implementation specification derived from this design.

---

**End of University Learning System v1.2 — Design Specification**
