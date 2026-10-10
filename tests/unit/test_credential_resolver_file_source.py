"""Regression tests for CredentialResolver's "file" source and
composition-root path_overrides parameter
(docs/plans/credential-secret-file-launcher.md, rev5).

These tests never touch the real OS secrets directory
(uls.config._secure_file.secrets_directory()); they monkeypatch
uls.config.credentials.secret_file_path to resolve inside tmp_path.
"""
from __future__ import annotations

import pytest

from uls.config import credentials as credentials_module
from uls.config._secure_file import write_secure_file
from uls.config.credentials import CredentialResolver
from uls.config.errors import ConfigurationError

pytestmark = pytest.mark.unit


def _point_file_bindings_at(monkeypatch, tmp_path):
    def fake_secret_file_path(filename: str):
        return tmp_path / filename

    monkeypatch.setattr(credentials_module, "secret_file_path", fake_secret_file_path)


def test_file_source_ready_reads_the_written_value(tmp_path, monkeypatch):
    _point_file_bindings_at(monkeypatch, tmp_path)
    filename = credentials_module.FILE_BINDINGS["NOTION_WORKER_TOKEN"]
    write_secure_file(tmp_path / filename, b"file-token-value")

    resolver = CredentialResolver({"NOTION_WORKER_TOKEN": "file"}, environ={})
    snap = resolver.resolve(required=frozenset({"NOTION_WORKER_TOKEN"}))

    assert snap["NOTION_WORKER_TOKEN"] == "file-token-value"


def test_file_source_missing_is_configuration_error_not_absent(tmp_path, monkeypatch):
    _point_file_bindings_at(monkeypatch, tmp_path)
    resolver = CredentialResolver({"NOTION_WORKER_TOKEN": "file"}, environ={})

    diagnostic = resolver.diagnose(frozenset({"NOTION_WORKER_TOKEN"}))

    assert diagnostic.results["NOTION_WORKER_TOKEN"].status == "error"
    assert diagnostic.results["NOTION_WORKER_TOKEN"].detail == "secret_file_missing"


def test_file_source_never_falls_back_to_environment_even_when_optional(tmp_path, monkeypatch):
    """A declared file source that fails must raise even for an optional
    credential -- the same silent-fallback-prohibition contract already
    enforced for keyring (test_keyring_source_never_falls_back_to_environment_even_when_optional)."""

    _point_file_bindings_at(monkeypatch, tmp_path)
    resolver = CredentialResolver({"REMOTE_MCP_SECRET": "file"}, environ={
        "REMOTE_MCP_SECRET": "should-never-be-used",
    })

    with pytest.raises(ConfigurationError):
        resolver.resolve(required=frozenset(), optional={"REMOTE_MCP_SECRET": "default"})


def test_file_source_rejects_group_or_world_readable_file(tmp_path, monkeypatch):
    _point_file_bindings_at(monkeypatch, tmp_path)
    filename = credentials_module.FILE_BINDINGS["REMOTE_MCP_SECRET"]
    path = tmp_path / filename
    write_secure_file(path, b"value")
    path.chmod(0o644)

    resolver = CredentialResolver({"REMOTE_MCP_SECRET": "file"}, environ={})
    diagnostic = resolver.diagnose(frozenset({"REMOTE_MCP_SECRET"}))

    assert diagnostic.results["REMOTE_MCP_SECRET"].status == "error"
    assert diagnostic.results["REMOTE_MCP_SECRET"].detail == "secret_file_permissions_too_open"


def test_file_source_rejects_invalid_utf8(tmp_path, monkeypatch):
    _point_file_bindings_at(monkeypatch, tmp_path)
    filename = credentials_module.FILE_BINDINGS["NOTION_WORKER_TOKEN"]
    write_secure_file(tmp_path / filename, b"\xff\xfe\x00\x01")

    resolver = CredentialResolver({"NOTION_WORKER_TOKEN": "file"}, environ={})
    diagnostic = resolver.diagnose(frozenset({"NOTION_WORKER_TOKEN"}))

    assert diagnostic.results["NOTION_WORKER_TOKEN"].status == "error"
    assert diagnostic.results["NOTION_WORKER_TOKEN"].detail == "secret_encoding_invalid"


def test_file_source_default_declared_source_is_still_environment():
    """Adding "file" to ALLOWED_SOURCES must not change DEFAULT_SOURCE:
    an undeclared NOTION_WORKER_TOKEN/REMOTE_MCP_SECRET still resolves via
    plain environment, exactly as before this plan (upgrade compatibility)."""

    resolver = CredentialResolver({}, environ={"NOTION_WORKER_TOKEN": "env-value"})
    snap = resolver.resolve(required=frozenset({"NOTION_WORKER_TOKEN"}))
    assert snap["NOTION_WORKER_TOKEN"] == "env-value"


# ---------------------------------------------------------------------------
# path_overrides (section 8.3): composition-root config-snapshot handoff for
# the non-secret Google credential path fields.
# ---------------------------------------------------------------------------


def test_path_override_takes_precedence_over_environment(tmp_path):
    env_file = tmp_path / "from-environ.json"
    cfg_file = tmp_path / "from-config.json"
    write_secure_file(env_file, b"{}")
    write_secure_file(cfg_file, b"{}")
    resolver = CredentialResolver(
        {}, environ={"GOOGLE_WORKER_CREDENTIALS_FILE": str(env_file)},
        path_overrides={"GOOGLE_WORKER_CREDENTIALS_FILE": str(cfg_file)},
    )
    snap = resolver.resolve(required=frozenset({"GOOGLE_WORKER_CREDENTIALS_FILE"}))
    assert snap["GOOGLE_WORKER_CREDENTIALS_FILE"] == str(cfg_file)
    assert snap.get_google_payload("GOOGLE_WORKER_CREDENTIALS_FILE") is not None


def test_path_override_falls_back_to_environment_when_absent(tmp_path):
    env_file = tmp_path / "from-environ.json"
    write_secure_file(env_file, b"{}")
    resolver = CredentialResolver(
        {}, environ={"GOOGLE_MCP_CREDENTIALS_FILE": str(env_file)},
        path_overrides={},
    )
    snap = resolver.resolve(required=frozenset({"GOOGLE_MCP_CREDENTIALS_FILE"}))
    assert snap["GOOGLE_MCP_CREDENTIALS_FILE"] == str(env_file)
    assert snap.get_google_payload("GOOGLE_MCP_CREDENTIALS_FILE") is not None


def test_path_override_rejects_names_outside_the_google_credential_set():
    with pytest.raises(ConfigurationError):
        CredentialResolver({}, path_overrides={"NOTION_WORKER_TOKEN": "/nope"})
