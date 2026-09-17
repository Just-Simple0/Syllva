"""TOCTOU-safe secret file read/write boundary.

Shared by CredentialResolver's 'source: file' (rev5 PLAN GO pending, see
docs/plans/credential-secret-file-launcher.md) and by the post-resolution
validator applied to GOOGLE_WORKER_CREDENTIALS_FILE/
GOOGLE_MCP_CREDENTIALS_FILE (same plan, section 2.4/8.2).

Every function here fails closed: an unverifiable directory/file identity,
ownership, permission, size, or encoding is treated as untrusted, never as
an implicit pass. No raised error ever carries a secret value.

Read contract (section 2.3/8.2 of the plan):
1. Open the secrets directory itself no-follow and verify its identity
   (regular directory, owner, mode/DACL) on that open handle.
2. Open the target file no-follow, relative to that verified directory
   handle where the platform supports it (POSIX dir_fd), and verify
   regular-file/owner/mode-or-DACL/size on that SAME open file
   descriptor/handle.
3. Read to EOF from that SAME descriptor/handle and return the bytes. The
   caller never reopens the path by name.

Write contract (section 2.2):
Create the temp file with O_CREAT | O_EXCL | O_WRONLY at 0o600 from
the moment of creation (POSIX) or with the canonical protected DACL already
applied before any secret byte is written (Windows), write, fsync,
os.replace onto the target, fsync the parent directory, and unlink the
temp file on any failure path (never preserved, unlike
knu_lms_sync.py's _atomic_json_write which is only used for
secret-free documents).
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from typing import Any, Final

from uls.config.errors import ConfigurationError

IS_WINDOWS: Final[bool] = os.name == "nt"
MAX_SECRET_BYTES: Final[int] = 4096

_DIRECTORY_FLAG: Final[int] = getattr(os, "O_DIRECTORY", 0)
_NOFOLLOW_FLAG: Final[int] = getattr(os, "O_NOFOLLOW", 0)

# Windows canonical trustee SIDs (section 8.5/8.7 of the plan). The
# "current user" trustee is always TokenUser (the real signed-in/service
# account), never TokenOwner (which can itself be the Administrators group
# SID under an elevated token -- see scripts/_lms_platform.py's
# _windows_default_owner_sid docstring for the same distinction applied to
# ownership rather than ACL trustees).
_WINDOWS_SYSTEM_SID: Final[str] = "S-1-5-18"
_WINDOWS_ADMINISTRATORS_SID: Final[str] = "S-1-5-32-544"
_WINDOWS_USER_ACCESS_MASK: Final[int] = 0x80000000 | 0x40000000 | 0x00010000
_WINDOWS_ADMIN_ACCESS_MASK: Final[int] = 0x10000000


def secrets_directory() -> Path:
    """Return the per-OS protected secrets directory (not created here).

    Only darwin/win32 are supported, matching the existing OS-native
    keyring platform gate in _keyring_backend.py: this project's CI
    matrix (.github/workflows/ci.yml) only exercises macOS and Windows,
    and generic POSIX permission bits provide no equivalent-strength
    guarantee on every possible filesystem/OS combination.
    """

    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ConfigurationError("secret_dir_missing")
        return Path(base) / "Syllva" / "secrets"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Syllva" / "secrets"
    raise ConfigurationError("secret_platform_unsupported")


def secret_file_path(filename: str) -> Path:
    return secrets_directory() / filename


# ---------------------------------------------------------------------------
# Read boundary
# ---------------------------------------------------------------------------


def read_secure_file(path: Path, *, max_bytes: int = MAX_SECRET_BYTES) -> bytes:
    """Read path through the TOCTOU-safe boundary described above."""

    directory = path.parent
    if IS_WINDOWS:
        return _windows_read_secure_file(directory, path.name, max_bytes)
    return _posix_read_secure_file(directory, path.name, max_bytes)


def _posix_read_secure_file(directory: Path, name: str, max_bytes: int) -> bytes:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY | _DIRECTORY_FLAG | _NOFOLLOW_FLAG)
    except FileNotFoundError as exc:
        raise ConfigurationError("secret_dir_missing") from exc
    except OSError as exc:
        # macOS/BSD reports a symlink combined with O_DIRECTORY as ENOTDIR
        # (it refuses to even look past the symlink), while Linux reports
        # the more specific ELOOP; both mean "not a plain directory we can
        # trust", so both are treated as the symlink-rejection case.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ConfigurationError("secret_dir_is_symlink") from exc
        raise ConfigurationError("secret_dir_missing") from exc
    try:
        _posix_verify_directory(dir_fd)
        try:
            # O_NONBLOCK prevents an indefinite hang if the target turns out
            # to be a FIFO with no writer (rejected as non-regular right
            # after this open, via the same fd); it has no effect on a
            # regular file's subsequent blocking-mode reads.
            file_fd = os.open(name, os.O_RDONLY | _NOFOLLOW_FLAG | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError as exc:
            raise ConfigurationError("secret_file_missing") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ConfigurationError("secret_file_is_symlink") from exc
            raise ConfigurationError("secret_file_missing") from exc
        try:
            _posix_verify_file(file_fd, max_bytes)
            return _read_all_fd(file_fd, max_bytes)
        finally:
            os.close(file_fd)
    finally:
        os.close(dir_fd)


def _posix_verify_directory(dir_fd: int) -> None:
    info = os.fstat(dir_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise ConfigurationError("secret_dir_is_symlink")
    if info.st_uid != os.getuid():
        raise ConfigurationError("secret_dir_owner_mismatch")
    if info.st_mode & 0o077:
        raise ConfigurationError("secret_dir_permissions_too_open")


def _posix_verify_file(file_fd: int, max_bytes: int) -> None:
    info = os.fstat(file_fd)
    if not stat.S_ISREG(info.st_mode):
        raise ConfigurationError("secret_file_not_regular")
    if info.st_uid != os.getuid():
        raise ConfigurationError("secret_file_owner_mismatch")
    if info.st_mode & 0o077:
        raise ConfigurationError("secret_file_permissions_too_open")
    if info.st_size > max_bytes:
        raise ConfigurationError("secret_file_too_large")


def _read_all_fd(fd: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, max_bytes + 1 - total)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ConfigurationError("secret_file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Write boundary
# ---------------------------------------------------------------------------


def write_secure_file(path: Path, data: bytes) -> None:
    """Atomically write data to path (see write contract above)."""

    if len(data) == 0:
        raise ConfigurationError("secret_value_empty")
    if len(data) > MAX_SECRET_BYTES:
        raise ConfigurationError("secret_value_too_large")
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        import ctypes
        dir_handle = _windows_open_reparse_check(str(directory))
        try:
            is_reparse, _ = _windows_is_reparse_point(dir_handle)
            if is_reparse:
                raise ConfigurationError("secret_dir_is_symlink")
            _windows_harden_directory(directory)
            _windows_verify_security_handle(dir_handle, expect_directory=True)
        finally:
            ctypes.WinDLL("kernel32").CloseHandle(dir_handle)
    else:
        os.chmod(directory, 0o700)
        dir_fd = os.open(str(directory), os.O_RDONLY | _DIRECTORY_FLAG | _NOFOLLOW_FLAG)
        try:
            _posix_verify_directory(dir_fd)
        finally:
            os.close(dir_fd)
    tmp_path = directory / (".tmp_" + path.name + "_" + str(os.getpid()))
    if IS_WINDOWS:
        _windows_atomic_write(tmp_path, path, data)
        return
    try:
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW_FLAG, 0o600)
    except FileExistsError as exc:
        raise ConfigurationError("secret_write_temp_collision") from exc
    try:
        try:
            _posix_write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
        _posix_fsync_directory(directory)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _posix_fsync_directory(directory: Path) -> None:
    dir_fd = os.open(str(directory), os.O_RDONLY | _DIRECTORY_FLAG | _NOFOLLOW_FLAG)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _posix_write_all(fd: int, data: bytes) -> None:
    total = 0
    while total < len(data):
        written = os.write(fd, data[total:])
        if written == 0:
            raise OSError("write returned 0 bytes")
        total += written


# ---------------------------------------------------------------------------
# Windows support
#
# This section mirrors the ctypes patterns already reviewed and merged in
# scripts/_lms_platform.py (open_nofollow via CreateFileW +
# FILE_FLAG_OPEN_REPARSE_POINT, TokenOwner retrieval). It is reimplemented
# here rather than imported, matching the project's existing
# scripts-vs-src.uls boundary convention (see _keyring_backend.py's module
# docstring). This module additionally verifies/sets an explicit DACL,
# which scripts/_lms_platform.py deliberately does not implement -- per
# that module's own docstring, Windows ACL/DACL hardening is separate,
# larger follow-up work; this module IS that follow-up for the secrets
# directory/files specifically. As with every other Windows-specific code
# path in this project, this is exercised by the windows-latest CI matrix
# job, not by local development on macOS.
# ---------------------------------------------------------------------------


def _windows_open_reparse_check(path: str) -> int:
    """Open path (file or directory) no-follow; return a Win32 handle.

    Raises ConfigurationError(...is_symlink) if the open object is a
    reparse point (symlink/junction), and FileNotFoundError if it does not
    exist. Caller must CloseHandle the returned value.
    """

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000  # required to open a directory
    file_flag_open_reparse_point = 0x00200000
    invalid_handle_value = wintypes.HANDLE(-1).value
    error_file_not_found = 2
    error_path_not_found = 3

    handle = kernel32.CreateFileW(
        path, generic_read, file_share_read | file_share_write, None,
        open_existing, file_flag_backup_semantics | file_flag_open_reparse_point, None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in (error_file_not_found, error_path_not_found):
            raise FileNotFoundError(errno.ENOENT, "file not found", path)
        raise OSError(error, "CreateFileW failed", path)
    return handle


def _windows_is_reparse_point(handle: int) -> tuple[bool, Any]:
    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime), ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
    file_attribute_reparse_point = 0x400
    info = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.pointer(info)):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    return bool(info.dwFileAttributes & file_attribute_reparse_point), info


def _windows_read_secure_file(directory: Path, name: str, max_bytes: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    try:
        dir_handle = _windows_open_reparse_check(str(directory))
    except FileNotFoundError as exc:
        raise ConfigurationError("secret_dir_missing") from exc
    try:
        is_reparse, _ = _windows_is_reparse_point(dir_handle)
        if is_reparse:
            raise ConfigurationError("secret_dir_is_symlink")
        _windows_verify_security_handle(dir_handle, expect_directory=True)
    finally:
        kernel32.CloseHandle(dir_handle)

    target = directory / name
    try:
        file_handle = _windows_open_reparse_check(str(target))
    except FileNotFoundError as exc:
        raise ConfigurationError("secret_file_missing") from exc
    try:
        is_reparse, info = _windows_is_reparse_point(file_handle)
        if is_reparse:
            raise ConfigurationError("secret_file_is_symlink")
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        if size > max_bytes:
            raise ConfigurationError("secret_file_too_large")
        _windows_verify_security_handle(file_handle, expect_directory=False)

        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = (
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        )
        buffer = ctypes.create_string_buffer(max_bytes + 1)
        read_count = wintypes.DWORD(0)
        if not kernel32.ReadFile(file_handle, buffer, max_bytes + 1, ctypes.byref(read_count), None):
            raise OSError(ctypes.get_last_error(), "ReadFile failed")
        if read_count.value > max_bytes:
            raise ConfigurationError("secret_file_too_large")
        return buffer.raw[: read_count.value]
    finally:
        kernel32.CloseHandle(file_handle)


def _windows_current_user_sid() -> str:
    """Return this process token's TokenUser SID (not TokenOwner -- see
    module docstring)."""

    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(ctypes.c_wchar_p))
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)

    token_query = 0x0008
    token_user = 1
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise OSError("GetTokenInformation size query failed")
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, token_user, buf, size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        # TOKEN_USER is { SID_AND_ATTRIBUTES User } == { PSID Sid; DWORD Attributes }.
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        string_sid = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_sid)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
        try:
            return string_sid.value or ""
        finally:
            kernel32.LocalFree(string_sid)
    finally:
        kernel32.CloseHandle(token)


def _make_acl_ctypes() -> Any:
    import ctypes

    class ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_ushort), ("AceCount", ctypes.c_ushort),
            ("Sbz2", ctypes.c_ushort),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    return ACL, AceHeader


def _windows_default_owner_sid() -> str:
    """Return this process token default-owner SID (TokenOwner, not TokenUser)."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token_query = 0x0008
    token_owner = 4

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_owner, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise OSError("GetTokenInformation size query failed")
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, token_owner, buf, size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        string_sid = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_sid)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
        try:
            return string_sid.value or ""
        finally:
            kernel32.LocalFree(string_sid)
    finally:
        kernel32.CloseHandle(token)


