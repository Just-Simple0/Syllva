# Credential Resolver — design plan (rev2, revising rev1 REVISE)

Status: draft for independent plan review. No implementation yet.

## rev2 changelog (response to rev1 REVISE, 5 blockers)

1. **BLOCKER 1 (file source ambiguity)** — `source: file` removed entirely
   from this rev. Only `environment | keyring` are legal declared sources.
   `GOOGLE_WORKER_CREDENTIALS_FILE`/`GOOGLE_MCP_CREDENTIALS_FILE` remain
   environment-only (unchanged: the value is a path string, read the same
   way as today). The protected-secret-file design stays a separate,
   not-yet-started follow-up, and will define its own read boundary
   (symlink/no-follow, regular-file, owner, POSIX mode/Windows ACL, size
   limit) before any `source: file` is reintroduced.
2. **BLOCKER 2 (unbound keyring locator + no source matrix)** — Added a fixed
   credential-to-allowed-source matrix and a fixed keyring `(service,
   account)` locator table, both hardcoded in `src/uls/config/credentials.py`,
   never taken from YAML. Config may only say `source: keyring`; it cannot
   name a keyring service/account, so a config cannot redirect Syllva to read
   an unrelated keyring item.
3. **BLOCKER 3 (fake Mapping drop-in + TOCTOU)** — Replaced the ambiguous
   `get()`/`get(key, default)` API with an explicit `resolve(required,
   optional) -> ResolvedCredentials` snapshot call. Every backing source is
   read exactly once per `resolve()` call; the returned immutable snapshot
   is threaded through every consumer within one composition (one `uls`
   command invocation, one MCP-server build, one worker build), so a
   distinctness check and the credential actually used are guaranteed to be
   the same value.
4. **BLOCKER 4 (integration scope narrower than reality)** — Added an
   explicit consumer inventory covering `src/uls/config/loader.py` (actually
   parsing `credentials:`), `src/uls/cli/main.py` (`_credential_ready`,
   `doctor`, `doctor --live`, the `mcp remote` dispatch branch), and
   `src/uls/worker.py`'s separate environment read, not just
   `src/uls/runtime.py`. Added a narrowly scoped typo guard for the new
   `credentials:` top-level key (edit-distance check against known top-level
   section names) plus unknown-credential-name and unknown-field rejection
   inside the section, without changing the pre-existing repository-wide
   behavior of ignoring unrelated unknown top-level keys (that is a separate,
   larger, out-of-scope change with its own blast radius).
5. **BLOCKER 5 (missing keyring dependency + incomplete hardening)** — Added
   `keyring` as a `pyproject.toml` optional extra with lazy import and a
   fixed `ConfigurationError` when `source: keyring` is declared but the
   package is absent. Spelled out the full invariant set reused from
   `scripts/knu_lms_sync.py`'s pattern (platform gate, explicit backend
   class import, `__module__` identity verification, forced `keychain =
   None` on macOS with re-verification), not just the `__module__` check.

Also fixed per the review's non-blocking notes: `LLM_API_KEY` is described
accurately as an existing `loader.SECRET_KEYS` entry that currently has no
resolver/runtime consumer, not a "newly introduced slot"; "byte-identical
behavior" reworded to "observable behavior equivalent"; the file-based
rationale for `GOOGLE_*_CREDENTIALS_FILE` no longer rests solely on the
Windows Credential Manager blob-size limit; and the "risky" classification
citation now points at the operator's global `~/.codex/AGENTS.md` review
policy (which this repository's own `AGENTS.md` explicitly defers to for
model/effort/review/safety rules), not this repository's `AGENTS.md` itself.

## Background

The 2026-09-14 credential-storage architecture discussion (see handoff.md)
produced an independent GPT Pro conclusion that a wholesale move of every
credential into OS keyring is not advisable:

- macOS default/login Keychain is bound to an interactive login session
  (Launch Agent vs Launch Daemon distinction); a real unattended LaunchDaemon
  running while logged out cannot rely on it without hitting lock/ACL prompts.
- Python `keyring` itself documents that scripts sharing the same Python
  executable can read each other's secrets without an OS prompt, so splitting
  keyring items per worker/MCP does not by itself create process isolation.
