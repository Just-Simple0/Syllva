"""Interactive credential registration: uls credential set NAME.

See docs/plans/credential-secret-file-launcher.md (rev5) section 2.6/7.6/8.6/8.7/8.8
for the full design rationale this implements:

- Only a NAME whose source is ALREADY declared in config.yaml as "file" or
  "keyring" may be set interactively here (no --source flag that silently
  diverts a single write to a different storage than config.yaml declares
  -- that "chicken-and-egg" mismatch was the rev1 defect this design fixed).
- The value is never accepted as a CLI argument (no --value option); only a
  masked interactive prompt (or a clean, explicit refusal when the process
  has no real TTY) ever reads it.
- Nothing here ever prints or logs the raw value, including in "success"
  feedback (length/permissions/storage location only, section 8.5's
  masked-preview leak is fully removed, not narrowed).
"""

from __future__ import annotations

import errno
import getpass
import hmac
import os
import sys
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uls.config._keyring_backend import explicit_os_keyring, write_keyring_credential
from uls.config._secure_file import (
    MAX_SECRET_BYTES,
    read_secure_file,
    secret_file_path,
    write_secure_file,
)
from uls.config.credentials import (
    ALLOWED_SOURCES,
    FILE_BINDINGS,
    KEYRING_BINDINGS,
)
from uls.config.errors import ConfigurationError

# Fixed short error code -> remediation hint. The code itself never carries
# a secret value; neither does the hint.
_REMEDIATION: dict[str, str] = {
    "secret_dir_is_symlink": "보호된 디렉터리 경로의 심볼릭 링크를 제거하십시오.",
    "secret_dir_permissions_too_open": '조치: chmod 700 "{path}"',
    "secret_dir_owner_mismatch": "디렉터리 소유자가 현재 사용자와 다릅니다. 디렉터리를 다시 만드십시오.",
    "secret_file_is_symlink": "심볼릭 링크를 제거하고 다시 실행하십시오.",
    "secret_file_not_regular": "일반 파일이 아닌 항목입니다. 해당 경로를 정리하십시오.",
    "secret_file_too_large": "파일이 너무 큽니다(최대 " + str(MAX_SECRET_BYTES) + "바이트).",
    "secret_file_permissions_too_open": '조치: chmod 600 "{path}"',
    "secret_file_owner_mismatch": "파일 소유자가 현재 사용자와 다릅니다.",
    "secret_value_empty": "빈 값은 저장할 수 없습니다.",
    "secret_value_too_large": "값이 너무 깁니다(최대 " + str(MAX_SECRET_BYTES) + "바이트).",
    "secret_encoding_invalid": "저장된 값이 올바른 UTF-8이 아닙니다.",
    "credential_tty_required": "이 명령은 대화형 터미널에서 실행해야 합니다(파이프/리다이렉트 입력은 지원하지 않습니다).",
    "credential_noecho_unavailable": "이 터미널에서는 입력이 화면에 표시되지 않는 안전한 입력을 보장할 수 없습니다.",
    "credential_contains_control_characters": "개행/제어문자가 포함되어 있습니다. 클립보드 복사 시 개행이 함께 복사되지 않았는지 확인하십시오.",
    "credential_double_entry_mismatch": "두 번 입력한 값이 서로 다릅니다. 다시 시도하십시오.",
    "credential_aborted": "취소되었습니다.",
    "credential_config_changed": "입력 중 config.yaml이 바뀌었습니다. 최신 설정을 확인한 뒤 다시 시도하십시오.",
    "credential_config_unavailable": "config.yaml을 안전하게 확인할 수 없습니다.",
    "credential_peer_unavailable": "반대 목적의 자격 증명을 확인할 수 없습니다. 설정과 보호된 저장소를 확인하십시오.",
    "credential_purpose_conflict": "Retrieval과 worker용 Notion 자격 증명은 서로 달라야 합니다.",
    "credential_store_failed": "저장소 쓰기 또는 정확한 readback 검증에 실패했습니다.",
    "keyring_dependency_missing": "OS keyring 파이썬 패키지가 설치되어 있지 않습니다.",
    "keyring_backend_unavailable": "OS-native keyring 백엔드를 사용할 수 없습니다.",
    "keyring_backend_invalid": "keyring 백엔드 신원 검증에 실패했습니다.",
    "keyring_platform_unsupported": "이 운영체제에서는 keyring 저장을 지원하지 않습니다.",
}


