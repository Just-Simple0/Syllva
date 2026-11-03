"""R3-1: the Canvas token lives only in the OS keychain (design §8, I9; R3 plan §6.1).

Order: verify -> PENDING row -> keychain write + exact readback -> SET. A PENDING row
found at start-up is an interrupted operation and is cleaned (keychain entry + row).
The token never reaches the database, logs, responses, exception text or ``repr``.
"""

from __future__ import annotations

import pickle
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.keychain import MemoryKeychain
from syllva.canvas import credential
from syllva.canvas.credential import (
    SERVICE,
    CanvasCredentials,
    CanvasToken,
    CredentialError,
    account,
    profile_id,
)
from syllva.canvas.http import CanvasCheckError
from syllva.core import logging as core_logging
from syllva.state.v2.database import Database

pytestmark = pytest.mark.contract

ORIGIN = "https://canvas.example.edu"
TOKEN = "7~SECRETtokenVALUE0123456789abcdefABCDEF"
OTHER = "7~OTHERtokenVALUE0123456789abcdefABCDEF"


def verifier(users: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    users = users if users is not None else {TOKEN: "42", OTHER: "42"}

    def verify(origin: str, token: str) -> dict[str, str]:
        if token not in users:
            raise CanvasCheckError("INVALID_CREDENTIAL")
        return {"user_id": users[token], "display_name": "Kim"}

    return verify


def creds(db: Database, keychain: MemoryKeychain, **kwargs) -> CanvasCredentials:  # type: ignore[no-untyped-def]
    return CanvasCredentials(db.writer(), keychain=lambda: keychain, verify=kwargs.pop("verify", verifier()), **kwargs)


def dump(db: Database) -> str:
    return "\n".join(db.connection.iterdump())


def test_connect_stores_the_token_only_in_the_keychain(v2_db: Database, tmp_path) -> None:  # type: ignore[no-untyped-def]
    core_logging.configure(tmp_path / "logs")
    try:
        keychain = MemoryKeychain()
        status = creds(v2_db, keychain).connect(ORIGIN, CanvasToken(TOKEN))
    finally:
        core_logging.configure(None)
    pid = profile_id(ORIGIN, "42")
    assert keychain.entries == {(SERVICE, f"profile:{pid}"): TOKEN}
    assert status["connected"] is True and status["profile_id"] == pid and status["origin"] == ORIGIN
    assert TOKEN not in repr(status) and TOKEN not in dump(v2_db)
    assert all(TOKEN not in p.read_text() for p in (tmp_path / "logs").glob("*"))
    row = v2_db.connection.execute("SELECT token_state, token_set_at FROM canvas_profile").fetchone()
    assert row[0] == "SET" and row[1]


def test_the_token_holder_never_prints_or_serializes() -> None:
    token = CanvasToken(TOKEN)
    assert TOKEN not in repr(token) and TOKEN not in str(token) and TOKEN not in f"{token}"
    with pytest.raises(TypeError):
        pickle.dumps(token)
    assert token.reveal_token() == TOKEN


@pytest.mark.parametrize("bad", ["", " ", "a b", "tok\n", "tok\x00", "é" * 10, "x" * 513])
def test_malformed_tokens_are_refused(bad: str) -> None:
    with pytest.raises(CredentialError) as caught:
        CanvasToken(bad)
    assert caught.value.code == "TOKEN_INVALID" and (not bad.strip() or bad not in str(caught.value))


def test_an_invalid_token_leaves_nothing_behind(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    with pytest.raises(CanvasCheckError) as caught:
        creds(v2_db, keychain).connect(ORIGIN, CanvasToken("7~wrong"))
    assert caught.value.code == "INVALID_CREDENTIAL"
    assert keychain.entries == {} and v2_db.connection.execute("SELECT count(*) FROM canvas_profile").fetchone()[0] == 0


@pytest.mark.parametrize("failure", [{"fail_set": True}, {"wrong_readback": True}])
def test_a_keychain_failure_rolls_the_connection_back(v2_db: Database, failure: dict[str, bool]) -> None:
    keychain = MemoryKeychain(**failure)
    with pytest.raises(CredentialError) as caught:
        creds(v2_db, keychain).connect(ORIGIN, CanvasToken(TOKEN))
    assert caught.value.code == "KEYCHAIN_WRITE_FAILED"
    assert TOKEN not in str(caught.value) and TOKEN not in repr(caught.value)
    keychain.wrong_readback = False
    assert keychain.entries == {} and v2_db.connection.execute("SELECT count(*) FROM canvas_profile").fetchone()[0] == 0


def test_a_failure_does_not_chain_the_backend_exception(v2_db: Database) -> None:
    with pytest.raises(CredentialError) as caught:
        creds(v2_db, MemoryKeychain(fail_set=True)).connect(ORIGIN, CanvasToken(TOKEN))
    assert caught.value.__cause__ is None and caught.value.__suppress_context__  # the backend text carried the token


def test_pending_rows_are_cleaned_at_startup(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain)
    service.connect(ORIGIN, CanvasToken(TOKEN))
    pid = profile_id(ORIGIN, "42")
    v2_db.connection.execute("UPDATE canvas_profile SET token_state = 'PENDING'")  # a crash between PENDING and SET
    assert service.status()["needs_token"] is True
    with pytest.raises(CredentialError) as caught:
        service.token()
    assert caught.value.code == "TOKEN_NOT_SET"
    assert creds(v2_db, keychain).recover() == "CLEANED"
    assert keychain.entries == {} and service.status() == {"connected": False}
    assert account(pid) == f"profile:{pid}"


def test_recovery_keeps_the_intent_when_the_keychain_is_unavailable(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    creds(v2_db, keychain).connect(ORIGIN, CanvasToken(TOKEN))
    v2_db.connection.execute("UPDATE canvas_profile SET token_state = 'PENDING'")
    keychain.fail_delete = True
    assert creds(v2_db, keychain).recover() == "KEYCHAIN_DELETE_FAILED"
    assert v2_db.connection.execute("SELECT token_state FROM canvas_profile").fetchone()[0] == "PENDING"


def test_a_second_principal_needs_the_first_one_forgotten(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain, verify=verifier({TOKEN: "42", OTHER: "77"}))
    service.connect(ORIGIN, CanvasToken(TOKEN))
    with pytest.raises(CredentialError) as caught:
        service.connect(ORIGIN, CanvasToken(OTHER))
    assert caught.value.code == "PROFILE_EXISTS"
    assert list(keychain.entries.values()) == [TOKEN]


def test_the_same_principal_replaces_its_token(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain)
    service.connect(ORIGIN, CanvasToken(TOKEN))
    service.connect(ORIGIN, CanvasToken(OTHER))
    assert list(keychain.entries.values()) == [OTHER] and service.token().reveal_token() == OTHER


def test_a_failed_replacement_drops_the_connection(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain)
    service.connect(ORIGIN, CanvasToken(TOKEN))
    keychain.fail_set = True
    with pytest.raises(CredentialError):
        service.connect(ORIGIN, CanvasToken(OTHER))
    assert service.status() == {"connected": False} and keychain.entries == {}


def test_forget_removes_the_token_the_row_and_the_course_links(v2_db: Database) -> None:
    v2_db.connection.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET','네트워크','NET','2026-2','t')")
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain)
    service.connect(ORIGIN, CanvasToken(TOKEN))
    service.link_course("1234", "NET", {"name": "Networks", "code": "CS101", "term_name": "2026-2"})
    service.forget()
    assert keychain.entries == {} and service.status() == {"connected": False}
    assert v2_db.connection.execute("SELECT count(*) FROM canvas_courses").fetchone()[0] == 0


def test_a_failed_forget_is_finished_at_startup(v2_db: Database) -> None:
    keychain = MemoryKeychain()
    service = creds(v2_db, keychain)
    service.connect(ORIGIN, CanvasToken(TOKEN))
    keychain.fail_delete = True
    with pytest.raises(CredentialError) as caught:
        service.forget()
    assert caught.value.code == "KEYCHAIN_DELETE_FAILED" and service.status()["needs_token"] is True
    keychain.fail_delete = False
    assert creds(v2_db, keychain).recover() == "CLEANED" and keychain.entries == {}


def test_course_links_are_one_to_one_and_need_a_known_course(v2_db: Database) -> None:
    v2_db.connection.execute(
        "INSERT INTO courses(course_key, name, code, semester, created_at) VALUES ('NET','네트워크','NET','2026-2','t')")
    service = creds(v2_db, MemoryKeychain())
    service.connect(ORIGIN, CanvasToken(TOKEN))
    meta = {"name": "Networks", "code": "CS101", "term_name": "2026-2"}
    with pytest.raises(CredentialError) as caught:
        service.link_course("1234", "NOPE", meta)
    assert caught.value.code == "COURSE_UNKNOWN"
    service.link_course("1234", "NET", meta)
    with pytest.raises(CredentialError):
        service.link_course("5678", "NET", meta)  # one Canvas course per local course
    service.link_course("1234", None, meta)  # unlink
    assert service.status()["courses"] == []


@pytest.mark.parametrize("hosts,ok", [
    (["files.example-cdn.com"], True),
    (["inst-fs-iad-prod.inscloudgate.net", "a.b.c"], True),
    (["localhost"], False), (["127.0.0.1"], False), (["[::1]"], False), (["example"], False),
    (["files.example.com:443"], False), (["https://files.example.com"], False), (["a.b", "c.d", "e.f", "g.h", "i.j"], False),
    (["Files.Example.com"], True), (["xn--ls8h.example"], True), (["-bad.example.com"], False),
])
def test_file_hosts_are_exact_public_names(v2_db: Database, hosts: list[str], ok: bool) -> None:
    service = creds(v2_db, MemoryKeychain())
    service.connect(ORIGIN, CanvasToken(TOKEN))
    if ok:
        assert service.set_file_hosts(hosts) == [h.lower() for h in hosts]
    else:
        with pytest.raises(CredentialError) as caught:
            service.set_file_hosts(hosts)
        assert caught.value.code == "FILE_HOSTS_INVALID"


# --- the OS keychain backend (explicit construction, R3 plan §6.1) -----------------------------


def test_the_backend_is_never_chosen_by_keyring_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    keyring = pytest.importorskip("keyring")

    monkeypatch.setattr(keyring, "get_keyring", lambda: pytest.fail("get_keyring must not be used"))
    with pytest.raises(CredentialError) as caught:
        credential.os_keychain(platform="linux", env={})
    assert caught.value.code == "KEYCHAIN_UNAVAILABLE"
    with pytest.raises(CredentialError):
        credential.os_keychain(platform="freebsd", env={"SYLLVA_DEV_KEYRING": "secretservice"})


def _fake_backend_module(monkeypatch: pytest.MonkeyPatch, name: str, cls_name: str, *, keychain_sticks: bool = False):  # type: ignore[no-untyped-def]
    class Backend:
        def __init__(self) -> None:
            self._keychain = "alt.keychain"

        @property
        def keychain(self):  # type: ignore[no-untyped-def]
            return self._keychain

        @keychain.setter
        def keychain(self, value):  # type: ignore[no-untyped-def]
            if not keychain_sticks:
                self._keychain = value

    Backend.__module__ = name
    Backend.__name__ = cls_name
    module = types.ModuleType(name)
    setattr(module, cls_name, Backend)
    monkeypatch.setitem(sys.modules, name, module)
    return Backend


def test_macos_backend_blocks_alternate_keychains(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("keyring")
    _fake_backend_module(monkeypatch, "keyring.backends.macOS", "Keyring")
    backend = credential.os_keychain(platform="darwin", env={})
    assert backend.keychain is None
    _fake_backend_module(monkeypatch, "keyring.backends.macOS", "Keyring", keychain_sticks=True)
    with pytest.raises(CredentialError) as caught:
        credential.os_keychain(platform="darwin", env={})
    assert caught.value.code == "KEYCHAIN_BACKEND_INVALID"


def test_windows_backend_is_the_credential_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("keyring")
    _fake_backend_module(monkeypatch, "keyring.backends.Windows", "WinVaultKeyring")
    assert type(credential.os_keychain(platform="win32", env={})).__name__ == "WinVaultKeyring"


def test_the_token_is_revealed_only_for_canvas_reads_and_the_keychain_write() -> None:
    src = Path(__file__).resolve().parents[3] / "src"
    callers = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py") if ".reveal_token()" in p.read_text(encoding="utf-8"))
    assert callers == ["syllva/canvas/credential.py", "syllva/ui/canvas.py"]
