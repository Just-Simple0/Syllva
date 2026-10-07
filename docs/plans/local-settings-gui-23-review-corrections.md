# GUI-2/GUI-3 review corrections — 2026-10-02

> 현재 적용 안내 (2026-10-04): 아래 배정·리뷰·단계·권한 문구는 작성 당시의 역사 기록입니다. 새 작업은 프로젝트 `AGENTS.md`가 참조하는 현행 전역 정책과 `handoff.md`의 최신 재개 기준을 따릅니다. 과거 모델/강도·quota 예외·commit/push 허용을 새 작업으로 승계하지 않습니다. 원래 검토된 bytes와 해시는 당시 Git snapshot/리뷰 패키지의 근거로 유지하며, 이 안내를 추가한 현재 파일을 그 원본과 동일하다고 주장하지 않습니다.

Status: corrective PLAN under independent review; no implementation acceptance.

This is a bounded continuation of GUI-2/GUI-3. Frozen design and implementation specification remain
authoritative. The current worker plans describe the intended behavior; this record maps independent
findings to corrections and acceptance checks. It does not authorize GUI-4/5, Canvas collection,
enabling sync, real-provider testing, new platforms, publishing, or a protected-branch push.

## Review evidence and disposition

Native PLAN R1: REVISE, actual `GPT-5.6 Sol` / `extra_high`, linked slider `[0,3,3]`,
`pro_option_unavailable` / `Slider maximum`. The original schema-2 manifest was harvested without
retransmission. The parent audited all ten complete source files and their package hash and ran
`state_check` successfully before corrective document edits. This proves evidence consistency, not
approval. Gemini plan and final reviewers actually ran `google-antigravity/gemini-3.8-flash` / ultra.
Gemini final R1 requires three UI corrections; the targeted corrective UI plan received a GO opinion.
Screens 01–12 are baseline fake-provider interaction evidence, not evidence of these future fixes.

Two native examples require qualification against actual code:

- CredentialService already holds the same config lock through `_continue` and rechecks separation
  before effects. That closes the described same-workspace promotion race. Separate workspace config
  locks and physical role admission still leave the durable provider purpose-pair gap.
- Canvas `_request_target` already rejects assignment/file/module paths. An offline read-only check
  reproduced `_next_link` accepting same-origin users/self, another course, and a list with missing
  active/term filters. The correction closes those remaining endpoint/query gaps.

## Native R1 — durable purpose-pair admission

The worker confirmed that config locks are per workspace whereas managed credential slots are per
user. The concrete bounded proposal below awaits the human scope decision about foreign-workspace
external/environment credentials as well as independent native plan review. It is not accepted yet.

Add a per-user `(provider, profile)` lock and secret-free pair reservation above both physical role
bindings. Hold both physical locks in a stable order inside the pair lock, then the existing role,
record and config locks. Scan both existing physical reservations, including legacy records that
predate pair admission. Pending, corrupt, incomplete, missing-journal or unreadable evidence blocks
the pair. Independent provider domains can proceed; the shared config lock still protects config CAS.

For multi-effect GUI mutations, before any stage/effect, publish and fsync the pair reservation, linked
to exactly the operation ID,
canonical binding, journal/config realpaths and config-directory identity, using the existing safe
owner/mode/no-follow and exclusive publish contract. Publish the physical reservation before journal
creation as before. Partial publication cannot be treated as an absent reservation. After journal
terminal state/readback, release reservations durably. No timeout or process-exit heuristic clears
uncertain evidence. Exact-operation recovery must validate pair plus physical identities and may
recover a legacy physical reservation only by acquiring/publishing the matching pair before effects.
An uncertain new pair publication cannot be treated as legacy or silently repaired.
New physical reservation records use a distinct schema/version marker and canonical pair key. Only
the previously validated legacy physical schema without that marker can enter the legacy recovery
bridge. Unknown schema, a new physical marker without its matching pair, or any leftover pair temp
evidence fails closed. Journal schema v3 config operations and v4 credential effects remain unchanged.
A crash after durable reservation publication but before journal creation deliberately leaves
unrecoverable-by-automation evidence, matching the already reviewed physical reservation contract.
It requires an exact-target human-owned manual disposition; this bundle adds no cleanup/repair route,
never infers permission from elapsed time, and does not reorder publication to allow an unreserved
effect. Treat this as an explicit retained fail-closed limitation, not a recoverable journal operation.

