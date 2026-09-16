"""Explicit, hardened OS-native keyring backend construction.

This module reimplements (does not import) the invariants used by
scripts/knu_lms_sync.py's _explicit_os_keyring() / _expected_backend_module().
The sidecar deliberately avoids importing the uls package so it stays
runnable as a bare script; this module serves the installed-package side
(CredentialResolver) with the same invariants instead of creating an import
dependency in either direction.

Invariant set (see docs/plans/credential-resolver.md, Blocker 5):
1. Platform gate: only darwin/win32 are supported.
2. Explicit backend class import -- never keyring.get_keyring().
3. Backend identity verification via __module__.
4. macOS alternate-keychain block: force keychain = None and re-verify.
5. A missing/empty get_password result is a failure, never an empty
   credential value.
"""

from __future__ import annotations

import sys
from typing import Any

from uls.config.errors import ConfigurationError


def expected_backend_module() -> str:
    """Return the required backend dotted path for the current OS, or raise."""

    if sys.platform == "darwin":
        return "keyring.backends.macOS.Keyring"
    if sys.platform == "win32":
        return "keyring.backends.Windows.WinVaultKeyring"
    raise ConfigurationError("keyring_platform_unsupported")


def explicit_os_keyring() -> Any:
    """Return a freshly constructed, verified OS-native keyring backend."""

    if sys.platform == "darwin":
        try:
            module = __import__("keyring.backends.macOS", fromlist=["Keyring"])
        except ImportError as exc:
            raise ConfigurationError("keyring_dependency_missing") from exc
        try:
            backend_type = module.Keyring
            backend = backend_type()
            backend.keychain = None
            if backend.__class__.__module__ != "keyring.backends.macOS" or backend.keychain is not None:
                raise ConfigurationError("keyring_backend_invalid")
            return backend
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError("keyring_backend_unavailable") from exc
    if sys.platform == "win32":
        try:
            module = __import__("keyring.backends.Windows", fromlist=["WinVaultKeyring"])
        except ImportError as exc:
            raise ConfigurationError("keyring_dependency_missing") from exc
        try:
            backend_type = module.WinVaultKeyring
            backend = backend_type()
            if backend.__class__.__module__ != "keyring.backends.Windows":
                raise ConfigurationError("keyring_backend_invalid")
            return backend
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError("keyring_backend_unavailable") from exc
    raise ConfigurationError("keyring_platform_unsupported")


def read_keyring_credential(service: str, account: str) -> str:
    """Read one credential value through the explicit, verified backend."""

    backend = explicit_os_keyring()
    if hasattr(backend, "keychain"):
        backend.keychain = None
        if backend.keychain is not None:
            raise ConfigurationError("keyring_backend_invalid")
    try:
        value = backend.get_password(service, account)
    except Exception as exc:
        raise ConfigurationError("keyring_read_failed") from exc
    if not isinstance(value, str) or not value:
        raise ConfigurationError("keyring_credential_missing")
    return value


__all__ = ["expected_backend_module", "explicit_os_keyring", "read_keyring_credential"]
