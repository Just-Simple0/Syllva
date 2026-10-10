"""Exact-contract tests for the personal Google OAuth config and parser (P2 plan §2/§3/§7)."""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.config import google_oauth as oauth
from uls.config.loader import load_config_unvalidated

pytestmark = pytest.mark.unit

CLIENT = oauth.GoogleOAuthClient(client_id="synthetic-client.apps.googleusercontent.com", client_secret="synthetic-secret-1234")


def _write(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _info(**overrides):
    base = {
        "type": "authorized_user",
        "client_id": CLIENT.client_id,
        "client_secret": CLIENT.client_secret,
        "refresh_token": "synthetic-refresh-token",
        "token_uri": oauth.TOKEN_URI,
        "scopes": [oauth.WORKER_SCOPE],
    }
    base.update(overrides)
    return base


# --- config section -----------------------------------------------------------

def test_absent_and_empty_sections_are_none(tmp_path):
    assert load_config_unvalidated(_write(tmp_path, "system:\n  timezone: Asia/Seoul\n")).google_oauth is None
    assert load_config_unvalidated(_write(tmp_path, "google_oauth:\n")).google_oauth is None
    assert load_config_unvalidated(_write(tmp_path, "google_oauth: {}\n")).google_oauth is None


def test_complete_pair_parses_and_never_reprs_secret(tmp_path):
    cfg = load_config_unvalidated(_write(
        tmp_path, "google_oauth:\n  client_id: abc.apps.googleusercontent.com\n  client_secret: s3cr3t-value\n"))
    assert cfg.google_oauth == oauth.GoogleOAuthClient("abc.apps.googleusercontent.com", "s3cr3t-value")
    assert "s3cr3t-value" not in repr(cfg.google_oauth)
    assert "s3cr3t-value" not in repr(cfg)


@pytest.mark.parametrize("section", [
    "google_oauth:\n  client_id: only-id\n",
    "google_oauth:\n  client_secret: only-secret\n",
    "google_oauth:\n  client_id: id\n  client_secret: secret\n  extra: 1\n",
    "google_oauth:\n  client_id: 1\n  client_secret: secret\n",
    "google_oauth:\n  client_id: id\n  client_secret: ''\n",
    "google_oauth:\n  client_id: ' id'\n  client_secret: secret\n",
    "google_oauth: not-a-mapping\n",
    "google_oauth: [a, b]\n",
])
def test_partial_or_malformed_sections_fail_closed(tmp_path, section):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, section))


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only permission contract")
def test_config_file_privacy_check(tmp_path):
    path = _write(tmp_path, "google_oauth:\n  client_id: id\n  client_secret: secret\n")
    os.chmod(path, 0o600)
    assert oauth.config_file_is_private(path) is True
    os.chmod(path, 0o644)
    assert oauth.config_file_is_private(path) is False
    assert oauth.config_file_is_private(tmp_path / "missing.yaml") is False
    assert oauth.config_file_is_private(tmp_path) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only permission contract")
def test_config_file_privacy_requires_current_user_ownership(tmp_path, monkeypatch):
    import os as _os
    import stat as _stat

    path = _write(tmp_path, "google_oauth:\n  client_id: id\n  client_secret: secret\n")
    os.chmod(path, 0o600)
    real = _os.lstat(path)

    class _Foreign:
        st_mode = real.st_mode
        st_uid = real.st_uid + 1
    monkeypatch.setattr(oauth.os, "lstat", lambda _p: _Foreign())
    assert _stat.S_ISREG(_Foreign.st_mode)
    assert oauth.config_file_is_private(path) is False


def test_config_file_privacy_keeps_windows_semantics(tmp_path, monkeypatch):
    """Windows keeps the pre-P2 readiness meaning: the check never inspects or changes the file."""
    monkeypatch.setattr(oauth.os, "name", "nt")
    monkeypatch.setattr(oauth.os, "lstat", lambda _p: (_ for _ in ()).throw(AssertionError("lstat must not run")))
    assert oauth.config_file_is_private(tmp_path / "missing.yaml") is True
    assert oauth.config_file_is_private(_write(tmp_path, "google_oauth:\n")) is True


# --- purposes -----------------------------------------------------------------

def test_purposes_map_to_exact_scopes_roles_and_names():
    assert oauth.GoogleOAuthPurpose.MCP.scope == oauth.MCP_SCOPE
    assert oauth.GoogleOAuthPurpose.WORKER.scope == oauth.WORKER_SCOPE
    assert oauth.GoogleOAuthPurpose.parse("mcp").role_slug == "google-mcp"
    assert oauth.GoogleOAuthPurpose.parse("worker").credential_name == "GOOGLE_WORKER_CREDENTIALS_FILE"
    with pytest.raises(ValueError):
        oauth.GoogleOAuthPurpose.parse("drive.file")


