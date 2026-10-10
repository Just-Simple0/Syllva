"""Personal Google Desktop OAuth client configuration and exact credential parsing.

Design: ``docs/plans/drive-oauth-p2-r2.md``.  One user-owned Google Cloud
Desktop client is configured in ``config.yaml``; separate consents produce one
``authorized_user`` refresh credential per purpose (read-only MCP, read/write
WORKER).  Roles, scopes and the token endpoint are code-owned and never read
from configuration.  Nothing in this module performs network I/O.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

from .errors import ConfigurationError

MCP_SCOPE: Final[str] = "https://www.googleapis.com/auth/drive.readonly"
WORKER_SCOPE: Final[str] = "https://www.googleapis.com/auth/drive"
TOKEN_URI: Final[str] = "https://oauth2.googleapis.com/token"
AUTH_URI: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
AUTHORIZED_USER_TYPE: Final[str] = "authorized_user"
SERVICE_ACCOUNT_TYPE: Final[str] = "service_account"
AUTHORIZED_USER_KEYS: Final[frozenset[str]] = frozenset(
    {"type", "client_id", "client_secret", "refresh_token", "token_uri", "scopes"}
)
_CONFIG_KEYS: Final[frozenset[str]] = frozenset({"client_id", "client_secret"})


class GoogleOAuthPurpose(str, Enum):
    """Code-owned purpose -> exact single scope mapping."""

    MCP = "mcp"
    WORKER = "worker"

    @property
    def scope(self) -> str:
        return MCP_SCOPE if self is GoogleOAuthPurpose.MCP else WORKER_SCOPE

    @property
    def role_slug(self) -> str:
        return f"google-{self.value}"

    @property
    def credential_name(self) -> str:
        return "GOOGLE_MCP_CREDENTIALS_FILE" if self is GoogleOAuthPurpose.MCP else "GOOGLE_WORKER_CREDENTIALS_FILE"

    @classmethod
    def parse(cls, value: object) -> GoogleOAuthPurpose:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for purpose in cls:
                if purpose.value == value:
                    return purpose
        raise ValueError("unknown Google OAuth purpose")


class GoogleOAuthCredentialError(ConfigurationError):
    """A persisted or candidate OAuth credential failed the exact contract.

    ``code`` is a fixed short string; no credential value ever enters the message.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.oauth_code = code


