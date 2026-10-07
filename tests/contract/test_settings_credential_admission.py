from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from tests.contract.test_settings_credential_service import crash, service

import uls.settings.credential_admission as admission_module
from uls.config.mutation import atomic_replace_config
from uls.settings.config_service import SettingsServiceError
from uls.settings.credential_admission import (
    CredentialPairAdmission,
    credential_admission,
    credential_pair_admission,
    credential_pair_recovery_admission,
)
from uls.settings.credential_roles import ROLES
from uls.settings.journal import JournalOperation, OperationInProgress, SimulatedCrash

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("point", ["reservation_written", "reservation_linked", "reservation_published"])
def test_publication_failure_leaves_evidence_and_never_admits_a_competitor(tmp_path, point):
    s = service(tmp_path); role = ROLES["notion-mcp"]
    with pytest.raises(SimulatedCrash), credential_admission(s.stores.root, role.locator(s.stores.root),
            journal=s.journal, config_path=s.config.path, fault_hook=crash(point)) as admission:
        admission.publish("a" * 32, s.config.binding()["config_dir_id"])
    assert not s.journal.unresolved()  # no record/effect before publication
    assert s.stores.read(role) is None
    with pytest.raises(OperationInProgress), credential_admission(s.stores.root, role.locator(s.stores.root)):
        pytest.fail("uncertain evidence was cleared")


def test_complete_marker_and_owner_mode_are_required(tmp_path):
    s = service(tmp_path); role = ROLES["notion-mcp"]
    with credential_admission(s.stores.root, role.locator(s.stores.root)) as admission:
        admission.path.write_text(json.dumps({"complete": False}))
        admission.path.chmod(0o600)
    with pytest.raises(OperationInProgress), credential_admission(s.stores.root, role.locator(s.stores.root)):
        pass


def test_binding_lock_is_held_across_cli_prompt(tmp_path):
    s = service(tmp_path); role = ROLES["notion-mcp"]
    with credential_admission(s.stores.root, role.locator(s.stores.root)), pytest.raises(OperationInProgress):
        s.save(role, b"fake-token", s.config.load().generation)


def _pair(s, role):
    peer = ROLES[f"{role.provider}-{'worker' if role.purpose == 'mcp' else 'mcp'}"]
    locators = [role.locator(s.stores.root), peer.locator(s.stores.root)]
    return peer, locators


def _leave_pending_pair(s, role):
    with pytest.raises(SimulatedCrash):
        s.save(role, b"synthetic-pending-credential", s.config.load().generation,
               fault_hook=crash("after_credential_stage_recorded"))
    [pending] = s.journal.unresolved()
    directory = s.stores.root / "admission"
    binding_key = hashlib.sha256(role.locator(s.stores.root).encode()).hexdigest()
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    return (
        pending["operation_id"],
        directory / f"pair-{pair_key}.reservation",
        directory / f"binding-{binding_key}.reservation",
    )


def _binding(s, role):
    return {
        **s.config.binding(), "provider": role.provider, "profile": role.profile,
        "role": role.purpose, "store_locator": role.locator(s.stores.root),
    }


@pytest.mark.parametrize("role_slug", ["notion-mcp", "notion-worker"])
def test_pair_lock_covers_both_roles_independent_of_entry_order(tmp_path, role_slug):
    s = service(tmp_path)
    role = ROLES[role_slug]
    peer, locators = _pair(s, role)
    with (
        credential_pair_admission(s.stores.root, role.provider, role.profile, locators,
                                  journal=s.journal, config_path=s.config.path),
        pytest.raises(OperationInProgress),
        credential_pair_admission(s.stores.root, role.provider, role.profile, list(reversed(locators)),
                                  journal=s.journal, config_path=s.config.path),
    ):
        pytest.fail("a second caller entered the same provider/profile pair")
    with (
        credential_pair_admission(s.stores.root, role.provider, role.profile, locators,
                                  journal=s.journal, config_path=s.config.path),
        pytest.raises(OperationInProgress),
        credential_admission(s.stores.root, peer.locator(s.stores.root)),
    ):
        pytest.fail("the pair admission did not hold the peer physical lock")


@pytest.mark.parametrize("point", [
    "reservation_written", "reservation_linked", "reservation_published",
    "pair_reservation_written", "pair_reservation_linked", "pair_reservation_published",
])
def test_pair_or_physical_publication_crash_is_durable_and_fail_closed(tmp_path, point):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    with pytest.raises(SimulatedCrash), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path, fault_hook=crash(point),
    ) as admission:
        admission.publish("d" * 32, _binding(s, role))
    assert not s.journal.unresolved()
    directory = s.stores.root / "admission"
    temporary_prefix = ".tmp_pair-" if point.startswith("pair_") else ".tmp_binding-"
    temporary_markers = list(directory.glob(f"{temporary_prefix}*"))
    if point.endswith(("_written", "_linked")):
        assert temporary_markers
    else:
        assert not temporary_markers
    if point == "reservation_published":
        [physical_path] = directory.glob("binding-*.reservation")
        physical = json.loads(physical_path.read_text(encoding="utf-8"))
        assert physical["schema"] == 2 and physical["kind"] == "physical_binding"
        assert not list(directory.glob("pair-*.reservation"))
    elif point == "pair_reservation_published":
        [pair_path] = directory.glob("pair-*.reservation")
        pair = json.loads(pair_path.read_text(encoding="utf-8"))
        assert pair["schema"] == 2 and pair["kind"] == "credential_pair"
    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("uncertain pair publication was cleared or ignored")


