"""CredentialService.save_google_oauth: snapshot -> fresh proof -> CAS -> one protected save (P2 plan §3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from tests.contract._settings_support import write_config
from tests.contract.test_settings_credential_service import crash, service

from uls.config import google_oauth as oauth
from uls.config.mutation import atomic_replace_config
from uls.settings.config_service import SettingsServiceError
from uls.settings.credential_roles import ROLES
from uls.settings.journal import SimulatedCrash

pytestmark = pytest.mark.contract

CLIENT = oauth.GoogleOAuthClient("synthetic-client.apps.googleusercontent.com", "synthetic-client-secret")
MCP, WORKER = ROLES["google-mcp"], ROLES["google-worker"]


class Verifier:
    """Injected fresh-grant verifier: records calls, never touches a provider."""

    def __init__(self, permission_id: str = "owner-1") -> None:
        self.permission_id = permission_id
        self.peer_permission_id = permission_id
        self.granted_override: str | list[str] | None = None
        self.granted_by_token: dict[str, object] = {}
        self.calls: list[str] = []
        self.hook = None

    def __call__(self, credential: oauth.AuthorizedUserCredential) -> tuple[object, str]:
        self.calls.append(credential.refresh_token)
        if self.hook is not None:
            self.hook(credential)
        granted = credential.scope if self.granted_override is None else self.granted_override
        if credential.refresh_token in self.granted_by_token:
            granted = self.granted_by_token[credential.refresh_token]
        own = credential.refresh_token.startswith("rt-new")
        return granted, self.permission_id if own else self.peer_permission_id


class _CountingTransport:
    """Wraps the fake provider transport to count Drive resource checks."""

    def __init__(self, inner):
        self.inner = inner
        self.requests = 0

    def request(self, *args, **kwargs):
        self.requests += 1
        return self.inner.request(*args, **kwargs)


def _service(tmp_path: Path, *, oauth_config: bool = True, environ=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = write_config(tmp_path, **({"google_oauth": {"client_id": CLIENT.client_id,
                                                              "client_secret": CLIENT.client_secret}}
                                            if oauth_config else {}))
    s = service(tmp_path, environ=environ)
    assert s.config.path == config_path
    s.oauth_verifier = Verifier()
    s.checks.transport = _CountingTransport(s.checks.transport)
    return s


def _candidate(role, token="rt-new-1"):
    purpose = oauth.GoogleOAuthPurpose.parse(role.purpose)
    return oauth.AuthorizedUserCredential(purpose=purpose, client_id=CLIENT.client_id,
                                          client_secret=CLIENT.client_secret, refresh_token=token, scope=purpose.scope)


def _snapshot(s):
    stores = {(role.slug, slot): s.stores.read(role, slot) for role in (MCP, WORKER) for slot in ("active", "staged", "backup")}
    journal = {path.name: path.read_bytes() for path in s.journal.directory.glob("*.json")} if s.journal.directory.exists() else {}
    admission = s.stores.root / "admission"
    markers = sorted(p.name for p in admission.rglob("*") if p.is_file()) if admission.exists() else []
    return stores, s.config.path.read_bytes(), journal, markers


def _sa(email="sa@example.iam", key_id="kid-1"):
    return json.dumps({"type": "service_account", "client_email": email, "private_key": "p", "private_key_id": key_id,
                       "project_id": "proj", "token_uri": oauth.TOKEN_URI}).encode()


def test_enrollment_persists_canonical_bytes_and_config_path(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation
    result = s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert result["code"] == "VERIFIED" and result["config_generation"] != generation
    stored = s.stores.read(WORKER)
    assert stored == _candidate(WORKER).to_canonical_json()
    assert s.stores.read(WORKER, "staged") is None and s.stores.read(WORKER, "backup") is None
    raw = s.config.load().raw
    assert raw["google_worker_credentials_path"] == WORKER.locator(s.stores.root)
    assert "rt-new-1" not in s.config.path.read_text() and CLIENT.client_secret in s.config.path.read_text()
    assert not s.journal.unresolved()
    assert s.oauth_verifier.calls == ["rt-new-1"]
    assert s.checks.transport.requests == 0, "the OAuth save never performs a Drive resource check"
    card = next(card for card in s.cards()["cards"] if card["role"] == "google-worker")
    assert card["state"] == "configured" and card["credential_type"] == "authorized_user" and card["source"] == "file"
    assert "rt-new-1" not in json.dumps(s.cards())
    assert s.test(WORKER)["code"] == "VERIFIED"
    for name in s.journal.directory.glob("*.json"):
        assert b"rt-new-1" not in name.read_bytes() and b"owner-1" not in name.read_bytes()


def test_replacement_is_bidirectional_and_keeps_old_until_verified(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(MCP), generation, replace=True, fresh_permission_id="owner-1")
    assert error.value.code == "NOT_CONFIGURED"
    generation = s.save_google_oauth(MCP, _candidate(MCP), generation, replace=False, fresh_permission_id="owner-1")["config_generation"]
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(MCP, "rt-new-2"), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "REPLACE_REQUIRED"
    assert s.stores.read(MCP) == _candidate(MCP).to_canonical_json()
    result = s.save_google_oauth(MCP, _candidate(MCP, "rt-new-2"), generation, replace=True, fresh_permission_id="owner-1")
    assert result["code"] == "VERIFIED"
    assert s.stores.read(MCP) == _candidate(MCP, "rt-new-2").to_canonical_json()
    assert s.stores.read(MCP, "backup") is None and not s.journal.unresolved()


@pytest.mark.parametrize("mutate, code", [
    (lambda v: setattr(v, "permission_id", "owner-2"), "ACCOUNT_MISMATCH"),
    (lambda v: setattr(v, "granted_override", oauth.MCP_SCOPE), "OAUTH_GRANT_MISMATCH"),
    (lambda v: setattr(v, "granted_override", [oauth.WORKER_SCOPE, "openid"]), "OAUTH_GRANT_MISMATCH"),
    (lambda v: setattr(v, "hook", lambda _c: (_ for _ in ()).throw(RuntimeError("invalid_grant rt-new-1"))), "RECONNECT_REQUIRED"),
])
def test_fresh_verification_failures_leave_zero_effects(tmp_path, mutate, code):
    s = _service(tmp_path)
    mutate(s.oauth_verifier)
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == code
    assert "rt-new-1" not in error.value.message
    assert _snapshot(s) == before


def test_precheck_failures_happen_before_any_provider_call(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), "stale-generation", replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CONFIGURATION_CHANGED"
    other = oauth.AuthorizedUserCredential(purpose=oauth.GoogleOAuthPurpose.WORKER, client_id="other-client",
                                           client_secret="other-secret", refresh_token="rt-new-9", scope=oauth.WORKER_SCOPE)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, other, generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "OAUTH_CLIENT_MISMATCH"
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "INVALID_CREDENTIAL"
    assert s.oauth_verifier.calls == []
    no_client = _service(tmp_path / "no-client", oauth_config=False)
    with pytest.raises(SettingsServiceError) as error:
        no_client.save_google_oauth(WORKER, _candidate(WORKER), no_client.config.load().generation, replace=False,
                                    fresh_permission_id="owner-1")
    assert error.value.code == "OAUTH_APP_NOT_READY"


def test_environment_or_external_source_is_refused_without_detach(tmp_path):
    external = tmp_path / "external-worker.json"
    external.write_bytes(_sa())
    external.chmod(0o600)
    s = _service(tmp_path, environ={"GOOGLE_MCP_CREDENTIALS_FILE": str(external)})
    generation = s.config.load().generation
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(MCP), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_SOURCE_EXTERNAL"
    raw = s.config.load().raw
    raw["google_worker_credentials_path"] = str(external)
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=True, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_SOURCE_EXTERNAL"
    assert s.oauth_verifier.calls == []


def test_service_account_peer_is_a_type_conflict_before_journal(tmp_path):
    s = _service(tmp_path)
    generation = s.save(MCP, _sa(), s.config.load().generation)["config_generation"]
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_TYPE_CONFLICT"
    assert _snapshot(s) == before and s.oauth_verifier.calls == []
    # Reverse direction: OAuth peer blocks a service-account target through the generic save.
    s.forget(MCP, generation)
    generation = s.save_google_oauth(MCP, _candidate(MCP), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    with pytest.raises(SettingsServiceError) as error:
        s.save(WORKER, _sa("other@example.iam", "kid-2"), generation)
    assert error.value.code == "CREDENTIAL_TYPE_CONFLICT"
    assert s.stores.read(WORKER) is None and s.stores.read(WORKER, "staged") is None and not s.journal.unresolved()


def test_oauth_peer_must_be_the_same_fresh_account_and_distinct_grant(tmp_path):
    s = _service(tmp_path)
    generation = s.save_google_oauth(MCP, _candidate(MCP, "rt-peer-1"), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    s.oauth_verifier.peer_permission_id = "owner-2"
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "ACCOUNT_MISMATCH"
    assert _snapshot(s) == before
    assert s.oauth_verifier.calls[-2:] == ["rt-new-1", "rt-peer-1"]
    s.oauth_verifier.peer_permission_id = "owner-1"
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER, "rt-peer-1"), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.stores.read(WORKER) is None and not s.journal.unresolved()
    assert s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")["code"] == "VERIFIED"
    assert s.stores.read(MCP) == _candidate(MCP, "rt-peer-1").to_canonical_json()


def test_unselected_peer_credential_in_the_store_still_joins_the_fresh_account_proof(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation
    # MCP active bytes exist in the protected store but the role is not selected in config.
    s.stores.write(MCP, _candidate(MCP, "rt-peer-unselected").to_canonical_json())
    assert s.effective(MCP, s.config.load().raw) is None
    s.oauth_verifier.peer_permission_id = "owner-b"
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "ACCOUNT_MISMATCH"
    assert s.oauth_verifier.calls[-2:] == ["rt-new-1", "rt-peer-unselected"]
    assert _snapshot(s) == before
    s.oauth_verifier.peer_permission_id = "owner-1"
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER, "rt-peer-unselected"), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")["code"] == "VERIFIED"


def test_unselected_service_account_peer_in_the_store_is_a_type_conflict(tmp_path):
    s = _service(tmp_path)
    s.stores.write(MCP, _sa())
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_TYPE_CONFLICT"
    assert _snapshot(s) == before and s.oauth_verifier.calls == []


def test_config_change_between_snapshot_and_provider_proof_is_refused(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation

    def drift(_credential):
        raw = s.config.load().raw
        raw["system"]["timezone"] = "UTC"
        atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    s.oauth_verifier.hook = drift
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert s.stores.read(WORKER) is None and not s.journal.unresolved()
    assert "google_worker_credentials_path" not in s.config.load().raw


def test_another_writer_holding_the_config_lock_during_provider_proof_is_detected_without_deadlock(tmp_path):
    import threading

    from uls.config.mutation import ConfigFileLock

    s = _service(tmp_path)
    generation = s.config.load().generation
    released = threading.Event()

    def other_writer():
        from uls.config.mutation import read_config_bytes
        with ConfigFileLock(s.config.path):
            raw = s.config._parse(read_config_bytes(s.config.path)).raw
            raw["system"]["timezone"] = "UTC"
            atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
            released.wait(0.3)  # keep the lock while the save side finishes its provider proof

    def during_proof(_credential):
        threading.Thread(target=other_writer).start()
        import time as _time
        _time.sleep(0.1)  # the other writer now holds the config lock
    s.oauth_verifier.hook = during_proof
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    released.set()
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert s.stores.read(WORKER) is None and s.stores.read(WORKER, "staged") is None and not s.journal.unresolved()
    assert "google_worker_credentials_path" not in s.config.load().raw


def test_peer_store_change_during_provider_proof_is_refused(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation

    def peer_appears(_credential):
        s.stores.write(MCP, _candidate(MCP, "rt-peer-x").to_canonical_json())
    s.oauth_verifier.hook = peer_appears
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert s.stores.read(WORKER) is None and not s.journal.unresolved()


def test_policy_rejection_after_provider_proof_leaves_journal_and_admission_untouched(tmp_path):
    s = _service(tmp_path)
    generation = s.save_google_oauth(MCP, _candidate(MCP, "rt-new-shared"), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER, "rt-new-shared"), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_PURPOSE_CONFLICT"
    assert _snapshot(s) == before, "no journal record, admission marker, staging or config byte changed"
    assert s.checks.transport.requests == 0


def test_peer_grant_scope_drift_is_rejected_before_any_effect(tmp_path):
    s = _service(tmp_path)
    generation = s.save_google_oauth(MCP, _candidate(MCP, "rt-peer-1"), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    before = _snapshot(s)
    for drifted in ([oauth.MCP_SCOPE, "openid"], oauth.WORKER_SCOPE, ""):
        s.oauth_verifier.granted_by_token = {"rt-peer-1": drifted}
        with pytest.raises(SettingsServiceError) as error:
            s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
        assert error.value.code == "OAUTH_GRANT_MISMATCH"
        assert s.oauth_verifier.calls[-2:] == ["rt-new-1", "rt-peer-1"]
        assert _snapshot(s) == before
    s.oauth_verifier.granted_by_token = {}
    assert s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")["code"] == "VERIFIED"


@pytest.mark.parametrize("direction", ["environment", "external_file"])
def test_external_or_environment_oauth_peer_is_refused_before_provider_proof(tmp_path, direction):
    external = tmp_path / "external-peer.json"
    external.write_bytes(_candidate(MCP, "rt-peer-ext").to_canonical_json())
    external.chmod(0o600)
    environ = {"GOOGLE_MCP_CREDENTIALS_FILE": str(external)} if direction == "environment" else None
    s = _service(tmp_path, environ=environ)
    if direction == "external_file":
        raw = s.config.load().raw
        raw["google_mcp_credentials_path"] = str(external)
        atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_SOURCE_EXTERNAL"
    assert s.oauth_verifier.calls == [] and s.checks.transport.requests == 0
    assert _snapshot(s) == before


def test_config_privacy_drift_between_proof_and_commit_is_refused(tmp_path):
    s = _service(tmp_path)
    generation = s.config.load().generation
    s.oauth_verifier.hook = lambda _credential: s.config.path.chmod(0o644)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "OAUTH_APP_NOT_READY"
    assert s.stores.read(WORKER) is None and not s.journal.unresolved()
    s.config.path.chmod(0o600)
    s.oauth_verifier.hook = None
    assert s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1")["code"] == "VERIFIED"


@pytest.mark.parametrize("mutate, code", [
    (lambda info: info.update(scopes=[oauth.MCP_SCOPE]), "OAUTH_GRANT_MISMATCH"),
    (lambda info: info.update(client_id="other-client"), "OAUTH_CLIENT_MISMATCH"),
    (lambda info: info.update(extra="x"), "INVALID_CREDENTIAL"),
    (lambda info: info.pop("refresh_token"), "INVALID_CREDENTIAL"),
])
def test_connection_test_applies_exact_oauth_contract_before_provider_read(tmp_path, mutate, code):
    s = _service(tmp_path)
    generation = s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    assert s.test(WORKER)["code"] == "VERIFIED"
    assert s.checks.transport.requests > 0
    s.checks.transport.requests = 0
    s.checks._last.clear()
    info = _candidate(WORKER).to_info()
    mutate(info)
    s.stores.write(WORKER, json.dumps(info, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(SettingsServiceError) as error:
        s.test(WORKER)
    assert error.value.code == code
    assert s.checks.transport.requests == 0
    assert s.config.load().generation == generation


@pytest.mark.parametrize("point", ["after_credential_stage_recorded", "after_credential_promote_recorded"])
def test_crash_recovery_completes_oauth_enrollment_with_exact_type(tmp_path, point):
    s = _service(tmp_path)
    generation = s.config.load().generation
    with pytest.raises(SimulatedCrash):
        s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False, fresh_permission_id="owner-1",
                            fault_hook=crash(point))
    [pending] = s.journal.unresolved()
    assert s.recover(pending["operation_id"], "resume")["status"] == "complete"
    assert s.stores.read(WORKER) == _candidate(WORKER).to_canonical_json()
    assert s.stores.read(WORKER, "staged") is None and not s.journal.unresolved()
    assert s.config.load().raw["google_worker_credentials_path"] == WORKER.locator(s.stores.root)


def test_crashed_oauth_replacement_can_restore_the_previous_grant(tmp_path):
    s = _service(tmp_path)
    generation = s.save_google_oauth(WORKER, _candidate(WORKER), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    with pytest.raises(SimulatedCrash):
        s.save_google_oauth(WORKER, _candidate(WORKER, "rt-new-2"), generation, replace=True, fresh_permission_id="owner-1",
                            fault_hook=crash("after_credential_promote_recorded"))
    [pending] = s.journal.unresolved()
    assert s.recover(pending["operation_id"], "restore")["status"] == "complete"
    assert s.stores.read(WORKER) == _candidate(WORKER).to_canonical_json()
    assert s.stores.read(WORKER, "backup") is None and not s.journal.unresolved()


def test_forget_removes_only_the_requested_role(tmp_path):
    s = _service(tmp_path)
    generation = s.save_google_oauth(MCP, _candidate(MCP, "rt-peer-1"), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    generation = s.save_google_oauth(WORKER, _candidate(WORKER), generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    s.forget(WORKER, generation)
    assert s.stores.read(WORKER) is None and s.stores.read(MCP) is not None
    raw = s.config.load().raw
    assert "google_worker_credentials_path" not in raw and raw["google_mcp_credentials_path"]


def test_service_account_pair_is_unchanged_when_google_oauth_is_absent(tmp_path):
    s = _service(tmp_path, oauth_config=False)
    generation = s.save(MCP, _sa(), s.config.load().generation)["config_generation"]
    generation = s.save(WORKER, _sa("worker@example.iam", "kid-2"), generation)["config_generation"]
    cards = {card["role"]: card for card in s.cards()["cards"]}
    assert cards["google-mcp"]["credential_type"] == "service_account"
    assert cards["google-worker"]["credential_type"] == "service_account"
    assert s.test(MCP)["code"] == "VERIFIED"
    s.forget(MCP, generation)
    assert s.stores.read(MCP) is None and s.stores.read(WORKER) is not None


def test_same_role_type_switch_requires_forget_in_both_directions(tmp_path):
    s = _service(tmp_path)
    generation = s.save(MCP, _sa(), s.config.load().generation)["config_generation"]
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(MCP), generation, replace=True, fresh_permission_id="owner-1")
    assert error.value.code == "CREDENTIAL_TYPE_CONFLICT"
    assert _snapshot(s) == before and s.oauth_verifier.calls == []
    s.forget(MCP, generation)
    generation = s.save_google_oauth(MCP, _candidate(MCP), s.config.load().generation, replace=False,
                                     fresh_permission_id="owner-1")["config_generation"]
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save(MCP, _sa(), generation, replace=True)
    assert error.value.code == "CREDENTIAL_TYPE_CONFLICT"
    assert _snapshot(s) == before
    # Same-type replacements stay allowed.
    assert s.save_google_oauth(MCP, _candidate(MCP, "rt-new-2"), generation, replace=True, fresh_permission_id="owner-1")["code"] == "VERIFIED"


def test_unrecognized_google_credential_type_is_never_treated_as_a_service_account(tmp_path):
    s = _service(tmp_path)
    orphan = json.dumps({"type": "unexpected", "client_email": "other@example.iam", "private_key_id": "kid-9",
                         "private_key": "p", "project_id": "proj", "token_uri": oauth.TOKEN_URI}).encode()
    s.stores.write(WORKER, orphan)
    before = _snapshot(s)
    with pytest.raises(SettingsServiceError) as error:
        s.save(MCP, _sa(), s.config.load().generation)
    assert error.value.code == "INVALID_CREDENTIAL"
    assert _snapshot(s) == before and not s.journal.unresolved()
    with pytest.raises(SettingsServiceError) as error:
        s.save_google_oauth(MCP, _candidate(MCP), s.config.load().generation, replace=False, fresh_permission_id="owner-1")
    assert error.value.code == "INVALID_CREDENTIAL" and _snapshot(s) == before
    # A selected credential whose type was corrupted: Connection Test refuses before any loader/provider call.
    raw = s.config.load().raw
    raw["google_worker_credentials_path"] = WORKER.locator(s.stores.root)
    atomic_replace_config(s.config.path, yaml.safe_dump(raw, sort_keys=False).encode())
    s.checks.transport.requests = 0
    with pytest.raises(SettingsServiceError) as error:
        s.test(WORKER)
    assert error.value.code == "INVALID_CREDENTIAL" and getattr(error.value, "code_only", False) is True
    assert s.checks.transport.requests == 0
    from uls.settings.provider_checks import LiveReadOnlyTransport, TransportCheckBudget
    live = LiveReadOnlyTransport(google_loader=lambda *a, **k: (_ for _ in ()).throw(AssertionError("SA loader used")))
    with pytest.raises(SettingsServiceError) as error:
        live._google(orphan, TransportCheckBudget())
    assert error.value.code == "INVALID_CREDENTIAL"


def test_concurrent_cross_role_commits_with_different_accounts_never_both_persist(tmp_path):
    import threading

    from uls.settings.journal import JournalError

    s = _service(tmp_path)
    generation = s.config.load().generation
    gate = threading.Barrier(2, timeout=5)
    arrived = threading.Lock()
    inside = {"count": 0, "max": 0}

    class _RacingVerifier(Verifier):
        def __call__(self, credential):
            with arrived:
                inside["count"] += 1
                inside["max"] = max(inside["max"], inside["count"])
            try:
                gate.wait()  # only reached by a thread that holds the pair admission
            except threading.BrokenBarrierError:
                pass
            with arrived:
                inside["count"] -= 1
            own = credential.refresh_token.startswith("rt-new")
            return credential.scope, ("owner-a" if own else "owner-b") if credential.purpose is oauth.GoogleOAuthPurpose.MCP else ("owner-b" if own else "owner-a")
    s.oauth_verifier = _RacingVerifier()
    outcomes: dict[str, object] = {}

    def run(name, role, token, account):
        try:
            outcomes[name] = s.save_google_oauth(role, _candidate(role, token), generation, replace=False,
                                                 fresh_permission_id=account)["code"]
        except (SettingsServiceError, JournalError) as exc:
            outcomes[name] = exc.code
    threads = [threading.Thread(target=run, args=("mcp", MCP, "rt-new-mcp", "owner-a")),
               threading.Thread(target=run, args=("worker", WORKER, "rt-new-worker", "owner-b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert inside["max"] == 1, "pair admission serializes the two commits; verifiers never overlap"
    stored = {role.slug for role in (MCP, WORKER) if s.stores.read(role) is not None}
    assert len(stored) <= 1, f"two different accounts must never both persist: {outcomes}"
    assert not s.journal.unresolved()


def test_generic_upload_never_accepts_authorized_user_bytes(tmp_path):
    s = _service(tmp_path)
    with pytest.raises(SettingsServiceError) as error:
        s.save(WORKER, _candidate(WORKER).to_canonical_json(), s.config.load().generation)
    assert error.value.code == "INVALID_CREDENTIAL"
    assert s.stores.read(WORKER) is None and not s.journal.unresolved()