@dataclass(frozen=True)
class GoogleOAuthClient:
    """Non-secret client ID plus the Desktop client secret (never repr'd)."""

    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("client_id", "client_secret"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("google_oauth values must be non-empty strings without surrounding whitespace")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("google_oauth values must not contain control characters")


def parse_google_oauth_section(raw: object) -> GoogleOAuthClient | None:
    """All-or-none ``google_oauth:`` section parser (plan §2).

    Absent, ``None`` or an empty mapping yields ``None``.  Exactly the two
    string keys ``client_id`` and ``client_secret`` yield a client.  Any
    partial pair, extra key or non-string value fails closed with
    :class:`ValueError`, matching the other loader parsers.
    """

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("google_oauth must be a YAML mapping")  # noqa: TRY004 - parser contract uses ValueError
    if not raw:
        return None
    keys = {key for key in raw}
    if keys != _CONFIG_KEYS:
        raise ValueError("google_oauth must contain exactly client_id and client_secret")
    for key in _CONFIG_KEYS:
        if not isinstance(raw[key], str):
            raise ValueError(f"google_oauth.{key} must be a string")  # noqa: TRY004 - parser contract uses ValueError
    return GoogleOAuthClient(client_id=raw["client_id"], client_secret=raw["client_secret"])


def config_file_is_private(path: str | os.PathLike[str]) -> bool:
    """POSIX readiness check: the config holding a client secret must be owner-only.

    Windows keeps its existing semantics (always ready).  The check never
    changes permissions.
    """

    if os.name == "nt":
        return True
    try:
        info = os.lstat(Path(path).expanduser())
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    return not (info.st_mode & 0o077)


@dataclass(frozen=True)
class AuthorizedUserCredential:
    """Validated six-key ``authorized_user`` record bound to one purpose."""

    purpose: GoogleOAuthPurpose
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    scope: str = ""

    def __post_init__(self) -> None:
        if self.scope != self.purpose.scope:
            raise GoogleOAuthCredentialError("OAUTH_GRANT_MISMATCH")
        for name in ("client_id", "client_secret", "refresh_token"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(char.isspace() for char in value):
                raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")

    def to_info(self) -> dict[str, Any]:
        return {
            "type": AUTHORIZED_USER_TYPE,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "token_uri": TOKEN_URI,
            "scopes": [self.scope],
        }

    def to_canonical_json(self) -> bytes:
        """Exact persisted form: sorted keys, no whitespace, UTF-8, trailing newline-free."""

        return json.dumps(self.to_info(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def credential_type(info: object) -> str | None:
    """Cheap, value-free type dispatch for an already-parsed credential mapping."""

    if not isinstance(info, Mapping):
        return None
    kind = info.get("type")
    if isinstance(kind, str) and kind in {AUTHORIZED_USER_TYPE, SERVICE_ACCOUNT_TYPE}:
        return kind
    return None


def is_authorized_user_info(info: object) -> bool:
    return credential_type(info) == AUTHORIZED_USER_TYPE


def is_service_account_info(info: object) -> bool:
    return credential_type(info) == SERVICE_ACCOUNT_TYPE


def credential_type_of_bytes(value: bytes | None) -> str | None:
    """Type of a stored credential file without exposing any value."""

    if not value:
        return None
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return credential_type(parsed)


def parse_authorized_user(
    info: Mapping[str, Any], *, purpose: GoogleOAuthPurpose, client: GoogleOAuthClient,
) -> AuthorizedUserCredential:
    """Validate the exact six logical keys of a resolver-produced payload.

    Raw duplicate JSON members are already collapsed by ``json.loads`` before
    this parser runs; they are deliberately not a claim of this contract.
    """

    if not isinstance(info, Mapping):
        raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")
    keys = {key for key in info}
    if keys != AUTHORIZED_USER_KEYS:
        raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")
    if info["type"] != AUTHORIZED_USER_TYPE:
        raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")
    for name in ("client_id", "client_secret", "refresh_token", "token_uri"):
        value = info[name]
        if not isinstance(value, str) or not value or any(char.isspace() for char in value):
            raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")
    if info["token_uri"] != TOKEN_URI:
        raise GoogleOAuthCredentialError("INVALID_CREDENTIAL")
    if info["client_id"] != client.client_id or info["client_secret"] != client.client_secret:
        raise GoogleOAuthCredentialError("OAUTH_CLIENT_MISMATCH")
    scopes = info["scopes"]
    if not isinstance(scopes, list) or len(scopes) != 1 or not isinstance(scopes[0], str):
        raise GoogleOAuthCredentialError("OAUTH_GRANT_MISMATCH")
    if scopes[0] != purpose.scope:
        raise GoogleOAuthCredentialError("OAUTH_GRANT_MISMATCH")
    return AuthorizedUserCredential(
        purpose=purpose, client_id=info["client_id"], client_secret=info["client_secret"],
        refresh_token=info["refresh_token"], scope=scopes[0],
    )


def parse_authorized_user_bytes(
    value: bytes, *, purpose: GoogleOAuthPurpose, client: GoogleOAuthClient,
) -> AuthorizedUserCredential:
    """Parse stored bytes through the same exact contract as the resolver payload."""

    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GoogleOAuthCredentialError("INVALID_CREDENTIAL") from None
    return parse_authorized_user(parsed, purpose=purpose, client=client)


def exact_scopes(granted: object, purpose: GoogleOAuthPurpose) -> bool:
    """True only when a provider-reported scope set is exactly the purpose scope."""

    if isinstance(granted, str):
        values = granted.split()
    elif isinstance(granted, (list, tuple, set, frozenset)):
        values = [item for item in granted]
    else:
        return False
    return len(values) == 1 and values[0] == purpose.scope


__all__ = [
    "AUTHORIZED_USER_KEYS",
    "AUTHORIZED_USER_TYPE",
    "AUTH_URI",
    "MCP_SCOPE",
    "SERVICE_ACCOUNT_TYPE",
    "TOKEN_URI",
    "WORKER_SCOPE",
    "AuthorizedUserCredential",
    "GoogleOAuthClient",
    "GoogleOAuthCredentialError",
    "GoogleOAuthPurpose",
    "config_file_is_private",
    "credential_type",
    "credential_type_of_bytes",
    "exact_scopes",
    "is_authorized_user_info",
    "is_service_account_info",
    "parse_authorized_user",
    "parse_authorized_user_bytes",
    "parse_google_oauth_section",
]