def test_pair_reservation_without_journal_remains_blocked_after_writer_process_exit(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    script = tmp_path / "publish_without_journal.py"
    script.write_text(
        """from pathlib import Path
import sys
from uls.settings.config_service import ConfigStore
from uls.settings.credential_admission import credential_pair_admission
from uls.settings.credential_roles import ROLES
from uls.settings.journal import JournalStore

config = ConfigStore(Path(sys.argv[1]))
root = Path(sys.argv[2])
journal = JournalStore(Path(sys.argv[3]), credential_root=root)
role = ROLES["notion-mcp"]
peer = ROLES["notion-worker"]
locators = [role.locator(root), peer.locator(root)]
binding = {**config.binding(), "provider": role.provider, "profile": role.profile,
           "role": role.purpose, "store_locator": role.locator(root)}
with credential_pair_admission(root, role.provider, role.profile, locators,
                               journal=journal, config_path=config.path) as admission:
    admission.publish("e" * 32, binding)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script), str(s.config.path), str(s.stores.root), str(s.journal.directory)],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not s.journal.unresolved()
    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("an orphaned durable reservation was treated as absent")


def test_pair_reservation_survives_abrupt_writer_process_death(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    script = tmp_path / "publish_then_die.py"
    script.write_text(
        """import os
import sys
from pathlib import Path
from uls.settings.config_service import ConfigStore
from uls.settings.credential_admission import credential_pair_admission
from uls.settings.credential_roles import ROLES
from uls.settings.journal import JournalStore

config = ConfigStore(Path(sys.argv[1]))
root = Path(sys.argv[2])
journal = JournalStore(Path(sys.argv[3]), credential_root=root)
role = ROLES["notion-mcp"]
peer = ROLES["notion-worker"]
locators = [role.locator(root), peer.locator(root)]
binding = {**config.binding(), "provider": role.provider, "profile": role.profile,
           "role": role.purpose, "store_locator": role.locator(root)}
with credential_pair_admission(root, role.provider, role.profile, locators,
                               journal=journal, config_path=config.path) as admission:
    admission.publish("f" * 32, binding)
    os._exit(73)
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        [sys.executable, str(script), str(s.config.path), str(s.stores.root), str(s.journal.directory)],
        cwd=Path.cwd(), env={"PYTHONPATH": str(Path.cwd() / "src")},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert child.returncode == 73
    assert not s.journal.unresolved()

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("abrupt process death cleared a durably published reservation")


def test_pair_only_reservation_without_physical_marker_fails_closed(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _operation_id, pair_path, physical_path = _leave_pending_pair(s, role)
    pair_bytes = pair_path.read_bytes()
    physical_path.unlink()

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("a pair marker without its physical marker was admitted")

    assert pair_path.read_bytes() == pair_bytes
    assert not physical_path.exists()
    assert len(s.journal.unresolved()) == 1


@pytest.mark.parametrize("marker_kind", ["pair", "physical"])
@pytest.mark.parametrize("damage", ["unknown_schema", "malformed_json"])
def test_corrupt_or_unknown_v2_marker_fails_closed_without_repair(tmp_path, marker_kind, damage):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _operation_id, pair_path, physical_path = _leave_pending_pair(s, role)
    marker_path = pair_path if marker_kind == "pair" else physical_path
    if damage == "unknown_schema":
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["schema"] = 3
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    else:
        marker_path.write_bytes(b"{")
    marker_path.chmod(0o600)
    damaged_bytes = marker_path.read_bytes()
    other_path = physical_path if marker_kind == "pair" else pair_path
    other_bytes = other_path.read_bytes()

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("corrupt or unknown v2 marker was repaired or ignored")

    assert marker_path.read_bytes() == damaged_bytes
    assert other_path.read_bytes() == other_bytes
    assert len(s.journal.unresolved()) == 1


def test_pair_markers_exclude_secret_and_google_identity_fields(tmp_path):
    s = service(tmp_path)
    role = ROLES["google-mcp"]
    _peer, locators = _pair(s, role)
    sentinels = ("synthetic-secret-value", "synthetic@example.test", "synthetic-key-id")

    def inspect_pair(point):
        if point == "pair_reservation_published":
            marker_files = list((s.stores.root / "admission").glob("*.reservation"))
            serialized = "".join(path.read_text(encoding="utf-8") for path in marker_files)
            assert len(marker_files) == 2
            assert all(item not in serialized for item in sentinels)
            pair_path = next(path for path in marker_files if path.name.startswith("pair-"))
            pair = json.loads(pair_path.read_text(encoding="utf-8"))
            assert pair["schema"] == 2 and pair["kind"] == "credential_pair"
            raise SimulatedCrash(point)

    binding = {
        **_binding(s, role), "private_key": sentinels[0],
        "client_email": sentinels[1], "private_key_id": sentinels[2],
    }
    with pytest.raises(SimulatedCrash), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path, fault_hook=inspect_pair,
    ) as admission:
        admission.publish("c" * 32, binding)


def _create_legacy_forget_marker(s, role, *, operation_id="f" * 32):
    raw = s.config.load().raw
    _candidate, patch = s._patch(role, raw, detach=True)
    payload = s._candidate_bytes(raw, patch)
    binding = {
        **s.config.binding(), "provider": role.provider, "profile": role.profile,
        "role": role.purpose, "store_locator": role.locator(s.stores.root), "config_patch": patch,
    }
    with s.journal.role_locks([role.role_key]) as roles:
        s.journal.create_operation(
            action_kind="credential_forget", binding=binding,
            original_generation=s.config.load().generation,
            candidate_hash=hashlib.sha256(payload).hexdigest(), fields=[role.slug],
            role_locks=roles, allow_unreleased=True, operation_id=operation_id,
        )
    directory = s.stores.root / "admission"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(role.locator(s.stores.root).encode()).hexdigest()
    marker = {
        "schema": 1, "complete": True, "operation_id": operation_id, "binding_key": key,
        "journal": str(s.journal.directory.resolve()), "config_path": str(s.config.path.resolve()),
        "config_dir_id": s.config.binding()["config_dir_id"],
    }
    path = directory / f"binding-{key}.reservation"
    path.write_text(json.dumps(marker), encoding="utf-8")
    path.chmod(0o600)
    return operation_id, key


def _terminalize_legacy_operation(s, operation_id):
    with s.journal.operation(operation_id) as operation:
        operation.update(phase="resolved_without_change", next_action="none")


def _make_schema2_pair_and_physical(s, role, operation_id, *, pair=True):
    key = hashlib.sha256(role.locator(s.stores.root).encode()).hexdigest()
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    directory = s.stores.root / "admission"
    physical_path = directory / f"binding-{key}.reservation"
    legacy = json.loads(physical_path.read_text(encoding="utf-8"))
    physical = {
        "schema": 2, "kind": "physical_binding", "complete": True,
        "operation_id": operation_id, "binding_key": key, "pair_key": pair_key,
        "journal": legacy["journal"], "config_path": legacy["config_path"],
        "config_dir_id": legacy["config_dir_id"],
    }
    physical_path.write_text(json.dumps(physical, sort_keys=True), encoding="utf-8")
    physical_path.chmod(0o600)
    pair_path = directory / f"pair-{pair_key}.reservation"
    if pair:
        marker = {
            "schema": 2, "kind": "credential_pair", "complete": True,
            "operation_id": operation_id, "pair_key": pair_key,
            "provider": role.provider, "profile": role.profile, "binding_key": key,
            "journal": legacy["journal"], "config_path": legacy["config_path"],
            "config_dir_id": legacy["config_dir_id"], "legacy_bridge": False,
        }
        pair_path.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
        pair_path.chmod(0o600)
    return pair_path, physical_path


def _make_schema2_terminal_orphan(s, role, *, operation_id):
    operation_id, _key = _create_legacy_forget_marker(s, role, operation_id=operation_id)
    _terminalize_legacy_operation(s, operation_id)
    pair_path, physical_path = _make_schema2_pair_and_physical(s, role, operation_id, pair=False)
    return operation_id, pair_path, physical_path


def _reservation_snapshot(s):
    return {path.name: path.read_bytes() for path in (s.stores.root / "admission").glob("*.reservation")}


def _convert_pending_pair_to_legacy(s, role):
    operation_id, pair_path, physical_path = _leave_pending_pair(s, role)
    physical = json.loads(physical_path.read_text(encoding="utf-8"))
    legacy = {
        "schema": 1, "complete": True, "operation_id": operation_id,
        "binding_key": physical["binding_key"], "journal": physical["journal"],
        "config_path": physical["config_path"], "config_dir_id": physical["config_dir_id"],
    }
    pair_path.unlink()
    physical_path.write_text(json.dumps(legacy), encoding="utf-8")
    physical_path.chmod(0o600)
    return operation_id, physical_path


def _create_legacy_detach_marker(s, role, *, operation_id="d" * 32):
    raw = s.config.load().raw
    _candidate, patch = s._patch(role, raw, detach=True)
    payload = s._candidate_bytes(raw, patch)
    binding = {
        **s.config.binding(), "provider": role.provider, "profile": role.profile,
        "role": role.purpose, "store_locator": role.locator(s.stores.root), "config_patch": patch,
    }
    with s.journal.role_locks([role.role_key]) as roles:
        s.journal.create_operation(
            action_kind="credential_detach", binding=binding,
            original_generation=s.config.load().generation,
            candidate_hash=hashlib.sha256(payload).hexdigest(), fields=[role.slug],
            role_locks=roles, allow_unreleased=True, operation_id=operation_id,
        )
    key = hashlib.sha256(role.locator(s.stores.root).encode()).hexdigest()
    marker = {
        "schema": 1, "complete": True, "operation_id": operation_id, "binding_key": key,
        "journal": str(s.journal.directory.resolve()), "config_path": str(s.config.path.resolve()),
        "config_dir_id": s.config.binding()["config_dir_id"],
    }
    path = s.stores.root / "admission" / f"binding-{key}.reservation"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps(marker), encoding="utf-8")
    path.chmod(0o600)
    return operation_id, key


def _all_journal_bytes(s):
    return {path.name: path.read_bytes() for path in s.journal.directory.glob("*.json")}


def _role_store_bytes(s, roles):
    return {
        (role.slug, slot): s.stores.read(role, slot)
        for role in roles for slot in ("active", "staged", "backup")
    }


def test_disallowed_but_valid_recovery_action_preserves_legacy_marker_bytes(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, physical_path = _convert_pending_pair_to_legacy(s, role)
    before = _reservation_snapshot(s)

    with pytest.raises(SettingsServiceError) as error:
        s.recover(operation_id, "restore")  # valid action spelling, not an available choice

    assert error.value.code == "MANUAL_REVIEW"
    assert physical_path.read_bytes() == before[physical_path.name]
    assert _reservation_snapshot(s) == before


@pytest.mark.parametrize("action_kind", ["credential_forget", "credential_detach"])
def test_legacy_forget_detach_leave_preserves_all_reservation_bytes(tmp_path, action_kind):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    if action_kind == "credential_forget":
        operation_id, _key = _create_legacy_forget_marker(s, role, operation_id="1" * 32)
    else:
        operation_id, _key = _create_legacy_detach_marker(s, role, operation_id="2" * 32)
    before = _reservation_snapshot(s)
    config_before = s.config.path.read_bytes()
    journal_before = _all_journal_bytes(s)
    stores_before = _role_store_bytes(s, [role])

    assert s.recover(operation_id, "leave") == {"status": "pending", "code": "LEFT_AS_IS"}

    assert _reservation_snapshot(s) == before
    assert s.config.path.read_bytes() == config_before
    assert _all_journal_bytes(s) == journal_before
    assert _role_store_bytes(s, [role]) == stores_before


def test_legacy_generation_mismatch_preserves_target_and_terminal_peer_markers(tmp_path):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    operation_id, _target_path = _convert_pending_pair_to_legacy(s, target)
    peer_id, _peer_key = _create_legacy_forget_marker(s, peer, operation_id="3" * 32)
    _terminalize_legacy_operation(s, peer_id)
    raw = s.config.load().raw
    raw["x_unknown_section"]["synthetic_mismatch"] = "only-for-this-test"
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    markers_before = _reservation_snapshot(s)
    config_before = s.config.path.read_bytes()
    journal_before = _all_journal_bytes(s)
    stores_before = _role_store_bytes(s, [target, peer])

    with pytest.raises(SettingsServiceError) as error:
        s.recover(operation_id, "resume")

    assert error.value.code == "MANUAL_REVIEW"
    assert _reservation_snapshot(s) == markers_before
    assert s.config.path.read_bytes() == config_before
    assert _all_journal_bytes(s) == journal_before
    assert _role_store_bytes(s, [target, peer]) == stores_before


def _create_terminal_reservation(s, role, phase, monkeypatch):
    if phase == "resolved_without_change":
        operation_id, _key = _create_legacy_forget_marker(s, role, operation_id="4" * 32)
        _terminalize_legacy_operation(s, operation_id)
        return operation_id

    original_update = JournalOperation.update

    def update_as_superseded(operation, **changes):
        if phase == "completed_then_superseded" and changes.get("phase") == "complete":
            changes = {**changes, "phase": "completed_then_superseded"}
        return original_update(operation, **changes)

    def crash_before_release(_admission, _operation_id):
        raise SimulatedCrash("before_exact_release")

    monkeypatch.setattr(JournalOperation, "update", update_as_superseded)
    monkeypatch.setattr(CredentialPairAdmission, "release", crash_before_release)
    with pytest.raises(SimulatedCrash):
        s.save(role, b"terminal-fixture-synthetic-token", s.config.load().generation)
    [record_path] = s.journal.directory.glob("*.json")
    record = s.journal.read(record_path.stem)
    assert record["phase"] == phase
    monkeypatch.undo()
    return record["operation_id"]


@pytest.mark.parametrize("phase", ["complete", "resolved_without_change", "completed_then_superseded"])
def test_terminal_recovery_parses_under_lock_and_releases_exact_reservation_once(tmp_path, monkeypatch, phase):
    from uls.config.mutation import ConfigFileLock
    from uls.settings.config_service import ConfigStore

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id = _create_terminal_reservation(s, role, phase, monkeypatch)
    config_before = s.config.path.read_bytes()
    journal_before = _all_journal_bytes(s)
    stores_before = _role_store_bytes(s, [role])
    release_calls = []
    config_lock_acquires = []
    real_release = CredentialPairAdmission.release
    real_acquire = ConfigFileLock.acquire

    def counted_release(admission, requested_id):
        release_calls.append(requested_id)
        return real_release(admission, requested_id)

    def counted_acquire(lock):
        config_lock_acquires.append(lock.config_path)
        return real_acquire(lock)

    def forbidden_load(_config):
        raise AssertionError("terminal recovery must reuse the held config lock")

    monkeypatch.setattr(CredentialPairAdmission, "release", counted_release)
    monkeypatch.setattr(ConfigFileLock, "acquire", counted_acquire)
    monkeypatch.setattr(ConfigStore, "load", forbidden_load)

    result = s.recover(operation_id, "resume")

    assert result == {"status": "complete", "config_generation": hashlib.sha256(config_before).hexdigest()}
    assert release_calls == [operation_id]
    assert len(config_lock_acquires) == 1
    assert _reservation_snapshot(s) == {}
    assert s.config.path.read_bytes() == config_before
    assert _all_journal_bytes(s) == journal_before
    assert _role_store_bytes(s, [role]) == stores_before


def test_terminal_recovery_parse_failure_keeps_reservation_without_release(tmp_path, monkeypatch):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id = _create_terminal_reservation(s, role, "complete", monkeypatch)
    markers_before = _reservation_snapshot(s)
    release_calls = []
    real_release = CredentialPairAdmission.release

    def counted_release(admission, requested_id):
        release_calls.append(requested_id)
        return real_release(admission, requested_id)

    monkeypatch.setattr(CredentialPairAdmission, "release", counted_release)
    s.config.path.write_bytes(b"system: [\n")

    with pytest.raises(OperationInProgress) as error:
        s.recover(operation_id, "resume")

    assert isinstance(error.value.__cause__, ValueError)
    assert str(error.value.__cause__) == "configuration could not be parsed"
    assert release_calls == []
    assert _reservation_snapshot(s) == markers_before


def test_exact_legacy_physical_reservation_is_read_only_on_admission_entry(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    operation_id, key = _create_legacy_forget_marker(s, role)
    markers_before = _reservation_snapshot(s)
    physical_path = s.stores.root / "admission" / f"binding-{key}.reservation"
    pair_path = s.stores.root / "admission" / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators, operation_id=operation_id,
        journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        assert not pair_path.exists()
        assert physical_path.exists()
    assert _reservation_snapshot(s) == markers_before


def test_exact_legacy_recovery_cleans_terminal_peer_before_bridging(tmp_path):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(s, target, operation_id="a" * 32)
    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="b" * 32)
    _terminalize_legacy_operation(s, peer_id)
    directory = s.stores.root / "admission"
    target_path = directory / f"binding-{target_key}.reservation"
    peer_path = directory / f"binding-{peer_key}.reservation"
    pair_path = directory / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    markers_before = _reservation_snapshot(s)
    peer_bytes = peer_path.read_bytes()

    with credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        assert not pair_path.exists()
        assert target_path.read_bytes() == markers_before[target_path.name]
        assert peer_path.read_bytes() == peer_bytes
    assert _reservation_snapshot(s) == markers_before


@pytest.mark.parametrize("peer_evidence", ["pending", "corrupt", "incomplete", "missing_journal"])
def test_exact_legacy_target_blocker_prevents_bridge_and_preserves_markers(tmp_path, peer_evidence):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(s, target, operation_id="a" * 32)
    _peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="b" * 32)
    directory = s.stores.root / "admission"
    target_path = directory / f"binding-{target_key}.reservation"
    peer_path = directory / f"binding-{peer_key}.reservation"
    if peer_evidence == "corrupt":
        peer_path.write_bytes(b"{")
    elif peer_evidence == "incomplete":
        peer_path.write_text(json.dumps({"schema": 1, "complete": False}), encoding="utf-8")
    elif peer_evidence == "missing_journal":
        marker = json.loads(peer_path.read_text(encoding="utf-8"))
        marker["journal"] = str((tmp_path / "missing-journal-root").resolve())
        peer_path.write_text(json.dumps(marker), encoding="utf-8")
    peer_path.chmod(0o600)
    peer_bytes = peer_path.read_bytes()
    target_bytes = target_path.read_bytes()
    pair_path = directory / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("a blocker was accepted before legacy pair publication")

    assert not pair_path.exists()
    assert target_path.read_bytes() == target_bytes
    assert peer_path.read_bytes() == peer_bytes


def test_journal_only_pair_blocker_prevents_legacy_bridge_publication(tmp_path):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(s, target, operation_id="a" * 32)
    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="b" * 32)
    directory = s.stores.root / "admission"
    target_path = directory / f"binding-{target_key}.reservation"
    peer_path = directory / f"binding-{peer_key}.reservation"
    peer_path.unlink()
    target_bytes = target_path.read_bytes()
    pair_path = directory / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    assert any(item["operation_id"] == peer_id for item in s.journal.unresolved())

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("a journal-only peer blocker was accepted before legacy bridge publication")

    assert not pair_path.exists()
    assert target_path.read_bytes() == target_bytes
    assert any(item["operation_id"] == peer_id for item in s.journal.unresolved())


