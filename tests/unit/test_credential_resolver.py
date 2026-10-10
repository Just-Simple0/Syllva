"""Regression tests for CredentialResolver (docs/plans/credential-resolver.md, PLAN GO).

Covers the fail-closed test matrix from the reviewed plan: source
resolution, the environment-vs-keyring absent/error split, snapshot
immutability (rev3 Blocker A), and the non-raising diagnose()/require()/
select() contract (rev3 Blocker B).

Platform selection for these tests uses CredentialResolver's explicit
platform= constructor override, never monkeypatch.setattr(sys, 'platform',
...). The latter mutates the single process-global sys module and would
also change the behavior of unrelated platform-branching code elsewhere
(for example orchestration/locks.py's fcntl/msvcrt selection), which is
exactly what caused a real Windows CI failure during this feature's
development: a test that forced sys.platform to 'darwin' made SQLite
locking code running on a real Windows CI runner try to select POSIX
locking primitives.
"""
from __future__ import annotations

import types

import pytest

from uls.config.credentials import (
    ALLOWED_SOURCES,
    KEYRING_BINDINGS,
    CredentialResolver,
    ResolvedCredentials,
)
from uls.config.errors import ConfigurationError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Construction / declared-source validation
# ---------------------------------------------------------------------------


def test_unknown_credential_name_rejected_at_construction():
    with pytest.raises(ConfigurationError):
        CredentialResolver({'NOT_A_REAL_CREDENTIAL': 'environment'})


def test_disallowed_source_rejected_at_construction():
    with pytest.raises(ConfigurationError):
        CredentialResolver({'GOOGLE_WORKER_CREDENTIALS_FILE': 'keyring'})


def test_every_allowed_source_construction_succeeds():
    for name, sources in ALLOWED_SOURCES.items():
        for source in sources:
            CredentialResolver({name: source})


# ---------------------------------------------------------------------------
# resolve(): required/optional, environment source
# ---------------------------------------------------------------------------


def test_resolve_required_environment_present():
    resolver = CredentialResolver({}, environ={'NOTION_MCP_TOKEN': 'tok'})
    snap = resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))
    assert snap['NOTION_MCP_TOKEN'] == 'tok'


def test_resolve_required_environment_unset_raises():
    resolver = CredentialResolver({}, environ={})
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


def test_resolve_optional_environment_unset_uses_default():
    resolver = CredentialResolver({}, environ={})
    snap = resolver.resolve(required=frozenset(), optional={'GITHUB_READ_TOKEN': ''})
    assert snap['GITHUB_READ_TOKEN'] == ''


def test_no_credentials_section_matches_environment_default(tmp_path):
    """Absent credentials: config -> every name defaults to 'environment',
    observably behavior-equivalent to the pre-resolver os.environ reads."""
    creds_file = tmp_path / 'x.json'
    from uls.config._secure_file import write_secure_file
    write_secure_file(creds_file, b'{}')
    resolver = CredentialResolver(None, environ={'GOOGLE_MCP_CREDENTIALS_FILE': str(creds_file)})
    snap = resolver.resolve(required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE'}))
    assert snap['GOOGLE_MCP_CREDENTIALS_FILE'] == str(creds_file)
    assert snap.get_google_payload('GOOGLE_MCP_CREDENTIALS_FILE') is not None


# ---------------------------------------------------------------------------
# Fake keyring backend harness
# ---------------------------------------------------------------------------


def _install_fake_macos_keyring(monkeypatch, *, password_by_account=None, call_counter=None):
    """Install a fake keyring.backends.macOS.Keyring module the resolver's
    explicit_os_keyring() will import, without requiring the real keyring
    package. Does NOT touch sys.platform; callers pass platform='darwin'
    explicitly to CredentialResolver instead."""

    password_by_account = password_by_account or {}
    call_counter = call_counter if call_counter is not None else []

    class FakeMacKeyring:
        __module__ = 'keyring.backends.macOS'

        def __init__(self):
            self.keychain = 'some-other-keychain'

        def get_password(self, service, account):
            call_counter.append((service, account))
            return password_by_account.get((service, account))

    module = types.ModuleType('keyring.backends.macOS')
    module.Keyring = FakeMacKeyring
    monkeypatch.setitem(__import__('sys').modules, 'keyring.backends.macOS', module)
    return call_counter


# ---------------------------------------------------------------------------
# Keyring source: ready / error / call-count
# ---------------------------------------------------------------------------


def test_keyring_source_ready(monkeypatch):
    service, account = KEYRING_BINDINGS['NOTION_MCP_TOKEN']
    _install_fake_macos_keyring(monkeypatch, password_by_account={(service, account): 'kr-tok'})
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    snap = resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))
    assert snap['NOTION_MCP_TOKEN'] == 'kr-tok'


