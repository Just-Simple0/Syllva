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

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from uls.config._keyring_backend import read_keyring_credential
from uls.config._secure_file import read_secure_file, secret_file_path
from uls.config.errors import ConfigurationError

# Fixed, code-owned. Never read from YAML. A config entry may only choose
# among the sources already allowed here for that credential name.
ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "NOTION_MCP_TOKEN": frozenset({"environment", "keyring"}),
    "GITHUB_READ_TOKEN": frozenset({"environment", "keyring"}),
    "LLM_API_KEY": frozenset({"environment", "keyring"}),
    "NOTION_WORKER_TOKEN": frozenset({"environment", "file"}),
    "GOOGLE_WORKER_CREDENTIALS_FILE": frozenset({"environment"}),
    "GOOGLE_MCP_CREDENTIALS_FILE": frozenset({"environment"}),
    "REMOTE_MCP_SECRET": frozenset({"environment", "file"}),
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

# Fixed, code-owned protected-secret-file locator per file-eligible
# credential (docs/plans/credential-secret-file-launcher.md rev5, section
# 2.1/2.2). Never read from YAML -- same "not configurable" discipline as
# KEYRING_BINDINGS above.
FILE_BINDINGS: Final[dict[str, str]] = {
    "NOTION_WORKER_TOKEN": "notion_worker_token.secret",
    "REMOTE_MCP_SECRET": "remote_mcp_secret.secret",
}

# Names whose declared source is always "environment" but whose value is a
# non-secret provider-credential *path*, not a raw secret string. These go
# through the same TOCTOU-safe secure-file boundary as FILE_BINDINGS
# entries (section 2.4/8.2 of the plan) during CredentialResolver's
# diagnose() call: verify identity (existence, no-follow, owner,
# permissions, size), read to EOF, and construct an immutable in-memory
# GoogleCredentialPayload. Downstream consumers (runtime.py/worker.py)
# receive this in-memory payload directly, with zero disk re-reading or
# path reopen.
GOOGLE_CREDENTIAL_PATH_NAMES: Final[frozenset[str]] = frozenset(
    {"GOOGLE_WORKER_CREDENTIALS_FILE", "GOOGLE_MCP_CREDENTIALS_FILE"}
)
# Google service-account JSON files (PEM private key + metadata) are
# larger than the 4096-byte raw-secret-string limit used elsewhere in this
# module; this is a separate, generous-but-bounded limit against a
# maliciously huge substituted file, not a secrecy boundary.
GOOGLE_CREDENTIAL_PATH_MAX_BYTES: Final[int] = 65536

DEFAULT_SOURCE: Final[str] = "environment"


@dataclass(frozen=True)
class GoogleCredentialPayload:
    """Immutable in-memory container for validated Google service-account JSON payload.

    Acquired strictly once during CredentialResolver's diagnose() via the TOCTOU-safe
    read_secure_file boundary, and handed to google_service / google_worker_service as
    already-parsed dict. Raw secrets are marked repr=False to prevent trace leaks.
    """

    info: Mapping[str, Any] = field(repr=False)
    source_name: str


