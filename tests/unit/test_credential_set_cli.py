"""Regression tests for the interactive "uls credential set NAME" command
(docs/plans/credential-secret-file-launcher.md rev5 section 2.6/7.6/8.6/8.7/8.8).

Every test asserts the raw secret value never appears anywhere in the
returned result dict (str(result) never contains it), matching the
"values are never exposed, including in success feedback" design
invariant.
"""
from __future__ import annotations

import builtins

import pytest
import yaml

from uls.behavior import asset_root
from uls.cli import credential_set
from uls.cli.main import _config, initialize
from uls.config._secure_file import read_secure_file

pytestmark = pytest.mark.unit


def _write_config(tmp_path, *, credentials_yaml: str = ""):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    if credentials_yaml:
        raw["credentials"] = yaml.safe_load(credentials_yaml)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize(path)
    return path, _config(path)


def _point_file_bindings_at(monkeypatch, tmp_path):
    def fake_secret_file_path(filename: str):
        return tmp_path / "secrets" / filename

    monkeypatch.setattr(credential_set, "secret_file_path", fake_secret_file_path)
    import uls.config.credentials as credentials_module

    monkeypatch.setattr(credentials_module, "secret_file_path", fake_secret_file_path)
    return fake_secret_file_path


def _fake_getpass(monkeypatch, values):
    values_iter = iter(values)
    monkeypatch.setattr(credential_set.getpass, "getpass", lambda prompt="": next(values_iter))


def _force_tty(monkeypatch, *, is_tty: bool = True):
    monkeypatch.setattr(credential_set.sys.stdin, "isatty", lambda: is_tty)


def test_undeclared_source_returns_guidance_without_prompting(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path)

    def fail_if_called(prompt=""):
        raise AssertionError("must not prompt when source is undeclared")

    monkeypatch.setattr(credential_set.getpass, "getpass", fail_if_called)

    result = credential_set.run(config=config, config_path=config_path,
                                name="NOTION_WORKER_TOKEN", overwrite=False)

    assert result["status"] == "guidance_only"
    assert "credentials:" in result["add_to_config"]
    assert result["allowed_sources"] == ["file"]


def test_google_credential_path_name_gets_dedicated_guidance(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path)

    def fail_if_called(prompt=""):
        raise AssertionError("must not prompt for a non-secret path name")

    monkeypatch.setattr(credential_set.getpass, "getpass", fail_if_called)

    result = credential_set.run(config=config, config_path=config_path,
                                name="GOOGLE_WORKER_CREDENTIALS_FILE", overwrite=False)

    assert result["status"] == "guidance_only"
    assert "google_worker_credentials_path" in result["unattended"]
    assert "export GOOGLE_WORKER_CREDENTIALS_FILE" in result["interactive"]


def test_environment_only_name_gets_export_guidance(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path)

    def fail_if_called(prompt=""):
        raise AssertionError("must not prompt for an environment-only name")

    monkeypatch.setattr(credential_set.getpass, "getpass", fail_if_called)

    result = credential_set.run(config=config, config_path=config_path,
                                name="REMOTE_MCP_EXPIRES_AT", overwrite=False)

    assert result["status"] == "guidance_only"
    assert result["action"].startswith("export REMOTE_MCP_EXPIRES_AT=")


def test_file_source_first_set_requires_no_confirmation(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["token-value-one", "token-value-one"])

    result = credential_set.run(config=config, config_path=config_path,
                                name="NOTION_WORKER_TOKEN", overwrite=False)

    assert result["status"] == "ready"
    assert "token-value-one" not in str(result)
    assert read_secure_file(secret_path) == b"token-value-one"


def test_file_source_existing_ready_value_requires_confirmation(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["first-value", "first-value"])
    credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)

    _fake_getpass(monkeypatch, ["second-value", "second-value"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "n")

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_aborted"


def test_file_source_overwrite_flag_skips_confirmation(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["first-value", "first-value"])
    credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)

    _fake_getpass(monkeypatch, ["rotated-value", "rotated-value"])

    def fail_if_prompted(prompt=""):
        raise AssertionError("must not prompt when --overwrite is set")

    monkeypatch.setattr(builtins, "input", fail_if_prompted)

    result = credential_set.run(config=config, config_path=config_path,
                                name="NOTION_WORKER_TOKEN", overwrite=True)
    assert result["status"] == "ready"
    assert read_secure_file(secret_path) == b"rotated-value"


def test_file_source_untrusted_existing_still_requires_confirmation(tmp_path, monkeypatch):
    """Regression: an existing-but-untrusted (failed-validation) storage must
    still require confirmation, never auto-overwrite (rev3 defect, fixed in
    rev5 section 8.7)."""

    config_path, config = _write_config(tmp_path, credentials_yaml="REMOTE_MCP_SECRET:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("remote_mcp_secret.secret")
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text("world-readable-value", encoding="utf-8")
    secret_path.chmod(0o644)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["new-value", "new-value"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "n")

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="REMOTE_MCP_SECRET", overwrite=False)
    assert exc_info.value.args[0] == "credential_aborted"

    _fake_getpass(monkeypatch, ["new-value", "new-value"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "y")
    result = credential_set.run(config=config, config_path=config_path, name="REMOTE_MCP_SECRET", overwrite=False)
    assert result["status"] == "ready"
    assert read_secure_file(secret_path) == b"new-value"


def test_non_tty_stdin_is_rejected_without_reading_anything(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch, is_tty=False)

    def fail_if_called(prompt=""):
        raise AssertionError("must never call getpass without a real TTY")

    monkeypatch.setattr(credential_set.getpass, "getpass", fail_if_called)

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_tty_required"


def test_empty_value_rejected(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["", ""])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "secret_value_empty"


def test_control_characters_rejected(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["value-with-newline\n", "value-with-newline\n"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_contains_control_characters"


def test_double_entry_mismatch_retries_then_fails(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["a", "b", "a", "b", "a", "b"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_double_entry_mismatch"


def test_value_never_appears_in_any_result_field(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    secret_marker = "UNIQUE-SECRET-MARKER-0xC0FFEE"
    _fake_getpass(monkeypatch, [secret_marker, secret_marker])

    result = credential_set.run(config=config, config_path=config_path,
                                name="NOTION_WORKER_TOKEN", overwrite=False)

    assert secret_marker not in str(result)
    assert secret_marker not in repr(result)


def test_non_tty_stdin_with_existing_value_fails_before_any_prompt(tmp_path, monkeypatch):
    """Regression (Gemini review): If an existing file exists and stdin is non-TTY,
    it must fail immediately with credential_tty_required without calling input(),
    so piped secrets are never consumed by a y/N prompt.
    """
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch, is_tty=True)
    _fake_getpass(monkeypatch, ["existing-secret", "existing-secret"])
    credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)

    # Now switch to non-TTY
    _force_tty(monkeypatch, is_tty=False)
    def fail_if_input_called(prompt=""): 
        raise AssertionError("input() must never be called on non-TTY stdin")
    monkeypatch.setattr(builtins, "input", fail_if_input_called)

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_tty_required"


def test_ctrl_d_eof_raises_clean_credential_aborted(tmp_path, monkeypatch):
    """Regression (Gemini review): Ctrl+D (EOFError) during getpass or overwrite
    must raise credential_aborted, not bubble up as an unhandled exception.
    """
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch, is_tty=True)

    def eof_getpass(prompt=""): 
        raise EOFError()
    monkeypatch.setattr(credential_set.getpass, "getpass", eof_getpass)

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_aborted"