class CredentialSetError(Exception):
    """Fail-closed error for the credential set command; message is a fixed
    short code, never a secret value."""


def _remediation_for(code: str, *, path: Path | None = None) -> str:
    template = _REMEDIATION.get(code, "")
    if path is not None and "{path}" in template:
        return template.format(path=str(path))
    if "{path}" in template:
        return template.replace('"{path}"', "해당 경로")
    return template


@dataclass
class _Existing:
    state: str  # "absent" | "ready" | "untrusted"
    detail: str | None = None


def _is_positive_missing(exc: ConfigurationError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, FileNotFoundError) and cause.errno == errno.ENOENT


def _existing_file_state(path: Path) -> _Existing:
    try:
        read_secure_file(path)
        return _Existing("ready")
    except ConfigurationError as exc:
        problems = exc.details.get("problems") if isinstance(exc.details, dict) else None
        code = problems[0] if isinstance(problems, list) and problems else None
        if code in ("secret_file_missing", "secret_dir_missing") and _is_positive_missing(exc):
            return _Existing("absent")
        return _Existing("untrusted", detail=code)


def _existing_keyring_state(service: str, account: str, *, platform: str | None) -> _Existing:
    try:
        backend = explicit_os_keyring(platform)
    except ConfigurationError as exc:
        problems = exc.details.get("problems") if isinstance(exc.details, dict) else None
        code = problems[0] if isinstance(problems, list) and problems else None
        return _Existing("untrusted", detail=code)
    try:
        value = backend.get_password(service, account)
    except Exception:  # noqa: BLE001 - a broken backend read is untrusted, not absent
        return _Existing("untrusted", detail="keyring_backend_unavailable")
    if value:
        return _Existing("ready")
    return _Existing("absent")


def _read_value_twice() -> bytes:
    if not sys.stdin.isatty():
        raise CredentialSetError("credential_tty_required")
    for attempt in range(3):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                first = getpass.getpass("값 입력 (보안을 위해 입력 내용이 화면에 표시되지 않습니다): ")
                second = getpass.getpass("다시 입력(확인): ")
            except Warning as exc:
                raise CredentialSetError("credential_noecho_unavailable") from exc
            except EOFError:
                raise CredentialSetError("credential_aborted") from None
            except KeyboardInterrupt:
                raise
        if first != second:
            print("두 입력이 일치하지 않습니다. 다시 시도하십시오.", file=sys.stderr)
            continue
        return _validate_value(first)
    raise CredentialSetError("credential_double_entry_mismatch")


def _validate_value(value: str) -> bytes:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise CredentialSetError("credential_contains_control_characters")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise CredentialSetError("secret_encoding_invalid") from None
    if len(encoded) == 0:
        raise CredentialSetError("secret_value_empty")
    if len(encoded) > MAX_SECRET_BYTES:
        raise CredentialSetError("secret_value_too_large")
    return encoded


def _confirm_overwrite(existing: _Existing, *, overwrite_flag: bool) -> None:
    if existing.state == "absent":
        return
    if overwrite_flag:
        return
    if existing.state == "untrusted":
        prompt = ("기존 저장소가 검증에 실패한 상태입니다(사유는 비밀 아닌 오류 코드만 표시: "
                 + str(existing.detail) + "). 그래도 덮어쓰시겠습니까? [y/N]: ")
    else:
        prompt = "기존 값이 있습니다. 덮어쓰시겠습니까? [y/N]: "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        raise CredentialSetError("credential_aborted") from None
    if answer not in ("y", "yes"):
        raise CredentialSetError("credential_aborted")