def _error_code(exc: ConfigurationError) -> str | None:
    """Extract the fixed short error-class string from a ConfigurationError.

    ConfigurationError.__init__ formats args[0]/self.message as
    "Invalid configuration: <code>" (see uls/config/errors.py), so the raw
    code lives in exc.details['problems'][0], never in exc.args[0] itself.
    """

    problems = exc.details.get('problems') if isinstance(exc.details, dict) else None
    if isinstance(problems, list) and problems and isinstance(problems[0], str):
        return problems[0]
    return None


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

    _values: Mapping[str, str] = field(repr=False)
    _google_payloads: Mapping[str, GoogleCredentialPayload] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))
        object.__setattr__(self, "_google_payloads", MappingProxyType(dict(self._google_payloads)))

    def __getitem__(self, name: str) -> str:
        return self._values[name]

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name, default)

    def __contains__(self, name: str) -> bool:
        return name in self._values

    def get_google_payload(self, name: str) -> GoogleCredentialPayload | None:
        return self._google_payloads.get(name)


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
    _ready_values: Mapping[str, str] = field(default_factory=dict, repr=False)
    _google_payloads: Mapping[str, GoogleCredentialPayload] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", MappingProxyType(dict(self.results)))
        object.__setattr__(self, "_ready_values", MappingProxyType(dict(self._ready_values)))
        object.__setattr__(self, "_google_payloads", MappingProxyType(dict(self._google_payloads)))

    def get_google_payload(self, name: str) -> GoogleCredentialPayload | None:
        return self._google_payloads.get(name)

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
        matched_payloads = {k: v for k, v in self._google_payloads.items() if k in names}
        return ResolvedCredentials({name: self._ready_values[name] for name in names}, matched_payloads)

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
                 environ: Mapping[str, str] | None = None,
                 platform: str | None = None,
                 path_overrides: Mapping[str, str] | None = None) -> None:
        sources = dict(declared_sources or {})
        for name, source in sources.items():
            if name not in ALLOWED_SOURCES:
                raise ConfigurationError(f'unknown credential name: {name}')
            if not isinstance(source, str):
                raise ConfigurationError(f'source for {name} must be a string')
            if source not in ALLOWED_SOURCES[name]:
                raise ConfigurationError(
                    f'source {source!r} is not allowed for {name}; allowed: '
                    + ', '.join(sorted(ALLOWED_SOURCES[name]))
                )
        self._declared_sources: Mapping[str, str] = MappingProxyType(sources)
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ
        # platform: an explicit test-only override for which OS-native
        # keyring backend to target. None means "use the real sys.platform".
        # This must never be done by monkeypatching the global sys.platform
        # attribute instead, since that would also change the behavior of
        # unrelated platform-branching code elsewhere in the process (for
        # example orchestration/locks.py's fcntl/msvcrt selection).
        self._platform: str | None = platform
        # path_overrides (section 8.3 of the plan): non-secret Google
        # credential path values already extracted, exactly once, from the
        # composition root's own single config.yaml load (e.g. a
        # google_worker_credentials_path config field). Only names in
        # GOOGLE_CREDENTIAL_PATH_NAMES may appear here. When a name has an
        # override, it takes precedence over reading that same name from
        # self._environ -- this resolver never re-reads config.yaml or a
        # loader itself, only the values the composition root already
        # extracted and handed over as plain strings.
        overrides = dict(path_overrides or {})
        for name in overrides:
            if name not in GOOGLE_CREDENTIAL_PATH_NAMES:
                raise ConfigurationError(f'path override not allowed for {name}')
        self._path_overrides: Mapping[str, str] = MappingProxyType(overrides)

    def _source_for(self, name: str) -> str:
        if name not in ALLOWED_SOURCES:
            raise ConfigurationError(f'unknown credential name: {name}')
        return self._declared_sources.get(name, DEFAULT_SOURCE)

    def _diagnose_one(self, name: str) -> tuple[CredentialDiagnostic, str | None, GoogleCredentialPayload | None]:
        source = self._source_for(name)
        if name in GOOGLE_CREDENTIAL_PATH_NAMES:
            value = self._path_overrides.get(name) or self._environ.get(name, '')
            if not value:
                return CredentialDiagnostic('absent'), None, None
            expanded = Path(value).expanduser()
            try:
                raw = read_secure_file(expanded, max_bytes=GOOGLE_CREDENTIAL_PATH_MAX_BYTES)
            except ConfigurationError as exc:
                return CredentialDiagnostic('error', detail=_error_code(exc)), None, None
            try:
                parsed = json.loads(raw.decode('utf-8'))
                if not isinstance(parsed, Mapping):
                    return CredentialDiagnostic('error', detail='Google credential file is not valid JSON'), None, None
            except (UnicodeDecodeError, ValueError):
                return CredentialDiagnostic('error', detail='Google credential file is not valid JSON'), None, None
            payload = GoogleCredentialPayload(info=MappingProxyType(dict(parsed)), source_name=name)
            return CredentialDiagnostic('ready'), value, payload
        if source == 'environment':
            value = self._environ.get(name, '')
            if value:
                return CredentialDiagnostic('ready'), value, None
            return CredentialDiagnostic('absent'), None, None
        if source == 'file':
            path = secret_file_path(FILE_BINDINGS[name])
            try:
                raw = read_secure_file(path)
                value = raw.decode('utf-8', errors='strict')
                return CredentialDiagnostic('ready'), value, None
            except ConfigurationError as exc:
                return CredentialDiagnostic('error', detail=_error_code(exc)), None, None
            except UnicodeDecodeError:
                return CredentialDiagnostic('error', detail='secret_encoding_invalid'), None, None
        # source == 'keyring'
        service, account = KEYRING_BINDINGS[name]
        try:
            value = read_keyring_credential(service, account, platform=self._platform)
            return CredentialDiagnostic('ready'), value, None
        except ConfigurationError as exc:
            return CredentialDiagnostic('error', detail=_error_code(exc)), None, None

    def diagnose(self, names: frozenset[str]) -> DiagnosticResolution:
        """Read each name's declared source exactly once. Never raises."""

        results: dict[str, CredentialDiagnostic] = {}
        ready_values: dict[str, str] = {}
        google_payloads: dict[str, GoogleCredentialPayload] = {}
        for name in names:
            diagnostic, value, payload = self._diagnose_one(name)
            results[name] = diagnostic
            if diagnostic.status == 'ready' and value is not None:
                ready_values[name] = value
                if payload is not None:
                    google_payloads[name] = payload
        return DiagnosticResolution(results, ready_values, google_payloads)

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
    matched_payloads = {k: v for k, v in diagnostic._google_payloads.items() if k in values}
    return ResolvedCredentials(values, matched_payloads)


__all__ = [
    'ALLOWED_SOURCES',
    'KEYRING_BINDINGS',
    'CredentialDiagnostic',
    'CredentialResolver',
    'DiagnosticResolution',
    'ResolvedCredentials',
]
