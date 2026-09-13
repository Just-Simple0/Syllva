# Phase 6–8 implementation and verification

Date: 2026-09-10. Base: merged Phase 5 `f4c321e`. Work branch:
`codex/phase6-8-direct`. The user explicitly requested direct implementation
without orchestration. The current assistant implemented and checked this bundle
without delegated workers or independent web/Gemini review gates. Product
contracts and human-approval boundaries remain in force.

## Implemented acceptance

| Frozen section | Implementation | Evidence |
| --- | --- | --- |
| §47 GitHub | Fixed-origin, GET-only repository validation, exact commit/tag peeling, pinned tree/blob reads, checksum/size/encoding checks, no branch substitution | 24 GitHub contract cases and Activity graph revalidation |
| §47 result linkage | Activity Repository/Repository Path/Submission Ref and result metadata; bounded submitted code with commit/blob/hash/path/line provenance; official instructions remain authoritative | Activity code tests, invalid ref and source-change rejection |
| §48 packaging | Five Claude skills and ChatGPT instructions, canonical contract v2/hash, portable 11-file archive, support matrix, real-client checklist | Canonical lint, zip inspection and installed-wheel lint |
| §49 runtime | macOS LaunchAgent and Windows Task XML use the same bounded `uls run`; source discovery/process/retry/reprocess commands, shared local worker lock | CLI/worker tests, scheduler parsing, macOS `plutil` |
| §49 MCP | Eleven read-only tools, real MCP SDK 2.2 stdio and stateless HTTP transports, the same RetrievalEngine, separate read-only provider composition | Real SDK subprocess initialize/list/call/chunk and HTTP initialize/list/call/chunk |
| §49 remote security | Opt-in development bearer, direct TLS, maximum one-hour expiry, exact Host/Origin, duplicate/missing token rejection, caller-bound capabilities, no worker tool | Authentication, expiry, host, origin, plaintext and caller isolation tests |
| §49 operation | Safe status/doctor, authenticated bridge health, SQLite online backup and restore/offline guidance | Installed package smoke, SQLite integrity, deployment docs |

Code evidence uses structured GitHub path/commit/line metadata in
`scope.result_source`. It does not invent page/timestamp locators or extend the
frozen `get_source_chunk` grammar. Follow-up code reads repeat the Activity context
call with a narrower query and revalidate the Activity and submitted tag.

## Runtime integration and recovery

The native worker connects the existing transcript vertical slice: a metadata-only
`sources.json` registration → source download/hash → durable source-bound Session
allocation → normalized immutable Drive upload/readback → Notion SOURCE metadata
→ completed processing provenance → read-only search and follow-up chunks.
Original bytes and USER blocks are never overwritten. Explicit title/date/Status
are required for creating a Session. A user-replaced transcript pointer without
matching completed source provenance is preserved and the operation fails.

Source updates keep the canonical entity identity. Unchanged polling does not
republish. A source edit revokes old chunk evidence. Explicit reprocessing
preserves historical processing records and their output pointers; completed
records are no longer changed back to PROCESSING. The CLI selects supported
ingestion work when a later AI enrichment job exists. AI processing output does
not become the canonical source binding.

The MCP process opens state in SQLite `mode=ro`, reads independently registered
source provenance, and has no worker SDK write surface. Provider credential
values, academic bodies and raw provider errors are excluded from tool error
responses. Required MCP credentials never fall back to worker credentials.
Provider-side read-only grants still require operator verification.

## Automated verification

- Baseline: **995 tests passed** before these changes.
- Final suite: **1,051 passed** on Python **3.14.7** and **1,051 passed** on Python
  **3.11.16**, including the final code installed from an independent wheel.
- The suite includes 24 GitHub contract cases, 15 MCP runtime/security cases,
  five model-neutral additional-tool cases, seven worker/CLI cases and five
  native integration cases. No live provider credentials were used.
- Real Google media downloader and upload classes execute against synthetic
  HTTP/provider fixtures. Real Notion/Drive account writes were not performed.
- Canonical projection lint: six matching projections, version **2**, hash
  `sha256:987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a`.
- Client archive: **11 explicit files**, no credentials or private config.
- Wheel build and independent installation: packaged contract/config/client
  assets work outside the checkout. `uls behavior lint`, `init`, `status`,
  expected unconfigured `doctor`, and SQLite online backup/integrity passed.
  SDK versions exercised were MCP **2.2.0** and notion-client **3.1.0**. The Notion
  extra requires `>=3.1,<4` so an older pre-data-source SDK cannot satisfy it.
- `compileall`, `git diff --check`, `plutil -lint` and Windows XML parsing passed.
- Full Ruff: **183 findings**. Full mypy: **74 errors**. These are retained
  pre-existing debt; normalized file/code/message comparison against the Phase 5
  audit introduces no new findings. Neither global static check is called clean.
- One external Starlette/AnyIO deprecation warning remains; no test failure.
- Added GitHub Actions macOS/Windows × Python 3.11/3.14 matrix. This workflow has
  not been pushed or executed on GitHub; local tests are not CI evidence.

## Explicit limits

This accepts the Phase 6–8 repository implementation, not the complete v1.2 live
Definition of Done (§56). The earlier §41 C0/M0/VS0/VS0-B/Goodnotes live gates
remain deferred. Native scheduled ingestion currently accepts transcripts only;
unsupported source kinds are rejected and unhandled pending operations become
NEEDS_REVIEW. Existing provider-neutral PDF/enrichment/approval functionality is
not presented as a fully wired live desktop pipeline.

No scheduler was installed; Windows was not exercised on a Windows host. No
public endpoint or client connector was created. The built-in remote profile is
development bearer authentication, not OAuth/OIDC. Clients requiring OAuth remain
DEPLOYMENT_DEFERRED. Real Claude/ChatGPT domain E2E is unrecorded. The Primary PC
must be awake, online and running the bridge for remote access. Public sharing is
never a workaround. Production grants, TLS routing, client validation and the
deferred source workflows need task-specific evidence before a live release.

## Official implementation references

- GitHub [commits](https://docs.github.com/en/rest/commits/commits),
  [trees](https://docs.github.com/en/rest/git/trees), and
  [repository contents](https://docs.github.com/en/rest/repos/contents).
- MCP Python SDK [ASGI transport](https://py.sdk.modelcontextprotocol.io/run/asgi/),
  [authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/), and
  [low-level server](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).
- Notion [database retrieval](https://developers.notion.com/reference/retrieve-database)
  and [block children](https://developers.notion.com/reference/get-block-children).
- Google Drive [files.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get).
