"""Per-operation journal: schemas, transitions, role reservation, lock order, crash recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _settings_support import make_harness, reviewed_apply

from uls.config.mutation import ConfigFileLock
from uls.orchestration import locks
from uls.settings.journal import (
    ACTION_SCHEMAS,
    JournalError,
    JournalStore,
    OperationInProgress,
    SimulatedCrash,
    validate_record,
    validate_transition,
)

pytestmark = pytest.mark.contract
ROLE = "canvas/default/token"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fake_multi(journal, tmp_path):
    return journal.create_operation(
        action_kind="fake_multi_store_test",
        binding={"credential_store": str(tmp_path / "credential.bin"),
                 "config_store": str(tmp_path / "config.bin")},
        original_generation=_sha(b"config-v0"), candidate_hash=_sha(b"config-v1"),
        fields=["canvas.profile"], allow_unreleased=True,
    )


def _credential_binding(kind, tmp_path, profile="default"):
    binding = {"provider": "canvas", "profile": profile, "role": "token",
               "store_locator": str(tmp_path / "active.bin"),
               "config_path": str(tmp_path / "config.yaml"), "config_dir_id": "1:2"}
    if kind in {"credential_enrollment", "credential_replacement"}:
        binding["staging_locator"] = str(tmp_path / "staged.bin")
    if kind == "credential_replacement":
        binding["backup_locator"] = str(tmp_path / "backup.bin")
    return binding


def test_records_are_private_secret_free_and_schema_bound(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = _fake_multi(journal, tmp_path)
    path = journal.directory / f"{operation_id}.json"
    if os.name != "nt":
        assert journal.directory.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
    record = json.loads(path.read_text())
    assert record["schema_version"] == 3
    assert record["branch"] == "primary"
    assert record["planned_effects"] == ["credential_promote", "config_commit"]
    assert set(record["binding"]) == {"credential_store", "config_store"}
    with pytest.raises(ValueError), journal.operation(operation_id) as operation:
        operation.update(secret_value="nope")


@pytest.mark.parametrize("kind", ["credential_enrollment", "credential_replacement", "credential_forget"])
def test_credential_actions_are_deferred_but_their_schemas_validate(tmp_path, kind):
    journal = JournalStore(tmp_path)
    with pytest.raises(JournalError) as error:
        journal.create_operation(action_kind=kind, binding={}, original_generation="a",
                                 candidate_hash="b", fields=[])
    assert error.value.code == "FEATURE_DEFERRED"
    schema = ACTION_SCHEMAS[kind]
    binding = _credential_binding(kind, tmp_path)
    assert set(binding) == schema["binding"]
    record = {
        "schema_version": 3, "operation_id": "a" * 32, "action_kind": kind, "role_group": "credential",
        "role_keys": [ROLE], "binding": binding,
        "original_generation": "0" * 64, "candidate_hash": "1" * 64, "fields": [], "branch_proof": None,
        "branch": "primary", "planned_effects": list(schema["branches"]["primary"]),
        "phase": "prepare", "effects": {},
        "created_at": 1.0, "next_action": "inspect",
    }
    validate_record(record, "a" * 32)
    for broken in (
        {**record, "binding": {**record["binding"], "extra": "x"}},
        {**record, "planned_effects": list(reversed(schema["branches"]["primary"]))},
        {**record, "role_keys": []},
        {**record, "role_keys": ["canvas/other/token"]},
        {**record, "branch": "unknown"},
    ):
        with pytest.raises(ValueError):
            validate_record(broken, "a" * 32)
    with pytest.raises(JournalError):
        journal.create_operation(action_kind="fake_store_test", binding={"store": "s"},
                                 original_generation="a", candidate_hash="b", fields=[])


def _base_record(tmp_path):
    journal = JournalStore(tmp_path)
    operation_id = journal.create_operation(
        action_kind="fake_multi_store_test",
        binding={"credential_store": "c", "config_store": "k"},
        original_generation="g0", candidate_hash="g1", fields=[], allow_unreleased=True,
    )
    return journal, operation_id


def test_transition_validator_keeps_evidence_immutable_and_phases_monotonic(tmp_path):
    journal, operation_id = _base_record(tmp_path)

    def crash(point):
        if point == "before_credential_promote":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash), journal.operation(operation_id) as operation:
        operation.update(phase="candidate_validated")
        operation.update(phase="locked")
        operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                             perform=lambda: None, observe=lambda: "p", fault_hook=crash)
    old = journal.read(operation_id)
    effect = old["effects"]["credential_promote"]
    assert effect["status"] == "intent"
    bad_updates = [
        {**old, "candidate_hash": "other"},                        # immutable identity
        {**old, "binding": {"credential_store": "x", "config_store": "k"}},
        {**old, "phase": "prepare"},                               # backwards
        {**old, "phase": "complete"},                              # complete without verified effects
        {**old, "effects": {"credential_promote": {**effect, "pre_state_id": "z"}}},  # rebinding
        {**old, "effects": {}},                                    # removing evidence
        {**old, "effects": {**old["effects"], "config_commit": {**effect, "sequence": 1}}},  # out of order
    ]
    for new in bad_updates:
        with pytest.raises(ValueError):
            validate_transition(old, copy.deepcopy(new))


def test_existing_intent_resumes_only_from_its_persisted_pre_state(tmp_path):
    journal, operation_id = _base_record(tmp_path)
    state = {"cred": "p"}
    with journal.operation(operation_id) as operation:
        with pytest.raises(JournalError) as error:
            # The effect "runs" but the store never moves: readback mismatch.
            operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                                 perform=lambda: None, observe=lambda: state["cred"])
        assert error.value.code == "EFFECT_READBACK_MISMATCH"
    assert journal.read(operation_id)["phase"] == "repair_required"
    with journal.operation(operation_id) as operation, pytest.raises(JournalError) as error:
        operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                             perform=lambda: None, observe=lambda: "p")
    assert error.value.code == "EFFECT_ALREADY_RESOLVED"

    journal2, second = _base_record(tmp_path / "two")
    store = {"cred": "p"}

    def crash(point):
        if point == "before_credential_promote":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash), journal2.operation(second) as operation:
        operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                             perform=lambda: store.update(cred="q"), observe=lambda: store["cred"],
                             fault_hook=crash)
    with journal2.operation(second) as operation:
        with pytest.raises(JournalError) as mismatch:
            operation.run_effect("credential_promote", pre_state="p2", intended_post_state="q",
                                 perform=lambda: None, observe=lambda: store["cred"])
        assert mismatch.value.code == "RESUME_STATE_MISMATCH"
        store["cred"] = "drifted"
        with pytest.raises(JournalError) as drifted:
            operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                                 perform=lambda: None, observe=lambda: store["cred"])
        assert drifted.value.code == "PRE_STATE_MISMATCH"
        store["cred"] = "p"
        operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                             perform=lambda: store.update(cred="q"), observe=lambda: store["cred"])
        with pytest.raises(JournalError) as order:
            operation.run_effect("credential_promote", pre_state="p", intended_post_state="q",
                                 perform=lambda: None, observe=lambda: store["cred"])
        assert order.value.code == "EFFECT_ALREADY_RESOLVED"
        with pytest.raises(ValueError):
            operation.update(phase="complete")  # config_commit not verified yet
        operation.run_effect("config_commit", pre_state="k0", intended_post_state="k1",
                             perform=lambda: store.update(conf="k1"), observe=lambda: store.get("conf", "k0"))
        operation.update(phase="complete", next_action="none")
        with pytest.raises(ValueError):
            operation.update(next_action="again")  # terminal records are immutable


_WRITER = """
import sys
from uls.settings.journal import JournalStore
journal = JournalStore(sys.argv[1])
ids = []
for index in range(15):
    op = journal.create_operation(action_kind='fake_store_test', binding={'store': 's'},
                                  original_generation='0' * 64, candidate_hash='1' * 64, fields=[],
                                  allow_unreleased=True)
    store = {'value': 'p'}
    with journal.operation(op) as operation:
        operation.run_effect('effect_a', pre_state='p', intended_post_state='q',
                             perform=lambda: store.update(value='q'), observe=lambda: store['value'])
        operation.update(phase='complete', next_action='none')
    ids.append(op)
