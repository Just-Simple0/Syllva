# Credential Resolver — design plan (rev3, revising rev2 REVISE)

Status: draft for independent plan review. No implementation yet.

## rev3 changelog (response to rev2 REVISE, 2 blockers)

1. **BLOCKER A (`ResolvedCredentials` not actually immutable)** — `frozen=True`
   only blocks reassigning the `_values` attribute; it does nothing to the
   dict object that attribute points at, so a caller-held reference to the
   original dict (including rev2's own suggested
   `ResolvedCredentials({...})` test-construction pattern) could still
   mutate an already-returned snapshot. Fixed by defensive-copying into a
   `MappingProxyType` inside `__post_init__` (using `object.__setattr__`,
   the standard pattern for a frozen dataclass normalizing its own field),
   so every `ResolvedCredentials` is immune both to later mutation of the
   caller's original dict and to direct mutation of its own internal
   mapping. Added both aliasing cases to the test matrix.
2. **BLOCKER B (single-read ownership not operationally defined for
   `doctor`/nested composition)** — Replaced the raising-only `resolve()`
   as the sole API with a non-raising `diagnose()` that reads each
   credential's declared source exactly once and records success/absence/
   error per key without ever raising, plus a non-reading `.require()` that
   builds a `ResolvedCredentials` from already-diagnosed values (no new
   source read). `doctor()` now has an operational contract: call
   `diagnose()` once, derive every `checks`/`optional_checks` boolean from
   its per-key results (replacing `_credential_ready()`'s raw
   `os.environ.get`), and reuse `.require(...)` on that same diagnostic
   object — never a second `diagnose()`/`resolve()` call — for the
   separation check and for `--live` provider construction. `resolve()` is
   now implemented internally as a filter over one `diagnose()` call, so
   there is exactly one code path that ever touches a backing source, used
   by both APIs. Added an explicit composition-root ownership table naming
   the one function per entry point (`dispatch()`'s `sync|process|run`
   branch, its `mcp local|remote` branch, and `doctor()`) that is allowed
   to call `.resolve()`/`.diagnose()`, with every downstream function
   (`worker.build_worker`, `runtime.build_intake_worker`,
   `runtime.build_retrieval`) taking `credentials: ResolvedCredentials` as
   a required (non-defaulted) parameter, so there is no code path left where
   a nested function could construct its own resolver or call resolve again.
   Confirmed `worker.build_worker`'s two branches (delegating to
   `runtime.build_intake_worker` for the intake-preview path, or building
   `NativeWorker` directly) against current `main`; both now receive the
   same caller-supplied snapshot instead of either branch reading
   `os.environ`/`secrets` itself.

