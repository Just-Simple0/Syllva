"""Bounded Canvas snapshot, native projection, and guarded auth preparation.

Snapshot/project are pure.  Collection and enrollment use explicit injected
boundaries and are never exercised by this module's local self-tests against live
Canvas or the real OS credential store (macOS Keychain / Windows Credential
Manager).
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn, TextIO

ORIGIN = "https://canvas.knu.ac.kr"
ANNOUNCEMENT_WINDOW_DAYS = 31
KST = dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")
MODULE_POSITIONS = frozenset(range(1, 16))
SEMESTER_TERM_ALIASES = {"2026-2": "2026-2", "2026년 2학기": "2026-2"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".review" / "knu-lms-hourly"
CONFIG_PATH = RUNTIME_DIR / "config.json"
AUTH_MANIFEST_PATH = RUNTIME_DIR / "auth-manifest.json"
KEYCHAIN_SERVICE = "Syllva KNU LMS"
CONFIG_FIELDS = frozenset(
    {"version", "course", "origin", "service", "account", "backend", "resources", "issued_at", "expires_at"}
)
MANIFEST_FIELDS = frozenset(
    {"version", "state", "scope_hash", "origin", "course", "service", "account", "backend", "expires_at"}
)
DATASOURCE_IDENTITY = "notion:datasource:2026-2:schedule"
SCHEMA_FIELDS = (
    "이름",
    "과목",
    "유형",
    "날짜",
    "내 상태",
    "내 메모",
    "LMS 원문 URL",
    "LMS 키",
    "수집 범위",
)
NOTIFICATION_STATE_VERSION = 1
NOTIFICATION_KINDS = frozenset({"change", "failure"})
FORBIDDEN_FIELDS = frozenset(
    {
        "body",
        "description",
        "download_url",
        "file_id",
        "grade",
        "html_url",
        "message",
        "submission",
        "submissions",
        "url",
    }
)


class SyncError(Exception):
    """Fixed public error code; input and backend details never cross the boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _notification_state(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"version": NOTIFICATION_STATE_VERSION, "last_signature": None, "last_kind": None}
    if set(value) != {"version", "last_signature", "last_kind"}:
        raise SyncError("notification_state_invalid")
    if value.get("version") != NOTIFICATION_STATE_VERSION:
        raise SyncError("notification_state_invalid")
    signature = value.get("last_signature")
    if signature is not None and (
        not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
    ):
        raise SyncError("notification_state_invalid")
    kind = value.get("last_kind")
    if kind is not None and kind not in NOTIFICATION_KINDS:
        raise SyncError("notification_state_invalid")
    return {"version": NOTIFICATION_STATE_VERSION, "last_signature": signature, "last_kind": kind}


def _notification_summary(value: Any, operations: list[str], reasons: list[str]) -> None:
    if isinstance(value, dict):
        operation = value.get("operation")
        if isinstance(operation, str) and re.fullmatch(r"[a-z_]+", operation):
            operations.append(operation)
        reason = value.get("reason")
        if isinstance(reason, str) and re.fullmatch(r"[a-z_]+", reason):
            reasons.append(reason)
        for child in value.values():
            _notification_summary(child, operations, reasons)
    elif isinstance(value, list):
        for child in value:
            _notification_summary(child, operations, reasons)