def test_existing_exact_legacy_bridge_recovers_after_terminal_peer_cleanup(tmp_path):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(s, target, operation_id="a" * 32)
    locators = _pair(s, target)[1]
    _terminalize_legacy_operation(s, target_id)
    keyed = {hashlib.sha256(locator.encode()).hexdigest(): locator for locator in locators}
    pair_admission = CredentialPairAdmission(
        s.stores.root, s.stores.root / "admission", target.provider, target.profile,
        keyed, s.journal, s.config.path, recovery_id=None,
        config_dir_id=s.config.binding()["config_dir_id"],
    )
    pair_path = pair_admission.path
    bridge_pair = pair_admission._pair_record(
        target_id, target_key, s.config.binding()["config_dir_id"], legacy_bridge=True,
    )
    pair_bytes = json.dumps(bridge_pair, sort_keys=True).encode()
    pair_path.write_bytes(pair_bytes)
    pair_path.chmod(0o600)

    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="b" * 32)
    _terminalize_legacy_operation(s, peer_id)
    peer_path = s.stores.root / "admission" / f"binding-{peer_key}.reservation"
    target_path = s.stores.root / "admission" / f"binding-{target_key}.reservation"

    assert pair_path.read_bytes() == pair_bytes
    assert target_path.exists() and peer_path.exists()
    assert s.recover(target_id, "resume")["status"] == "complete"

    assert not peer_path.exists()
    assert not target_path.exists()
    assert not pair_path.exists()