def _declared_source(config: Any, name: str) -> str | None:
    val = config.credentials.get(name)
    return str(val) if isinstance(val, str) else None


def _runtime_environment() -> Mapping[str, str]:
    return os.environ


def _raw_source(raw: dict[str, Any], name: str) -> str | None:
    credentials = raw.get("credentials", {})
    if not isinstance(credentials, dict) or name not in credentials:
        return None
    entry = credentials[name]
    if isinstance(entry, dict):
        source = entry.get("source")
    else:
        source = entry
    return str(source) if isinstance(source, str) else None


def _read_managed_notion(role: Any, root: Path, platform: str | None) -> bytes | None:
    if role.service:
        try:
            backend = explicit_os_keyring(platform)
            value = backend.get_password(role.service, role.account)
        except Exception:  # noqa: BLE001 - inability to verify a peer store must fail closed
            raise CredentialSetError("credential_peer_unavailable") from None
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise CredentialSetError("credential_peer_unavailable")
        try:
            payload = value.encode("utf-8", errors="strict")
        except (AttributeError, UnicodeEncodeError):
            raise CredentialSetError("credential_peer_unavailable") from None
        if not isinstance(payload, bytes) or not payload:
            raise CredentialSetError("credential_peer_unavailable")
        return payload
    try:
        payload = read_secure_file(Path(role.locator(root)), max_bytes=role.max_bytes)
    except ConfigurationError as exc:
        problems = exc.details.get("problems") if isinstance(exc.details, dict) else None
        if (problems in (["secret_file_missing"], ["secret_dir_missing"])
                and _is_positive_missing(exc)):
            return None
        raise CredentialSetError("credential_peer_unavailable") from None
    except Exception:  # noqa: BLE001 - unsafe and unreadable peer paths share a fixed error
        raise CredentialSetError("credential_peer_unavailable") from None
    if not isinstance(payload, bytes) or not payload:
        raise CredentialSetError("credential_peer_unavailable")
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise CredentialSetError("credential_peer_unavailable") from None
    return payload


def _notion_peer_values(peer: Any, raw: dict[str, Any], root: Path, platform: str | None) -> list[bytes]:
    source = _raw_source(raw, peer.name)
    declared = source is not None
    if source is None:
        source = "environment"
    managed = _read_managed_notion(peer, root, platform)
    if source == "environment":
        value = _runtime_environment().get(peer.name)
        if value is None:
            if declared:
                raise CredentialSetError("credential_peer_unavailable")
            effective = None
        elif not value:
            raise CredentialSetError("credential_peer_unavailable")
        else:
            try:
                effective = value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise CredentialSetError("credential_peer_unavailable") from None
    elif source in {"file", "keyring"}:
        effective = managed
        if effective is None:
            raise CredentialSetError("credential_peer_unavailable")
    else:
        raise CredentialSetError("credential_peer_unavailable")
    return [item for item in (effective, managed) if item is not None]


