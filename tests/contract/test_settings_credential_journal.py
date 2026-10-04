"""Credential journal machines (parent plan): enrollment, replacement, forget, role binding.

Stores are fake file slots identified by content hash (or "absent"). Recovery
steps are reconstructed from the record's binding and recorded state IDs only.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from uls.config.mutation import ConfigFileLock
from uls.settings.journal import (
    ABSENT_STATE,
    ACTION_SCHEMAS,
    NOT_APPLIED,
    JournalError,
    JournalStore,
    OperationInProgress,
    OperationLockRequired,
    SimulatedCrash,
    _expected_proof,
    canonical_role_key,
    enrollment_recovery_action,
    replacement_recovery_action,
    validate_record,
)

pytestmark = pytest.mark.contract
OLD, NEW, CFG0, CFG1 = b"old-secret-value", b"new-secret-value", b"config: 0\n", b"config: 1\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Slot:
    """One authoritative store slot; its state ID is a content hash or absent."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def state(self) -> str:
        return _sha(self.path.read_bytes()) if self.path.exists() else ABSENT_STATE

    def write(self, data: bytes) -> None:
        self.path.write_bytes(data)

    def read(self) -> bytes:
        return self.path.read_bytes()

    def delete(self) -> None:
        self.path.unlink()


def _binding(kind: str, tmp_path: Path) -> dict[str, str]:
    binding = {"provider": "canvas", "profile": "default", "role": "token",
               "store_locator": str(tmp_path / "active.bin"),
               "config_path": str(tmp_path / "config.bin"), "config_dir_id": "1:2"}
    if kind in {"fake_credential_enrollment", "fake_credential_replacement"}:
        binding["staging_locator"] = str(tmp_path / "staged.bin")
    if kind == "fake_credential_replacement":
        binding["backup_locator"] = str(tmp_path / "backup.bin")
    return binding


def _create(journal: JournalStore, kind: str, tmp_path: Path) -> str:
    binding = _binding(kind, tmp_path)
    with journal.role_locks([canonical_role_key(binding)]) as roles:
        return journal.create_operation(
            action_kind=kind, binding=binding, original_generation=_sha(CFG0),
            candidate_hash=_sha(CFG1), fields=["canvas.token"], role_locks=roles,
            allow_unreleased=True,
        )


def _slots(record: dict) -> dict[str, Slot]:
    binding = record["binding"]
    names = {"active": "store_locator", "staged": "staging_locator", "backup": "backup_locator",
             "config": "config_path"}
    return {name: Slot(binding[key]) for name, key in names.items() if key in binding}


def _crash_at(point):
    def hook(name):
        if name == point:
            raise SimulatedCrash(name)
    return hook


def _observers(s):
    return {name: slot.state for name, slot in s.items()}


def _held(roles, lock, s):
    """Alternate-branch effects run only with both locks held and live guard readers."""

    return {"role_locks": roles, "config_lock": lock, "guards": _observers(s)}


def _recover(journal, operation_id):
    """Hold role lock -> record lock -> config lock (the fixed order) for a recovery step."""

    record = journal.read(operation_id)
    s = _slots(record)
    roles = journal.role_locks(record["role_keys"])
    op = journal.operation(operation_id)
    lock = ConfigFileLock(s["config"].path)
    return record, s, roles, op, lock


# ---------------------------------------------------------------- enrollment

def _enroll(journal, tmp_path, fault=None):
    Slot(tmp_path / "config.bin").write(CFG0)
    operation_id = _create(journal, "fake_credential_enrollment", tmp_path)
    s = _slots(journal.read(operation_id))
    with journal.operation(operation_id) as op:
        op.run_effect("credential_stage", pre_state=s["staged"].state(), intended_post_state=_sha(NEW),
                      perform=lambda: s["staged"].write(NEW), observe=s["staged"].state, fault_hook=fault)
        with ConfigFileLock(s["config"].path):
            op.run_effect("config_commit", pre_state=s["config"].state(), intended_post_state=_sha(CFG1),
                          perform=lambda: s["config"].write(CFG1), observe=s["config"].state,
                          fault_hook=fault)
        op.run_effect("credential_promote", pre_state=s["active"].state(), intended_post_state=_sha(NEW),
                      perform=lambda: s["active"].write(s["staged"].read()), observe=s["active"].state,
                      fault_hook=fault)
        op.update(phase="complete", next_action="none")
    return operation_id


