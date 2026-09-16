"""Regression tests for doctor()'s composition-root credential resolution
(docs/plans/credential-resolver.md, PLAN GO rev3, Blocker B).

doctor() must:
- diagnose() every credential name exactly once per call;
- report a keyring-declared optional credential's failure as False in
  optional_checks without raising, distinguishing it from a merely
  unconfigured environment-sourced optional credential only by evidence,
  not by crashing;
- reuse that single diagnostic for the separation check and for --live
  provider construction, never diagnosing/resolving a second time.
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.behavior import asset_root
from uls.cli.main import _config, doctor, initialize

pytestmark = pytest.mark.contract


def _write_config(tmp_path, *, worker_enabled: bool = False, credentials_yaml: str = ""):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    raw["worker"]["enabled"] = worker_enabled
    if credentials_yaml:
        raw["credentials"] = yaml.safe_load(credentials_yaml)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize(path)
    return _config(path)


def _clear_all_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GOOGLE_WORKER_CREDENTIALS_FILE",
        "NOTION_WORKER_TOKEN",
        "GOOGLE_MCP_CREDENTIALS_FILE",
        "NOTION_MCP_TOKEN",
        "GITHUB_READ_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_mcp_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    google_mcp_file = tmp_path / "google-mcp-credentials.json"
    google_mcp_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_MCP_CREDENTIALS_FILE", str(google_mcp_file))
    monkeypatch.setenv("NOTION_MCP_TOKEN", "mcp-token-value")


def _install_empty_fake_macos_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyKeyring:
        __module__ = "keyring.backends.macOS"

        def __init__(self):
            self.keychain = None

        def get_password(self, service, account):
            return None

    module = types.ModuleType("keyring.backends.macOS")
    module.Keyring = EmptyKeyring
    monkeypatch.setitem(sys.modules, "keyring.backends.macOS", module)
    monkeypatch.setattr(sys, "platform", "darwin")


def test_doctor_keyring_declared_optional_credential_missing_is_false_not_raise(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a keyring-declared GITHUB_READ_TOKEN whose entry is
    missing must report optional_checks['GITHUB_READ_TOKEN'] is False and
    must not raise or otherwise abort doctor()."""

    _clear_all_credentials(monkeypatch)
    _install_empty_fake_macos_keyring(monkeypatch)
    config = _write_config(
        tmp_path,
        worker_enabled=False,
        credentials_yaml="GITHUB_READ_TOKEN:\n  source: keyring\n",
    )
    _set_mcp_credentials(monkeypatch, tmp_path)

    result = doctor(config)

    assert result["status"] == "ok"
    assert result["optional_checks"]["GITHUB_READ_TOKEN"] is False


def test_doctor_diagnoses_credentials_exactly_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composition-root ownership: doctor() must call
    CredentialResolver.diagnose() exactly once per invocation, reusing that
    one DiagnosticResolution for every check that follows (separation
    check, --live provider construction), never diagnosing/resolving a
    second time."""

    _clear_all_credentials(monkeypatch)
    config = _write_config(tmp_path, worker_enabled=False)
    _set_mcp_credentials(monkeypatch, tmp_path)

    import uls.cli.main as cli_main

    call_count = []
    original_diagnose = cli_main.CredentialResolver.diagnose

    def counting_diagnose(self, names):
        call_count.append(names)
        return original_diagnose(self, names)

    monkeypatch.setattr(cli_main.CredentialResolver, "diagnose", counting_diagnose)

    result = doctor(config)

    assert result["status"] == "ok"
    assert len(call_count) == 1, "doctor() must call diagnose() exactly once"