def test_keyring_source_missing_entry_is_configuration_error(monkeypatch):
    _install_fake_macos_keyring(monkeypatch, password_by_account={})
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={
        'NOTION_MCP_TOKEN': 'should-not-be-read',
    }, platform='darwin')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


def test_keyring_source_never_falls_back_to_environment_even_when_optional(monkeypatch):
    """A declared keyring source that fails must raise even for an
    'optional' name, never silently use the given default."""
    _install_fake_macos_keyring(monkeypatch, password_by_account={})
    resolver = CredentialResolver({'GITHUB_READ_TOKEN': 'keyring'}, environ={
        'GITHUB_READ_TOKEN': 'ignored-env-value',
    }, platform='darwin')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset(), optional={'GITHUB_READ_TOKEN': ''})


def test_keyring_backend_called_exactly_once_per_resolve(monkeypatch):
    service, account = KEYRING_BINDINGS['NOTION_MCP_TOKEN']
    counter = _install_fake_macos_keyring(
        monkeypatch, password_by_account={(service, account): 'kr-tok'})
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))
    assert counter == [(service, account)]


def test_keyring_unsupported_platform_is_configuration_error():
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='linux')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


def test_keyring_backend_identity_spoof_rejected(monkeypatch):
    """A backend whose concrete class does not report the expected
    __module__ must be rejected, matching the LMS sidecar's own defense."""

    class SpoofedKeyring:
        __module__ = 'attacker.module'

        def __init__(self):
            self.keychain = None

        def get_password(self, service, account):
            return 'stolen'

    module = types.ModuleType('keyring.backends.macOS')
    module.Keyring = SpoofedKeyring
    monkeypatch.setitem(__import__('sys').modules, 'keyring.backends.macOS', module)
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


def test_keyring_dependency_missing_is_configuration_error(monkeypatch):
    monkeypatch.delitem(__import__('sys').modules, 'keyring.backends.macOS', raising=False)
    monkeypatch.delitem(__import__('sys').modules, 'keyring', raising=False)
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


def test_keyring_alternate_keychain_override_is_forced_and_reverified(monkeypatch):
    """A backend that resists having its keychain attribute forced back to
    None (e.g. via a property that ignores assignment) must be rejected
    before any get_password call."""

    class ResistantKeyring:
        __module__ = 'keyring.backends.macOS'

        def __init__(self):
            self._calls = []

        def __setattr__(self, name, value):
            if name == 'keychain':
                object.__setattr__(self, '_keychain_forced', False)
                return
            object.__setattr__(self, name, value)

        @property
        def keychain(self):
            return 'still-overridden'

        def get_password(self, service, account):
            raise AssertionError('get_password must not be called after a rejected override')

    module = types.ModuleType('keyring.backends.macOS')
    module.Keyring = ResistantKeyring
    monkeypatch.setitem(__import__('sys').modules, 'keyring.backends.macOS', module)
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))


# ---------------------------------------------------------------------------
# ResolvedCredentials immutability (rev3 Blocker A)
# ---------------------------------------------------------------------------


def test_resolved_credentials_immune_to_source_dict_mutation():
    original = {'X': 'orig'}
    snapshot = ResolvedCredentials(original)
    original['X'] = 'mutated'
    original['Y'] = 'new'
    assert snapshot['X'] == 'orig'
    assert 'Y' not in snapshot


def test_resolved_credentials_internal_mapping_rejects_mutation():
    snapshot = ResolvedCredentials({'X': 'orig'})
    with pytest.raises(TypeError):
        snapshot._values['X'] = 'hack'


# ---------------------------------------------------------------------------
# diagnose()/require()/select(): non-raising diagnostics, no re-read
# ---------------------------------------------------------------------------


def test_diagnose_environment_absent_never_raises():
    resolver = CredentialResolver({}, environ={})
    diagnostic = resolver.diagnose(frozenset({'GITHUB_READ_TOKEN'}))
    assert diagnostic.results['GITHUB_READ_TOKEN'].status == 'absent'


