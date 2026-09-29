"""Shared temp-config and TestClient helpers for Local Settings contract tests."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from uls.settings.app import create_settings_app
from uls.settings.config_service import ConfigStore
from uls.settings.journal import JournalStore
from uls.settings.security import MutationBarrier, SessionSecurity, new_path_prefix

REPO = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1:8765"
ORIGIN = f"http://{HOST}"
NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-site": "none"}
FETCH = {"sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}
PRIVATE_SENTINEL = "private-sentinel-value-7f3a"


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    data["system"]["workspace_dir"] = str(tmp_path / "workspace")
    data["behavior_contract"]["path"] = str(REPO / "contracts" / "study-behavior.md")
    data["x_unknown_section"] = {"keep": [1, 2], "api_key": PRIVATE_SENTINEL}
    data["retrieval"]["x_nested_unknown"] = "kept"
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class SettingsHarness:
    client: TestClient
    token: str
    security: SessionSecurity
    store: ConfigStore
    journal: JournalStore
    config_path: Path
    clock: FakeClock
    closed: list[bool]
    prefix: str
    barrier: MutationBarrier

    @property
    def root(self) -> str:
        return f"/{self.prefix}/"

    def bootstrap(self) -> Any:
        return self.client.get(f"?bootstrap={self.token}", headers=NAVIGATE, follow_redirects=False)

    def csrf(self) -> str:
        response = self.client.get("/api/v1/session/csrf", headers=FETCH)
        assert response.status_code == 200, response.text
        value = response.json()["csrf_token"]
        assert isinstance(value, str)
        return value

    def post(self, path: str, body: Any, csrf: str | None, **headers: str) -> Any:
        merged = {**FETCH, "origin": ORIGIN}
        if csrf is not None:
            merged["x-uls-csrf"] = csrf
        merged.update(headers)
        return self.client.post(path, json=body, headers=merged)

    def signed_in(self) -> str:
        assert self.bootstrap().status_code == 303
        return self.csrf()

    def review(self, group: str, values: dict[str, Any], csrf: str) -> dict[str, Any]:
        response = self.post(f"/api/v1/settings/{group}/validate",
                             {"values": values, "generation": self.store.load().generation}, csrf)
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    def apply_reviewed(self, group: str, reviewed: dict[str, Any], csrf: str) -> Any:
        return self.post(f"/api/v1/settings/{group}/apply", {
            "values": reviewed["values"], "generation": reviewed["generation"],
            "candidate_hash": reviewed["candidate_hash"]}, csrf)


def reviewed_apply(store: ConfigStore, group: str, values: dict[str, Any], journal: JournalStore,
                   **kwargs: Any) -> dict[str, Any]:
    """Service-level validate-then-apply with the server-issued candidate hash."""

    generation = store.load().generation
    preview = store.preview(group, values, generation)
    result: dict[str, Any] = store.apply(group, values, generation, journal,
                                         candidate_hash=preview["candidate_hash"], **kwargs)
    return result


def make_harness(
    tmp_path: Path, *, replaced_previous: bool = False,
    clock: FakeClock | None = None, on_close: Callable[[], None] | None = None,
) -> SettingsHarness:
    config_path = write_config(tmp_path)
    fake_clock = clock or FakeClock()
    token, security = SessionSecurity.issue(clock=fake_clock)
    store = ConfigStore(config_path)
    journal = JournalStore(tmp_path / "workspace")
    closed: list[bool] = []
    prefix = new_path_prefix()
    barrier = MutationBarrier()

    def close_hook() -> None:
        closed.append(True)
        if on_close is not None:
            on_close()

    app = create_settings_app(
        store, journal, security, HOST, prefix=prefix, barrier=barrier, on_close=close_hook,
        replaced_previous=replaced_previous,
    )
    # httpx appends request paths to the base path, so "/api/v1/..." resolves
    # under this launch's prefix.
    client = TestClient(app, base_url=f"{ORIGIN}/{prefix}/")
    return SettingsHarness(client, token, security, store, journal, config_path, fake_clock, closed,
                           prefix, barrier)


def monotonic() -> float:
    return time.monotonic()