For enroll/replace and CLI writes, compare the incoming credential against both the fresh effective
peer of this config and the canonical managed peer, even if this config omits the peer declaration.
Notion comparison is constant-time in memory; Google client_email and private_key_id must differ as
already required. A configured missing/unreadable or invalid-identity peer is a fixed fail-closed
failure. Only positively absent undeclared environment and absent canonical slots count as absence.
Read and compare peer values only in backend memory; never persist secret values or their identities
in pair evidence. Recheck under the config lock before effects. Forget/detach does not need incoming
value comparison but uses the same pair admission and existing source/deletion safeguards.

CLI Notion writes acquire the same pair/both-physical locks and inspect all GUI durable markers, but
create no CLI pair or physical reservation, preserving the existing single atomic write contract.
Hold these locks from before prompting through write and exact readback. Read initial config identity,
generation and source briefly before prompting; do not hold the config lock while waiting for input.
Reload the selected source/config generation under its config lock before writing
so a prompt cannot authorize a write against a changed declaration. Preserve the existing single
atomic write contract: the CLI creates no multi-effect unresolved operation and no new config patch;
after a crashed single write, the next admission compares the actually observed canonical old/new
state. There is no journal-less CLI marker to auto-clear or misclassify as legacy. Unknown/unreadable
store state and a file readback differing from submitted bytes fail with fixed redacted codes.
Config change, purpose conflict and store/readback failure remain distinguishable fixed codes.
Google CLI remains path guidance, with no write.

The bounded proposal protects Syllva-managed shared slots and compares this workspace's effective
external/environment peer. It does not claim an index of all completed foreign workspace configs or
control arbitrary external file/environment mutations. Extending that boundary requires a separate
trusted config index design and explicit scope acceptance; none is implemented or assumed here.

Acceptance tests: simultaneous same-token/same-Google-identity enroll/replace across purpose roles
and workspace config files; crash before/after stage and promote; process-exit reservation survival;
legacy peer reservation; exact recovery ID/config identity; corrupt/partial publication failclosed;
configured-unreadable peer and undeclared canonical-peer conflicts; CLI prompt/write interleaving.
The second operation stays blocked until the first is terminal and then re-compares: a committed
duplicate fails CREDENTIAL_PURPOSE_CONFLICT, while a restored/abandoned operation is judged from its
actual restored state. Notion/Google domains remain independently admitted.

## Native R2 — provider response bounds before parsing

The worker verified installed google-auth 2.57.1 and google-api-python-client 2.200.0 source. Keep the
change confined to Settings `provider_checks.py`; do not change the general collector runtime SDK.
All Notion, Google OAuth and Drive responses have a fixed 1 MiB body cap before any JSON/SDK parsing.
Read at most cap+1 to detect excess, including chunked/no-length/error bodies, and explicitly fail
oversized success or error responses with the existing redacted PROVIDER_UNAVAILABLE. No raw body,
OAuth token, submitted key or exception text reaches API responses, journal, logs or files. Do not
accept an oversized 200 as an empty successful payload. Request identity encoding and reject
unsupported content encoding before parsing so decompression cannot evade the cap.

One check-scoped monotonic budget covers at most 8 actual wire calls and 30 s admission budget;
each call consumes budget before dispatch and receives min(10 s, remaining time) socket timeout.
Every OAuth request counts, as do resource requests. Check remaining time after each request and
before verification success. Never send a ninth request or a request after the deadline. Accepted
body volume is at most 8 MiB; reading for overflow detection is bounded at 8*(1 MiB+1). These are
socket/cooperative deadline bounds, not a promise to forcibly terminate OS DNS or arbitrary code.

