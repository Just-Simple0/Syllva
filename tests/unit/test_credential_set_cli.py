"""Regression tests for the interactive "uls credential set NAME" command
(docs/plans/credential-secret-file-launcher.md rev5 section 2.6/7.6/8.6/8.7/8.8).

Every test asserts the raw secret value never appears anywhere in the
returned result dict (str(result) never contains it), matching the
"values are never exposed, including in success feedback" design
invariant.
"""
from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest
import yaml

from uls.behavior import asset_root
from uls.cli import credential_set
from uls.cli.main import _config, initialize
from uls.config._secure_file import read_secure_file

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fake_external_credential_state(monkeypatch):
    from uls.settings.credential_stores import FakeKeyring

    backend = FakeKeyring()
    monkeypatch.setattr(credential_set, "explicit_os_keyring", lambda platform=None: backend)
    monkeypatch.setattr(credential_set, "_runtime_environment", dict)
    return backend


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
    (tmp_path / "secrets").mkdir(mode=0o700, exist_ok=True)
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


def test_notion_cli_holds_pair_locks_across_prompt_without_writing_reservation(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)
    from uls.settings.config_service import ConfigStore
    from uls.settings.credential_admission import credential_pair_admission
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import JournalStore, OperationInProgress

    store = ConfigStore(config_path)
    root = credential_set.secret_file_path("settings_state.key").parent
    mcp, worker = ROLES["notion-mcp"], ROLES["notion-worker"]
    journal = JournalStore(store.load().config.system.workspace_dir, credential_root=root)
    locators = [mcp.locator(root), worker.locator(root)]
    prompt_attempts = []

    def input_during_lock(prompt=""):
        with pytest.raises(OperationInProgress), credential_pair_admission(
            root, "notion", "default", locators, journal=journal, config_path=config_path,
        ):
            pytest.fail("CLI did not retain pair admission while waiting for input")
        prompt_attempts.append(prompt)
        return "synthetic-cli-token"

    monkeypatch.setattr(credential_set.getpass, "getpass", input_during_lock)
    result = credential_set.run(config=config, config_path=config_path,
                                name="NOTION_WORKER_TOKEN", overwrite=False)
    assert result["status"] == "ready"
    assert len(prompt_attempts) == 2
    assert not list((root / "admission").glob("*.reservation"))
    assert not list(journal.directory.glob("*.json"))


def test_notion_cli_rejects_canonical_managed_peer_duplicate(fake_external_credential_state, tmp_path, monkeypatch):
    backend = fake_external_credential_state
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    duplicate = "synthetic-shared-notion-token"
    backend.set_password("Syllva MCP", "notion_mcp_token", duplicate)
    _fake_getpass(monkeypatch, [duplicate, duplicate])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_purpose_conflict"
    assert duplicate not in str(exc_info.value)
    assert not secret_path.exists()


@pytest.mark.parametrize("declared_peer", [False, True], ids=["undeclared-peer", "declared-keyring-peer"])
@pytest.mark.parametrize("bad_kind", ["empty", "wrong_type", "encoding_error"],
                         ids=["empty-string", "invalid-type", "invalid-encoding"])
def test_notion_cli_rejects_invalid_canonical_keyring_peer(
    fake_external_credential_state, tmp_path, monkeypatch, declared_peer, bad_kind,
):
    backend = fake_external_credential_state
    credentials = "NOTION_WORKER_TOKEN:\n  source: file\n"
    if declared_peer:
        credentials += "NOTION_MCP_TOKEN:\n  source: keyring\n"
    config_path, config = _write_config(tmp_path, credentials_yaml=credentials)
    target_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    if bad_kind == "empty":
        backend.set_password("Syllva MCP", "notion_mcp_token", "")
    elif bad_kind == "wrong_type":
        monkeypatch.setattr(backend, "get_password", lambda service, account: 17)
    else:
        monkeypatch.setattr(backend, "get_password", lambda service, account: "\ud800")
    _fake_getpass(monkeypatch, ["synthetic-target-token", "synthetic-target-token"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)

    assert exc_info.value.args[0] == "credential_peer_unavailable"
    assert not target_path.exists()
    assert "synthetic-target-token" not in str(exc_info.value)


@pytest.mark.parametrize("declared_peer", [False, True], ids=["undeclared-peer", "declared-file-peer"])
def test_notion_cli_rejects_empty_canonical_file_peer_without_target_write(
    fake_external_credential_state, tmp_path, monkeypatch, declared_peer,
):
    backend = fake_external_credential_state
    credentials = "NOTION_MCP_TOKEN:\n  source: keyring\n"
    if declared_peer:
        credentials += "NOTION_WORKER_TOKEN:\n  source: file\n"
    config_path, config = _write_config(tmp_path, credentials_yaml=credentials)
    peer_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    peer_path.write_bytes(b"")
    peer_path.chmod(0o600)
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-target-token", "synthetic-target-token"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_MCP_TOKEN", overwrite=False)

    assert exc_info.value.args[0] == "credential_peer_unavailable"
    assert peer_path.read_bytes() == b""
    assert backend.get_password("Syllva MCP", "notion_mcp_token") is None
    assert "synthetic-target-token" not in str(exc_info.value)


