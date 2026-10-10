"""Regression tests for runtime._load_google_credentials' TOCTOU-safe
read boundary (docs/plans/credential-secret-file-launcher.md section 8.2).

Verifies the credential file is read once through the secure boundary and
handed to google.auth as an already-parsed dict (never reopened by name
through google.auth's own file loader), and that a world-readable or
symlinked credential file is rejected before any Google SDK call.
"""
from __future__ import annotations

import os
from types import MappingProxyType

import pytest

from uls.config._secure_file import write_secure_file
from uls.config.credentials import CredentialResolver, GoogleCredentialPayload
from uls.config.errors import ConfigurationError
from uls.runtime import _load_google_credentials

pytestmark = pytest.mark.unit


class _FakeCredentials:
    def __init__(self, scopes):
        self.scopes = scopes


def _install_fake_load_credentials_from_dict(monkeypatch, *, calls):
    import google.auth

    def fake_load_credentials_from_dict(info, scopes=None, **kwargs):
        calls.append({"info": info, "scopes": scopes})
        return _FakeCredentials(scopes), None

    monkeypatch.setattr(google.auth, "load_credentials_from_dict", fake_load_credentials_from_dict)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("google.auth.load_credentials_from_file must never be called; "
                             "it reopens the path by name outside the secure boundary")

    monkeypatch.setattr(google.auth, "load_credentials_from_file", fail_if_called)


def test_load_google_credentials_accepts_payload_and_uses_dict_loader(monkeypatch):
    calls = []
    _install_fake_load_credentials_from_dict(monkeypatch, calls=calls)

    payload = {"type": "service_account", "project_id": "example", "client_id": "abc"}
    cred_payload = GoogleCredentialPayload(info=MappingProxyType(payload), source_name="GOOGLE_MCP_CREDENTIALS_FILE")
    credentials = _load_google_credentials(cred_payload, read_only=True)

    assert len(calls) == 1
    assert calls[0]["info"] == payload
    assert calls[0]["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert credentials.scopes == ["https://www.googleapis.com/auth/drive.readonly"]


def test_load_google_credentials_rejects_bare_string_or_non_payload():
    with pytest.raises(ConfigurationError) as exc_info:
        _load_google_credentials("/path/to/creds.json", read_only=True)  # type: ignore[arg-type]
    assert "GoogleCredentialPayload" in str(exc_info.value)


def test_resolver_diagnose_rejects_world_readable_google_credentials(tmp_path):
    path = tmp_path / "creds.json"
    write_secure_file(path, b'{"type": "service_account"}')
    os.chmod(path, 0o644)

    resolver = CredentialResolver({}, environ={"GOOGLE_MCP_CREDENTIALS_FILE": str(path)})
    diag = resolver.diagnose(frozenset({"GOOGLE_MCP_CREDENTIALS_FILE"}))
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].status == "error"
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].detail == "secret_file_permissions_too_open"
    assert diag.get_google_payload("GOOGLE_MCP_CREDENTIALS_FILE") is None


def test_resolver_diagnose_rejects_symlinked_google_credentials(tmp_path):
    real = tmp_path / "real-creds.json"
    write_secure_file(real, b'{"type": "service_account"}')
    link = tmp_path / "link-creds.json"
    os.symlink(real, link)

    resolver = CredentialResolver({}, environ={"GOOGLE_MCP_CREDENTIALS_FILE": str(link)})
    diag = resolver.diagnose(frozenset({"GOOGLE_MCP_CREDENTIALS_FILE"}))
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].status == "error"
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].detail == "secret_file_is_symlink"
    assert diag.get_google_payload("GOOGLE_MCP_CREDENTIALS_FILE") is None


def test_resolver_diagnose_rejects_invalid_json_google_credentials(tmp_path):
    path = tmp_path / "creds.json"
    write_secure_file(path, b"not-json-at-all")

    resolver = CredentialResolver({}, environ={"GOOGLE_MCP_CREDENTIALS_FILE": str(path)})
    diag = resolver.diagnose(frozenset({"GOOGLE_MCP_CREDENTIALS_FILE"}))
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].status == "error"
    assert diag.results["GOOGLE_MCP_CREDENTIALS_FILE"].detail == "Google credential file is not valid JSON"
    assert diag.get_google_payload("GOOGLE_MCP_CREDENTIALS_FILE") is None
