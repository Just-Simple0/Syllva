from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.contract.test_settings_credential_service import crash, service

import uls.settings.credential_admission as admission_module
from uls.settings.credential_admission import (
    credential_admission,
    credential_pair_admission,
    credential_pair_recovery_admission,
)
from uls.settings.credential_roles import ROLES
from uls.settings.journal import OperationInProgress, SimulatedCrash

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


def test_exact_legacy_physical_reservation_bridges_to_pair_before_recovery(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    _peer, locators = _pair(s, role)
    operation_id, key = _create_legacy_forget_marker(s, role)
    with credential_pair_recovery_admission(
        s.stores.root, role.provider, role.profile, locators, operation_id=operation_id,
        journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        pair = json.loads(admission.path.read_text(encoding="utf-8"))
        assert pair["legacy_bridge"] is True
        assert pair["operation_id"] == operation_id
        assert pair["binding_key"] == key
    with pytest.raises(OperationInProgress), credential_pair_admission(
        s.stores.root, role.provider, role.profile, locators,
        journal=s.journal, config_path=s.config.path,
    ):
        pytest.fail("legacy recovery reservation did not keep the shared pair blocked")


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

    with credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, _pair(s, target)[1],
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        pair = json.loads(admission.path.read_text(encoding="utf-8"))
        assert pair["legacy_bridge"] is True
        assert pair["operation_id"] == target_id
        assert pair["binding_key"] == target_key

    assert pair_path.exists()
    assert target_path.exists()
    assert not peer_path.exists()


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
    with credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, locators,
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        pair_path = admission.path
        original_pair = pair_path.read_bytes()

    peer_id, peer_key = _create_legacy_forget_marker(s, peer, operation_id="b" * 32)
    _terminalize_legacy_operation(s, peer_id)
    peer_path = s.stores.root / "admission" / f"binding-{peer_key}.reservation"
    target_path = s.stores.root / "admission" / f"binding-{target_key}.reservation"

    with credential_pair_recovery_admission(
        s.stores.root, target.provider, target.profile, locators,
        operation_id=target_id, journal=s.journal, config_path=s.config.path,
        config_dir_id=s.config.binding()["config_dir_id"],
    ) as admission:
        assert admission.path.read_bytes() == original_pair

    assert not peer_path.exists()
    assert target_path.exists()
    assert pair_path.read_bytes() == original_pair


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
