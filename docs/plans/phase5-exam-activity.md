# Phase 5 — Exam and Activity

**Status:** UNAPPROVED plan. No source, frozen specification, `AGENTS.md`, or `CLAUDE.md` changes are authorized by this document; implementation starts only after independent plan gates and Astra acceptance.

**Owner:** the continuous Phase 5 worker owns this plan, implementation, self-tests, and related fixes; no separate verifier is required.

**Design routing:** verified root task record assigns `luna-implementation`, `gpt-5.6-luna / max`. Technical fit: approval identity, graph freshness, capability revocation, and provenance need one coherent contract across adapters and retrieval.

## Authority and current baseline

The authorities are `university-learning-system-v1.2-design-frozen.md` §§5, 17–18, 20.7–20.8, 21–22, 39 and `university-learning-system-v1.2-implementation-spec-frozen.md` §§14.5–14.7, 15.1–15.2.4, 16–17, 20–21, 24.7–24.8, 24.11, 25–26, 46, 50.1–50.2, 51–54, plus `AGENTS.md`; `CLAUDE.md` remains unchanged and preserved.

Branch `codex/phase5-8-completion` is at `9ba41a5`; Phase 4 is integrated and accepted in `.review/phase4-rev10-final-acceptance.json`. Supplied baseline: `.venv/bin/python -m pytest -q tests/` → 866 passed in 12.46s; not rerun for this design-only turn. The actual Phase 4 surface is `domain/approval_identity.py`, `adapters/notion/base.py`, `retrieval/capabilities.py`, `retrieval/engine.py`, `adapters/drive/binding.py`, and `retrieval/context.py`. The rev1/rev2 drafts were inspected for findings only; their obsolete assumptions do not override the current snapshot.

## §46 acceptance mapping

| Frozen acceptance | Concrete delivery and evidence |
| --- | --- |
| Engine restricts supplied Exam evidence to confirmed scope | `RetrievalEngine.get_exam_context` reads one typed Exam and exact Course, collects only its current `Included Sessions`, uses verified current leaf evidence, and issues capabilities only for those final items. Contract tests cover S01/S02 allowed and S03 excluded, including `get_source_chunk`. |
| Unconfirmed scope is labeled provisional in the context schema | `scope.status="provisional"`, `scope.hard_boundary=false`, explicit `EXAM_SCOPE_UNCONFIRMED` warning, and a preserved parent snapshot. Tests cover false scope, empty/absent relations, and follow-up revocation. |
| Exam scope proposal enters Automation Queue | `build_exam_scope_proposal` creates a strict `EXAM_SCOPE` Queue proposal with canonical identity, old/desired snapshots, Course, dependencies, evidence, reason, and processor version. Producer tests prove enqueue-only behavior. |
| Only an approved/current scope proposal can set `Scope Confirmed=true` | `HumanApprovalApplier` revalidates the exact Queue row, human decision, target, Course, snapshots, relations, and dependencies, then makes one target patch containing `Included Sessions` and `Scope Confirmed`. Guard/applier/unknown-write tests cover denial, stale state, duplicate rows, and replay. |
| Official Activity instructions have highest supplied constraint authority | `get_activity_context` loads a typed `uls.activity.v1` derivative through a trusted source/normalized-pointer association, returns complete official instruction evidence first, and labels incomplete coverage. Related Sessions/Materials never become official instructions. |
| Client Behavior Contract tests prevent external/pretrained knowledge from being labeled supplied course evidence | Deterministic fixtures independently inspect the semantic body of every affected projection for SOURCE/USER/AI/External separation, provisional wording, missing evidence, and incomplete instruction disclosure; wrong body plus correct metadata is rejected before stamping. These tests do not claim model obedience or live-client acceptance. |

## Required contracts

### Typed graph records and course identity

Add `src/uls/domain/academic.py` with typed `ExamRecord`, `ActivityRecord`, `ActivityInstructionRefs`, and `ActivityResultMetadata`. Add these exact reader interfaces to `src/uls/adapters/notion/base.py`:

```text
NotionReader.get_exam(exam_id: str) -> ExamRecord | None
NotionReader.get_activity(activity_id: str) -> ActivityRecord | None
```

`ExamRecord.course` and `ActivityRecord.course` use existing `CourseIdentity`; every record and related Session/Material resolves exactly one Course matching the parent page ID and parsed Course Key. Malformed IDs, multiple Course relations, and malformed optional relations are errors, never silently filtered. Absent optional relation = `None`; present empty relation = empty tuple, so an empty Exam remains honest. `Scope Confirmed` is an actual `bool`, never truthiness coercion.

`ActivityRecord` carries both instruction pointers independently, non-empty `result_type`, optional related IDs, and typed result fields including exact `submission_ref`. Coerce raw Notion mappings once at the adapter boundary; authority-bearing code does not use unrestricted `Any` or `getattr` probing.

### Exam scope identity, Queue lifecycle, and human gate

Add `src/uls/proposal/exam_scope.py` with `build_exam_scope_proposal(...) -> Proposal`. Extend `src/uls/domain/approval_identity.py` so `EXAM_SCOPE` uses the Phase 4 canonical JSON/namespaced digest mechanism while preserving every Phase 4 semantic.

The canonical action is `schema="uls.exam_scope.approval.v1"` with optional values explicitly `null`: `operation`, `target_db="Exams"`, `target_entity_id`, exact `course` (`relation_page_id`, `course_key`), `old_snapshot` (`included_session_ids`, actual `scope_confirmed`), `desired_snapshot` (`included_session_ids`, `scope_confirmed=true`), `session_dependencies`, `source_dependencies`, `evidence`, `review_reason`, and `processor_version`. Each dependency contains validated Session ID/Course and the trusted source fingerprint/reference if consulted; any consulted non-Session approval source is either explicitly bound in `source_dependencies` and revalidated or excluded from the action. An unconsulted optional source dependency is explicit `null`, never invented.

Validate IDs before sorting and reject duplicates. An empty desired scope is valid only for explicit `operation="confirm_empty_scope"`; the action must carry `desired_snapshot.included_session_ids=[]`, and the exact empty value participates in the Proposal ID. The Proposal ID includes the complete canonical action, so changes to IDs, membership, old/desired snapshots, Course, dependencies, evidence, reason, or processor version create a new ID. No set conversion may hide malformed input.

Treat `EXAM_SCOPE` as a strict Queue type in `base.py`: one physical row per Proposal ID, canonical Proposed Action and Queue mirrors agree, retries update metadata only, and the frozen Queue schema is unchanged. Target DB/snapshot stay in Proposed Action; do not emit new `Target DB`/fingerprint Queue properties. The producer is enqueue-only.

Extend `enforce_write_policy` and `guarded.py` so ordinary automation cannot write `Exams.Scope Confirmed` or `Exams.Included Sessions`, including an empty relation; it may create/update only allowed pending proposal fields and system terminal errors. The applier boundary accepts a trusted attributable human identity, not stored Queue `Decision By/At`; those fields are audit outputs written after a successful effect per the frozen lifecycle. It requires one current Queue row, `APPROVED`, `Approve`, non-terminal target, exact ID/type/action, current Course, old snapshot, desired relation, and dependencies. Only the exact human-approved `confirm_empty_scope` action may patch `Included Sessions=[]` with `Scope Confirmed=true`; the same single target update handles non-empty scope. It records audit fields after effect observation and makes `APPLIED` replay idempotent. Rejection/staleness produces no target write and defined `SUPERSEDED`/`FAILED`.

Carry Phase 4's durable marker protocol into this path: persist `prepared` for the exact Proposal ID/action and dependency snapshot before the target write, then immediately reread the unique Queue row before mutation and revalidate approval, type, ID, canonical action, and marker. Any approval revocation or duplicate insertion at that boundary causes zero target mutation. After an ambiguous write, reread target, parent, and every dependency, then persist `effect_observed` only after the full post-marker target/dependency checks pass. Only a durable `effect_observed` marker followed by the audit update may produce `APPLIED`; merely observing the desired target later, or having only `prepared`, does not prove this applier caused it and remains reconciliation. Recovery is audit-only after an observed effect, never an unproven target rewrite. No extra Queue fields.