ENROLL_POINTS = [f"{edge}_{name}" if edge != "recorded" else f"after_{name}_recorded"
                 for name in ("credential_stage", "config_commit", "credential_promote")
                 for edge in ("before", "after", "recorded")]


@pytest.mark.parametrize("point", ENROLL_POINTS)
def test_enrollment_never_activates_a_credential_before_the_config_cas(tmp_path, point):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _enroll(journal, tmp_path, _crash_at(point))
    [pending] = JournalStore(tmp_path).unresolved()
    record = JournalStore(tmp_path).read(pending["operation_id"])
    s = _slots(record)
    commit = record["effects"].get("config_commit", {})
    if commit.get("status") != "verified":
        # Until the config/binding CAS is verified, the active slot is untouched.
        assert s["active"].state() == ABSENT_STATE
        assert "credential_promote" not in record["effects"]
    assert record["planned_effects"] == ["credential_stage", "config_commit", "credential_promote"]


def test_enrollment_cannot_promote_out_of_order(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _enroll(journal, tmp_path, _crash_at("after_credential_stage_recorded"))
    [pending] = journal.unresolved()
    s = _slots(journal.read(pending["operation_id"]))
    with journal.operation(pending["operation_id"]) as op, pytest.raises(JournalError) as error:
        op.run_effect("credential_promote", pre_state=ABSENT_STATE, intended_post_state=_sha(NEW),
                      perform=lambda: s["active"].write(NEW), observe=s["active"].state)
    assert error.value.code == "EFFECT_OUT_OF_ORDER"
    assert s["active"].state() == ABSENT_STATE


def test_enrollment_abandoned_before_cas_removes_only_the_staged_copy(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _enroll(journal, tmp_path, _crash_at("before_config_commit"))
    [pending] = journal.unresolved()
    record = journal.read(pending["operation_id"])
    s = _slots(record)
    staged_version = record["effects"]["credential_stage"]["post_state_id"]
    assert enrollment_recovery_action(record, config_generation=s["config"].state(),
                                      staged_state=s["staged"].state(),
                                      active_state=s["active"].state()) == "abandon"
    _record, _s, roles, op, lock = _recover(journal, pending["operation_id"])
    with roles, op, lock:
        with pytest.raises(ValueError):
            op.update(phase="complete")  # the primary branch is not finished
        switched = op.switch_branch("abandon", role_locks=roles, config_lock=lock,
                                    observe=_observers(s), next_action="run_staged_delete")
        # The unapplied config intent is durably proven not applied, with its proof.
        assert switched["effects"]["config_commit"]["status"] == NOT_APPLIED
        assert switched["branch_proof"] == {"config": _sha(CFG0), "staged": staged_version}
        op.run_effect("staged_delete", pre_state=staged_version, intended_post_state=ABSENT_STATE,
                      perform=s["staged"].delete, observe=s["staged"].state, **_held(roles, lock, s))
        op.update(phase="complete", next_action="none")
    assert s["staged"].state() == ABSENT_STATE and s["active"].state() == ABSENT_STATE
    assert s["config"].read() == CFG0
    assert journal.unresolved() == []
    done = journal.read(pending["operation_id"])
    broken = {**done, "effects": {**done["effects"], "config_commit": {
        **done["effects"]["config_commit"], "status": "intent", "post_state_id": None}}}
    with pytest.raises(ValueError):
        validate_record(broken, pending["operation_id"])  # unresolved out-of-branch intent


@pytest.mark.parametrize("point", ["after_config_commit", "after_config_commit_recorded"])
def test_enrollment_cannot_abandon_once_the_config_write_happened(tmp_path, point):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _enroll(journal, tmp_path, _crash_at(point))
    [pending] = journal.unresolved()
    record, s, roles, op, lock = _recover(journal, pending["operation_id"])
    # The write reached disk (recorded or not): the only repair is to continue.
    assert s["config"].read() == CFG1
    assert enrollment_recovery_action(record, config_generation=s["config"].state(),
                                      staged_state=s["staged"].state(),
                                      active_state=s["active"].state()) == "continue_commit"
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.switch_branch("abandon", role_locks=roles, config_lock=lock, observe=_observers(s),
                         next_action="run_staged_delete")
    assert error.value.code == "BRANCH_PROOF_FAILED"
    assert s["staged"].read() == NEW
    assert journal.read(pending["operation_id"]) == record


def test_abandon_requires_the_recorded_staged_version_and_both_locks(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _enroll(journal, tmp_path, _crash_at("before_config_commit"))
    [pending] = journal.unresolved()
    record, s, roles, op, lock = _recover(journal, pending["operation_id"])
    with op:
        with pytest.raises(JournalError) as no_role:
            op.switch_branch("abandon", role_locks=roles, config_lock=lock, observe=_observers(s),
                             next_action="x")
        assert no_role.value.code == "ROLE_LOCK_REQUIRED"
    with roles, op:
        with pytest.raises(JournalError) as no_config:
            op.switch_branch("abandon", role_locks=roles, config_lock=lock, observe=_observers(s),
                             next_action="x")
        assert no_config.value.code == "CONFIG_LOCK_REQUIRED"
    s["staged"].write(b"tampered")
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.switch_branch("abandon", role_locks=roles, config_lock=lock, observe=_observers(s),
                         next_action="x")
    assert error.value.code == "BRANCH_PROOF_FAILED"
    assert journal.read(pending["operation_id"]) == record


def test_successful_enrollment_completes_with_every_store_verified(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _enroll(journal, tmp_path)
    record = journal.read(operation_id)
    assert record["phase"] == "complete"
    assert {name: e["status"] for name, e in record["effects"].items()} == {
        "credential_stage": "verified", "config_commit": "verified", "credential_promote": "verified"}
    assert _slots(record)["active"].read() == NEW


# --------------------------------------------------------------- replacement

def _replace(journal, tmp_path, fault=None, *, stop_before=None):
    Slot(tmp_path / "config.bin").write(CFG0)
    Slot(tmp_path / "active.bin").write(OLD)
    operation_id = _create(journal, "fake_credential_replacement", tmp_path)
    s = _slots(journal.read(operation_id))
    with journal.operation(operation_id) as op:
        op.run_effect("credential_stage", pre_state=s["staged"].state(), intended_post_state=_sha(NEW),
                      perform=lambda: s["staged"].write(NEW), observe=s["staged"].state, fault_hook=fault)
        with ConfigFileLock(s["config"].path):
            op.run_effect("credential_backup", pre_state=ABSENT_STATE, intended_post_state=s["active"].state(),
                          perform=lambda: s["backup"].write(s["active"].read()), observe=s["backup"].state,
                          fault_hook=fault)
            op.run_effect("credential_promote", pre_state=s["active"].state(), intended_post_state=_sha(NEW),
                          perform=lambda: s["active"].write(s["staged"].read()), observe=s["active"].state,
                          fault_hook=fault)
            op.run_effect("config_commit", pre_state=s["config"].state(), intended_post_state=_sha(CFG1),
                          perform=lambda: s["config"].write(CFG1), observe=s["config"].state,
                          fault_hook=fault)
        if stop_before == "backup_delete":
            return operation_id
        backup_version = op.read()["effects"]["credential_backup"]["post_state_id"]
        op.run_effect("backup_delete", pre_state=backup_version, intended_post_state=ABSENT_STATE,
                      perform=s["backup"].delete, observe=s["backup"].state, fault_hook=fault)
        op.update(phase="complete", next_action="none")
    return operation_id


def test_successful_replacement_requires_verified_backup_deletion_before_complete(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _replace(journal, tmp_path, stop_before="backup_delete")
    with journal.operation(operation_id) as op, pytest.raises(ValueError):
        op.update(phase="complete")  # backup still present: not complete
    s = _slots(journal.read(operation_id))
    assert s["backup"].read() == OLD
    with journal.operation(operation_id) as op:
        backup_version = op.read()["effects"]["credential_backup"]["post_state_id"]
        with pytest.raises(JournalError) as mismatch:
            # A deletion that does not read back as absent never completes.
            op.run_effect("backup_delete", pre_state=backup_version, intended_post_state=ABSENT_STATE,
                          perform=lambda: None, observe=s["backup"].state)
        assert mismatch.value.code == "EFFECT_READBACK_MISMATCH"
    assert journal.read(operation_id)["phase"] == "repair_required"

    second = tmp_path / "second"
    second.mkdir()
    journal2 = JournalStore(second)
    done = journal2.read(_replace(journal2, second))
    s2 = _slots(done)
    assert done["phase"] == "complete"
    assert done["effects"]["backup_delete"]["post_state_id"] == ABSENT_STATE
    assert s2["backup"].state() == ABSENT_STATE and s2["active"].read() == NEW and s2["config"].read() == CFG1


def test_replacement_crash_after_promotion_before_config_restores_the_exact_backup(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    restarted = JournalStore(tmp_path)
    [pending] = restarted.unresolved()
    record = restarted.read(pending["operation_id"])
    s = _slots(record)  # recovery uses only the record's binding
    assert s["active"].read() == NEW and s["config"].read() == CFG0
    action = replacement_recovery_action(record, active_state=s["active"].state(),
                                         config_generation=s["config"].state())
    assert action == "restore_backup"
    effects = record["effects"]
    _r, _s, roles, op, lock = _recover(restarted, pending["operation_id"])
    with roles, op, lock:
        switched = op.switch_branch("restore", role_locks=roles, config_lock=lock, observe=_observers(s),
                                    next_action="run_credential_restore")
        assert switched["branch_proof"] == {
            "config": _sha(CFG0), "active": effects["credential_promote"]["post_state_id"],
            "backup": effects["credential_backup"]["post_state_id"]}
        with pytest.raises(ValueError):
            # On the restore branch, config_commit is no longer a planned effect.
            op.run_effect("config_commit", pre_state=_sha(CFG0), intended_post_state=_sha(CFG1),
                          perform=lambda: None, observe=s["config"].state)
        with pytest.raises(ValueError):
            # Branches are mutually exclusive.
            op.switch_branch("primary", role_locks=roles, config_lock=lock, observe=_observers(s),
                             next_action="none")
    with roles, op, lock:
        assert op.read()["branch"] == "restore"
        with pytest.raises(ValueError):
            # Restoring anything other than the exact backed-up version is refused
            # before the store is touched.
            op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                          intended_post_state=_sha(b"some-other-value"),
                          perform=lambda: s["active"].write(b"some-other-value"), observe=s["active"].state,
                          **_held(roles, lock, s))
        assert s["active"].read() == NEW
        op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                      intended_post_state=effects["credential_backup"]["post_state_id"],
                      perform=lambda: s["active"].write(s["backup"].read()), observe=s["active"].state,
                      **_held(roles, lock, s))
        op.run_effect("backup_delete", pre_state=effects["credential_backup"]["post_state_id"],
                      intended_post_state=ABSENT_STATE, perform=s["backup"].delete, observe=s["backup"].state,
                      **_held(roles, lock, s))
        op.update(phase="complete", next_action="none")
    assert s["active"].read() == OLD  # exactly the prior known-good value
    assert s["backup"].state() == ABSENT_STATE and s["config"].read() == CFG0
    assert restarted.unresolved() == []


@pytest.mark.parametrize("point", ["after_config_commit", "after_config_commit_recorded"])
def test_restore_is_refused_once_the_config_write_happened(tmp_path, point):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at(point))
    [pending] = journal.unresolved()
    record, s, roles, op, lock = _recover(journal, pending["operation_id"])
    assert s["config"].read() == CFG1  # applied, recorded or not
    assert replacement_recovery_action(record, active_state=s["active"].state(),
                                       config_generation=s["config"].state()) == "continue_commit"
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.switch_branch("restore", role_locks=roles, config_lock=lock, observe=_observers(s),
                         next_action="run_credential_restore")
    assert error.value.code == "BRANCH_PROOF_FAILED"
    assert s["active"].read() == NEW and s["backup"].read() == OLD
    assert journal.read(pending["operation_id"]) == record


def test_restore_requires_the_recorded_active_and_backup_versions(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    [pending] = journal.unresolved()
    record, s, roles, op, lock = _recover(journal, pending["operation_id"])
    for slot in ("backup", "active"):
        original = s[slot].read()
        s[slot].write(b"changed-elsewhere")
        with roles, op, lock, pytest.raises(JournalError) as error:
            op.switch_branch("restore", role_locks=roles, config_lock=lock, observe=_observers(s),
                             next_action="run_credential_restore")
        assert error.value.code == "BRANCH_PROOF_FAILED"
        s[slot].write(original)
    assert journal.read(pending["operation_id"]) == record


@pytest.mark.parametrize("point", ["after_credential_stage_recorded", "after_credential_backup_recorded",
                                   "after_credential_promote"])
def test_pre_commit_continuation_is_manual_unless_config_is_exactly_original(tmp_path, point):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at(point))
    [pending] = journal.unresolved()
    record = journal.read(pending["operation_id"])
    active = Slot(record["binding"]["store_locator"]).state()
    assert replacement_recovery_action(record, active_state=active,
                                       config_generation=_sha(CFG0)) != "manual_review"
    assert replacement_recovery_action(record, active_state=active,
                                       config_generation=_sha(b"config: other")) == "manual_review"


# ------------------------------------------- config effects bound to the record

CONFIG_EFFECT = {"fake_credential_enrollment": "config_commit", "fake_credential_replacement": "config_commit",
                 "fake_credential_forget": "config_detach"}


def _advance_to_config_effect(journal, kind, tmp_path):
    Slot(tmp_path / "config.bin").write(CFG0)
    Slot(tmp_path / "active.bin").write(OLD)
    operation_id = _create(journal, kind, tmp_path)
    s = _slots(journal.read(operation_id))
    with journal.operation(operation_id) as op:
        if kind != "fake_credential_forget":
            op.run_effect("credential_stage", pre_state=ABSENT_STATE, intended_post_state=_sha(NEW),
                          perform=lambda: s["staged"].write(NEW), observe=s["staged"].state)
        if kind == "fake_credential_replacement":
            op.run_effect("credential_backup", pre_state=ABSENT_STATE, intended_post_state=_sha(OLD),
                          perform=lambda: s["backup"].write(OLD), observe=s["backup"].state)
            op.run_effect("credential_promote", pre_state=_sha(OLD), intended_post_state=_sha(NEW),
                          perform=lambda: s["active"].write(NEW), observe=s["active"].state)
    return operation_id, s


@pytest.mark.parametrize("kind", sorted(CONFIG_EFFECT))
@pytest.mark.parametrize(("pre", "intended"), [
    (CFG0, b"config: 2\n"),         # correct authoritative pre-state, wrong intended generation
    (b"config: 9\n", CFG1),          # wrong recorded pre-state, correct intended generation
])
def test_config_effects_must_carry_the_record_generations(tmp_path, kind, pre, intended):
    journal = JournalStore(tmp_path)
    operation_id, s = _advance_to_config_effect(journal, kind, tmp_path)
    touched: list[str] = []

    def perform():
        touched.append("perform")
        s["config"].write(intended)

    def observe():
        touched.append("observe")
        return s["config"].state()

    before = journal.read(operation_id)
    with journal.operation(operation_id) as op, pytest.raises(ValueError):
        op.run_effect(CONFIG_EFFECT[kind], pre_state=_sha(pre), intended_post_state=_sha(intended),
                      perform=perform, observe=observe)
    assert touched == []  # rejected before the config store was read or written
    assert s["config"].read() == CFG0
    assert journal.read(operation_id) == before


@pytest.mark.parametrize("generations", [("g", "1" * 64), ("0" * 64, "0" * 64), ("0" * 64, "XYZ")])
def test_credential_records_require_distinct_sha256_generations(tmp_path, generations):
    journal = JournalStore(tmp_path)
    binding = _binding("fake_credential_enrollment", tmp_path)
    with journal.role_locks([canonical_role_key(binding)]) as roles, pytest.raises(ValueError):
        journal.create_operation(action_kind="fake_credential_enrollment", binding=binding,
                                 original_generation=generations[0], candidate_hash=generations[1],
                                 fields=[], role_locks=roles, allow_unreleased=True)


def test_replacement_with_unexpected_versions_stays_manual(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    [pending] = journal.unresolved()
    record = journal.read(pending["operation_id"])
    assert replacement_recovery_action(record, active_state=_sha(b"someone-else"),
                                       config_generation=_sha(CFG0)) == "manual_review"
    assert replacement_recovery_action(record, active_state=_sha(NEW),
                                       config_generation=_sha(b"config: other")) == "manual_review"


def test_replacement_backup_must_capture_the_active_version(tmp_path):
    journal = JournalStore(tmp_path)
    Slot(tmp_path / "active.bin").write(OLD)
    operation_id = _create(journal, "fake_credential_replacement", tmp_path)
    s = _slots(journal.read(operation_id))
    with journal.operation(operation_id) as op:
        op.run_effect("credential_stage", pre_state=ABSENT_STATE, intended_post_state=_sha(NEW),
                      perform=lambda: s["staged"].write(NEW), observe=s["staged"].state)
        op.run_effect("credential_backup", pre_state=ABSENT_STATE, intended_post_state=_sha(b"not-active"),
                      perform=lambda: s["backup"].write(b"not-active"), observe=s["backup"].state)
        with pytest.raises(ValueError):
            # Promotion must start from exactly the version the backup preserved.
            op.run_effect("credential_promote", pre_state=s["active"].state(), intended_post_state=_sha(NEW),
                          perform=lambda: s["active"].write(NEW), observe=s["active"].state)
    assert s["active"].read() == OLD


# -------------------------------------------------------------------- forget

def test_forget_detaches_config_before_deleting_and_deletion_must_read_back_absent(tmp_path):
    journal = JournalStore(tmp_path)
    Slot(tmp_path / "config.bin").write(CFG0)
    Slot(tmp_path / "active.bin").write(OLD)
    operation_id = _create(journal, "fake_credential_forget", tmp_path)
    s = _slots(journal.read(operation_id))
    with journal.operation(operation_id) as op:
        with pytest.raises(JournalError) as order:
            op.run_effect("credential_delete", pre_state=_sha(OLD), intended_post_state=ABSENT_STATE,
                          perform=s["active"].delete, observe=s["active"].state)
        assert order.value.code == "EFFECT_OUT_OF_ORDER"
        assert s["active"].read() == OLD
        op.run_effect("config_detach", pre_state=_sha(CFG0), intended_post_state=_sha(CFG1),
                      perform=lambda: s["config"].write(CFG1), observe=s["config"].state)
        with pytest.raises(ValueError):
            op.run_effect("credential_delete", pre_state=_sha(OLD), intended_post_state=_sha(b"x"),
                          perform=lambda: None, observe=s["active"].state)
        op.run_effect("credential_delete", pre_state=_sha(OLD), intended_post_state=ABSENT_STATE,
                      perform=s["active"].delete, observe=s["active"].state)
        op.update(phase="complete", next_action="none")
    assert s["active"].state() == ABSENT_STATE and s["config"].read() == CFG1


# -------------------------------------------------------------- role binding

def test_role_lock_must_equal_the_binding_derivation(tmp_path):
    journal = JournalStore(tmp_path)
    binding = _binding("fake_credential_replacement", tmp_path)
    assert canonical_role_key(binding) == "canvas/default/token"
    for keys in (["canvas/other/token"], ["canvas/default/token", "notion/default/worker"]):
        with journal.role_locks(keys) as roles, pytest.raises(JournalError) as error:
            journal.create_operation(action_kind="fake_credential_replacement", binding=binding,
                                     original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                     role_locks=roles, allow_unreleased=True)
        assert error.value.code == "ROLE_BINDING_MISMATCH"
    with pytest.raises(JournalError):
        journal.create_operation(action_kind="fake_credential_replacement", binding=binding,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 allow_unreleased=True)
    with pytest.raises(ValueError):
        canonical_role_key({**binding, "profile": "bad profile/../x"})
    with pytest.raises(JournalError) as deferred, journal.role_locks(["canvas/default/token"]) as roles:
        journal.create_operation(action_kind="fake_credential_replacement", binding=binding,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 role_locks=roles)
    assert deferred.value.code == "FEATURE_DEFERRED"


def test_same_binding_role_is_reserved_whatever_lock_a_caller_submits(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    other_locators = {**_binding("fake_credential_enrollment", tmp_path),
                      "store_locator": str(tmp_path / "elsewhere.bin")}
    with journal.role_locks(["canvas/default/token"]) as roles, pytest.raises(OperationInProgress):
        journal.create_operation(action_kind="fake_credential_enrollment", binding=other_locators,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 role_locks=roles, allow_unreleased=True)
    with journal.role_locks(["canvas/default/other"]) as roles, pytest.raises(JournalError) as error:
        journal.create_operation(action_kind="fake_credential_enrollment", binding=other_locators,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 role_locks=roles, allow_unreleased=True)
    assert error.value.code == "ROLE_BINDING_MISMATCH"


# ------------------------------------------ sealed branch transitions (REQ1)

@pytest.mark.parametrize(("kind", "branch"), [("fake_credential_enrollment", "abandon"),
                                               ("fake_credential_replacement", "restore")])
def test_generic_update_cannot_forge_a_branch_switch_after_the_config_write(tmp_path, kind, branch):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        (_enroll if kind == "fake_credential_enrollment" else _replace)(
            journal, tmp_path, _crash_at("after_config_commit"))
    [pending] = journal.unresolved()
    path = journal.directory / f"{pending['operation_id']}.json"
    before_bytes = path.read_bytes()
    record = journal.read(pending["operation_id"])
    assert Slot(record["binding"]["config_path"]).read() == CFG1  # the write reached disk
    rule = ACTION_SCHEMAS[kind]["switch"][branch]
    commit = record["effects"]["config_commit"]
    forged_effects = {**record["effects"], "config_commit": {
        **commit, "status": NOT_APPLIED, "post_state_id": commit["pre_state_id"]}}
    forged = {"branch": branch, "branch_proof": _expected_proof(record, rule),
              "planned_effects": list(ACTION_SCHEMAS[kind]["branches"][branch]), "effects": forged_effects}
    with journal.operation(pending["operation_id"]) as op:  # record lock only: no role/config lock
        for attempt in (forged, {"effects": forged_effects},
                        {"branch_proof": forged["branch_proof"]}, {"branch": branch}):
            with pytest.raises(ValueError):
                op.update(**attempt)
    assert path.read_bytes() == before_bytes


# ------------------------------- alternate-branch effects re-prove guards (REQ2)

def _switched(journal, tmp_path, kind):
    runner, point, branch = ((_enroll, "before_config_commit", "abandon") if kind == "enroll"
                             else (_replace, "after_credential_promote_recorded", "restore"))
    with pytest.raises(SimulatedCrash):
        runner(journal, tmp_path, _crash_at(point))
    [pending] = journal.unresolved()
    _record, s, roles, op, lock = _recover(journal, pending["operation_id"])
    with roles, op, lock:
        op.switch_branch(branch, role_locks=roles, config_lock=lock, observe=_observers(s), next_action="x")
    # ...the process dies here; its locks die with it.
    return pending["operation_id"]


def _snapshot(s):
    return {name: slot.state() for name, slot in s.items() if name != "config"}


def test_abandon_resumed_after_a_config_change_touches_nothing(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _switched(journal, tmp_path, "enroll")
    record, s, roles, op, lock = _recover(journal, operation_id)
    s["config"].write(b"config: other writer\n")  # a config-only writer after the crash
    stores = _snapshot(s)
    assert enrollment_recovery_action(record, config_generation=s["config"].state(),
                                      staged_state=s["staged"].state(),
                                      active_state=s["active"].state()) == "manual_review"
    staged = record["effects"]["credential_stage"]["post_state_id"]
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.run_effect("staged_delete", pre_state=staged, intended_post_state=ABSENT_STATE,
                      perform=s["staged"].delete, observe=s["staged"].state, **_held(roles, lock, s))
    assert error.value.code == "BRANCH_GUARD_FAILED"
    with op, pytest.raises(JournalError) as unlocked:
        op.run_effect("staged_delete", pre_state=staged, intended_post_state=ABSENT_STATE,
                      perform=s["staged"].delete, observe=s["staged"].state)
    assert unlocked.value.code == "ROLE_LOCK_REQUIRED"
    assert _snapshot(s) == stores
    assert journal.read(operation_id) == record


def test_restore_resumed_after_a_config_change_touches_nothing(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _switched(journal, tmp_path, "replace")
    record, s, roles, op, lock = _recover(journal, operation_id)
    effects = record["effects"]
    assert replacement_recovery_action(record, active_state=s["active"].state(),
                                       config_generation=s["config"].state(),
                                       backup_state=s["backup"].state()) == "continue_restore"
    s["config"].write(b"config: other writer\n")
    stores = _snapshot(s)
    assert replacement_recovery_action(record, active_state=s["active"].state(),
                                       config_generation=s["config"].state(),
                                       backup_state=s["backup"].state()) == "manual_review"
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                      intended_post_state=effects["credential_backup"]["post_state_id"],
                      perform=lambda: s["active"].write(s["backup"].read()), observe=s["active"].state,
                      **_held(roles, lock, s))
    assert error.value.code == "BRANCH_GUARD_FAILED"
    assert _snapshot(s) == stores
    assert journal.read(operation_id) == record


def test_backup_delete_after_restore_rechecks_config_and_touches_nothing(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _switched(journal, tmp_path, "replace")
    record, s, roles, op, lock = _recover(journal, operation_id)
    effects = record["effects"]
    with pytest.raises(SimulatedCrash), roles, op, lock:
        op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                      intended_post_state=effects["credential_backup"]["post_state_id"],
                      perform=lambda: s["active"].write(s["backup"].read()), observe=s["active"].state,
                      fault_hook=_crash_at("after_credential_restore_recorded"), **_held(roles, lock, s))
    record = journal.read(operation_id)
    assert record["effects"]["credential_restore"]["status"] == "verified"
    s["config"].write(b"config: other writer\n")
    stores = _snapshot(s)
    assert replacement_recovery_action(record, active_state=s["active"].state(),
                                       config_generation=s["config"].state(),
                                       backup_state=s["backup"].state()) == "manual_review"
    with roles, op, lock, pytest.raises(JournalError) as error:
        op.run_effect("backup_delete", pre_state=record["effects"]["credential_backup"]["post_state_id"],
                      intended_post_state=ABSENT_STATE, perform=s["backup"].delete,
                      observe=s["backup"].state, **_held(roles, lock, s))
    assert error.value.code == "BRANCH_GUARD_FAILED"
    assert _snapshot(s) == stores and s["backup"].read() == OLD
    assert journal.read(operation_id) == record



# ------------------------------------------- per-operation record lock (round 3)

def _locked_trap():
    calls: list[str] = []

    def reader(name):
        def read():
            calls.append(name)
            return "x"
        return read

    return calls, reader


def test_switch_branch_without_the_operation_lock_fails_before_any_observer(tmp_path):
    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    [pending] = journal.unresolved()
    _record, _s, roles, op, lock = _recover(journal, pending["operation_id"])
    path = journal.directory / f"{pending['operation_id']}.json"
    before = path.read_bytes()
    calls, reader = _locked_trap()
    with roles, lock, pytest.raises(OperationLockRequired) as error:  # role + config locks, no record lock
        op.switch_branch("restore", role_locks=roles, config_lock=lock,
                         observe={name: reader(name) for name in ("config", "active", "backup")},
                         next_action="x")
    assert error.value.code == "OPERATION_LOCK_REQUIRED"
    assert calls == []
    assert path.read_bytes() == before


def test_run_effect_on_an_alternate_branch_intent_without_the_operation_lock_touches_nothing(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _switched(journal, tmp_path, "replace")
    record, s, roles, op, lock = _recover(journal, operation_id)
    effects = record["effects"]
    with pytest.raises(SimulatedCrash), roles, op, lock:  # leave credential_restore as an intent
        op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                      intended_post_state=effects["credential_backup"]["post_state_id"],
                      perform=lambda: None, observe=s["active"].state,
                      fault_hook=_crash_at("before_credential_restore"), **_held(roles, lock, s))
    path = journal.directory / f"{operation_id}.json"
    before = path.read_bytes()
    assert journal.read(operation_id)["effects"]["credential_restore"]["status"] == "intent"
    calls, reader = _locked_trap()
    with roles, lock, pytest.raises(OperationLockRequired):
        op.run_effect("credential_restore", pre_state=effects["credential_promote"]["post_state_id"],
                      intended_post_state=effects["credential_backup"]["post_state_id"],
                      perform=lambda: calls.append("perform"), observe=reader("observe"),
                      role_locks=roles, config_lock=lock,
                      guards={name: reader(name) for name in ("config", "active", "backup")})
    assert calls == []
    assert path.read_bytes() == before and s["active"].read() == NEW


_HOLDER = """
import sys, time
from uls.settings.journal import JournalStore
journal = JournalStore(sys.argv[1])
with journal.operation(sys.argv[2]):
    print('held', flush=True)
    time.sleep(float(sys.argv[3]))
"""


def test_a_lockless_switch_cannot_overwrite_a_record_held_by_another_process(tmp_path, monkeypatch):
    import subprocess
    import sys

    from uls.settings import journal as journal_module

    journal = JournalStore(tmp_path)
    with pytest.raises(SimulatedCrash):
        _replace(journal, tmp_path, _crash_at("after_credential_promote_recorded"))
    [pending] = journal.unresolved()
    path = journal.directory / f"{pending['operation_id']}.json"
    before = path.read_bytes()
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(tmp_path), pending["operation_id"], "5"],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        _record, s, roles, op, lock = _recover(journal, pending["operation_id"])
        with roles, lock:
            with pytest.raises(OperationLockRequired):
                op.switch_branch("restore", role_locks=roles, config_lock=lock, observe=_observers(s),
                                 next_action="x")
            monkeypatch.setattr(journal_module, "JOURNAL_LOCK_WAIT_SECONDS", 0.3)
            with pytest.raises(TimeoutError), op:
                pass  # the record lock belongs to the other process
        assert path.read_bytes() == before
    finally:
        holder.kill()
        holder.wait(timeout=10)
