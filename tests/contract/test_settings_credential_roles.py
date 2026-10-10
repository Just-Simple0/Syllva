from __future__ import annotations

import pytest

from uls.config.credentials import (
    ALLOWED_SOURCES,
    FILE_BINDINGS,
    KEYRING_BINDINGS,
    CredentialResolver,
)
from uls.settings.credential_roles import ROLES, canvas_role, validate_locators

pytestmark = pytest.mark.contract


def test_role_table_matches_shared_bindings(tmp_path):
    for role in ROLES.values():
        assert role.name in ALLOWED_SOURCES
        if role.service:
            assert (role.service, role.account) == KEYRING_BINDINGS[role.name]
        elif role.provider == "notion":
            assert role.filename == FILE_BINDINGS[role.name]
    role = canvas_role("c" + "a" * 32)
    assert role.locator(tmp_path, "staged").endswith(":staging")
    assert CredentialResolver(environ={}).diagnose(frozenset({"CANVAS_PAT"})).results["CANVAS_PAT"].detail == "canvas_profile_required"


def test_other_roles_locator_rejected(tmp_path):
    binding = {"provider": "google", "profile": "default", "role": "mcp", "store_locator": ROLES["google-worker"].locator(tmp_path)}
    with pytest.raises(ValueError):
        validate_locators(binding, tmp_path)