print(' '.join(ids))
"""


def test_concurrent_cross_process_writers_keep_every_record(tmp_path):
    JournalStore(tmp_path)
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, str(tmp_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(4)]
    ids = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        ids.extend(out.split())
    journal = JournalStore(tmp_path)
    assert len(ids) == len(set(ids)) == 60
    for operation_id in ids:
        record = journal.read(operation_id)
        assert record["phase"] == "complete"
        assert record["effects"]["effect_a"]["status"] == "verified"
    assert journal.unresolved() == []
    assert list(journal.directory.glob(".*.tmp")) == []


_RESERVER = """
import sys
from uls.settings.journal import JournalStore
journal = JournalStore(sys.argv[1])
binding = {'provider': 'canvas', 'profile': 'default', 'role': 'token', 'store_locator': 'a',
           'staging_locator': 's', 'backup_locator': 'b', 'config_path': 'c', 'config_dir_id': '1:2'}
with journal.role_locks([sys.argv[2]]) as roles:
    journal.create_operation(action_kind='credential_replacement', binding=binding,
                             original_generation='0' * 64, candidate_hash='1' * 64, fields=[],
                             role_locks=roles, allow_unreleased=True)
"""


def test_unresolved_role_record_blocks_overlapping_operation_across_processes(tmp_path):
    proc = subprocess.run([sys.executable, "-c", _RESERVER, str(tmp_path), ROLE], check=False,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    journal = JournalStore(tmp_path)
    same_role = {"provider": "canvas", "profile": "default", "role": "token", "store_locator": "a2",
                 "staging_locator": "s2", "config_path": "c", "config_dir_id": "1:2"}

    def enroll(binding, lock_key):
        with journal.role_locks([lock_key]) as roles:
            return journal.create_operation(
                action_kind="credential_enrollment", binding=binding, original_generation="0" * 64,
                candidate_hash="1" * 64, fields=[], role_locks=roles, allow_unreleased=True)

    with journal.role_locks([ROLE]) as roles, pytest.raises(OperationInProgress) as error:
        journal.create_operation(action_kind="credential_enrollment", binding=same_role,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 role_locks=roles, allow_unreleased=True)
    assert error.value.code == "OPERATION_IN_PROGRESS"
    # Same binding role, but the caller submits a different lock: rejected outright.
    with pytest.raises(JournalError) as mismatch:
        enroll(same_role, "canvas/default/other")
    assert mismatch.value.code == "ROLE_BINDING_MISMATCH"
    other_role = {**same_role, "provider": "notion", "role": "worker"}
    enroll(other_role, "notion/default/worker")
    with pytest.raises(RuntimeError):
        journal.create_operation(action_kind="credential_enrollment", binding=same_role,
                                 original_generation="0" * 64, candidate_hash="1" * 64, fields=[],
                                 role_locks=journal.role_locks([ROLE]), allow_unreleased=True)


def test_config_apply_takes_record_lock_before_config_lock(tmp_path, monkeypatch):
    h = make_harness(tmp_path)
    events: list[tuple[str, str]] = []
    original_acquire = locks.LocalWorkerLock.acquire
    original_release = locks.LocalWorkerLock.release

    def acquire(self, timeout=0.0):
        result = original_acquire(self, timeout)
        if result:
            events.append(("acquire", self.path.name))
        return result

    def release(self):
        if self.is_held:
            events.append(("release", self.path.name))
        original_release(self)

    monkeypatch.setattr(locks.LocalWorkerLock, "acquire", acquire)
    monkeypatch.setattr(locks.LocalWorkerLock, "release", release)
    events.clear()
    result = reviewed_apply(h.store, "general", {"system.timezone": "UTC"}, h.journal)
    config_lock = f".{h.config_path.name}.uls-config.lock"
    record_lock = f"{result['operation_id']}.lock"
    nested = [event for event in events if event[1] in {config_lock, record_lock}]
    assert nested[-4:] == [
        ("acquire", record_lock), ("acquire", config_lock),
        ("release", config_lock), ("release", record_lock),
    ]


class FakeStore:
    """A durable fake authoritative store identified by content hash."""

    def __init__(self, path: Path, initial: bytes | None = None) -> None:
        self.path = Path(path)
        if initial is not None and not self.path.exists():
            self.path.write_bytes(initial)

    def state(self) -> str:
        return _sha(self.path.read_bytes())

    def write(self, payload: bytes) -> None:
        self.path.write_bytes(payload)


def _run_multi_store(journal, tmp_path, fault):
    cred = FakeStore(tmp_path / "credential.bin", b"cred-v0")
    conf = FakeStore(tmp_path / "config.bin", b"config-v0")
    operation_id = _fake_multi(journal, tmp_path)
    with journal.operation(operation_id) as operation:
        operation.run_effect(
            "credential_promote", pre_state=cred.state(), intended_post_state=_sha(b"cred-v1"),
            perform=lambda: cred.write(b"cred-v1"), observe=cred.state, fault_hook=fault,
        )
        with ConfigFileLock(tmp_path / "config.bin"):
            operation.run_effect(
                "config_commit", pre_state=conf.state(), intended_post_state=_sha(b"config-v1"),
                perform=lambda: conf.write(b"config-v1"), observe=conf.state, fault_hook=fault,
            )
        operation.update(phase="complete", next_action="none")
    return operation_id


# A fresh process reconstructs recovery from the record alone: its binding
# names the authoritative stores; nothing is carried over from the writer.
_RECONCILE = """
import hashlib, json, sys
from pathlib import Path
from uls.settings.journal import JournalStore, reconcile_operation
journal = JournalStore(sys.argv[1])
out = []
for pending in journal.unresolved():
    record = journal.read(pending['operation_id'])
    stores = {'credential_promote': record['binding']['credential_store'],
              'config_commit': record['binding']['config_store']}
    observers = {name: (lambda p=path: hashlib.sha256(Path(p).read_bytes()).hexdigest())
                 for name, path in stores.items()}
    out.append(reconcile_operation(record, observers))