# --- exact authorized_user parser -------------------------------------------------

def test_exact_six_key_authorized_user_parses_and_round_trips():
    credential = oauth.parse_authorized_user(_info(), purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)
    assert credential.scope == oauth.WORKER_SCOPE
    canonical = credential.to_canonical_json()
    assert canonical == json.dumps(_info(), sort_keys=True, separators=(",", ":")).encode()
    again = oauth.parse_authorized_user_bytes(canonical, purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)
    assert again == credential
    assert "synthetic-refresh-token" not in repr(credential)
    assert CLIENT.client_secret not in repr(credential)


@pytest.mark.parametrize("mutation, code", [
    ({"extra": "x"}, "INVALID_CREDENTIAL"),
    ({"type": "service_account"}, "INVALID_CREDENTIAL"),
    ({"token_uri": "https://evil.example/token"}, "INVALID_CREDENTIAL"),
    ({"refresh_token": ""}, "INVALID_CREDENTIAL"),
    ({"refresh_token": "has space"}, "INVALID_CREDENTIAL"),
    ({"client_id": "other-client"}, "OAUTH_CLIENT_MISMATCH"),
    ({"client_secret": "other-secret"}, "OAUTH_CLIENT_MISMATCH"),
    ({"scopes": oauth.WORKER_SCOPE}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": None}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": []}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": [oauth.WORKER_SCOPE, oauth.WORKER_SCOPE]}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": [oauth.WORKER_SCOPE, "openid"]}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": [oauth.MCP_SCOPE]}, "OAUTH_GRANT_MISMATCH"),
    ({"scopes": ["https://www.googleapis.com/auth/drive.file"]}, "OAUTH_GRANT_MISMATCH"),
])
def test_parser_rejections_use_fixed_codes(mutation, code):
    info = _info(**mutation)
    with pytest.raises(oauth.GoogleOAuthCredentialError) as error:
        oauth.parse_authorized_user(info, purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)
    assert error.value.oauth_code == code
    assert "synthetic-refresh-token" not in str(error.value)
    assert CLIENT.client_secret not in str(error.value)


def test_missing_key_is_rejected():
    info = _info()
    del info["refresh_token"]
    with pytest.raises(oauth.GoogleOAuthCredentialError) as error:
        oauth.parse_authorized_user(info, purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)
    assert error.value.oauth_code == "INVALID_CREDENTIAL"


def test_mcp_purpose_requires_read_only_scope():
    with pytest.raises(oauth.GoogleOAuthCredentialError) as error:
        oauth.parse_authorized_user(_info(), purpose=oauth.GoogleOAuthPurpose.MCP, client=CLIENT)
    assert error.value.oauth_code == "OAUTH_GRANT_MISMATCH"
    ok = oauth.parse_authorized_user(_info(scopes=[oauth.MCP_SCOPE]), purpose=oauth.GoogleOAuthPurpose.MCP, client=CLIENT)
    assert ok.scope == oauth.MCP_SCOPE


def test_service_account_info_never_parses_as_oauth_and_dispatch_is_value_free():
    sa = {"type": "service_account", "client_email": "x@y", "private_key_id": "k", "private_key": "p",
          "project_id": "p", "token_uri": oauth.TOKEN_URI}
    assert oauth.is_service_account_info(sa) and not oauth.is_authorized_user_info(sa)
    assert oauth.is_authorized_user_info(_info())
    assert oauth.credential_type_of_bytes(json.dumps(sa).encode()) == "service_account"
    assert oauth.credential_type_of_bytes(b"not json") is None
    assert oauth.credential_type_of_bytes(None) is None
    with pytest.raises(oauth.GoogleOAuthCredentialError):
        oauth.parse_authorized_user(sa, purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)


def test_malformed_bytes_fail_closed():
    with pytest.raises(oauth.GoogleOAuthCredentialError):
        oauth.parse_authorized_user_bytes(b"\xff\xfe", purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)
    with pytest.raises(oauth.GoogleOAuthCredentialError):
        oauth.parse_authorized_user_bytes(b"[1,2]", purpose=oauth.GoogleOAuthPurpose.WORKER, client=CLIENT)


@pytest.mark.parametrize("granted, expected", [
    (oauth.WORKER_SCOPE, True),
    ([oauth.WORKER_SCOPE], True),
    (oauth.WORKER_SCOPE + " openid", False),
    ([oauth.MCP_SCOPE], False),
    ("", False),
    (None, False),
])
def test_exact_scopes_helper(granted, expected):
    assert oauth.exact_scopes(granted, oauth.GoogleOAuthPurpose.WORKER) is expected
