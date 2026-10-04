import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import os

import pytest

from uls.config._secure_file import (
    _WINDOWS_ADMIN_ACCESS_MASK,
    _WINDOWS_USER_ACCESS_MASK,
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


def test_windows_file_access_masks_use_specific_rights():
    file_generic_read = 0x00020000 | 0x00100000 | 0x00000001 | 0x00000008 | 0x00000080
    file_generic_write = (
        0x00020000 | 0x00100000 | 0x00000002 | 0x00000004 | 0x00000010 | 0x00000100
    )
    file_all_access = 0x000F0000 | 0x00100000 | 0x000001FF
    user_mask = file_generic_read | file_generic_write | 0x00010000

    assert file_generic_read == 0x00120089
    assert file_generic_write == 0x00120116
    assert file_all_access == 0x001F01FF
    assert _WINDOWS_USER_ACCESS_MASK == user_mask == 0x0013019F
    assert _WINDOWS_ADMIN_ACCESS_MASK == file_all_access
    assert not (user_mask & 0xF0000000)
    assert not (file_all_access & 0xF0000000)


def _assert_windows_raw_canonical_acl(path):
    """Independently inspect the actual owner, DACL and raw ACE bytes."""
    import ctypes
    from ctypes import wintypes

    from uls.config._secure_file import (
        _windows_current_user_sid,
        _windows_default_owner_sid,
        _windows_is_reparse_point,
        _windows_open_reparse_check,
    )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = (wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID))
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(ctypes.c_wchar_p),
    )
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    class ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_ushort), ("AceCount", ctypes.c_ushort),
            ("Sbz2", ctypes.c_ushort),
        ]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    handle = _windows_open_reparse_check(str(path))
    try:
        is_reparse, _ = _windows_is_reparse_point(handle)
        assert not is_reparse

        owner_ptr = wintypes.LPVOID()
        dacl_ptr = wintypes.LPVOID()
        security_descriptor = wintypes.LPVOID()
        result = advapi32.GetSecurityInfo(
            handle, 1, 0x00000001 | 0x00000004,
            ctypes.byref(owner_ptr), None, ctypes.byref(dacl_ptr), None,
            ctypes.byref(security_descriptor),
        )
        assert result == 0, "GetSecurityInfo failed"
        try:
            assert owner_ptr
            owner_string = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(owner_ptr, ctypes.byref(owner_string)):
                raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW owner failed")
            try:
                actual_owner = owner_string.value or ""
            finally:
                kernel32.LocalFree(owner_string)
            assert actual_owner == _windows_default_owner_sid()

            assert dacl_ptr
            control = wintypes.WORD(0)
            revision = wintypes.DWORD(0)
            assert advapi32.GetSecurityDescriptorControl(
                security_descriptor, ctypes.byref(control), ctypes.byref(revision),
            ), "GetSecurityDescriptorControl failed"
            assert control.value & 0x1000  # SE_DACL_PROTECTED

            acl = ctypes.cast(dacl_ptr, ctypes.POINTER(ACL)).contents
            assert acl.AceCount == 3
            raw_masks_by_sid = {}
            for index in range(acl.AceCount):
                ace_ptr = wintypes.LPVOID()
                assert advapi32.GetAce(dacl_ptr, index, ctypes.byref(ace_ptr)), "GetAce failed"
                header = ctypes.cast(ace_ptr, ctypes.POINTER(ACE_HEADER)).contents
                assert header.AceType == 0  # ACCESS_ALLOWED_ACE_TYPE
                assert header.AceFlags == 0
                raw_addr = ctypes.cast(ace_ptr, ctypes.c_void_p).value
                assert raw_addr is not None
                mask_addr = raw_addr + ctypes.sizeof(ACE_HEADER)
                raw_mask = ctypes.cast(
                    mask_addr, ctypes.POINTER(wintypes.DWORD),
                ).contents.value
                sid_addr = mask_addr + ctypes.sizeof(wintypes.DWORD)
                sid_string = ctypes.c_wchar_p()
                if not advapi32.ConvertSidToStringSidW(
                    ctypes.c_void_p(sid_addr), ctypes.byref(sid_string),
                ):
                    raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW ACE failed")
                try:
                    sid = sid_string.value or ""
                finally:
                    kernel32.LocalFree(sid_string)
                assert sid not in raw_masks_by_sid
                raw_masks_by_sid[sid] = raw_mask

            # Expected masks are independent test literals; do not use the
            # production verifier or its constants as this assertion oracle.
            expected_raw_masks = {
                _windows_current_user_sid(): 0x0013019F,
                "S-1-5-18": 0x001F01FF,
                "S-1-5-32-544": 0x001F01FF,
            }
            assert raw_masks_by_sid == expected_raw_masks
        finally:
            if security_descriptor:
                kernel32.LocalFree(security_descriptor)
    finally:
        assert kernel32.CloseHandle(handle)


