from __future__ import annotations

import hashlib

import pytest

from uls.config._secure_file import is_reserved_secret_locator, temporary_secret_name
from uls.settings.credential_roles import ROLES
from uls.settings.credential_stores import CredentialStores, FakeKeyring

pytestmark = pytest.mark.contract


def test_slot_invariant_role_scoped_private_ids(tmp_path):
    stores = CredentialStores(tmp_path, backend=FakeKeyring())
    a, b = ROLES["notion-mcp"], ROLES["notion-worker"]
    for slot in ("active", "staged", "backup"):
        stores.write(a, b"fake-value", slot)
    assert stores.state(a) == stores.state(a, "staged") == stores.state(a, "backup")
    assert stores.value_id(a, b"fake-value") != stores.value_id(b, b"fake-value")
    assert stores.state(a) != "h:" + hashlib.sha256(b"fake-value").hexdigest()
    stores.delete(a)
    assert stores.state(a) == "absent"
    assert (tmp_path / "settings_state.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name", ["google.json.staging", "google.json.backup", temporary_secret_name("google.json.staging"), "binding.lock", "binding.reservation"])
def test_reserved_temporary_names(tmp_path, name):
    assert is_reserved_secret_locator(tmp_path / name, root=tmp_path)
    assert not is_reserved_secret_locator(tmp_path.parent / name, root=tmp_path)
