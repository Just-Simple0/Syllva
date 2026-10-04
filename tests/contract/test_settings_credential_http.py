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
