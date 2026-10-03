"""Explicit isolated fake stores/providers for local browser evidence."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from uls.config._secure_file import delete_secure_file, read_secure_file, write_secure_file
from uls.config.errors import ConfigurationError

from .canvas_checks import CanvasCheckError
from .credential_stores import CredentialStores
from .provider_checks import FakeProviderTransport, ProviderChecks


class FileFakeKeyring:
    storage_label = "the fake test store"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, service: str, account: str) -> Path:
        return self.root / ("fake-keyring-" + hashlib.sha256((service + "\0" + account).encode()).hexdigest())

    def get_password(self, service: str, account: str) -> str | None:
        try:
            return read_secure_file(self._path(service, account), max_bytes=65536).decode()
        except ConfigurationError as exc:
            if exc.details.get("problems") in (["secret_file_missing"], ["secret_dir_missing"]):
                return None
            raise

    def set_password(self, service: str, account: str, value: str) -> None:
        write_secure_file(self._path(service, account), value.encode(), max_bytes=65536)

    def delete_password(self, service: str, account: str) -> None:
        delete_secure_file(self._path(service, account))


def fake_canvas_verify(origin: str, token: str) -> dict[str, str]:
    if "invalid" in token:
        raise CanvasCheckError("INVALID_CREDENTIAL")
    if "outage" in token:
        raise CanvasCheckError("PROVIDER_UNAVAILABLE")
    return {"user_id": "12345", "display_name": "Demo Student"}


def fake_canvas_discover(origin: str, token: str) -> dict[str, Any]:
    fake_canvas_verify(origin, token)
    rows = [{"course_id": str(41921 + index), "term_id": "678" if index < 4 else "679",
             "term_name": "Autumn 2026" if index < 4 else "Spring 2027",
             "name": name, "code": code} for index, (name, code) in enumerate((
                 ("Database Systems", "DB101"), ("Computer Networks", "NET201"),
                 ("Software Engineering", "SE301"), ("Operating Systems", "OS201"),
                 ("Algorithms", "ALG202")))]
    return {"courses": rows}


def fake_canvas_selection(origin: str, token: str, ids: Iterable[str]) -> list[dict[str, str]]:
    ids = list(ids)
    return [{key: row[key] for key in ("course_id", "term_id", "name", "code")}
            for row in fake_canvas_discover(origin, token)["courses"] if row["course_id"] in ids]


def services(config: Any, journal: Any, root: Path) -> tuple[Any, Any]:
    from .canvas_service import CanvasService
    from .composition import _FakeCredentialService

    root.mkdir(mode=0o700, exist_ok=True)
    credentials = _FakeCredentialService(
        config, journal, CredentialStores(root, backend=FileFakeKeyring(root)),
        ProviderChecks(FakeProviderTransport(), cooldown=0), platform="darwin",
        environ={}, google_loader=lambda *a, **k: None)
    canvas = CanvasService(credentials, verifier=fake_canvas_verify, discoverer=fake_canvas_discover,
                           selection_reader=fake_canvas_selection, storage_label="the fake test store")
    return credentials, canvas
