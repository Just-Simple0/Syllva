"""Regression tests for the credentials: config section parser
(docs/plans/credential-resolver.md, PLAN GO).

Covers loader.py's _credentials_section() and _check_top_level_typos():
absence defaults to {} (environment for every credential), presence is
strictly validated (unknown credential name, unknown field, disallowed
source), and a near-miss top-level typo of "credentials" is rejected
instead of silently downgrading to "absent".
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from uls.config.loader import load_config_unvalidated

pytestmark = pytest.mark.unit


def _write(tmp_path, content, name="config.yaml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_absent_credentials_section_defaults_to_empty(tmp_path):
    cfg = load_config_unvalidated(_write(tmp_path, "system:\n  timezone: Asia/Seoul\n"))
    assert cfg.credentials == {}


def test_valid_credentials_section_parses(tmp_path):
    cfg = load_config_unvalidated(_write(tmp_path, (
        "credentials:\n"
        "  NOTION_MCP_TOKEN:\n"
        "    source: keyring\n"
        "  GITHUB_READ_TOKEN:\n"
        "    source: environment\n"
    )))
    assert cfg.credentials == {"NOTION_MCP_TOKEN": "keyring", "GITHUB_READ_TOKEN": "environment"}


def test_credentials_not_a_mapping_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, "credentials: not-a-mapping\n"))


@pytest.mark.parametrize("null_value", ["null", "~", ""])
def test_credentials_null_or_empty_rejected(tmp_path, null_value):
    """Regression: credentials: null must fail closed as a malformed mapping,
    never silently downgrade to absent/empty {}.
    """
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, f"credentials: {null_value}\n"))


def test_credentials_non_string_source_rejected(tmp_path):
    """Regression: credentials.<name>.source must be a string; a list/dict must
    be rejected with ValueError, not TypeError."""
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            "credentials:\n  NOTION_MCP_TOKEN:\n    source: [keyring]\n"
        )))


def test_unknown_credential_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            "credentials:\n  NOTION_MPC_TOKEN:\n    source: keyring\n"
        )))


def test_unknown_field_inside_entry_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            "credentials:\n"
            "  NOTION_MCP_TOKEN:\n"
            "    source: keyring\n"
            "    keyring_service: hijacked\n"
        )))


def test_missing_source_field_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            "credentials:\n  NOTION_MCP_TOKEN:\n    not_source: keyring\n"
        )))


def test_disallowed_source_for_credential_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            "credentials:\n  GOOGLE_WORKER_CREDENTIALS_FILE:\n    source: keyring\n"
        )))


@pytest.mark.parametrize("typo_key", ["credentails", "credential", "Credentials"])
def test_top_level_typo_of_credentials_rejected(tmp_path, typo_key):
    with pytest.raises(ValueError):
        load_config_unvalidated(_write(tmp_path, (
            f"{typo_key}:\n  NOTION_MCP_TOKEN:\n    source: keyring\n"
        )))


def test_unrelated_unknown_top_level_key_still_silently_ignored(tmp_path):
    """This plan's typo guard is narrowly scoped to near-misses of
    'credentials'; it must not change the pre-existing repository-wide
    behavior of ignoring other unrelated unknown top-level keys."""
    cfg = load_config_unvalidated(_write(tmp_path, (
        "some_unrelated_future_section:\n  foo: bar\n"
    )))
    assert cfg.credentials == {}