Also fixed per rev2 review's two documentation notes: the top-level typo
guard's example list no longer claims `"creds"` is within edit-distance 2 of
`"credentials"` (it is not; removed from the example, kept `credentails`/
`credential`/case-only variants which genuinely are); and the "redundant"
description of an explicit `source: environment` entry is now grounded in
the concrete semantic split introduced by Blocker B's `diagnose()`/
`resolve()` unification (environment-sourced absence is optional-friendly,
keyring-sourced failure is always an error) rather than in any
declared-vs-undeclared distinction, which removes the ambiguity the review
flagged and makes the redundancy claim actually correct.

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
    it as a live, re-readable view of the backing sources.

    rev3 fix (Blocker A): frozen=True alone only stops `_values` from being
    *reassigned*; it does nothing to protect the dict object `_values`
    points at. __post_init__ defensively copies into a MappingProxyType so
    the snapshot is immune to (a) later mutation of whatever mapping the
    caller originally passed in, and (b) any attempt to mutate _values
    directly on the returned object itself.
    """

    _values: Mapping[str, str]

    def __post_init__(self) -> None:
        # object.__setattr__ is the standard escape hatch a frozen
        # dataclass uses to normalize its own field in __post_init__; this
        # does not weaken frozen-ness for any external caller.
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    def __getitem__(self, name: str) -> str:
        return self._values[name]

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name, default)

    def __contains__(self, name: str) -> bool:
        return name in self._values


@dataclass(frozen=True)
class CredentialDiagnostic:
    """One credential's non-raising diagnostic result from diagnose().

    status is one of "ready" | "absent" | "error":
    - "ready": the declared source produced a non-empty value; that value
      is available (only) via the DiagnosticResolution.require(...) path
      below, never exposed directly on this dataclass, so a diagnostic
      object itself never carries a secret value into wherever doctor's
      JSON output gets logged/printed.
    - "absent": source is "environment" and the variable is unset. This is
      the existing "optional feature not configured" case
      (e.g. GITHUB_READ_TOKEN unset today already means "GitHub reading
      disabled", not an error).
    - "error": source is "keyring" and the entry/backend could not produce
      a value (missing entry, unsupported platform, backend identity
      mismatch, missing keyring package, etc.). An unset "environment"
      source is always "absent", never "error", regardless of whether
      resolve() later treats that name as required or optional -- required-
      vs-optional is resolve()'s concern when turning a diagnosis into a
      raise/default decision, not diagnose()'s concern when producing the
      diagnosis itself. A keyring-declared
      credential is never reported "absent"; opting into keyring is an
      explicit statement that this credential is expected to be
      keyring-backed, so a failure there is always surfaced as an error,
      never silently treated as "not configured"."""

    status: str
    detail: str | None = None  # fixed short error class only; never a
                                # provider payload or credential value


@dataclass(frozen=True)
class DiagnosticResolution:
    """Result of one diagnose() call: per-key status plus the subset of
    successfully-resolved values, ready to be sliced into a
    ResolvedCredentials via require() without touching any backing source
    again."""

    results: Mapping[str, CredentialDiagnostic]
    _ready_values: Mapping[str, str]  # internal; only "ready" names appear here

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", MappingProxyType(dict(self.results)))
        object.__setattr__(self, "_ready_values", MappingProxyType(dict(self._ready_values)))

    def require(self, names: frozenset[str]) -> ResolvedCredentials:
        """Build a ResolvedCredentials restricted to names, sourced only
        from this diagnose() call's already-obtained values. Performs no
        new environment/keyring read. Raises ConfigurationError naming
        every requested name whose status was not "ready" (a name absent
        from this diagnostic entirely is treated the same as "error": it
        was never diagnosed, so it cannot be required)."""


class CredentialResolver:
    """Fail-closed credential lookup honoring per-credential declared source.

    Never falls back across sources. Each declared source (environment
    variable, keyring entry) is read at most once per diagnose()/resolve()
    call — resolve() is implemented internally as a thin filter over one
    diagnose() call, so there is exactly one code path that ever touches a
    backing source. Callers MUST reuse the single ResolvedCredentials or
    DiagnosticResolution object returned by one diagnose()/resolve() call
    for every consumer within one composition (see "Composition root
    ownership" below) instead of calling either method again.
    """

    def __init__(self, declared_sources: Mapping[str, str], *,
                 environ: Mapping[str, str] | None = None) -> None: ...
    # declared_sources: credential name -> "environment" | "keyring", as
    # parsed from config's credentials: section (missing entries default to
    # "environment"). Constructor validates every key against
    # ALLOWED_SOURCES and raises ConfigurationError on a disallowed source
    # for that name; it does not defer that check to resolve() time.

    def diagnose(self, names: frozenset[str]) -> DiagnosticResolution:
        """Read each name's declared source exactly once. Never raises.
        Every name gets exactly one CredentialDiagnostic (see status
        semantics above)."""

    def resolve(self, *, required: frozenset[str],
                optional: Mapping[str, str] = MappingProxyType({})
                ) -> ResolvedCredentials:
        """Convenience wrapper: internally calls
        self.diagnose(required | frozenset(optional)) exactly once, then:
        - every required name with status != "ready" raises
          ConfigurationError (collecting all such names into one message,
          not just the first);
        - every optional name with status == "absent" is filled from the
          given per-key default;
        - every optional name with status == "error" ALSO raises
          ConfigurationError (a keyring-declared credential's failure is
          never treated as "optional and unconfigured" — see
          CredentialDiagnostic.status docs);
        - every "ready" name (required or optional) uses its diagnosed
          value.
        Returns a ResolvedCredentials over exactly required | optional's
        keys."""
```

### Composition root ownership (Blocker B)

Exactly one function per CLI/MCP entry point is the "composition root" that
is allowed to construct a `CredentialResolver` and call
`.diagnose()`/`.resolve()`. Every function it calls into receives the
resulting `ResolvedCredentials` as a required (non-defaulted) parameter,
never reads `os.environ`/`secrets` itself, and never constructs its own
resolver — this is enforced by the parameter being required, not by
convention alone.

| Composition root | Reads sources | Passes `ResolvedCredentials` into |
| --- | --- | --- |
| `cli/main.py: dispatch()`, `sync|process|run` branch | `resolve(required={GOOGLE_WORKER_CREDENTIALS_FILE, NOTION_WORKER_TOKEN})` once | `worker.build_worker(config, credentials)` |
| `worker.py: build_worker(config, credentials)` | never | either `runtime.build_intake_worker(config, credentials, ...)` (intake-preview branch) or reads `credentials[...]` directly for its own `NativeWorker` branch — confirmed against current `main`: these are two mutually exclusive branches of the same function, not a nested call chain, so one snapshot covers both |
| `runtime.py: build_intake_worker(config, credentials, ...)` | never | reads `credentials[...]` directly when not using injected test ports; injected-port test callers pass a trivial `ResolvedCredentials({})` since that branch never reads a credential value (confirmed: `worker_provider_binding` discards its `values` argument today) |
| `cli/main.py: dispatch()`, `mcp local|remote` branch | `resolve(required={GOOGLE_MCP_CREDENTIALS_FILE, NOTION_MCP_TOKEN}, optional={GITHUB_READ_TOKEN: '', REMOTE_MCP_SECRET: '', REMOTE_MCP_EXPIRES_AT: '0', NOTION_WORKER_TOKEN: '', GOOGLE_WORKER_CREDENTIALS_FILE: ''})` once, covering both retrieval build and `BearerCredential` while retaining worker/MCP distinctness checks in `require_mcp_credentials()` | `runtime.build_retrieval(config, credentials)`; `BearerCredential(credentials['REMOTE_MCP_SECRET'], float(credentials['REMOTE_MCP_EXPIRES_AT']))` from the SAME snapshot |
| `runtime.py: build_retrieval(config, credentials)` | never | reads `credentials[...]` directly |
| `cli/main.py: doctor(config, live=False)` | `diagnose(ALL_CREDENTIAL_NAMES)` once | `.require(...)` on that SAME `DiagnosticResolution` (no new read) for the separation check and, when `live=True`, for `build_retrieval(config, credentials=snapshot)` / `google_service(snapshot['GOOGLE_MCP_CREDENTIALS_FILE'], ...)` |

`doctor()`'s `checks`/`optional_checks` booleans are derived directly from
`DiagnosticResolution.results[name].status` (`"ready"` → `True`, anything
else → `False`), replacing today's `_credential_ready()` raw
`os.environ.get` read. This keeps `doctor()`'s existing behavior of
reporting per-credential readiness without raising for a merely-unconfigured
optional credential, while a broken keyring-declared credential now
surfaces as `False` from a real diagnostic `"error"` status rather than
being indistinguishable from "not configured".

`CredentialResolver` is intentionally NOT a `Mapping` drop-in (rev1's
attempt to fake that shape produced the `get(name)` vs `get(key, default)`
conflict this review flagged). Every current
`values = os.environ if secrets is None else secrets` call site instead
becomes: the composition root named in the table above calls
`resolve()`/`diagnose()` exactly once, binds the result to one local name,
and passes that same `ResolvedCredentials`/`DiagnosticResolution` into
every function in that composition that previously read
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
    source: environment   # legal but redundant: omitting this entry defaults to
                           # "environment" too, and both are "absent -> optional
                           # default / required -> error" -- never "error always"
                           # the way source: keyring is (see Blocker B fix below)
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

`source: environment` is genuinely redundant with omitting the entry
entirely — both mean exactly the same thing to `CredentialResolver`,
because "environment" is defined by `CredentialDiagnostic.status`
semantics (see Proposed API) to behave identically whether it was
explicitly declared or defaulted: an unset environment variable is
`"absent"` (optional names fall back to their default, required names
error), never `"error"`. `source: keyring`, by contrast, is never
redundant with anything — declaring it is what makes a failed lookup
`"error"` instead of `"absent"`, for both required and optional names.
That is the one axis rev3 uses to decide fail-open-friendly-when-absent vs.
always-fail-closed-on-failure; there is no separate "was this credential
explicitly declared in YAML" axis anywhere in the resolver.

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
  `credential`, `Credentials`, etc. -- NOT `creds`, whose edit distance to
  `credentials` is far greater than 2 and is not caught by this guard),
  raise `ValueError` naming
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
| **rev3 additions (Blocker A: snapshot immutability)** | |
| Build `ResolvedCredentials` from a plain dict, then mutate the original dict afterward | The snapshot's values are unchanged (defensive copy, not an alias) |
| Attempt to write to `resolved._values` directly (e.g. `resolved._values['X'] = 'Y'`) | Raises (`MappingProxyType` rejects item assignment) |
| Same two cases for `DiagnosticResolution.results`/`_ready_values` | Same immutability guarantees |
| **rev3 additions (Blocker B: diagnose()/composition-root ownership)** | |
| `diagnose()` on a name whose declared source is `environment` and unset | Returns status `"absent"`, never raises |
| `diagnose()` on a name whose declared source is `keyring` and the entry is missing/backend broken | Returns status `"error"`, never raises |
| `diagnose()` call count against the backend per declared keyring name | Exactly 1 `get_password` call per name, matching `resolve()`'s existing guarantee, since `resolve()` is implemented in terms of `diagnose()` |
| `DiagnosticResolution.require({name})` where `name`'s status was `"absent"` or `"error"` | `ConfigurationError` naming `name`; no new backend/environment read occurs (assert call count unchanged from before `require()` was called) |
| `doctor()` with `GITHUB_READ_TOKEN: {source: keyring}` and the entry missing | `optional_checks['GITHUB_READ_TOKEN'] is False`; `doctor()` does not raise and does not report `status: 'failed'` merely because one optional keyring-declared credential errored (matches current "GitHub optional" behavior, generalized to keyring source) |
| `doctor(live=True)` | `build_retrieval`/`google_service` receive the exact `ResolvedCredentials` produced by `.require(...)` on `doctor()`'s one `diagnose()` call; assert no second `diagnose()`/`resolve()` call happens anywhere in the `doctor(live=True)` call path (mock/spy the resolver and assert call count == 1) |
| `worker.build_worker`'s two branches (intake-preview delegate vs. `NativeWorker`) | Both receive the composition root's one `ResolvedCredentials`; assert neither branch constructs a `CredentialResolver` or reads `os.environ` itself |

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

1. This plan document (rev3) requires an independent web ChatGPT review
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