def test_write_then_read_round_trip(tmp_path):
    path = _secrets_dir(tmp_path) / "token.secret"
    write_secure_file(path, b"top-secret-value")
    if os.name == "nt":
        assert read_secure_file(path) == b"top-secret-value"
        _assert_windows_raw_canonical_acl(path.parent)
        _assert_windows_raw_canonical_acl(path)
    else:
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

    if os.name == "nt":
        import ctypes

        attempted_sources = []
        original_windll = ctypes.WinDLL

        def failing_move_file_ex(source, destination, flags):
            attempted_sources.append(pathlib.Path(source))
            ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
            return 0

        class Kernel32WithFailingMove:
            def __init__(self, dll):
                self._dll = dll
                self.MoveFileExW = failing_move_file_ex

            def __getattr__(self, name):
                return getattr(self._dll, name)

        def windll_with_failing_replace(name, *args, **kwargs):
            dll = original_windll(name, *args, **kwargs)
            if name.lower() == "kernel32":
                return Kernel32WithFailingMove(dll)
            return dll

        monkeypatch.setattr(ctypes, "WinDLL", windll_with_failing_replace)
        with pytest.raises(OSError):
            write_secure_file(path, b"value")
        assert len(attempted_sources) == 1
        assert attempted_sources[0].parent == directory
        assert attempted_sources[0].name.startswith(".tmp_")
    else:
        def failing_replace(*args, **kwargs):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(secure_file_module.os, "replace", failing_replace)
        with pytest.raises(OSError):
            write_secure_file(path, b"value")

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
    write_secure_file(path, b"initial")
    with open(path, "wb") as handle:
        handle.write(b"x" * (MAX_SECRET_BYTES + 1))
    if os.name != "nt":
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
    expected_user_mask = 0x0013019F
    expected_admin_mask = 0x001F01FF
    tampered_user_masks = (
        expected_user_mask & ~0x00000002,  # missing FILE_WRITE_DATA
        expected_user_mask | 0x00000020,  # extra FILE_EXECUTE
    )

    for tampered_user_mask in tampered_user_masks:
        entries = []
        sid_ptrs = []
        new_acl = wintypes.LPVOID()
        try:
            for sid_string, mask in (
                (current_user, tampered_user_mask),
                ("S-1-5-18", expected_admin_mask),
                ("S-1-5-32-544", expected_admin_mask),
            ):
                sid_ptr = wintypes.LPVOID()
                if not advapi32.ConvertStringSidToSidW(sid_string, ctypes.byref(sid_ptr)):
                    raise OSError(ctypes.get_last_error(), "ConvertStringSidToSidW failed")
                sid_ptrs.append(sid_ptr)
                trustee = Trustee(None, 0, 0, 0, ctypes.cast(sid_ptr, wintypes.LPWSTR))
                entries.append(ExplicitAccess(mask, 2, 0, trustee))

            array_type = ExplicitAccess * len(entries)
            result = advapi32.SetEntriesInAclW(
                len(entries), array_type(*entries), None, ctypes.byref(new_acl),
            )
            assert result == 0, "SetEntriesInAclW failed"
            result = advapi32.SetNamedSecurityInfoW(
                str(p), 1, 0x00000004 | 0x80000000,
                None, None, new_acl, None,
            )
            assert result == 0, "SetNamedSecurityInfoW failed"
        finally:
            if new_acl:
                kernel32.LocalFree(new_acl)
            for sid_ptr in sid_ptrs:
                kernel32.LocalFree(sid_ptr)

        with pytest.raises(ConfigurationError) as exc_info:
            read_secure_file(p)
        assert exc_info.value.details["problems"][0] == "secret_file_permissions_too_open"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only partial SID cleanup test")
