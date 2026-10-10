"""C8 regression: next-version default poll interval.

rev10 (docs/ux/intake-execution-contract.md section 3.1/section 9 C8) requires the next-version
poll_interval_minutes default to be 1 (60 seconds), still user-configurable in range, with no code
or config claiming a guaranteed maximum delay.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import pytest

from uls.config.loader import load_config
from uls.config.schema import WorkerCfg
from uls.config.validation import validate_config

pytestmark = pytest.mark.unit


def test_worker_cfg_default_poll_interval_is_one_minute() -> None:
    assert WorkerCfg().poll_interval_minutes == 1


def test_config_without_poll_interval_loads_the_new_default(tmp_path, monkeypatch) -> None:
    """A config that omits worker.poll_interval_minutes entirely must pick up
    the new 1-minute default, and validate_config must still accept it."""

    monkeypatch.delenv("NOTION_WORKER_TOKEN", raising=False)
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
    assert cfg.worker.poll_interval_minutes == 1
    assert validate_config(cfg) == []

def test_explicit_poll_interval_override_is_preserved(tmp_path, monkeypatch) -> None:
    """An explicit non-default value (still within the positive range
    validation requires) must be preserved exactly, confirming the new
    default does not clamp or override a user's own configured value."""

    monkeypatch.delenv("NOTION_WORKER_TOKEN", raising=False)
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
worker:
  poll_interval_minutes: 5
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
    assert cfg.worker.poll_interval_minutes == 5
    assert validate_config(cfg) == []