def test_diagnose_keyring_error_never_raises(monkeypatch):
    _install_fake_macos_keyring(monkeypatch, password_by_account={})
    resolver = CredentialResolver({'GITHUB_READ_TOKEN': 'keyring'}, environ={}, platform='darwin')
    diagnostic = resolver.diagnose(frozenset({'GITHUB_READ_TOKEN'}))
    assert diagnostic.results['GITHUB_READ_TOKEN'].status == 'error'


def test_diagnostic_results_and_ready_values_are_immutable():
    resolver = CredentialResolver({}, environ={'NOTION_MCP_TOKEN': 'tok'})
    diagnostic = resolver.diagnose(frozenset({'NOTION_MCP_TOKEN'}))
    with pytest.raises(TypeError):
        diagnostic.results['NOTION_MCP_TOKEN'] = None
    with pytest.raises(TypeError):
        diagnostic._ready_values['NOTION_MCP_TOKEN'] = 'hack'


def test_require_raises_naming_not_ready_names():
    resolver = CredentialResolver({}, environ={'NOTION_MCP_TOKEN': 'tok'})
    diagnostic = resolver.diagnose(frozenset({'NOTION_MCP_TOKEN', 'GITHUB_READ_TOKEN'}))
    with pytest.raises(ConfigurationError):
        diagnostic.require(frozenset({'GITHUB_READ_TOKEN'}))
    snap = diagnostic.require(frozenset({'NOTION_MCP_TOKEN'}))
    assert snap['NOTION_MCP_TOKEN'] == 'tok'


def test_require_performs_no_new_keyring_read(monkeypatch):
    service, account = KEYRING_BINDINGS['NOTION_MCP_TOKEN']
    counter = _install_fake_macos_keyring(
        monkeypatch, password_by_account={(service, account): 'kr-tok'})
    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    diagnostic = resolver.diagnose(frozenset({'NOTION_MCP_TOKEN'}))
    assert counter == [(service, account)]
    diagnostic.require(frozenset({'NOTION_MCP_TOKEN'}))
    diagnostic.select(required=frozenset({'NOTION_MCP_TOKEN'}))
    assert counter == [(service, account)], 'require()/select() must not read the backend again'


def test_select_mixed_required_and_optional_with_default(tmp_path):
    creds_file = tmp_path / 'mcp.json'
    from uls.config._secure_file import write_secure_file
    write_secure_file(creds_file, b'{}')
    resolver = CredentialResolver({}, environ={'GOOGLE_MCP_CREDENTIALS_FILE': str(creds_file),
                                               'NOTION_MCP_TOKEN': 'mcp-tok'})
    diagnostic = resolver.diagnose(frozenset({
        'GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN',
        'NOTION_WORKER_TOKEN', 'GOOGLE_WORKER_CREDENTIALS_FILE',
    }))
    snap = diagnostic.select(
        required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'}),
        optional={'NOTION_WORKER_TOKEN': '', 'GOOGLE_WORKER_CREDENTIALS_FILE': ''},
    )
    assert snap['GOOGLE_MCP_CREDENTIALS_FILE'] == str(creds_file)
    assert snap['NOTION_MCP_TOKEN'] == 'mcp-tok'
    assert snap['NOTION_WORKER_TOKEN'] == ''
    assert snap['GOOGLE_WORKER_CREDENTIALS_FILE'] == ''


def test_snapshot_from_earlier_resolve_unaffected_by_later_backend_mutation(monkeypatch):
    """TOCTOU regression guard: build one snapshot, then mutate the backend,
    then confirm the already-returned snapshot's values are unchanged."""
    service, account = KEYRING_BINDINGS['NOTION_MCP_TOKEN']
    store = {(service, account): 'first-value'}
    counter = []

    class MutableFakeKeyring:
        __module__ = 'keyring.backends.macOS'

        def __init__(self):
            self.keychain = None

        def get_password(self, svc, acct):
            counter.append((svc, acct))
            return store.get((svc, acct))

    module = types.ModuleType('keyring.backends.macOS')
    module.Keyring = MutableFakeKeyring
    monkeypatch.setitem(__import__('sys').modules, 'keyring.backends.macOS', module)

    resolver = CredentialResolver({'NOTION_MCP_TOKEN': 'keyring'}, environ={}, platform='darwin')
    snap = resolver.resolve(required=frozenset({'NOTION_MCP_TOKEN'}))
    assert snap['NOTION_MCP_TOKEN'] == 'first-value'

    store[(service, account)] = 'second-value'
    assert snap['NOTION_MCP_TOKEN'] == 'first-value', 'earlier snapshot must not observe the later value'
