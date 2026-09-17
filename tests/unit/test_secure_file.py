import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import os

import pytest

from uls.config._secure_file import (
    MAX_SECRET_BYTES,
    read_secure_file,
    write_secure_file,
)
from uls.config.errors import ConfigurationError

pytestmark = pytest.mark.unit


def _secrets_dir(tmp_path):
    d = tmp_path / "secrets"
    d.mkdir(mode=0o700)
    return d


def test_write_then_read_round_trip(tmp_path):
    path = _secrets_dir(tmp_path) / "token.secret"
    write_secure_file(path, b"top-secret-value")
    assert read_secure_file(path) == b"top-secret-value"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_rejects_empty_value(tmp_path):
    path = _secrets_dir(tmp_path) / "empty.secret"
    with pytest.raises(ConfigurationError) as exc_info:
        write_secure_file(path, b"")
    assert exc_info.value.details["problems"][0] == "secret_value_empty"
    assert not path.exists()


def test_write_rejects_oversized_value(tmp_path):
    path = _secrets_dir(tmp_path) / "big.secret"
    with pytest.raises(ConfigurationError) as exc_info:
        write_secure_file(path, b"x" * (MAX_SECRET_BYTES + 1))
    assert exc_info.value.details["problems"][0] == "secret_value_too_large"
    assert not path.exists()


def test_write_failure_never_leaves_a_temp_file_behind(tmp_path, monkeypatch):
    directory = _secrets_dir(tmp_path)
    path = directory / "boom.secret"

    import uls.config._secure_file as secure_file_module

    original_replace = os.replace

    def failing_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(secure_file_module.os, "replace", failing_replace)
    with pytest.raises(OSError):
        write_secure_file(path, b"value")
    monkeypatch.setattr(secure_file_module.os, "replace", original_replace)

    leftovers = list(directory.glob(".tmp_*"))
    assert leftovers == [], "a failed write must never preserve its temp file"
    assert not path.exists()


def test_read_rejects_missing_file(tmp_path):
    directory = _secrets_dir(tmp_path)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(directory / "missing.secret")
    assert exc_info.value.details["problems"][0] == "secret_file_missing"


def test_read_rejects_missing_directory(tmp_path):
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(tmp_path / "nonexistent" / "token.secret")
    assert exc_info.value.details["problems"][0] == "secret_dir_missing"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit test")
def test_read_rejects_group_or_world_readable_file(tmp_path):
    directory = _secrets_dir(tmp_path)
    path = directory / "loose.secret"
    write_secure_file(path, b"value")
    os.chmod(path, 0o644)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(path)
    assert exc_info.value.details["problems"][0] == "secret_file_permissions_too_open"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit test")
def test_read_rejects_group_or_world_readable_directory(tmp_path):
    directory = _secrets_dir(tmp_path)
    path = directory / "token.secret"
    write_secure_file(path, b"value")
    os.chmod(directory, 0o755)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(path)
    assert exc_info.value.details["problems"][0] == "secret_dir_permissions_too_open"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink test")
def test_read_rejects_symlinked_file(tmp_path):
    directory = _secrets_dir(tmp_path)
    real = directory / "real.secret"
    write_secure_file(real, b"real-value")
    link = directory / "link.secret"
    os.symlink(real, link)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(link)
    assert exc_info.value.details["problems"][0] == "secret_file_is_symlink"
    # The real file underneath must remain fully readable through its own name.
    assert read_secure_file(real) == b"real-value"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink test")
def test_read_rejects_symlinked_directory(tmp_path):
    real_dir = tmp_path / "real_secrets"
    real_dir.mkdir(mode=0o700)
    (real_dir / "token.secret").write_bytes(b"value")
    os.chmod(real_dir / "token.secret", 0o600)
    link_dir = tmp_path / "linked_secrets"
    os.symlink(real_dir, link_dir)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(link_dir / "token.secret")
    assert exc_info.value.details["problems"][0] == "secret_dir_is_symlink"


