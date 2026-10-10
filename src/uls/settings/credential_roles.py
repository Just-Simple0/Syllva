"""Code-owned credential roles; browser input never chooses a physical locator."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uls.config._secure_file import secrets_directory


@dataclass(frozen=True)
class CredentialRole:
    slug: str
    provider: str
    purpose: str
    name: str
    filename: str = ""
    service: str = ""
    account: str = ""
    max_bytes: int = 4096
    profile: str = "default"

    @property
    def role_key(self) -> str:
        return f"{self.provider}/{self.profile}/{self.purpose}"

    def locator(self, root: Path, slot: str = "active") -> str:
        if slot not in {"active", "staged", "backup"}:
            raise ValueError("unknown credential slot")
        suffix = {"active": "", "staged": ".staging", "backup": ".backup"}[slot]
        if self.service:
            if self.provider == "canvas":
                suffix = {"active": "", "staged": ":staging", "backup": ":backup"}[slot]
            return f"keyring:{self.service}/{self.account}{suffix}"
        return str(root.resolve() / (self.filename + suffix))

    def physical_key(self, root: Path) -> str:
        return hashlib.sha256(self.locator(root).encode()).hexdigest()

    def candidate(self, raw: dict[str, Any], root: Path, revision: str, *, detach: bool = False) -> dict[str, Any]:
        result = copy.deepcopy(raw)
        result.setdefault("credential_revisions", {})[self.slug] = revision
        if self.provider == "notion":
            credentials = result.setdefault("credentials", {})
            if detach:
                credentials.pop(self.name, None)
            else:
                credentials[self.name] = {"source": "keyring" if self.service else "file"}
        else:
            key = f"google_{self.purpose}_credentials_path"
            result.setdefault("google_drive", {}).pop(f"{self.purpose}_credentials_path", None)
            if detach:
                result.pop(key, None)
            else:
                result[key] = self.locator(root)
        return result


ROLES = {
    role.slug: role for role in (
        CredentialRole("notion-mcp", "notion", "mcp", "NOTION_MCP_TOKEN",
                       service="Syllva MCP", account="notion_mcp_token"),
        CredentialRole("notion-worker", "notion", "worker", "NOTION_WORKER_TOKEN",
                       filename="notion_worker_token.secret"),
        CredentialRole("google-mcp", "google", "mcp", "GOOGLE_MCP_CREDENTIALS_FILE",
                       filename="google_mcp_service_account.json", max_bytes=65536),
        CredentialRole("google-worker", "google", "worker", "GOOGLE_WORKER_CREDENTIALS_FILE",
                       filename="google_worker_service_account.json", max_bytes=65536),
    )
}


def role_from_binding(binding: dict[str, Any]) -> CredentialRole:
    if binding.get("provider") == "canvas" and binding.get("role") == "pat":
        return canvas_role(binding["profile"])
    if binding.get("profile") != "default":
        raise ValueError("credential profile is not supported")
    role = ROLES.get(f"{binding.get('provider')}-{binding.get('role')}")
    if role is None:
        raise ValueError("credential role is not supported")
    return role


def canvas_role(profile_id: str) -> CredentialRole:
    from uls.config.credentials import canvas_keyring_locator
    service, account = canvas_keyring_locator(profile_id)
    return CredentialRole("canvas:" + profile_id, "canvas", "pat", "CANVAS_PAT",
                          service=service, account=account, profile=profile_id)


def validate_locators(binding: dict[str, Any], root: Path | None = None) -> None:
    role = role_from_binding(binding)
    # File locators fix the root as well as the basename. Production roots
    # come from secrets_directory; test JournalStore injects its private root.
    base = root if root is not None else secrets_directory()
    for field, slot in (("store_locator", "active"), ("staging_locator", "staged"), ("backup_locator", "backup")):
        if field in binding and binding[field] != role.locator(base, slot):
            raise ValueError("credential locator is not canonical")
