"""Compose Settings services without touching providers or stores at startup."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from uls.config._secure_file import secrets_directory

from .canvas_checks import CanvasCheckError
from .canvas_service import CanvasService
from .config_service import ConfigStore
from .credential_roles import CredentialRole
from .credential_service import CredentialService
from .credential_stores import CredentialStores, FakeKeyring
from .journal import JournalStore
from .provider_checks import FakeProviderTransport, ProviderChecks

FAKE_STORES_ENV = "ULS_SETTINGS_FAKE_STORES"


class _FakeCredentialService(CredentialService):
    """Fake mode never reads environment credentials or external secret files."""

    def effective(self, role: CredentialRole, raw: dict[str, Any]) -> bytes | None:
        source, _path = self.source(role, raw)
        if source in {"environment", "external_file"}:
            return None
        return self.stores.read(role) if source in {"file", "keyring"} else None


def _fake_verify(origin: str, token: str) -> dict[str, str]:
    if "invalid" in token:
        raise CanvasCheckError("INVALID_CREDENTIAL")
    if "outage" in token:
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return {"user_id": "12345", "display_name": "Student N."}


_FAKE_COURSES = (
    {"course_id": "41921", "term_id": "678", "term_name": "2026 Fall",
     "name": "Database Systems", "code": "DB101"},
    {"course_id": "41922", "term_id": "678", "term_name": "2026 Fall",
     "name": "Network Programming", "code": "NET201"},
    {"course_id": "41923", "term_id": "679", "term_name": "2027 Spring",
     "name": "Algorithms", "code": "CS202"},
)


def _fake_discover(origin: str, token: str) -> dict[str, Any]:
    _fake_verify(origin, token)
    return {"courses": [dict(row) for row in _FAKE_COURSES], "terms": [
        {"term_id": "678", "name": "2026 Fall"},
        {"term_id": "679", "name": "2027 Spring"},
    ]}


def _fake_selection(origin: str, token: str, course_ids: Any) -> list[dict[str, str]]:
    _fake_verify(origin, token)
    selected = set(course_ids)
    return [dict(row) for row in _FAKE_COURSES if row["course_id"] in selected]


def build_settings_services(
    config: ConfigStore, journal: JournalStore, runtime_dir: Path, *, fake_mode: bool = False,
    fake_root: Path | None = None,
) -> tuple[CredentialService, CanvasService]:
    """Fake storage is explicit and isolated; Linux remains non-mutating."""
    credentials: CredentialService
    if fake_mode:
        from .fake_mode import FileFakeKeyring

        root = (fake_root or runtime_dir) / "fake-secrets"
        stores = CredentialStores(root, backend=FileFakeKeyring(root))
        credentials = _FakeCredentialService(
            config, journal, stores, ProviderChecks(FakeProviderTransport(), cooldown=0),
            platform="darwin", environ={}, google_loader=lambda *args, **kwargs: None,
        )
        canvas = CanvasService(credentials, verifier=_fake_verify,
                               discoverer=_fake_discover, selection_reader=_fake_selection,
                               storage_label="the fake test store")
        return credentials, canvas
    if sys.platform == "win32":
        raise RuntimeError("Local Settings is unavailable on Windows")
    if sys.platform == "darwin":
        stores = CredentialStores(secrets_directory())
    else:
        # This path is only a locator placeholder. The platform gate forbids
        # writes, and no system keyring is selected on unsupported platforms.
        stores = CredentialStores(runtime_dir / "unsupported-secrets", backend=FakeKeyring())
    credentials = CredentialService(config, journal, stores, ProviderChecks())
    return credentials, CanvasService(credentials)
