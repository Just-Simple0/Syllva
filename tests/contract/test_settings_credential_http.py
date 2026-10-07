from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from tests.contract._settings_support import FETCH, HOST, NAVIGATE, ORIGIN
from tests.contract.test_settings_credential_service import service

from uls.settings.app import create_settings_app
from uls.settings.composition import build_settings_services
from uls.settings.security import SessionSecurity, new_path_prefix

pytestmark = pytest.mark.contract


def _crash_at(expected):
    from uls.settings.journal import SimulatedCrash
    def crash(point):
        if point == expected:
            raise SimulatedCrash(point)
    return crash


def _promoted_replacement(credentials):
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import SimulatedCrash
    role = ROLES["notion-mcp"]
    generation = credentials.save(role, b"old-preview", credentials.config.load().generation)["config_generation"]
    with pytest.raises(SimulatedCrash):
        credentials.save(role, b"new-preview", generation, replace=True,
                         fault_hook=_crash_at("after_credential_promote_recorded"))
    [pending] = credentials.journal.unresolved()
    return role, pending["operation_id"], generation


def _recovery_snapshot(credentials, role, operation_id):
    reservation_dir = credentials.stores.root / "admission"
    reservations = tuple((str(path.relative_to(reservation_dir)), path.read_bytes())
                         for path in sorted(reservation_dir.rglob("*")) if path.is_file()) if reservation_dir.exists() else ()
    record_path = credentials.journal.directory / f"{operation_id}.json"
    return (credentials.config.path.read_bytes(), credentials.stores.read(role),
            credentials.stores.read(role, "staged"), credentials.stores.read(role, "backup"),
            record_path.read_bytes(), reservations)


@pytest.mark.parametrize("action", ["leave", "retry_delete"])
def test_direct_recovery_refuses_action_that_would_cross_the_recorded_effect_boundary(http, action):
    from uls.settings.config_service import SettingsServiceError
    _client, credentials, _headers = http
    role, operation_id, generation = _promoted_replacement(credentials)
    before = _recovery_snapshot(credentials, role, operation_id)
    with pytest.raises(SettingsServiceError) as error:
        credentials.recover(operation_id, action)
    assert error.value.code == "MANUAL_REVIEW"
    assert _recovery_snapshot(credentials, role, operation_id) == before
    assert credentials.config.load().generation == generation


