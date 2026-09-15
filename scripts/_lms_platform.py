"""Cross-platform filesystem/lock primitives shared by the KNU LMS sidecar
scripts (scripts/knu_lms_sync.py, scripts/knu_lms_apply_lock.py).

POSIX (macOS/Linux) keeps the exact prior behavior: UID comparison via
os.getuid(), os.O_NOFOLLOW/os.O_DIRECTORY open flags, os.fchmod, and
fcntl.flock. Windows has none of those: no os.getuid(), no os.fchmod(), no
os.O_NOFOLLOW/os.O_DIRECTORY attributes, and importing fcntl raises
ModuleNotFoundError. Before this module existed, every one of those call
sites crashed at import or first use on Windows, so only the keyring
backend-selection code path (not the surrounding file/lock path) was ever
actually exercised there.

Every function here fails closed: an unverifiable owner, lock, or directory
sync on any platform is treated as untrusted/unavailable, never as an
implicit pass. Windows ACL/DACL hardening (equivalent confidentiality to
POSIX 0600/0700 mode bits) is separate, larger follow-up work already
tracked in project handoff notes (protected secret file plus NTFS DACL via
icacls); this module only makes the existing ownership/lock boundary run
correctly on Windows, and never substitutes a weaker or plaintext fallback.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# os.O_NOFOLLOW / os.O_DIRECTORY do not exist as os module attributes on
# Windows; referencing them unconditionally raises AttributeError there.
NOFOLLOW_FLAG: int = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAG: int = getattr(os, "O_DIRECTORY", 0)


def owns_path(path: Path, *, posix_stat: os.stat_result | None = None) -> bool:
    """Return True only if the current OS user provably owns the path.

    POSIX compares the file owner UID to os.getuid() (using posix_stat when
    the caller already has a fresh stat result, to avoid a redundant
    syscall). Windows has no UID concept; it compares the file's owner SID
    (via GetNamedSecurityInfoW) to this process token's TokenOwner SID (via
    OpenProcessToken + GetTokenInformation), through ctypes only so no extra
    dependency beyond the stdlib is required. This proves the file's owner
    matches this token's default-owner-for-new-objects, which is what NTFS
    actually stamps a newly created file with -- it is not proof of
    exclusive personal ownership or same-process creation, since
    TokenOwner can itself be a shared group SID (see
    _windows_default_owner_sid). Any failure to read either SID is treated
    as non-ownership, never as an implicit pass.
    """
    if not IS_WINDOWS:
        try:
            info = posix_stat if posix_stat is not None else path.stat()
            return info.st_uid == os.getuid()
        except OSError:
            return False
    try:
        return _windows_owner_sid(path) == _windows_default_owner_sid()
    except OSError:
        return False


def fchmod_if_supported(fd: int, mode: int) -> None:
    """POSIX fchmod; a documented no-op on Windows.

    os.fchmod does not exist on Windows and DOS file attributes cannot
    express POSIX mode bits. Windows confidentiality hardening for these
    paths is the separate NTFS DACL follow-up noted above, not silently
    substituted here.
    """
    if IS_WINDOWS:
        return
    os.fchmod(fd, mode)


def chmod_if_supported(path: Path, mode: int) -> None:
    """POSIX chmod; a documented no-op on Windows. See fchmod_if_supported."""
    if IS_WINDOWS:
        return
    os.chmod(path, mode)


def open_nofollow(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
    """os.open with O_NOFOLLOW added where the platform supports it.

    Windows has no O_NOFOLLOW flag for os.open, so a symlink there would
    otherwise be followed silently instead of rejected. A prior version of
    this function used a Path.is_symlink() pre-check before calling
    os.open(); that has a real TOCTOU gap an independent review caught: a
    plain file can be replaced by a symlink between the check and the
    open, and the following os.open() call would then silently follow it,
    since Windows os.open() has no equivalent of O_NOFOLLOW to fall back
    on. This is now closed by opening via CreateFileW with
    FILE_FLAG_OPEN_REPARSE_POINT (a handle to the reparse point itself,
    never the link target) and inspecting that same open handle's own
    attributes for FILE_ATTRIBUTE_REPARSE_POINT before it is ever used for
    anything else. There is no separate check-then-open step: the open and
    the reparse-point rejection both operate on one already-open handle,
    so nothing can be substituted in between.
    """
    if IS_WINDOWS:
        return _windows_open_no_follow(path, flags, mode)
    return os.open(path, flags | NOFOLLOW_FLAG, mode)


def _windows_open_no_follow(path: str | os.PathLike[str], flags: int, mode: int) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    del mode  # Windows os.chmod-style mode bits are not meaningful here;
    # see chmod_if_supported/fchmod_if_supported.

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    open_always = 4
    create_new = 1
    file_attribute_normal = 0x80
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x400
    invalid_handle_value = wintypes.HANDLE(-1).value
    error_file_not_found = 2
    error_path_not_found = 3

    access = generic_read
    if flags & (os.O_RDWR | os.O_WRONLY):
        access |= generic_write
    if flags & os.O_CREAT:
        disposition = create_new if flags & os.O_EXCL else open_always
    else:
        disposition = open_existing

    handle = kernel32.CreateFileW(
        str(path),
        access,
        file_share_read | file_share_write,
        None,
        disposition,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in (error_file_not_found, error_path_not_found):
            raise FileNotFoundError(errno.ENOENT, "file not found", str(path))
        raise OSError(error, "CreateFileW failed", str(path))

    try:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.pointer(info)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed", str(path))
        if info.dwFileAttributes & file_attribute_reparse_point:
            raise OSError(errno.ELOOP, "symlink rejected", str(path))
        crt_flags = os.O_RDWR if access & generic_write else os.O_RDONLY
        crt_flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        return msvcrt.open_osfhandle(handle, crt_flags)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def sync_directory(path: Path) -> None:
    """Force a rename directory entry to durable storage, where supported.

    Windows lacks O_DIRECTORY/O_NOFOLLOW and this specific fsync-a-directory
    primitive. os.replace is already atomic on NTFS, so this is an
    intentional no-op on Windows rather than a weakened equivalent write.
    """
    if IS_WINDOWS:
        return
    directory_fd = os.open(path, os.O_RDONLY | DIRECTORY_FLAG | NOFOLLOW_FLAG)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def try_lock_exclusive(fd: int) -> bool:
    """Acquire a non-blocking exclusive advisory lock on the descriptor.

    Mirrors src/uls/orchestration/locks.py cross-platform lock primitive
    so the LMS apply-lock module does not need a bare, Windows-incompatible
    import fcntl at module scope.
    """
    try:
        if IS_WINDOWS:
            import msvcrt

            # msvcrt.locking locks bytes, so ensure byte zero exists even
            # for a freshly-created empty file.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def unlock(fd: int) -> None:
    try:
        if IS_WINDOWS:
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _win32_dlls() -> tuple[object, object]:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ctypes assumes a 32-bit C int for any return/argument type it is not
    # told about. HANDLE and PSID values are pointer-sized (64-bit on x64),
    # so leaving these prototypes unset silently truncates/misreads them on
    # 64-bit Windows. Every prototype used below is declared explicitly.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)

    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    )
    return advapi32, kernel32


def _windows_default_owner_sid() -> str:
    """Return this process token's default-owner SID for new objects.

    Deliberately TokenOwner (4), not TokenUser (1). NTFS stamps a newly
    created file's owner with the token's default-owner SID, which on an
    elevated/Administrator-context token (the common case for CI runners)
    is often the BUILTIN Administrators group SID (S-1-5-32-544), not the
    signed-in user's personal SID -- confirmed against a real GitHub
    windows-latest runner, where TokenUser returned a personal
    S-1-5-21-...-500 SID but a file this same process had just created
   reported an owner of S-1-5-32-544. Comparing against TokenOwner
    matches what NTFS actually assigns to a new file in both the
    personal-owner and group-owner cases. Note this is a default-owner
    match, not proof of exclusive personal ownership or same-process
    creation: TokenOwner can itself be a shared group SID (BUILTIN
    Administrators here), so a match only shows the file's owner equals
    what this token would stamp on a new object, which any other process
    running under the same default-owner configuration would also match.
    """
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _win32_dlls()
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
        # TOKEN_OWNER is { PSID Owner }, a single pointer-sized field.
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_to_string(sid_ptr)
    finally:
        kernel32.CloseHandle(token)


def _windows_owner_sid(path: Path) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _win32_dlls()
    se_file_object = 1
    owner_security_information = 0x00000001

    owner_sid = wintypes.LPVOID()
    security_descriptor = wintypes.LPVOID()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        owner_security_information,
        ctypes.byref(owner_sid),
        None,
        None,
        None,
        ctypes.byref(security_descriptor),
    )
    if result != 0:
        raise OSError(result, "GetNamedSecurityInfoW failed")
    try:
        return _sid_to_string(owner_sid)
    finally:
        kernel32.LocalFree(security_descriptor)


def _sid_to_string(sid_ptr: object) -> str:
    import ctypes

    advapi32, kernel32 = _win32_dlls()
    string_sid = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_sid)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
    try:
        return string_sid.value or ""
    finally:
        kernel32.LocalFree(string_sid)


__all__ = [
    "DIRECTORY_FLAG",
    "IS_WINDOWS",
    "NOFOLLOW_FLAG",
    "chmod_if_supported",
    "fchmod_if_supported",
    "open_nofollow",
    "owns_path",
    "sync_directory",
    "try_lock_exclusive",
    "unlock",
]