def test_terminal_peer_cleanup_rechecks_its_named_workspace_journal(tmp_path):
    first = service(tmp_path / "workspace-one")
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    second = service(tmp_path / "workspace-two", backend=first.stores.backend, root=first.stores.root)
    target_id, _target_key = _create_legacy_forget_marker(first, target, operation_id="c" * 32)
    _terminalize_legacy_operation(first, target_id)
    peer_id, peer_key = _create_legacy_forget_marker(second, peer, operation_id="d" * 32)
    _terminalize_legacy_operation(second, peer_id)
    peer_path = first.stores.root / "admission" / f"binding-{peer_key}.reservation"
    first_journal_before = _all_journal_bytes(first)
    second_journal_before = _all_journal_bytes(second)
    config_before = second.config.path.read_bytes()

    result = first.recover(target_id, "resume")

    assert result["status"] == "complete"
    assert not peer_path.exists()
    assert _all_journal_bytes(first) == first_journal_before
    assert _all_journal_bytes(second) == second_journal_before
    assert second.config.path.read_bytes() == config_before


def test_recovery_maintenance_commits_after_live_proof_under_all_locks_once(tmp_path, monkeypatch):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _physical_path = _convert_pending_pair_to_legacy(s, role)
    pair_path = s.stores.root / "admission" / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    live_proofs = []
    real_require = s._require_live_recovery
    real_commit = CredentialPairAdmission.commit_recovery_maintenance
    commit_states = []

    def require_live(record, generation, action):
        if not live_proofs:
            assert not pair_path.exists()
        real_require(record, generation, action)
        live_proofs.append((record["operation_id"], action))

    def commit_once(admission, **kwargs):
        assert live_proofs[-1] == (operation_id, "leave")
        assert admission._active
        assert admission._pair_lock is not None and admission._pair_lock.is_held
        assert len(admission._physical_locks) == 2
        assert all(lock.is_held for lock in admission._physical_locks)
        assert kwargs["role_locks"].is_held
        assert kwargs["operation"].is_held
        assert kwargs["operation"].operation_id == operation_id
        assert kwargs["config_lock"].is_held
        real_commit(admission, **kwargs)
        after_first_commit = _reservation_snapshot(s)
        with pytest.raises(OperationInProgress):
            real_commit(admission, **kwargs)
        assert _reservation_snapshot(s) == after_first_commit
        commit_states.append(after_first_commit)

    monkeypatch.setattr(s, "_require_live_recovery", require_live)
    monkeypatch.setattr(CredentialPairAdmission, "commit_recovery_maintenance", commit_once)

    result = s.recover(operation_id, "leave")

    assert result["status"] == "complete"
    assert live_proofs and commit_states
    assert not pair_path.exists()


def test_changed_raw_snapshot_blocks_all_recovery_marker_mutations(tmp_path, monkeypatch):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    operation_id, target_path = _convert_pending_pair_to_legacy(s, target)
    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="e" * 32)
    _terminalize_legacy_operation(s, peer_id)
    peer_path = s.stores.root / "admission" / f"binding-{peer_key}.reservation"
    original_markers = _reservation_snapshot(s)
    tampered_peer = original_markers[peer_path.name] + b"\n"
    original_commit = CredentialPairAdmission.commit_recovery_maintenance

    def mutate_after_live_proof(admission, **kwargs):
        peer_path.write_bytes(tampered_peer)
        peer_path.chmod(0o600)
        return original_commit(admission, **kwargs)

    monkeypatch.setattr(CredentialPairAdmission, "commit_recovery_maintenance", mutate_after_live_proof)
    config_before = s.config.path.read_bytes()
    journal_before = _all_journal_bytes(s)
    stores_before = _role_store_bytes(s, [target, peer])

    with pytest.raises(OperationInProgress):
        s.recover(operation_id, "leave")

    pair_path = s.stores.root / "admission" / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    assert not pair_path.exists()
    assert target_path.read_bytes() == original_markers[target_path.name]
    assert peer_path.read_bytes() == tampered_peer
    assert s.config.path.read_bytes() == config_before
    assert _all_journal_bytes(s) == journal_before
    assert _role_store_bytes(s, [target, peer]) == stores_before


