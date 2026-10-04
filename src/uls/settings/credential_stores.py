"""Shared protected-file/keyring adapters and slot-invariant private state IDs."""

from __future__ import annotations

import errno
import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Any

from uls.config._keyring_backend import (
    delete_keyring_credential,
    explicit_os_keyring,
    write_keyring_credential,
)
from uls.config._secure_file import delete_secure_file, read_secure_file, write_secure_file
from uls.config.errors import ConfigurationError
from uls.config.mutation import read_config_bytes
from uls.orchestration.locks import LocalFileLock

from .credential_roles import CredentialRole, role_from_binding, validate_locators


class FakeKeyring:
    """Explicit fake; never selects a system backend."""

    storage_label = "the fake test store"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[service, account] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


class CredentialStores:
    def __init__(self, root: Path, *, backend: Any = None) -> None:
        self.root = root.resolve()
        self.backend = backend

    def _backend(self) -> Any:
        if self.backend is None:
            self.backend = explicit_os_keyring()
        return self.backend

    def read(self, role: CredentialRole, slot: str = "active") -> bytes | None:
        if role.service:
            account = role.locator(self.root, slot).split("/", 1)[1]
            value = self._backend().get_password(role.service, account)
            return None if value is None else value.encode("utf-8")
        try:
            return read_secure_file(Path(role.locator(self.root, slot)), max_bytes=role.max_bytes)
        except ConfigurationError as exc:
            cause = exc.__cause__
            if (
                exc.details.get("problems") in (["secret_file_missing"], ["secret_dir_missing"])
                and isinstance(cause, FileNotFoundError)
                and cause.errno == errno.ENOENT
            ):
                return None
            raise

    def write(self, role: CredentialRole, value: bytes, slot: str = "active") -> None:
        if not 0 < len(value) <= role.max_bytes:
            raise ValueError("credential size is invalid")
        if role.service:
            account = role.locator(self.root, slot).split("/", 1)[1]
            write_keyring_credential(role.service, account, value.decode("utf-8"), backend=self._backend())
        else:
            write_secure_file(Path(role.locator(self.root, slot)), value, max_bytes=role.max_bytes)
        if self.read(role, slot) != value:
            raise ValueError("credential readback failed")

    def delete(self, role: CredentialRole, slot: str = "active") -> None:
        if role.service:
            account = role.locator(self.root, slot).split("/", 1)[1]
            delete_keyring_credential(role.service, account, backend=self._backend())
        else:
            delete_secure_file(Path(role.locator(self.root, slot)))
        if self.read(role, slot) is not None:
            raise ValueError("credential deletion readback failed")

    def state_key(self) -> bytes:
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock = LocalFileLock(self.root / "settings_state.key.lock")
        if not lock.acquire(timeout=5):
            raise TimeoutError("credential identity is busy")
        try:
            path = self.root / "settings_state.key"
            try:
                value = read_secure_file(path)
            except ConfigurationError as exc:
                if exc.details.get("problems") != ["secret_file_missing"]:
                    raise
                value = secrets.token_bytes(32)
                write_secure_file(path, value)
            if len(value) != 32:
                raise ValueError("credential identity key is invalid")
            return value
        finally:
            lock.release()

    def value_id(self, role: CredentialRole, value: bytes | None) -> str:
        if value is None:
            return "absent"
        return "h:" + hmac.new(self.state_key(), b"state\0" + role.slug.encode() + b"\0" + value, hashlib.sha256).hexdigest()

    def state(self, role: CredentialRole, slot: str = "active") -> str:
        return self.value_id(role, self.read(role, slot))


class StoreResolver:
    """Observers are derived from the immutable, validated record binding."""

    def __init__(self, stores: CredentialStores) -> None:
        self.stores = stores

    def observe(self, record: dict[str, Any], key: str) -> str:
        binding = record["binding"]
        validate_locators(binding, self.stores.root)
        if key == "config":
            return hashlib.sha256(read_config_bytes(binding["config_path"])).hexdigest()
        return self.stores.state(role_from_binding(binding), {"active": "active", "staged": "staged", "backup": "backup"}[key])

    def effect_store(self, name: str) -> str:
        return {
            "credential_stage": "staged", "staged_delete": "staged", "staging_cleanup": "staged",
            "credential_backup": "backup", "backup_delete": "backup", "config_commit": "config",
            "config_detach": "config", "credential_promote": "active", "credential_restore": "active",
            "credential_delete": "active",
        }[name]
