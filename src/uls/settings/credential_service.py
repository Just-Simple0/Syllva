"""Credential transactions over the sealed journal and per-user admission."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from uls.config._secure_file import is_reserved_secret_locator, read_secure_file
from uls.config.google_oauth import (
    AUTHORIZED_USER_TYPE,
    SERVICE_ACCOUNT_TYPE,
    AuthorizedUserCredential,
    GoogleOAuthClient,
    GoogleOAuthCredentialError,
    GoogleOAuthPurpose,
    config_file_is_private,
    credential_type,
    credential_type_of_bytes,
    exact_scopes,
    parse_authorized_user_bytes,
    parse_google_oauth_section,
)
from uls.config.mutation import ConfigFileLock, atomic_replace_config, read_config_bytes

from .config_service import ConfigStore, SettingsServiceError
from .credential_admission import (
    CredentialPairAdmission,
    credential_admission,
    credential_pair_admission,
    credential_pair_recovery_admission,
    credential_recovery_admission,
)
from .credential_roles import ROLES, CredentialRole, role_from_binding
from .credential_stores import CredentialStores, StoreResolver
from .journal import (
    ABSENT_STATE,
    TERMINAL_PHASES,
    JournalStore,
    enrollment_recovery_action,
    recovery_choices,
    replacement_recovery_action,
)
from .provider_checks import (
    ProviderChecks,
    failure,
    structural_credential,
    structural_oauth_credential,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _google_identity(payload: bytes) -> tuple[str, ...]:
    """Type-tagged, value-derived identity used only for peer separation."""

    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError, UnicodeError):
        raise failure("INVALID_CREDENTIAL") from None
    if not isinstance(parsed, dict):
        raise failure("INVALID_CREDENTIAL")
    kind = credential_type(parsed)
    if kind == AUTHORIZED_USER_TYPE:
        token = parsed.get("refresh_token")
        if not isinstance(token, str) or not token.strip():
            raise failure("INVALID_CREDENTIAL")
        return (AUTHORIZED_USER_TYPE, token)
    if kind != SERVICE_ACCOUNT_TYPE:
        # Exact dispatch: an unrecognized type is never treated as a service account.
        raise failure("INVALID_CREDENTIAL")
    values = tuple(parsed.get(key) for key in ("client_email", "private_key_id"))
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise failure("INVALID_CREDENTIAL")
    return (SERVICE_ACCOUNT_TYPE, *cast(tuple[str, str], values))


class CredentialService:
    def __init__(self, config: ConfigStore, journal: JournalStore, stores: CredentialStores,
                 checks: ProviderChecks, *, platform: str | None = None,
                 environ: Mapping[str, str] | None = None, google_loader: Callable[..., Any] | None = None) -> None:
        self.config, self.journal, self.stores, self.checks = config, journal, stores, checks
        self.platform = platform or sys.platform
        self.environ = environ if environ is not None else os.environ
        self.google_loader = google_loader
        self.resolver = StoreResolver(stores)
        journal.credential_root = stores.root
        self.canvas_verifier: Callable[[CredentialRole, bytes, Any], Any] | None = None
        # Personal OAuth fresh-grant verifier: (credential) -> (granted scopes,
        # account permission ID). Injected by composition; fake mode supplies a
        # provider-free one. Never persisted, never logged.
        self.oauth_verifier: Callable[[AuthorizedUserCredential], tuple[Any, str]] | None = None

    def _require_platform(self) -> None:
        if self.platform != "darwin":
            raise SettingsServiceError("unsupported_platform", "Syllva cannot store credentials securely on this computer yet.", 403)

    def source(self, role: CredentialRole, raw: dict[str, Any]) -> tuple[str, str | None]:
        if role.provider == "canvas":
            return "keyring", None
        if role.provider == "notion":
            source = raw.get("credentials", {}).get(role.name, {}).get("source", "environment")
            return source, None
        nested = raw.get("google_drive", raw.get("drive", {}))
        path = raw.get(f"google_{role.purpose}_credentials_path") or nested.get(f"{role.purpose}_credentials_path")
        value = path or self.environ.get(role.name)
        if not path:
            return "environment", value
        configured_path = Path(path).expanduser()
        if not configured_path.is_absolute():
            configured_path = self.config.path.parent / configured_path
        managed_path = Path(role.locator(self.stores.root)).resolve()
        source = "file" if configured_path.resolve() == managed_path else "external_file"
        return source, value

    def _storage_label(self, role: CredentialRole, source: str) -> str:
        if source == "external_file":
            return "the configured external credential file"
        if source == "environment":
            return "the Settings process environment"
        backend_label = getattr(self.stores.backend, "storage_label", None)
        if source in {"keyring", "file"} and isinstance(backend_label, str) and backend_label:
            return backend_label
        if source == "keyring" and role.service and self.platform == "darwin":
            return "this Mac's Keychain"
        if source == "file":
            return "this computer's protected secrets folder"
        return "the local credential store"

    def effective(self, role: CredentialRole, raw: dict[str, Any]) -> bytes | None:
        source, path = self.source(role, raw)
        if role.provider == "google":
            if not path:
                return None
            expanded = Path(path).expanduser()
            if not expanded.is_absolute():
                expanded = self.config.path.parent / expanded
            if is_reserved_secret_locator(expanded, root=self.stores.root):
                raise failure("INVALID_CREDENTIAL")
            return read_secure_file(expanded, max_bytes=65536)
        if source != "environment":
            return self.stores.read(role)
        value = self.environ.get(role.name)
        return value.encode() if value else None

    def _separate(self, role: CredentialRole, value: bytes, raw: dict[str, Any]) -> None:
        if role.provider == "canvas":
            return
        peer = ROLES[f"{role.provider}-{'worker' if role.purpose == 'mcp' else 'mcp'}"]
        credentials = raw.get("credentials", {})
        notion_entry = credentials.get(peer.name) if isinstance(credentials, dict) else None
        declared_notion_peer = role.provider == "notion" and notion_entry is not None

        try:
            source, _path = self.source(peer, raw)
            if source == "environment" and peer.name not in self.environ:
                if declared_notion_peer:
                    raise failure("INVALID_CREDENTIAL")
                effective_peer = None
            elif source == "environment" and not self.environ.get(peer.name):
                raise failure("INVALID_CREDENTIAL")
            else:
                effective_peer = self.effective(peer, raw)
                if effective_peer is None or not effective_peer:
                    raise failure("INVALID_CREDENTIAL")
                if role.provider == "notion":
                    effective_peer.decode("utf-8", errors="strict")
        except SettingsServiceError:
            raise
        except UnicodeDecodeError:
            raise failure("INVALID_CREDENTIAL") from None
        except Exception:  # noqa: BLE001 - peer read failures must fail closed without details
            raise failure("INVALID_CREDENTIAL") from None

        try:
            managed_peer = self.stores.read(peer)
            if role.provider == "notion" and managed_peer is not None:
                managed_peer.decode("utf-8", errors="strict")
        except Exception:  # noqa: BLE001 - managed peer read failures must fail closed without details
            raise failure("INVALID_CREDENTIAL") from None
        if managed_peer == b"":
            raise failure("INVALID_CREDENTIAL")

        peers = [item for item in (effective_peer, managed_peer) if item is not None]
        if role.provider == "notion":
            conflict = any(hmac.compare_digest(value, old) for old in peers)
        else:
            incoming_identity = _google_identity(value)
            old_identities = [_google_identity(old) for old in peers]
            # Exact type dispatch (P2 plan §3): a service-account target next to
            # a personal-OAuth peer, or the reverse, is a pre-journal conflict.
            if any(old[0] != incoming_identity[0] for old in old_identities):
                raise failure("CREDENTIAL_TYPE_CONFLICT")
            conflict = any(any(incoming == existing for incoming, existing in zip(incoming_identity[1:], old_identity[1:]))
                           for old_identity in old_identities)
        if conflict:
            raise SettingsServiceError("CREDENTIAL_PURPOSE_CONFLICT", "Retrieval and worker must use different credentials.")

    def _admission_context(self, role: CredentialRole, *, recovery_id: str | None = None,
                           fault_hook: Callable[[str], None] | None = None) -> Any:
        if role.provider in {"notion", "google"}:
            peer = ROLES[f"{role.provider}-{'worker' if role.purpose == 'mcp' else 'mcp'}"]
            locators = [role.locator(self.stores.root), peer.locator(self.stores.root)]
            if recovery_id is not None:
                return credential_pair_recovery_admission(
                    self.stores.root, role.provider, role.profile, locators,
                    operation_id=recovery_id, journal=self.journal, config_path=self.config.path,
                    config_dir_id=self.config.binding()["config_dir_id"],
                )
            return credential_pair_admission(
                self.stores.root, role.provider, role.profile, locators,
                journal=self.journal, config_path=self.config.path, fault_hook=fault_hook,
            )
        if recovery_id is not None:
            return credential_recovery_admission(
                self.stores.root, role.locator(self.stores.root), operation_id=recovery_id,
                journal=self.journal, config_path=self.config.path,
                config_dir_id=self.config.binding()["config_dir_id"],
            )
        return credential_admission(self.stores.root, role.locator(self.stores.root), journal=self.journal,
                                    config_path=self.config.path, fault_hook=fault_hook)

    @staticmethod
    def _publish_admission(role: CredentialRole, admission: Any, operation_id: str,
                           binding: dict[str, Any]) -> None:
        if role.provider in {"notion", "google"}:
            admission.publish(operation_id, binding)
        else:
            admission.publish(operation_id, binding["config_dir_id"])

    @staticmethod
    def _oauth_client(raw: dict[str, Any]) -> GoogleOAuthClient | None:
        try:
            return parse_google_oauth_section(raw.get("google_oauth"))
        except ValueError:
            return None

    def _verify(self, role: CredentialRole, value: bytes, raw: dict[str, Any]) -> None:
        if role.provider == "google" and credential_type_of_bytes(value) == AUTHORIZED_USER_TYPE:
            # Personal OAuth bytes are never re-interpreted by the service-
            # account parser, and their proof is the fresh grant/account check
            # performed before the journal; no Drive resource check runs here.
            structural_oauth_credential(role, value, self._oauth_client(raw))
            self._separate(role, value, raw)
            return
        structural_credential(role, value, google_loader=self.google_loader)
        self._separate(role, value, raw)
        if role.provider == "canvas":
            if self.canvas_verifier is None:
                raise failure("NOT_CONFIGURED")
            self.canvas_verifier(role, value, raw)
        else:
            self.checks.check(role, value, self.config._parse(yaml.safe_dump(raw).encode()).config)

    def cards(self) -> dict[str, Any]:
        loaded = self.config.load()
        cards = []
        pending = self.journal.unresolved()
        for role in ROLES.values():
            source, _path = self.source(role, loaded.raw)
            managed = (source in {"keyring", "file"})
            kind: str | None = None
            try:
                effective_value = self.effective(role, loaded.raw)
                present = effective_value is not None
                state = ("external" if present and source in {"environment", "external_file"}
                         else "configured" if present else "not_configured")
                if role.provider == "google" and present:
                    kind = credential_type_of_bytes(effective_value)
            except Exception:  # noqa: BLE001 - return metadata only
                present, state = False, "error"
            operation = next((item for item in pending if role.role_key in item["role_keys"]), None)
            if operation:
                card_state = "partial"
            elif state == "external" or self.platform == "darwin":
                card_state = state
            else:
                card_state = "unsupported_platform"
            cards.append({"role": role.slug, "provider": role.provider, "purpose": role.purpose,
                          "state": card_state, "credential_type": kind,
                          "source": source, "managed": managed, "can_test": present,
                          "can_mutate": self.platform == "darwin" and not operation,
                          "can_detach": source == "external_file", "environment_variable": role.name,
                          "storage_label": self._storage_label(role, source),
                          "last_check": self.checks.results.get(role.slug), "pending_operation": operation,
                          "takes_effect": "MCP restart" if role.purpose == "mcp" else "worker restart"})
        return {"cards": cards, "config_generation": loaded.generation,
                # Presence only: the configured personal Desktop client plus an
                # owner-only config file. Never the client values (P2 plan §2).
                "google_oauth_ready": self._oauth_client(loaded.raw) is not None
                and config_file_is_private(self.config.path)}

    def test(self, role: CredentialRole) -> dict[str, Any]:
        loaded = self.config.load()
        value = self.effective(role, loaded.raw)
        if value is None:
            raise failure("NOT_CONFIGURED")
        if role.provider == "google" and credential_type_of_bytes(value) is None:
            # Unrecognized stored type: refused before any loader or provider call.
            exc = failure("INVALID_CREDENTIAL")
            exc.code_only = True  # type: ignore[attr-defined]
            raise exc
        if role.provider == "google" and credential_type_of_bytes(value) == AUTHORIZED_USER_TYPE:
            # Exact client/purpose/scope contract before any provider read;
            # service-account bytes keep the existing path untouched. Errors
            # from this same snapshot are marked so the HTTP layer answers
            # with the fixed code only (plan §2).
            try:
                structural_oauth_credential(role, value, self._oauth_client(loaded.raw))
                return self.checks.check(role, value, loaded.config)
            except SettingsServiceError as exc:
                exc.code_only = True  # type: ignore[attr-defined]
                raise
        return self.checks.check(role, value, loaded.config)

    def _patch(self, role: CredentialRole, raw: dict[str, Any], *, detach: bool = False,
               candidate: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
        revision = secrets.token_hex(16)
        result = candidate if candidate is not None else role.candidate(raw, self.stores.root, revision, detach=detach)
        result.setdefault("credential_revisions", {})[role.slug] = revision
        keys = {"credential_revisions", "canvas"} if role.provider == "canvas" else {
            "credential_revisions", "credentials", "google_drive", f"google_{role.purpose}_credentials_path"}
        patch = {key: result.get(key) for key in keys}
        return result, json.dumps(patch, sort_keys=True)

    def _candidate_bytes(self, raw: dict[str, Any], patch: str) -> bytes:
        result = dict(raw)
        for key, value in json.loads(patch).items():
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
        data = cast(str, yaml.safe_dump(result, sort_keys=False, allow_unicode=True)).encode()
        self.config._parse(data)
        return data

    def save(self, role: CredentialRole, value: bytes, generation: str, *, replace: bool = False,
             candidate: dict[str, Any] | None = None, fault_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
        self._require_platform()
        structural_credential(role, value, google_loader=self.google_loader)
        with self._admission_context(role, fault_hook=fault_hook) as admission, self.journal.role_locks([role.role_key]) as roles:
            with ConfigFileLock(self.config.path):
                loaded = self.config._parse(read_config_bytes(self.config.path))
                if generation != loaded.generation:
                    raise failure("CONFIGURATION_CHANGED")
                self._separate(role, value, loaded.raw)
                if replace and (not self.source(role, loaded.raw)[0] in {"keyring", "file"} or self.stores.read(role) is None):
                    raise failure("NOT_CONFIGURED")
                if role.provider == "google" and credential_type_of_bytes(self.stores.read(role)) == AUTHORIZED_USER_TYPE:
                    # Service-account bytes never replace a personal OAuth grant in place.
                    raise failure("CREDENTIAL_TYPE_CONFLICT")
                old = self.stores.state(role)
                kind = "credential_enrollment" if old == "absent" else "credential_replacement"
                _raw, patch = self._patch(role, loaded.raw, candidate=candidate)
                payload = self._candidate_bytes(loaded.raw, patch)
                binding = {**self.config.binding(), "provider": role.provider, "profile": role.profile,
                           "role": role.purpose, "store_locator": role.locator(self.stores.root),
                           "staging_locator": role.locator(self.stores.root, "staged"), "config_patch": patch}
                if kind == "credential_replacement":
                    binding.update(backup_locator=role.locator(self.stores.root, "backup"), original_active_id=old)
                if self.stores.read(role, "staged") is not None or self.stores.read(role, "backup") is not None:
                    raise failure("OPERATION_IN_PROGRESS")
                operation_id = secrets.token_hex(16)
                self._publish_admission(role, admission, operation_id, binding)
                self.journal.create_operation(action_kind=kind, binding=binding, original_generation=loaded.generation,
                    candidate_hash=_digest(payload), fields=[role.slug], role_locks=roles, allow_unreleased=True, operation_id=operation_id)
            with self.journal.operation(operation_id) as op:
                self._effect(op, roles, role, "credential_stage", "absent", self.stores.value_id(role, value),
                             lambda: self.stores.write(role, value, "staged"), fault_hook)
                try:
                    self._verify(role, value, _raw if role.provider == "canvas" else loaded.raw)
                except SettingsServiceError:
                    with ConfigFileLock(self.config.path) as lock:
                        op.switch_branch("abandon" if kind == "credential_enrollment" else "reject", role_locks=roles,
                            config_lock=lock, resolver=self.resolver, observe={}, next_action="cleanup")
                        self._effect(op, roles, role, "staged_delete", self.stores.value_id(role, value), "absent",
                                     lambda: self.stores.delete(role, "staged"), fault_hook, lock)
                        op.update(phase="complete", next_action="none")
                    admission.release(operation_id)
                    raise
                self._continue(op, roles, role, payload, fault_hook)
            admission.release(operation_id)
        return {"status": "complete", "code": "VERIFIED", "config_generation": self.config.load().generation}

    def _oauth_snapshot(self, role: CredentialRole, candidate: AuthorizedUserCredential, loaded: Any,
                        generation: str, replace: bool) -> dict[str, Any]:
        """Config/client/generation/peer snapshot taken under the config lock (plan §3 step 2)."""

        if generation != loaded.generation:
            raise failure("CONFIGURATION_CHANGED")
        client = self._oauth_client(loaded.raw)
        if client is None or not config_file_is_private(self.config.path):
            raise failure("OAUTH_APP_NOT_READY")
        if client.client_id != candidate.client_id or client.client_secret != candidate.client_secret:
            raise failure("OAUTH_CLIENT_MISMATCH")
        source, path = self.source(role, loaded.raw)
        if source == "external_file" or (source == "environment" and path):
            raise failure("CREDENTIAL_SOURCE_EXTERNAL")
        managed = self.stores.read(role)
        if replace and managed is None:
            raise failure("NOT_CONFIGURED")
        if not replace and managed is not None:
            raise failure("REPLACE_REQUIRED")
        if managed is not None and credential_type_of_bytes(managed) != AUTHORIZED_USER_TYPE:
            # A type switch on the same role goes through forget first (plan §3).
            raise failure("CREDENTIAL_TYPE_CONFLICT")
        peer = ROLES[f"google-{'worker' if role.purpose == 'mcp' else 'mcp'}"]
        peer_source, peer_path = self.source(peer, loaded.raw)
        if peer_source == "external_file" or (peer_source == "environment" and peer_path):
            # An external/environment peer is outside the managed-store CAS and
            # could drift during the provider proof; detach it first.
            raise failure("CREDENTIAL_SOURCE_EXTERNAL")
        peer_credential: AuthorizedUserCredential | None = None
        try:
            peer_value = self.effective(peer, loaded.raw)
            if peer_value is None:
                # An unselected credential left in the protected store is still
                # part of the pair: it joins the fresh account proof.
                peer_value = self.stores.read(peer)
        except SettingsServiceError:
            raise
        except Exception:  # noqa: BLE001 - peer read failures fail closed without details
            raise failure("INVALID_CREDENTIAL") from None
        peer_kind = credential_type_of_bytes(peer_value)
        if peer_value is not None:
            if peer_kind == SERVICE_ACCOUNT_TYPE:
                raise failure("CREDENTIAL_TYPE_CONFLICT")
            if peer_kind != AUTHORIZED_USER_TYPE:
                raise failure("INVALID_CREDENTIAL")
            try:
                peer_credential = parse_authorized_user_bytes(
                    peer_value, purpose=GoogleOAuthPurpose.parse(peer.purpose), client=client)
            except GoogleOAuthCredentialError:
                raise failure("RECONNECT_REQUIRED") from None
        return {
            "generation": loaded.generation, "client_id": client.client_id,
            "target_source": source, "target_state": self.stores.state(role),
            "peer_source": peer_source, "peer_state": self.stores.state(peer), "peer_kind": peer_kind,
            "peer_credential": peer_credential,
        }

    def _fresh_oauth_verification(self, candidate: AuthorizedUserCredential, fresh_permission_id: str,
                                  snapshot: dict[str, Any]) -> None:
        """Provider HTTP outside the config lock (plan §3 step 3)."""

        if self.oauth_verifier is None:
            raise failure("NOT_CONFIGURED")
        if not isinstance(fresh_permission_id, str) or not fresh_permission_id:
            raise failure("ACCOUNT_MISMATCH")
        try:
            granted, permission_id = self.oauth_verifier(candidate)
        except SettingsServiceError:
            raise
        except Exception:  # noqa: BLE001 - provider diagnostics may carry tokens
            raise failure("RECONNECT_REQUIRED") from None
        if not exact_scopes(granted, candidate.purpose):
            raise failure("OAUTH_GRANT_MISMATCH")
        if not isinstance(permission_id, str) or not hmac.compare_digest(permission_id, fresh_permission_id):
            raise failure("ACCOUNT_MISMATCH")
        peer_credential = snapshot.get("peer_credential")
        if peer_credential is not None:
            try:
                peer_granted, peer_permission_id = self.oauth_verifier(peer_credential)
            except SettingsServiceError:
                raise
            except Exception:  # noqa: BLE001 - provider diagnostics may carry tokens
                raise failure("RECONNECT_REQUIRED") from None
            if not exact_scopes(peer_granted, peer_credential.purpose):
                raise failure("OAUTH_GRANT_MISMATCH")
            if not isinstance(peer_permission_id, str) or not hmac.compare_digest(peer_permission_id, fresh_permission_id):
                raise failure("ACCOUNT_MISMATCH")

    def save_google_oauth(self, role: CredentialRole, candidate: AuthorizedUserCredential, generation: str, *,
                          replace: bool, fresh_permission_id: str,
                          fault_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
        """One personal-OAuth commit: admission -> snapshot -> fresh proof -> CAS -> journal (plan §3)."""

        self._require_platform()
        if role.provider != "google" or GoogleOAuthPurpose.parse(role.purpose) is not candidate.purpose:
            raise failure("INVALID_CREDENTIAL")
        value = candidate.to_canonical_json()
        if not 0 < len(value) <= role.max_bytes:
            raise failure("INVALID_CREDENTIAL")
        with self._admission_context(role, fault_hook=fault_hook) as admission, self.journal.role_locks([role.role_key]) as roles:
            with ConfigFileLock(self.config.path):
                loaded = self.config._parse(read_config_bytes(self.config.path))
                snapshot = self._oauth_snapshot(role, candidate, loaded, generation, replace)
            # Provider HTTP happens here with pair/physical/role locks held and
            # the config lock released; nothing durable has changed yet.
            self._fresh_oauth_verification(candidate, fresh_permission_id, snapshot)
            with ConfigFileLock(self.config.path):
                loaded = self.config._parse(read_config_bytes(self.config.path))
                current = self._oauth_snapshot(role, candidate, loaded, generation, replace)
                comparable = {key: value_ for key, value_ in snapshot.items() if key != "peer_credential"}
                if {key: value_ for key, value_ in current.items() if key != "peer_credential"} != comparable:
                    raise failure("CONFIGURATION_CHANGED")
                # Every policy rejection (structure, peer separation) happens
                # here, before admission publish, journal or staging.
                self._verify(role, value, loaded.raw)
                old = self.stores.state(role)
                kind = "credential_enrollment" if old == "absent" else "credential_replacement"
                _raw, patch = self._patch(role, loaded.raw)
                payload = self._candidate_bytes(loaded.raw, patch)
                binding = {**self.config.binding(), "provider": role.provider, "profile": role.profile,
                           "role": role.purpose, "store_locator": role.locator(self.stores.root),
                           "staging_locator": role.locator(self.stores.root, "staged"), "config_patch": patch}
                if kind == "credential_replacement":
                    binding.update(backup_locator=role.locator(self.stores.root, "backup"), original_active_id=old)
                if self.stores.read(role, "staged") is not None or self.stores.read(role, "backup") is not None:
                    raise failure("OPERATION_IN_PROGRESS")
                operation_id = secrets.token_hex(16)
                self._publish_admission(role, admission, operation_id, binding)
                self.journal.create_operation(action_kind=kind, binding=binding, original_generation=loaded.generation,
                    candidate_hash=_digest(payload), fields=[role.slug], role_locks=roles, allow_unreleased=True, operation_id=operation_id)
            with self.journal.operation(operation_id) as op:
                self._effect(op, roles, role, "credential_stage", "absent", self.stores.value_id(role, value),
                             lambda: self.stores.write(role, value, "staged"), fault_hook)
                try:
                    self._verify(role, value, loaded.raw)
                except SettingsServiceError:
                    with ConfigFileLock(self.config.path) as lock:
                        op.switch_branch("abandon" if kind == "credential_enrollment" else "reject", role_locks=roles,
                            config_lock=lock, resolver=self.resolver, observe={}, next_action="cleanup")
                        self._effect(op, roles, role, "staged_delete", self.stores.value_id(role, value), "absent",
                                     lambda: self.stores.delete(role, "staged"), fault_hook, lock)
                        op.update(phase="complete", next_action="none")
                    admission.release(operation_id)
                    raise
                self._continue(op, roles, role, payload, fault_hook)
            admission.release(operation_id)
        return {"status": "complete", "code": "VERIFIED", "config_generation": self.config.load().generation}

    def _effect(self, op: Any, roles: Any, role: CredentialRole, name: str, pre: str, post: str,
                perform: Callable[[], None], fault: Callable[[str], None] | None, lock: Any = None) -> None:
        existing = op.read()["effects"].get(name)
        if existing and existing["status"] in {"verified", "verified_by_recovery"}:
            return
        if existing:
            pre, post = existing["pre_state_id"], existing["intended_post_state_id"]
        op.run_effect(name, pre_state=pre, intended_post_state=post, perform=perform, observe=lambda: "unused",
                      resolver=self.resolver, role_locks=roles, config_lock=lock, fault_hook=fault)

    def _continue(self, op: Any, roles: Any, role: CredentialRole, payload: bytes | None,
                  fault: Callable[[str], None] | None = None) -> None:
        with ConfigFileLock(self.config.path) as lock:
            record = op.read()
            generation = self.resolver.observe(record, "config")
            expected = record["original_generation"] if record["branch"] != "primary" else record["candidate_hash"]
            if generation not in {record["original_generation"], expected}:
                raise failure("MANUAL_REVIEW")
            if record["branch"] == "primary" and role.provider in {"notion", "google"} and record["action_kind"] in {"credential_enrollment", "credential_replacement"}:
                staged = self.stores.read(role, "staged")
                if staged is not None:
                    self._separate(role, staged, self.config._parse(read_config_bytes(self.config.path)).raw)
            for name in record["planned_effects"]:
                if name == "credential_stage":
                    if op.read()["effects"].get(name, {}).get("status") not in {"verified", "verified_by_recovery"}:
                        staged = self.stores.read(role, "staged")
                        if staged is None:
                            raise failure("MANUAL_REVIEW")
                        effect = op.read()["effects"][name]
                        self._effect(op, roles, role, name, effect["pre_state_id"], effect["intended_post_state_id"], lambda: None, fault, lock)
                    continue
                record = op.read()
                existing = record["effects"].get(name)
                if existing and existing["status"] in {"verified", "verified_by_recovery"}:
                    continue
                self._check_store_evidence(record, except_store=self.resolver.effect_store(name))
                if name in {"config_commit", "config_detach"}:
                    if payload is None and self.resolver.observe(record, "config") != record["candidate_hash"]:
                        raise failure("MANUAL_REVIEW")
                    pre, post = record["original_generation"], record["candidate_hash"]
                    perform = lambda: atomic_replace_config(self.config.path, payload or b"")
                elif name == "credential_backup":
                    pre, post = "absent", self.stores.state(role)
                    perform = lambda: self.stores.write(role, self.stores.read(role) or b"", "backup")
                elif name in {"credential_promote", "credential_restore"}:
                    slot = "staged" if name == "credential_promote" else "backup"
                    pre, post = self.stores.state(role), self.stores.state(role, slot)
                    perform = partial(self.stores.write, role, self.stores.read(role, slot) or b"")
                else:
                    slot = {"backup_delete": "backup", "credential_delete": "active",
                            "staged_delete": "staged", "staging_cleanup": "staged"}[name]
                    pre, post = self.stores.state(role, slot), "absent"
                    if name == "credential_delete":
                        self._ensure_not_selected(role, self.config._parse(read_config_bytes(self.config.path)).raw)
                    perform = partial(self.stores.delete, role, slot)
                self._effect(op, roles, role, name, pre, post, perform, fault, lock)
            self._check_store_evidence(op.read())
            op.update(phase="complete", next_action="none")

    def _require_live_recovery(self, record: dict[str, Any], config_generation: str, action: str) -> None:
        """Recheck recorded live state under admission, role, journal and config locks."""

        if config_generation not in {record["original_generation"], record["candidate_hash"]}:
            raise failure("MANUAL_REVIEW")
        latest: dict[str, dict[str, Any]] = {}
        try:
            for name in record["planned_effects"]:
                effect = record["effects"].get(name)
                if effect is not None:
                    latest[self.resolver.effect_store(name)] = effect
            for store, effect in latest.items():
                actual = self.resolver.observe(record, store)
                status = effect["status"]
                if status in {"verified", "verified_by_recovery"}:
                    expected = {effect["post_state_id"]}
                elif status == "intent":
                    expected = {effect["pre_state_id"], effect["intended_post_state_id"]}
                elif status == "verified_not_applied":
                    expected = {effect["pre_state_id"]}
                else:
                    raise failure("MANUAL_REVIEW")
                if actual not in expected:
                    raise failure("MANUAL_REVIEW")
        except (KeyError, OSError, TypeError, ValueError):
            raise failure("MANUAL_REVIEW") from None

        kind = record["action_kind"]
        role = role_from_binding(record["binding"])
        if kind == "credential_enrollment":
            stage = record["effects"].get("credential_stage")
            if stage is not None and stage["status"] == "intent":
                if (record["branch"] != "primary" or config_generation != record["original_generation"]
                        or self.stores.state(role, "staged") != stage["intended_post_state_id"]
                        or self.stores.state(role) != ABSENT_STATE
                        or "credential_promote" in record["effects"]):
                    raise failure("MANUAL_REVIEW")
                decision = "stage_intent"
            else:
                decision = enrollment_recovery_action(
                    record, config_generation=config_generation,
                    staged_state=self.stores.state(role, "staged"), active_state=self.stores.state(role),
                )
                if (decision == "manual_review" and record["branch"] == "abandon"
                        and record["effects"].get("staged_delete", {}).get("status") in {"verified", "verified_by_recovery"}
                        and self.stores.state(role, "staged") == ABSENT_STATE
                        and self.stores.state(role) == ABSENT_STATE
                        and config_generation == record["original_generation"]):
                    decision = "continue_abandon"
            leave_decisions = {"abandon"} if record["branch"] == "primary" else {"continue_abandon"}
            if decision == "manual_review" or (action == "leave" and decision not in leave_decisions):
                raise failure("MANUAL_REVIEW")
        elif kind == "credential_replacement":
            active_state = self.stores.state(role)
            backup_state = self.stores.state(role, "backup")
            decision = replacement_recovery_action(
                record, active_state=active_state, config_generation=config_generation,
                backup_state=backup_state,
            )
            if (decision == "manual_review" and record["branch"] == "restore"
                    and record["effects"].get("credential_restore", {}).get("status") in {"verified", "verified_by_recovery"}
                    and record["effects"].get("backup_delete", {}).get("status") in {"verified", "verified_by_recovery"}
                    and backup_state == ABSENT_STATE
                    and active_state == record["effects"]["credential_restore"]["post_state_id"]
                    and config_generation == record["original_generation"]):
                decision = "continue_restore"
            if (record["branch"] in {"primary", "reject"}
                    and "credential_promote" not in record["effects"]
                    and active_state != record["binding"]["original_active_id"]):
                raise failure("MANUAL_REVIEW")
            if "credential_backup" not in record["effects"] and backup_state != ABSENT_STATE:
                raise failure("MANUAL_REVIEW")
            restore_decisions = {"restore_backup"} if record["branch"] == "primary" else {"continue_restore"}
            if (decision == "manual_review"
                    or (action == "restore" and decision not in restore_decisions)
                    or (action == "leave" and decision != "resume_before_promotion")):
                raise failure("MANUAL_REVIEW")

    def _check_store_evidence(self, record: dict[str, Any], *, except_store: str | None = None) -> None:
        last: dict[str, Any] = {}
        for name in record["planned_effects"]:
            effect = record["effects"].get(name)
            if effect is not None:
                last[self.resolver.effect_store(name)] = effect
        for key, effect in last.items():
            if key == except_store:
                continue
            current = self.resolver.observe(record, key)
            expected = effect["post_state_id"] if effect["status"] in {"verified", "verified_by_recovery"} else effect["intended_post_state_id"]
            if current != expected:
                raise failure("MANUAL_REVIEW")

    def _ensure_not_selected(self, role: CredentialRole, candidate: dict[str, Any]) -> None:
        if role.provider != "google":
            return
        target = Path(role.locator(self.stores.root)).resolve()
        for peer in ROLES.values():
            if peer.provider != "google":
                continue
            _source, path = self.source(peer, candidate)
            if path:
                real = Path(path).expanduser()
                if not real.is_absolute():
                    real = self.config.path.parent / real
                if real.resolve() == target:
                    raise failure("CREDENTIAL_STILL_SELECTED")

    def forget(self, role: CredentialRole, generation: str, *, detach: bool = False,
               candidate: dict[str, Any] | None = None, fault_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
        self._require_platform()
        with self._admission_context(role, fault_hook=fault_hook) as admission, self.journal.role_locks([role.role_key]) as roles:
            with ConfigFileLock(self.config.path):
                loaded = self.config._parse(read_config_bytes(self.config.path))
                if loaded.generation != generation:
                    raise failure("CONFIGURATION_CHANGED")
                source, _path = self.source(role, loaded.raw)
                if (detach and source != "external_file") or (not detach and source not in {"file", "keyring"}):
                    raise failure("NOT_CONFIGURED")
                raw, patch = self._patch(role, loaded.raw, detach=True, candidate=candidate)
                if detach and self.source(role, raw)[1]:
                    raise failure("DETACH_INEFFECTIVE")
                if not detach:
                    self._ensure_not_selected(role, raw)
                payload = self._candidate_bytes(loaded.raw, patch)
                binding = {**self.config.binding(), "provider": role.provider, "profile": role.profile,
                           "role": role.purpose, "store_locator": role.locator(self.stores.root), "config_patch": patch}
                operation_id = secrets.token_hex(16)
                self._publish_admission(role, admission, operation_id, binding)
                self.journal.create_operation(action_kind="credential_detach" if detach else "credential_forget", binding=binding,
                    original_generation=loaded.generation, candidate_hash=_digest(payload), fields=[role.slug],
                    role_locks=roles, allow_unreleased=True, operation_id=operation_id)
            with self.journal.operation(operation_id) as op:
                self._continue(op, roles, role, payload, fault_hook)
            admission.release(operation_id)
        return {"status": "complete", "code": "DETACHED" if detach else "FORGOTTEN", "config_generation": self.config.load().generation}

    def recover(self, operation_id: str, action: str) -> dict[str, Any]:
        self._require_platform()
        if action not in {"resume", "restore", "leave", "retry_delete"}:
            raise failure("INVALID_REQUEST")
        record = self.journal.read(operation_id)
        if record["schema_version"] != 4 or {key: record["binding"][key] for key in self.config.binding()} != self.config.binding():
            raise failure("MANUAL_REVIEW")
        if record["phase"] not in TERMINAL_PHASES:
            choices = {choice["id"] for choice in recovery_choices(record)}
            if not choices:
                raise failure("MANUAL_REVIEW")
            if action not in choices:
                raise failure("MANUAL_REVIEW")
        role = role_from_binding(record["binding"])
        with self._admission_context(role, recovery_id=operation_id) as admission:
            with self.journal.role_locks([role.role_key]) as roles, self.journal.operation(operation_id) as op, ConfigFileLock(self.config.path) as lock:
                record = op.read()
                if record["phase"] in TERMINAL_PHASES:
                    current = self.config._parse(read_config_bytes(self.config.path))
                    if isinstance(admission, CredentialPairAdmission):
                        admission.commit_recovery_maintenance(
                            operation_id=operation_id, expected_record=record,
                            role_locks=roles, operation=op, config_lock=lock,
                        )
                    admission.release(operation_id)
                    return {"status": "complete", "config_generation": current.generation}
                choices = {choice["id"] for choice in recovery_choices(record)}
                if action not in choices:
                    raise failure("MANUAL_REVIEW")
                current = self.config._parse(read_config_bytes(self.config.path))
                if current.generation not in {record["original_generation"], record["candidate_hash"]}:
                    raise failure("MANUAL_REVIEW")
                payload = self._candidate_bytes(current.raw, record["binding"]["config_patch"]) if current.generation == record["original_generation"] else None
                if payload is not None and _digest(payload) != record["candidate_hash"]:
                    raise failure("MANUAL_REVIEW")
                self._require_live_recovery(record, current.generation, action)
                if action == "leave" and record["action_kind"] in {"credential_forget", "credential_detach"}:
                    return {"status": "pending", "code": "LEFT_AS_IS"}
                if isinstance(admission, CredentialPairAdmission):
                    admission.commit_recovery_maintenance(
                        operation_id=operation_id, expected_record=op.read(),
                        role_locks=roles, operation=op, config_lock=lock,
                    )
                if record["effects"].get("credential_stage", {}).get("status") == "intent":
                    staged = self.stores.read(role, "staged")
                    e = record["effects"]["credential_stage"]
                    if staged is None or self.stores.state(role, "staged") != e["intended_post_state_id"]:
                        raise failure("MANUAL_REVIEW")
                    self._effect(op, roles, role, "credential_stage", e["pre_state_id"], e["intended_post_state_id"], lambda: None, None, lock)
                    record = op.read()
                    self._require_live_recovery(record, current.generation, action)
                if record["branch"] == "primary" and action in {"restore", "leave"}:
                    if action == "restore":
                        branch = "restore"
                    elif record["action_kind"] == "credential_enrollment":
                        branch = "abandon"
                    elif record["action_kind"] == "credential_replacement":
                        branch = "reject"
                    else:
                        raise failure("MANUAL_REVIEW")
                    op.switch_branch(branch, role_locks=roles, config_lock=lock, resolver=self.resolver, observe={}, next_action="cleanup")
                    self._require_live_recovery(op.read(), current.generation, action)
                if op.read()["branch"] == "primary" and record["action_kind"] in {"credential_enrollment", "credential_replacement"}:
                    verify_raw = self.config._parse(payload).raw if payload is not None and role.provider == "canvas" else current.raw
                    staged = self.stores.read(role, "staged")
                    if staged is not None:
                        self._verify(role, staged, verify_raw)
                    elif "staging_cleanup" not in op.read()["effects"]:
                        raise failure("MANUAL_REVIEW")
            # _continue takes the config lock; role/record locks stay held.
            with self.journal.role_locks([role.role_key]) as roles, self.journal.operation(operation_id) as op:
                self._continue(op, roles, role, payload)
            admission.release(operation_id)
        return {"status": "complete", "config_generation": self.config.load().generation}