def test_legacy_bridge_crash_reenters_exactly_and_remains_fail_closed(tmp_path, monkeypatch):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    operation_id, _target_path = _convert_pending_pair_to_legacy(s, target)
    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="f" * 32)
    _terminalize_legacy_operation(s, peer_id)
    pair_path = s.stores.root / "admission" / f"pair-{hashlib.sha256(b'notion/default').hexdigest()}.reservation"
    peer_path = s.stores.root / "admission" / f"binding-{peer_key}.reservation"
    original_bridge = CredentialPairAdmission._bridge_legacy

    def publish_then_crash(admission, pair, key):
        original_bridge(admission, pair, key)
        raise SimulatedCrash("after_durable_legacy_bridge")

    monkeypatch.setattr(CredentialPairAdmission, "_bridge_legacy", publish_then_crash)
    with pytest.raises(SimulatedCrash):
        s.recover(operation_id, "leave")
    assert pair_path.exists() and peer_path.exists()
    after_crash = _reservation_snapshot(s)
    monkeypatch.undo()

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("ordinary admission must fail closed on a pending published bridge")
    assert _reservation_snapshot(s) == after_crash

    assert s.recover(operation_id, "leave")["status"] == "complete"
    assert not pair_path.exists()
    assert not peer_path.exists()


def test_retired_terminal_pair_crash_reenters_without_overwriting_pair(tmp_path, monkeypatch):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    operation_id, target_path = _convert_pending_pair_to_legacy(s, target)
    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="9" * 32)
    _terminalize_legacy_operation(s, peer_id)
    locators = _pair(s, target)[1]
    keyed = {hashlib.sha256(locator.encode()).hexdigest(): locator for locator in locators}
    pair_admission = CredentialPairAdmission(
        s.stores.root, s.stores.root / "admission", target.provider, target.profile,
        keyed, s.journal, s.config.path, recovery_id=None,
        config_dir_id=s.config.binding()["config_dir_id"],
    )
    pair_path = pair_admission.path
    retired_pair = pair_admission._pair_record(
        peer_id, peer_key, s.config.binding()["config_dir_id"], legacy_bridge=True,
    )
    pair_path.write_bytes(json.dumps(retired_pair, sort_keys=True).encode())
    pair_path.chmod(0o600)
    peer_path = s.stores.root / "admission" / f"binding-{peer_key}.reservation"
    directory = s.stores.root / "admission"
    original_fsync = admission_module._fsync
    crash_state = []

    def fsync_then_crash_after_pair_unlink(path):
        result = original_fsync(path)
        if (not crash_state and Path(path) == directory and not pair_path.exists()
                and peer_path.exists()):
            crash_state.append("pair-unlink-durable")
            raise SimulatedCrash("after_retired_pair_fsync")
        return result

    monkeypatch.setattr(admission_module, "_fsync", fsync_then_crash_after_pair_unlink)
    with pytest.raises(SimulatedCrash):
        s.recover(operation_id, "leave")
    assert crash_state == ["pair-unlink-durable"]
    assert not pair_path.exists() and peer_path.exists() and target_path.exists()
    after_crash = _reservation_snapshot(s)
    monkeypatch.undo()

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, target.provider, target.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("ordinary admission must fail closed after retired-pair unlink")
    assert _reservation_snapshot(s) == after_crash

    assert s.recover(operation_id, "leave")["status"] == "complete"
    assert not pair_path.exists()
    assert not peer_path.exists()
    assert not target_path.exists()


def test_legacy_reservation_wrong_recovery_id_is_not_bridged(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    _operation_id, _key = _create_legacy_forget_marker(s, role)
    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators, operation_id="a" * 32,
        journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("a different operation ID bridged a legacy reservation")


def test_legacy_reservation_wrong_config_workspace_is_not_bridged(tmp_path):
    first = service(tmp_path / "workspace-one")
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(first, role)
    operation_id, _key = _create_legacy_forget_marker(first, role)
    second = service(tmp_path / "workspace-two", backend=first.stores.backend, root=first.stores.root)
    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        second.stores.root, role.provider, role.profile, locators, operation_id=operation_id,
        journal=second.journal, config_path=second.config.path,
        config_dir_id=second.config.binding()["config_dir_id"],
    ):
        pytest.fail("a different workspace config bridged the legacy reservation")


@pytest.mark.parametrize("marker_kind", ["pair", "physical"])
@pytest.mark.parametrize("identity_field", ["journal", "config_path", "config_dir_id"])
def test_v2_recovery_rejects_marker_journal_or_config_identity_mismatch(
    tmp_path, marker_kind, identity_field,
):
    import shutil

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, pair_path, physical_path = _leave_pending_pair(s, role)
    marker_path = pair_path if marker_kind == "pair" else physical_path
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if identity_field == "journal":
        alternate_journal = tmp_path / "copied-journal"
        shutil.copytree(s.journal.directory, alternate_journal)
        marker[identity_field] = str(alternate_journal.resolve())
    elif identity_field == "config_path":
        marker[identity_field] = str((tmp_path / "other-config.yaml").resolve())
    else:
        marker[identity_field] = "synthetic-wrong-config-dir-id"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    marker_path.chmod(0o600)
    changed_bytes = marker_path.read_bytes()
    other_path = physical_path if marker_kind == "pair" else pair_path
    other_bytes = other_path.read_bytes()

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("mismatched pair/physical identity entered exact recovery")

    assert marker_path.read_bytes() == changed_bytes
    assert other_path.read_bytes() == other_bytes
    assert len(s.journal.unresolved()) == 1


