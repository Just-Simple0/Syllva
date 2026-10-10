"""Regression tests for MCP dispatch-level worker/MCP credential distinctness
(docs/plans/credential-resolver.md).

Guarantees that when dispatch(command='mcp') resolves credentials for
build_retrieval, it includes NOTION_WORKER_TOKEN and GOOGLE_WORKER_CREDENTIALS_FILE
in the snapshot as optional values so require_mcp_credentials() actually enforces
separation between MCP and worker credentials in production dispatch.
"""
from __future__ import annotations

import pathlib
import sys
from argparse import Namespace

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import uls.cli.main as cli_main
from uls.behavior import asset_root
from uls.config.errors import ConfigurationError

pytestmark = pytest.mark.contract


def _write_config(tmp_path):
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text(encoding="utf-8"))
    raw["system"]["workspace_dir"] = "state"
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts/study-behavior.md")
    raw["worker"]["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cli_main.initialize(path)
    return path


def test_mcp_dispatch_rejects_identical_mcp_and_worker_notion_token(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    google_mcp_file = tmp_path / "google-mcp-credentials.json"
    google_mcp_file.write_text("{}", encoding="utf-8")
    google_mcp_file.chmod(0o600)
    monkeypatch.delenv("GOOGLE_WORKER_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("GOOGLE_MCP_CREDENTIALS_FILE", str(google_mcp_file))
    monkeypatch.setenv("NOTION_MCP_TOKEN", "shared-token-value")
    monkeypatch.setenv("NOTION_WORKER_TOKEN", "shared-token-value")

    # In dispatch(mcp local), distinctness check must trigger and raise ConfigurationError
    with pytest.raises(ConfigurationError) as exc_info:
        cli_main.dispatch(Namespace(command="mcp", mode="local", config=path))
    assert "MCP and worker Notion tokens must be distinct" in str(exc_info.value)


def test_mcp_dispatch_rejects_identical_mcp_and_worker_drive_file(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    shared_file = tmp_path / "shared-credentials.json"
    shared_file.write_text("{}", encoding="utf-8")
    shared_file.chmod(0o600)
    monkeypatch.setenv("GOOGLE_MCP_CREDENTIALS_FILE", str(shared_file))
    monkeypatch.setenv("GOOGLE_WORKER_CREDENTIALS_FILE", str(shared_file))
    monkeypatch.setenv("NOTION_MCP_TOKEN", "mcp-token-value")
    monkeypatch.setenv("NOTION_WORKER_TOKEN", "different-worker-token")

    with pytest.raises(ConfigurationError) as exc_info:
        cli_main.dispatch(Namespace(command="mcp", mode="local", config=path))
    assert "MCP and worker Drive credential files must be distinct" in str(exc_info.value)
