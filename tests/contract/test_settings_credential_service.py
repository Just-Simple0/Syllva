"""Real journal/admission/CAS with temp files, fake keyring and fake provider only."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest
from tests.contract._settings_support import write_config

from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.credential_roles import ROLES
from uls.settings.credential_service import CredentialService
from uls.settings.credential_stores import CredentialStores, FakeKeyring
from uls.settings.journal import JournalStore, SimulatedCrash
from uls.settings.provider_checks import FakeProviderTransport, ProviderChecks

pytestmark = pytest.mark.contract


def service(tmp_path: Path, *, backend=None, root=None, environ=None):
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "config.yaml"
    if not path.exists():
        path = write_config(tmp_path)
    config = ConfigStore(path)
    root = root or tmp_path / "fake-secrets"
    root.mkdir(mode=0o700, exist_ok=True)
    stores = CredentialStores(root, backend=backend or FakeKeyring())
    journal = JournalStore(tmp_path / "workspace", credential_root=root)
    return CredentialService(config, journal, stores, ProviderChecks(FakeProviderTransport(), cooldown=0),
                             platform="darwin", environ=environ or {}, google_loader=lambda *a, **k: None)


def crash(point):
    def hook(value):
        if value == point:
            raise SimulatedCrash(value)
    return hook


def _configure_notion_sources(service, sources):
    import yaml

    from uls.config.mutation import atomic_replace_config

    raw = service.config.load().raw
    raw["credentials"] = {name: {"source": source} for name, source in sources.items()}
    atomic_replace_config(service.config.path, yaml.safe_dump(raw, sort_keys=False).encode())


def _notion_peer_effect_snapshot(service, roles):
    stores = {
        (role.slug, slot): service.stores.read(role, slot)
        for role in roles
        for slot in ("active", "staged", "backup")
    }
    journal = {
        path.name: path.read_bytes()
        for path in service.journal.directory.glob("*.json")
    }
    return stores, service.config.path.read_bytes(), journal


def _inject_synthetic_eacces(monkeypatch, target):
    import uls.config._secure_file as secure_file

    real_open = secure_file.os.open
    parent = target.parent.stat()
    intercepted = []

    def open_with_synthetic_eacces(path, flags, mode=0o777, *, dir_fd=None):
        if path == target.name and dir_fd is not None:
            opened_parent = os.fstat(dir_fd)
            if (opened_parent.st_dev, opened_parent.st_ino) == (parent.st_dev, parent.st_ino):
                intercepted.append(target.name)
                raise PermissionError(errno.EACCES, "synthetic denied", str(target))
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(secure_file.os, "open", open_with_synthetic_eacces)
    return intercepted


def test_enroll_replace_forget_and_unchanged_source_revision(tmp_path):
    s = service(tmp_path)
    role = ROLES["notion-mcp"]
    before = s.config.load().generation
    first = s.save(role, b"first-token", before)
    second = s.save(role, b"second-token", first["config_generation"], replace=True)
    assert first["config_generation"] != second["config_generation"] != before
    assert s.stores.read(role) == b"second-token"
    assert s.stores.read(role, "backup") is None and s.stores.read(role, "staged") is None
    s.forget(role, second["config_generation"])
    assert s.stores.read(role) is None and not s.journal.unresolved()
    assert s.config.load().raw["x_unknown_section"]["keep"] == [1, 2]


@pytest.mark.parametrize("value,code", [(b"invalid-token", "INVALID_CREDENTIAL"), (b"outage-token", "PROVIDER_UNAVAILABLE")])
def test_failed_replacement_keeps_old_and_cleans_staging(tmp_path, value, code):
    s = service(tmp_path); role = ROLES["notion-mcp"]
    generation = s.save(role, b"good-token", s.config.load().generation)["config_generation"]
    with pytest.raises(SettingsServiceError) as error:
        s.save(role, value, generation, replace=True)
    assert error.value.code == code
    assert s.stores.read(role) == b"good-token" and s.stores.read(role, "staged") is None
    assert s.config.load().generation == generation and not s.journal.unresolved()


def _credential_card(service, role_slug):
    return next(card for card in service.cards()["cards"] if card["role"] == role_slug)


def _configure_google_path(service, role, path_value, *, legacy=False):
    import yaml

    from uls.config.mutation import atomic_replace_config

    raw = service.config.load().raw
    raw.pop(f"google_{role.purpose}_credentials_path", None)
    if legacy:
        raw.setdefault("google_drive", {})[f"{role.purpose}_credentials_path"] = path_value
    else:
        raw[f"google_{role.purpose}_credentials_path"] = path_value
    atomic_replace_config(service.config.path, yaml.safe_dump(raw, sort_keys=False).encode())


def _seed_managed_google_file(service, role, marker):
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    service.stores.write(role, payload)
    return Path(role.locator(service.stores.root)), payload


def test_cards_distinguish_absent_and_present_environment_credentials_without_disclosure(tmp_path):
    absent_service = service(tmp_path / "absent-environment", environ={})
    absent = _credential_card(absent_service, "notion-mcp")
    assert (absent["state"], absent["source"], absent["managed"], absent["storage_label"], absent["can_test"]) == (
        "not_configured", "environment", False, "the Settings process environment", False)

    marker = "synthetic-environment-secret-sentinel"
    present_service = service(tmp_path / "present-environment", environ={"NOTION_MCP_TOKEN": marker})
    present = _credential_card(present_service, "notion-mcp")
    assert (present["state"], present["source"], present["managed"], present["storage_label"], present["can_test"]) == (
        "external", "environment", False, "the Settings process environment", True)
    assert marker not in json.dumps(present_service.cards())

    present_service.platform = "linux"
    linux_present = _credential_card(present_service, "notion-mcp")
    assert (linux_present["state"], linux_present["can_test"], linux_present["can_mutate"]) == (
        "external", True, False)


def test_cards_report_external_google_file_without_path_or_contents(tmp_path):
    import yaml

    from uls.config.mutation import atomic_replace_config

    s = service(tmp_path / "external-google-file", environ={})
    external_path = tmp_path / "external-google-file" / "user-managed.json"
    marker = "synthetic-google-private-key-sentinel"
    external_path.write_text(json.dumps({"private_key": marker}), encoding="utf-8")
    external_path.parent.chmod(0o700)
    external_path.chmod(0o600)
    raw = s.config.load().raw
    raw["google_mcp_credentials_path"] = str(external_path)
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())

    card = _credential_card(s, "google-mcp")
    assert (card["state"], card["source"], card["managed"], card["storage_label"], card["can_test"], card["can_detach"]) == (
        "external", "external_file", False, "the configured external credential file", True, True)
    serialized = json.dumps(s.cards())
    assert str(external_path) not in serialized and marker not in serialized


@pytest.mark.parametrize(("role_slug", "legacy"), [
    ("google-mcp", False), ("google-worker", False),
    ("google-mcp", True), ("google-worker", True),
])
def test_relative_managed_google_path_keeps_managed_metadata(tmp_path, role_slug, legacy):
    s = service(tmp_path, environ={})
    role = ROLES[role_slug]
    marker = f"synthetic-managed-{role_slug}-private-key-sentinel"
    active_path, payload = _seed_managed_google_file(s, role, marker)
    relative_path = active_path.relative_to(s.config.path.parent).as_posix()
    _configure_google_path(s, role, relative_path, legacy=legacy)

    card = _credential_card(s, role_slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "configured", "file", True, "the fake test store", True, False)
    serialized = json.dumps(s.cards())
    assert str(active_path) not in serialized and marker not in serialized and payload.decode() not in serialized


def test_google_managed_path_uses_expanduser_and_canonical_relative_resolution(tmp_path, monkeypatch):
    s = service(tmp_path, environ={})
    role = ROLES["google-worker"]
    marker = "synthetic-expanded-managed-private-key-sentinel"
    active_path, payload = _seed_managed_google_file(s, role, marker)
    monkeypatch.setenv("HOME", str(s.config.path.parent))
    configured_path = f"~/fake-secrets/../fake-secrets/{active_path.name}"
    _configure_google_path(s, role, configured_path)

    card = _credential_card(s, role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "configured", "file", True, "the fake test store", True, False)
    serialized = json.dumps(s.cards())
    assert str(active_path) not in serialized and marker not in serialized and payload.decode() not in serialized


def test_relative_external_google_path_stays_external_without_path_or_contents(tmp_path):
    s = service(tmp_path, environ={})
    role = ROLES["google-mcp"]
    external_path = tmp_path / "user-managed-relative.json"
    marker = "synthetic-relative-external-private-key-sentinel"
    external_path.write_text(json.dumps({"private_key": marker}), encoding="utf-8")
    external_path.chmod(0o600)
    _configure_google_path(s, role, external_path.name)

    card = _credential_card(s, role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "external", "external_file", False, "the configured external credential file", True, True)
    serialized = json.dumps(s.cards())
    assert str(external_path) not in serialized and marker not in serialized


def test_google_environment_path_remains_environment_origin_when_it_names_managed_file(tmp_path):
    s = service(tmp_path, environ={})
    role = ROLES["google-mcp"]
    marker = "synthetic-environment-path-private-key-sentinel"
    active_path, payload = _seed_managed_google_file(s, role, marker)
    s.environ = {role.name: str(active_path)}

    card = _credential_card(s, role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "external", "environment", False, "the Settings process environment", True, False)
    serialized = json.dumps(s.cards())
    assert str(active_path) not in serialized and marker not in serialized and payload.decode() not in serialized


def test_canonical_source_classification_does_not_relax_symlink_read_guard(tmp_path):
    from uls.config.errors import ConfigurationError

    s = service(tmp_path, environ={})
    role = ROLES["google-mcp"]
    marker = "synthetic-symlink-private-key-sentinel"
    active_path, _ = _seed_managed_google_file(s, role, marker)
    alias = tmp_path / "managed-alias.json"
    alias.symlink_to(active_path)
    _configure_google_path(s, role, str(alias))

    assert s.source(role, s.config.load().raw)[0] == "file"
    with pytest.raises(ConfigurationError):
        s.effective(role, s.config.load().raw)


def test_managed_fake_keyring_and_file_keep_their_source_and_store_label(tmp_path):
    s = service(tmp_path / "managed-fake-stores", environ={})
    notion_marker = "synthetic-managed-keyring-secret-sentinel"
    s.save(ROLES["notion-mcp"], notion_marker.encode(), s.config.load().generation)
    google_marker = "synthetic-managed-file-private-key-sentinel"
    google_value = {"type": "service_account", "client_email": "fake@example.com", "private_key": google_marker,
                    "private_key_id": "fake-key-id", "project_id": "fake-project",
                    "token_uri": "https://oauth2.googleapis.com/token"}
    s.save(ROLES["google-mcp"], json.dumps(google_value).encode(), s.config.load().generation)

    notion = _credential_card(s, "notion-mcp")
    google = _credential_card(s, "google-mcp")
    assert (notion["state"], notion["source"], notion["managed"], notion["storage_label"]) == (
        "configured", "keyring", True, "the fake test store")
    assert (google["state"], google["source"], google["managed"], google["storage_label"]) == (
        "configured", "file", True, "the fake test store")
    serialized = json.dumps(s.cards())
    assert notion_marker not in serialized and google_marker not in serialized
    assert str(s.stores.root) not in serialized


ENROLL_EFFECTS = ("credential_stage", "config_commit", "credential_promote", "staging_cleanup")
REPLACE_EFFECTS = ("credential_stage", "credential_backup", "credential_promote", "config_commit", "backup_delete", "staging_cleanup")


@pytest.mark.parametrize("kind,effects", [("enroll", ENROLL_EFFECTS), ("replace", REPLACE_EFFECTS)])
@pytest.mark.parametrize("edge", ["before", "after", "recorded"])
def test_fresh_service_recovery_at_every_effect(tmp_path, kind, effects, edge):
    for name in effects:
        folder = tmp_path / name; folder.mkdir()
        s = service(folder); role = ROLES["notion-mcp"]
        if kind == "replace":
            s.save(role, b"old-token", s.config.load().generation)
        generation = s.config.load().generation
        point = f"after_{name}_recorded" if edge == "recorded" else f"{edge}_{name}"
        with pytest.raises(SimulatedCrash):
            s.save(role, b"new-token", generation, fault_hook=crash(point))
        [pending] = s.journal.unresolved()
        record = s.journal.read(pending["operation_id"])
        if s.stores.read(role) == b"new-token" and kind == "enroll":
            assert s.config.load().generation != generation  # never activate before CAS
        restarted = service(folder, backend=s.stores.backend)
        try:
            restarted.recover(record["operation_id"], "resume")
        except SettingsServiceError as error:
            assert error.code == "MANUAL_REVIEW"
        else:
            assert not restarted.journal.unresolved()
            assert restarted.stores.read(role) == b"new-token"
            assert restarted.stores.read(role, "staged") is None


def test_restore_exact_backup_after_promote_before_commit(tmp_path):
    s = service(tmp_path); role = ROLES["notion-mcp"]
    s.save(role, b"old-token", s.config.load().generation)
    generation = s.config.load().generation
    with pytest.raises(SimulatedCrash):
        s.save(role, b"new-token", generation, fault_hook=crash("after_credential_promote_recorded"))
    [pending] = s.journal.unresolved()
    s.recover(pending["operation_id"], "restore")
    assert s.stores.read(role) == b"old-token"
    assert s.stores.read(role, "backup") is None and s.stores.read(role, "staged") is None
    assert s.config.load().generation == generation


def test_same_value_env_peer_refused_before_staging(tmp_path):
    s = service(tmp_path, environ={"NOTION_WORKER_TOKEN": "same-token"})
    with pytest.raises(SettingsServiceError) as error:
        s.save(ROLES["notion-mcp"], b"same-token", s.config.load().generation)
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert not s.journal.unresolved()


def test_two_workspaces_reservation_blocks_until_exact_recovery(tmp_path):
    a = service(tmp_path / "a"); role = ROLES["notion-mcp"]
    a.save(role, b"old-token", a.config.load().generation)
    with pytest.raises(SimulatedCrash):
        a.save(role, b"new-token", a.config.load().generation, fault_hook=crash("after_credential_promote_recorded"))
    b = service(tmp_path / "b", backend=a.stores.backend, root=a.stores.root)
    from uls.settings.journal import OperationInProgress
    with pytest.raises(OperationInProgress):
        b.save(role, b"third-token", b.config.load().generation)
    [pending] = a.journal.unresolved()
    with pytest.raises((OperationInProgress, SettingsServiceError, FileNotFoundError)):
        b.recover(pending["operation_id"], "restore")
    a.recover(pending["operation_id"], "restore")
    b.save(role, b"third-token", b.config.load().generation)
    assert b.stores.read(role) == b"third-token"


def test_google_forget_refuses_other_effective_env_binding_and_external_detach(tmp_path):
    import json

    from uls.config.mutation import atomic_replace_config
    s = service(tmp_path); role = ROLES["google-mcp"]
    value = {"type": "service_account", "client_email": "mcp@example.com", "private_key": "fake-key",
             "private_key_id": "mcp-key", "project_id": "fake", "token_uri": "https://oauth2.googleapis.com/token"}
    s.save(role, json.dumps(value).encode(), s.config.load().generation)
    s.environ = {"GOOGLE_WORKER_CREDENTIALS_FILE": role.locator(s.stores.root)}
    with pytest.raises(SettingsServiceError) as error:
        s.forget(role, s.config.load().generation)
    assert error.value.code == "CREDENTIAL_STILL_SELECTED" and s.stores.read(role)
    # External detach never touches the external file, including legacy nested paths.
    external = tmp_path / "user-owned.json"; external.write_text("unchanged"); external.chmod(0o600)
    raw = s.config.load().raw
    raw.pop("google_mcp_credentials_path")
    raw["google_drive"]["mcp_credentials_path"] = str(external)
    import yaml
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    s.environ = {}
    s.forget(role, s.config.load().generation, detach=True)
    assert external.read_text() == "unchanged"
    assert s.stores.read(role) is not None


def test_v3_config_history_remains_readable_but_v3_credential_record_refused(tmp_path):
    from uls.settings.journal import validate_record
    s = service(tmp_path)
    generation = s.config.load().generation
    operation_id = s.journal.create_config_operation(binding=s.config.binding(), original_generation=generation,
                                                     candidate_hash="1" * 64, fields=[])
    assert s.journal.read(operation_id)["schema_version"] == 3
    s.config.recover(s.journal, operation_id, "leave")
    s.save(ROLES["notion-mcp"], b"fake-token", generation)
    records = [s.journal.read(path.stem) for path in s.journal.directory.glob("*.json")]
    credential = next(record for record in records if record["schema_version"] == 4)
    with pytest.raises(ValueError):
        validate_record({**credential, "schema_version": 3}, credential["operation_id"])


def _google_identity(email, key_id, private_key="synthetic-private-key"):
    return json.dumps({
        "type": "service_account", "client_email": email, "private_key": private_key,
        "private_key_id": key_id, "project_id": "synthetic-project",
        "token_uri": "https://oauth2.googleapis.com/token",
    }).encode()


def test_notion_save_checks_undeclared_canonical_managed_peer(tmp_path):
    s = service(tmp_path, environ={})
    mcp, worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    shared = b"synthetic-managed-notion-peer"
    s.stores.write(mcp, shared)

    with pytest.raises(SettingsServiceError) as error:
        s.save(worker, shared, s.config.load().generation)

    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.stores.read(worker) is None
    assert not s.journal.unresolved()
    assert shared.decode() not in json.dumps(s.journal.unresolved())


@pytest.mark.skipif(os.name == "nt", reason="POSIX EACCES injection targets the secure-file open boundary")
def test_notion_save_fails_closed_when_canonical_peer_read_is_eacces(tmp_path, monkeypatch):
    from uls.config._secure_file import read_secure_file
    from uls.config.errors import ConfigurationError

    s = service(tmp_path / "notion-peer-eacces", environ={})
    mcp, worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    _configure_notion_sources(s, {mcp.name: "keyring"})
    s.stores.write(mcp, b"synthetic-existing-target")
    s.stores.write(worker, b"synthetic-managed-peer")
    worker_path = Path(worker.locator(s.stores.root))
    before = _notion_peer_effect_snapshot(s, (mcp, worker))
    with monkeypatch.context() as eacces_patch:
        intercepted = _inject_synthetic_eacces(eacces_patch, worker_path)
        with pytest.raises(ConfigurationError) as boundary_error:
            read_secure_file(worker_path)
        assert boundary_error.value.details["problems"] == ["secret_file_missing"]
        assert isinstance(boundary_error.value.__cause__, PermissionError)
        assert boundary_error.value.__cause__.errno == errno.EACCES

        with pytest.raises(SettingsServiceError) as error:
            s.save(mcp, b"synthetic-distinct-replacement", s.config.load().generation, replace=True)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert intercepted
    assert _notion_peer_effect_snapshot(s, (mcp, worker)) == before
    assert not s.journal.unresolved()


@pytest.mark.parametrize("declare_peer", [False, True], ids=["undeclared-canonical", "declared-effective-file"])
def test_notion_save_rejects_invalid_utf8_peer_without_effects(tmp_path, declare_peer):
    s = service(tmp_path / ("declared" if declare_peer else "undeclared"), environ={})
    mcp, worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    sources = {mcp.name: "keyring"}
    if declare_peer:
        sources[worker.name] = "file"
    _configure_notion_sources(s, sources)
    s.stores.write(mcp, b"synthetic-existing-target")
    s.stores.write(worker, b"\xff")
    before = _notion_peer_effect_snapshot(s, (mcp, worker))

    with pytest.raises(SettingsServiceError) as error:
        s.save(mcp, b"synthetic-distinct-replacement", s.config.load().generation, replace=True)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert _notion_peer_effect_snapshot(s, (mcp, worker)) == before
    assert not s.journal.unresolved()


def test_notion_save_checks_current_workspace_environment_peer(tmp_path):
    shared = "synthetic-workspace-environment-peer"
    s = service(tmp_path, environ={"NOTION_MCP_TOKEN": shared})
    worker = ROLES["notion-worker"]

    with pytest.raises(SettingsServiceError) as error:
        s.save(worker, shared.encode(), s.config.load().generation)

    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.stores.read(worker) is None


@pytest.mark.parametrize("source", ["keyring", "environment"])
def test_configured_missing_notion_peer_fails_closed(tmp_path, source):
    import yaml

    from uls.config.mutation import atomic_replace_config

    s = service(tmp_path, environ={})
    raw = s.config.load().raw
    raw["credentials"] = {"NOTION_MCP_TOKEN": {"source": source}}
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())

    with pytest.raises(SettingsServiceError) as error:
        s.save(ROLES["notion-worker"], b"synthetic-distinct-token", s.config.load().generation)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert s.stores.read(ROLES["notion-worker"]) is None
    assert not s.journal.unresolved()


def test_google_save_checks_undeclared_canonical_peer_identity(tmp_path):
    s = service(tmp_path, environ={})
    mcp, worker = ROLES["google-mcp"], ROLES["google-worker"]
    s.stores.write(mcp, _google_identity("same@example.test", "managed-key-id"))

    with pytest.raises(SettingsServiceError) as error:
        s.save(worker, _google_identity("same@example.test", "worker-key-id"),
               s.config.load().generation)

    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.stores.read(worker) is None


def test_google_save_fails_closed_on_invalid_managed_peer_identity(tmp_path):
    s = service(tmp_path, environ={})
    mcp = ROLES["google-mcp"]
    s.stores.write(mcp, json.dumps({"client_email": "incomplete@example.test"}).encode())

    with pytest.raises(SettingsServiceError) as error:
        s.save(ROLES["google-worker"], _google_identity("worker@example.test", "worker-key-id"),
               s.config.load().generation)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert s.stores.read(ROLES["google-worker"], "staged") is None
    assert not s.journal.unresolved()


def test_google_save_checks_current_workspace_external_peer_identity(tmp_path):
    s = service(tmp_path, environ={})
    external = tmp_path / "current-workspace-peer.json"
    external.write_bytes(_google_identity("external@example.test", "external-key-id"))
    external.chmod(0o600)
    _configure_google_path(s, ROLES["google-mcp"], str(external))

    with pytest.raises(SettingsServiceError) as error:
        s.save(ROLES["google-worker"], _google_identity("different@example.test", "external-key-id"),
               s.config.load().generation)

    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.stores.read(ROLES["google-worker"]) is None
    assert not s.journal.unresolved()


def test_configured_missing_google_peer_fails_closed_without_staging(tmp_path):
    s = service(tmp_path, environ={})
    missing = tmp_path / "configured-but-missing.json"
    _configure_google_path(s, ROLES["google-mcp"], str(missing))

    with pytest.raises(SettingsServiceError) as error:
        s.save(ROLES["google-worker"], _google_identity("worker@example.test", "worker-key-id"),
               s.config.load().generation)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert s.stores.read(ROLES["google-worker"], "staged") is None
    assert not s.journal.unresolved()


def test_concurrent_cross_workspace_same_notion_value_cannot_fill_both_purposes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from uls.settings.journal import OperationInProgress

    root = tmp_path / "shared-per-user-secrets"
    backend = FakeKeyring()
    first = service(tmp_path / "workspace-one", backend=backend, root=root, environ={})
    second = service(tmp_path / "workspace-two", backend=backend, root=root, environ={})
    targets = [(first, ROLES["notion-mcp"]), (second, ROLES["notion-worker"])]
    barrier = Barrier(2)
    value = b"synthetic-cross-workspace-shared-token"

    def enroll(target):
        current, role = target
        barrier.wait()
        try:
            current.save(role, value, current.config.load().generation)
            return "saved"
        except (OperationInProgress, SettingsServiceError) as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(enroll, targets))
    assert outcomes.count("saved") == 1
    assert all(item in {"saved", "CREDENTIAL_PURPOSE_CONFLICT", "OPERATION_IN_PROGRESS"} for item in outcomes)

    failed_target = targets[outcomes.index(next(item for item in outcomes if item != "saved"))]
    current, role = failed_target
    if outcomes[targets.index(failed_target)] == "OPERATION_IN_PROGRESS":
        with pytest.raises(SettingsServiceError) as error:
            current.save(role, value, current.config.load().generation)
        assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert sum(current.stores.read(role) is not None for current, role in targets) == 1


@pytest.mark.parametrize(
    ("mcp_email", "mcp_key_id", "worker_email", "worker_key_id"),
    [
        ("shared@example.test", "mcp-key-id", "shared@example.test", "worker-key-id"),
        ("mcp@example.test", "shared-key-id", "worker@example.test", "shared-key-id"),
    ],
    ids=["same-client-email", "same-private-key-id"],
)
def test_concurrent_cross_workspace_same_google_identity_cannot_fill_both_purposes(
    tmp_path, mcp_email, mcp_key_id, worker_email, worker_key_id,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from uls.settings.journal import OperationInProgress

    root = tmp_path / "shared-per-user-secrets"
    backend = FakeKeyring()
    first = service(tmp_path / "workspace-one", backend=backend, root=root, environ={})
    second = service(tmp_path / "workspace-two", backend=backend, root=root, environ={})
    targets = [(first, ROLES["google-mcp"]), (second, ROLES["google-worker"])]
    values = [
        _google_identity(mcp_email, mcp_key_id, "synthetic-mcp-private-key"),
        _google_identity(worker_email, worker_key_id, "synthetic-worker-private-key"),
    ]
    barrier = Barrier(2)

    def enroll(index):
        current, role = targets[index]
        barrier.wait()
        try:
            current.save(role, values[index], current.config.load().generation)
            return "saved"
        except (OperationInProgress, SettingsServiceError) as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(enroll, range(2)))

    assert outcomes.count("saved") == 1
    winner_index = outcomes.index("saved")
    loser_index = 1 - winner_index
    assert outcomes[loser_index] in {"CREDENTIAL_PURPOSE_CONFLICT", "OPERATION_IN_PROGRESS"}

    loser_service, loser_role = targets[loser_index]
    with pytest.raises(SettingsServiceError) as error:
        loser_service.save(loser_role, values[loser_index], loser_service.config.load().generation)
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"

    active = [current.stores.read(role) for current, role in targets]
    expected_active = [values[0], None] if winner_index == 0 else [None, values[1]]
    assert active == expected_active
    assert sum(value is not None for value in active) == 1
    for current, role in targets:
        assert current.stores.read(role, "staged") is None
        assert current.stores.read(role, "backup") is None
        assert not current.journal.unresolved()


def _replace_cross_workspace_together(targets, values):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from uls.settings.journal import OperationInProgress

    barrier = Barrier(2)

    def replace(index):
        current, role = targets[index]
        barrier.wait()
        try:
            current.save(role, values[index], current.config.load().generation, replace=True)
            return "saved"
        except (OperationInProgress, SettingsServiceError) as error:
            return getattr(error, "code", "OPERATION_IN_PROGRESS")

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(replace, range(2)))


def _workspace_config_journal_snapshot(current):
    return (
        current.config.path.read_bytes(),
        {path.name: path.read_bytes() for path in current.journal.directory.glob("*.json")},
    )


def test_concurrent_cross_workspace_same_notion_replacement_preserves_loser_active(tmp_path):
    root = tmp_path / "shared-per-user-secrets"
    backend = FakeKeyring()
    first = service(tmp_path / "workspace-one", backend=backend, root=root, environ={})
    second = service(tmp_path / "workspace-two", backend=backend, root=root, environ={})
    targets = [(first, ROLES["notion-mcp"]), (second, ROLES["notion-worker"])]
    old_values = [b"synthetic-old-mcp-token", b"synthetic-old-worker-token"]
    for (current, role), old in zip(targets, old_values):
        current.save(role, old, current.config.load().generation)
    before_replacement = [_workspace_config_journal_snapshot(current) for current, _role in targets]

    replacement = b"synthetic-shared-replacement-token"
    values = [replacement, replacement]
    outcomes = _replace_cross_workspace_together(targets, values)

    assert outcomes.count("saved") == 1
    winner_index = outcomes.index("saved")
    loser_index = 1 - winner_index
    assert outcomes[loser_index] in {"CREDENTIAL_PURPOSE_CONFLICT", "OPERATION_IN_PROGRESS"}
    active = [current.stores.read(role) for current, role in targets]
    expected = list(old_values)
    expected[winner_index] = replacement
    assert active == expected

    loser, loser_role = targets[loser_index]
    with pytest.raises(SettingsServiceError) as error:
        loser.save(loser_role, replacement, loser.config.load().generation, replace=True)
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert _workspace_config_journal_snapshot(loser) == before_replacement[loser_index]
    assert [current.stores.read(role) for current, role in targets] == expected
    for current, role in targets:
        assert current.stores.read(role, "staged") is None
        assert current.stores.read(role, "backup") is None
        assert not current.journal.unresolved()


@pytest.mark.parametrize(
    ("mcp_email", "mcp_key_id", "worker_email", "worker_key_id"),
    [
        ("new-shared@example.test", "new-mcp-id", "new-shared@example.test", "new-worker-id"),
        ("new-mcp@example.test", "new-shared-id", "new-worker@example.test", "new-shared-id"),
    ],
    ids=["same-client-email", "same-private-key-id"],
)
def test_concurrent_cross_workspace_same_google_replacement_preserves_loser_active(
    tmp_path, mcp_email, mcp_key_id, worker_email, worker_key_id,
):
    root = tmp_path / "shared-per-user-secrets"
    backend = FakeKeyring()
    first = service(tmp_path / "workspace-one", backend=backend, root=root, environ={})
    second = service(tmp_path / "workspace-two", backend=backend, root=root, environ={})
    targets = [(first, ROLES["google-mcp"]), (second, ROLES["google-worker"])]
    old_values = [
        _google_identity("old-mcp@example.test", "old-mcp-key-id", "synthetic-old-mcp-key"),
        _google_identity("old-worker@example.test", "old-worker-key-id", "synthetic-old-worker-key"),
    ]
    for (current, role), old in zip(targets, old_values):
        current.save(role, old, current.config.load().generation)
    before_replacement = [_workspace_config_journal_snapshot(current) for current, _role in targets]

    values = [
        _google_identity(mcp_email, mcp_key_id, "synthetic-new-mcp-key"),
        _google_identity(worker_email, worker_key_id, "synthetic-new-worker-key"),
    ]
    outcomes = _replace_cross_workspace_together(targets, values)

    assert outcomes.count("saved") == 1
    winner_index = outcomes.index("saved")
    loser_index = 1 - winner_index
    assert outcomes[loser_index] in {"CREDENTIAL_PURPOSE_CONFLICT", "OPERATION_IN_PROGRESS"}
    expected = list(old_values)
    expected[winner_index] = values[winner_index]
    assert [current.stores.read(role) for current, role in targets] == expected

    loser, loser_role = targets[loser_index]
    with pytest.raises(SettingsServiceError) as error:
        loser.save(loser_role, values[loser_index], loser.config.load().generation, replace=True)
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert _workspace_config_journal_snapshot(loser) == before_replacement[loser_index]
    assert [current.stores.read(role) for current, role in targets] == expected
    for current, role in targets:
        assert current.stores.read(role, "staged") is None
        assert current.stores.read(role, "backup") is None
        assert not current.journal.unresolved()


@pytest.mark.parametrize("unsafe_kind", ["world_readable", "symlink"])
def test_google_save_fails_closed_on_configured_unsafe_external_peer_without_changes(tmp_path, unsafe_kind):
    s = service(tmp_path, environ={})
    worker = ROLES["google-worker"]
    old_worker = _google_identity("existing-worker@example.test", "existing-worker-key-id",
                                  "synthetic-existing-worker-key")
    s.save(worker, old_worker, s.config.load().generation)

    external = tmp_path / "configured-peer.json"
    peer_value = _google_identity("unsafe-peer@example.test", "unsafe-peer-key-id",
                                  "synthetic-unsafe-peer-key")
    if unsafe_kind == "world_readable":
        external.write_bytes(peer_value)
        external.chmod(0o644)
    else:
        target = tmp_path / "secure-peer-target.json"
        target.write_bytes(peer_value)
        target.chmod(0o600)
        external.symlink_to(target)
    _configure_google_path(s, ROLES["google-mcp"], str(external))

    config_before = s.config.path.read_bytes()
    slots_before = {slot: s.stores.read(worker, slot) for slot in ("active", "staged", "backup")}
    generation = s.config.load().generation
    with pytest.raises(SettingsServiceError) as error:
        s.save(worker, _google_identity("replacement@example.test", "replacement-key-id",
                                        "synthetic-replacement-key"), generation, replace=True)

    assert error.value.code == "INVALID_CREDENTIAL"
    assert {slot: s.stores.read(worker, slot) for slot in ("active", "staged", "backup")} == slots_before
    assert s.config.path.read_bytes() == config_before
    assert not s.journal.unresolved()


def test_abandoned_enrollment_releases_peer_for_same_credential_registration(tmp_path):
    from uls.settings.journal import OperationInProgress

    root = tmp_path / "shared-per-user-secrets"
    backend = FakeKeyring()
    first = service(tmp_path / "workspace-one", backend=backend, root=root, environ={})
    second = service(tmp_path / "workspace-two", backend=backend, root=root, environ={})
    mcp, worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    value = b"synthetic-abandoned-enrollment-token"

    with pytest.raises(SimulatedCrash):
        first.save(mcp, value, first.config.load().generation,
                   fault_hook=crash("after_credential_stage_recorded"))
    [pending] = first.journal.unresolved()
    operation_id = pending["operation_id"]
    assert first.stores.read(mcp) is None
    assert first.stores.read(mcp, "staged") == value

    with pytest.raises(OperationInProgress):
        second.save(worker, value, second.config.load().generation)
    assert second.stores.read(worker) is None
    assert second.stores.read(worker, "staged") is None

    first.recover(operation_id, "leave")
    assert first.stores.read(mcp) is None
    assert first.stores.read(mcp, "staged") is None
    assert first.stores.read(mcp, "backup") is None
    assert not first.journal.unresolved()

    second.save(worker, value, second.config.load().generation)
    assert second.stores.read(worker) == value
    assert second.stores.read(worker, "staged") is None
    assert second.stores.read(worker, "backup") is None
    assert not second.journal.unresolved()