print(json.dumps(out))
"""

EXPECTED_RECOVERY = {
    "before_credential_promote": ("resume:credential_promote",
                                  {"credential_promote": "effect_not_applied", "config_commit": "not_started"}),
    "after_credential_promote": ("verify:credential_promote",
                                 {"credential_promote": "effect_applied_needs_verification",
                                  "config_commit": "not_started"}),
    # Generic effect-order evidence (a fake kind): the first effect is durable
    # and the second never started, so the plan resumes the second effect.
    # Credential replacement uses its own restore branch; see
    # test_settings_credential_journal.py.
    "after_credential_promote_recorded": ("resume:config_commit",
                                          {"credential_promote": "verified", "config_commit": "not_started"}),
    "before_config_commit": ("resume:config_commit",
                             {"credential_promote": "verified", "config_commit": "effect_not_applied"}),
    "after_config_commit": ("verify:config_commit",
                            {"credential_promote": "verified",
                             "config_commit": "effect_applied_needs_verification"}),
    "after_config_commit_recorded": ("complete",
                                     {"credential_promote": "verified", "config_commit": "verified"}),
}


def _fresh_reconcile(tmp_path):
    proc = subprocess.run([sys.executable, "-c", _RECONCILE, str(tmp_path)], check=False,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("point", sorted(EXPECTED_RECOVERY))
def test_crash_after_each_authoritative_effect_reconciles_in_a_fresh_process(tmp_path, point):
    journal = JournalStore(tmp_path)

    def fault(name):
        if name == point:
            raise SimulatedCrash(name)

    with pytest.raises(SimulatedCrash):
        _run_multi_store(journal, tmp_path, fault)
    [pending] = JournalStore(tmp_path).unresolved()
    raw = (journal.directory / f"{pending['operation_id']}.json").read_bytes()
    assert b"cred-v1" not in raw and b"config-v1" not in raw
    expected_action, expected_effects = EXPECTED_RECOVERY[point]
    assert _fresh_reconcile(tmp_path) == [{"effects": expected_effects, "next_action": expected_action}]


def test_resume_after_promotion_completes_config_commit_from_the_record(tmp_path):
    journal = JournalStore(tmp_path)

    def fault(name):
        if name == "after_credential_promote_recorded":
            raise SimulatedCrash(name)

    with pytest.raises(SimulatedCrash):
        _run_multi_store(journal, tmp_path, fault)
    restarted = JournalStore(tmp_path)
    [pending] = restarted.unresolved()
    record = restarted.read(pending["operation_id"])
    conf = FakeStore(Path(record["binding"]["config_store"]))
    with restarted.operation(pending["operation_id"]) as operation:
        with ConfigFileLock(conf.path):
            operation.run_effect("config_commit", pre_state=conf.state(),
                                 intended_post_state=record["candidate_hash"],
                                 perform=lambda: conf.write(b"config-v1"), observe=conf.state)
        operation.update(phase="complete", next_action="none")
    assert restarted.unresolved() == []
    assert conf.path.read_bytes() == b"config-v1"


def test_concurrent_change_after_crash_requires_manual_review(tmp_path):
    journal = JournalStore(tmp_path)

    def fault(name):
        if name == "before_config_commit":
            raise SimulatedCrash(name)

    with pytest.raises(SimulatedCrash):
        _run_multi_store(journal, tmp_path, fault)
    (tmp_path / "config.bin").write_bytes(b"someone-else")
    [plan] = _fresh_reconcile(tmp_path)
    assert plan["next_action"] == "manual_review"
    assert plan["effects"]["config_commit"] == "state_conflict"


def test_unreadable_record_stays_visible(tmp_path):
    journal = JournalStore(tmp_path)
    bad = journal.directory / ("f" * 32 + ".json")
    bad.write_text("{not json")
    os.chmod(bad, 0o600)
    assert journal.unresolved()[0]["phase"] == "unreadable"