def test_notion_cli_compares_current_workspace_environment_peer(tmp_path, monkeypatch):
    config_path, config = _write_config(
        tmp_path,
        credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\nNOTION_MCP_TOKEN:\n  source: environment\n",
    )
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    duplicate = "synthetic-environment-peer-token"
    monkeypatch.setattr(credential_set, "_runtime_environment", lambda: {"NOTION_MCP_TOKEN": duplicate})
    _fake_getpass(monkeypatch, [duplicate, duplicate])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_purpose_conflict"
    assert not secret_path.exists()


def test_notion_cli_fails_closed_for_configured_missing_environment_peer(tmp_path, monkeypatch):
    config_path, config = _write_config(
        tmp_path,
        credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\nNOTION_MCP_TOKEN:\n  source: environment\n",
    )
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-new-token", "synthetic-new-token"])
    monkeypatch.setattr(credential_set, "_runtime_environment", dict)

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_peer_unavailable"
    assert not secret_path.exists()


def test_notion_cli_aborts_if_config_changes_during_prompt(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    import yaml

    from uls.config.mutation import atomic_replace_config

    calls = 0

    def change_config_during_prompt(prompt=""):
        nonlocal calls
        calls += 1
        if calls == 1:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            raw["x_cli_prompt_interleave"] = "changed"
            atomic_replace_config(config_path, yaml.safe_dump(raw, sort_keys=False).encode())
        return "synthetic-cli-token"

    monkeypatch.setattr(credential_set.getpass, "getpass", change_config_during_prompt)
    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_config_changed"
    assert not secret_path.exists()


def test_notion_cli_requires_exact_file_readback(tmp_path, monkeypatch):
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_WORKER_TOKEN:\n  source: file\n")
    secret_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-readback-token", "synthetic-readback-token"])
    read_secure = credential_set.read_secure_file
    target_reads = 0

    def mismatched_readback(path, **kwargs):
        nonlocal target_reads
        if Path(path) == secret_path:
            target_reads += 1
            if target_reads > 1:
                return b"different-synthetic-value"
        return read_secure(path, **kwargs)

    monkeypatch.setattr(credential_set, "read_secure_file", mismatched_readback)
    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_WORKER_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_store_failed"
    assert "synthetic-readback-token" not in str(exc_info.value)


def test_notion_cli_requires_exact_keyring_readback(fake_external_credential_state, tmp_path, monkeypatch):
    backend = fake_external_credential_state
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_MCP_TOKEN:\n  source: keyring\n")
    _point_file_bindings_at(monkeypatch, tmp_path)
    _force_tty(monkeypatch)

    def mismatched_set(service, account, value):
        backend.values[(service, account)] = value + "-changed"

    monkeypatch.setattr(backend, "set_password", mismatched_set)
    _fake_getpass(monkeypatch, ["synthetic-keyring-token", "synthetic-keyring-token"])
    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path,
                           name="NOTION_MCP_TOKEN", overwrite=False)
    assert exc_info.value.args[0] == "credential_store_failed"
    assert "synthetic-keyring-token" not in str(exc_info.value)


def _inject_secure_file_oserror(monkeypatch, target: Path, errnum: int, *, max_failures: int | None = None):
    import uls.config._secure_file as secure_file

    expected_parent = target.parent.stat()
    real_open = os.open
    intercepted = []

    def guarded_open(path, flags, *args, **kwargs):
        dir_fd = kwargs.get("dir_fd")
        if path == target.name and dir_fd is not None:
            actual_parent = os.fstat(dir_fd)
            if (actual_parent.st_dev, actual_parent.st_ino) == (expected_parent.st_dev, expected_parent.st_ino):
                intercepted.append(path)
                if max_failures is None or len(intercepted) <= max_failures:
                    raise OSError(errnum, "synthetic secure-file open failure", str(target))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(secure_file.os, "open", guarded_open)
    return intercepted


@pytest.mark.parametrize("missing_kind", ["file", "directory"], ids=["enoent-file", "enoent-directory"])
def test_cli_secure_file_absence_requires_real_enoent_cause(tmp_path, missing_kind):
    import errno

    from uls.config.errors import ConfigurationError
    from uls.settings.credential_roles import ROLES

    root = tmp_path / "secrets"
    if missing_kind == "file":
        root.mkdir(mode=0o700)
    path = Path(ROLES["notion-worker"].locator(root))
    expected_problem = "secret_file_missing" if missing_kind == "file" else "secret_dir_missing"

    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(path)
    assert exc_info.value.details["problems"] == [expected_problem]
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
    assert exc_info.value.__cause__.errno == errno.ENOENT
    assert credential_set._read_managed_notion(ROLES["notion-worker"], root, None) is None
    assert credential_set._existing_file_state(path).state == "absent"


