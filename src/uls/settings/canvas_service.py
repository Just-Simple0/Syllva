"""Canvas settings orchestration over the shared credential/config journals.

Only profile, term/course metadata and a renewable authorization lease are
written. A lease does not grant the deferred collector permission to run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from uls.config.errors import ConfigurationError
from uls.config.mutation import ConfigFileLock, atomic_replace_config, read_config_bytes

from .canvas_checks import (
    CanvasCheckError,
    CanvasDiscoveryGate,
    discover_canvas_courses,
    reread_canvas_courses,
    validate_canvas_origin,
    verify_canvas_user,
)
from .config_service import LoadedConfig, SettingsServiceError
from .credential_admission import credential_admission
from .credential_roles import CredentialRole, canvas_role
from .credential_service import CredentialService
from .journal import JournalError
from .provider_checks import failure, structural_credential

Verifier = Callable[[str, str], dict[str, str]]
Discoverer = Callable[[str, str], dict[str, Any]]
SelectionReader = Callable[[str, str, Iterable[str]], list[dict[str, str]]]
RESOURCE_POLICY = "canvas-metadata-v1:users-self,active-courses,terms;GET-only;collector-deferred"
_SAFE_CODES = frozenset({"INVALID_CREDENTIAL", "PERMISSION_MISSING", "NOT_FOUND",
    "PROVIDER_UNAVAILABLE", "TIMEOUT", "RATE_LIMITED", "DESTINATION_NOT_ALLOWED",
    "CHECK_NOT_READ_ONLY", "PROFILE_MISMATCH", "NOT_CONFIGURED", "CONFIGURATION_CHANGED",
    "OPERATION_IN_PROGRESS", "MANUAL_REVIEW", "CREDENTIAL_STORE_UNAVAILABLE"})


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canvas_profile_id(origin: str, user_id: str) -> str:
    return "c" + _sha((origin + "\n" + user_id).encode())[:32]


def canvas_registry_hash(registry: dict[str, Any]) -> str:
    """Display text never becomes identity or changes the lease binding."""
    return _sha(_canonical({"term_id": registry["term_id"], "courses": sorted(
        (row["course_id"], row["term_id"]) for row in registry["courses"])}))


def canvas_lease_binding(profile: dict[str, str], registry: dict[str, Any]) -> str:
    return _sha(_canonical({"profile_id": profile["id"], "origin": profile["origin"],
                           "user_id": profile["user_id"], "registry": canvas_registry_hash(registry),
                           "resource_policy": RESOURCE_POLICY}))


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise failure("NOT_FOUND")
    return value


def _masked_account(value: str) -> str:
    """Return a short, server-derived identity hint without exposing the full name."""
    words = value.split()
    if not words:
        return "Verified Canvas account"
    return " ".join(word[0] + "•" * min(6, max(2, len(word) - 1)) for word in words)


def _display(value: object, token: str) -> str:
    if not isinstance(value, str):
        return ""
    if token in value:
        raise failure("PROVIDER_UNAVAILABLE")
    return value[:2048]


class CanvasService:
    def __init__(self, credential_service: CredentialService, *,
                 verifier: Verifier = verify_canvas_user,
                 discoverer: Discoverer = discover_canvas_courses,
                 selection_reader: SelectionReader = reread_canvas_courses,
                 now: Callable[[], datetime] | None = None,
                 fault_hook: Callable[[str], None] | None = None,
                 storage_label: str | None = None) -> None:
        self.credentials = credential_service
        self.config = credential_service.config
        self.journal = credential_service.journal
        self.stores = credential_service.stores
        self.verifier, self.discoverer, self.selection_reader = verifier, discoverer, selection_reader
        self.now = now or (lambda: datetime.now(UTC))
        self.fault_hook = fault_hook
        self.storage_label = storage_label or (
            "this Mac's Keychain" if credential_service.platform == "darwin" else "local credential storage"
        )
        self._last_check: dict[str, Any] | None = None
        self._discovery_gate = CanvasDiscoveryGate()
        # Also used by shared credential recovery in a fresh service instance.
        self.credentials.canvas_verifier = self._verify_staged

    def _pending(self) -> list[dict[str, Any]]:
        return [item for item in self.journal.unresolved() if (
            item["phase"] == "unreadable" or item["binding"].get("provider") == "canvas"
            or any(field.startswith("canvas.") for field in item["fields"]))]

    def _guard_pending(self) -> None:
        if self._pending():
            raise failure("OPERATION_IN_PROGRESS")

    def _load(self, generation: str | None = None) -> LoadedConfig:
        self._guard_pending()
        loaded = self.config.load()
        if generation is not None and generation != loaded.generation:
            raise failure("CONFIGURATION_CHANGED")
        return loaded

    def _profile(self, raw: dict[str, Any]) -> dict[str, str]:
        profile = raw.get("canvas", {}).get("profile")
        if (not isinstance(profile, dict) or set(profile) != {
                "id", "origin", "user_id", "display_name", "credential"}
                or not all(isinstance(value, str) for value in profile.values())):
            raise failure("NOT_CONFIGURED")
        try:
            normalized = validate_canvas_origin(profile["origin"]).origin
        except CanvasCheckError:
            normalized = ""
        if (normalized != profile["origin"] or profile["credential"] != "keyring"
                or canvas_profile_id(normalized, _identifier(profile["user_id"])) != profile["id"]):
            raise failure("NOT_CONFIGURED")
        return profile

    def _registry(self, section: dict[str, Any]) -> dict[str, Any]:
        registry = section.get("registry")
        if not isinstance(registry, dict) or set(registry) != {"term_id", "courses"}:
            raise failure("NOT_CONFIGURED")
        term = _identifier(registry["term_id"])
        rows = registry["courses"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise failure("NOT_CONFIGURED")
        ids: set[str] = set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"course_id", "term_id", "name", "code"}
                    or _identifier(row["term_id"]) != term
                    or _identifier(row["course_id"]) in ids
                    or not isinstance(row["name"], str) or not isinstance(row["code"], str)):
                raise failure("NOT_CONFIGURED")
            ids.add(row["course_id"])
        return registry

    def _token_bytes(self, token: str) -> bytes:
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise failure("INVALID_CREDENTIAL")
        return token.encode()

    def _invoke(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        code: str | None = None
        try:
            result = callback(*args, **kwargs)
        except (CanvasCheckError, SettingsServiceError, JournalError) as error:
            code = error.code if error.code in _SAFE_CODES else "PROVIDER_UNAVAILABLE"
        except ConfigurationError:
            code = "CREDENTIAL_STORE_UNAVAILABLE"
        except TimeoutError:
            code = "TIMEOUT"
        except Exception:  # noqa: BLE001 -- injected/provider error text may contain secrets
            code = "PROVIDER_UNAVAILABLE"
        if code is not None:
            # Replace even SettingsServiceError: an injected message may echo PATs.
            raise failure(code)
        return result

    def _verify(self, origin: str, token: str, *, deadline: float | None = None) -> dict[str, str]:
        kwargs = {"deadline": deadline} if self.verifier is verify_canvas_user else {}
        result = self._invoke(self.verifier, origin, token, **kwargs)
        if deadline is not None and time.monotonic() > deadline:
            raise failure("TIMEOUT")
        if not isinstance(result, dict):
            raise failure("PROVIDER_UNAVAILABLE")
        user_id = _identifier(result.get("user_id"))
        if token in user_id:
            raise failure("PROVIDER_UNAVAILABLE")
        return {"user_id": user_id,
                "display_name": _display(result.get("display_name"), token)}

    def _verify_staged(self, role: CredentialRole, value: bytes, raw: Any) -> None:
        profile = self._profile(raw)
        if role.profile != profile["id"]:
            raise failure("PROFILE_MISMATCH")
        # Shared save already read back staging; verify that exact slot again.
        staged = self.stores.read(role, "staged")
        if staged is None or staged != value:
            raise failure("INVALID_CREDENTIAL")
        identity = self._verify(profile["origin"], staged.decode())
        if identity["user_id"] != profile["user_id"]:
            raise failure("PROFILE_MISMATCH")

    @contextmanager
    def _admit_profile(self, loaded: LoadedConfig) -> Iterator[dict[str, str]]:
        profile = self._profile(loaded.raw)
        role = canvas_role(profile["id"])
        with (credential_admission(self.stores.root, role.locator(self.stores.root),
                                  journal=self.journal, config_path=self.config.path),
              self.journal.role_locks([role.role_key])):
            fresh = self._load(loaded.generation)
            if self._profile(fresh.raw) != profile:
                raise failure("CONFIGURATION_CHANGED")
            yield profile

    def _stored_token(self, profile: dict[str, str]) -> str:
        value = self._invoke(self.stores.read, canvas_role(profile["id"]))
        if value is None:
            raise failure("NOT_CONFIGURED")
        self._invoke(structural_credential, canvas_role(profile["id"]), value)
        text = self._invoke(bytes.decode, value)
        self._token_bytes(text)
        return cast(str, text)

    def _lease_state(self, section: dict[str, Any], profile: dict[str, str]) -> dict[str, Any]:
        lease = section.get("lease")
        if not lease:
            return {"state": "not_issued"}
        try:
            registry = self._registry(section)
            binding = canvas_lease_binding(profile, registry)
            if not isinstance(lease, dict) or lease.get("binding_hash") != binding:
                return {"state": "needs_renewal"}
            issued = datetime.fromisoformat(lease["issued_at"])
            expires = datetime.fromisoformat(lease["expires_at"])
            now = self.now().astimezone(UTC)
            if (issued.tzinfo is None or expires.tzinfo is None or expires <= issued
                    or expires - issued > timedelta(days=30) or issued > now):
                return {"state": "needs_renewal"}
            state = "expired" if expires <= now else "expiring" if expires - now <= timedelta(days=7) else "active"
            return {"state": state, "issued_at": lease["issued_at"], "expires_at": lease["expires_at"]}
        except (ValueError, TypeError, KeyError, AttributeError, SettingsServiceError):
            return {"state": "needs_renewal"}

    def snapshot(self) -> dict[str, Any]:
        loaded = self.config.load()
        section = loaded.raw.get("canvas", {})
        pending = self._pending()
        profile: dict[str, str] | None = None
        try:
            profile = self._profile(loaded.raw)
        except SettingsServiceError:
            pass
        registry = None
        if profile is not None:
            try:
                registry = self._registry(section)
            except SettingsServiceError:
                pass
        supported = self.credentials.platform == "darwin"
        present = False
        store_failed = False
        if supported and not pending and profile is not None:
            try:
                present = self._invoke(self.stores.read, canvas_role(profile["id"])) is not None
            except SettingsServiceError:
                store_failed = True
        state = "partial" if profile else "not_configured"
        if profile and registry and present:
            state = "ready"
        if pending:
            state = "partial"
        if store_failed:
            state = "error"
        if not supported:
            state = "unsupported_platform"
        masked_account = _masked_account(profile.get("display_name", "")) if profile else None
        return {"config_generation": loaded.generation, "state": state,
                "profile": copy.deepcopy(profile), "registry": copy.deepcopy(registry),
                "masked_account": masked_account, "storage_label": self.storage_label,
                "lease": self._lease_state(section, profile) if profile else {"state": "not_issued"},
                "sync_enabled": section.get("sync_enabled", False), "sync_available": False,
                "credential_present": bool(present), "can_mutate": supported and not pending,
                "last_check": copy.deepcopy(self._last_check), "pending_operations": [
                    {key: item[key] for key in ("operation_id", "action_kind", "phase", "next_action")}
                    for item in pending]}

    def connect(self, origin: str, token: str, generation: str) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load(generation)
        if loaded.raw.get("canvas", {}).get("profile"):
            raise failure("PROFILE_EXISTS")
        value = self._token_bytes(token)
        normalized = self._invoke(validate_canvas_origin, origin).origin
        identity = self._verify(normalized, token)
        profile = {"id": canvas_profile_id(normalized, identity["user_id"]), "origin": normalized,
                   **identity, "credential": "keyring"}
        candidate = copy.deepcopy(loaded.raw)
        candidate["canvas"] = {"profile": profile, "sync_enabled": False,
                               "registry": {"term_id": "", "courses": []}}
        result: dict[str, Any] = self._invoke(self.credentials.save, canvas_role(profile["id"]),
            value, generation, candidate=candidate, fault_hook=self.fault_hook)
        self._last_check = None
        return result

    def replace(self, token: str, generation: str) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load(generation)
        profile = self._profile(loaded.raw)
        value = self._token_bytes(token)
        if self._verify(profile["origin"], token)["user_id"] != profile["user_id"]:
            raise failure("PROFILE_MISMATCH")
        result: dict[str, Any] = self._invoke(self.credentials.save, canvas_role(profile["id"]),
            value, generation, replace=True, candidate=copy.deepcopy(loaded.raw), fault_hook=self.fault_hook)
        self._last_check = None
        return result

    def forget(self, confirm_profile_id: str, generation: str) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load(generation)
        profile = self._profile(loaded.raw)
        if confirm_profile_id != profile["id"]:
            raise failure("PROFILE_MISMATCH")
        candidate = copy.deepcopy(loaded.raw)
        candidate["canvas"] = {"sync_enabled": False}
        result: dict[str, Any] = self._invoke(self.credentials.forget, canvas_role(profile["id"]),
            generation, candidate=candidate, fault_hook=self.fault_hook)
        self._last_check = None
        return result

    def test(self) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load()
        code: str | None = None
        try:
            with self._admit_profile(loaded) as profile:
                identity = self._verify(profile["origin"], self._stored_token(profile))
                if identity["user_id"] != profile["user_id"]:
                    raise failure("PROFILE_MISMATCH")
                self._load(loaded.generation)
        except SettingsServiceError as error:
            code = error.code
        self._last_check = {"code": code or "VERIFIED", "checked_at": self.now().astimezone(UTC).isoformat()}
        if code:
            raise failure(code)
        return copy.deepcopy(self._last_check)

    def discover(self) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load()
        profile_id = self._profile(loaded.raw)["id"]
        self._invoke(self._discovery_gate.begin, profile_id)
        try:
            return self._discover(loaded)
        finally:
            self._discovery_gate.finish(profile_id)

    def _discover(self, loaded: LoadedConfig) -> dict[str, Any]:
        deadline = time.monotonic() + 45.0
        with self._admit_profile(loaded) as profile:
            token = self._stored_token(profile)
            if self._verify(profile["origin"], token, deadline=deadline)["user_id"] != profile["user_id"]:
                raise failure("PROFILE_MISMATCH")
            kwargs = ({"verify_user": False, "deadline": deadline, "profile_id": profile["id"],
                       "gate": CanvasDiscoveryGate()} if self.discoverer is discover_canvas_courses else {})
            result = self._invoke(self.discoverer, profile["origin"], token, **kwargs)
            if time.monotonic() > deadline:
                raise failure("TIMEOUT")
            if not isinstance(result, dict) or not isinstance(result.get("courses"), list):
                raise failure("PROVIDER_UNAVAILABLE")
            rows = result["courses"]
            if len(rows) > 200:
                raise failure("PROVIDER_UNAVAILABLE")
            courses = [self._course_metadata(row, token) for row in rows]
            terms = {row["term_id"]: _display(source.get("term_name"), token)
                     for row, source in zip(courses, rows, strict=True)}
            self._load(loaded.generation)
            return {"terms": [{"term_id": key, "name": value} for key, value in sorted(terms.items())],
                    "courses": courses}

    def _course_metadata(self, row: Any, token: str) -> dict[str, str]:
        if not isinstance(row, dict):
            raise failure("PROVIDER_UNAVAILABLE")
        course_id, term_id = _identifier(row.get("course_id")), _identifier(row.get("term_id"))
        if token in course_id or token in term_id:
            raise failure("PROVIDER_UNAVAILABLE")
        return {"course_id": course_id, "term_id": term_id,
                "name": _display(row.get("name"), token), "code": _display(row.get("code"), token)}

    def _selection_ids(self, term_id: str, course_ids: Iterable[str]) -> tuple[str, list[str]]:
        term = _identifier(term_id)
        ids = list(islice(course_ids, 21))
        if not 1 <= len(ids) <= 20 or any(not isinstance(item, str) for item in ids):
            raise failure("NOT_FOUND")
        ids = [_identifier(item) for item in ids]
        if len(set(ids)) != len(ids):
            raise failure("NOT_FOUND")
        return term, ids

    def _selection_candidate(self, loaded: LoadedConfig, profile: dict[str, str],
                             term: str, ids: list[str]) -> dict[str, Any]:
        deadline = time.monotonic() + 45.0
        token = self._stored_token(profile)
        if self._verify(profile["origin"], token, deadline=deadline)["user_id"] != profile["user_id"]:
            raise failure("PROFILE_MISMATCH")
        kwargs = {"deadline": deadline, "term_id": term} if self.selection_reader is reread_canvas_courses else {}
        rows = self._invoke(self.selection_reader, profile["origin"], token, ids, **kwargs)
        if time.monotonic() > deadline:
            raise failure("TIMEOUT")
        if not isinstance(rows, list) or len(rows) != len(ids):
            raise failure("NOT_FOUND")
        courses = [self._course_metadata(row, token) for row in rows]
        if ({row["course_id"] for row in courses} != set(ids)
                or any(row["term_id"] != term for row in courses)):
            raise failure("NOT_FOUND")
        candidate = copy.deepcopy(loaded.raw)
        candidate["canvas"]["registry"] = {"term_id": term,
                                           "courses": sorted(courses, key=lambda row: row["course_id"])}
        self._load(loaded.generation)
        return candidate

    def _payload(self, candidate: dict[str, Any]) -> bytes:
        payload = cast(str, yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True)).encode()
        self.config._parse(payload)
        return payload

    def save_selection(self, term_id: str, course_ids: Iterable[str], generation: str) -> dict[str, Any]:
        """Validate/re-read and preview only. No config or journal writes."""
        self.credentials._require_platform()
        term, ids = self._selection_ids(term_id, course_ids)
        loaded = self._load(generation)
        with self._admit_profile(loaded) as profile:
            candidate = self._selection_candidate(loaded, profile, term, ids)
            return {"valid": True, "generation": generation, "candidate_hash": _sha(self._payload(candidate)),
                    "values": {"term_id": term, "course_ids": sorted(ids)},
                    "registry": copy.deepcopy(candidate["canvas"]["registry"]),
                    "diff": [{"field": "canvas.registry", "before": loaded.raw["canvas"].get("registry"),
                              "after": copy.deepcopy(candidate["canvas"]["registry"])}]}

    def apply_selection(self, term_id: str, course_ids: Iterable[str], generation: str,
                        candidate_hash: str) -> dict[str, Any]:
        """Re-read the exact selection and CAS only the user-reviewed candidate."""
        self.credentials._require_platform()
        if not isinstance(candidate_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_hash):
            raise failure("REVIEW_REQUIRED")
        term, ids = self._selection_ids(term_id, course_ids)
        loaded = self._load(generation)
        with self._admit_profile(loaded) as profile:
            candidate = self._selection_candidate(loaded, profile, term, ids)
            if _sha(self._payload(candidate)) != candidate_hash:
                raise failure("REVIEW_STALE")
            return self._apply(candidate, generation, "canvas.registry")

    def renew(self, generation: str) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self._load(generation)
        profile = self._profile(loaded.raw)
        registry = self._registry(loaded.raw["canvas"])
        binding = canvas_lease_binding(profile, registry)
        with self._admit_profile(loaded):
            identity = self._verify(profile["origin"], self._stored_token(profile))
            if identity["user_id"] != profile["user_id"]:
                raise failure("PROFILE_MISMATCH")
            now = self.now().astimezone(UTC)
            candidate = copy.deepcopy(loaded.raw)
            candidate["canvas"]["lease"] = {"issued_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
                "binding_hash": binding}
            return self._apply(candidate, generation, "canvas.lease")

    def disable_sync(self, generation: str) -> dict[str, Any]:
        self.credentials._require_platform()
        loaded = self.config.load()
        if loaded.generation != generation:
            raise failure("CONFIGURATION_CHANGED")
        if loaded.raw.get("canvas", {}).get("sync_enabled", False) is False:
            # Already disabled is safe even during credential recovery, without
            # changing the generations recorded by the pending transaction.
            return {"status": "complete", "code": "APPLIED", "config_generation": generation}
        self._guard_pending()
        candidate = copy.deepcopy(loaded.raw)
        candidate.setdefault("canvas", {})["sync_enabled"] = False
        # Disabling never reads the keyring or uses the provider. A profile may
        # be absent after detach; the operation is still a config-only CAS.
        if loaded.raw.get("canvas", {}).get("profile"):
            with self._admit_profile(loaded):
                return self._apply(candidate, generation, "canvas.sync_enabled")
        return self._apply(candidate, generation, "canvas.sync_enabled")

    def _apply(self, candidate: dict[str, Any], generation: str, field: str) -> dict[str, Any]:
        self._guard_pending()
        payload = self._payload(candidate)
        candidate_hash = _sha(payload)
        operation_id = self.journal.create_config_operation(binding=self.config.binding(),
            original_generation=generation, candidate_hash=candidate_hash, fields=[field])
        with self.journal.operation(operation_id) as operation, ConfigFileLock(self.config.path):
            current = read_config_bytes(self.config.path)
            if _sha(current) != generation:
                operation.update(phase="resolved_without_change", next_action="none")
                raise failure("CONFIGURATION_CHANGED")
            operation.run_effect("config_replace", pre_state=generation, intended_post_state=candidate_hash,
                perform=lambda: atomic_replace_config(self.config.path, payload),
                observe=lambda: _sha(read_config_bytes(self.config.path)), fault_hook=self.fault_hook)
            readback = self.config._parse(read_config_bytes(self.config.path))
            if readback.generation != candidate_hash:
                raise failure("CONFIGURATION_CHANGED")
            operation.update(phase="complete", next_action="none", readback_generation=readback.generation)
        return {"status": "complete", "code": "APPLIED", "config_generation": candidate_hash}