def _notion_cli(config: Any, config_path: Path, name: str, overwrite: bool,
                platform: str | None) -> dict[str, Any]:
    from uls.config.mutation import ConfigFileLock, read_config_bytes
    from uls.settings.config_service import ConfigStore
    from uls.settings.credential_admission import credential_pair_admission
    from uls.settings.credential_roles import ROLES
    from uls.settings.journal import JournalError, JournalStore

    store = ConfigStore(config_path)
    try:
        initial = store.load()
    except Exception:  # noqa: BLE001 - config parsing errors must not expose paths or parser details
        raise CredentialSetError("credential_config_unavailable") from None
    declared = _raw_source(initial.raw, name)
    if declared is None or declared == "environment":
        return _guidance_for_undeclared(initial.config, config_path, name)
    if declared not in {"file", "keyring"}:
        raise CredentialSetError("credential_source_unsupported")
    if not sys.stdin.isatty():
        raise CredentialSetError("credential_tty_required")

    role = next(item for item in ROLES.values() if item.provider == "notion" and item.name == name)
    peer = ROLES[f"notion-{'worker' if role.purpose == 'mcp' else 'mcp'}"]
    root = secret_file_path("settings_state.key").parent
    locators = [ROLES["notion-mcp"].locator(root), ROLES["notion-worker"].locator(root)]
    journal = JournalStore(initial.config.system.workspace_dir, credential_root=root)
    initial_binding = store.binding()

    try:
        with credential_pair_admission(root, "notion", role.profile, locators,
                                       journal=journal, config_path=config_path):
            with ConfigFileLock(config_path):
                try:
                    before_prompt = store._parse(read_config_bytes(config_path))
                except Exception:  # noqa: BLE001 - changed or unreadable config aborts before writes
                    raise CredentialSetError("credential_config_changed") from None
                if (before_prompt.generation != initial.generation or store.binding() != initial_binding
                        or _raw_source(before_prompt.raw, name) != declared):
                    raise CredentialSetError("credential_config_changed")

            if declared == "file":
                path = secret_file_path(FILE_BINDINGS[name])
                _confirm_overwrite(_existing_file_state(path), overwrite_flag=overwrite)
            else:
                service, account = KEYRING_BINDINGS[name]
                _confirm_overwrite(_existing_keyring_state(service, account, platform=platform),
                                   overwrite_flag=overwrite)
            value_bytes = _read_value_twice()

            with ConfigFileLock(config_path):
                try:
                    current = store._parse(read_config_bytes(config_path))
                except Exception:  # noqa: BLE001 - changed or unreadable config aborts before writes
                    raise CredentialSetError("credential_config_changed") from None
                if (current.generation != initial.generation or store.binding() != initial_binding
                        or _raw_source(current.raw, name) != declared):
                    raise CredentialSetError("credential_config_changed")
                peer_values = _notion_peer_values(peer, current.raw, root, platform)
                if any(hmac.compare_digest(value_bytes, old) for old in peer_values):
                    raise CredentialSetError("credential_purpose_conflict")

                try:
                    if declared == "file":
                        path = secret_file_path(FILE_BINDINGS[name])
                        write_secure_file(path, value_bytes)
                        stored = read_secure_file(path)
                        if not hmac.compare_digest(stored, value_bytes):
                            raise CredentialSetError("credential_store_failed")
                        mode = path.stat().st_mode & 0o777
                        result = {
                            "status": "ready", "name": name, "source": "file", "path": str(path),
                            "permissions": oct(mode), "length_bytes": len(value_bytes),
                        }
                    else:
                        service, account = KEYRING_BINDINGS[name]
                        backend = explicit_os_keyring(platform)
                        write_keyring_credential(service, account, value_bytes.decode("utf-8"), backend=backend)
                        readback = backend.get_password(service, account)
                        if not isinstance(readback, str) or not hmac.compare_digest(
                                readback.encode("utf-8", errors="strict"), value_bytes):
                            raise CredentialSetError("credential_store_failed")
                        result = {"status": "ready", "name": name, "source": "keyring",
                                  "length_bytes": len(value_bytes)}
                except CredentialSetError:
                    raise
                except Exception:  # noqa: BLE001 - backend/write errors use one fixed redacted code
                    raise CredentialSetError("credential_store_failed") from None
            return result
    except CredentialSetError:
        raise
    except JournalError as exc:
        raise CredentialSetError(exc.code) from None
    except Exception:  # noqa: BLE001 - unexpected config/admission errors stay redacted
        raise CredentialSetError("credential_config_unavailable") from None


def _guidance_for_undeclared(config: Any, config_path: Path, name: str) -> dict[str, Any]:
    allowed = sorted(ALLOWED_SOURCES.get(name, frozenset()))
    persistent = [source for source in allowed if source != "environment"]
    absolute_config = str(config_path.resolve())
    if not persistent:
        return {
            "status": "guidance_only",
            "name": name,
            "message": name + "는 영구 저장소를 지원하지 않는 environment 전용 값입니다.",
            "action": "export " + name + "=<value>",
        }
    has_section = bool(config.credentials)
    chosen = persistent[0]
    if has_section:
        snippet = "  " + name + ":\n    source: " + chosen + "\n"
    else:
        snippet = "credentials:\n  " + name + ":\n    source: " + chosen + "\n"
    return {
        "status": "guidance_only",
        "name": name,
        "config_path": absolute_config,
        "message": name + "의 저장소가 config.yaml에 선언되어 있지 않습니다(현재: environment).",
        "add_to_config": snippet,
        "allowed_sources": persistent,
    }