@pytest.mark.skipif(os.name == "nt", reason="POSIX secure-file EACCES cause regression")
def test_notion_cli_unreadable_undeclared_file_peer_fails_before_keyring_write(
    fake_external_credential_state, tmp_path, monkeypatch,
):
    import errno

    from uls.config.errors import ConfigurationError

    backend = fake_external_credential_state
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_MCP_TOKEN:\n  source: keyring\n")
    peer_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    peer_path.write_bytes(b"synthetic-existing-canonical-peer")
    peer_path.chmod(0o600)
    backend.set_password("Syllva MCP", "notion_mcp_token", "synthetic-existing-target-token")
    intercepted = _inject_secure_file_oserror(monkeypatch, peer_path, errno.EACCES)

    with pytest.raises(ConfigurationError) as boundary_error:
        read_secure_file(peer_path)
    assert boundary_error.value.details["problems"] == ["secret_file_missing"]
    assert isinstance(boundary_error.value.__cause__, PermissionError)
    assert boundary_error.value.__cause__.errno == errno.EACCES
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-new-target-token", "synthetic-new-target-token"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_MCP_TOKEN", overwrite=True)

    assert exc_info.value.args[0] == "credential_peer_unavailable"
    assert backend.get_password("Syllva MCP", "notion_mcp_token") == "synthetic-existing-target-token"
    assert peer_path.read_bytes() == b"synthetic-existing-canonical-peer"
    assert len(intercepted) >= 2


@pytest.mark.parametrize("declared_peer", [False, True], ids=["undeclared-peer", "declared-file-peer"])
def test_notion_cli_rejects_invalid_utf8_canonical_file_peer_without_target_write(
    fake_external_credential_state, tmp_path, monkeypatch, declared_peer,
):
    backend = fake_external_credential_state
    credentials = "NOTION_MCP_TOKEN:\n  source: keyring\n"
    if declared_peer:
        credentials += "NOTION_WORKER_TOKEN:\n  source: file\n"
    config_path, config = _write_config(tmp_path, credentials_yaml=credentials)
    peer_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    peer_path.write_bytes(b"\xff")
    peer_path.chmod(0o600)
    backend.set_password("Syllva MCP", "notion_mcp_token", "synthetic-existing-target-token")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-new-target-token", "synthetic-new-target-token"])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_MCP_TOKEN", overwrite=True)

    assert exc_info.value.args[0] == "credential_peer_unavailable"
    assert peer_path.read_bytes() == b"\xff"
    assert backend.get_password("Syllva MCP", "notion_mcp_token") == "synthetic-existing-target-token"


def test_notion_cli_compares_valid_utf8_file_peer_bytes_without_reencoding(
    fake_external_credential_state, tmp_path, monkeypatch,
):
    backend = fake_external_credential_state
    config_path, config = _write_config(tmp_path, credentials_yaml="NOTION_MCP_TOKEN:\n  source: keyring\n")
    peer_path = _point_file_bindings_at(monkeypatch, tmp_path)("notion_worker_token.secret")
    value = "synthetic-shared-π-token"
    original_peer_bytes = value.encode("utf-8")
    peer_path.write_bytes(original_peer_bytes)
    peer_path.chmod(0o600)
    backend.set_password("Syllva MCP", "notion_mcp_token", "synthetic-existing-target-token")
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, [value, value])

    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="NOTION_MCP_TOKEN", overwrite=True)

    assert exc_info.value.args[0] == "credential_purpose_conflict"
    assert peer_path.read_bytes() == original_peer_bytes
    assert backend.get_password("Syllva MCP", "notion_mcp_token") == "synthetic-existing-target-token"


@pytest.mark.skipif(os.name == "nt", reason="POSIX secure-file EACCES cause regression")
def test_cli_unreadable_existing_file_requires_confirmation_and_refusal_preserves_target(tmp_path, monkeypatch):
    import errno

    from uls.config.errors import ConfigurationError

    config_path, config = _write_config(tmp_path, credentials_yaml="REMOTE_MCP_SECRET:\n  source: file\n")
    target_path = _point_file_bindings_at(monkeypatch, tmp_path)("remote_mcp_secret.secret")
    original = b"synthetic-existing-file-content"
    target_path.write_bytes(original)
    target_path.chmod(0o600)
    intercepted = _inject_secure_file_oserror(monkeypatch, target_path, errno.EACCES, max_failures=2)

    with pytest.raises(ConfigurationError) as boundary_error:
        read_secure_file(target_path)
    assert boundary_error.value.details["problems"] == ["secret_file_missing"]
    assert isinstance(boundary_error.value.__cause__, PermissionError)
    assert boundary_error.value.__cause__.errno == errno.EACCES
    _force_tty(monkeypatch)
    _fake_getpass(monkeypatch, ["synthetic-replacement-value", "synthetic-replacement-value"])
    prompts = []

    def decline(prompt=""):
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr(builtins, "input", decline)
    with pytest.raises(credential_set.CredentialSetError) as exc_info:
        credential_set.run(config=config, config_path=config_path, name="REMOTE_MCP_SECRET", overwrite=False)

    assert exc_info.value.args[0] == "credential_aborted"
    assert len(prompts) == 1 and "기존 저장소가 검증에 실패한 상태입니다" in prompts[0]
    assert target_path.read_bytes() == original
    assert len(intercepted) >= 2