### Exam retrieval and provisional behavior

Add this direct engine interface in `src/uls/retrieval/engine.py`:

```text
get_exam_context(exam_id: str, *, query: str | None = None,
                 caller_scope: str | None = None) -> ContextPackage
```

Read the current Exam and Course on every call. `confirmed` means
`scope.hard_boundary=true`; only current, exact `Included Sessions` can produce
supplied evidence. A malformed authority-bearing Exam relation fails closed.
An absent or empty relation returns no evidence plus `EXAM_SCOPE_EMPTY` and
preserves the distinction in metadata. A confirmed empty scope remains
`confirmed` with an empty source set.

For every leaf, reuse the current Phase 4 freshness, trusted binding, Course,
Material Usage/Role/Type, page-index, and `Partial`/`Ready` gates. Do not include
unverified Material Usage or cross-Course evidence. Aggregate all leaf
evidence, apply the item/per-item/total budget once, then issue capability
bindings only for that final list. `assemble_context_package` must not apply a
second hidden truncation.

### Activity instructions and related evidence

Add `src/uls/normalization/activity.py` types and deterministic normalization
for `uls.activity.v1`; extend `normalization/validators.py`,
`retrieval/chunking.py`, and the derivative preparation boundary without
changing transcript behavior. Preserve LF normalization, front matter,
fingerprint, provenance, status, and justified page/timestamp markers.

Extend `src/uls/adapters/drive/binding.py` with a typed
`ActivityInstructionBinding` and resolver operation:

```text
resolve_activity_instructions(entity_id: str, instructions_source_url: str,
                              normalized_instructions_url: str)
    -> ActivityInstructionBinding
```

The trusted association must match the Activity ID and both graph pointers to a
registered derivative/originating `SourceRef`; compare `SourceRef.identity`
(`provider`, `file_id`), never `web_url`, which is navigational metadata. A
source hash alone is insufficient: a pointer rewire with the same hash
invalidates the binding.
Missing Source, missing Normalized Instructions, missing trusted association,
or ambiguity is unavailable/partial and never a full official-instruction
claim.

Add this engine interface:

```text
get_activity_context(activity_id: str, *, query: str | None = None,
                     caller_scope: str | None = None) -> ContextPackage
```

Use `source_class="official_activity"` and `schema="uls.activity.v1"`. Add
typed `ActivityConstraintMetadata` separate from global `SourceAuthority`, with
the exact official locator set, official evidence set, and `priority="hard"`.
Place official chunks first in this Activity package, but do not reorder the
global authority enum. Conflict behavior is direct and serialized: an official
instruction forbidding X governs a professor-material recommendation of X;
both evidence items and provenance remain visible. Policy uses the configured
Material Type mapping. Result metadata is typed, including exact
`submission_ref`; Phase 6 owns GitHub retrieval. Related evidence is never
relabeled official.

Expose instruction coverage in the context package, including expected, returned, missing locators and a truncation/complete flag. Every official fragment retains `source_class="official_activity"` and the highest supplied constraint authority even when coverage is incomplete; completeness is separate metadata. Full coverage requires a current ready derivative, complete justified coverage, no query selection loss, and no item/count/total truncation. Otherwise return selected official fragments with the same authority plus explicit `INSTRUCTIONS_INCOMPLETE` or `INSTRUCTIONS_PARTIAL` warnings. The Behavior Contract must disclose that coverage is incomplete and forbid complete-set claims. Never fabricate page 1 for unmarked text.

### Parent capability revocation