Use the public google.auth.transport.Request interface with Credentials.refresh(request), creating
credentials from the submitted validated service-account payload with drive.readonly scope. The
custom Request only accepts the fixed OAuth token endpoint and expected POST. It returns bounded
success data; any error status, oversize or timeout raises a fixed SettingsServiceError immediately,
so installed SDK's response-driven retry loop never receives that error response. Allow one refresh
attempt and no automatic or explicit retries. Then use credentials.apply(headers) and fixed-host GET
`/drive/v3/files/{validated-id}` with the existing metadata fields; no media, discovery download,
redirect, AuthorizedHttp refresh/retry wrapper, or provider mutation. Authentication POST is only
token acquisition, not an API resource write. All calls share the same budget. Credentials and
transport context are check-local and not persisted.

Use fake HTTP/SDK seams to cover cap-1, cap and cap+1, large 200 and error bodies, missing/incorrect
length and chunked bodies, OAuth and Drive overflow, SDK error retry suppression, total wire-count
and deadline expiry, readonly scope/endpoint enforcement, closure on all outcomes, and credential
sentinel absence from API/log/journal. Reuse existing mapping/cooldown/staging-rollback checks.

## Native R3 — Canvas next-link endpoint and query scope

Every next link must satisfy the existing exact-origin public-DNS pinned-TLS GET policy and the
following constraints before the transport dispatches an Authorization-bearing request:

- normalized path exactly `/api/v1/courses`, with no encoded path alias;
- exactly one `enrollment_state=active`, `include[]=term`, and `per_page=50`;
- at most one optional positive ASCII-decimal `page`, maximum 10000;
- no duplicate, unknown, missing, or conflicting query parameters.

users/self and selected-course reads remain code-owned call sites. A provider next link cannot select
them. The client does not broaden the contract for another pagination representation. Existing fixed
redacted errors, 4-page/200-course/request/byte/time caps, no redirects and header-only PAT remain.
The official Canvas pagination documentation describes returned links as containing required query
parameters; links outside this bounded contract fail safely.

Tests must show that the original user/list requests occur but forbidden next requests do not. Include
same-origin assignments, files, modules, users/self, other course/API paths, encoded aliases, missing
filters, conflicting/duplicate keys and unknown parameters, plus a valid next page and existing bounds.

## Gemini UI 1–3 — restore the approved interaction contracts

1. Replace the native term-change confirm with a DOM dialog. Opening it preserves the current term and
   selected courses. Initial Cancel focus, the shared focus trap, Escape cancellation, previous-value
   restoration and focus return to the selector are required. Only explicit confirmation changes the
   term and clears the unsaved selection. No stale pending response may apply or reopen the warning.
2. Successful registry Apply re-reads the server snapshot and renders saved term, IDs, labels,
   checkboxes and count, also after Settings reopens. Keep unsaved page edits separate from saved
   state. Invalidate consumed review/discovery IDs; a new review requires a fresh discovery. Older
   responses cannot overwrite later state. The minimum-one-course rule and lease-independent Ready
   predicate stay unchanged. The review report's zero-course Apply wording is not accepted behavior.
3. Forget names the verified origin, backend's masked account and user ID, plus the actual storage.
   macOS says `this Mac's Keychain`; fake mode explicitly names its test store. Keep lease-removal and
   history-retention wording, Cancel default, profile confirmation and secret clearing.

Verify meaningful DOM harness regressions and actual fake in-app-browser interactions for confirmation,
cancel, Escape, focus return, saved-selection readback/reopen, stale-response handling and Forget text.
Screenshots prove only visible state; do not use them as keyboard, race or 20-course-limit proof.

## Ownership, gates and exclusions

The orchestrator is actual `gpt-6.1-sol` / high. The existing assigned worker remains actual
`gpt-6-luna` / max, fitting the continuous UI, durable admission and OAuth transport corrections.
The parent integrates plan/evidence and owns the small Canvas next-link correction. Jev has no current
usable official skill/tool integration (`not_configured`); no model identity changes are made.

After corrective native plan acceptance, implement the bounded corrections, run focused fake-only
checks and missing integration checks, then obtain source-complete native final reviews and Gemini
ultra final rereview of the changed UI. Record all finding dispositions and original-bound recovery
evidence before acceptance. Preserve unrelated work. No final acceptance, commit or push is currently
granted.

Official references used for the bounded transport and pagination design:

- https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html
- https://google-auth.readthedocs.io/en/latest/reference/google.auth.transport.html
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get
- https://canvas.instructure.com/doc/api/file.pagination.html
