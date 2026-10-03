"""Private per-operation journal for local settings mutations and recovery.

Each operation owns one owner-only JSON record and one cross-process record lock.
Writers never share an index file. Records follow a versioned, action-specific
schema: the action fixes the effect names and order and the required binding
(target identity/locators). A transition validator keeps every identity field
immutable, phases monotonic, terminal records immutable, and completion tied to
every planned effect's verified post-state. Every authoritative side effect is
bracketed by a durable intent (exact pre-state and intended post-state) and a
durable observed post-state, so recovery compares recorded state IDs with the
current authoritative store instead of guessing.

Lock order is fixed: sorted credential/profile role locks -> this operation's
record lock -> the shared config lock. Config-only operations skip role locks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from functools import partial
from pathlib import Path
from typing import Any, Self

from uls.orchestration.locks import LocalFileLock

SCHEMA_VERSION = 3
JOURNAL_LOCK_WAIT_SECONDS = 5.0
MAX_RECORD_BYTES = 128 * 1024

_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,31}/[A-Za-z0-9_.-]{1,64}/[a-z][a-z0-9_-]{0,31}$")
TERMINAL_PHASES = frozenset({"complete", "resolved_without_change", "completed_then_superseded"})
# Allowed phase transitions. Effects alternate effect_intent <-> committed.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "prepare": frozenset({
        "candidate_validated", "effect_intent", "resolved_without_change", "repair_required",
    }),
    "candidate_validated": frozenset({
        "locked", "effect_intent", "resolved_without_change", "repair_required",
    }),
    "locked": frozenset({"effect_intent", "resolved_without_change", "repair_required"}),
    "effect_intent": frozenset({
        "committed", "repair_required", "complete", "resolved_without_change",
    }),
    "committed": frozenset({
        "effect_intent", "readback", "complete", "repair_required", "completed_then_superseded",
    }),
    "readback": frozenset({"complete", "repair_required", "completed_then_superseded"}),
    "repair_required": frozenset({
        "complete", "resolved_without_change", "completed_then_superseded",
    }),
}
_ALLOWED_PHASES = frozenset(_TRANSITIONS) | TERMINAL_PHASES
_VERIFIED = frozenset({"verified", "verified_by_recovery"})
# verified_not_applied: an intent whose authoritative store was proven, under
# the branch-switch locks, to still hold its pre-state. Only effects outside
# the selected branch may carry it.
NOT_APPLIED = "verified_not_applied"
_EFFECT_STATUSES = _VERIFIED | {"intent", "readback_mismatch", NOT_APPLIED}
_GENERATION = re.compile(r"^[a-f0-9]{64}$")
_EFFECT_KEYS = frozenset({
    "sequence", "pre_state_id", "intended_post_state_id", "post_state_id", "status",
})

# The state ID an authoritative store reports for a slot that holds nothing.
ABSENT_STATE = "absent"
_CREDENTIAL_BINDING = frozenset({
    "provider", "profile", "role", "store_locator", "config_path", "config_dir_id",
})

# Versioned action schemas (parent plan, accepted transaction machines).
#
# branches: named, mutually exclusive effect sequences. Every record starts on
#   "primary"; "switch" lists the only allowed one-time alternative branches and
#   the evidence each requires. Completion always means every effect of the
#   selected branch is verified at its intended post-state.
# links: (effect, field, source_effect, source_field) equalities that bind one
#   effect's recorded states to earlier evidence (for example, a restore must
#   restore exactly the backed-up version).
# fixed: (effect, field, value) constants, for example deletion ends absent.
ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "config_apply": {
        "branches": {"primary": ("config_replace",)},
        "binding": frozenset({"config_path", "config_dir_id"}),
        "roles": False,
        "record_links": (
            ("config_replace", "pre_state_id", "original_generation"),
            ("config_replace", "intended_post_state_id", "candidate_hash"),
        ),
        "generations": "sha256",
    },
    # stage + verify -> config/binding CAS -> promote/activate (never before CAS).
    "credential_enrollment": {
        "branches": {
            "primary": ("credential_stage", "config_commit", "credential_promote"),
            # Config CAS never happened: remove only the operation-owned staged copy.
            "abandon": ("credential_stage", "staged_delete"),
        },
        # Branch admission is bound to authoritative readbacks taken under the
        # canonical role lock and the config lock, persisted as branch_proof.
        "switch": {"abandon": {
            "verified": ("credential_stage",),
            "absent": ("credential_promote",),
            "proof": {"config": ("record", "original_generation"),
                      "staged": ("effect", "credential_stage", "post_state_id")},
            "not_applied": {"config_commit": "config"},
            # Guards reread before each branch effect (its own store is checked
            # by the effect's linked pre-state).
            "effect_guards": {"staged_delete": {"config": ("record", "original_generation")}},
        }},
        "binding": _CREDENTIAL_BINDING | {"staging_locator"},
        "roles": True,
        "links": (
            ("credential_promote", "intended_post_state_id", "credential_stage", "post_state_id"),
            ("staged_delete", "pre_state_id", "credential_stage", "post_state_id"),
        ),
        "record_links": (
            ("config_commit", "pre_state_id", "original_generation"),
            ("config_commit", "intended_post_state_id", "candidate_hash"),
        ),
        "generations": "distinct",
        "fixed": (("staged_delete", "intended_post_state_id", ABSENT_STATE),),
    },
    # stage + verify -> backup old active -> promote + readback -> config commit
    # + readback -> delete backup + verify deletion.
    "credential_replacement": {
        "branches": {
            "primary": ("credential_stage", "credential_backup", "credential_promote",
                        "config_commit", "backup_delete"),
            # Promotion happened but config is still original: restore the exact
            # backup, then remove the backup copy.
            "restore": ("credential_stage", "credential_backup", "credential_promote",
                        "credential_restore", "backup_delete"),
        },
        "switch": {"restore": {
            "verified": ("credential_stage", "credential_backup", "credential_promote"),
            "absent": (),
            "proof": {"config": ("record", "original_generation"),
                      "active": ("effect", "credential_promote", "post_state_id"),
                      "backup": ("effect", "credential_backup", "post_state_id")},
            "not_applied": {"config_commit": "config"},
            "effect_guards": {
                "credential_restore": {"config": ("record", "original_generation"),
                                       "backup": ("effect", "credential_backup", "post_state_id")},
                "backup_delete": {"config": ("record", "original_generation"),
                                  "active": ("effect", "credential_restore", "post_state_id")},
            },
        }},
        "binding": _CREDENTIAL_BINDING | {"staging_locator", "backup_locator"},
        "roles": True,
        "links": (
            ("credential_promote", "pre_state_id", "credential_backup", "intended_post_state_id"),
            ("credential_promote", "intended_post_state_id", "credential_stage", "post_state_id"),
            ("credential_restore", "pre_state_id", "credential_promote", "post_state_id"),
            ("credential_restore", "intended_post_state_id", "credential_backup", "post_state_id"),
            ("backup_delete", "pre_state_id", "credential_backup", "post_state_id"),
        ),
        "record_links": (
            ("config_commit", "pre_state_id", "original_generation"),
            ("config_commit", "intended_post_state_id", "candidate_hash"),
        ),
        "generations": "distinct",
        "fixed": (
            ("credential_backup", "pre_state_id", ABSENT_STATE),
            ("backup_delete", "intended_post_state_id", ABSENT_STATE),
        ),
    },
    # config/binding/lease detach under CAS -> delete exact credential -> readback.
    "credential_forget": {
        "branches": {"primary": ("config_detach", "credential_delete")},
        "binding": _CREDENTIAL_BINDING,
        "roles": True,
        "record_links": (
            ("config_detach", "pre_state_id", "original_generation"),
            ("config_detach", "intended_post_state_id", "candidate_hash"),
        ),
        "generations": "distinct",
        "fixed": (("credential_delete", "intended_post_state_id", ABSENT_STATE),),
    },
    "fake_store_test": {
        "branches": {"primary": ("effect_a",)},
        "binding": frozenset({"store"}),
        "roles": False,
    },
    "fake_multi_store_test": {
        "branches": {"primary": ("credential_promote", "config_commit")},
        "binding": frozenset({"credential_store", "config_store"}),
        "roles": False,
    },
}


def _effect_positions(schema: dict[str, Any]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for effects in schema["branches"].values():
        for index, name in enumerate(effects):
            if positions.setdefault(name, index) != index:
                raise AssertionError("schema effect positions must agree across branches")
    return positions


# Preserve the earlier fake-store machine regressions as unreleased test kinds.
for _kind in ("credential_enrollment", "credential_replacement", "credential_forget"):
    ACTION_SCHEMAS["fake_" + _kind] = copy.deepcopy(ACTION_SCHEMAS[_kind])

ACTION_SCHEMAS["credential_enrollment"]["branches"]["primary"] += ("staging_cleanup",)
ACTION_SCHEMAS["credential_replacement"]["branches"]["primary"] += ("staging_cleanup",)
ACTION_SCHEMAS["credential_replacement"]["branches"]["restore"] += ("staging_cleanup",)
ACTION_SCHEMAS["credential_replacement"]["branches"]["reject"] = ("credential_stage", "staged_delete")
ACTION_SCHEMAS["credential_replacement"]["switch"]["reject"] = {
    "verified": ("credential_stage",), "absent": ("credential_backup", "credential_promote", "config_commit"),
    "proof": {"config": ("record", "original_generation"),
              "active": ("effect", "credential_stage", "pre_active_id"), "backup": ("constant", ABSENT_STATE)},
    "not_applied": {},
    "effect_guards": {"staged_delete": {"config": ("record", "original_generation"),
                                         "active": ("binding", "original_active_id"),
                                         "backup": ("constant", ABSENT_STATE)}},
}
# The pre-active identity is immutable binding evidence (never a secret).
ACTION_SCHEMAS["credential_replacement"]["binding"] |= {"original_active_id"}
ACTION_SCHEMAS["credential_replacement"]["switch"]["reject"]["proof"]["active"] = ("binding", "original_active_id")
ACTION_SCHEMAS["credential_replacement"]["switch"]["restore"]["effect_guards"]["staging_cleanup"] = {
    "config": ("record", "original_generation"), "active": ("effect", "credential_restore", "post_state_id"),
    "backup": ("constant", ABSENT_STATE),
}
for _kind in ("credential_enrollment", "credential_replacement"):
    ACTION_SCHEMAS[_kind]["links"] += (("staging_cleanup", "pre_state_id", "credential_stage", "post_state_id"),)
    ACTION_SCHEMAS[_kind]["fixed"] += (("staging_cleanup", "intended_post_state_id", ABSENT_STATE),)
ACTION_SCHEMAS["credential_replacement"]["links"] += (("staged_delete", "pre_state_id", "credential_stage", "post_state_id"),)
ACTION_SCHEMAS["credential_replacement"]["fixed"] += (("staged_delete", "intended_post_state_id", ABSENT_STATE),)
ACTION_SCHEMAS["credential_detach"] = copy.deepcopy(ACTION_SCHEMAS["credential_forget"])
ACTION_SCHEMAS["credential_detach"]["branches"] = {"primary": ("config_detach",)}
ACTION_SCHEMAS["credential_detach"]["fixed"] = ()
for _kind in ("credential_enrollment", "credential_replacement", "credential_forget", "credential_detach"):
    ACTION_SCHEMAS[_kind]["binding"] |= {"config_patch"}
ACTION_VERSIONS = {kind: (4 if kind.startswith("credential_") else 3) for kind in ACTION_SCHEMAS}


def _positions(schema: dict[str, Any], branch: str) -> dict[str, int]:
    return {name: index for index, name in enumerate(schema["branches"][branch])}


def canonical_role_key(binding: dict[str, Any]) -> str:
    """Derive the single role-lock key from the immutable credential binding."""

    key = f"{binding.get('provider')}/{binding.get('profile')}/{binding.get('role')}"
    if not _ROLE_KEY.fullmatch(key):
        raise ValueError("credential binding does not identify a valid role")
    return key
LIVE_ACTION_KINDS = frozenset({"config_apply"})
# Parent-plan schemas owned by GUI-2/GUI-3. Records of these kinds validate, but
# GUI-1 refuses to create them because it owns no credential store.
DEFERRED_ACTION_KINDS = frozenset({
    "credential_enrollment", "credential_replacement", "credential_forget", "credential_detach",
})
FAKE_ACTION_KINDS = frozenset(kind for kind in ACTION_SCHEMAS if kind.startswith("fake_"))
_IMMUTABLE_FIELDS = (
    "schema_version", "operation_id", "action_kind", "role_group", "role_keys", "binding",
    "original_generation", "candidate_hash", "fields", "created_at",
)
_REQUIRED_FIELDS = frozenset(_IMMUTABLE_FIELDS) | {
    "branch", "branch_proof", "planned_effects", "phase", "effects", "next_action",
}
_OPTIONAL_FIELDS = frozenset({"observed_generation", "readback_generation"})
_UPDATABLE_FIELDS = frozenset({
    "phase", "effects", "observed_generation", "readback_generation", "next_action",
})


class JournalError(Exception):
    """A fixed, secret-free journal refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OperationLockRequired(JournalError, RuntimeError):
    """A record-mutating call was made without holding this operation's record lock."""

    def __init__(self) -> None:
        super().__init__("OPERATION_LOCK_REQUIRED", "This settings operation is not locked by the caller.")