Extend `src/uls/retrieval/schemas.py` `CapabilityBinding` and the decorated
`AllowedLocator` metadata with typed parent snapshot fields:
`parent_entity_id/type`, parent Course page ID/Key, parent
`scope_confirmed`, exact parent Included/Related Session and Material sets
(with duplicate validation and no set-collapse),
`parent_path_leaf`, and Activity instruction Source/Normalized pointers plus
trusted binding identity. Preserve Phase 4 leaf `usage_id`, Role, Type,
PageRange, Course, full locator, source reference, and fingerprint fields.

Update `src/uls/retrieval/capabilities.py` and `engine.py` so issuance rejects
incomplete parent snapshots and follow-up authorization re-reads the parent,
Course, exact relation set, current path leaf, trusted Activity pointer pair,
and source fingerprint. Any subset/expansion, Course rewire, confirmation
change, pointer rewire, or provisional-to-confirmed change revokes the old
capability and requires a new parent context. A provisional binding may never
be elevated by a follow-up or config flag.

## Read-only wrapper boundary

Implement provider-neutral callable wrappers and typed request/response
serialization in `src/uls/mcp/schemas.py`, `src/uls/mcp/tools/exam.py`, and
`src/uls/mcp/tools/activity.py` for the exact names
`uls.get_exam_context` and `uls.get_activity_context`. They call the direct
engine interfaces, serialize through the canonical ContextPackage shape, and
perform no writes or approvals. `src/uls/mcp/server.py` transport registration,
startup, and deployment remain Phase 8 (§49); Phase 5 records this as an
explicit unvalidated delivery dependency. No live provider or client result is
claimed.

### Behavior Contract projection

First update the semantic bodies of `contracts/study-behavior.md` and all six affected projections so missing, partial, or truncated official instructions are disclosed and never described as the complete instruction set:
`clients/chatgpt/instructions/study-behavior.md`,
`clients/claude/skills/activity-help/SKILL.md`,
`clients/claude/skills/exam-prep/SKILL.md`,
`clients/claude/skills/explain-concept/SKILL.md`,
`clients/claude/skills/study-session/SKILL.md`, and
`clients/claude/skills/verify-source/SKILL.md`. The Phase 5 behavior test must check each projection body independently of its metadata and reject a wrong body paired with the correct version/hash. Only after those body assertions
pass, bump the canonical contract version from 1 to 2, align
`src/uls/config/schema.py` `BehaviorContractCfg.version` default with v2, stamp
all six projection hashes/versions using `scripts/project_behavior_contract.py`,
and run `scripts/lint_behavior_projection.py`; do not hand-edit generated
metadata.

## Required tests and evidence

Add or update these exact paths after plan acceptance:

- `tests/unit/test_activity_normalization.py` — typed front matter, LF/status/fingerprint gates, marker and unmarked-text behavior.
- `tests/contract/test_exam_scope_identity.py` — canonical equality, changed semantics, malformed/duplicate/empty inputs, strict Queue mirrors.
- `tests/contract/test_exam_scope_lifecycle.py` — enqueue-only producer, automation denial, empty human-approved clear, approval tamper, Course/dependency staleness, duplicate physical rows, committed-write/raise recovery, marker regressions, APPLIED replay, and idempotent clear.
- `tests/contract/test_get_exam_context.py` — §50.1 boundary, provisional/empty warnings, Course failures, stale and parent subset/expansion follow-ups.
- `tests/contract/test_get_activity_context.py` — official-first ordering, trusted pointer pair, related-source labels, typed hard-constraint metadata, direct/serialized forbid-vs-recommend conflict, result metadata, missing/partial coverage and budget warnings.
- `tests/contract/test_phase5_capability_revocation.py` — parent Course/scope/relation/path/pointer changes and preservation of Phase 4 leaf checks.
- `tests/contract/test_behavior_contract_exam_activity.py` — supplied versus external prose fixtures, SOURCE/USER/AI/External separation, missing evidence, provisional wording, no S03 leakage, and incomplete/truncated versus complete instruction disclosures; independently assert each projection body, reject wrong-body/correct-metadata fixtures, then stamp versions/hashes and run `scripts/lint_behavior_projection.py`.
- `tests/contract/test_mcp_exam_activity_serialization.py` — exact wrapper names, input/output schema, read-only behavior, Activity constraint/coverage metadata survival, and no claim of transport/live validation.
- `tests/integration/test_exam_activity_end_to_end.py` — fake Notion/Drive flow from Activity derivative/provenance through context and Exam proposal approval; no live provider dependency.