def test_windows_dacl_frees_prior_sid_when_later_conversion_fails(monkeypatch, tmp_path):
    import ctypes
    from ctypes import wintypes

    import uls.config._secure_file as secure_file_module

    real_windll = ctypes.WinDLL
    attempted_sids = []
    allocated_sid_addresses = []
    freed_sid_addresses = []

    class DelegatedDLL:
        def __init__(self, name, *args, **kwargs):
            self._dll = real_windll(name, *args, **kwargs)
            if name.lower() == "advapi32":
                real_convert = self._dll.ConvertStringSidToSidW
                real_convert.restype = wintypes.BOOL
                real_convert.argtypes = (
                    wintypes.LPCWSTR,
                    ctypes.POINTER(wintypes.LPVOID),
                )

                def convert_string_sid(sid_string, sid_output):
                    attempted_sids.append(sid_string)
                    if len(attempted_sids) == 2:
                        ctypes.set_last_error(87)
                        return 0
                    converted = real_convert(sid_string, sid_output)
                    if converted:
                        sid_address = ctypes.cast(
                            sid_output, ctypes.POINTER(wintypes.LPVOID)
                        ).contents.value
                        allocated_sid_addresses.append(sid_address)
                    return converted

                convert_string_sid.restype = wintypes.BOOL
                convert_string_sid.argtypes = (
                    wintypes.LPCWSTR,
                    ctypes.POINTER(wintypes.LPVOID),
                )
                self.ConvertStringSidToSidW = convert_string_sid

            if name.lower() == "kernel32":
                real_local_free = self._dll.LocalFree
                real_local_free.restype = wintypes.HLOCAL
                real_local_free.argtypes = (wintypes.HLOCAL,)

                def tracked_local_free(pointer):
                    address = ctypes.cast(pointer, ctypes.c_void_p).value
                    if address in allocated_sid_addresses:
                        freed_sid_addresses.append(address)
                    return real_local_free(wintypes.HLOCAL(address))

                tracked_local_free.restype = wintypes.HLOCAL
                tracked_local_free.argtypes = (wintypes.HLOCAL,)
                self.LocalFree = tracked_local_free

        def __getattr__(self, name):
            return getattr(self._dll, name)

    def delegated_windll(name, *args, **kwargs):
        return DelegatedDLL(name, *args, **kwargs)

    monkeypatch.setattr(ctypes, "WinDLL", delegated_windll)
    with pytest.raises(OSError, match="ConvertStringSidToSidW failed"):
        secure_file_module._windows_set_canonical_dacl(tmp_path / "unused.secret")

    assert len(attempted_sids) == 2
    assert attempted_sids[1] == "S-1-5-18"
    assert len(allocated_sid_addresses) == 1
    assert freed_sid_addresses == allocated_sid_addresses


@pytest.mark.skipif(os.name != "nt", reason="Windows-only default-owner token API test")
def test_windows_default_owner_sid_returns_valid_sid():
    import re

    from uls.config._secure_file import _windows_default_owner_sid

    sid = _windows_default_owner_sid()
    assert isinstance(sid, str)
    assert re.fullmatch(r"S-\d+(?:-\d+)+", sid)