def decide_notification(
    result: Mapping[str, Any], previous_state: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return a deterministic notify decision and the state to persist durably."""
    if not isinstance(result, Mapping):
        raise SyncError("notification_result_invalid")
    state = _notification_state(previous_state)
    status = result.get("status")
    operations: list[str] = []
    reasons: list[str] = []
    _notification_summary(result, operations, reasons)
    changes = sorted({operation for operation in operations if operation != "noop"})
    if status == "complete" and changes:
        kind = "change"
    elif status in {"conflict", "incomplete", "failed"}:
        kind = "failure"
    else:
        return {
            "notify": False,
            "kind": "quiet",
            "signature": None,
            "reason": "no_action",
            "state": state,
        }
    snapshot_hash = result.get("snapshot_hash")
    safe_snapshot_hash = snapshot_hash if isinstance(snapshot_hash, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) else None
    summary = {
        "kind": kind,
        "status": status,
        "operations": changes,
        "reasons": sorted(set(reasons)),
        "snapshot_hash": safe_snapshot_hash,
    }
    signature = sha256(_canonical_bytes(summary)).hexdigest()
    duplicate = state["last_signature"] == signature and state["last_kind"] == kind
    next_state = {
        "version": NOTIFICATION_STATE_VERSION,
        "last_signature": signature,
        "last_kind": kind,
    }
    return {
        "notify": not duplicate,
        "kind": kind,
        "signature": signature,
        "reason": "unchanged" if duplicate else f"new_{kind}",
        "state": next_state,
    }


@dataclass(frozen=True)
class SyncConfig:
    course_id: int
    expected_name: str
    expected_code: str
    expected_term: str
    origin: str = ORIGIN
    announcement_window_days: int = ANNOUNCEMENT_WINDOW_DAYS
    module_positions: frozenset[int] | None = None

    def validate(self) -> None:
        _positive_int(self.course_id, "course_id")
        if self.origin != ORIGIN:
            raise SyncError("origin_not_allowlisted")
        _label(self.expected_name, "expected_name")
        _label(self.expected_code, "expected_code")
        _label(self.expected_term, "expected_term")
        if type(self.announcement_window_days) is not int:
            raise SyncError("invalid_window")
        if self.announcement_window_days != ANNOUNCEMENT_WINDOW_DAYS:
            raise SyncError("invalid_window")
        if self.module_positions is not None and (
            not self.module_positions or any(type(p) is not int or p <= 0 for p in self.module_positions)
        ):
            raise SyncError("invalid_module_contract")


@dataclass(frozen=True)
class CourseSpec:
    """An explicitly bound course in one semester registry."""

    course_id: int
    expected_name: str
    expected_code: str | None
    expected_term: str
    origin: str = ORIGIN
    verification_state: str = "api_code_verified"
    academic_import: bool = True
    module_positions: frozenset[int] | None = None

    def validate(self, semester: str) -> None:
        _positive_int(self.course_id, "course_id")
        if self.origin != ORIGIN:
            raise SyncError("origin_not_allowlisted")
        _label(self.expected_name, "expected_name")
        _label(self.expected_term, "expected_term")
        if self.expected_code is not None:
            _label(self.expected_code, "expected_code")
        if _label(semester, "semester") != "2026-2":
            raise SyncError("semester_mismatch")
        if self.academic_import and SEMESTER_TERM_ALIASES.get(
            _label(self.expected_term, "expected_term")
        ) != semester:
            raise SyncError("course_term_mismatch")
        if self.verification_state not in {
            "api_code_verified", "identity_observed", "observed_candidate", "needs_verification"
        }:
            raise SyncError("verification_state_invalid")
        if self.module_positions is not None and not self.module_positions:
            raise SyncError("invalid_module_contract")

    def config(self) -> SyncConfig:
        if self.expected_code is None:
            raise SyncError("course_code_unverified")
        return SyncConfig(
            course_id=self.course_id,
            expected_name=self.expected_name,
            expected_code=self.expected_code,
            expected_term=self.expected_term,
            origin=self.origin,
            module_positions=self.module_positions,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.course_id,
            "name": self.expected_name,
            "code": self.expected_code,
            "term": self.expected_term,
            "origin": self.origin,
            "verification_state": self.verification_state,
            "academic_import": self.academic_import,
            **({"module_positions": sorted(self.module_positions)} if self.module_positions else {}),
        }


@dataclass(frozen=True)
class SemesterCourseRegistry:
    semester: str
    courses: tuple[CourseSpec, ...]

    def validate(self) -> None:
        if self.semester != "2026-2" or not self.courses:
            raise SyncError("registry_invalid")
        ids: set[int] = set()
        for course in self.courses:
            course.validate(self.semester)
            if course.course_id in ids:
                raise SyncError("duplicate_course_identity")
            ids.add(course.course_id)

    @property
    def academic_courses(self) -> tuple[CourseSpec, ...]:
        return tuple(course for course in self.courses if course.academic_import)

    def canonical_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "version": "knu-lms-semester-registry.v1",
            "semester": self.semester,
            "courses": [course.as_dict() for course in sorted(self.courses, key=lambda c: c.course_id)],
        }

    def scope_hash(self, *, transport: str = "aside-readonly") -> str:
        policy = {
            "registry": self.canonical_dict(),
            "transport": transport,
            "resource_policy": ["course", "assignments", "announcements", "modules"],
        }
        return sha256(_canonical_bytes(policy)).hexdigest()


def registry_from_document(document: Any) -> SemesterCourseRegistry:
    if not isinstance(document, dict) or set(document) != {"version", "semester", "courses"}:
        raise SyncError("registry_invalid")
    if document.get("version") != "knu-lms-semester-registry.v1" or not isinstance(document.get("courses"), list):
        raise SyncError("registry_invalid")
    courses: list[CourseSpec] = []
    for value in document["courses"]:
        if not isinstance(value, dict):
            raise SyncError("registry_invalid")
        allowed = {"id", "name", "code", "term", "origin", "verification_state", "academic_import", "module_positions"}
        if set(value) - allowed or set(value) < {"id", "name", "code", "term", "verification_state", "academic_import"}:
            raise SyncError("registry_invalid")
        positions = value.get("module_positions")
        module_positions: frozenset[int] | None = None
        if positions is not None:
            if not isinstance(positions, list) or any(type(p) is not int or p <= 0 for p in positions):
                raise SyncError("registry_invalid")
            module_positions = frozenset(positions)
            if len(module_positions) != len(positions):
                raise SyncError("registry_invalid")
        courses.append(CourseSpec(
            course_id=value["id"], expected_name=value["name"], expected_code=value["code"],
            expected_term=value["term"], origin=value.get("origin", ORIGIN),
            verification_state=value["verification_state"], academic_import=value["academic_import"],
            module_positions=module_positions,
        ))
    registry = SemesterCourseRegistry(semester=document["semester"], courses=tuple(courses))
    registry.validate()
    return registry


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SyncError("noncanonical_value") from exc


def _atomic_json_write(path: Path, value: dict[str, Any], mode: int) -> None:
    if RUNTIME_DIR.is_symlink() or RUNTIME_DIR.exists() and not RUNTIME_DIR.is_dir():
        raise SyncError("runtime_dir_invalid")
    if path.is_symlink():
        raise SyncError("state_path_invalid")
    try:
        RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(RUNTIME_DIR, 0o700)
        runtime_info = RUNTIME_DIR.stat()
        if runtime_info.st_uid != os.getuid() or stat.S_IMODE(runtime_info.st_mode) != 0o700:
            raise SyncError("runtime_dir_invalid")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=RUNTIME_DIR)
        temporary: Path | None = Path(temporary_name)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if path.is_symlink():
                raise SyncError("state_path_invalid")
            if temporary is None:
                raise SyncError("state_write_failed")
            os.replace(temporary, path)
            directory_fd = os.open(
                RUNTIME_DIR,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            temporary = None
        finally:
            # Keep a failed temporary for diagnosis.  It is in the private runtime
            # directory and contains only the caller-supplied secret-free document.
            # The next atomic write uses a fresh name; no cleanup is attempted here.
            pass
    except SyncError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise SyncError("state_write_failed") from exc


def _read_json_file(
    path: Path, *, max_bytes: int = 16384, expected_mode: int | None = None
) -> dict[str, Any]:
    if path.is_symlink():
        raise SyncError("state_path_invalid")
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise SyncError("state_too_large")
        if info.st_uid != os.getuid() or (
            expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode
        ):
            raise SyncError("state_permissions_invalid")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            value = json.load(stream)
    except SyncError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError("state_unreadable") from exc
    finally:
        if fd is not None:
            os.close(fd)
    if not isinstance(value, dict):
        raise SyncError("state_invalid")
    return value


def _parse_expiry(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise SyncError("expiry_invalid")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise SyncError("expiry_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncError("expiry_invalid")
    return parsed


def _config_from_document(document: Mapping[str, Any]) -> tuple[SyncConfig, dict[str, Any]]:
    if set(document) != CONFIG_FIELDS:
        raise SyncError("config_invalid")
    if document.get("version") != 1:
        raise SyncError("config_invalid")
    course = document.get("course")
    if not isinstance(course, dict):
        raise SyncError("config_invalid")
    course_id = course.get("id")
    name = course.get("name")
    code = course.get("code")
    term = course.get("term")
    origin = document.get("origin")
    if (
        type(course_id) is not int
        or not isinstance(name, str)
        or not isinstance(code, str)
        or not isinstance(term, str)
        or not isinstance(origin, str)
    ):
        raise SyncError("config_invalid")
    config = SyncConfig(
        course_id=course_id,
        expected_name=name,
        expected_code=code,
        expected_term=term,
        origin=origin,
    )
    config.validate()
    if document.get("service") != KEYCHAIN_SERVICE:
        raise SyncError("config_binding_mismatch")
    expected_account = f"canvas.knu.ac.kr/course/{config.course_id}"
    if document.get("account") != expected_account:
        raise SyncError("config_binding_mismatch")
    if document.get("backend") != _expected_backend_module():
        raise SyncError("config_binding_mismatch")
    scope = document.get("resources")
    if scope != ["course", "assignments", "announcements", "modules"]:
        raise SyncError("config_scope_invalid")
    issued_at = _parse_expiry(document.get("issued_at"))
    expires_at = _parse_expiry(document.get("expires_at"))
    if expires_at <= issued_at or expires_at > issued_at + dt.timedelta(days=30):
        raise SyncError("expiry_invalid")
    return config, dict(document)


def config_scope_hash(document: Mapping[str, Any]) -> str:
    config, normalized = _config_from_document(document)
    config.validate()
    digest = sha256(_canonical_bytes(normalized)).hexdigest()
    if len(digest) != 64:
        raise SyncError("scope_hash_invalid")
    return digest


def _lock_module() -> Any:
    module_path = PROJECT_ROOT / "scripts" / "knu_lms_apply_lock.py"
    try:
        spec = importlib.util.spec_from_file_location("knu_lms_apply_lock_for_sync", module_path)
        if spec is None or spec.loader is None:
            raise SyncError("reservation_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except SyncError:
        raise
    except Exception as exc:
        raise SyncError("reservation_unavailable") from exc


def _begin_reservation(scope_hash: str) -> Any:
    if not re.fullmatch(r"[0-9a-f]{64}", scope_hash):
        raise SyncError("scope_hash_invalid")
    try:
        module = _lock_module()
        return module.Reservation.begin(RUNTIME_DIR, scope_hash)
    except SyncError:
        raise
    except Exception as exc:
        code = getattr(exc, "args", [None])[0]
        if code in {"run_busy", "interrupted_reservation_blocked", "scope_hash_invalid"}:
            raise SyncError(str(code)) from None
        raise SyncError("reservation_unavailable") from exc


def _ensure_participant(owner_id: str, scope_hash: str) -> None:
    try:
        module = _lock_module()
        module.ensure_participant(RUNTIME_DIR, owner_id, scope_hash)
    except SyncError:
        raise
    except Exception as exc:
        code = getattr(exc, "args", [None])[0]
        if isinstance(code, str) and re.fullmatch(r"[a-z_]+", code):
            raise SyncError(code) from None
        raise SyncError("reservation_binding_mismatch") from exc


def _expected_backend_module() -> str:
    """Return the required backend dotted path for the current OS, or raise.

    Evaluated fresh on every call (never cached) so that platform-specific
    binding checks in config/manifest documents reflect the process's actual
    ``sys.platform`` at call time, including test monkeypatching of
    ``sys.platform``.
    """
    if sys.platform == "darwin":
        return "keyring.backends.macOS.Keyring"
    if sys.platform == "win32":
        return "keyring.backends.Windows.WinVaultKeyring"
    raise SyncError("keychain_platform_unsupported")


def _explicit_os_keyring() -> Any:
    """Return a freshly constructed, verified OS-native keyring backend.

    macOS uses the explicit ``keyring.backends.macOS.Keyring`` class and
    forces ``keychain = None`` so a ``KEYCHAIN_PATH``-style override cannot
    redirect reads/writes away from the user's default system Keychain.
    Windows uses the explicit ``keyring.backends.Windows.WinVaultKeyring``
    class bound to Windows Credential Manager for the current OS user; it has
    no analogous overridable vault-path property. Both branches verify the
    constructed instance's concrete ``__module__`` to reject a
    monkeypatched/global keyring backend substitution. No other platform is
    supported; there is no fallback backend.
    """
    if sys.platform == "darwin":
        try:
            module = __import__("keyring.backends.macOS", fromlist=["Keyring"])
            backend_type = module.Keyring
            backend = backend_type()
            backend.keychain = None
            if backend.__class__.__module__ != "keyring.backends.macOS" or backend.keychain is not None:
                raise SyncError("keychain_backend_invalid")
            return backend
        except SyncError:
            raise
        except Exception as exc:
            raise SyncError("keychain_backend_unavailable") from exc
    if sys.platform == "win32":
        try:
            module = __import__("keyring.backends.Windows", fromlist=["WinVaultKeyring"])
            backend_type = module.WinVaultKeyring
            backend = backend_type()
            if backend.__class__.__module__ != "keyring.backends.Windows":
                raise SyncError("keychain_backend_invalid")
            return backend
        except SyncError:
            raise
        except Exception as exc:
            raise SyncError("keychain_backend_unavailable") from exc
    raise SyncError("keychain_platform_unsupported")


def _fixed_auth_document(config_document: dict[str, Any], state: str, scope_hash: str) -> dict[str, Any]:
    config, _ = _config_from_document(config_document)
    return {
        "version": 1,
        "state": state,
        "scope_hash": scope_hash,
        "origin": config.origin,
        "course": {
            "id": config.course_id,
            "name": config.expected_name,
            "code": config.expected_code,
            "term": config.expected_term,
        },
        "service": KEYCHAIN_SERVICE,
        "account": f"canvas.knu.ac.kr/course/{config.course_id}",
        "backend": _expected_backend_module(),
        "expires_at": config_document["expires_at"],
    }


def _load_auth_config() -> tuple[SyncConfig, dict[str, Any], str]:
    config_document = _read_json_file(CONFIG_PATH, expected_mode=0o600)
    config, normalized = _config_from_document(config_document)
    return config, normalized, config_scope_hash(normalized)


def _validate_auth_window(
    config_document: dict[str, Any], now: dt.datetime | None
) -> dt.datetime:
    issued_at = _parse_expiry(config_document["issued_at"])
    expires_at = _parse_expiry(config_document["expires_at"])
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise SyncError("auth_clock_invalid")
    if current < issued_at:
        raise SyncError("auth_not_yet_valid")
    if current >= expires_at:
        raise SyncError("auth_manifest_expired")
    return current


def read_enrolled_token(
    *, now: dt.datetime | None = None, expected_scope_hash: str | None = None
) -> tuple[str, SyncConfig, str]:
    config, config_document, scope_hash = _load_auth_config()
    if expected_scope_hash is not None and expected_scope_hash != scope_hash:
        raise SyncError("scope_hash_mismatch")
    _validate_auth_window(config_document, now)
    manifest = _read_json_file(AUTH_MANIFEST_PATH, expected_mode=0o600)
    if set(manifest) != MANIFEST_FIELDS:
        raise SyncError("auth_manifest_binding_mismatch")
    if manifest.get("state") != "enrolled":
        raise SyncError("auth_manifest_not_enrolled")
    if manifest.get("scope_hash") != scope_hash:
        raise SyncError("auth_manifest_binding_mismatch")
    expected = _fixed_auth_document(config_document, "enrolled", scope_hash)
    for field in ("origin", "course", "service", "account", "backend", "expires_at"):
        if manifest.get(field) != expected[field]:
            raise SyncError("auth_manifest_binding_mismatch")
    backend = _explicit_os_keyring()
    if hasattr(backend, "keychain"):
        backend.keychain = None
        if backend.keychain is not None:
            raise SyncError("keychain_backend_invalid")
    try:
        token = backend.get_password(KEYCHAIN_SERVICE, f"canvas.knu.ac.kr/course/{config.course_id}")
    except Exception as exc:
        raise SyncError("keychain_read_failed") from exc
    if not isinstance(token, str) or not token:
        raise SyncError("credential_missing")
    return token, config, scope_hash


def enroll_keychain(
    *,
    prompt: Callable[[str], str],
    stdin: Any,
    now: dt.datetime | None = None,
    backend: Any | None = None,
) -> dict[str, Any]:
    if not stdin.isatty():
        raise SyncError("credential_tty_required")
    config, config_document, scope_hash = _load_auth_config()
    _validate_auth_window(config_document, now)
    reservation = _begin_reservation(scope_hash)
    try:
        pending = _fixed_auth_document(config_document, "pending", scope_hash)
        _atomic_json_write(AUTH_MANIFEST_PATH, pending, 0o600)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = prompt("Canvas access token: ")
        except getpass.GetPassWarning:
            raise SyncError("credential_noecho_unavailable") from None
        except (EOFError, OSError, KeyboardInterrupt):
            raise SyncError("credential_noecho_unavailable") from None
        if not isinstance(token, str) or not token or any(not 0x21 <= ord(char) <= 0x7E for char in token):
            raise SyncError("credential_invalid")
        selected_backend = backend if backend is not None else _explicit_os_keyring()
        if hasattr(selected_backend, "keychain"):
            selected_backend.keychain = None
            if selected_backend.keychain is not None:
                raise SyncError("keychain_backend_invalid")
        try:
            selected_backend.set_password(KEYCHAIN_SERVICE, f"canvas.knu.ac.kr/course/{config.course_id}", token)
        except Exception as exc:
            raise SyncError("keychain_write_failed") from exc
        try:
            enrolled = _fixed_auth_document(config_document, "enrolled", scope_hash)
            _atomic_json_write(AUTH_MANIFEST_PATH, enrolled, 0o600)
        except SyncError:
            raise SyncError("enrollment_incomplete") from None
        reservation.complete()
        return {"status": "enrolled", "scope_hash": scope_hash, "expires_at": config_document["expires_at"]}
    finally:
        reservation.close()


def _probe_module() -> Any:
    module_path = PROJECT_ROOT / "scripts" / "knu_lms_probe.py"
    module_name = "knu_lms_probe_for_sync"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise SyncError("probe_unavailable")
        module = importlib.util.module_from_spec(spec)
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except SyncError:
        raise
    except Exception as exc:
        if "previous" in locals():
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
        raise SyncError("probe_unavailable") from exc


def collect(
    *,
    owner_id: str,
    scope_hash: str,
    now: dt.datetime | None = None,
    opener: Any | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CanonicalSnapshot:
    """Collect one complete pilot snapshot under a root-owned reservation."""
    config, _document, calculated_scope_hash = _load_auth_config()
    if scope_hash != calculated_scope_hash:
        raise SyncError("scope_hash_mismatch")
    _ensure_participant(owner_id, scope_hash)
    token, credential_config, returned_scope_hash = read_enrolled_token(
        now=now, expected_scope_hash=scope_hash
    )
    if returned_scope_hash != scope_hash:
        raise SyncError("scope_hash_mismatch")
    config = credential_config
    _ensure_participant(owner_id, scope_hash)
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise SyncError("invalid_clock")
    today = current.astimezone(KST).date()
    # Canvas treats end_date as inclusive.  This current-day-covering tomorrow
    # boundary remains within the reviewed probe's 31-date inclusive span.
    end_date = (today + dt.timedelta(days=1)).isoformat()
    start_date = (today - dt.timedelta(days=ANNOUNCEMENT_WINDOW_DAYS - 2)).isoformat()
    probe = _probe_module()
    args = argparse.Namespace(
        course_id=config.course_id,
        expected_name=config.expected_name,
        expected_code=config.expected_code,
        expected_term=config.expected_term,
        start_date=start_date,
        end_date=end_date,
        include_files=False,
        include_modules=True,
    )
    if opener is None:
        opener = probe.build_opener(probe.NoRedirectHandler())
    try:
        raw = probe.run_probe(args, token, opener=opener, clock=clock)
    except Exception as exc:
        raise SyncError("collect_failed") from exc
    _ensure_participant(owner_id, scope_hash)
    return validate_snapshot(raw, config)


@dataclass(frozen=True)
class CanonicalSnapshot:
    payload: dict[str, Any]
    snapshot_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "complete",
            "snapshot": self.payload,
            "snapshot_hash": self.snapshot_hash,
        }


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise SyncError("invalid_" + field)
    return value


def _label(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SyncError("invalid_" + field)
    normalized = unicodedata.normalize("NFKC", value)
    if any(ord(char) < 32 for char in normalized):
        raise SyncError("invalid_" + field)
    normalized = " ".join(normalized.split())
    if not allow_empty and not normalized:
        raise SyncError("invalid_" + field)
    return normalized


def _optional_label(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _label(value, field)


def _walk_reject_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_FIELDS:
                raise SyncError("forbidden_field")
            _walk_reject_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            _walk_reject_forbidden(child)


def _course_url(origin: str, course_id: int) -> str:
    return f"{origin}/courses/{course_id}"


def _assignment_url(origin: str, course_id: int, assignment_id: int) -> str:
    return f"{origin}/courses/{course_id}/assignments/{assignment_id}"


def _announcement_url(origin: str, course_id: int, announcement_id: int) -> str:
    return f"{origin}/courses/{course_id}/discussion_topics/{announcement_id}"


def _module_url(origin: str, course_id: int, module_id: int) -> str:
    return f"{origin}/courses/{course_id}/modules/{module_id}"


def _module_item_url(origin: str, course_id: int, item_id: int) -> str:
    return f"{origin}/courses/{course_id}/modules/items/{item_id}"


def _source_identity(origin: str, course_id: int, kind: str, resource_id: int) -> dict[str, Any]:
    return {
        "origin": origin,
        "course_id": course_id,
        "resource_kind": kind,
        "resource_id": resource_id,
    }


def _validate_course(record: Any, config: SyncConfig) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SyncError("invalid_course_shape")
    course_id = _positive_int(record.get("id"), "course_id")
    name = _label(record.get("name"), "course_name")
    code = _label(record.get("course_code"), "course_code")
    term = _label(record.get("term"), "course_term")
    if course_id != config.course_id:
        raise SyncError("course_identity_mismatch")
    if name.casefold() != _label(config.expected_name, "expected_name").casefold():
        raise SyncError("course_identity_mismatch")
    if code.casefold() != _label(config.expected_code, "expected_code").casefold():
        raise SyncError("course_identity_mismatch")
    if term.casefold() != _label(config.expected_term, "expected_term").casefold():
        raise SyncError("course_identity_mismatch")
    return {
        "id": course_id,
        "name": name,
        "course_code": code,
        "term": term,
        "source_identity": _source_identity(config.origin, course_id, "course", course_id),
        "source_key": _course_url(config.origin, course_id),
        "lms_url": _course_url(config.origin, course_id),
    }


def _validate_assignment(record: Any, config: SyncConfig) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SyncError("invalid_assignment_shape")
    assignment_id = _positive_int(record.get("id"), "assignment_id")
    name = _label(record.get("name"), "assignment_name")
    result: dict[str, Any] = {
        "id": assignment_id,
        "name": name,
        "due_at": _optional_label(record.get("due_at"), "due_at"),
        "source_identity": _source_identity(
            config.origin, config.course_id, "assignment", assignment_id
        ),
        "source_key": _assignment_url(config.origin, config.course_id, assignment_id),
        "lms_url": _assignment_url(config.origin, config.course_id, assignment_id),
    }
    for field in ("lock_at", "unlock_at"):
        if field in record:
            result[field] = _optional_label(record.get(field), field)
    for field in ("position",):
        if field in record:
            value = record[field]
            if type(value) is not int or value < 0:
                raise SyncError("invalid_assignment_shape")
            result[field] = value
    for field in ("published", "locked_for_user"):
        if field in record:
            value = record[field]
            if type(value) is not bool:
                raise SyncError("invalid_assignment_shape")
            result[field] = value
    return result


def _validate_announcement(record: Any, config: SyncConfig) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SyncError("invalid_announcement_shape")
    announcement_id = _positive_int(record.get("id"), "announcement_id")
    title = _label(record.get("title"), "announcement_title")
    context_code = _label(record.get("context_code"), "context_code")
    if context_code != f"course_{config.course_id}":
        raise SyncError("announcement_context_mismatch")
    result: dict[str, Any] = {
        "id": announcement_id,
        "title": title,
        "context_code": context_code,
        "source_identity": _source_identity(
            config.origin, config.course_id, "announcement", announcement_id
        ),
        "source_key": _announcement_url(config.origin, config.course_id, announcement_id),
        "lms_url": _announcement_url(config.origin, config.course_id, announcement_id),
    }
    for field in ("posted_at", "published_at", "delayed_post_at"):
        if field in record:
            result[field] = _optional_label(record.get(field), field)
    return result


def _validate_module_item(record: Any, config: SyncConfig) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SyncError("invalid_module_item_shape")
    item_id = _positive_int(record.get("id"), "module_item_id")
    title = _label(record.get("title"), "module_item_title")
    result: dict[str, Any] = {
        "id": item_id,
        "title": title,
        "lms_url": _module_item_url(config.origin, config.course_id, item_id),
    }
    if "type" in record:
        result["type"] = _optional_label(record.get("type"), "module_item_type")
    if "position" in record:
        position = record["position"]
        if type(position) is not int or position < 0:
            raise SyncError("invalid_module_item_shape")
        result["position"] = position
    if "published" in record:
        published = record["published"]
        if type(published) is not bool:
            raise SyncError("invalid_module_item_shape")
        result["published"] = published
    if "content_id" in record:
        content_id = record["content_id"]
        if type(content_id) is int:
            if content_id <= 0:
                raise SyncError("invalid_module_item_shape")
            result["content_id"] = content_id
        elif isinstance(content_id, str):
            result["content_id"] = _label(content_id, "content_id")
        else:
            raise SyncError("invalid_module_item_shape")
    return result


def _validate_module(record: Any, config: SyncConfig) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SyncError("invalid_module_shape")
    if "items_count" in record:
        raise SyncError("invalid_module_shape")
    position = record.get("position")
    if type(position) is not int or position <= 0:
        raise SyncError("invalid_module_position")
    name = _label(record.get("name"), "module_name")
    if config.module_positions is not None and (
        position not in config.module_positions or name != f"{position}주차"
    ):
        raise SyncError("invalid_module_name")
    module_id = _positive_int(record.get("id"), "module_id")
    items = record.get("items")
    if not isinstance(items, list) or record.get("items_complete") is not True:
        raise SyncError("module_items_incomplete")
    if record.get("items_available") is not True or record.get("items_unavailable") is not False:
        raise SyncError("module_items_incomplete")
    if record.get("items_count_available") is not True:
        raise SyncError("module_items_count_missing")
    returned_count = record.get("items_returned_count")
    if type(returned_count) is not int or returned_count < 0 or returned_count != len(items):
        raise SyncError("module_items_count_mismatch")
    if record.get("items_complete") is not True:
        raise SyncError("module_items_incomplete")
    safe_items = [_validate_module_item(item, config) for item in items]
    safe_items.sort(key=lambda item: (item.get("position", 0), item["id"]))
    result: dict[str, Any] = {
        "id": module_id,
        "name": name,
        "position": position,
        "items_available": True,
        "items_unavailable": False,
        "items_count_available": True,
        "items_returned_count": len(safe_items),
        "items_complete": True,
        "items": safe_items,
        "source_identity": _source_identity(config.origin, config.course_id, "module", module_id),
        "source_key": _module_url(config.origin, config.course_id, module_id),
        "lms_url": _module_url(config.origin, config.course_id, module_id),
    }
    return result


def validate_snapshot(raw: Any, config: SyncConfig) -> CanonicalSnapshot:
    config.validate()
    if not isinstance(raw, dict):
        raise SyncError("invalid_snapshot_shape")
    _walk_reject_forbidden(raw)
    if raw.get("status") != "complete":
        raise SyncError("incomplete_snapshot")
    allowed = {"status", "course", "assignments", "announcements", "modules"}
    if set(raw) - allowed:
        raise SyncError("invalid_snapshot_shape")
    course = _validate_course(raw.get("course"), config)
    assignments_raw = raw.get("assignments")
    announcements_raw = raw.get("announcements")
    modules_raw = raw.get("modules")
    if not isinstance(assignments_raw, list):
        raise SyncError("invalid_assignments_shape")
    if not isinstance(announcements_raw, list):
        raise SyncError("invalid_announcements_shape")
    if not isinstance(modules_raw, list):
        raise SyncError("invalid_module_set")
    assignments = [_validate_assignment(item, config) for item in assignments_raw]
    announcements = [_validate_announcement(item, config) for item in announcements_raw]
    modules = [_validate_module(item, config) for item in modules_raw]
    if len({item["source_key"] for item in assignments}) != len(assignments):
        raise SyncError("duplicate_source_identity")
    if len({item["source_key"] for item in announcements}) != len(announcements):
        raise SyncError("duplicate_source_identity")
    positions = {item["position"] for item in modules}
    if len(positions) != len(modules):
        raise SyncError("duplicate_module_position")
    if config.module_positions is not None and positions != config.module_positions:
        raise SyncError("invalid_module_set")
    if len({item["source_key"] for item in modules}) != len(modules):
        raise SyncError("duplicate_source_identity")
    module_item_keys = [
        item["lms_url"] for module in modules for item in module["items"]
    ]
    if len(set(module_item_keys)) != len(module_item_keys):
        raise SyncError("duplicate_source_identity")
    assignments.sort(key=lambda item: item["source_key"])
    announcements.sort(key=lambda item: item["source_key"])
    modules.sort(key=lambda item: item["position"])
    payload: dict[str, Any] = {
        "version": 1,
        "origin": config.origin,
        "scope": {
            "course_id": config.course_id,
            "expected_name": _label(config.expected_name, "expected_name"),
            "expected_code": _label(config.expected_code, "expected_code"),
            "expected_term": _label(config.expected_term, "expected_term"),
            "announcement_window_days": ANNOUNCEMENT_WINDOW_DAYS,
            "resources": ["course", "assignments", "announcements", "modules"],
            **({"module_positions": sorted(config.module_positions)} if config.module_positions is not None else {}),
        },
        "course": course,
        "assignments": assignments,
        "announcements": announcements,
        "modules": modules,
    }
    digest = sha256(_canonical_bytes(payload)).hexdigest()
    return CanonicalSnapshot(payload=payload, snapshot_hash=digest)


def validate_semester_snapshot(
    registry: SemesterCourseRegistry,
    raw: Any,
    *,
    expected_scope_hash: str | None = None,
) -> dict[str, Any]:
    """Validate every explicitly registered course and preserve per-course coverage."""
    registry.validate()
    if not isinstance(raw, dict) or raw.get("status") != "complete":
        raise SyncError("incomplete_semester_snapshot")
    provenance = raw.get("provenance")
    if provenance not in {"aside-readonly", "fixture-synthetic"}:
        raise SyncError("invalid_provenance")
    registry_scope_hash = registry.scope_hash(transport="aside-readonly")
    if raw.get("scope_hash") != registry_scope_hash or (
        expected_scope_hash is not None and expected_scope_hash != registry_scope_hash
    ):
        raise SyncError("scope_hash_mismatch")
    courses_raw = raw.get("courses")
    if not isinstance(courses_raw, dict):
        raise SyncError("invalid_semester_snapshot")
    results: dict[str, Any] = {}
    canonical: dict[str, Any] = {}
    academic_complete = True
    for spec in registry.courses:
        key = str(spec.course_id)
        if not spec.academic_import:
            results[key] = {
                "status": "excluded",
                "academic_import": False,
                "verification_state": spec.verification_state,
                "coverage": {"expected": 0, "observed": 0},
            }
            continue
        if spec.expected_code is None or spec.verification_state not in {"api_code_verified", "verified"}:
            results[key] = {
                "status": "needs_verification",
                "academic_import": True,
                "verification_state": spec.verification_state,
                "coverage": {"expected": 1, "observed": 0},
            }
            academic_complete = False
            continue
        value = courses_raw.get(key)
        if not isinstance(value, dict):
            results[key] = {"status": "failed", "error": "course_snapshot_missing", "coverage": {"expected": 1, "observed": 0}}
            academic_complete = False
            continue
        if value.get("status") != "complete":
            status = value.get("status")
            results[key] = {
                "status": status if status in {"partial", "failed"} else "partial",
                "error": value.get("error", "incomplete_snapshot") if isinstance(value.get("error"), str) else "incomplete_snapshot",
                "coverage": {"expected": 1, "observed": 0},
            }
            academic_complete = False
            continue
        try:
            canonical_snapshot = validate_snapshot(value, spec.config())
        except SyncError as exc:
            results[key] = {"status": "failed", "error": exc.code, "coverage": {"expected": 1, "observed": 0}}
            academic_complete = False
            continue
        canonical[key] = canonical_snapshot.as_dict()
        results[key] = {
            "status": "complete",
            "academic_import": True,
            "coverage": {
                "expected": {"course": 1, "assignments": len(canonical_snapshot.payload["assignments"]), "announcements": len(canonical_snapshot.payload["announcements"]), "modules": len(canonical_snapshot.payload["modules"])},
                "observed": {"course": 1, "assignments": len(canonical_snapshot.payload["assignments"]), "announcements": len(canonical_snapshot.payload["announcements"]), "modules": len(canonical_snapshot.payload["modules"])},
            },
            "snapshot": canonical_snapshot.as_dict(),
        }
    return {
        "status": "complete" if academic_complete else "incomplete",
        "provenance": provenance,
        "semester": registry.semester,
        "registry_hash": sha256(_canonical_bytes(registry.canonical_dict())).hexdigest(),
        "scope_hash": registry_scope_hash,
        "academic_expected": len(registry.academic_courses),
        "academic_complete": sum(1 for item in results.values() if item.get("status") == "complete" and item.get("academic_import") is True),
        "apply_ready": academic_complete and provenance == "aside-readonly",
        "courses": results,
        "snapshots": canonical,
        "excluded_candidates": [str(spec.course_id) for spec in registry.courses if not spec.academic_import],
    }


def build_semester_projection(
    registry: SemesterCourseRegistry,
    semester_snapshot: dict[str, Any],
    *,
    notion_readback: dict[str, Any] | None = None,
    prior: dict[str, Any] | None = None,
    owner_id: str | None = None,
    scope_hash: str | None = None,
    observed_on: str | None = None,
) -> dict[str, Any]:
    """Build one shared semester datasource and course-qualified child plans."""
    registry.validate()
    if owner_id is not None or scope_hash is not None:
        if not isinstance(owner_id, str) or not isinstance(scope_hash, str):
            raise SyncError("reservation_binding_mismatch")
        _ensure_participant(owner_id, scope_hash)
    if not isinstance(semester_snapshot, dict) or semester_snapshot.get("status") != "complete":
        return {"status": "incomplete", "apply_ready": False, "conflicts": [{"reason": "semester_incomplete"}]}
    if semester_snapshot.get("provenance") == "fixture-synthetic":
        return {"status": "synthetic", "apply_ready": False, "conflicts": [{"reason": "synthetic_provenance"}]}
    expected_registry_hash = sha256(_canonical_bytes(registry.canonical_dict())).hexdigest()
    expected_scope_hash = registry.scope_hash(transport="aside-readonly")
    if semester_snapshot.get("registry_hash") != expected_registry_hash:
        raise SyncError("registry_hash_mismatch")
    if semester_snapshot.get("scope_hash") != expected_scope_hash:
        raise SyncError("scope_hash_mismatch")
    if semester_snapshot.get("semester") != registry.semester:
        raise SyncError("semester_mismatch")
    if observed_on is not None:
        try:
            dt.date.fromisoformat(observed_on)
        except ValueError as exc:
            raise SyncError("invalid_observed_on") from exc
    snapshots = semester_snapshot.get("snapshots")
    if not isinstance(snapshots, dict):
        raise SyncError("invalid_semester_snapshot")
    readback = notion_readback or {}
    if not isinstance(readback, dict):
        raise SyncError("invalid_notion_readback")
    if prior is not None:
        if not isinstance(prior, dict) or not isinstance(prior.get("courses"), dict):
            raise SyncError("semester_prior_required")
        prior_courses = prior["courses"]
        for spec in registry.academic_courses:
            entry = prior_courses.get(str(spec.course_id))
            if entry is None or not isinstance(entry, dict):
                raise SyncError("semester_prior_missing")
    if "verified_course_title" in readback:
        raise SyncError("semester_title_scope_required")
    course_keys = {_course_url(spec.origin, spec.course_id) for spec in registry.courses}
    verified_titles = readback.get("verified_course_titles", {})
    if not isinstance(verified_titles, dict):
        raise SyncError("invalid_course_title_mapping")
    if any(
        not isinstance(key, str) or key not in course_keys or not isinstance(value, str)
        for key, value in verified_titles.items()
    ):
        raise SyncError("invalid_course_title_mapping")
    normalized_titles = {
        key: _label(value, "course_display_title") for key, value in verified_titles.items()
    }
    scoped_readbacks = readback.get("course_readbacks", {})
    if not isinstance(scoped_readbacks, dict):
        raise SyncError("invalid_course_readbacks")
    if any(
        not isinstance(key, str) or key not in course_keys or not isinstance(value, dict)
        for key, value in scoped_readbacks.items()
    ):
        raise SyncError("invalid_course_readbacks")
    course_plans: dict[str, Any] = {}
    rows: list[Any] = []
    conflicts: list[Any] = []
    for spec in registry.academic_courses:
        canonical = snapshots.get(str(spec.course_id))
        if not isinstance(canonical, dict):
            conflicts.append({"source_key": _course_url(spec.origin, spec.course_id), "reason": "course_snapshot_missing"})
            continue
        snapshot = _canonical_from_document(canonical)
        canonical_course = snapshot.payload.get("course")
        expected_course_key = _course_url(spec.origin, spec.course_id)
        if (
            snapshot.payload.get("origin") != spec.origin
            or not isinstance(canonical_course, dict)
            or canonical_course.get("id") != spec.course_id
            or canonical_course.get("name") != spec.expected_name
            or canonical_course.get("course_code") != spec.expected_code
            or canonical_course.get("term") != spec.expected_term
            or canonical_course.get("source_key") != expected_course_key
        ):
            raise SyncError("semester_course_identity_mismatch")
        scoped = dict(readback)
        scoped.pop("verified_course_titles", None)
        scoped.pop("course_readbacks", None)
        local_readback = scoped_readbacks.get(expected_course_key)
        if local_readback is not None:
            if "verified_course_title" in local_readback and not isinstance(
                local_readback["verified_course_title"], str
            ):
                raise SyncError("invalid_course_title_mapping")
            scoped.update(local_readback)
        if "verified_course_title" in scoped:
            scoped["verified_course_title"] = _label(
                scoped["verified_course_title"], "course_display_title"
            )
        elif expected_course_key in normalized_titles:
            scoped["verified_course_title"] = normalized_titles[expected_course_key]
        course_prior = None
        if isinstance(prior, dict):
            per_course = prior.get("courses", {})
            if isinstance(per_course, dict):
                course_prior = per_course.get(str(spec.course_id))
        plan = build_projection(snapshot, course_prior, scoped, observed_on=observed_on)
        course_plans[str(spec.course_id)] = plan
        rows.extend(plan.get("rows", []))
        conflicts.extend(plan.get("conflicts", []))
    views = [
        {"name": "To DO", "source": DATASOURCE_IDENTITY, "filter": "내 상태 != 완료 AND (유형 IN (과제, 할 일) OR 유형 empty)"},
        {"name": "캘린더", "source": DATASOURCE_IDENTITY, "filter": "날짜 IS NOT EMPTY"},
    ]
    for spec in registry.academic_courses:
        prefix = f"{spec.origin}/courses/{spec.course_id}/assignments/"
        views.append({"name": f"과목 필터 · {spec.course_id}", "identity": f"course:{_course_url(spec.origin, spec.course_id)}", "source": DATASOURCE_IDENTITY, "filter": f"LMS 키 STARTS WITH {prefix}"})
    return {
        "status": "complete" if not conflicts else "conflict",
        "apply_ready": not conflicts and semester_snapshot.get("apply_ready") is True,
        "semester": registry.semester,
        "registry_hash": semester_snapshot.get("registry_hash"),
        "scope_hash": semester_snapshot.get("scope_hash"),
        "owner_id": owner_id,
        "observed_on": observed_on,
        "datasource_count": 1,
        "datasource": {"identity": DATASOURCE_IDENTITY, "semester": registry.semester, "schema": list(SCHEMA_FIELDS), "views": views},
        "courses": course_plans,
        "rows": rows,
        "conflicts": conflicts,
        "recovery_components": ["datasource", "view:To DO", "view:캘린더"]
        + [
            component
            for spec in registry.academic_courses
            for component in (
                f"view:course:{_course_url(spec.origin, spec.course_id)}",
                f"course_page:{_course_url(spec.origin, spec.course_id)}",
                f"source_region:{_course_url(spec.origin, spec.course_id)}",
            )
        ],
    }


def bind_bootstrap(registry_path: str, candidate_path: str) -> dict[str, Any]:
    """Bind one identity-only course candidate through a private atomic update."""
    registry_file = Path(registry_path)
    if registry_file.is_symlink() or not registry_file.is_file():
        raise SyncError("registry_path_invalid")
    try:
        info = registry_file.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise SyncError("registry_permissions_invalid")
    except SyncError:
        raise
    except OSError as exc:
        raise SyncError("registry_path_invalid") from exc
    registry_document = _read_json(registry_path)
    registry = registry_from_document(registry_document)
    candidate_document = _read_json(candidate_path)
    candidates = candidate_document.get("candidates") if isinstance(candidate_document, dict) else None
    if not isinstance(candidates, dict) or len(candidates) != 1:
        raise SyncError("bootstrap_candidate_invalid")
    candidate = next(iter(candidates.values()))
    if not isinstance(candidate, dict) or candidate.get("status") != "needs_verification":
        raise SyncError("bootstrap_candidate_invalid")
    identity = candidate.get("course")
    if not isinstance(identity, dict):
        raise SyncError("bootstrap_candidate_invalid")
    candidate_id, candidate_name, candidate_term, candidate_code = (
        identity.get("id"), identity.get("name"), identity.get("term"), identity.get("course_code")
    )
    if type(candidate_id) is not int or candidate_id <= 0 or not all(isinstance(v, str) and v for v in (candidate_name, candidate_term, candidate_code)):
        raise SyncError("bootstrap_candidate_invalid")
    matches = [course for course in registry.courses if course.course_id == candidate_id]
    if len(matches) != 1:
        raise SyncError("bootstrap_binding_mismatch")
    target = matches[0]
    if target.expected_code is not None or target.origin != ORIGIN or target.expected_name != candidate_name or target.expected_term != candidate_term:
        raise SyncError("bootstrap_binding_mismatch")
    updated = [
        CourseSpec(
            course_id=course.course_id,
            expected_name=course.expected_name,
            expected_code=candidate_code if course.course_id == candidate_id else course.expected_code,
            expected_term=course.expected_term,
            origin=course.origin,
            verification_state="api_code_verified" if course.course_id == candidate_id else course.verification_state,
            academic_import=course.academic_import,
            module_positions=course.module_positions,
        )
        for course in registry.courses
    ]
    document = SemesterCourseRegistry(registry.semester, tuple(updated)).canonical_dict()
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{registry_file.name}.", dir=registry_file.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(temporary_name, registry_file)
        directory_fd = os.open(registry_file.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SyncError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise SyncError("registry_update_failed") from exc
    rebound = registry_from_document(document)
    return {"status": "bound", "course_id": candidate_id, "registry_hash": sha256(_canonical_bytes(rebound.canonical_dict())).hexdigest()}


def _escape_notion_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r", " ").replace("\n", " ")
    normalized = " ".join(normalized.split())
    normalized = normalized.replace("\\", "\\\\")
    return re.sub(r"([`*_\[\]()<>#|~])", r"\\\1", normalized)


def _posted_date_kst(record: dict[str, Any]) -> str:
    for field in ("posted_at", "published_at", "delayed_post_at"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(KST).date().isoformat()
        return parsed.date().isoformat()
    return "날짜 미상"


def _normalize_notion_readback(value: Any) -> str:
    if not isinstance(value, str):
        raise SyncError("invalid_notion_readback")
    value = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def render_source_region(
    snapshot: CanonicalSnapshot,
    announcements: list[dict[str, Any]] | None = None,
) -> str:
    payload = snapshot.payload
    lines = [
        "## 수집한 공지",
        "현재까지 수집한 공지입니다. 게시판 전체 기록 수집은 연결 전입니다.",
    ]
    notice_entries = announcements
    if notice_entries is None:
        notice_entries = [
            {"record": announcement, "seen_in_window": True, "last_seen": None}
            for announcement in payload["announcements"]
        ]
    for entry in notice_entries:
        announcement = entry["record"]
        line = (
            f"- {_posted_date_kst(announcement)} · "
            f"[{_escape_notion_text(announcement['title'])}]({announcement['lms_url']})"
        )
        if not entry.get("seen_in_window", True):
            last_seen = entry.get("last_seen")
            line += f" · 이전 확인: {_escape_notion_text(last_seen) if isinstance(last_seen, str) else '날짜 미상'}"
        lines.append(line)
    lines.extend(
        [
            "",
            "## 주차별 자료",
            "각 항목을 누르면 학교 LMS에서 열립니다. 원본 파일 저장이 확인되면 파일 확인에 연결합니다.",
        ]
    )
    for module in payload["modules"]:
        lines.extend(["<details>", f"<summary>{_escape_notion_text(module['name'])}</summary>"])
        if module["items"]:
            for item in module["items"]:
                lines.append(
                    f"\t- [{_escape_notion_text(item['title'])}]({item['lms_url']})"
                )
        else:
            lines.append("\t- 등록된 자료 없음 · 수집 시점 기준")
        lines.append("</details>")
    return "\n".join(lines) + "\n"


def render_markdown(
    snapshot: CanonicalSnapshot,
    announcements: list[dict[str, Any]] | None = None,
) -> str:
    payload = snapshot.payload
    course = payload["course"]
    lines = [
        f"# {_escape_notion_text(course['name'])}",
        "",
        "## LMS 원문",
        f"[과목 열기]({course['lms_url']})",
        "",
        "## 과제",
        "과제는 2026-2 일정 datasource의 과목 필터 linked view로 표시합니다.",
        "",
    ]
    lines.append(render_source_region(snapshot, announcements).rstrip("\n"))
    lines.extend(
        [
            "",
            "## 학습 세션",
            "확인된 학습 세션 없음.",
            "",
            "## 내 메모",
            "<!-- USER-owned; preserve -->",
            "",
        ]
    )
    return "\n".join(lines)


def _readback_value(row: Any, key: str) -> Any:
    if not isinstance(row, dict):
        return None
    if key in row:
        return row[key]
    properties = row.get("properties")
    if isinstance(properties, dict):
        return properties.get(key)
    return None


def _readback_lms_key(row: Any) -> Any:
    value = _readback_value(row, "LMS 키")
    if value is not None:
        return value
    if isinstance(row, dict) and isinstance(row.get("source_values"), dict):
        return row["source_values"].get("LMS 키")
    return None


def _normalize_readback_key(value: Any, expected: str) -> str | None:
    if value == expected:
        return expected
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\[([^\]\r\n]+)\]\(([^()\r\n]+)\)", value)
    if match is not None and match.group(1) == expected and match.group(2) == expected:
        return expected
    return None


def _canonical_assignment_binding(value: Any) -> str | None:
    """Return a validated assignment URL independent of the current course."""
    if not isinstance(value, str):
        return None
    candidate = value
    match = re.fullmatch(r"\[([^\]\r\n]+)\]\(([^()\r\n]+)\)", value)
    if match is not None:
        if match.group(1) != match.group(2):
            return None
        candidate = match.group(1)
    match = re.fullmatch(r"https://canvas\.knu\.ac\.kr/courses/([1-9][0-9]*)/assignments/([1-9][0-9]*)", candidate)
    if match is None:
        return None
    return candidate


def _assignment_binding_course_id(value: str) -> int:
    match = re.fullmatch(
        r"https://canvas\.knu\.ac\.kr/courses/([1-9][0-9]*)/assignments/[1-9][0-9]*",
        value,
    )
    if match is None:
        raise SyncError("invalid_source_binding")
    return int(match.group(1))


def _source_values_from_row(row: dict[str, Any], expected_key: str) -> dict[str, Any] | None:
    source_values = row.get("source_values")
    properties = row.get("properties")
    values: Any
    if isinstance(source_values, dict) and isinstance(properties, dict):
        values = {**source_values, **properties}
    elif isinstance(properties, dict):
        values = properties
    else:
        values = source_values
    if not isinstance(values, dict):
        return None
    normalized: dict[str, Any] = {}
    for field in ("이름", "과목", "유형", "날짜", "LMS 원문 URL", "LMS 키", "수집 범위"):
        if field == "날짜" and field not in values:
            normalized[field] = None
            continue
        if field not in values:
            return None
        value = values[field]
        if field == "LMS 키":
            value = _normalize_readback_key(value, expected_key)
            if value is None:
                return None
        elif field == "LMS 원문 URL" and value != expected_key:
            return None
        normalized[field] = value
    return normalized


def _row_plan(
    assignment: dict[str, Any],
    readback_rows: Any,
    course_name: str,
    *,
    expected_parent: str,
    missing_bindings: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(readback_rows, list):
        raise SyncError("invalid_notion_readback")
    key = assignment["source_key"]
    exact = [row for row in readback_rows if _normalize_readback_key(_readback_lms_key(row), key) == key]
    current_course_id = _assignment_binding_course_id(key)
    same_title = [
        row
        for row in readback_rows
        if _readback_value(row, "이름") == assignment["name"]
        and (
            (binding := _canonical_assignment_binding(_readback_lms_key(row))) is None
            or _assignment_binding_course_id(binding) == current_course_id
        )
    ]
    source_fields = {
        "이름": assignment["name"],
        "과목": course_name,
        "유형": "과제",
        "날짜": assignment["due_at"],
        "LMS 원문 URL": assignment["lms_url"],
        "LMS 키": assignment["source_key"],
            "수집 범위": "과제 메타데이터 · LMS 제출 상태 미수집",
    }
    source_hash = sha256(_canonical_bytes(source_fields)).hexdigest()
    if len(exact) > 1:
        operation = "conflict"
        reason = "duplicate_source_key"
    elif len(exact) == 1:
        row = exact[0]
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            operation = "conflict"
            reason = "known_binding_missing_id"
        elif row.get("parent_datasource_id") != expected_parent:
            operation = "conflict"
            reason = "parent_mismatch"
        else:
            current_values = _source_values_from_row(row, key)
            prior_values = row.get("last_applied_source_values")
            prior_hash = row.get("last_applied_source_hash")
            if not isinstance(current_values, dict) or not isinstance(prior_values, dict):
                operation = "conflict"
                reason = "prior_source_binding_missing"
            elif not isinstance(prior_hash, str) or prior_hash != sha256(_canonical_bytes(prior_values)).hexdigest():
                operation = "conflict"
                reason = "prior_source_hash_invalid"
            elif current_values != prior_values:
                operation = "conflict"
                reason = "source_user_edit_conflict"
            elif current_values == source_fields:
                operation = "noop"
                reason = None
            else:
                operation = "update_source_preserve_user"
                reason = None
    elif missing_bindings is not None and key in missing_bindings:
        operation = "conflict"
        reason = "known_binding_missing"
    elif any(
        isinstance(row, dict)
        and isinstance(_readback_lms_key(row), str)
        and key in _readback_lms_key(row)
        for row in readback_rows
    ):
        operation = "conflict"
        reason = "source_key_readback_mismatch"
    elif same_title:
        operation = "conflict"
        reason = "title_only_candidate"
    elif any(
        isinstance(row, dict)
        and row.get("source_identity") == assignment["source_identity"]
        for row in readback_rows
    ):
        operation = "conflict"
        reason = "missing_source_binding"
    else:
        operation = "create"
        reason = None
    result: dict[str, Any] = {
        "operation": operation,
        "source_key": key,
        "source_fields": source_fields,
        "source_hash": source_hash,
        "user_fields": {"내 상태": "preserve", "내 메모": "preserve"},
    }
    if operation == "create":
        result["initial_user_fields"] = {"내 상태": "확인 전", "내 메모": ""}
    if reason is not None:
        result["reason"] = reason
    return result


def _announcement_projection(
    current: list[dict[str, Any]],
    prior: Any,
    observed_on: str | None,
    *,
    origin: str,
    course_id: int,
) -> list[dict[str, Any]]:
    if prior is None:
        prior_records: list[Any] = []
    elif isinstance(prior, list):
        prior_records = prior
    else:
        raise SyncError("invalid_prior_state")
    prior_by_key: dict[str, dict[str, Any]] = {}
    for entry in prior_records:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_key"), str):
            raise SyncError("invalid_prior_state")
        record = entry.get("record")
        if not isinstance(record, dict):
            raise SyncError("invalid_prior_state")
        _walk_reject_forbidden(record)
        announcement_id = _positive_int(record.get("id"), "announcement_id")
        expected_url = _announcement_url(origin, course_id, announcement_id)
        if (
            record.get("source_key") != expected_url
            or record.get("lms_url") != expected_url
            or entry["source_key"] != expected_url
            or record.get("context_code") != f"course_{course_id}"
            or record.get("source_identity")
            != _source_identity(origin, course_id, "announcement", announcement_id)
            or not isinstance(record.get("title"), str)
        ):
            raise SyncError("invalid_prior_state")
        if entry["source_key"] in prior_by_key:
            raise SyncError("duplicate_prior_source_key")
        prior_by_key[entry["source_key"]] = entry
    result: dict[str, dict[str, Any]] = {}
    for record in current:
        key = record["source_key"]
        previous = prior_by_key.get(key, {})
        last_seen = observed_on if observed_on is not None else previous.get("last_seen")
        result[key] = {
            "source_key": key,
            "record": record,
            "last_seen": last_seen,
            "seen_in_window": True,
        }
    for key, previous in prior_by_key.items():
        if key not in result:
            prior_record: Any = previous.get("record")
            if not isinstance(prior_record, dict):
                raise SyncError("invalid_prior_state")
            result[key] = {
                "source_key": key,
                "record": prior_record,
                "last_seen": previous.get("last_seen"),
                "seen_in_window": False,
            }
    return [result[key] for key in sorted(result)]


def _course_page_plan(
    snapshot: CanonicalSnapshot,
    readback: Any,
    source_region: str,
    page_template: str,
    display_title: str,
) -> dict[str, Any]:
    if not isinstance(readback, dict):
        raise SyncError("invalid_notion_readback")
    pages = readback.get("course_pages", [])
    if not isinstance(pages, list):
        raise SyncError("invalid_notion_readback")
    course_key = snapshot.payload["course"]["source_key"]
    exact = [page for page in pages if _readback_value(page, "source_key") == course_key]
    title = snapshot.payload["course"]["name"]
    same_titles = {title, display_title}
    same_title = [page for page in pages if _readback_value(page, "title") in same_titles]
    if len(exact) > 1:
        return {"operation": "conflict", "reason": "duplicate_course_binding", "source_key": course_key}
    if not exact:
        if isinstance(readback.get("missing_bindings"), list) and course_key in readback["missing_bindings"]:
            return {"operation": "conflict", "reason": "known_binding_missing", "source_key": course_key}
        if same_title:
            return {"operation": "conflict", "reason": "title_only_candidate", "source_key": course_key}
        if readback.get("course_parent_verified") is not True or readback.get("private_root_verified") is not True:
            return {"operation": "conflict", "reason": "parent_or_privacy_unverified", "source_key": course_key}
        return {
            "operation": "create",
            "source_key": course_key,
            "title": display_title,
            "source_hash": snapshot.snapshot_hash,
            "source_region_hash": sha256(
                _normalize_notion_readback(source_region).encode("utf-8")
            ).hexdigest(),
            "desired_source_region_hash": sha256(
                _normalize_notion_readback(source_region).encode("utf-8")
            ).hexdigest(),
            "source_region": source_region,
            "page_template": page_template,
        }
    page = exact[0]
    if not isinstance(page, dict) or not isinstance(page.get("id"), str) or not page["id"]:
        return {"operation": "conflict", "reason": "course_binding_id_missing", "source_key": course_key}
    expected_parent = readback.get("course_parent_id")
    if not isinstance(expected_parent, str) or page.get("parent_id") != expected_parent:
        return {"operation": "conflict", "reason": "course_parent_mismatch", "source_key": course_key}
    if page.get("privacy") != "private":
        return {"operation": "conflict", "reason": "course_privacy_unverified", "source_key": course_key}
    comments = page.get("comments")
    if not isinstance(comments, list):
        return {"operation": "conflict", "reason": "course_comments_unverified", "source_key": course_key}
    if comments:
        return {"operation": "conflict", "reason": "source_region_comments", "source_key": course_key}
    last_hash = page.get("last_applied_source_hash")
    if not isinstance(last_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", last_hash):
        return {"operation": "conflict", "reason": "prior_source_hash_missing", "source_key": course_key}
    last_desired_hash = page.get("last_applied_desired_region_hash")
    if not isinstance(last_desired_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", last_desired_hash):
        return {
            "operation": "conflict",
            "reason": "prior_desired_source_hash_missing",
            "source_key": course_key,
        }
    current = page.get("source_region")
    if current is None:
        return {"operation": "conflict", "reason": "source_region_missing", "source_key": course_key}
    current_normalized = _normalize_notion_readback(current)
    current_hash = sha256(current_normalized.encode("utf-8")).hexdigest()
    desired_hash = sha256(_normalize_notion_readback(source_region).encode("utf-8")).hexdigest()
    if current_hash != last_hash:
        return {"operation": "conflict", "reason": "source_region_changed", "source_key": course_key}
    if desired_hash == last_desired_hash or current_hash == desired_hash:
        operation = "noop"
    else:
        operation = "replace_source_region"
    return {
        "operation": operation,
        "source_key": course_key,
        "source_hash": snapshot.snapshot_hash,
        "source_region_hash": desired_hash,
        "desired_source_region_hash": desired_hash,
        "source_region": source_region,
        "page_template": page_template,
        "title": display_title,
        "preserve_user_regions": True,
    }


def _bootstrap_page_candidates(readback: dict[str, Any], course_key: str) -> list[dict[str, Any]]:
    pages = readback.get("course_pages")
    if not isinstance(pages, list):
        raise SyncError("invalid_notion_readback")
    return [page for page in pages if isinstance(page, dict) and page.get("source_key") == course_key]


def _bootstrap_page_common_checks(
    page: Any,
    readback: dict[str, Any],
    course_key: str,
    heading: str,
    *,
    allow_managed: bool = False,
) -> tuple[str, str]:
    if not isinstance(page, dict) or not isinstance(page.get("id"), str) or not page["id"]:
        raise SyncError("bootstrap_page_invalid")
    parent = readback.get("course_parent_id")
    if not isinstance(parent, str) or page.get("parent_id") != parent:
        raise SyncError("bootstrap_parent_unverified")
    if readback.get("course_parent_verified") is not True or readback.get("private_root_verified") is not True:
        raise SyncError("bootstrap_parent_unverified")
    if page.get("privacy") != "private":
        raise SyncError("bootstrap_privacy_unverified")
    if page.get("full_body_verified") is not True and page.get("full_body_readback_verified") is not True:
        raise SyncError("bootstrap_body_unverified")
    comments = page.get("comments")
    if not isinstance(comments, list):
        raise SyncError("bootstrap_comments_unverified")
    if comments:
        raise SyncError("bootstrap_comments_present")
    body = page.get("body")
    if not isinstance(body, str) or not body:
        raise SyncError("bootstrap_body_unverified")
    if body.count(heading) != 1:
        raise SyncError("bootstrap_heading_conflict")
    sessions = page.get("learning_sessions")
    if not isinstance(sessions, list):
        raise SyncError("bootstrap_sessions_unverified")
    session_ids: list[str] = []
    for session in sessions:
        if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not session["id"]:
            raise SyncError("bootstrap_sessions_unverified")
        session_ids.append(session["id"])
    if len(set(session_ids)) != len(session_ids):
        raise SyncError("bootstrap_sessions_unverified")
    if not allow_managed and (
        page.get("last_applied_source_hash") is not None
        or page.get("last_applied_desired_region_hash") is not None
        or page.get("source_region") is not None
    ):
        raise SyncError("bootstrap_already_managed")
    return page["id"], body


def _extract_bootstrap_source_region(body: str, source_heading: str, end_heading: str) -> tuple[str, str, str]:
    normalized = _normalize_notion_readback(body)
    if normalized.count(source_heading) != 1 or normalized.count(end_heading) != 1:
        raise SyncError("bootstrap_readback_mismatch")
    start = normalized.index(source_heading)
    end = normalized.index(end_heading)
    if start >= end:
        raise SyncError("bootstrap_readback_mismatch")
    return normalized[:start].rstrip(), normalized[start:end].strip(), normalized[end:].lstrip()


def build_course_page_bootstrap_plan(
    snapshot: CanonicalSnapshot,
    notion_readback: dict[str, Any],
    source_region: str,
    *,
    original_body: str,
    heading: str = "## 학습 세션",
) -> dict[str, Any]:
    """Prepare the additive first adoption of an already verified course page.

    This is deliberately separate from normal managed-region replacement.  It
    emits an append request and no fabricated source hash; the hash is published
    only by :func:`verify_course_page_bootstrap` after semantic readback.
    """
    if not isinstance(notion_readback, dict) or not isinstance(source_region, str) or not source_region:
        raise SyncError("bootstrap_input_invalid")
    course_key = snapshot.payload["course"]["source_key"]
    exact = _bootstrap_page_candidates(notion_readback, course_key)
    if len(exact) != 1:
        if len(exact) > 1:
            raise SyncError("bootstrap_duplicate_binding")
        raise SyncError("bootstrap_binding_required")
    page_id, body = _bootstrap_page_common_checks(exact[0], notion_readback, course_key, heading)
    if not isinstance(original_body, str) or not original_body:
        raise SyncError("bootstrap_input_invalid")
    if _normalize_notion_readback(original_body) != _normalize_notion_readback(body):
        raise SyncError("bootstrap_user_content_changed")
    normalized_region = _normalize_notion_readback(source_region)
    if normalized_region.count("## 수집한 공지") != 1 or heading in normalized_region:
        raise SyncError("bootstrap_region_invalid")
    if "## 수집한 공지" in _normalize_notion_readback(body):
        raise SyncError("bootstrap_heading_conflict")
    return {
        "operation": "bootstrap_append_source_region",
        "source_key": course_key,
        "page_id": page_id,
        "append_before": heading,
        "source_region": source_region,
        "source_region_hash": sha256(normalized_region.encode("utf-8")).hexdigest(),
        "preexisting_body_hash": sha256(_normalize_notion_readback(body).encode("utf-8")).hexdigest(),
        "preserve_user_regions": True,
        "requires_semantic_readback": True,
    }


def verify_course_page_bootstrap(
    plan: Mapping[str, Any],
    notion_readback: dict[str, Any],
    *,
    original_body: str,
) -> dict[str, Any]:
    """Verify one additive bootstrap response and then publish both hashes."""
    if not isinstance(plan, Mapping) or plan.get("operation") != "bootstrap_append_source_region":
        raise SyncError("bootstrap_plan_invalid")
    source_key = plan.get("source_key")
    source_region = plan.get("source_region")
    page_id = plan.get("page_id")
    if not all(isinstance(value, str) and value for value in (source_key, source_region, page_id, original_body)):
        raise SyncError("bootstrap_plan_invalid")
    if not isinstance(source_key, str) or not isinstance(source_region, str) or not isinstance(page_id, str):
        raise SyncError("bootstrap_plan_invalid")
    pages = _bootstrap_page_candidates(notion_readback, source_key)
    if len(pages) != 1 or pages[0].get("id") != page_id:
        raise SyncError("bootstrap_readback_mismatch")
    _page_id, body = _bootstrap_page_common_checks(
        pages[0], notion_readback, source_key, str(plan.get("append_before")), allow_managed=True
    )
    normalized_region = _normalize_notion_readback(source_region)
    original_hash = sha256(_normalize_notion_readback(original_body).encode("utf-8")).hexdigest()
    if plan.get("preexisting_body_hash") != original_hash:
        raise SyncError("bootstrap_user_content_changed")
    prefix, actual_region, suffix = _extract_bootstrap_source_region(
        body, "## 수집한 공지", str(plan.get("append_before"))
    )
    before = _normalize_notion_readback(original_body)
    heading = str(plan.get("append_before"))
    heading_index = before.index(heading)
    original_prefix = before[:heading_index].rstrip()
    original_suffix = before[heading_index:].lstrip()
    if prefix != original_prefix or suffix != original_suffix:
        raise SyncError("bootstrap_user_content_changed")
    if actual_region != normalized_region:
        raise SyncError("bootstrap_readback_mismatch")
    desired_hash = sha256(normalized_region.encode("utf-8")).hexdigest()
    actual_hash = sha256(actual_region.encode("utf-8")).hexdigest()
    return {
        "operation": "bootstrap_verified",
        "source_key": source_key,
        "page_id": page_id,
        "last_applied_source_hash": actual_hash,
        "last_applied_desired_region_hash": desired_hash,
        "source_region_hash": desired_hash,
        "actual_source_region_hash": actual_hash,
        "preserve_user_regions": True,
    }


def build_projection(
    snapshot: CanonicalSnapshot,
    prior: dict[str, Any] | None = None,
    notion_readback: dict[str, Any] | None = None,
    *,
    observed_on: str | None = None,
) -> dict[str, Any]:
    if observed_on is not None:
        try:
            dt.date.fromisoformat(observed_on)
        except ValueError as exc:
            raise SyncError("invalid_observed_on") from exc
    payload = snapshot.payload
    course = payload["course"]
    prefix = f"{payload['origin']}/courses/{course['id']}/assignments/"
    readback = notion_readback or {}
    datasource_readback = readback.get("datasource")
    if datasource_readback is None:
        expected_parent = DATASOURCE_IDENTITY
    elif isinstance(datasource_readback, dict) and isinstance(datasource_readback.get("id"), str):
        expected_parent = datasource_readback["id"]
    else:
        raise SyncError("invalid_notion_readback")
    missing_bindings: set[str] = set()
    for entry in readback.get("missing_bindings", []):
        if isinstance(entry, dict) and isinstance(entry.get("source_key"), str):
            missing_bindings.add(entry["source_key"])
        elif isinstance(entry, str):
            missing_bindings.add(entry)
    display_title = readback.get("verified_course_title")
    if not isinstance(display_title, str):
        bound_pages = readback.get("course_pages", [])
        if isinstance(bound_pages, list):
            bound = [
                page
                for page in bound_pages
                if isinstance(page, dict) and page.get("source_key") == course["source_key"]
            ]
            if len(bound) == 1 and isinstance(bound[0].get("title"), str):
                display_title = bound[0]["title"]
    if not isinstance(display_title, str):
        display_title = course["name"]
    display_title = _label(display_title, "course_display_title")
    rows = [
        _row_plan(
            assignment,
            readback.get("rows", []),
            display_title,
            expected_parent=expected_parent,
            missing_bindings=missing_bindings,
        )
        for assignment in payload["assignments"]
    ]
    prior_announcements = prior.get("announcements", []) if prior is not None else []
    announcements = _announcement_projection(
        payload["announcements"],
        prior_announcements,
        observed_on,
        origin=payload["origin"],
        course_id=course["id"],
    )
    source_region = render_source_region(snapshot, announcements)
    page_template = render_markdown(snapshot, announcements)
    course_page = _course_page_plan(
        snapshot,
        readback,
        source_region,
        page_template,
        display_title,
    )
    conflicts = [item for item in rows if item["operation"] == "conflict"]
    if course_page["operation"] == "conflict":
        conflicts.append(course_page)
    status = "conflict" if conflicts else "complete"
    return {
        "status": status,
        "snapshot_hash": snapshot.snapshot_hash,
        "datasource_count": 1,
        "datasource": {
            "identity": DATASOURCE_IDENTITY,
            "semester": "2026-2",
            "schema": list(SCHEMA_FIELDS),
            "views": [
                {
                    "name": "To DO",
                    "source": DATASOURCE_IDENTITY,
                    "filter": "내 상태 != 완료 AND (유형 IN (과제, 할 일) OR 유형 empty)",
                },
                {
                    "name": "캘린더",
                    "source": DATASOURCE_IDENTITY,
                    "filter": "날짜 IS NOT EMPTY",
                },
                {
                    "name": "과목 필터",
                    "source": DATASOURCE_IDENTITY,
                    "filter": f"LMS 키 STARTS WITH {prefix}",
                },
            ],
        },
        "rows": rows,
        "announcements": announcements,
        "course_page": course_page,
        "course_page_source_region": source_region,
        "course_page_template": page_template,
        "recovery_components": [
            "datasource",
            "view:To DO",
            "view:캘린더",
            "view:과목 필터",
            "course_page",
            "course_page_source_region",
        ],
        "conflicts": conflicts,
    }


class SanitizedParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SyncError("argument_error")


def _parser() -> SanitizedParser:
    parser = SanitizedParser(prog="knu_lms_sync")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=SanitizedParser
    )
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--owner-id", required=True)
    collect_parser.add_argument("--scope-hash", required=True)
    enroll_parser = subparsers.add_parser("enroll")
    enroll_parser.add_argument("--confirm", required=True)
    hold_parser = subparsers.add_parser("hold-lock")
    hold_parser.add_argument("--scope-hash", required=True)
    snapshot = subparsers.add_parser("snapshot")
    _add_config_args(snapshot)
    snapshot.add_argument("--input", required=True)
    snapshot.add_argument("--registry")
    snapshot.add_argument("--owner-id")
    snapshot.add_argument("--scope-hash")
    snapshot.add_argument("--output")
    project = subparsers.add_parser("project")
    project.add_argument("--snapshot", required=True)
    project.add_argument("--readback")
    project.add_argument("--prior")
    project.add_argument("--observed-on")
    project.add_argument("--registry")
    project.add_argument("--owner-id")
    project.add_argument("--scope-hash")
    project.add_argument("--output")
    bind = subparsers.add_parser("bind-bootstrap")
    bind.add_argument("--registry", required=True)
    bind.add_argument("--candidate", required=True)
    return parser


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--course-id", type=int)
    parser.add_argument("--expected-name")
    parser.add_argument("--expected-code")
    parser.add_argument("--expected-term")
    parser.add_argument("--origin", default=ORIGIN)


def _read_json(path_value: str) -> Any:
    try:
        with Path(path_value).open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError("input_unreadable") from exc


def _canonical_from_document(document: Any) -> CanonicalSnapshot:
    if not isinstance(document, dict) or document.get("status") != "complete":
        raise SyncError("invalid_canonical_snapshot")
    payload = document.get("snapshot")
    digest = document.get("snapshot_hash")
    if not isinstance(payload, dict) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SyncError("invalid_canonical_snapshot")
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise SyncError("invalid_canonical_snapshot")
    positions = scope.get("module_positions")
    module_positions: frozenset[int] | None = None
    if positions is not None:
        if not isinstance(positions, list) or any(type(p) is not int or p <= 0 for p in positions):
            raise SyncError("invalid_canonical_snapshot")
        module_positions = frozenset(positions)
    course_id = scope.get("course_id")
    expected_name = scope.get("expected_name")
    expected_code = scope.get("expected_code")
    expected_term = scope.get("expected_term")
    origin = payload.get("origin")
    if type(course_id) is not int or not isinstance(expected_name, str) or not isinstance(expected_code, str) or not isinstance(expected_term, str) or not isinstance(origin, str):
        raise SyncError("invalid_canonical_snapshot")
    config = SyncConfig(course_id, expected_name, expected_code, expected_term, origin, module_positions=module_positions)
    revalidated = validate_snapshot({
        "status": "complete", "course": payload.get("course"), "assignments": payload.get("assignments"),
        "announcements": payload.get("announcements"), "modules": payload.get("modules"),
    }, config)
    if revalidated.snapshot_hash != digest:
        raise SyncError("snapshot_hash_mismatch")
    return revalidated


def _load_canonical(path_value: str) -> CanonicalSnapshot:
    return _canonical_from_document(_read_json(path_value))


def _write_json(stream: TextIO, value: Any) -> None:
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        stream.write(serialized)
        stream.write("\n")
    except (TypeError, ValueError) as exc:
        raise SyncError("output_serialization") from exc


def _write_json_path(path_value: str | None, value: Any, output: TextIO) -> None:
    if path_value is None:
        _write_json(output, value)
        return
    path = Path(path_value)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise SyncError("output_path_invalid")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            _write_json(stream, value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SyncError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise SyncError("output_write_failed") from exc


def main(argv: list[str] | None = None, *, output: TextIO | None = None, error: TextIO | None = None) -> int:
    output_stream = sys.stdout if output is None else output
    error_stream = sys.stderr if error is None else error
    try:
        args = _parser().parse_args(argv)
        if args.command == "hold-lock":
            module = _lock_module()
            try:
                return int(module.hold_cli(RUNTIME_DIR, args.scope_hash, sys.stdin, output_stream))
            except Exception as exc:
                code = getattr(exc, "args", [None])[0]
                if isinstance(code, str) and re.fullmatch(r"[a-z_]+", code):
                    raise SyncError(code) from None
                raise SyncError("reservation_unavailable") from exc
        if args.command == "collect":
            collect_result = collect(owner_id=args.owner_id, scope_hash=args.scope_hash)
            _write_json(output_stream, collect_result.as_dict())
            return 0
        if args.command == "enroll":
            if args.confirm != "ENROLL":
                raise SyncError("authorization_required")
            enroll_result = enroll_keychain(prompt=getpass.getpass, stdin=sys.stdin)
            _write_json(output_stream, enroll_result)
            return 0
        if args.command == "bind-bootstrap":
            bind_result = bind_bootstrap(args.registry, args.candidate)
            _write_json(output_stream, bind_result)
            return 0
        result: dict[str, Any]
        if args.command == "snapshot":
            if args.registry:
                registry = registry_from_document(_read_json(args.registry))
                if args.owner_id is None or args.scope_hash is None:
                    raise SyncError("reservation_binding_mismatch")
                if not re.fullmatch(r"[0-9a-f]{64}", args.scope_hash):
                    raise SyncError("scope_hash_mismatch")
                if args.scope_hash != registry.scope_hash(transport="aside-readonly"):
                    raise SyncError("scope_hash_mismatch")
                _ensure_participant(args.owner_id, args.scope_hash)
                input_value = _read_json(args.input)
                if not isinstance(input_value, dict) or input_value.get("scope_hash") != args.scope_hash:
                    raise SyncError("scope_hash_mismatch")
                result = validate_semester_snapshot(registry, input_value, expected_scope_hash=args.scope_hash)
                result["owner_id"] = args.owner_id
            else:
                if None in (args.course_id, args.expected_name, args.expected_code, args.expected_term):
                    raise SyncError("argument_error")
                config = SyncConfig(
                    course_id=args.course_id,
                    expected_name=args.expected_name,
                    expected_code=args.expected_code,
                    expected_term=args.expected_term,
                    origin=args.origin,
                )
                result = validate_snapshot(_read_json(args.input), config).as_dict()
        else:
            readback = _read_json(args.readback) if args.readback else {}
            prior = _read_json(args.prior) if args.prior else None
            if readback is not None and not isinstance(readback, dict):
                raise SyncError("invalid_notion_readback")
            if prior is not None and not isinstance(prior, dict):
                raise SyncError("invalid_prior_state")
            if args.registry:
                registry = registry_from_document(_read_json(args.registry))
                if args.owner_id is None or args.scope_hash is None or args.observed_on is None or args.prior is None:
                    raise SyncError("semester_runtime_binding_required")
                if not re.fullmatch(r"[0-9a-f]{64}", args.scope_hash):
                    raise SyncError("scope_hash_mismatch")
                if args.scope_hash != registry.scope_hash(transport="aside-readonly"):
                    raise SyncError("scope_hash_mismatch")
                _ensure_participant(args.owner_id, args.scope_hash)
                semester_value = _read_json(args.snapshot)
                if not isinstance(semester_value, dict) or semester_value.get("scope_hash") != args.scope_hash:
                    raise SyncError("scope_hash_mismatch")
                result = build_semester_projection(
                    registry, semester_value, notion_readback=readback, prior=prior,
                    owner_id=args.owner_id, scope_hash=args.scope_hash, observed_on=args.observed_on,
                )
            else:
                canonical_snapshot = _load_canonical(args.snapshot)
                result = build_projection(
                    canonical_snapshot, prior, readback, observed_on=args.observed_on,
                )
        _write_json_path(args.output, result, output_stream)
        return 0 if result.get("status") == "complete" else 3
    except SyncError as exc:
        _write_json(output_stream, {"status": "failed", "error": exc.code})
        error_stream.write(f"sync_error={exc.code}\n")
        return 2
    except Exception:  # noqa: BLE001 - public CLI must never expose input details
        _write_json(output_stream, {"status": "failed", "error": "unexpected_error"})
        error_stream.write("sync_error=unexpected_error\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