def test_v2_recovery_requires_exact_operation_id(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, pair_path, physical_path = _leave_pending_pair(s, role)
    pair_bytes = pair_path.read_bytes()
    physical_bytes = physical_path.read_bytes()
    wrong_operation_id = "0" * 32 if operation_id != "0" * 32 else "1" * 32

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=wrong_operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("a different recovery operation ID entered exact recovery")

    assert pair_path.read_bytes() == pair_bytes
    assert physical_path.read_bytes() == physical_bytes
    assert len(s.journal.unresolved()) == 1


@pytest.mark.parametrize("identity_field", ["journal", "config_path", "config_dir_id"])
def test_legacy_reservation_wrong_identity_field_is_not_bridged(tmp_path, identity_field):
    import shutil

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, key = _create_legacy_forget_marker(s, role)
    marker_path = s.stores.root / "admission" / f"binding-{key}.reservation"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if identity_field == "journal":
        alternate_journal = tmp_path / "copied-journal"
        shutil.copytree(s.journal.directory, alternate_journal)
        marker[identity_field] = str(alternate_journal.resolve())
    elif identity_field == "config_path":
        marker[identity_field] = str((tmp_path / "other-config.yaml").resolve())
    else:
        marker[identity_field] = "synthetic-wrong-config-dir-id"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    marker_path.chmod(0o600)
    changed_bytes = marker_path.read_bytes()
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    pair_path = s.stores.root / "admission" / f"pair-{pair_key}.reservation"

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("mismatched legacy identity was bridged")

    assert marker_path.read_bytes() == changed_bytes
    assert not pair_path.exists()


def test_independent_provider_pairs_can_be_admitted_together(tmp_path):
    s = service(tmp_path)
    notion_mcp, notion_worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    google_mcp, google_worker = ROLES["google-mcp"], ROLES["google-worker"]
    with credential_pair_admission(
        s.stores.root, "notion", "default",
        [notion_mcp.locator(s.stores.root), notion_worker.locator(s.stores.root)],
        journal=s.journal, config_path=s.config.path,
    ), credential_pair_admission(
        s.stores.root, "google", "default",
        [google_mcp.locator(s.stores.root), google_worker.locator(s.stores.root)],
        journal=s.journal, config_path=s.config.path,
    ):
        assert True


def test_preexisting_pair_directory_parent_fsync_failure_blocks_before_yield_or_effect(tmp_path, monkeypatch):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    directory = s.stores.root / "admission"
    directory.mkdir(mode=0o700)
    real_fsync = admission_module._fsync
    directory_synced = False

    def fail_admission_parent_fsync(path):
        nonlocal directory_synced
        if path == directory:
            real_fsync(path)
            directory_synced = True
            return
        if path == s.stores.root and directory_synced:
            raise OSError("synthetic parent directory fsync failure")
        real_fsync(path)

    monkeypatch.setattr(admission_module, "_fsync", fail_admission_parent_fsync)
    yielded = False
    effect_ran = False
    with pytest.raises(OSError, match="synthetic parent directory fsync failure"), credential_pair_admission(
            s.stores.root, role.provider, role.profile, locators,
            journal=s.journal, config_path=s.config.path,
    ):
        yielded = True
        effect_ran = True

    assert directory_synced
    assert not yielded and not effect_ran
    assert not list(directory.glob("*.reservation"))
    assert not s.journal.unresolved()


def test_reentry_after_admission_directory_creator_parent_fsync_abort(tmp_path, monkeypatch):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    directory = s.stores.root / "admission"
    assert not directory.exists()
    real_fsync = admission_module._fsync
    directory_synced = False
    fail_parent_once = True

    def abort_creator_before_parent_fsync(path):
        nonlocal directory_synced, fail_parent_once
        if path == directory:
            real_fsync(path)
            directory_synced = True
            return
        if path == s.stores.root and directory_synced and fail_parent_once:
            fail_parent_once = False
            raise OSError("synthetic creator parent fsync failure")
        real_fsync(path)

    monkeypatch.setattr(admission_module, "_fsync", abort_creator_before_parent_fsync)
    yielded = False
    effect_ran = False
    with pytest.raises(OSError, match="synthetic creator parent fsync failure"), credential_pair_admission(
            s.stores.root, role.provider, role.profile, locators,
            journal=s.journal, config_path=s.config.path,
    ):
        yielded = True
        effect_ran = True

    assert directory.is_dir() and directory_synced
    assert not yielded and not effect_ran
    assert not list(directory.glob("*.reservation"))
    assert not s.journal.unresolved()

    monkeypatch.setattr(admission_module, "_fsync", real_fsync)
    with credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ) as admission:
        assert admission.directory == directory
    assert not list(directory.glob("*.reservation"))


def test_reentry_after_credential_root_creator_parent_fsync_abort(tmp_path, monkeypatch):
    s = service(tmp_path)
    root = tmp_path / "new-private-root"
    admission_directory = root / "admission"
    role = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    locators = [role.locator(root), peer.locator(root)]
    real_fsync = admission_module._fsync
    fail_parent_once = True

    def abort_root_creator_before_parent_fsync(path):
        nonlocal fail_parent_once
        if path == root.parent and root.exists() and fail_parent_once:
            fail_parent_once = False
            raise OSError("synthetic root creator parent fsync failure")
        real_fsync(path)

    monkeypatch.setattr(admission_module, "_fsync", abort_root_creator_before_parent_fsync)
    yielded = False
    effect_ran = False
    with pytest.raises(OSError, match="synthetic root creator parent fsync failure"), credential_pair_admission(
            root, role.provider, role.profile, locators,
            journal=s.journal, config_path=s.config.path,
    ):
        yielded = True
        effect_ran = True

    assert root.is_dir() and not admission_directory.exists()
    assert not yielded and not effect_ran
    assert not s.journal.unresolved()

    monkeypatch.setattr(admission_module, "_fsync", real_fsync)
    with credential_pair_admission(
        root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ) as admission:
        assert admission.root == root
        assert admission.directory == admission_directory
    assert not list(admission_directory.glob("*.reservation"))


def _terminal_schema2_pair(s, role, phase, monkeypatch):
    if phase == "resolved_without_change":
        operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
            s, role, operation_id="7" * 32,
        )
        pair_path, physical_path = _make_schema2_pair_and_physical(
            s, role, operation_id, pair=True,
        )
    else:
        operation_id = _create_terminal_reservation(s, role, phase, monkeypatch)
        pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
        binding_key = hashlib.sha256(role.locator(s.stores.root).encode()).hexdigest()
        directory = s.stores.root / "admission"
        pair_path = directory / f"pair-{pair_key}.reservation"
        physical_path = directory / f"binding-{binding_key}.reservation"
    return operation_id, pair_path, physical_path


@pytest.mark.parametrize("phase", ["complete", "resolved_without_change", "completed_then_superseded"])
def test_pairless_schema2_own_terminal_orphan_recovers_only_exactly(tmp_path, monkeypatch, phase):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, pair_path, physical_path = _terminal_schema2_pair(s, role, phase, monkeypatch)
    directory = s.stores.root / "admission"
    real_fsync = admission_module._fsync
    crash_states = []

    def crash_after_pair_unlink_is_durable(path):
        result = real_fsync(path)
        if (Path(path) == directory and not pair_path.exists() and physical_path.exists()
                and not crash_states):
            crash_states.append("pair-absent-physical-present")
            raise SimulatedCrash("after_pair_unlink_fsync")
        return result

    monkeypatch.setattr(admission_module, "_fsync", crash_after_pair_unlink_is_durable)
    with pytest.raises(SimulatedCrash):
        s.recover(operation_id, "resume")
    assert crash_states == ["pair-absent-physical-present"]
    assert not pair_path.exists() and physical_path.exists()
    orphan_bytes = physical_path.read_bytes()
    monkeypatch.setattr(admission_module, "_fsync", real_fsync)

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("ordinary admission accepted a pairless schema-2 physical marker")
    assert physical_path.read_bytes() == orphan_bytes

    result = s.recover(operation_id, "resume")
    assert result["status"] == "complete"
    assert not pair_path.exists() and not physical_path.exists()
    assert s.journal.read(operation_id)["phase"] == phase


@pytest.mark.parametrize("phase", ["complete", "resolved_without_change", "completed_then_superseded"])
def test_pairless_all_absent_terminal_recovery_reenters_read_only_then_completes(tmp_path, monkeypatch, phase):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, pair_path, physical_path = _terminal_schema2_pair(s, role, phase, monkeypatch)
    directory = s.stores.root / "admission"
    real_fsync = admission_module._fsync
    crash_states = []

    def crash_after_physical_unlink_fsync(path):
        result = real_fsync(path)
        if (Path(path) == directory and not pair_path.exists() and not physical_path.exists()
                and not crash_states):
            crash_states.append("all-reservations-absent")
            raise SimulatedCrash("after_physical_unlink_fsync")
        return result

    monkeypatch.setattr(admission_module, "_fsync", crash_after_physical_unlink_fsync)
    with pytest.raises(SimulatedCrash):
        s.recover(operation_id, "resume")
    assert crash_states == ["all-reservations-absent"]
    assert not pair_path.exists() and not physical_path.exists()
    monkeypatch.setattr(admission_module, "_fsync", real_fsync)

    assert s.recover(operation_id, "resume")["status"] == "complete"
    assert not pair_path.exists() and not physical_path.exists()