def _guidance_for_google_path(config_path: Path, name: str) -> dict[str, Any]:
    field = "google_worker_credentials_path" if name == "GOOGLE_WORKER_CREDENTIALS_FILE" else "google_mcp_credentials_path"
    return {
        "status": "guidance_only",
        "name": name,
        "config_path": str(config_path.resolve()),
        "message": name + "은 secret이 아니라 서비스 계정 키 경로입니다.",
        "unattended": field + ': "<absolute-path-to-service-account.json>"  # in config.yaml',
        "interactive": "export " + name + "=<absolute-path-to-service-account.json>",
    }


def run(*, config: Any, config_path: Path, name: str, overwrite: bool,
       platform: str | None = None) -> dict[str, Any]:
    if name not in ALLOWED_SOURCES:
        raise CredentialSetError("credential_unknown_name")
    if name == "CANVAS_PAT":
        raise CredentialSetError("credential_settings_only")

    from uls.config.credentials import GOOGLE_CREDENTIAL_PATH_NAMES

    if name in GOOGLE_CREDENTIAL_PATH_NAMES:
        return _guidance_for_google_path(config_path, name)

    if name in {"NOTION_MCP_TOKEN", "NOTION_WORKER_TOKEN"}:
        return _notion_cli(config, config_path, name, overwrite, platform)

    declared = _declared_source(config, name)
    if declared is None or declared == "environment":
        return _guidance_for_undeclared(config, config_path, name)

    # Fail closed on non-TTY stdin before issuing any interactive prompt
    # (_confirm_overwrite included), so piped secrets cannot be consumed as y/N responses.
    if not sys.stdin.isatty():
        raise CredentialSetError("credential_tty_required")

    from uls.settings.credential_admission import credential_admission
    from uls.settings.journal import JournalError, JournalStore
    root = secret_file_path("settings_state.key").parent
    if declared == "file":
        locator = str(secret_file_path(FILE_BINDINGS[name]).resolve())
    else:
        service, account = KEYRING_BINDINGS[name]
        locator = f"keyring:{service}/{account}"
    journal = JournalStore(config.system.workspace_dir, credential_root=root)
    try:
        with credential_admission(root, locator, journal=journal, config_path=config_path):
            return _write_declared(name, declared, overwrite, platform)
    except JournalError as exc:
        raise CredentialSetError(exc.code) from None


def _write_declared(name: str, declared: str, overwrite: bool, platform: str | None) -> dict[str, Any]:

    if declared == "file":
        path = secret_file_path(FILE_BINDINGS[name])
        existing = _existing_file_state(path)
        _confirm_overwrite(existing, overwrite_flag=overwrite)
        value_bytes = _read_value_twice()
        write_secure_file(path, value_bytes)
        read_secure_file(path)  # re-verify immediately, fail-closed if the write is somehow untrusted
        mode = path.stat().st_mode & 0o777
        return {
            "status": "ready",
            "name": name,
            "source": "file",
            "path": str(path),
            "permissions": oct(mode),
            "length_bytes": len(value_bytes),
        }

    # declared == "keyring"
    service, account = KEYRING_BINDINGS[name]
    existing = _existing_keyring_state(service, account, platform=platform)
    _confirm_overwrite(existing, overwrite_flag=overwrite)
    value_bytes = _read_value_twice()
    backend = explicit_os_keyring(platform)
    write_keyring_credential(service, account, value_bytes.decode("utf-8"), backend=backend)
    return {
        "status": "ready",
        "name": name,
        "source": "keyring",
        "length_bytes": len(value_bytes),
    }