def _windows_verify_security_handle(handle: int, *, expect_directory: bool) -> None:
    """Verify owner SID and DACL on the open Win32 handle directly (TOCTOU-safe)."""
    import ctypes
    from ctypes import wintypes

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
    advapi32.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(ctypes.c_wchar_p))
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)

    se_file_object = 1
    owner_sec_info = 0x00000001
    dacl_security_information = 0x00000004
    se_dacl_protected = 0x1000
    access_allowed_ace_type = 0x0

    perm_err = "secret_dir_permissions_too_open" if expect_directory else "secret_file_permissions_too_open"
    owner_err = "secret_dir_owner_mismatch" if expect_directory else "secret_file_owner_mismatch"

    owner_ptr = wintypes.LPVOID()
    dacl_ptr = wintypes.LPVOID()
    security_descriptor = wintypes.LPVOID()
    result = advapi32.GetSecurityInfo(
        handle, se_file_object, owner_sec_info | dacl_security_information,
        ctypes.byref(owner_ptr), None, ctypes.byref(dacl_ptr), None, ctypes.byref(security_descriptor),
    )
    if result != 0:
        raise OSError(result, "GetSecurityInfo failed")
    try:
        if not owner_ptr:
            raise ConfigurationError(owner_err)
        string_owner = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(owner_ptr, ctypes.byref(string_owner)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW owner failed")
        try:
            actual_owner = string_owner.value or ""
        finally:
            kernel32.LocalFree(string_owner)
        if actual_owner != _windows_default_owner_sid():
            raise ConfigurationError(owner_err)

        if not dacl_ptr:
            raise ConfigurationError(perm_err)
        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        if not advapi32.GetSecurityDescriptorControl(security_descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise OSError(ctypes.get_last_error(), "GetSecurityDescriptorControl failed")
        if not bool(control.value & se_dacl_protected):
            raise ConfigurationError(perm_err)

        acl_type, header_type = _make_acl_ctypes()
        acl = ctypes.cast(dacl_ptr, ctypes.POINTER(acl_type)).contents
        if acl.AceCount != 3:
            raise ConfigurationError(perm_err)
        sids = []
        for index in range(acl.AceCount):
            ace_ptr = wintypes.LPVOID()
            if not advapi32.GetAce(dacl_ptr, index, ctypes.byref(ace_ptr)):
                raise OSError(ctypes.get_last_error(), "GetAce failed")
            header = ctypes.cast(ace_ptr, ctypes.POINTER(header_type)).contents
            if header.AceType != access_allowed_ace_type or header.AceFlags != 0:
                raise ConfigurationError(perm_err)
            raw_addr = ctypes.cast(ace_ptr, ctypes.c_void_p).value
            if raw_addr is None:
                raise OSError("Invalid ACE memory address")
            mask_addr = raw_addr + ctypes.sizeof(header_type)
            mask = ctypes.cast(mask_addr, ctypes.POINTER(wintypes.DWORD))[0]
            sid_addr = mask_addr + 4
            string_sid = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid_addr), ctypes.byref(string_sid)):
                raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
            try:
                sids.append((string_sid.value or "", mask))
            finally:
                kernel32.LocalFree(string_sid)

        current_user = _windows_current_user_sid()
        expected_masks = {
            current_user: _WINDOWS_USER_ACCESS_MASK,
            _WINDOWS_SYSTEM_SID: _WINDOWS_ADMIN_ACCESS_MASK,
            _WINDOWS_ADMINISTRATORS_SID: _WINDOWS_ADMIN_ACCESS_MASK,
        }
        sid_set = {s[0] for s in sids}
        allowed_sids = set(expected_masks)
        if len(sids) != 3 or sid_set != allowed_sids:
            raise ConfigurationError(perm_err)
        for sid_val, ace_mask in sids:
            expected = expected_masks[sid_val]
            if ace_mask != expected:
                raise ConfigurationError(perm_err)
    finally:
        kernel32.LocalFree(security_descriptor)



