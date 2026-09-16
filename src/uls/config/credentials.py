"""Fail-closed credential resolution honoring a per-credential declared source.

See docs/plans/credential-resolver.md (rev3, PLAN GO) for the full design
rationale, the fixed credential-to-allowed-source matrix, and the
composition-root ownership contract every consumer of this module must
follow: exactly one function per CLI/MCP entry point may construct a
CredentialResolver and call diagnose()/resolve(); every downstream
function receives the resulting snapshot as a required parameter and must
never read os.environ or construct its own resolver.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from uls.config._keyring_backend import read_keyring_credential
from uls.config.errors import ConfigurationError

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

DEFAULT_SOURCE: Final[str] = "environment"


@dataclass(frozen=True)
class ResolvedCredentials:
    """Immutable single-read snapshot.

    Not a Mapping subclass on purpose -- it deliberately does not support
    arbitrary repeated re-lookup semantics beyond what one composition
    needs, so callers cannot accidentally treat it as a live, re-readable
    view of the backing sources.

    __post_init__ defensively copies into a MappingProxyType so the
    snapshot is immune to (a) later mutation of whatever mapping the
    caller originally passed in, and (b) any attempt to mutate the
    internal mapping directly on the returned object itself.
    """

    _values: Mapping[str, str]

    def __post_init__(self) -> None:
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
      is available (only) via DiagnosticResolution.require(...), never
      exposed directly on this dataclass, so a diagnostic object itself
      never carries a secret value into wherever doctor's JSON output gets
      logged/printed.
    - "absent": source is "environment" and the variable is unset. This is
      always the status for an unset environment source, independent of
      whether resolve() will later treat that name as required or
      optional -- required-vs-optional is resolve()'s concern when turning
      a diagnosis into a raise/default decision, not diagnose()'s concern
      when producing the diagnosis itself.
    - "error": source is "keyring" and the entry/backend could not produce
      a value (missing entry, unsupported platform, backend identity
      mismatch, missing keyring package, etc.). A keyring-declared
      credential is never reported "absent"; opting into keyring is an
      explicit statement that this credential is expected to be
      keyring-backed, so a failure there is always surfaced as an error.
    """

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
    _ready_values: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", MappingProxyType(dict(self.results)))
        object.__setattr__(self, "_ready_values", MappingProxyType(dict(self._ready_values)))

    def require(self, names: frozenset[str]) -> ResolvedCredentials:
        """Build a ResolvedCredentials restricted to names, sourced only
        from this diagnose() call's already-obtained values. Performs no
        new environment/keyring read. Raises ConfigurationError naming
        every requested name whose status was not "ready" (a name absent
        from this diagnostic entirely is treated the same as an error: it
        was never diagnosed, so it cannot be required)."""

        missing = sorted(
            name for name in names
            if self.results.get(name) is None or self.results[name].status != "ready"
        )
        if missing:
            raise ConfigurationError(
                'required credential(s) not ready: ' + ', '.join(missing)
            )
        return ResolvedCredentials({name: self._ready_values[name] for name in names})

    def select(self, *, required: frozenset[str],
               optional: Mapping[str, str] | None = None) -> ResolvedCredentials:
        """Slice this already-diagnosed data into a ResolvedCredentials
        using the same required/optional selection rules as
        CredentialResolver.resolve() (see that method's docstring),
        without performing any new environment/keyring read. Every name
        passed here must have already been included in the diagnose()
        call that produced this DiagnosticResolution, or it is treated as
        an undiagnosed failure (same as require())."""

        return _select_from_diagnosis(self, required=required, optional=optional)