- Windows Credential Manager is comparatively suited to unattended Password
  logon tasks, but `CredRead`/`CREDENTIALA` blobs are capped at 2560 bytes,
  which rules out storing a Google service-account JSON there.

The recommended structure is a per-credential hybrid, not an all-or-nothing
migration:

| Credential | Storage | Rationale |
| --- | --- | --- |
| Canvas/KNU LMS token (human-enrolled) | OS keyring (unchanged) | Already implemented and verified in `scripts/knu_lms_sync.py`. |
| Main worker credentials (unattended schedule) | Protected secret file + minimal-env launcher | `.env`-sourced-from-interactive-shell is fundamentally incompatible with scheduled/logged-out execution. |
| `NOTION_MCP_TOKEN`, `GITHUB_READ_TOKEN`, `LLM_API_KEY` (human-run interactive paths) | OS keyring (new) | Short string tokens; keyring is well suited and improves operator UX. |
| `GOOGLE_WORKER_CREDENTIALS_FILE`, `GOOGLE_MCP_CREDENTIALS_FILE` | File + OS ACL (unchanged) | Google's SDK loads structured JSON natively from a file path (`google.auth.load_credentials_from_file`); file ACLs and atomic-replace rotation are simpler to reason about for this shape than a fixed-size credential-manager blob, and the Windows `CREDENTIALA` blob cap (2560 bytes, Microsoft-documented) rules out the blob route regardless. |
| `REMOTE_MCP_SECRET` | Out of scope for this plan | A long-lived bearer secret is better replaced by OAuth/OIDC than moved into keyring; tracked separately. |

## Scope of this plan