def _windows_harden_directory(directory: Path) -> None:
    _windows_set_canonical_dacl(directory)


def _windows_set_canonical_dacl(path: Path) -> None:
    """Replace path's DACL with exactly the three canonical explicit ALLOW
    ACEs (current user, SYSTEM, Administrators), protected (no
    inheritance). Uses SetEntriesInAclW + SetNamedSecurityInfoW."""

    import ctypes
    from ctypes import wintypes

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
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)

    trustee_form_sid = 0
    trustee_type_unknown = 0
    set_access = 2  # SET_ACCESS: replace existing entries for this trustee
    no_inheritance = 0
    generic_all = 0x10000000
    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_right = 0x00010000
    se_file_object = 1
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000

    current_user = _windows_current_user_sid()
    entries = []
    sid_ptrs = []
    for sid_string, mask in (
        (current_user, generic_read | generic_write | delete_right),
        (_WINDOWS_SYSTEM_SID, generic_all),
        (_WINDOWS_ADMINISTRATORS_SID, generic_all),
    ):
        sid_ptr = wintypes.LPVOID()
        if not advapi32.ConvertStringSidToSidW(sid_string, ctypes.byref(sid_ptr)):
            raise OSError(ctypes.get_last_error(), "ConvertStringSidToSidW failed")
        sid_ptrs.append(sid_ptr)
        trustee = Trustee(None, 0, trustee_form_sid, trustee_type_unknown,
                          ctypes.cast(sid_ptr, wintypes.LPWSTR))
        entries.append(ExplicitAccess(mask, set_access, no_inheritance, trustee))

    array_type = ExplicitAccess * len(entries)
    new_acl = wintypes.LPVOID()
    try:
        entries_array = array_type(*entries)
        result = advapi32.SetEntriesInAclW(len(entries), entries_array, None, ctypes.byref(new_acl))
        if result != 0:
            raise OSError(result, "SetEntriesInAclW failed")
        try:
            result = advapi32.SetNamedSecurityInfoW(
                str(path), se_file_object,
                dacl_security_information | protected_dacl_security_information,
                None, None, new_acl, None,
            )
            if result != 0:
                raise OSError(result, "SetNamedSecurityInfoW failed", str(path))
        finally:
            kernel32.LocalFree(new_acl)
    finally:
        for sid_ptr in sid_ptrs:
            kernel32.LocalFree(sid_ptr)