def test_authenticated_recovery_post_refuses_forged_leave_without_mutation_then_allows_restore(http):
    client, credentials, headers = http
    role, operation_id, generation = _promoted_replacement(credentials)
    before = _recovery_snapshot(credentials, role, operation_id)
    unauthenticated = client.post(f"/api/v1/recovery/{operation_id}/leave", json={}, headers=FETCH)
    assert unauthenticated.status_code == 403
    assert _recovery_snapshot(credentials, role, operation_id) == before
    refused = client.post(f"/api/v1/recovery/{operation_id}/leave", json={}, headers=headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "MANUAL_REVIEW"
    assert _recovery_snapshot(credentials, role, operation_id) == before
    restored = client.post(f"/api/v1/recovery/{operation_id}/restore", json={}, headers=headers)
    assert restored.status_code == 200, restored.text
    assert credentials.stores.read(role) == b"old-preview"
    assert credentials.stores.read(role, "backup") is None
    assert credentials.stores.read(role, "staged") is None
    assert credentials.config.load().generation == generation
    assert credentials.journal.unresolved() == []


def test_retry_delete_cannot_continue_a_transaction_with_non_delete_effects_remaining(http):
    from uls.settings.config_service import SettingsServiceError
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import SimulatedCrash
    _client, credentials, _headers = http
    role = ROLES["notion-mcp"]
    generation = credentials.config.load().generation
    with pytest.raises(SimulatedCrash):
        credentials.save(role, b"stage-only-preview", generation,
                         fault_hook=_crash_at("after_credential_stage_recorded"))
    [pending] = credentials.journal.unresolved()
    operation_id = pending["operation_id"]
    before = _recovery_snapshot(credentials, role, operation_id)
    with pytest.raises(SettingsServiceError) as error:
        credentials.recover(operation_id, "retry_delete")
    assert error.value.code == "MANUAL_REVIEW"
    assert _recovery_snapshot(credentials, role, operation_id) == before
    assert credentials.config.load().generation == generation


def test_leave_on_forget_is_a_locked_noop_and_keeps_the_recovery_reservation(http):
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import SimulatedCrash
    _client, credentials, _headers = http
    role = ROLES["notion-mcp"]
    generation = credentials.save(role, b"forget-preview", credentials.config.load().generation)["config_generation"]
    with pytest.raises(SimulatedCrash):
        credentials.forget(role, generation, fault_hook=_crash_at("after_config_detach_recorded"))
    [pending] = credentials.journal.unresolved()
    operation_id = pending["operation_id"]
    before = _recovery_snapshot(credentials, role, operation_id)
    result = credentials.recover(operation_id, "leave")
    assert result == {"status": "pending", "code": "LEFT_AS_IS"}
    assert _recovery_snapshot(credentials, role, operation_id) == before
    assert credentials.journal.unresolved()[0]["operation_id"] == operation_id


def test_restore_live_mismatch_is_refused_before_recovery_journal_or_store_changes(http):
    from uls.settings.config_service import SettingsServiceError
    _client, credentials, _headers = http
    role, operation_id, _generation = _promoted_replacement(credentials)
    credentials.stores.write(role, b"unexpected-active-preview")
    before = _recovery_snapshot(credentials, role, operation_id)
    with pytest.raises(SettingsServiceError) as error:
        credentials.recover(operation_id, "restore")
    assert error.value.code == "MANUAL_REVIEW"
    assert _recovery_snapshot(credentials, role, operation_id) == before


def test_resume_finishes_already_verified_enrollment_cleanup_without_another_effect(http):
    from uls.config.mutation import ConfigFileLock
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import SimulatedCrash
    _client, credentials, _headers = http
    role = ROLES["notion-mcp"]
    generation = credentials.config.load().generation
    with pytest.raises(SimulatedCrash):
        credentials.save(role, b"cleanup-enrollment-preview", generation,
                         fault_hook=_crash_at("after_credential_stage_recorded"))
    [pending] = credentials.journal.unresolved()
    operation_id = pending["operation_id"]
    with (credentials._admission_context(role, recovery_id=operation_id),
          credentials.journal.role_locks([role.role_key]) as roles,
          credentials.journal.operation(operation_id) as op,
          ConfigFileLock(credentials.config.path) as lock):
        op.switch_branch("abandon", role_locks=roles, config_lock=lock,
                         resolver=credentials.resolver, observe={}, next_action="cleanup")
        stage = op.read()["effects"]["credential_stage"]
        with pytest.raises(SimulatedCrash):
            credentials._effect(op, roles, role, "staged_delete", stage["post_state_id"], "absent",
                                lambda: credentials.stores.delete(role, "staged"),
                                _crash_at("after_staged_delete_recorded"), lock)
    before_config = credentials.config.path.read_bytes()
    assert credentials.stores.read(role) is None and credentials.stores.read(role, "staged") is None
    result = credentials.recover(operation_id, "resume")
    assert result["status"] == "complete"
    assert credentials.config.path.read_bytes() == before_config
    assert credentials.journal.unresolved() == []
    assert not list((credentials.stores.root / "admission").glob("*.reservation"))


def test_resume_finishes_already_verified_replacement_restore_tail_without_another_effect(http):
    from uls.config.mutation import ConfigFileLock
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import SimulatedCrash
    _client, credentials, _headers = http
    role = ROLES["notion-mcp"]
    generation = credentials.save(role, b"restore-tail-old-preview", credentials.config.load().generation)["config_generation"]
    with pytest.raises(SimulatedCrash):
        credentials.save(role, b"restore-tail-new-preview", generation, replace=True,
                         fault_hook=_crash_at("after_credential_promote_recorded"))
    [pending] = credentials.journal.unresolved()
    operation_id = pending["operation_id"]
    with (credentials._admission_context(role, recovery_id=operation_id),
          credentials.journal.role_locks([role.role_key]) as roles,
          credentials.journal.operation(operation_id) as op,
          ConfigFileLock(credentials.config.path) as lock):
            op.switch_branch("restore", role_locks=roles, config_lock=lock,
                             resolver=credentials.resolver, observe={}, next_action="cleanup")
            record = op.read()
            backup_value = credentials.stores.read(role, "backup")
            assert backup_value == b"restore-tail-old-preview"
            credentials._effect(
                op, roles, role, "credential_restore",
                record["effects"]["credential_promote"]["post_state_id"],
                record["effects"]["credential_backup"]["post_state_id"],
                lambda: credentials.stores.write(role, backup_value or b""), None, lock,
            )
            backup = op.read()["effects"]["credential_backup"]
            credentials._effect(op, roles, role, "backup_delete", backup["post_state_id"], "absent",
                                lambda: credentials.stores.delete(role, "backup"), None, lock)
            staged = op.read()["effects"]["credential_stage"]
            with pytest.raises(SimulatedCrash):
                credentials._effect(op, roles, role, "staging_cleanup", staged["post_state_id"], "absent",
                                    lambda: credentials.stores.delete(role, "staged"),
                                    _crash_at("after_staging_cleanup_recorded"), lock)
    before_config = credentials.config.path.read_bytes()
    assert credentials.stores.read(role) == b"restore-tail-old-preview"
    assert credentials.stores.read(role, "backup") is None and credentials.stores.read(role, "staged") is None
    result = credentials.recover(operation_id, "resume")
    assert result["status"] == "complete"
    assert credentials.config.path.read_bytes() == before_config
    assert credentials.stores.read(role) == b"restore-tail-old-preview"
    assert credentials.journal.unresolved() == []
    assert not list((credentials.stores.root / "admission").glob("*.reservation"))


@pytest.fixture
def http(tmp_path):
    s = service(tmp_path)
    credentials, canvas = build_settings_services(s.config, s.journal, tmp_path, fake_mode=True, fake_root=tmp_path)
    prefix = new_path_prefix(); token, security = SessionSecurity.issue()
    app = create_settings_app(s.config, s.journal, security, HOST, prefix=prefix,
                              credential_service=credentials, canvas_service=canvas, fake_mode=True)
    with TestClient(app, base_url=f"{ORIGIN}/{prefix}/") as client:
        assert client.get(f"?bootstrap={token}", headers=NAVIGATE, follow_redirects=False).status_code == 303
        csrf = client.get("/api/v1/session/csrf", headers=FETCH).json()["csrf_token"]
        yield client, credentials, {**FETCH, "origin": ORIGIN, "x-uls-csrf": csrf}


def test_metadata_redacted_and_every_mutation_authenticated(http):
    client, s, headers = http
    assert client.get("/api/v1/credentials", headers=FETCH).status_code == 200
    body = {"secret": "fake-sentinel", "generation": s.config.load().generation}
    assert client.post("/api/v1/credentials/notion-mcp/set", json=body, headers={**FETCH, "origin": ORIGIN}).status_code == 403
    assert client.post("/api/v1/credentials/no-role/set", json=body, headers=headers).status_code == 404
    response = client.post("/api/v1/credentials/notion-mcp/set", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert "fake-sentinel" not in response.text
    cards = client.get("/api/v1/credentials", headers=FETCH).text
    assert "fake-sentinel" not in cards and '"h:' not in cards
    assert client.post("/api/v1/credentials/notion-mcp/forget", json={"generation": s.config.load().generation,
                       "confirm_role": "notion-worker"}, headers=headers).status_code == 400


def test_invalid_preview_replacement_is_terminal_and_old_card_stays_configured(http):
    client, s, headers = http
    from uls.settings.credential_roles import ROLES
    role = ROLES["notion-mcp"]
    response = client.post("/api/v1/credentials/notion-mcp/set", json={"generation": s.config.load().generation, "secret": "valid-preview"}, headers=headers)
    assert response.status_code == 200, response.text
    generation = s.config.load().generation
    response = client.post("/api/v1/credentials/notion-mcp/replace", json={"generation": generation, "secret": "invalid-preview"}, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "INVALID_CREDENTIAL", response.text
    assert s.stores.read(role) == b"valid-preview"
    assert s.stores.read(role, "staged") is None and s.config.load().generation == generation
    assert s.journal.unresolved() == []
    card = next(row for row in client.get("/api/v1/credentials", headers=FETCH).json()["cards"] if row["role"] == role.slug)
    assert card["state"] == "configured" and card["can_mutate"] and card["managed"]


@pytest.mark.parametrize("secret,code", [(b"invalid-preview", "INVALID_CREDENTIAL"), (b"outage-preview", "PROVIDER_UNAVAILABLE")])
def test_persistent_fake_replacement_cleanup_through_directory_alias(tmp_path, secret, code):
    from uls.config.mutation import ConfigFileLock
    from uls.settings.config_service import ConfigStore, SettingsServiceError
    from uls.settings.credential_roles import ROLES
    from uls.settings.fake_mode import FileFakeKeyring

    s = service(tmp_path / "real")
    alias = tmp_path / "alias"
    alias.symlink_to(s.config.path.parent, target_is_directory=True)
    config = ConfigStore(alias / "config.yaml")
    credentials, _ = build_settings_services(config, s.journal, alias, fake_mode=True, fake_root=alias)
    assert isinstance(credentials.stores.backend, FileFakeKeyring)
    assert ConfigFileLock(config.path).config_path == config.path.resolve()
    role = ROLES["notion-mcp"]
    generation = credentials.save(role, b"notion-preview-only", config.load().generation)["config_generation"]
    with pytest.raises(SettingsServiceError) as error:
        credentials.save(role, secret, generation, replace=True)
    assert error.value.code == code
    # Recompose like a new CLI process: all evidence comes from persistent stores.
    fresh, _ = build_settings_services(config, s.journal, alias, fake_mode=True, fake_root=alias)
    assert fresh.stores.read(role) == b"notion-preview-only"
    assert fresh.stores.read(role, "staged") is None and fresh.stores.read(role, "backup") is None
    assert config.load().generation == generation and s.journal.unresolved() == []
    card = next(row for row in fresh.cards()["cards"] if row["role"] == role.slug)
    assert card["state"] == "configured" and card["can_mutate"]
    assert not list((fresh.stores.root / "admission").glob("*.reservation"))


def test_duplicate_keys_and_secret_body_bounds(http):
    client, s, headers = http
    duplicate = '{"secret":"fake","secret":"fake2","generation":"' + s.config.load().generation + '"}'
    assert client.post("/api/v1/credentials/notion-mcp/set", content=duplicate, headers={**headers, "content-type": "application/json"}).status_code == 400
    response = client.post("/api/v1/credentials/notion-mcp/set", json={"secret": "x" * 8192,
                           "generation": s.config.load().generation}, headers=headers)
    assert response.status_code == 413
    assert client.post("/api/v1/credentials/google-mcp/set", content=b"x" * 65537,
                       headers={**headers, "x-uls-generation": s.config.load().generation}).status_code == 413


def test_google_raw_json_upload_with_generation_header(http):
    client, s, headers = http
    payload = {"type": "service_account", "client_email": "mcp@example.com", "private_key": "fake-key",
               "private_key_id": "mcp-key", "project_id": "fake", "token_uri": "https://oauth2.googleapis.com/token"}
    response = client.post("/api/v1/credentials/google-mcp/set", content=json.dumps(payload),
                           headers={**headers, "x-uls-generation": s.config.load().generation})
    assert response.status_code == 200, response.text
    assert "fake-key" not in response.text


def test_canvas_test_fixed_route_and_registry_preview_is_read_only(http):
    client, s, headers = http
    response = client.post("/api/v1/canvas/connect", json={"origin": "https://canvas.example.edu", "secret": "fake-token",
                           "generation": s.config.load().generation}, headers=headers)
    assert response.status_code == 200, response.text
    assert client.post("/api/v1/connections/canvas/test", json={}, headers=headers).status_code == 200
    generation = s.config.load().generation
    body = {"term_id": "678", "course_ids": ["41921"], "generation": generation}
    response = client.post("/api/v1/settings/canvas_registry/validate", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert s.config.load().generation == generation
    body["candidate_hash"] = response.json()["candidate_hash"]
    assert client.post("/api/v1/settings/canvas_registry/apply", json=body, headers=headers).status_code == 200
    assert s.config.load().generation != generation