@pytest.mark.parametrize("phase", ["complete", "resolved_without_change", "completed_then_superseded"])
def test_pairless_all_absent_recovery_after_physical_unlink_before_fsync(tmp_path, monkeypatch, phase):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, pair_path, physical_path = _terminal_schema2_pair(s, role, phase, monkeypatch)
    original_unlink = Path.unlink
    crash_states = []

    def unlink_physical_then_crash(path, *args, **kwargs):
        result = original_unlink(path, *args, **kwargs)
        if Path(path) == physical_path and not crash_states:
            crash_states.append("physical-unlinked-before-directory-fsync")
            raise SimulatedCrash("before_physical_unlink_fsync")
        return result

    monkeypatch.setattr(Path, "unlink", unlink_physical_then_crash)
    with pytest.raises(SimulatedCrash):
        s.recover(operation_id, "resume")
    assert crash_states == ["physical-unlinked-before-directory-fsync"]
    assert not pair_path.exists() and not physical_path.exists()
    monkeypatch.setattr(Path, "unlink", original_unlink)

    assert s.recover(operation_id, "resume")["status"] == "complete"
    assert not pair_path.exists() and not physical_path.exists()


def test_pairless_commit_requires_fsync_before_one_context_exact_release(tmp_path):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="8" * 32,
    )
    physical_path.unlink()
    marker_snapshot = _reservation_snapshot(s)
    locators = _pair(s, role)[1]
    keyed = {hashlib.sha256(locator.encode()).hexdigest(): locator for locator in locators}
    wrong_id = "9" * 32
    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators,
        operation_id=wrong_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("an unrelated terminal record used the exact all-absent recovery exception")
    assert _reservation_snapshot(s) == marker_snapshot == {}

    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators,
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        with pytest.raises(OperationInProgress):
            admission.release(operation_id)
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            admission.commit_recovery_maintenance(
                operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                operation=operation, config_lock=config_lock,
            )
            assert admission._pairless_release_armed_id == operation_id
            foreign = CredentialPairAdmission(
                s.stores.root, s.stores.root / "admission", role.provider, role.profile,
                keyed, s.journal, s.config.path, recovery_id=operation_id,
                config_dir_id=s.config.binding()["config_dir_id"],
            )
            with pytest.raises(OperationInProgress):
                foreign.release(operation_id)
            with pytest.raises(OperationInProgress):
                admission.release(wrong_id)
            admission.release(operation_id)
            with pytest.raises(OperationInProgress):
                admission.release(operation_id)

    assert _reservation_snapshot(s) == marker_snapshot == {}


def test_pairless_fsync_failure_does_not_arm_release_and_fresh_recovery_succeeds(tmp_path, monkeypatch):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="a" * 32,
    )
    physical_path.unlink()
    real_fsync = admission_module._fsync
    locators = _pair(s, role)[1]
    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators,
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        def fail_directory_fsync(path):
            if Path(path) == admission.directory:
                raise OSError("synthetic directory fsync failure")
            return real_fsync(path)

        monkeypatch.setattr(admission_module, "_fsync", fail_directory_fsync)
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            with pytest.raises(OperationInProgress):
                admission.commit_recovery_maintenance(
                    operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                    operation=operation, config_lock=config_lock,
                )
        assert admission._pairless_release_armed_id is None
        with pytest.raises(OperationInProgress):
            admission.release(operation_id)
        monkeypatch.setattr(admission_module, "_fsync", real_fsync)

    assert not physical_path.exists()
    assert s.recover(operation_id, "resume")["status"] == "complete"


def test_pairless_release_cannot_consume_arm_after_service_locks_are_released(tmp_path):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="f" * 32,
    )
    physical_path.unlink()
    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            admission.commit_recovery_maintenance(
                operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                operation=operation, config_lock=config_lock,
            )
        with pytest.raises(OperationInProgress):
            admission.release(operation_id)
        assert admission._pairless_release_armed_id == operation_id
        assert admission._pairless_release_consumed is False

    assert s.recover(operation_id, "resume")["status"] == "complete"


def test_pairless_all_absent_parse_failure_keeps_release_unarmed_and_reenters(tmp_path, monkeypatch):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="b" * 32,
    )
    physical_path.unlink()
    config_before = s.config.path.read_bytes()
    journal_before = _all_journal_bytes(s)
    commit_calls = []
    real_commit = CredentialPairAdmission.commit_recovery_maintenance

    def counted_commit(admission, **kwargs):
        commit_calls.append(kwargs["operation_id"])
        return real_commit(admission, **kwargs)

    monkeypatch.setattr(CredentialPairAdmission, "commit_recovery_maintenance", counted_commit)
    s.config.path.write_bytes(b"system: [\n")
    with pytest.raises(OperationInProgress) as error:
        s.recover(operation_id, "resume")
    assert isinstance(error.value.__cause__, ValueError)
    assert commit_calls == []
    assert _reservation_snapshot(s) == {}
    assert _all_journal_bytes(s) == journal_before

    s.config.path.write_bytes(config_before)
    assert s.recover(operation_id, "resume")["status"] == "complete"
    assert commit_calls == [operation_id]
    assert _reservation_snapshot(s) == {}


def test_pairless_absent_to_present_snapshot_drift_blocks_before_cleanup(tmp_path):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="b" * 32,
    )
    physical_path.unlink()
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    pair_path = s.stores.root / "admission" / f"pair-{pair_key}.reservation"
    locators = _pair(s, role)[1]

    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators,
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        pair_path.write_bytes(b"new marker after absent snapshot")
        pair_path.chmod(0o600)
        drift_bytes = pair_path.read_bytes()
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            with pytest.raises(OperationInProgress):
                admission.commit_recovery_maintenance(
                    operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                    operation=operation, config_lock=config_lock,
                )
        assert admission._pairless_release_armed_id is None
        assert pair_path.read_bytes() == drift_bytes
        assert not physical_path.exists()


def test_pairless_state_combination_invariant_rejects_before_first_mutation(tmp_path, monkeypatch):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="c" * 32,
    )
    physical_path.unlink()
    marker_snapshot = _reservation_snapshot(s)
    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        assert admission._recovery_maintenance is not None
        admission._recovery_maintenance.stale_paths = (physical_path,)
        fsync_calls = []

        def detect_early_mutation_fsync(path):
            fsync_calls.append(Path(path))
            raise AssertionError("state invariant must run before filesystem mutation")

        monkeypatch.setattr(admission_module, "_fsync", detect_early_mutation_fsync)
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            with pytest.raises(OperationInProgress):
                admission.commit_recovery_maintenance(
                    operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                    operation=operation, config_lock=config_lock,
                )
        assert fsync_calls == []
        assert admission._pairless_release_armed_id is None
    assert _reservation_snapshot(s) == marker_snapshot == {}


