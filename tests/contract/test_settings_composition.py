"""Settings startup wiring and fake-mode isolation from real credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from tests.contract._settings_support import write_config

from uls.config.mutation import atomic_replace_config
from uls.settings import composition, credential_stores
from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.credential_roles import ROLES
from uls.settings.fake_mode import services as fake_mode_services
from uls.settings.journal import JournalStore

pytestmark = pytest.mark.contract


def _configure_google_path(config, role, path_value, *, legacy=False):
    raw = config.load().raw
    raw.pop(f"google_{role.purpose}_credentials_path", None)
    if legacy:
        raw.setdefault("google_drive", {})[f"{role.purpose}_credentials_path"] = path_value
    else:
        raw[f"google_{role.purpose}_credentials_path"] = path_value
    atomic_replace_config(config.path, yaml.safe_dump(raw, sort_keys=False).encode())


def test_persistent_fake_mode_rejected_replacement_keeps_configured_card(tmp_path):
    config = ConfigStore(write_config(tmp_path))
    journal = JournalStore(tmp_path / "workspace")
    credentials, _ = composition.build_settings_services(
        config, journal, tmp_path / "runtime", fake_mode=True)
    role = ROLES["notion-mcp"]
    credentials.save(role, b"preview-only-original", config.load().generation)
    generation = config.load().generation
    with pytest.raises(SettingsServiceError):
        credentials.save(role, b"invalid-preview", generation, replace=True)
    assert config.load().generation == generation
    assert credentials.stores.read(role) == b"preview-only-original"
    assert credentials.stores.read(role, "staged") is None
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role.slug)
    assert card["state"] == "configured"
    assert card["can_mutate"] is True
    assert card["storage_label"] == "the fake test store"


def test_fake_composition_connects_and_selects_without_any_real_provider(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real provider or store was selected")

    monkeypatch.setattr(composition, "secrets_directory", forbidden)
    monkeypatch.setattr("uls.settings.credential_stores.explicit_os_keyring", forbidden)
    monkeypatch.setattr("uls.settings.provider_checks.LiveReadOnlyTransport.request", forbidden)
    config = ConfigStore(write_config(tmp_path))
    journal = JournalStore(tmp_path / "workspace")
    credentials, canvas = composition.build_settings_services(
        config, journal, tmp_path / "runtime", fake_mode=True)
    assert not (tmp_path / "runtime").exists()
    generation = config.load().generation
    canvas.connect("https://canvas.example.edu", "preview-only-token", generation)
    assert len(canvas.discover()["courses"]) == 3
    assert credentials.stores.root == (tmp_path / "runtime/fake-secrets").resolve()
    snapshot = canvas.snapshot()
    assert "preview-only-token" not in json.dumps(snapshot)
    assert snapshot["masked_account"] == "S•••••• N••"
    assert snapshot["storage_label"] == "the fake test store"


def test_fake_mode_services_canvas_snapshot_names_fake_store(tmp_path):
    config = ConfigStore(write_config(tmp_path))
    journal = JournalStore(tmp_path / "workspace")
    credentials, canvas = fake_mode_services(config, journal, tmp_path / "runtime")

    assert isinstance(credentials, composition._FakeCredentialService)
    assert canvas.snapshot()["storage_label"] == "the fake test store"


def test_standalone_fake_services_never_reads_configured_external_google_file(
    tmp_path, monkeypatch,
):
    marker = "synthetic-standalone-external-private-key-sentinel"
    external_path = tmp_path / "external-google.json"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    external_path.write_bytes(payload)
    external_path.chmod(0o600)
    config = ConfigStore(write_config(
        tmp_path, google_mcp_credentials_path=str(external_path)))
    credentials, canvas = fake_mode_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "standalone-fake-store")
    read_attempts = []

    def forbidden_read(path, *args, **kwargs):
        read_attempts.append(Path(path))
        raise AssertionError("standalone fake service read configured external credentials")

    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden_read)
    card = next(card for card in credentials.cards()["cards"] if card["role"] == "google-mcp")
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "not_configured", "external_file", False,
        "the configured external credential file", False, True)
    serialized = json.dumps(credentials.cards())
    assert read_attempts == []
    assert str(external_path) not in serialized
    assert marker not in serialized and payload.decode() not in serialized
    assert canvas.snapshot()["storage_label"] == "the fake test store"


@pytest.mark.parametrize(("role_slug", "legacy"), [
    ("google-mcp", False), ("google-worker", False),
    ("google-mcp", True), ("google-worker", True),
])
def test_standalone_fake_services_reads_relative_google_only_from_fixed_store(
    tmp_path, monkeypatch, role_slug, legacy,
):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES[role_slug]
    credentials, canvas = fake_mode_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "standalone-fake-store")
    marker = f"synthetic-standalone-managed-{role_slug}-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    credentials.stores.write(role, payload)
    active_path = Path(role.locator(credentials.stores.root))
    configured_path = active_path.relative_to(config.path.parent).as_posix()
    _configure_google_path(config, role, configured_path, legacy=legacy)

    real_read = credential_stores.read_secure_file
    read_paths = []

    def track_fake_store_read(path, *args, **kwargs):
        read_paths.append(Path(path).resolve())
        return real_read(path, *args, **kwargs)

    def forbidden_input_path_read(*args, **kwargs):
        raise AssertionError("standalone fake service read a supplied Google path")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", track_fake_store_read)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden_input_path_read)
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role_slug)
    assert isinstance(credentials, composition._FakeCredentialService)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "configured", "file", True, "the fake test store", True, False)
    assert read_paths == [active_path]
    serialized = json.dumps(credentials.cards())
    assert str(active_path) not in serialized and configured_path not in serialized
    assert marker not in serialized and payload.decode() not in serialized
    assert canvas.snapshot()["storage_label"] == "the fake test store"


def test_standalone_fake_services_ignores_environment_path_even_if_active_store(
    tmp_path, monkeypatch,
):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES["google-worker"]
    credentials, _ = fake_mode_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "standalone-fake-store")
    marker = "synthetic-standalone-environment-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    credentials.stores.write(role, payload)
    active_path = Path(role.locator(credentials.stores.root))
    credentials.environ = {role.name: str(active_path)}

    def forbidden(*args, **kwargs):
        raise AssertionError("standalone fake service read an environment-provided path")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", forbidden)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden)
    raw = config.load().raw
    assert credentials.effective(role, raw) is None
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "not_configured", "environment", False,
        "the Settings process environment", False, False)
    serialized = json.dumps(credentials.cards())
    assert str(active_path) not in serialized and marker not in serialized
    assert payload.decode() not in serialized


def test_fake_mode_does_not_read_configured_external_files_or_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_MCP_TOKEN", "never-use-real-environment")
    config = ConfigStore(write_config(tmp_path, google_mcp_credentials_path=str(tmp_path / "external.json")))
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime", fake_mode=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("external secret file read")

    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden)
    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", forbidden)
    cards = credentials.cards()["cards"]
    assert not any(card["can_test"] for card in cards)
    assert credentials.effective(ROLES["notion-mcp"], config.load().raw) is None
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize(("role_slug", "legacy"), [
    ("google-mcp", False), ("google-worker", False),
    ("google-mcp", True), ("google-worker", True),
])
def test_fake_composition_reads_relative_managed_google_from_fixed_fake_store(
    tmp_path, monkeypatch, role_slug, legacy,
):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES[role_slug]
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime", fake_mode=True)
    marker = f"synthetic-fake-managed-{role_slug}-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    credentials.stores.write(role, payload)
    active_path = Path(role.locator(credentials.stores.root))
    configured_path = active_path.relative_to(config.path.parent).as_posix()
    _configure_google_path(config, role, configured_path, legacy=legacy)

    real_read = credential_stores.read_secure_file
    read_paths = []

    def track_fake_store_read(path, *args, **kwargs):
        read_paths.append(Path(path).resolve())
        return real_read(path, *args, **kwargs)

    def forbidden_input_path_read(*args, **kwargs):
        raise AssertionError("fake composition read a supplied Google path")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", track_fake_store_read)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden_input_path_read)
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role_slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "configured", "file", True, "the fake test store", True, False)
    assert read_paths == [active_path]
    serialized = json.dumps(credentials.cards())
    assert str(active_path) not in serialized
    assert configured_path not in serialized
    assert marker not in serialized and payload.decode() not in serialized


def test_fake_composition_reads_only_active_store_for_expanduser_dotdot_path(tmp_path, monkeypatch):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES["google-worker"]
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime", fake_mode=True)
    marker = "synthetic-fake-expanded-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    credentials.stores.write(role, payload)
    active_path = Path(role.locator(credentials.stores.root))
    configured_path = f"~/runtime/fake-secrets/../fake-secrets/{active_path.name}"
    monkeypatch.setenv("HOME", str(tmp_path))
    _configure_google_path(config, role, configured_path)

    real_read = credential_stores.read_secure_file
    read_paths = []

    def track_fake_store_read(path, *args, **kwargs):
        read_paths.append(Path(path).resolve())
        return real_read(path, *args, **kwargs)

    def forbidden_input_path_read(*args, **kwargs):
        raise AssertionError("fake composition read a supplied Google path")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", track_fake_store_read)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden_input_path_read)
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "configured", "file", True, "the fake test store", True, False)
    assert read_paths == [active_path]
    serialized = json.dumps(credentials.cards())
    assert str(active_path) not in serialized and configured_path not in serialized
    assert marker not in serialized and payload.decode() not in serialized


def test_fake_composition_never_reads_environment_path_even_if_it_is_active_store(
    tmp_path, monkeypatch,
):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES["google-mcp"]
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime", fake_mode=True)
    marker = "synthetic-fake-environment-path-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    credentials.stores.write(role, payload)
    active_path = Path(role.locator(credentials.stores.root))
    credentials.environ = {role.name: str(active_path)}

    def forbidden(*args, **kwargs):
        raise AssertionError("fake composition read an environment-provided path")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", forbidden)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden)
    raw = config.load().raw
    assert credentials.effective(role, raw) is None
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "not_configured", "environment", False, "the Settings process environment", False, False)
    serialized = json.dumps(credentials.cards())
    assert str(active_path) not in serialized and marker not in serialized
    assert payload.decode() not in serialized


@pytest.mark.parametrize("candidate_kind", ["staged", "backup", "other-role", "external"])
def test_fake_composition_excludes_nonactive_google_file_candidates(
    tmp_path, monkeypatch, candidate_kind,
):
    config = ConfigStore(write_config(tmp_path))
    role = ROLES["google-mcp"]
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime", fake_mode=True)
    marker = f"synthetic-fake-{candidate_kind}-private-key-sentinel"
    payload = json.dumps({"private_key": marker, "project_id": "synthetic-test-project"}).encode()
    if candidate_kind in {"staged", "backup"}:
        credentials.stores.write(role, payload, candidate_kind)
        candidate_path = Path(role.locator(credentials.stores.root, candidate_kind))
    elif candidate_kind == "other-role":
        other_role = ROLES["google-worker"]
        credentials.stores.write(other_role, payload)
        candidate_path = Path(other_role.locator(credentials.stores.root))
    else:
        candidate_path = tmp_path / "outside" / "external-candidate.json"
        candidate_path.parent.mkdir(mode=0o700)
        candidate_path.write_bytes(payload)
        candidate_path.chmod(0o600)
    _configure_google_path(config, role, str(candidate_path))

    def forbidden(*args, **kwargs):
        raise AssertionError("fake composition read a nonactive Google candidate")

    monkeypatch.setattr("uls.settings.credential_stores.read_secure_file", forbidden)
    monkeypatch.setattr("uls.settings.credential_service.read_secure_file", forbidden)
    card = next(card for card in credentials.cards()["cards"] if card["role"] == role.slug)
    assert (card["state"], card["source"], card["managed"], card["storage_label"],
            card["can_test"], card["can_detach"]) == (
        "not_configured", "external_file", False,
        "the configured external credential file", False, True)
    serialized = json.dumps(credentials.cards())
    assert str(candidate_path) not in serialized and marker not in serialized
    assert payload.decode() not in serialized


def test_linux_composition_is_readonly_and_does_not_select_system_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(composition.sys, "platform", "linux")
    config = ConfigStore(write_config(tmp_path))
    credentials, canvas = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime")
    cards = credentials.cards()["cards"]
    assert not any(card["can_mutate"] for card in cards)
    notion = next(card for card in cards if card["role"] == "notion-mcp")
    assert (notion["state"], notion["environment_variable"], notion["can_mutate"]) == (
        "unsupported_platform", "NOTION_MCP_TOKEN", False)
    with pytest.raises(SettingsServiceError) as error:
        credentials.save(ROLES["notion-mcp"], b"synthetic-linux-token", config.load().generation)
    assert error.value.code == "unsupported_platform"
    assert not (tmp_path / "runtime").exists()
    assert canvas.snapshot()["can_mutate"] is False


def test_macos_startup_only_selects_locators_without_creating_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(composition.sys, "platform", "darwin")
    target = tmp_path / "real-store-placeholder"
    monkeypatch.setattr(composition, "secrets_directory", lambda: target)
    config = ConfigStore(write_config(tmp_path))
    credentials, _ = composition.build_settings_services(
        config, JournalStore(tmp_path / "workspace"), tmp_path / "runtime")
    assert credentials.stores.root == target.resolve()
    assert credentials.stores.backend is None
    assert not target.exists()
