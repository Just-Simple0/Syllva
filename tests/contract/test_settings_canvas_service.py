"""Offline Canvas service contracts using real journals and fake credentials/API."""
from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from uls.settings.canvas_checks import CanvasCheckError
from uls.settings.canvas_service import CanvasService, canvas_lease_binding, canvas_profile_id
from uls.settings.config_service import ConfigStore, SettingsServiceError
from uls.settings.credential_roles import canvas_role
from uls.settings.credential_service import CredentialService
from uls.settings.credential_stores import CredentialStores, FakeKeyring
from uls.settings.journal import JournalStore, SimulatedCrash
from uls.settings.provider_checks import FakeProviderTransport, ProviderChecks

pytestmark = pytest.mark.contract
ORIGIN = "https://canvas.example.edu"
OLD = "PAT_OLD_SENTINEL"
NEW = "PAT_NEW_SENTINEL"


class FakeCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.users = {OLD: "42", NEW: "42", "other-account": "43"}
        self.rows = [{"course_id": "12", "term_id": "1", "term_name": "Term One",
                      "name": "Algorithms", "code": "A101"},
                     {"course_id": "13", "term_id": "1", "term_name": "Term One",
                      "name": "Databases", "code": "D101"}]

    def verify(self, origin: str, token: str) -> dict[str, str]:
        assert origin == ORIGIN
        self.calls.append(("verify", token, ()))
        if token not in self.users:
            raise CanvasCheckError("INVALID_CREDENTIAL")
        return {"user_id": self.users[token], "display_name": "Student N."}

    def discover(self, origin: str, token: str) -> dict[str, Any]:
        self.calls.append(("discover", token, ()))
        return {"courses": copy.deepcopy(self.rows), "headers": {"secret": token}, "extra": token}

    def reread(self, origin: str, token: str, ids: Any) -> list[dict[str, str]]:
        self.calls.append(("reread", token, tuple(ids)))
        return [copy.deepcopy(row) for row in self.rows if row["course_id"] in ids]


@dataclass
class Harness:
    service: CanvasService
    credentials: CredentialService
    config: ConfigStore
    journal: JournalStore
    stores: CredentialStores
    keyring: FakeKeyring
    api: FakeCanvas
    now: list[datetime]

    @property
    def generation(self) -> str:
        return self.config.load().generation

    @property
    def profile(self) -> dict[str, str]:
        return self.config.load().raw["canvas"]["profile"]  # type: ignore[no-any-return]

    def connect(self) -> None:
        self.service.connect(ORIGIN, OLD, self.generation)

    def select(self, ids: list[str] | None = None) -> None:
        chosen = ids or ["12"]
        generation = self.generation
        preview = self.service.save_selection("1", chosen, generation)
        self.service.apply_selection("1", chosen, generation, preview["candidate_hash"])


def make_harness(tmp_path: Path, *, platform: str = "darwin") -> Harness:
    repo = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = str(tmp_path / "workspace")
    raw["behavior_contract"]["path"] = str(repo / "contracts" / "study-behavior.md")
    raw["x_user_work"] = {"keep": [1, 2, 3]}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    path.chmod(0o600)
    config = ConfigStore(path)
    journal = JournalStore(tmp_path / "workspace")
    backend = FakeKeyring()
    stores = CredentialStores(tmp_path / "fake-secrets", backend=backend)
    credentials = CredentialService(config, journal, stores,
        ProviderChecks(FakeProviderTransport(), cooldown=0), platform=platform, environ={})
    api = FakeCanvas()
    now = [datetime(2026, 10, 1, tzinfo=UTC)]
    service = CanvasService(credentials, verifier=api.verify, discoverer=api.discover,
                            selection_reader=api.reread, now=lambda: now[0])
    return Harness(service, credentials, config, journal, stores, backend, api, now)