def _windows_atomic_write(tmp_path: Path, target: Path, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = (
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    )
    kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.MoveFileExW.restype = wintypes.BOOL
    kernel32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)

    generic_write = 0x40000000
    create_new = 1
    file_attribute_normal = 0x80
    invalid_handle_value = wintypes.HANDLE(-1).value
    move_file_replace_existing = 0x1
    move_file_write_through = 0x8

    handle = kernel32.CreateFileW(str(tmp_path), generic_write, 0, None, create_new, file_attribute_normal, None)
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(tmp_path))
    try:
        try:
            _windows_set_canonical_dacl(tmp_path)
            _windows_verify_security_handle(handle, expect_directory=False)
            written = wintypes.DWORD(0)
            if not kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
                raise OSError(ctypes.get_last_error(), "WriteFile failed")
            if written.value != len(data):
                raise OSError("WriteFile short write")
            kernel32.FlushFileBuffers(handle)
        finally:
            kernel32.CloseHandle(handle)
        if not kernel32.MoveFileExW(str(tmp_path), str(target), move_file_replace_existing | move_file_write_through):
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = [
    "IS_WINDOWS",
    "MAX_SECRET_BYTES",
    "read_secure_file",
    "secret_file_path",
    "secrets_directory",
    "write_secure_file",
]