class OperationInProgress(JournalError):
    def __init__(self) -> None:
        super().__init__(
            "OPERATION_IN_PROGRESS",
            "An earlier change for this connection is unfinished. Resolve it first.",
        )


class SimulatedCrash(BaseException):
    """Raised only by test fault hooks to model a process dying mid-operation."""


def journal_directory(workspace_dir: str | os.PathLike[str]) -> Path:
    """Create/verify the private journal directory under the configured workspace."""

    workspace = Path(workspace_dir).expanduser().resolve()
    directory = workspace / ".uls" / "settings-journal"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("settings journal directory is not a regular directory")
    if os.name != "nt":
        info = directory.stat()
        if info.st_uid != os.getuid():
            raise ValueError("settings journal directory is not owned by the current user")
        os.chmod(directory, 0o700)
        if directory.stat().st_mode & 0o077:
            raise ValueError("settings journal directory permissions are too open")
    return directory


class RoleLockSet:
    """Sorted cross-process credential/profile role locks held by one caller."""

    def __init__(self, directory: Path, role_keys: Iterable[str]) -> None:
        keys = sorted(set(role_keys))
        for key in keys:
            if not _ROLE_KEY.fullmatch(key):
                raise ValueError("credential role key is invalid")
        self.keys = tuple(keys)
        self._locks = [
            LocalFileLock(directory / f"role-{hashlib.sha256(key.encode()).hexdigest()[:32]}.lock")
            for key in self.keys
        ]
        self._held: list[LocalFileLock] = []

    def __enter__(self) -> Self:
        try:
            for lock in self._locks:
                if not lock.acquire(timeout=JOURNAL_LOCK_WAIT_SECONDS):
                    raise TimeoutError("credential role is locked by another operation")
                self._held.append(lock)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        while self._held:
            self._held.pop().release()

    @property
    def is_held(self) -> bool:
        return bool(self._locks) and len(self._held) == len(self._locks)