def test_connect_preverify_stage_reverify_and_single_profile(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    before = h.generation
    h.connect()
    role = canvas_role(h.profile["id"])
    assert h.profile["id"] == canvas_profile_id(ORIGIN, "42")
    assert h.stores.read(role) == OLD.encode()
    assert h.stores.read(role, "staged") is None
    assert h.api.calls == [("verify", OLD, ())] * 2
    assert h.generation != before
    snapshot = h.service.snapshot()
    assert snapshot["state"] == "partial"
    assert snapshot["masked_account"] == "S•••••• N••"
    assert snapshot["storage_label"] == "this Mac's Keychain"
    calls = list(h.api.calls)
    with pytest.raises(SettingsServiceError, match="could not be completed") as error:
        h.service.connect(ORIGIN, NEW, h.generation)
    assert error.value.code == "PROFILE_EXISTS"
    assert h.api.calls == calls
    assert not h.journal.unresolved()


def test_preverification_failure_writes_nothing_and_secret_never_escapes(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    original = h.config.path.read_bytes()
    with pytest.raises(SettingsServiceError) as error:
        h.service.connect(ORIGIN, "invalid-PAT", h.generation)
    assert error.value.code == "INVALID_CREDENTIAL"
    assert error.value.__context__ is None
    assert h.config.path.read_bytes() == original
    assert h.keyring.values == {}
    assert not list(h.journal.directory.glob("*.json"))


def test_staged_verification_failure_cleans_staging_before_activation(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    original = h.config.path.read_bytes()
    count = [0]

    def verify(origin: str, token: str) -> dict[str, str]:
        count[0] += 1
        if count[0] == 2:
            raise RuntimeError(token)
        return h.api.verify(origin, token)

    h.service.verifier = verify
    with pytest.raises(SettingsServiceError) as error:
        h.connect()
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert OLD not in str(error.value)
    assert error.value.__context__ is None
    assert h.config.path.read_bytes() == original
    assert h.keyring.values == {}
    assert not h.journal.unresolved()


def test_replace_same_account_works_with_revoked_active_token_and_changes_revision(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    role = canvas_role(h.profile["id"])
    before = h.config.load()
    h.api.users.pop(OLD)
    h.api.calls.clear()
    h.service.replace(NEW, before.generation)
    after = h.config.load()
    assert before.raw["canvas"] == after.raw["canvas"]
    assert before.raw["credential_revisions"][role.slug] != after.raw["credential_revisions"][role.slug]
    assert h.api.calls == [("verify", NEW, ())] * 2
    assert h.stores.read(role) == NEW.encode()
    assert h.stores.read(role, "backup") is None


@pytest.mark.parametrize("token,code", [("other-account", "PROFILE_MISMATCH"),
                                       ("invalid-PAT", "INVALID_CREDENTIAL")])
def test_failed_replacement_preserves_old_binding_before_staging(
        tmp_path: Path, token: str, code: str) -> None:
    h = make_harness(tmp_path)
    h.connect()
    original = h.config.path.read_bytes()
    keys = copy.deepcopy(h.keyring.values)
    with pytest.raises(SettingsServiceError) as error:
        h.service.replace(token, h.generation)
    assert error.value.code == code
    assert h.config.path.read_bytes() == original
    assert h.keyring.values == keys


def test_forget_has_no_provider_dependency_and_detaches_every_canvas_binding(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    h.service.renew(h.generation)
    profile = h.profile
    before = h.config.load()
    h.api.calls.clear()

    def denied(*args: Any) -> Any:
        pytest.fail("forget must not invoke any provider")

    h.service.verifier = denied
    h.service.discoverer = denied
    h.service.selection_reader = denied
    h.service.forget(profile["id"], h.generation)
    after = h.config.load()
    assert after.raw["canvas"] == {"sync_enabled": False}
    assert h.stores.read(canvas_role(profile["id"])) is None
    assert before.raw["x_user_work"] == after.raw["x_user_work"]
    assert h.api.calls == []
    assert not h.journal.unresolved()


def test_forget_confirmation_and_stale_generation_have_no_side_effects(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    stale = h.generation
    h.connect()
    original = h.config.path.read_bytes()
    for confirmation, generation, code in (("wrong", h.generation, "PROFILE_MISMATCH"),
                                           (h.profile["id"], stale, "CONFIGURATION_CHANGED")):
        with pytest.raises(SettingsServiceError) as error:
            h.service.forget(confirmation, generation)
        assert error.value.code == code
    assert h.config.path.read_bytes() == original


def test_selection_preview_only_and_apply_rereads_exact_reviewed_ids(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    before = h.config.path.read_bytes()
    records = list(h.journal.directory.glob("*.json"))
    generation = h.generation
    preview = h.service.save_selection("1", ["13", "12"], generation)
    assert preview["valid"] is True
    assert h.config.path.read_bytes() == before
    assert list(h.journal.directory.glob("*.json")) == records
    h.service.apply_selection("1", ["12", "13"], generation, preview["candidate_hash"])
    registry = h.config.load().raw["canvas"]["registry"]
    assert [row["course_id"] for row in registry["courses"]] == ["12", "13"]
    assert all(set(row) == {"course_id", "term_id", "name", "code"} for row in registry["courses"])
    assert [call[0] for call in h.api.calls].count("reread") == 2
    assert h.service.snapshot()["state"] == "ready"


def test_selection_apply_refuses_unreviewed_changed_metadata_or_different_ids(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    original = h.config.path.read_bytes()
    generation = h.generation
    preview = h.service.save_selection("1", ["12"], generation)
    for ids, candidate, code in ((["12"], "", "REVIEW_REQUIRED"),
                                 (["13"], preview["candidate_hash"], "REVIEW_STALE")):
        with pytest.raises(SettingsServiceError) as error:
            h.service.apply_selection("1", ids, generation, candidate)
        assert error.value.code == code
    h.api.rows[0]["name"] = "Renamed by provider"
    with pytest.raises(SettingsServiceError) as error:
        h.service.apply_selection("1", ["12"], generation, preview["candidate_hash"])
    assert error.value.code == "REVIEW_STALE"
    assert h.config.path.read_bytes() == original


def test_selection_rejects_unreturned_ids_mixed_term_and_selection_limit(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    generation = h.generation
    for term, ids in (("2", ["12"]), ("1", ["99"]), ("1", ["12", "12"]),
                      ("1", [str(index) for index in range(21)])):
        with pytest.raises(SettingsServiceError) as error:
            h.service.save_selection(term, ids, generation)
        assert error.value.code == "NOT_FOUND"
    assert h.generation == generation


def test_renew_checks_registry_before_keyring_and_provider_then_preserves_revision(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path)
    h.connect()
    calls = list(h.api.calls)
    with monkeypatch.context() as scoped:
        scoped.setattr(h.stores, "read", lambda *args, **kwargs: pytest.fail("keyring before registry validation"))
        with pytest.raises(SettingsServiceError):
            h.service.renew(h.generation)
    assert h.api.calls == calls
    h.select()
    before = h.config.load()
    h.service.renew(before.generation)
    after = h.config.load()
    assert before.raw["credential_revisions"] == after.raw["credential_revisions"]
    lease = after.raw["canvas"]["lease"]
    assert datetime.fromisoformat(lease["expires_at"]) - datetime.fromisoformat(lease["issued_at"]) == timedelta(days=30)
    assert lease["binding_hash"] == canvas_lease_binding(h.profile, after.raw["canvas"]["registry"])
    assert h.service.snapshot()["lease"]["state"] == "active"
    h.now[0] += timedelta(days=25)
    assert h.service.snapshot()["lease"]["state"] == "expiring"
    h.now[0] += timedelta(days=6)
    assert h.service.snapshot()["lease"]["state"] == "expired"
    assert h.service.snapshot()["state"] == "ready"


def test_changed_registry_requires_renewal_and_401_leaves_existing_lease_unchanged(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    h.service.renew(h.generation)
    before = h.config.path.read_bytes()
    h.api.users.pop(OLD)
    with pytest.raises(SettingsServiceError) as error:
        h.service.renew(h.generation)
    assert error.value.code == "INVALID_CREDENTIAL"
    assert h.config.path.read_bytes() == before
    h.api.users[OLD] = "42"
    h.select(["13"])
    assert h.service.snapshot()["lease"]["state"] == "needs_renewal"


def test_disable_sync_never_reads_keyring_calls_provider_or_changes_lease(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    h.service.renew(h.generation)
    before = h.config.load()
    calls = list(h.api.calls)
    monkeypatch.setattr(h.stores, "read", lambda *args, **kwargs: pytest.fail("disable read keyring"))
    h.service.disable_sync(before.generation)
    assert h.config.load().raw["canvas"]["lease"] == before.raw["canvas"]["lease"]
    assert h.config.load().raw["canvas"]["sync_enabled"] is False
    assert h.api.calls == calls


def test_test_and_discovery_return_only_metadata_and_never_persist(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    original = h.config.path.read_bytes()
    assert h.service.test()["code"] == "VERIFIED"
    discovery = h.service.discover()
    assert set(discovery) == {"courses", "terms"}
    assert h.config.path.read_bytes() == original
    assert OLD not in json_output(discovery)
    assert OLD not in json_output(h.service.snapshot())
    assert OLD not in "".join(path.read_text() for path in h.journal.directory.glob("*.json"))


def json_output(value: Any) -> str:
    import json
    return json.dumps(value)


def test_linux_is_read_only_without_keyring_or_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path, platform="linux")
    monkeypatch.setattr(h.stores, "read", lambda *args, **kwargs: pytest.fail("Linux keyring access"))
    assert h.service.snapshot()["state"] == "unsupported_platform"
    with pytest.raises(SettingsServiceError):
        h.service.connect(ORIGIN, OLD, h.generation)
    assert h.api.calls == []


def _crash_at(point: str) -> Any:
    def fault(name: str) -> None:
        if name == point:
            raise SimulatedCrash(point)
    return fault


def fresh_service(h: Harness) -> CanvasService:
    credentials = CredentialService(h.config, JournalStore(h.config.load().config.system.workspace_dir),
        CredentialStores(h.stores.root, backend=h.keyring), ProviderChecks(FakeProviderTransport()),
        platform="darwin", environ={})
    return CanvasService(credentials, verifier=h.api.verify, discoverer=h.api.discover,
                         selection_reader=h.api.reread, now=lambda: h.now[0])


@pytest.mark.parametrize("point", [
    "after_credential_stage", "after_credential_stage_recorded",
    "before_config_commit", "after_config_commit", "after_config_commit_recorded",
    "before_credential_promote", "after_credential_promote", "after_credential_promote_recorded",
    "before_staging_cleanup", "after_staging_cleanup", "after_staging_cleanup_recorded",
])
def test_enrollment_recovers_each_effect_boundary_in_fresh_service(tmp_path: Path, point: str) -> None:
    h = make_harness(tmp_path)
    h.service.fault_hook = _crash_at(point)
    with pytest.raises(SimulatedCrash):
        h.connect()
    pending = h.journal.unresolved()
    assert len(pending) == 1
    restarted = fresh_service(h)
    restarted.credentials.recover(pending[0]["operation_id"], "resume")
    assert restarted.snapshot()["profile"]["user_id"] == "42"
    assert h.stores.read(canvas_role(canvas_profile_id(ORIGIN, "42"))) == OLD.encode()
    assert h.stores.read(canvas_role(canvas_profile_id(ORIGIN, "42")), "staged") is None
    assert not h.journal.unresolved()


@pytest.mark.parametrize("point", [
    "after_credential_stage", "after_credential_stage_recorded",
    "before_credential_backup", "after_credential_backup", "after_credential_backup_recorded",
    "before_credential_promote", "after_credential_promote", "after_credential_promote_recorded",
    "before_config_commit", "after_config_commit", "after_config_commit_recorded",
    "before_backup_delete", "after_backup_delete", "after_backup_delete_recorded",
    "before_staging_cleanup", "after_staging_cleanup", "after_staging_cleanup_recorded",
])
def test_replacement_recovers_each_effect_boundary_in_fresh_service(tmp_path: Path, point: str) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.service.fault_hook = _crash_at(point)
    with pytest.raises(SimulatedCrash):
        h.service.replace(NEW, h.generation)
    pending = h.journal.unresolved()
    restarted = fresh_service(h)
    restarted.credentials.recover(pending[0]["operation_id"], "resume")
    assert h.stores.read(canvas_role(h.profile["id"])) == NEW.encode()
    assert h.stores.read(canvas_role(h.profile["id"]), "staged") is None
    assert h.stores.read(canvas_role(h.profile["id"]), "backup") is None
    assert not h.journal.unresolved()


@pytest.mark.parametrize("point", ["before_config_detach", "after_config_detach", "after_config_detach_recorded",
                                   "before_credential_delete", "after_credential_delete", "after_credential_delete_recorded"])
def test_forget_recovers_without_provider_in_fresh_service(tmp_path: Path, point: str) -> None:
    h = make_harness(tmp_path)
    h.connect()
    profile = h.profile
    h.service.fault_hook = _crash_at(point)
    with pytest.raises(SimulatedCrash):
        h.service.forget(profile["id"], h.generation)
    restarted = fresh_service(h)
    h.api.calls.clear()
    restarted.credentials.recover(h.journal.unresolved()[0]["operation_id"], "retry_delete")
    assert h.config.load().raw["canvas"] == {"sync_enabled": False}
    assert h.stores.read(canvas_role(profile["id"])) is None
    assert h.api.calls == []
    assert not h.journal.unresolved()


def test_active_token_is_absent_until_enrollment_config_cas(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    events: list[str] = []

    def inspect(name: str) -> None:
        events.append(name)
        if name in {"before_config_commit", "after_config_commit", "before_credential_promote"}:
            assert h.stores.read(canvas_role(canvas_profile_id(ORIGIN, "42"))) is None

    h.service.fault_hook = inspect
    h.connect()
    assert "before_credential_promote" in events


def test_pending_operation_blocks_all_network_and_credential_actions(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.service.fault_hook = _crash_at("after_credential_stage_recorded")
    with pytest.raises(SimulatedCrash):
        h.connect()
    h.api.calls.clear()
    h.service.fault_hook = None
    actions: tuple[Callable[[], Any], ...] = (lambda: h.service.connect(ORIGIN, NEW, h.generation),
                   lambda: h.service.replace(NEW, h.generation),
                   lambda: h.service.forget("wrong", h.generation), h.service.test, h.service.discover,
                   lambda: h.service.save_selection("1", ["12"], h.generation),
                   lambda: h.service.renew(h.generation))
    for action in actions:
        with pytest.raises(SettingsServiceError) as error:
            action()
        assert error.value.code == "OPERATION_IN_PROGRESS"
    assert h.api.calls == []
    assert h.service.snapshot()["state"] == "partial"


def test_lease_cas_loss_after_provider_check_preserves_concurrent_user_change(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    generation = h.generation

    def concurrent_verify(origin: str, token: str) -> dict[str, str]:
        raw = h.config.load().raw
        raw["x_user_work"]["keep"].append(4)
        h.config.path.write_text(yaml.safe_dump(raw, sort_keys=False))
        return h.api.verify(origin, token)

    h.service.verifier = concurrent_verify
    with pytest.raises(SettingsServiceError) as error:
        h.service.renew(generation)
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert h.config.load().raw["x_user_work"]["keep"] == [1, 2, 3, 4]
    assert "lease" not in h.config.load().raw["canvas"]
    assert not h.journal.unresolved()


def test_stage_intent_without_persisted_token_fails_closed_for_manual_recovery(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    original = h.config.path.read_bytes()
    h.service.fault_hook = _crash_at("before_credential_stage")
    with pytest.raises(SimulatedCrash):
        h.connect()
    pending = h.journal.unresolved()
    restarted = fresh_service(h)
    with pytest.raises(SettingsServiceError) as error:
        restarted.credentials.recover(pending[0]["operation_id"], "resume")
    assert error.value.code == "MANUAL_REVIEW"
    assert h.keyring.values == {}
    assert h.config.path.read_bytes() == original
    assert restarted.snapshot()["state"] == "partial"
    assert restarted.disable_sync(h.generation)["config_generation"] == h.generation


def test_staged_replace_failure_keeps_old_token_and_cleans_new_staging(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    original = h.config.path.read_bytes()
    calls = [0]

    def verify(origin: str, token: str) -> dict[str, str]:
        calls[0] += 1
        if calls[0] == 2:
            raise CanvasCheckError("INVALID_CREDENTIAL")
        return h.api.verify(origin, token)

    h.service.verifier = verify
    with pytest.raises(SettingsServiceError) as error:
        h.service.replace(NEW, h.generation)
    assert error.value.code == "INVALID_CREDENTIAL"
    assert h.config.path.read_bytes() == original
    role = canvas_role(h.profile["id"])
    assert h.stores.read(role) == OLD.encode()
    assert h.stores.read(role, "staged") is None
    assert h.stores.read(role, "backup") is None
    assert not h.journal.unresolved()


def test_forget_local_deletion_failure_keeps_detached_config_and_can_retry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path)
    h.connect()
    profile = h.profile
    original_delete = h.stores.delete

    def failed_delete(role: Any, slot: str = "active") -> None:
        raise RuntimeError(OLD)

    with monkeypatch.context() as scoped:
        scoped.setattr(h.stores, "delete", failed_delete)
        with pytest.raises(SettingsServiceError) as error:
            h.service.forget(profile["id"], h.generation)
        assert OLD not in str(error.value)
        assert error.value.__context__ is None
    assert h.config.load().raw["canvas"] == {"sync_enabled": False}
    assert h.stores.read(canvas_role(profile["id"])) == OLD.encode()
    pending = h.journal.unresolved()
    assert h.service.snapshot()["state"] == "partial"
    assert h.stores.delete == original_delete
    h.api.calls.clear()
    fresh_service(h).credentials.recover(pending[0]["operation_id"], "retry_delete")
    assert h.stores.read(canvas_role(profile["id"])) is None
    assert h.api.calls == []


def test_discovery_cooldown_blocks_before_keyring_or_provider(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.service.discover()
    calls = list(h.api.calls)
    with pytest.raises(SettingsServiceError) as error:
        h.service.discover()
    assert error.value.code == "RATE_LIMITED"
    assert h.api.calls == calls


def test_stored_token_for_another_account_cannot_discover_or_save_registry(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.stores.write(canvas_role(h.profile["id"]), b"other-account")
    generation = h.generation
    for action in (h.service.discover, lambda: h.service.save_selection("1", ["12"], generation)):
        with pytest.raises(SettingsServiceError) as error:
            action()
        assert error.value.code == "PROFILE_MISMATCH"
    assert h.generation == generation
    assert all(item[0] not in {"discover", "reread"} for item in h.api.calls)


def test_discovery_total_deadline_includes_account_verification(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path)
    h.connect()
    clock = [100.0]
    monkeypatch.setattr("uls.settings.canvas_service.time.monotonic", lambda: clock[0])

    def delayed_verify(origin: str, token: str) -> dict[str, str]:
        clock[0] += 9
        return h.api.verify(origin, token)

    def delayed_discover(origin: str, token: str) -> dict[str, Any]:
        clock[0] += 37
        return h.api.discover(origin, token)

    h.service.verifier = delayed_verify
    h.service.discoverer = delayed_discover
    with pytest.raises(SettingsServiceError) as error:
        h.service.discover()
    assert error.value.code == "TIMEOUT"


def test_callback_error_codes_and_echoes_are_sanitized(tmp_path: Path) -> None:
    h = make_harness(tmp_path)

    def forged_error(origin: str, token: str) -> Any:
        raise SettingsServiceError(token, token)

    h.service.verifier = forged_error
    with pytest.raises(SettingsServiceError) as error:
        h.connect()
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.__context__ is None
    assert OLD not in str(error.value)
    assert OLD not in repr(error.value)


def test_lease_binding_uses_identity_not_display_text(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    profile, registry = h.profile, h.config.load().raw["canvas"]["registry"]
    binding = canvas_lease_binding(profile, registry)
    profile["display_name"] = "Different display"
    registry["courses"][0]["name"] = "Renamed course"
    assert canvas_lease_binding(profile, registry) == binding
    profile["origin"] = "https://other.example.edu"
    assert canvas_lease_binding(profile, registry) != binding


def test_registry_config_effect_crash_blocks_network_until_common_config_recovery(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.connect()
    preview = h.service.save_selection("1", ["12"], h.generation)
    h.service.fault_hook = _crash_at("after_config_replace")
    with pytest.raises(SimulatedCrash):
        h.service.apply_selection("1", ["12"], h.generation, preview["candidate_hash"])
    pending = h.journal.unresolved()
    calls = list(h.api.calls)
    with pytest.raises(SettingsServiceError) as error:
        h.service.test()
    assert error.value.code == "OPERATION_IN_PROGRESS"
    assert h.api.calls == calls
    h.config.recover(h.journal, pending[0]["operation_id"], "resume")
    assert h.service.snapshot()["state"] == "ready"


def test_default_service_network_helpers_integrate_through_fake_boundary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from uls.settings import canvas_checks
    from uls.settings.canvas_checks import CanvasResponse

    h = make_harness(tmp_path)
    h.connect()
    calls: list[tuple[str, str]] = []
    replies = [CanvasResponse(200, {}, b'{"id":42,"name":"Student"}'),
               CanvasResponse(200, {}, json.dumps([{"id": 12, "name": "Course", "course_code": "C",
                                                   "term": {"id": 1, "name": "T"}}]).encode())]

    class Wire:
        def request(self, method: str, path: str, headers: Any, timeout: float) -> CanvasResponse:
            assert headers["Authorization"] == "Bearer " + OLD
            assert 0 < timeout <= 10
            calls.append((method, path))
            return replies.pop(0)

        def close(self) -> None:
            return

    original_open = canvas_checks.open_canvas_connection

    def fake_open(origin: str, **kwargs: Any) -> Any:
        return original_open(origin, resolver=lambda host, port: ["93.184.216.34"],
                             connector=lambda host, port, address, timeout: Wire())

    monkeypatch.setattr(canvas_checks, "open_canvas_connection", fake_open)
    h.service.verifier = canvas_checks.verify_canvas_user
    h.service.discoverer = canvas_checks.discover_canvas_courses
    result = h.service.discover()
    assert result["terms"] == [{"term_id": "1", "name": "T"}]
    assert len(calls) == 2
    assert calls[0] == ("GET", "/api/v1/users/self")
    assert calls[1][1].startswith("/api/v1/courses?")


def test_malformed_lease_and_policy_change_fail_closed_without_affecting_readiness(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = make_harness(tmp_path)
    h.connect()
    h.select()
    h.service.renew(h.generation)
    with monkeypatch.context() as scoped:
        scoped.setattr("uls.settings.canvas_service.RESOURCE_POLICY", "metadata-policy-v2")
        assert h.service.snapshot()["lease"]["state"] == "needs_renewal"
        assert h.service.snapshot()["state"] == "ready"
    raw = h.config.load().raw
    raw["canvas"]["lease"]["expires_at"] = "not-a-time"
    h.config.path.write_text(yaml.safe_dump(raw, sort_keys=False))
    assert h.service.snapshot()["lease"]["state"] == "needs_renewal"
    assert h.service.snapshot()["state"] == "ready"


@pytest.mark.parametrize("point", ["after_credential_promote_recorded", "before_config_commit"])
def test_replacement_at_original_generation_restores_verified_backup(
        tmp_path: Path, point: str) -> None:
    h = make_harness(tmp_path)
    h.connect()
    original = h.config.path.read_bytes()
    role = canvas_role(h.profile["id"])
    h.service.fault_hook = _crash_at(point)
    with pytest.raises(SimulatedCrash):
        h.service.replace(NEW, h.generation)
    assert h.stores.read(role) == NEW.encode()
    restarted = fresh_service(h)
    restarted.credentials.recover(h.journal.unresolved()[0]["operation_id"], "restore")
    assert h.config.path.read_bytes() == original
    assert h.stores.read(role) == OLD.encode()
    assert h.stores.read(role, "backup") is None
    assert h.stores.read(role, "staged") is None
    assert not h.journal.unresolved()