class CredentialResolver:
    """Fail-closed credential lookup honoring per-credential declared source.

    Never falls back across sources. Each declared source (environment
    variable, keyring entry) is read at most once per diagnose()/resolve()
    call -- resolve() is implemented internally as a thin filter over one
    diagnose() call, so there is exactly one code path that ever touches a
    backing source. Callers MUST reuse the single ResolvedCredentials or
    DiagnosticResolution object returned by one diagnose()/resolve() call
    for every consumer within one composition (see
    docs/plans/credential-resolver.md's composition-root ownership table)
    instead of calling either method again.
    """

    def __init__(self, declared_sources: Mapping[str, str] | None = None, *,
                 environ: Mapping[str, str] | None = None) -> None:
        sources = dict(declared_sources or {})
        for name, source in sources.items():
            if name not in ALLOWED_SOURCES:
                raise ConfigurationError(f'unknown credential name: {name}')
            if source not in ALLOWED_SOURCES[name]:
                raise ConfigurationError(
                    f'source {source!r} is not allowed for {name}; allowed: '
                    + ', '.join(sorted(ALLOWED_SOURCES[name]))
                )
        self._declared_sources: Mapping[str, str] = MappingProxyType(sources)
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ

    def _source_for(self, name: str) -> str:
        if name not in ALLOWED_SOURCES:
            raise ConfigurationError(f'unknown credential name: {name}')
        return self._declared_sources.get(name, DEFAULT_SOURCE)

    def _diagnose_one(self, name: str) -> tuple[CredentialDiagnostic, str | None]:
        source = self._source_for(name)
        if source == 'environment':
            value = self._environ.get(name, '')
            if value:
                return CredentialDiagnostic('ready'), value
            return CredentialDiagnostic('absent'), None
        # source == 'keyring'
        service, account = KEYRING_BINDINGS[name]
        try:
            value = read_keyring_credential(service, account)
            return CredentialDiagnostic('ready'), value
        except ConfigurationError as exc:
            code = exc.args[0] if exc.args else None
            detail = code if isinstance(code, str) else None
            return CredentialDiagnostic('error', detail=detail), None

    def diagnose(self, names: frozenset[str]) -> DiagnosticResolution:
        """Read each name's declared source exactly once. Never raises."""

        results: dict[str, CredentialDiagnostic] = {}
        ready_values: dict[str, str] = {}
        for name in names:
            diagnostic, value = self._diagnose_one(name)
            results[name] = diagnostic
            if diagnostic.status == 'ready' and value is not None:
                ready_values[name] = value
        return DiagnosticResolution(results, ready_values)

    def resolve(self, *, required: frozenset[str],
                optional: Mapping[str, str] | None = None) -> ResolvedCredentials:
        """Read each name in required | optional exactly once (via one
        diagnose() call), then:
        - every required name with status != "ready" raises
          ConfigurationError, collecting all such names into one message;
        - every optional name with status == "absent" is filled from the
          given per-key default;
        - every optional name with status == "error" ALSO raises
          ConfigurationError -- a keyring-declared credential's failure is
          never treated as "optional and unconfigured";
        - every "ready" name (required or optional) uses its diagnosed
          value.
        Returns a ResolvedCredentials over exactly required | optional's
        keys."""

        optional = dict(optional or {})
        all_names = frozenset(required) | frozenset(optional)
        diagnostic = self.diagnose(all_names)
        return _select_from_diagnosis(diagnostic, required=required, optional=optional)


def _select_from_diagnosis(diagnostic: DiagnosticResolution, *, required: frozenset[str],
                            optional: Mapping[str, str] | None = None) -> ResolvedCredentials:
    """Shared selection logic used by both CredentialResolver.resolve()
    (over a freshly diagnose()'d snapshot) and DiagnosticResolution.select()
    (over an already-diagnosed snapshot, performing no new read either
    way). See CredentialResolver.resolve()'s docstring for the exact
    required/optional/absent/error rules."""

    optional = dict(optional or {})
    values: dict[str, str] = {}
    failures: list[str] = []
    for name in required:
        result = diagnostic.results.get(name)
        if result is not None and result.status == 'ready':
            values[name] = diagnostic._ready_values[name]
        else:
            failures.append(name)
    for name, default in optional.items():
        result = diagnostic.results.get(name)
        if result is not None and result.status == 'ready':
            values[name] = diagnostic._ready_values[name]
        elif result is not None and result.status == 'absent':
            values[name] = default
        else:  # "error", or never diagnosed at all
            failures.append(name)
    if failures:
        raise ConfigurationError(
            'required credential(s) not ready: ' + ', '.join(sorted(failures))
        )
    return ResolvedCredentials(values)


__all__ = [
    'ALLOWED_SOURCES',
    'KEYRING_BINDINGS',
    'CredentialDiagnostic',
    'CredentialResolver',
    'DiagnosticResolution',
    'ResolvedCredentials',
]