In scope:
1. A `CredentialResolver` abstraction with an explicit `source` per credential
   (`environment` | `keyring` — `file` deferred, see rev2 changelog #1),
   used everywhere `src/uls/runtime.py`, `src/uls/worker.py`, and
   `src/uls/cli/main.py` currently read `os.environ` directly (full
   inventory below).
2. Config schema addition: a `credentials:` section mapping credential name to
   `{source: environment|keyring}` — no other fields are legal per entry.
   Default behavior when a credential has no explicit `credentials:` entry,
   or when the whole `credentials:` section is absent, is the current
   behavior (`environment`), so existing deployments are observably
   behavior-equivalent on upgrade.
3. Fail-closed contract: if a credential's declared source fails to resolve
   (env var unset, keyring entry missing, keyring backend/package
   unavailable), `CredentialResolver.resolve()` raises `ConfigurationError`
   immediately for that call. It never falls back to a different source than
   the one declared. This mirrors the existing `require_mcp_credentials`
   fail-closed pattern in `runtime.py`.
4. A fixed, code-owned `(service, account)` keyring locator per
   keyring-eligible credential (`KEYRING_BINDINGS`), plus a fixed
   credential-to-allowed-source matrix (`ALLOWED_SOURCES`). Neither is
   configurable from YAML; config only selects among the sources that
   matrix already allows for that credential name.
5. A dedicated keyring-backend helper in `src/uls/config/_keyring_backend.py`
   that reimplements (does not import, see rationale below) the full
   invariant set from `scripts/knu_lms_sync.py`'s `_explicit_os_keyring()` /
   `_expected_backend_module()`: platform gate, explicit backend class
   import, `__module__` identity verification, and forced/re-verified
   `keychain = None` on macOS.
6. Migrate `NOTION_MCP_TOKEN`, `GITHUB_READ_TOKEN`, and `LLM_API_KEY`
   (already present in `loader.SECRET_KEYS` but with no resolver/runtime
   consumer today) to be keyring-resolvable when `credentials.<name>.source:
   keyring` is configured; `environment` remains available and is the
   default for all three.

Out of scope for this plan (tracked as follow-ups, not implemented here):
- The main-worker "protected secret file + minimal-env launcher" pattern
  (macOS `~/Library/Application Support/Syllva/secrets/` with `0700`/`0600`,
  Windows NTFS DACL via `icacls`). This depends on the Stage D Windows
  scheduler `Password` logon fix (already committed) and deserves its own
  design + review pass focused on the launcher's own attack surface.
- `source: file` as a general `CredentialResolver` source (see rev2
  changelog #1) — will be designed together with the item above, sharing one
  read-boundary implementation instead of two.
- `REMOTE_MCP_SECRET` → OAuth/OIDC replacement.
- Any change to `GOOGLE_WORKER_CREDENTIALS_FILE`/`GOOGLE_MCP_CREDENTIALS_FILE`
  beyond documenting the existing file+ACL expectation; these stay file-based.
- Any change to the Canvas/KNU LMS sidecar's existing keyring implementation.
  `scripts/knu_lms_sync.py` deliberately avoids importing the `uls` package
  so it stays runnable as a bare script without a full `pip install
  -e '.[...]'`; `src/uls/config/_keyring_backend.py` reimplements the same
  invariants for the installed-package side rather than creating an import
  dependency in either direction. A follow-up plan may unify both into one
  shared module once the sidecar's standalone-script constraint is
  revisited; that unification is explicitly not part of this plan.

### Full consumer inventory (blocker 4)

Every current direct `os.environ` read that this plan's `CredentialResolver`
must replace, confirmed against `main` at commit `00c7179`:

| File | Symbol | Current behavior |
| --- | --- | --- |
| `src/uls/runtime.py` | `require_mcp_credentials` | reads `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`, `NOTION_WORKER_TOKEN`, `GOOGLE_WORKER_CREDENTIALS_FILE` from the `secrets` mapping passed in (defaults to `os.environ`) |
| `src/uls/runtime.py` | `build_intake_worker` | reads `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` |
| `src/uls/runtime.py` | `build_retrieval` | reads `GOOGLE_MCP_CREDENTIALS_FILE`, `NOTION_MCP_TOKEN`, `GITHUB_READ_TOKEN` (optional) |
| `src/uls/worker.py` | `build_worker` (around line 231) | separately reads `os.environ` for `GOOGLE_WORKER_CREDENTIALS_FILE`, `NOTION_WORKER_TOKEN` — must be confirmed during implementation whether this duplicates or diverges from `build_intake_worker`'s read, and consolidated onto one resolved snapshot either way |
| `src/uls/cli/main.py` | `_credential_ready` | direct `os.environ.get(key, '')`, used only for presence-checking in `doctor()` |
| `src/uls/cli/main.py` | `doctor` | calls `require_mcp_credentials(os.environ)` directly; also reads `os.environ.get('REMOTE_MCP_SECRET', '')` / `REMOTE_MCP_EXPIRES_AT` for the remote-profile check |
| `src/uls/cli/main.py` | `doctor(live=True)` | reads `os.environ['GOOGLE_MCP_CREDENTIALS_FILE']` directly for the live Drive probe |
| `src/uls/cli/main.py` | `dispatch` (`mcp remote` branch) | reads `os.environ.get('REMOTE_MCP_SECRET', '')` / `REMOTE_MCP_EXPIRES_AT` to build `BearerCredential` |

All eight call sites move onto `CredentialResolver`-produced
`ResolvedCredentials` snapshots. `REMOTE_MCP_SECRET`/`REMOTE_MCP_EXPIRES_AT`
stay `environment`-only per `ALLOWED_SOURCES` (unchanged behavior, routed
through the same resolver purely for one consistent credential-reading code
path instead of two).

## Proposed API

```python
# src/uls/config/credentials.py (new module)
from types import MappingProxyType
from typing import Final

# Fixed, code-owned. Never read from YAML. A config entry may only choose
# among the sources already allowed here for that credential name.
ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "NOTION_MCP_TOKEN": frozenset({"environment", "keyring"}),
    "GITHUB_READ_TOKEN": frozenset({"environment", "keyring"}),
    "LLM_API_KEY": frozenset({"environment", "keyring"}),
    "NOTION_WORKER_TOKEN": frozenset({"environment"}),
    "GOOGLE_WORKER_CREDENTIALS_FILE": frozenset({"environment"}),
    "GOOGLE_MCP_CREDENTIALS_FILE": frozenset({"environment"}),
    "REMOTE_MCP_SECRET": frozenset({"environment"}),
    "REMOTE_MCP_EXPIRES_AT": frozenset({"environment"}),
}

# Fixed, code-owned keyring locator per keyring-eligible credential. Never
# read from YAML; config cannot redirect a credential to an arbitrary
# keyring (service, account) pair.
KEYRING_BINDINGS: Final[dict[str, tuple[str, str]]] = {
    "NOTION_MCP_TOKEN": ("Syllva MCP", "notion_mcp_token"),
    "GITHUB_READ_TOKEN": ("Syllva MCP", "github_read_token"),
    "LLM_API_KEY": ("Syllva LLM", "llm_api_key"),
}


@dataclass(frozen=True)
class ResolvedCredentials:
    """Immutable single-read snapshot. Not a Mapping subclass on purpose —
    it deliberately does not support arbitrary repeated re-lookup semantics
    beyond what one composition needs, so callers cannot accidentally treat
    it as a live, re-readable view of the backing sources."""

    _values: Mapping[str, str]

    def __getitem__(self, name: str) -> str:
        return self._values[name]

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name, default)

    def __contains__(self, name: str) -> bool:
        return name in self._values

class CredentialResolver:
    """Fail-closed credential lookup honoring per-credential declared source.

    Never falls back across sources. A missing/unreadable declared source is
    a ConfigurationError raised from resolve(), not an empty string and not a
    silent environment fallback. Each declared source (environment variable,
    keyring entry) is read at most once per resolve() call. Callers MUST
    reuse the single ResolvedCredentials object returned by one resolve()
    call for every consumer within one composition (one 'uls' command
    invocation, one MCP-server build, one worker build) instead of calling
    resolve() again, so a distinctness/validity check and the value actually
    used by a provider client are guaranteed to be the same read.
    """

    def __init__(self, declared_sources: Mapping[str, str], *,
                 environ: Mapping[str, str] | None = None) -> None: ...
    # declared_sources: credential name -> "environment" | "keyring", as
    # parsed from config's credentials: section (missing entries default to
    # "environment"). Constructor validates every key against
    # ALLOWED_SOURCES and raises ConfigurationError on a disallowed source
    # for that name; it does not defer that check to resolve() time.

    def resolve(self, *, required: frozenset[str],
                optional: Mapping[str, str] = MappingProxyType({})
                ) -> ResolvedCredentials:
        """Read each name in required/optional exactly once through its
        declared source. required entries raise ConfigurationError when
        their declared source cannot produce a non-empty value; optional
        entries fall back to the given per-key default ONLY when the
        credential is entirely undeclared in config AND unset in the
        environment — a *declared* keyring source that fails to resolve
        still raises, even for an "optional" name, because declaring a
        source is an explicit statement that this credential is expected
        to be backed by it."""
```

`CredentialResolver` is intentionally NOT a `Mapping` drop-in (rev1's
attempt to fake that shape produced the `get(name)` vs `get(key, default)`
conflict this review flagged). Every current
`values = os.environ if secrets is None else secrets` call site instead
becomes: call `resolve()` exactly once at the top of that composition,
bind the result to one local name, and pass that same `ResolvedCredentials`
object into every function in that composition that previously read
`os.environ`/`secrets` directly (`require_mcp_credentials`,
`google_service`, the Notion `Client(...)` constructor call, etc.).
`require_mcp_credentials`'s signature changes from `Mapping[str, str]` to
`ResolvedCredentials`, since a raw `os.environ`/plain-dict caller no longer
applies once every call site has a resolver in front of it; existing unit
tests that construct a plain dict today will wrap it as
`ResolvedCredentials({...})` directly for the pure-environment test cases
that need no resolver machinery at all.

## Config schema addition

```yaml
credentials:
  GOOGLE_WORKER_CREDENTIALS_FILE:
    source: environment   # unchanged default; explicit entries are legal but redundant here
  NOTION_MCP_TOKEN:
    source: keyring
  GITHUB_READ_TOKEN:
    source: environment
  LLM_API_KEY:
    source: environment
```

A `credentials:` entry has exactly one legal field, `source`, whose value
must be a member of `ALLOWED_SOURCES[<credential name>]` for that specific
credential. There is no `keyring_service`, `keyring_account`, or
`file_path` field anywhere in this schema — those are entirely code-owned
per Blocker 2.

Parsing rules (in `src/uls/config/loader.py`, new `_credentials_section()`,
plus `src/uls/config/validation.py`):
- `credentials:` entirely absent from the YAML root → `{}`, i.e. every
  credential defaults to `environment`. Observably behavior-equivalent to
  today for every existing deployment.
- `credentials:` present but not a mapping → `ValueError`.
- Each key must be a member of `ALLOWED_SOURCES`; an unknown credential
  name → `ValueError` naming the offending key (typo guard for credential
  names, e.g. `NOTION_MPC_TOKEN`).
- Each value must be a mapping with exactly the key set `{"source"}`; any
  extra or missing field → `ValueError`.
- `source` must be a string in `ALLOWED_SOURCES[<name>]`; a disallowed
  source for that specific credential (e.g. `GOOGLE_WORKER_CREDENTIALS_FILE:
  {source: keyring}`) → `ValueError`, independent of the constructor-time
  check `CredentialResolver.__init__` also performs (defense in depth: the
  config loader rejects it before a `CredentialResolver` is ever built, and
  the resolver itself refuses to accept an invalid declared-sources mapping
  from any other caller).
- **Top-level typo guard (Blocker 4, "or equivalent typo detection")**:
  after parsing the known top-level sections, if the raw YAML root contains
  a key that is not exactly `"credentials"` but is within edit-distance 2
  of `"credentials"` case-insensitively (catches `credentails`,
  `credential`, `Credentials`, `creds`, etc.), raise `ValueError` naming
  both the offending key and the expected `credentials`. This does not
  change the pre-existing repository-wide behavior of silently ignoring
  unrelated unknown top-level keys; it only prevents this plan's own new
  section from being silently downgraded to "absent" by a near-miss typo,
  which is the specific failure mode Blocker 4 described.

## Fail-closed test matrix (to add before/with implementation)

| Scenario | Expected |
| --- | --- |
| No `credentials:` section at all | Observable behavior equivalent to current `os.environ` reads (regression baseline) |
| `source: environment`, var set | Returns the value |
| `source: environment`, var unset | `ConfigurationError`, same as today |
| `source: keyring`, entry present | Returns the value via the fixed `KEYRING_BINDINGS` locator; no environment read attempted |
| `source: keyring`, entry missing | `ConfigurationError`; must not fall back to checking the environment variable of the same name |
| `source: keyring`, backend identity spoofed (monkeypatched non-native module) | Rejected, same fail-closed check as `scripts/knu_lms_sync.py`'s `_explicit_os_keyring()` |
| `source: keyring` on an unsupported platform (not darwin/win32) | `ConfigurationError` (`keychain_platform_unsupported`-equivalent), not silently treated as absent |
| `source: keyring` declared but the `keyring` package is not installed | `ConfigurationError`, never falls back to `environment` |
| macOS backend's `keychain` attribute cannot be forced back to `None` (monkeypatched override) | Rejected before any `get_password` call |
| Disallowed source for a given credential (e.g. `GOOGLE_WORKER_CREDENTIALS_FILE: {source: keyring}`) | Rejected at config load, before a `CredentialResolver` is constructed |
| Unknown credential name under `credentials:` | `ValueError` at config load |
| Unknown field inside a `credentials:` entry (e.g. stray `keyring_service`) | `ValueError` at config load |
| Top-level `credentails:` (typo) | `ValueError` naming the near-miss key, not a silent downgrade to all-environment |
| `resolve()` call reads each declared keyring entry exactly once | Assert backend `get_password` call count == 1 per name per `resolve()` call |
| Keyring value changes between a `resolve()` call and a later, separate `resolve()` call | The already-returned `ResolvedCredentials` snapshot from the first call is unaffected (TOCTOU regression guard: build one snapshot, mutate the backend, assert the snapshot's values are unchanged) |
| `require_mcp_credentials`-style worker/MCP distinctness check | Still enforced when both resolve through `CredentialResolver`, regardless of whether they use the same or different sources |
| `require_mcp_credentials` and the actual provider `Client(...)` construction in the same command invocation | Both read from the same `ResolvedCredentials` object, not two separate `resolve()` calls |

## keyring dependency (Blocker 5)

`pyproject.toml` gains a new optional extra:

```toml
[project.optional-dependencies]
keyring = ["keyring>=25.0"]
```

`src/uls/config/_keyring_backend.py` imports `keyring` lazily (inside the
function that needs it, not at module load time), so environments that never
configure `source: keyring` for any credential never need the dependency
installed. If `source: keyring` is declared for any credential and the
import fails, `CredentialResolver` raises a fixed `ConfigurationError`
(e.g. `keyring_dependency_missing`) — fail-closed, no fallback to
`environment` for that credential.

### Full backend hardening invariant set (not just `__module__`)

Reimplemented from `scripts/knu_lms_sync.py`'s `_explicit_os_keyring()` /
`_expected_backend_module()` (see rationale for reimplementation vs. import
in "Out of scope" above):

1. Platform gate: only `sys.platform in {"darwin", "win32"}`; anything else
   is `ConfigurationError`, never silently treated as "no keyring
   available, fall back".
2. Explicit backend class import — `keyring.backends.macOS.Keyring` on
   `darwin`, `keyring.backends.Windows.WinVaultKeyring` on `win32` — never
   `keyring.get_keyring()`, which can resolve to any installed or
   config-selected backend.
3. Backend identity verification: after construction,
   `backend.__class__.__module__` must exactly equal the expected dotted
   path; a mismatch (e.g. a monkeypatched or third-party substitute) is
   `ConfigurationError`.
4. macOS alternate-keychain block: when `hasattr(backend, "keychain")`,
   force `backend.keychain = None` and re-read the attribute to confirm it
   is still `None` after assignment; a backend that resists this (e.g. via
   a property override) is `ConfigurationError`. Windows'
   `WinVaultKeyring` has no such attribute and this step is a no-op there,
   matching the sidecar's existing `hasattr` guard.
5. A missing/empty `get_password` result (`None` or `""`) is
   `ConfigurationError`, never an empty-string credential value.
6. No credential value obtained from `get_password` may appear in any log
   line, exception message, or CLI/JSON output — enforced the same way the
   existing `fake-private-note`-style tests in
   `tests/contract/test_worker_cli.py` already enforce it for other
   credential paths.

## Review requirement

"Risky" classification citation corrected: the operator's global
`~/.codex/AGENTS.md` personal-defaults review policy — not this
repository's own `AGENTS.md`, which explicitly defers ("Model/effort
selection, orchestration, reviews, and safety follow the applicable global
Codex `AGENTS.md`") — is what names auth/authorization code as "risky" and
requires an independent web ChatGPT review for both plan and final review.

Before any implementation of this plan lands:

1. This plan document (rev2) requires an independent web ChatGPT review
   (insane-review Pro, or the user-authorized 매우 높음 fallback if Pro
   quota is exhausted).
2. This coding session's exec sandbox blocks outbound network including
   loopback (confirmed again this session: `curl` to `api.github.com` and to
   `127.0.0.1:9222` both returned no connection), so insane-review's built-in
   Playwright/CDP automation cannot run from inside this exec sandbox — this
   matches the same failure documented in handoff.md from 2026-09-14.
3. Fallback path: use the `cua_repl` desktop-automation channel (which
   controls the user's real desktop browser, not a network call from this
   sandboxed shell) to drive the review, or have the user paste this plan
   directly into ChatGPT web as they did for the earlier credential-storage
   conclusion.
4. No implementation commit should land against live `NOTION_MCP_TOKEN`,
   `GITHUB_READ_TOKEN`, or `LLM_API_KEY` handling until this plan receives an
   independent GO (or REVISE-and-fix cycle) — matching the same gate already
   used for Stage A/B/C in this repository's PR #9/#10 history.

## Verification plan (once implementation starts)

- `python -m pytest -q` full suite plus new `tests/unit/test_credential_resolver.py`
  covering the fail-closed matrix above.
- Revert-test-restore on the fail-closed branches (temporarily allow silent
  fallback, confirm the new tests catch it, then restore).
- `ruff check`, `mypy`, `python -m compileall`, `scripts/lint_behavior_projection.py`
  all clean/baseline, matching the verification discipline used for the C2
  readiness-funnel work in this same repository.
- No secret value (keyring token content, file contents) may appear in any
  test assertion string, log line, or exception message — mirrors the existing
  `fake-private-note`-style tests already in `tests/contract/test_worker_cli.py`.
- New: assert `_credentials_section()` parsing raises for every malformed
  case in the test matrix above (typo top-level key, unknown credential
  name, unknown field, disallowed source) using `load_config_unvalidated`
  directly, not only through the full `load_config`/`validate_config` path,
  so a parsing-time rejection is distinguished from a validation-time one in
  the test suite.