class JournalOperation(AbstractContextManager["JournalOperation"]):
    def __init__(self, store: JournalStore, operation_id: str) -> None:
        self.store = store
        self.operation_id = operation_id
        self._lock = LocalFileLock(store.directory / f"{operation_id}.lock")

    def __enter__(self) -> Self:
        if not self._lock.acquire(timeout=JOURNAL_LOCK_WAIT_SECONDS):
            raise TimeoutError("settings operation is already being updated")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._lock.release()

    @property
    def is_held(self) -> bool:
        return self._lock.is_held

    def _require_operation_lock(self) -> None:
        """Every record-mutating method calls this first, before any read or side effect."""

        if not self._lock.is_held:
            raise OperationLockRequired()

    def read(self) -> dict[str, Any]:
        return self.store.read(self.operation_id)

    def switch_branch(
        self,
        branch: str,
        *,
        role_locks: RoleLockSet,
        config_lock: Any,
        observe: Mapping[str, Callable[[], str]],
        next_action: str,
        resolver: Any = None,
    ) -> dict[str, Any]:
        """Select a mutually exclusive recovery/cleanup branch from authoritative proof.

        Requires the record's canonical role lock and the bound config's lock to
        be held. Each store named by the branch rule is read back through
        observe; every readback must equal its recorded expectation, and the
        proof is persisted immutably with the switch. An unresolved intent left
        outside the new branch becomes verified_not_applied only when the proof
        shows its store still at the intent's pre-state.
        """

        self._require_operation_lock()
        record = self.read()
        if record["schema_version"] == 4:
            if resolver is None:
                raise ValueError("credential effects require a code-owned resolver")
            observe = {key: partial(resolver.observe, record, key) for key in ("config", "active", "staged", "backup")}
        rule = ACTION_SCHEMAS[record["action_kind"]].get("switch", {}).get(branch)
        if rule is None or record["branch"] != "primary":
            raise ValueError("journal branch switch is not allowed")
        _require_branch_locks(record, role_locks, config_lock)
        proof = {key: observe[key]() for key in rule["proof"]}
        if proof != _expected_proof(record, rule):
            raise JournalError(
                "BRANCH_PROOF_FAILED", "The saved state does not allow this repair. It stays for review.",
            )
        order = ACTION_SCHEMAS[record["action_kind"]]["branches"][branch]
        effects = copy.deepcopy(record["effects"])
        for name, effect in effects.items():
            if name in order:
                continue
            store_key = rule.get("not_applied", {}).get(name)
            if effect["status"] != "intent" or store_key is None or proof[store_key] != effect["pre_state_id"]:
                raise JournalError(
                    "BRANCH_PROOF_FAILED", "An unfinished step may have taken effect. It stays for review.",
                )
            effects[name] = {**effect, "post_state_id": effect["pre_state_id"], "status": NOT_APPLIED}
        # Dedicated write path: the only way a branch, its proof, or a
        # verified_not_applied status can ever enter a record.
        new = {**record, "branch": branch, "branch_proof": proof, "planned_effects": list(order),
               "effects": effects, "next_action": next_action}
        validate_transition(record, new, branch_switch=True)
        self.store._atomic_write(new)
        return new

    def update(self, **changes: Any) -> dict[str, Any]:
        """Generic phase/effect update. It can never switch branches or prove non-application."""

        self._require_operation_lock()
        if set(changes) - _UPDATABLE_FIELDS:
            raise ValueError("journal update contains unsupported fields")
        old = self.read()
        new = {**old, **changes}
        validate_transition(old, new, branch_switch=False)
        self.store._atomic_write(new)
        return new

    def run_effect(
        self,
        name: str,
        *,
        pre_state: str,
        intended_post_state: str,
        perform: Callable[[], None],
        observe: Callable[[], str],
        fault_hook: Callable[[str], None] | None = None,
        role_locks: RoleLockSet | None = None,
        config_lock: Any = None,
        guards: Mapping[str, Callable[[], str]] | None = None,
        resolver: Any = None,
    ) -> str:
        """Perform exactly one authoritative side effect between durable records.

        The effect must be the next planned effect. The authoritative store must
        still be at the pre-state; an existing intent may be resumed only with
        its persisted pre/intended states, and is not re-performed when the
        store already shows the intended state. The intent is fsynced before the
        effect; the observed post-state is fsynced after authoritative readback.
        Fault-hook points: before_<name>, after_<name>, after_<name>_recorded.

        On an alternate (recovery/cleanup) branch the caller must hold the
        canonical role lock and the bound config lock for the whole call, and
        every guard store of that effect is reread and must still match the
        record before the effect runs; the branch proof alone is historical.
        """

        self._require_operation_lock()
        record = self.read()
        if record["schema_version"] == 4:
            if resolver is None:
                raise ValueError("credential effects require a code-owned resolver")
            observe = lambda: resolver.observe(record, resolver.effect_store(name))
            guards = {key: partial(resolver.observe, record, key) for key in ("config", "active", "staged", "backup")}
        planned = record["planned_effects"]
        if name not in planned:
            raise ValueError("journal effect was not planned for this operation")
        if record["branch"] != "primary":
            _require_branch_locks(record, role_locks, config_lock)
            rule = ACTION_SCHEMAS[record["action_kind"]]["switch"][record["branch"]]
            spec = rule["effect_guards"].get(name, {})
            expected = _expected_proof(record, {"proof": spec})
            guard_values = {key: (guards or {})[key]() for key in spec} if set(spec) <= set(guards or {}) else None
            if guard_values != expected:
                raise JournalError(
                    "BRANCH_GUARD_FAILED",
                    "Saved settings changed since this repair was chosen. It stays for review.",
                )
        index = planned.index(name)
        effects = dict(record["effects"])
        for earlier in planned[:index]:
            if effects.get(earlier, {}).get("status") not in _VERIFIED:
                raise JournalError("EFFECT_OUT_OF_ORDER", "An earlier step has not finished.")
        existing = effects.get(name)
        if existing is not None:
            if existing["status"] != "intent":
                raise JournalError("EFFECT_ALREADY_RESOLVED", "This step is already resolved.")
            if existing["pre_state_id"] != pre_state or existing["intended_post_state_id"] != intended_post_state:
                raise JournalError("RESUME_STATE_MISMATCH", "The recorded step does not match this retry.")
        intent = {
            "sequence": index,
            "pre_state_id": pre_state,
            "intended_post_state_id": intended_post_state,
            "post_state_id": None,
            "status": "intent",
        }
        if existing is None:
            # Validate the intent against every schema link (including the
            # record's generations) before any store is read or touched.
            validate_record({**record, "phase": "effect_intent", "effects": {**effects, name: intent}},
                            self.operation_id)
        current = observe()
        already_applied = existing is not None and current == intended_post_state
        if not already_applied and current != pre_state:
            raise JournalError("PRE_STATE_MISMATCH", "The saved state changed; this step was not run.")
        if existing is None:
            effects[name] = intent
            self.update(phase="effect_intent", effects=effects, next_action=f"verify_{name}")
        _fault(fault_hook, f"before_{name}")
        if not already_applied:
            perform()
        _fault(fault_hook, f"after_{name}")
        observed = observe()
        effects = dict(self.read()["effects"])
        if observed != intended_post_state:
            effects[name] = {**effects[name], "post_state_id": observed, "status": "readback_mismatch"}
            self.update(phase="repair_required", effects=effects, next_action="manual_review")
            raise JournalError("EFFECT_READBACK_MISMATCH", "A saved value did not read back as expected.")
        effects[name] = {**effects[name], "post_state_id": observed, "status": "verified"}
        remaining = [
            item for item in planned
            if item not in effects or effects[item].get("status") not in _VERIFIED
        ]
        self.update(
            phase="committed", effects=effects,
            next_action=f"run_{remaining[0]}" if remaining else "finalize",
        )
        _fault(fault_hook, f"after_{name}_recorded")
        return observed