Preserve and rerun the full existing suite plus `pytest -m contract`, `pytest
-m unit`, `ruff check src tests`, and `mypy` at implementation review. Direct
engine/fake tests prove engine and wrapper contracts only; they do not prove
model obedience, authenticated client behavior, MCP transport, or deployment.

## Resolved decisions, risks, and dependencies

Root accepts the conservative choices required by the frozen design: an
unconfirmed Exam returns its current listed Sessions as verified-only
provisional evidence with `scope.hard_boundary=false`, warning
`EXAM_SCOPE_UNCONFIRMED`, and no scope mutation; Activity instructions require
both graph pointers and a trusted association for a full official claim. A
normalized-only pointer is unavailable/partial. No further product decision is
required for this plan.

Risk is high because this phase combines human authorization, authority-bearing
relations, source provenance, capability revocation, and user-facing warnings.
Phase 8 transport/deployment and the root's optional deployment-target question
remain explicit dependencies; they do not block direct Phase 5 design, and no
live validation may be reported before those gates.

## Independent review manifest

Plan review is required before implementation: independent web ChatGPT through
`insane-review` (Pro preferred, verified quota-only Very high fallback if
needed) and independent `google-antigravity/gemini-3.8-flash` high review for
the user-facing warning and Behavior Contract flows. Final review repeats the
same gates after implementation. Reviewers receive these exact current
authority/dependency paths:

- `docs/plans/phase5-exam-activity.md`
- `AGENTS.md`
- `university-learning-system-v1.2-design-frozen.md`
- `university-learning-system-v1.2-implementation-spec-frozen.md`
- `.review/phase5-root-integration-checklist.md`
- `src/uls/domain/models.py`
- `src/uls/domain/course_identity.py`
- `src/uls/domain/approval_identity.py`
- `src/uls/ephemeral/models.py`
- `src/uls/ephemeral/memory.py`
- `src/uls/adapters/notion/base.py`
- `src/uls/adapters/notion/guarded.py`
- `src/uls/adapters/drive/base.py`
- `src/uls/adapters/drive/binding.py`
- `src/uls/config/schema.py`
- `src/uls/retrieval/engine.py`
- `src/uls/retrieval/authority.py`
- `src/uls/retrieval/capabilities.py`
- `src/uls/retrieval/schemas.py`
- `src/uls/retrieval/context.py`
- `src/uls/retrieval/chunking.py`
- `src/uls/normalization/schemas.py`
- `src/uls/normalization/pdf.py`
- `src/uls/normalization/activity.py`
- `src/uls/normalization/validators.py`
- `src/uls/enrichment/_common.py`
- `src/uls/mcp/schemas.py`
- `src/uls/mcp/tools/exam.py`
- `src/uls/mcp/tools/activity.py`
- `src/uls/mcp/server.py`
- `contracts/study-behavior.md`
- `scripts/project_behavior_contract.py`
- `scripts/lint_behavior_projection.py`
- `clients/chatgpt/instructions/study-behavior.md`
- `clients/claude/skills/exam-prep/SKILL.md`
- `clients/claude/skills/activity-help/SKILL.md`
- `clients/claude/skills/explain-concept/SKILL.md`
- `clients/claude/skills/study-session/SKILL.md`
- `clients/claude/skills/verify-source/SKILL.md`
- `tests/fixtures/fake_notion.py`
- `tests/fixtures/fake_drive.py`
- `tests/fixtures/phase4.py`
- `tests/contract/test_phase4_rev6_recovery.py`

**Changed in this assignment:** only this new unapproved plan. **No source,
frozen document, instruction, or task-record file was edited.**
