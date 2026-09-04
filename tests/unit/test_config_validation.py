import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.config.errors import ConfigurationError
from uls.config.loader import load_config, load_config_unvalidated, load_secrets
from uls.config.validation import validate_config


def test_valid_example_shape_loads_and_validates(tmp_path, monkeypatch) -> None:
    contract = tmp_path / "study-behavior.md"
    contract.write_text("# contract\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
system:
  timezone: Asia/Seoul
  workspace_dir: ~/.uls
  state_backend: sqlite
  ephemeral_backend: memory
mcp:
  mode: local
  read_only: true
remote_mcp:
  enabled: false
  public_unauthenticated: false
behavior_contract:
  version: 1
  path: {contract}
courses:
  - course_key: 2026-1_COMP319-002
    name: 알고리즘1
    code: COMP319
    section: '002'
    semester: 2026-1
""",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert validate_config(cfg) == []

    dotenv = tmp_path / ".env"
    dotenv.write_text("NOTION_WORKER_TOKEN='secret'\nCUSTOM_VALUE=value\n", encoding="utf-8")
    monkeypatch.setenv("CUSTOM_VALUE", "from-environment")
    secrets = load_secrets(dotenv)
    assert secrets["NOTION_WORKER_TOKEN"] == "secret"
    assert secrets["CUSTOM_VALUE"] == "from-environment"
    assert secrets["GITHUB_READ_TOKEN"] == ""


def test_security_and_required_config_violations_are_reported(tmp_path) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text(
        """
mcp:
  read_only: false
remote_mcp:
  public_unauthenticated: true
behavior_contract:
  path: missing-contract.md
courses: []
""",
        encoding="utf-8",
    )
    problems = validate_config(load_config_unvalidated(config))
    assert any("mcp.read_only" in problem for problem in problems)
    assert any("remote_mcp.public_unauthenticated" in problem for problem in problems)
    assert any("behavior_contract.path" in problem for problem in problems)
    assert any("courses" in problem for problem in problems)
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_security_boolean_strings_fail_closed(tmp_path) -> None:
    contract = tmp_path / "study-behavior.md"
    contract.write_text("# contract\n", encoding="utf-8")
    config = tmp_path / "string-bool.yaml"
    config.write_text(
        f"""
mcp:
  read_only: "false"
remote_mcp:
  enabled: "false"
  public_unauthenticated: "false"
retrieval:
  allow_bounded_llm_rerank: "false"
behavior_contract:
  path: {contract}
courses:
  - course_key: 2026-1_COMP319-002
""",
        encoding="utf-8",
    )
    problems = validate_config(load_config_unvalidated(config))
    assert any("mcp.read_only" in problem and "boolean" in problem for problem in problems)
    assert any("remote_mcp.enabled" in problem for problem in problems)
    assert any("remote_mcp.public_unauthenticated" in problem for problem in problems)
    assert any("retrieval.allow_bounded_llm_rerank" in problem for problem in problems)
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_all_declared_worker_and_normalization_booleans_are_strict(tmp_path) -> None:
    contract = tmp_path / "study-behavior.md"
    contract.write_text("# contract\n", encoding="utf-8")
    config = tmp_path / "string-bool-sections.yaml"
    config.write_text(
        f"""
worker:
  enabled: "false"
normalization:
  goodnotes_visual_fallback: "false"
behavior_contract:
  path: {contract}
courses:
  - course_key: 2026-1_COMP319-002
""",
        encoding="utf-8",
    )

    problems = validate_config(load_config_unvalidated(config))
    assert any("worker.enabled" in problem and "boolean" in problem for problem in problems)
    assert any(
        "normalization.goodnotes_visual_fallback" in problem and "boolean" in problem
        for problem in problems
    )
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_enabled_remote_mcp_requires_allowed_auth_mode(tmp_path) -> None:
    contract = tmp_path / "study-behavior.md"
    contract.write_text("# contract\n", encoding="utf-8")
    config = tmp_path / "invalid-auth.yaml"
    config.write_text(
        f"""
remote_mcp:
  enabled: true
  auth_mode: shared_secret
behavior_contract:
  path: {contract}
courses:
  - course_key: 2026-1_COMP319-002
""",
        encoding="utf-8",
    )
    problems = validate_config(load_config_unvalidated(config))
    assert any("remote_mcp.auth_mode" in problem for problem in problems)
    with pytest.raises(ConfigurationError):
        load_config(config)