class JournalStore:
    """Each operation owns one 0600 JSON record and its own cross-process lock."""

    def __init__(self, workspace_dir: str | os.PathLike[str], *, credential_root: Path | None = None) -> None:
        self.directory = journal_directory(workspace_dir)
        self.credential_root = credential_root

    def role_locks(self, role_keys: Iterable[str]) -> RoleLockSet:
        return RoleLockSet(self.directory, role_keys)

    def create_operation(
        self,
        *,
        action_kind: str,
        binding: dict[str, str],
        original_generation: str,
        candidate_hash: str,
        fields: list[str],
        role_locks: RoleLockSet | None = None,
        allow_unreleased: bool = False,
        operation_id: str | None = None,
    ) -> str:
        """Exclusively create one durable record for one new operation.

        The action schema fixes the planned effects (primary branch). A
        credential operation must hold exactly the role lock derived from its
        immutable binding (provider/profile/role); while holding it, any
        unresolved record for the same role (or an unreadable record) fails
        with OPERATION_IN_PROGRESS, so a pending record keeps its role reserved
        across process exit until it is resolved. allow_unreleased admits the
        test-only fake kinds and the credential kinds that later bundles own.
        """

        if action_kind in DEFERRED_ACTION_KINDS | FAKE_ACTION_KINDS:
            if not allow_unreleased:
                raise JournalError("FEATURE_DEFERRED", "This change is not available in this version.")
        elif action_kind not in LIVE_ACTION_KINDS:
            raise ValueError("journal action kind is unsupported")
        schema = ACTION_SCHEMAS[action_kind]
        role_keys: list[str] = []
        if schema["roles"]:
            expected = canonical_role_key(binding)
            if role_locks is None or list(role_locks.keys) != [expected]:
                raise JournalError(
                    "ROLE_BINDING_MISMATCH", "The role lock does not match this credential binding.",
                )
            if not role_locks.is_held:
                raise RuntimeError("credential role locks must be held before creating a record")
            role_keys = [expected]
            for pending in self.unresolved():
                if pending["phase"] == "unreadable" or set(pending["role_keys"]) & set(role_keys):
                    raise OperationInProgress()
        elif role_locks is not None:
            raise ValueError("this action kind does not take role locks")
        if role_keys:
            role_group = "credential"
        else:
            role_group = "config" if action_kind == "config_apply" else "fake"
        operation_id = operation_id or secrets.token_hex(16)
        record: dict[str, Any] = {
            "schema_version": ACTION_VERSIONS[action_kind],
            "operation_id": operation_id,
            "action_kind": action_kind,
            "role_group": role_group,
            "role_keys": role_keys,
            "binding": dict(binding),
            "original_generation": original_generation,
            "candidate_hash": candidate_hash,
            "fields": sorted(set(fields)),
            "branch": "primary",
            "branch_proof": None,
            "planned_effects": list(schema["branches"]["primary"]),
            "phase": "prepare",
            "effects": {},
            "created_at": time.time(),
            "next_action": "inspect",
        }
        validate_record(record, operation_id)
        self._validate_locators(record)
        self._create(record)
        return operation_id

    def create_config_operation(
        self, *, binding: dict[str, str], original_generation: str, candidate_hash: str,
        fields: list[str],
    ) -> str:
        return self.create_operation(
            action_kind="config_apply", binding=binding,
            original_generation=original_generation, candidate_hash=candidate_hash, fields=fields,
        )

    def operation(self, operation_id: str) -> JournalOperation:
        _validate_id(operation_id)
        return JournalOperation(self, operation_id)

    def read(self, operation_id: str) -> dict[str, Any]:
        path = self._record_path(operation_id)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("journal record is not a regular file")
            if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError("journal record ownership or mode is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, MAX_RECORD_BYTES + 1 - total)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RECORD_BYTES:
                    raise ValueError("journal record exceeds the supported size")
                chunks.append(chunk)
        finally:
            os.close(fd)
        record = json.loads(b"".join(chunks).decode("utf-8"))
        validate_record(record, operation_id)
        self._validate_locators(record)
        return dict(record)

    def _validate_locators(self, record: dict[str, Any]) -> None:
        if record["schema_version"] == 4:
            from .credential_roles import validate_locators
            validate_locators(record["binding"], self.credential_root)

    def unresolved(self) -> list[dict[str, Any]]:
        """Scan every record; unreadable records stay visible as pending."""

        result: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            if not _OPERATION_ID.fullmatch(path.stem):
                continue
            try:
                record = self.read(path.stem)
            except (OSError, ValueError, UnicodeDecodeError):
                result.append({
                    "operation_id": path.stem, "action_kind": "unknown", "phase": "unreadable",
                    "fields": [], "role_keys": [], "binding": {}, "next_action": "manual_review",
                })
                continue
            if record["phase"] not in TERMINAL_PHASES:
                result.append({
                    "operation_id": record["operation_id"],
                    "action_kind": record["action_kind"],
                    "phase": record["phase"],
                    "fields": record["fields"],
                    "role_keys": record["role_keys"],
                    "binding": record["binding"],
                    "next_action": record["next_action"],
                })
        return result

    def _create(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["operation_id"])
        data = _json_bytes(record)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            _discard(path)
            raise
        else:
            os.close(fd)
        _fsync_directory(self.directory)

    def _atomic_write(self, record: dict[str, Any]) -> None:
        operation_id = record.get("operation_id")
        if not isinstance(operation_id, str):
            raise ValueError("journal operation ID is invalid")  # noqa: TRY004 - one error type for callers
        target = self._record_path(operation_id)
        data = _json_bytes(record)
        temp = self.directory / f".{operation_id}.{secrets.token_hex(8)}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temp, flags, 0o600)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            _discard(temp)
            raise
        else:
            os.close(fd)
        try:
            os.replace(temp, target)
            _fsync_directory(self.directory)
        except BaseException:
            _discard(temp)
            raise

    def _record_path(self, operation_id: str) -> Path:
        _validate_id(operation_id)
        return self.directory / f"{operation_id}.json"


def _validate_id(operation_id: object) -> None:
    if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
        raise ValueError("journal operation ID is invalid")


def validate_record(record: object, operation_id: str) -> None:
    """Validate one record against its versioned, action-specific schema."""

    if not isinstance(record, dict):
        raise ValueError("journal record root must be an object")  # noqa: TRY004 - one error type for callers
    if not _REQUIRED_FIELDS.issubset(record) or not set(record).issubset(
        _REQUIRED_FIELDS | _OPTIONAL_FIELDS
    ):
        raise ValueError("journal record shape is unsupported")
    if record["schema_version"] != ACTION_VERSIONS.get(record["action_kind"]) or record["operation_id"] != operation_id:
        raise ValueError("journal record identity is invalid")
    schema = ACTION_SCHEMAS.get(record["action_kind"])
    if schema is None:
        raise ValueError("journal record action kind is invalid")
    if record["phase"] not in _ALLOWED_PHASES:
        raise ValueError("journal record phase is invalid")
    for key in ("original_generation", "candidate_hash", "next_action", "role_group"):
        if not isinstance(record[key], str) or len(record[key]) > 128:
            raise ValueError("journal record text field is invalid")
    generations = schema.get("generations")
    if generations is not None:
        if not (_GENERATION.fullmatch(record["original_generation"])
                and _GENERATION.fullmatch(record["candidate_hash"])):
            raise ValueError("journal record generations must be SHA-256 hex digests")
        if generations == "distinct" and record["original_generation"] == record["candidate_hash"]:
            raise ValueError("journal record candidate must differ from the original generation")
    if not _string_list(record["fields"]) or not _string_list(record["role_keys"]):
        raise ValueError("journal record lists are invalid")
    if not all(_ROLE_KEY.fullmatch(item) for item in record["role_keys"]):
        raise ValueError("journal record role keys are invalid")
    binding = record["binding"]
    if not isinstance(binding, dict) or set(binding) != schema["binding"] or not all(
        isinstance(value, str) and 0 < len(value) <= (16384 if key == "config_patch" else 1024)
        for key, value in binding.items()
    ):
        raise ValueError("journal record binding does not match its action schema")
    if schema["roles"]:
        if record["role_keys"] != [canonical_role_key(binding)]:
            raise ValueError("credential role keys must equal the binding's canonical role")
    elif record["role_keys"]:
        raise ValueError("this action kind has no role keys")
    branches = schema["branches"]
    if record["branch"] not in branches or record["planned_effects"] != list(branches[record["branch"]]):
        raise ValueError("journal planned effects do not match the selected branch")
    positions = _positions(schema, record["branch"])
    for other_order in schema["branches"].values():
        for index, name in enumerate(other_order):
            positions.setdefault(name, index)
    order = list(branches[record["branch"]])
    effects = record["effects"]
    if not isinstance(effects, dict) or not set(effects).issubset(positions):
        raise ValueError("journal effects are invalid")
    for name, effect in effects.items():
        if not isinstance(effect, dict) or set(effect) != _EFFECT_KEYS:
            raise ValueError("journal effect record is invalid")
        if effect["status"] not in _EFFECT_STATUSES or effect["sequence"] != positions[name]:
            raise ValueError("journal effect record is invalid")
        if not isinstance(effect["pre_state_id"], str) or not isinstance(effect["intended_post_state_id"], str):
            raise ValueError("journal effect states are invalid")  # noqa: TRY004 - one error type for callers
        if (effect["status"] == "intent") != (effect["post_state_id"] is None):
            raise ValueError("journal effect post-state is inconsistent")
        if (name in order) == (effect["status"] == NOT_APPLIED):
            # Outside the selected branch only proven-not-applied evidence may
            # remain; inside it that status can never occur.
            raise ValueError("an effect outside the selected branch must be proven not applied")
        if effect["status"] == NOT_APPLIED and effect["post_state_id"] != effect["pre_state_id"]:
            raise ValueError("a not-applied effect must record its pre-state as post-state")
    for effect_name, field, record_field in schema.get("record_links", ()):
        if effect_name in effects and effects[effect_name][field] != record[record_field]:
            raise ValueError(f"journal effect {effect_name} is not bound to the record {record_field}")
    proof = record["branch_proof"]
    if record["branch"] == "primary":
        if proof is not None:
            raise ValueError("the primary branch carries no branch proof")
    else:
        rule = schema["switch"][record["branch"]]
        if not isinstance(proof, dict) or proof != _expected_proof(record, rule):
            raise ValueError("the selected branch lacks its authoritative proof")
        if any(effects.get(name, {}).get("status") not in _VERIFIED for name in rule["verified"]) or any(
            name in effects for name in rule["absent"]
        ):
            raise ValueError("the selected branch lacks its required evidence")
    for effect_name, field, source_name, source_field in schema.get("links", ()):
        if (
            effect_name in effects and source_name in effects
            and effects[effect_name][field] != effects[source_name][source_field]
        ):
            raise ValueError(f"journal effect {effect_name} is not bound to {source_name}")
    for effect_name, field, value in schema.get("fixed", ()):
        if effect_name in effects and effects[effect_name][field] != value:
            raise ValueError(f"journal effect {effect_name} has an unexpected {field}")
    # Effects start strictly in order: every earlier effect is verified.
    for index, name in enumerate(order):
        if name in effects:
            for earlier in order[:index]:
                if effects.get(earlier, {}).get("status") not in _VERIFIED:
                    raise ValueError("journal effects started out of order")
    all_verified = all(
        effects.get(name, {}).get("status") in _VERIFIED
        and effects[name]["post_state_id"] == effects[name]["intended_post_state_id"]
        for name in order
    )
    if record["phase"] in {"complete", "completed_then_superseded"} and not all_verified:
        raise ValueError("a completed record requires every planned effect verified")
    if record["phase"] == "resolved_without_change" and any(
        effect["status"] in _VERIFIED for effect in effects.values()
    ):
        raise ValueError("a record with a verified effect cannot resolve without change")
    for key in _OPTIONAL_FIELDS:
        if key in record and record[key] is not None and not isinstance(record[key], str):
            raise ValueError("journal record generation is invalid")


def validate_transition(old: dict[str, Any], new: dict[str, Any], *, branch_switch: bool = False) -> None:
    """Reject any update that rewrites evidence or moves a phase backwards.

    Only the dedicated branch-switch path (branch_switch=True) may change the
    branch, planned effects, or branch proof, or create verified_not_applied.
    """

    validate_record(new, old["operation_id"])
    if old["phase"] in TERMINAL_PHASES:
        raise ValueError("terminal journal records are immutable")
    for key in _IMMUTABLE_FIELDS:
        if old[key] != new[key]:
            raise ValueError(f"journal field {key} is immutable")
    if new["phase"] != old["phase"] and new["phase"] not in _TRANSITIONS[old["phase"]]:
        raise ValueError("journal phase transition is not allowed")
    if not branch_switch:
        for key in ("branch", "branch_proof", "planned_effects"):
            if new[key] != old[key]:
                raise ValueError(f"journal field {key} changes only through a proven branch switch")
        for name, after in new["effects"].items():
            if after["status"] == NOT_APPLIED and old["effects"].get(name) != after:
                raise ValueError("an effect is proven not applied only by a branch switch")
    elif new["branch"] == old["branch"]:
        raise ValueError("a branch switch must change the branch")
    if new["branch"] != old["branch"]:
        rule = ACTION_SCHEMAS[old["action_kind"]].get("switch", {}).get(new["branch"])
        if old["branch"] != "primary" or rule is None:
            raise ValueError("journal branch switch is not allowed")
        for name, before in old["effects"].items():
            after = new["effects"].get(name)
            if after != before and not (
                before["status"] == "intent" and after is not None and after["status"] == NOT_APPLIED
            ):
                raise ValueError("a branch switch may only prove intents not applied")
        if set(new["effects"]) != set(old["effects"]):
            raise ValueError("a branch switch cannot add or remove effect evidence")
    elif new["branch_proof"] != old["branch_proof"]:
        raise ValueError("branch proof is immutable")
    for name, after in new["effects"].items():
        before = old["effects"].get(name)
        if after["status"] == NOT_APPLIED and (before is None or before["status"] != NOT_APPLIED) and (
            new["branch"] == old["branch"]
        ):
            raise ValueError("an effect is proven not applied only by a branch switch")
    for name, before in old["effects"].items():
        after = new["effects"].get(name)
        if after is None:
            raise ValueError("journal effect records cannot be removed")
        for key in ("sequence", "pre_state_id", "intended_post_state_id"):
            if before[key] != after[key]:
                raise ValueError("journal effect identity is immutable")
        if before["status"] != "intent" and before != after:
            raise ValueError("resolved journal effects are immutable")


def reconcile_effect(effect: dict[str, Any], current_state_id: str) -> str:
    """Classify one recorded effect against the authoritative store's state ID."""

    if effect.get("status") in _VERIFIED:
        if current_state_id == effect.get("post_state_id"):
            return "verified"
        return "changed_after_verify"
    if effect.get("status") == "readback_mismatch":
        return "state_conflict"
    before = effect.get("pre_state_id")
    intended = effect.get("intended_post_state_id")
    if not isinstance(before, str) or not isinstance(intended, str):
        return "insufficient_evidence"
    if current_state_id == intended:
        return "effect_applied_needs_verification"
    if current_state_id == before:
        return "effect_not_applied"
    return "state_conflict"


def reconcile_operation(
    record: dict[str, Any], observers: dict[str, Callable[[], str]],
) -> dict[str, Any]:
    """Plan recovery from recorded pre/post state IDs only; never guess.

    Returns each planned effect's classification and one next action:
    complete (every effect verified and unchanged), resume:<effect> (every
    earlier effect is verified and this one provably did not happen or never
    started), verify:<effect> (the store shows the recorded intended state), or
    manual_review for any conflict, later drift, or missing evidence.
    """

    effects = record["effects"]
    classification: dict[str, str] = {}
    next_action: str | None = None
    for name in record["planned_effects"]:
        effect = effects.get(name)
        if effect is None:
            classification[name] = "not_started"
            if next_action is None:
                next_action = f"resume:{name}"
            continue
        observer = observers.get(name)
        if observer is None:
            classification[name] = "insufficient_evidence"
            next_action = "manual_review"
            continue
        state = reconcile_effect(effect, observer())
        classification[name] = state
        if state == "verified" and next_action is None:
            continue
        if next_action is not None or state in {
            "state_conflict", "changed_after_verify", "insufficient_evidence",
        }:
            next_action = "manual_review"
        elif state == "effect_applied_needs_verification":
            next_action = f"verify:{name}"
        elif state == "effect_not_applied":
            next_action = f"resume:{name}"
    return {"effects": classification, "next_action": next_action or "complete"}


def replacement_recovery_action(
    record: dict[str, Any], *, active_state: str, config_generation: str,
    backup_state: str | None = None,
) -> str:
    """Choose the recorded repair for an interrupted credential replacement.

    Uses only recorded version IDs and current authoritative readbacks:
    restore_backup when the exact staged version is active and config still
    has the original generation; continue_commit when config already has the
    candidate generation and the staged version is active; the generic
    effect-level plan before promotion; manual_review otherwise.
    """

    if record["action_kind"] not in {"credential_replacement", "fake_credential_replacement"}:
        raise ValueError("not a credential replacement record")
    effects = record["effects"]
    promote = effects.get("credential_promote")
    if record["branch"] == "restore":
        # The historical branch proof is not enough: every current guard must
        # still hold for the next restore-branch effect.
        backup = effects["credential_backup"]
        restore = effects.get("credential_restore")
        delete = effects.get("backup_delete")
        if config_generation != record["original_generation"] or backup_state is None:
            return "manual_review"
        if restore is None or restore["status"] == "intent":
            active_ok = active_state == promote["post_state_id"] or (
                restore is not None and active_state == restore["intended_post_state_id"])
            return "continue_restore" if active_ok and backup_state == backup["post_state_id"] else "manual_review"
        if restore["status"] not in _VERIFIED or active_state != restore["post_state_id"]:
            return "manual_review"
        if backup_state == backup["post_state_id"] or (
            delete is not None and delete["status"] == "intent" and backup_state == ABSENT_STATE
        ):
            return "continue_restore"
        return "manual_review"
    if promote is not None and promote["status"] in _VERIFIED:
        if active_state != promote["post_state_id"]:
            return "manual_review"
        commit_verified = effects.get("config_commit", {}).get("status") in _VERIFIED
        if config_generation == record["original_generation"] and not commit_verified:
            return "restore_backup"
        if config_generation == record["candidate_hash"]:
            return "continue_commit"
        return "manual_review"
    if promote is not None:
        if config_generation != record["original_generation"]:
            return "manual_review"
        if active_state == promote["intended_post_state_id"]:
            return "verify:credential_promote"
        if active_state == promote["pre_state_id"]:
            return "resume:credential_promote"
        return "manual_review"
    if config_generation != record["original_generation"]:
        return "manual_review"
    return "resume_before_promotion"


def enrollment_recovery_action(
    record: dict[str, Any], *, config_generation: str, staged_state: str, active_state: str,
) -> str:
    """Choose the recorded repair for an interrupted credential enrollment.

    continue_commit when config already has the candidate generation (the CAS
    took effect, so the staged credential must be activated, never deleted);
    abandon when config is exactly original, promotion never started, and the
    staged slot still holds the recorded staged version; manual_review otherwise.
    """

    if record["action_kind"] not in {"credential_enrollment", "fake_credential_enrollment"}:
        raise ValueError("not a credential enrollment record")
    if record["branch"] == "abandon":
        effects = record["effects"]
        delete = effects.get("staged_delete")
        staged_ok = staged_state == effects["credential_stage"]["post_state_id"] or (
            delete is not None and delete["status"] == "intent" and staged_state == ABSENT_STATE)
        if config_generation == record["original_generation"] and staged_ok and active_state == ABSENT_STATE:
            return "continue_abandon"
        return "manual_review"
    effects = record["effects"]
    stage = effects.get("credential_stage")
    promote = effects.get("credential_promote")
    if config_generation == record["candidate_hash"] and "config_commit" in effects:
        if promote is not None and promote["status"] in _VERIFIED and active_state == promote["post_state_id"]:
            return "complete"
        return "continue_commit"
    if (
        config_generation == record["original_generation"] and promote is None
        and stage is not None and stage["status"] in _VERIFIED and staged_state == stage["post_state_id"]
    ):
        return "abandon"
    return "manual_review"


def _require_branch_locks(record: dict[str, Any], role_locks: Any, config_lock: Any) -> None:
    if not getattr(role_locks, "is_held", False) or list(role_locks.keys) != record["role_keys"]:
        raise JournalError("ROLE_LOCK_REQUIRED", "The credential role lock must be held.")
    bound = Path(record["binding"]["config_path"]).expanduser().absolute()
    if not getattr(config_lock, "is_held", False) or getattr(config_lock, "config_path", None) != bound:
        raise JournalError("CONFIG_LOCK_REQUIRED", "The settings file lock must be held.")


def _expected_proof(record: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for key, source in rule["proof"].items():
        if source[0] == "record":
            expected[key] = record[source[1]]
        elif source[0] == "binding":
            expected[key] = record["binding"][source[1]]
        elif source[0] == "constant":
            expected[key] = source[1]
        else:
            effect = record["effects"].get(source[1])
            expected[key] = effect.get(source[2]) if isinstance(effect, dict) else None
    return expected


def _fault(hook: Callable[[str], None] | None, point: str) -> None:
    if hook is not None:
        hook(point)


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and len(item) <= 128 for item in value)


def _json_bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("journal write returned no data")
        view = view[count:]


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "ABSENT_STATE",
    "ACTION_SCHEMAS",
    "DEFERRED_ACTION_KINDS",
    "FAKE_ACTION_KINDS",
    "LIVE_ACTION_KINDS",
    "NOT_APPLIED",
    "SCHEMA_VERSION",
    "TERMINAL_PHASES",
    "JournalError",
    "JournalOperation",
    "JournalStore",
    "OperationInProgress",
    "OperationLockRequired",
    "RoleLockSet",
    "SimulatedCrash",
    "canonical_role_key",
    "enrollment_recovery_action",
    "journal_directory",
    "reconcile_effect",
    "reconcile_operation",
    "replacement_recovery_action",
    "validate_record",
    "validate_transition",
]
