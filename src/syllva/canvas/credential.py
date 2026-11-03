"""The Canvas token: OS keychain only (design §8, I9; R3 plan §6.1).

Order of a connection: verify the token against the Canvas origin (in memory) ->
commit a ``canvas_profile`` row with ``token_state='PENDING'`` -> write the keychain
entry and read it back exactly -> commit ``token_state='SET'``. The keychain cannot
be enumerated, so the PENDING row is the record of an operation that may have left
an entry behind; ``recover()`` (Core start-up) deletes the entry and the row.

The keychain backend is constructed explicitly (never ``keyring.get_keyring()``,
which honours ``PYTHON_KEYRING_BACKEND``/``keyringrc.cfg`` and could select a
plaintext backend): macOS ``Keyring`` with alternate keychains blocked, Windows
``WinVaultKeyring``; Linux ``SecretService`` only when the Core environment sets
``SYLLVA_DEV_KEYRING=secretservice`` (the Shell never passes that variable).

Errors carry fixed codes only. A backend exception is never chained, because its
text may contain the value it was given.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol

from syllva.canvas.http import CanvasCheckError, validate_canvas_origin
from syllva.core.logging import log_event
from syllva.state.v2.ownership import Actor, Owner
from syllva.state.v2.writer import StateWriter

SERVICE = "Syllva Canvas"
MAX_TOKEN = 512
MAX_FILE_HOSTS = 4
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class CredentialError(Exception):
    """A fixed code; never the token, never a backend message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    error = CredentialError(code)
    error.__suppress_context__ = True
    raise error


class CanvasToken:
    """The token in memory: never printable, never serializable."""

    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        if (not isinstance(value, str) or not value or len(value) > MAX_TOKEN or not value.isascii()
                or not value.isprintable() or any(ch.isspace() for ch in value)):
            _fail("TOKEN_INVALID")
        self._value = value

    def reveal_token(self) -> str:
        """Only the Canvas HTTP calls and the keychain write use this."""

        return self._value

    def __repr__(self) -> str:
        return "<canvas token>"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce__(self) -> NoReturn:
        raise TypeError("CanvasToken cannot be pickled")

    def __getstate__(self) -> NoReturn:
        raise TypeError("CanvasToken cannot be serialized")