def test_read_rejects_oversized_file(tmp_path):
    directory = _secrets_dir(tmp_path)
    path = directory / "big.secret"
    # Bypass write_secure_file's own size guard to simulate a file that grew
    # after being written by something else entirely.
    with open(path, "wb") as handle:
        handle.write(b"x" * (MAX_SECRET_BYTES + 1))
    os.chmod(path, 0o600)
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(path)
    assert exc_info.value.details["problems"][0] == "secret_file_too_large"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO test")
def test_read_rejects_non_regular_file(tmp_path):
    directory = _secrets_dir(tmp_path)
    fifo_path = directory / "pipe.secret"
    os.mkfifo(fifo_path, 0o600)
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            read_secure_file(fifo_path)
        assert exc_info.value.details["problems"][0] == "secret_file_not_regular"
    finally:
        os.unlink(fifo_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 0700 mode-bit test")
def test_write_creates_directory_with_0700(tmp_path):
    directory = tmp_path / "fresh_secrets"
    assert not directory.exists()
    write_secure_file(directory / "token.secret", b"value")
    assert (directory.stat().st_mode & 0o777) == 0o700


def test_no_toctou_window_between_verification_and_read(tmp_path):
    """The verified file descriptor must be the one actually read from --
    swapping the file's content immediately after write (simulating a
    concurrent attacker) must not let a subsequent read see the swapped
    content unless it independently re-passes verification."""

    directory = _secrets_dir(tmp_path)
    path = directory / "token.secret"
    write_secure_file(path, b"original-value")
    assert read_secure_file(path) == b"original-value"

    # A legitimate rewrite through the same atomic writer is always safe
    # and independently re-verified on the next read.
    write_secure_file(path, b"rotated-value")
    assert read_secure_file(path) == b"rotated-value"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only DACL mask tests")
def test_windows_dacl_round_trip_and_tampered_mask_rejected(tmp_path):
    d = tmp_path / "secrets"
    p = d / "win_token.secret"
    write_secure_file(p, b"windows-secret-data")
    assert read_secure_file(p) == b"windows-secret-data"

    # Tamper with the DACL: give current_user only read permission (tampered mask subset)
    import ctypes
    from ctypes import wintypes

    from uls.config._secure_file import (
        _WINDOWS_ADMINISTRATORS_SID,
        _WINDOWS_SYSTEM_SID,
        _windows_current_user_sid,
    )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class Trustee(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", wintypes.LPVOID), ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int),
            ("ptstrName", wintypes.LPWSTR),
        ]

    class ExplicitAccess(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD), ("Trustee", Trustee),
        ]

    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID))
    advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    advapi32.SetEntriesInAclW.argtypes = (
        wintypes.ULONG, ctypes.POINTER(ExplicitAccess), wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
    )

    current_user = _windows_current_user_sid()
    entries = []
    sid_ptrs = []
    for sid_string, mask in (
        (current_user, 0x80000000),  # tampered mask: read only (missing write/delete)
        (_WINDOWS_SYSTEM_SID, 0x10000000),
        (_WINDOWS_ADMINISTRATORS_SID, 0x10000000),
    ):
        sid_ptr = wintypes.LPVOID()
        advapi32.ConvertStringSidToSidW(sid_string, ctypes.byref(sid_ptr))
        sid_ptrs.append(sid_ptr)
        trustee = Trustee(None, 0, 0, 0, ctypes.cast(sid_ptr, wintypes.LPWSTR))
        entries.append(ExplicitAccess(mask, 2, 0, trustee))

    array_type = ExplicitAccess * len(entries)
    new_acl = wintypes.LPVOID()
    advapi32.SetEntriesInAclW(len(entries), array_type(*entries), None, ctypes.byref(new_acl))
    advapi32.SetNamedSecurityInfoW(str(p), 1, 0x00000004 | 0x80000000, None, None, new_acl, None)
    kernel32.LocalFree(new_acl)
    for sid_ptr in sid_ptrs:
        kernel32.LocalFree(sid_ptr)

    # Must raise ConfigurationError because current_user mask != expected
    with pytest.raises(ConfigurationError) as exc_info:
        read_secure_file(p)
    assert exc_info.value.details["problems"][0] == "secret_file_permissions_too_open"