def test_pairless_all_absent_rejects_unrecognized_same_pair_marker(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="d" * 32,
    )
    physical_path.unlink()
    directory = s.stores.root / "admission"
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    unknown_path = directory / "binding-unrecognized.reservation"
    unknown = {
        "schema": 2, "kind": "physical_binding", "complete": True,
        "operation_id": "e" * 32, "binding_key": "unrecognized", "pair_key": pair_key,
        "journal": str(s.journal.directory.resolve()), "config_path": str(s.config.path.resolve()),
        "config_dir_id": s.config.binding()["config_dir_id"],
    }
    unknown_path.write_text(json.dumps(unknown), encoding="utf-8")
    unknown_path.chmod(0o600)
    unknown_bytes = unknown_path.read_bytes()

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("all-absent recovery ignored an unenumerated marker for the same pair")

    assert unknown_path.read_bytes() == unknown_bytes
    assert not physical_path.exists()


def test_pairless_unrecognized_marker_appearing_after_snapshot_blocks_commit(tmp_path):
    from uls.config.mutation import ConfigFileLock

    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    operation_id, _pair_path, physical_path = _make_schema2_terminal_orphan(
        s, role, operation_id="e" * 32,
    )
    physical_path.unlink()
    directory = s.stores.root / "admission"
    pair_key = hashlib.sha256(f"{role.provider}/{role.profile}".encode()).hexdigest()
    unknown_path = directory / "binding-new-same-pair.reservation"
    unknown = {
        "schema": 2, "kind": "physical_binding", "complete": True,
        "operation_id": "f" * 32, "binding_key": "new-same-pair", "pair_key": pair_key,
        "journal": str(s.journal.directory.resolve()), "config_path": str(s.config.path.resolve()),
        "config_dir_id": s.config.binding()["config_dir_id"],
    }
    unknown_bytes = json.dumps(unknown, sort_keys=True).encode()

    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, _pair(s, role)[1],
        operation_id=operation_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        unknown_path.write_bytes(unknown_bytes)
        unknown_path.chmod(0o600)
        with s.journal.role_locks([role.role_key]) as roles, s.journal.operation(operation_id) as operation, ConfigFileLock(s.config.path) as config_lock:
            s.config._parse(s.config.path.read_bytes())
            with pytest.raises(OperationInProgress):
                admission.commit_recovery_maintenance(
                    operation_id=operation_id, expected_record=operation.read(), role_locks=roles,
                    operation=operation, config_lock=config_lock,
                )
        assert admission._pairless_release_armed_id is None
        assert unknown_path.read_bytes() == unknown_bytes
        assert not physical_path.exists()


def test_pairless_terminal_peer_is_not_cleaned_without_target_marker(tmp_path):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_path = _create_legacy_forget_marker(s, target, operation_id="c" * 32)
    _terminalize_legacy_operation(s, target_id)
    target_path = s.stores.root / "admission" / f"binding-{hashlib.sha256(target.locator(s.stores.root).encode()).hexdigest()}.reservation"
    target_path.unlink()
    _peer_id, _pair_path, peer_path = _make_schema2_terminal_orphan(
        s, peer, operation_id="d" * 32,
    )
    peer_bytes = peer_path.read_bytes()

    with pytest.raises(OperationInProgress), credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ):
        pytest.fail("an orphan peer without a target marker used the all-absent recovery exception")

    assert peer_path.read_bytes() == peer_bytes
    assert not target_path.exists()


def test_pairless_schema2_foreign_workspace_peer_with_same_operation_id_is_namespaced(tmp_path):
    first = service(tmp_path / "workspace-one")
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    operation_id, _key = _create_legacy_forget_marker(first, target, operation_id="e" * 32)
    _terminalize_legacy_operation(first, operation_id)
    second = service(tmp_path / "workspace-two", backend=first.stores.backend, root=first.stores.root)
    peer_id, _pair_path, peer_path = _make_schema2_terminal_orphan(
        second, peer, operation_id=operation_id,
    )
    first_journal_before = _all_journal_bytes(first)
    second_journal_before = _all_journal_bytes(second)
    peer_bytes = peer_path.read_bytes()

    assert peer_id == operation_id
    assert first.recover(operation_id, "resume")["status"] == "complete"

    assert not peer_path.exists()
    assert _all_journal_bytes(first) == first_journal_before
    assert _all_journal_bytes(second) == second_journal_before
    assert first_journal_before[operation_id + ".json"] != second_journal_before[operation_id + ".json"]
    assert peer_bytes


def test_native_schema2_terminal_peer_retirement_crash_reenters_via_target_bridge(tmp_path, monkeypatch):
    s = service(tmp_path)
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(s, target, operation_id="f" * 32)
    _terminalize_legacy_operation(s, target_id)
    peer_id, pair_path, peer_path = _make_schema2_terminal_orphan(
        s, peer, operation_id="1" * 32,
    )
    _make_schema2_pair_and_physical(s, peer, peer_id, pair=True)
    target_path = s.stores.root / "admission" / f"binding-{target_key}.reservation"
    directory = s.stores.root / "admission"
    real_fsync = admission_module._fsync
    crash_states = []

    def crash_after_retired_pair_fsync(path):
        result = real_fsync(path)
        if (Path(path) == directory and not pair_path.exists() and peer_path.exists()
                and target_path.exists() and not crash_states):
            crash_states.append("retired-schema2-pair-absent")
            raise SimulatedCrash("after_native_retired_pair_fsync")
        return result

    monkeypatch.setattr(admission_module, "_fsync", crash_after_retired_pair_fsync)
    with pytest.raises(SimulatedCrash):
        s.recover(target_id, "resume")
    assert crash_states == ["retired-schema2-pair-absent"]
    assert not pair_path.exists() and peer_path.exists() and target_path.exists()
    monkeypatch.setattr(admission_module, "_fsync", real_fsync)

    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("ordinary admission accepted the native schema-2 peer orphan")
    assert s.recover(target_id, "resume")["status"] == "complete"
    assert not pair_path.exists() and not peer_path.exists() and not target_path.exists()


def test_schema2_foreign_peer_cleanup_reenters_after_target_bridge_crash(tmp_path, monkeypatch):
    first = service(tmp_path / "workspace-one")
    target = ROLES["notion-mcp"]
    peer = ROLES["notion-worker"]
    target_id, target_key = _create_legacy_forget_marker(first, target, operation_id="2" * 32)
    _terminalize_legacy_operation(first, target_id)
    second = service(tmp_path / "workspace-two", backend=first.stores.backend, root=first.stores.root)
    peer_id, _pair_path, peer_path = _make_schema2_terminal_orphan(
        second, peer, operation_id="3" * 32,
    )
    directory = first.stores.root / "admission"
    pair_key = hashlib.sha256(f"{target.provider}/{target.profile}".encode()).hexdigest()
    pair_path = directory / f"pair-{pair_key}.reservation"
    target_path = directory / f"binding-{target_key}.reservation"
    original_bridge = CredentialPairAdmission._bridge_legacy

    def publish_bridge_then_crash(admission, pair, key):
        original_bridge(admission, pair, key)
        raise SimulatedCrash("after_schema2_peer_bridge_publish")

    monkeypatch.setattr(CredentialPairAdmission, "_bridge_legacy", publish_bridge_then_crash)
    with pytest.raises(SimulatedCrash):
        first.recover(target_id, "resume")
    assert pair_path.exists() and target_path.exists() and peer_path.exists()
    monkeypatch.undo()

    assert first.recover(target_id, "resume")["status"] == "complete"
    assert not pair_path.exists() and not target_path.exists() and not peer_path.exists()
    assert second.journal.read(peer_id)["phase"] == "resolved_without_change"
