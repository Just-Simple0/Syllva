"""Regression tests for purpose-scoped credential diagnostics in doctor().

Before this fix, doctor() checked all five provider credentials
unconditionally, regardless of which features a deployment actually uses.
A minimal read-only deployment with the intake worker disabled and no
GitHub integration configured would be reported as needs_configuration
purely because of unused-feature credentials, contradicting
docs/operator-guide/installation.md's promise that doctor reports only
the credentials a selected feature actually needs.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.behavior import asset_root
from uls.cli.main import _config, doctor, initialize

pytestmark = pytest.mark.contract


def _write_config(tmp_path, *, worker_enabled: bool):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    raw["worker"]["enabled"] = worker_enabled
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize(path)
    return _config(path)


def _set_mcp_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    google_mcp_file = tmp_path / "google-mcp-credentials.json"
    google_mcp_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_MCP_CREDENTIALS_FILE", str(google_mcp_file))
    monkeypatch.setenv("NOTION_MCP_TOKEN", "mcp-token-value")


def _clear_all_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GOOGLE_WORKER_CREDENTIALS_FILE",
        "NOTION_WORKER_TOKEN",
        "GOOGLE_MCP_CREDENTIALS_FILE",
        "NOTION_MCP_TOKEN",
        "GITHUB_READ_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_worker_disabled_minimal_setup_is_ok_without_worker_or_github_credentials(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: this exact setup used to report needs_configuration purely
    because GOOGLE_WORKER_CREDENTIALS_FILE, NOTION_WORKER_TOKEN and
    GITHUB_READ_TOKEN were unconditionally required even though the worker
    is disabled and GitHub is an optional supplemental source.
    """

    _clear_all_credentials(monkeypatch)
    config = _write_config(tmp_path, worker_enabled=False)
    _set_mcp_credentials(monkeypatch, tmp_path)

    result = doctor(config)

    assert result["status"] == "ok"
    assert "GOOGLE_WORKER_CREDENTIALS_FILE" not in result["checks"]
    assert "NOTION_WORKER_TOKEN" not in result["checks"]
    assert "GITHUB_READ_TOKEN" not in result["checks"]
    assert result["optional_checks"]["GOOGLE_WORKER_CREDENTIALS_FILE"] is False
    assert result["optional_checks"]["NOTION_WORKER_TOKEN"] is False
    assert result["optional_checks"]["GITHUB_READ_TOKEN"] is False


def test_worker_enabled_missing_worker_credentials_is_needs_configuration(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_all_credentials(monkeypatch)
    config = _write_config(tmp_path, worker_enabled=True)
    _set_mcp_credentials(monkeypatch, tmp_path)

    result = doctor(config)

    assert result["status"] == "needs_configuration"
    assert result["checks"]["GOOGLE_WORKER_CREDENTIALS_FILE"] is False
    assert result["checks"]["NOTION_WORKER_TOKEN"] is False
    # GitHub stays optional even when the worker is enabled.
    assert "GITHUB_READ_TOKEN" not in result["checks"]


def test_missing_mcp_credentials_always_fails_regardless_of_worker_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-only MCP search surface is the core deliverable, so its
    credentials must remain required even when the worker is disabled."""

    _clear_all_credentials(monkeypatch)
    config = _write_config(tmp_path, worker_enabled=False)

    result = doctor(config)

    assert result["status"] == "needs_configuration"
    assert result["checks"]["GOOGLE_MCP_CREDENTIALS_FILE"] is False
    assert result["checks"]["NOTION_MCP_TOKEN"] is False
    assert result["checks"]["credential_separation"] is False


def test_remote_mcp_disabled_is_optional_not_a_status_blocker(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_all_credentials(monkeypatch)
    config = _write_config(tmp_path, worker_enabled=False)
    _set_mcp_credentials(monkeypatch, tmp_path)

    result = doctor(config)

    assert "remote_profile" not in result["checks"]
    assert result["optional_checks"]["remote_profile"] == "not_configured"