class Keychain(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...

    def set_password(self, service: str, account: str, value: str) -> None: ...

    def delete_password(self, service: str, account: str) -> None: ...


def profile_id(origin: str, user_id: str) -> str:
    return "c" + hashlib.sha256(f"{origin}\n{user_id}".encode()).hexdigest()[:32]


def account(pid: str) -> str:
    return f"profile:{pid}"


def _construct(module_name: str, class_name: str) -> Any:
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ImportError:
        _fail("KEYCHAIN_UNAVAILABLE")
    try:
        backend = getattr(module, class_name)()
    except Exception:  # noqa: BLE001 - redacted to a code
        _fail("KEYCHAIN_UNAVAILABLE")
    if type(backend).__module__ != module_name:
        _fail("KEYCHAIN_BACKEND_INVALID")
    return backend


def os_keychain(*, platform: str | None = None, env: Mapping[str, str] | None = None) -> Keychain:
    """The platform keychain, constructed explicitly (``platform``/``env`` are test overrides)."""

    current = platform if platform is not None else sys.platform
    environ = env if env is not None else os.environ
    if current == "darwin":
        backend = _construct("keyring.backends.macOS", "Keyring")
        backend.keychain = None  # never an alternate keychain file
        if backend.keychain is not None:
            _fail("KEYCHAIN_BACKEND_INVALID")
        return backend  # type: ignore[no-any-return]
    if current == "win32":
        return _construct("keyring.backends.Windows", "WinVaultKeyring")  # type: ignore[no-any-return]
    if current.startswith("linux") and environ.get("SYLLVA_DEV_KEYRING") == "secretservice":
        return _construct("keyring.backends.SecretService", "Keyring")  # type: ignore[no-any-return]
    _fail("KEYCHAIN_UNAVAILABLE")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_file_hosts(hosts: object) -> list[str]:
    """Exact public DNS names (no IP literals, ports, schemes or single labels), at most four."""

    if not isinstance(hosts, list) or len(hosts) > MAX_FILE_HOSTS:
        _fail("FILE_HOSTS_INVALID")
    cleaned: list[str] = []
    for host in hosts:
        if not isinstance(host, str) or not host.isascii() or len(host) > 253:
            _fail("FILE_HOSTS_INVALID")
        name = host.lower()
        labels = name.split(".")
        try:
            ipaddress.ip_address(name.strip("[]"))
            _fail("FILE_HOSTS_INVALID")
        except ValueError:
            pass
        if (len(labels) < 2 or name == "localhost" or name.endswith(".localhost")
                or not all(_LABEL.fullmatch(label) for label in labels) or labels[-1].isdigit()):
            _fail("FILE_HOSTS_INVALID")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


Verify = Callable[[str, str], Mapping[str, str]]


class CanvasCredentials:
    """The Canvas connection: profile row in SQLite, token in the keychain."""

    def __init__(self, writer: StateWriter, *, keychain: Callable[[], Keychain] = os_keychain, verify: Verify,
                 now: Callable[[], str] = _now) -> None:
        self._writer = writer
        self._keychain = keychain
        self._verify = verify
        self._now = now
        self._lock = threading.Lock()

    # -- reads ---------------------------------------------------------------------------------

    def _profile(self) -> Any:
        return self._writer.query_one("SELECT * FROM canvas_profile WHERE id = 1")

    def status(self) -> dict[str, Any]:
        row = self._profile()
        if row is None:
            return {"connected": False}
        courses = self._writer.query(
            "SELECT k.canvas_course_id, k.name, k.code, k.term_name, c.course_key FROM canvas_courses k "
            "JOIN courses c ON c.id = k.course_id ORDER BY k.canvas_course_id")
        return {
            "connected": row["token_state"] == "SET", "needs_token": row["token_state"] == "PENDING",
            "profile_id": row["profile_id"], "origin": row["origin"], "display_name": row["display_name"],
            "token_set_at": row["token_set_at"], "file_hosts": json.loads(row["file_hosts"]),
            "courses": [dict(c) for c in courses],
        }

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        """A read for the settings/activities views (the writer's read-only query)."""

        return self._writer.query(sql, params)

    def token(self) -> CanvasToken:
        """The stored token, for a Canvas read (only when the profile is SET)."""

        row = self._profile()
        if row is None or row["token_state"] != "SET":
            _fail("TOKEN_NOT_SET")
        try:
            value = self._keychain().get_password(SERVICE, account(row["profile_id"]))
        except CredentialError:
            raise
        except Exception:  # noqa: BLE001 - redacted to a code
            _fail("KEYCHAIN_READ_FAILED")
        if not value:
            _fail("KEYCHAIN_CREDENTIAL_MISSING")
        return CanvasToken(value)

    def origin(self) -> str:
        row = self._profile()
        if row is None:
            _fail("TOKEN_NOT_SET")
        return str(row["origin"])

    # -- connect / forget / recover -------------------------------------------------------------

    def connect(self, origin: str, token: CanvasToken) -> dict[str, Any]:
        target = validate_canvas_origin(origin).origin  # CanvasCheckError(DESTINATION_NOT_ALLOWED)
        existing = self._profile()
        if existing is not None and existing["origin"] != target:
            _fail("PROFILE_EXISTS")  # refused before the token is sent anywhere (review of PR #54)
        user = self._verify(target, token.reveal_token())  # CanvasCheckError with a fixed code
        pid = profile_id(target, str(user["user_id"]))
        with self._lock:
            row = self._profile()
            if row is not None and row["profile_id"] != pid:
                _fail("PROFILE_EXISTS")
            with self._writer.transaction():
                if row is None:
                    self._writer.insert(Owner.SETTINGS, Actor.UI_USER, "canvas_profile", {
                        "id": 1, "profile_id": pid, "origin": target, "user_id": str(user["user_id"]),
                        "display_name": str(user.get("display_name", ""))[:200], "token_state": "PENDING",
                        "created_at": self._now()})
                else:
                    self._writer.update(Owner.SETTINGS, Actor.UI_USER, "canvas_profile", {"profile_id": pid},
                                        {"token_state": "PENDING", "token_set_at": None})
            if not self._store(pid, token):
                self._drop(pid, Actor.UI_USER)
                log_event("CANVAS_TOKEN_REFUSED")
                _fail("KEYCHAIN_WRITE_FAILED")
            self._writer.update(Owner.SETTINGS, Actor.UI_USER, "canvas_profile", {"profile_id": pid},
                                {"token_state": "SET", "token_set_at": self._now(),
                                 "display_name": str(user.get("display_name", ""))[:200]})
        log_event("CANVAS_CONNECTED")
        return self.status()

    def _store(self, pid: str, token: CanvasToken) -> bool:
        try:
            keychain = self._keychain()
            keychain.set_password(SERVICE, account(pid), token.reveal_token())
            return keychain.get_password(SERVICE, account(pid)) == token.reveal_token()
        except Exception:  # noqa: BLE001 - CredentialError included: any failure means "not stored"
            return False

    def _erase(self, pid: str) -> bool:
        try:
            keychain = self._keychain()
            if keychain.get_password(SERVICE, account(pid)) is not None:
                keychain.delete_password(SERVICE, account(pid))
            return keychain.get_password(SERVICE, account(pid)) is None
        except Exception:  # noqa: BLE001
            return False

    def _drop(self, pid: str, actor: Actor) -> bool:
        """Erase the keychain entry, then the row and its course links. False keeps the PENDING row."""

        if not self._erase(pid):
            return False
        with self._writer.transaction():
            for link in self._writer.query("SELECT canvas_course_id FROM canvas_courses"):
                self._writer.delete(Owner.SETTINGS, actor, "canvas_courses",
                                    {"canvas_course_id": link["canvas_course_id"]})
            self._writer.delete(Owner.SETTINGS, actor, "canvas_profile", {"profile_id": pid})
        return True

    def forget(self) -> None:
        with self._lock:
            row = self._profile()
            if row is None:
                return
            pid = row["profile_id"]
            self._writer.update(Owner.SETTINGS, Actor.UI_USER, "canvas_profile", {"profile_id": pid},
                                {"token_state": "PENDING", "token_set_at": None})
            if not self._drop(pid, Actor.UI_USER):
                _fail("KEYCHAIN_DELETE_FAILED")
        log_event("CANVAS_FORGOTTEN")

    def recover(self) -> str:
        """Start-up: finish an interrupted connect/forget (a PENDING row)."""

        with self._lock:
            row = self._profile()
            if row is None or row["token_state"] != "PENDING":
                return "NONE"
            if not self._drop(row["profile_id"], Actor.CANVAS_COLLECTOR):
                log_event("CANVAS_RECOVERY_DEFERRED")
                return "KEYCHAIN_DELETE_FAILED"
        log_event("CANVAS_RECOVERED")
        return "CLEANED"

    # -- settings ------------------------------------------------------------------------------

    def set_file_hosts(self, hosts: object) -> list[str]:
        cleaned = clean_file_hosts(hosts)
        with self._lock:
            row = self._profile()
            if row is None:
                _fail("TOKEN_NOT_SET")
            self._writer.update(Owner.SETTINGS, Actor.UI_USER, "canvas_profile", {"profile_id": row["profile_id"]},
                                {"file_hosts": json.dumps(cleaned)})
        return cleaned

    def link_course(self, canvas_course_id: str, course_key: str | None, meta: Mapping[str, str]) -> None:
        """Link a Canvas course to a local course (``course_key=None`` unlinks)."""

        if not re.fullmatch(r"[0-9]{1,64}", canvas_course_id):
            _fail("COURSE_UNKNOWN")
        with self._lock, self._writer.transaction():
            if self._profile() is None:
                _fail("TOKEN_NOT_SET")
            self._writer.delete(Owner.SETTINGS, Actor.UI_USER, "canvas_courses", {"canvas_course_id": canvas_course_id})
            if course_key is None:
                return
            course = self._writer.query_one("SELECT id FROM courses WHERE course_key = ?", (course_key,))
            if course is None:
                _fail("COURSE_UNKNOWN")
            if self._writer.query_one("SELECT 1 FROM canvas_courses WHERE course_id = ?", (course["id"],)):
                _fail("COURSE_ALREADY_LINKED")
            self._writer.insert(Owner.SETTINGS, Actor.UI_USER, "canvas_courses", {
                "canvas_course_id": canvas_course_id, "course_id": int(course["id"]),
                "name": str(meta.get("name", ""))[:200], "code": str(meta.get("code", ""))[:64],
                "term_name": str(meta.get("term_name", ""))[:64], "linked_at": self._now()})


__all__ = [
    "SERVICE", "CanvasCheckError", "CanvasCredentials", "CanvasToken", "CredentialError", "Keychain",
    "account", "clean_file_hosts", "os_keychain", "profile_id",
]
