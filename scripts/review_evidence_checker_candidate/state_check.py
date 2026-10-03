#!/usr/bin/env python3
"""Check task-record consistency without granting trust or approval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import urllib.parse
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

HEX256 = re.compile(r"^[0-9a-f]{64}$")
SECRET_PART = re.compile(
    r"(?:^|[._-])(?:secret|secrets|credential|credentials|auth|cookie|cookies|token|tokens|private[-_]?key|private[-_]?keys)(?:$|[._-])",
    re.IGNORECASE,
)
MAX_JSON_BYTES = 8 * 1024 * 1024
PINNED_SOURCE_MAX_BYTES = 1024 * 1024
PINNED_SOURCE_ROOT = "/Users/admin/Project/Syllva"
PINNED_SOURCE_ROOT_DEVICE = 16777230
PINNED_SOURCE_ROOT_INODE = 22556999
PINNED_SOURCE_PINS = MappingProxyType(
    {
        "src/uls/cli/credential_set.py": (
            "cada62beb0c0faacf34ed64e44eebb1cf24b6d6574380d0291943d709bc09e09",
            22654,
        ),
        "src/uls/config/credentials.py": (
            "ecbe398f52b4edf4267b07e368ae4e1a3413e0aa577dee0eac2fc4ae8cfefa86",
            20347,
        ),
        "src/uls/settings/credential_admission.py": (
            "9b72fa7d3ff333f643c203f064025f2a5e5edfe9dfc27d6d53ee2a5c5e03da87",
            29151,
        ),
        "src/uls/settings/credential_roles.py": (
            "f9f3ea23807747cc42ad7c08a9947ac255942619660218695e0e84eb58d763c9",
            4222,
        ),
        "src/uls/settings/credential_stores.py": (
            "6ec843bcf25ef2046d3a580180468e658f923a816760c89a55eda7c5c7b88896",
            5608,
        ),
        "src/uls/settings/credential_service.py": (
            "d70bf1b5fcb63cb7d4c2f774a675be1b1d617b46a60bb4f932d3a7c831c62852",
            28058,
        ),
        "tests/unit/test_credential_set_cli.py": (
            "a36d47e6cb0e66dd341512cd0833416e56de85cab42900e439dcfa130b6a61d1",
            31404,
        ),
        "tests/contract/test_settings_credential_service.py": (
            "1eeae0b30657492c8d67a829acf41f16398075f9527c891854b6af4dff4520e2",
            38729,
        ),
        "tests/contract/test_settings_credential_admission.py": (
            "2a06ec2fc45494d89f84cf519cf619c1b738a7bee15ce168d23aefa7fe0d4bae",
            32402,
        ),
    }
)


class InvalidRecord(Exception):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidRecord


def _safe_relative(value: Any) -> PurePosixPath:
    _require(isinstance(value, str) and bool(value) and "\\" not in value)
    _require("\x00" not in value)
    _require(not value.startswith(("/", "~")) and not re.match(r"^[A-Za-z]:", value))
    ref = PurePosixPath(value)
    _require(str(ref) == value)
    _require(not ref.is_absolute() and all(part not in {"", ".", ".."} for part in ref.parts))
    for part in ref.parts:
        _require(part.lower() != ".env" and not part.lower().startswith(".env."))
        _require(SECRET_PART.search(part) is None)
        _require(not part.lower().endswith((".pem", ".p12", ".pfx", ".key")))
    return ref


def _read_relative(root: Path, value: Any, *, size_limit: int | None = None) -> bytes:
    ref = _safe_relative(value)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    current_fd = -1
    file_fd = -1
    try:
        current_fd = os.open(root, os.O_RDONLY | directory_flag | nofollow_flag)
        for part in ref.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(
            ref.parts[-1], os.O_RDONLY | nofollow_flag, dir_fd=current_fd
        )
        info = os.fstat(file_fd)
        _require(stat.S_ISREG(info.st_mode))
        if size_limit is not None:
            _require(info.st_size <= size_limit)
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = -1
            data = stream.read((size_limit + 1) if size_limit is not None else -1)
        _require(size_limit is None or len(data) <= size_limit)
        return data
    except OSError as exc:
        raise InvalidRecord from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if current_fd >= 0:
            os.close(current_fd)


def _pinned_source_components(value: Any) -> tuple[PurePosixPath, str, int]:
    _require(isinstance(value, str) and value in PINNED_SOURCE_PINS)
    _require("\x00" not in value and "\\" not in value)
    _require(not value.startswith(("/", "~")) and not re.match(r"^[A-Za-z]:", value))
    ref = PurePosixPath(value)
    _require(str(ref) == value and not ref.is_absolute())
    _require(all(part not in {"", ".", ".."} for part in ref.parts))
    _require(ref.suffix == ".py" and bool(ref.parts))
    for part in ref.parts[:-1]:
        lower = part.lower()
        _require(lower != ".env" and not lower.startswith(".env."))
        _require(SECRET_PART.search(part) is None)
        _require(not lower.endswith((".pem", ".p12", ".pfx", ".key")))
    basename = ref.parts[-1]
    lower_basename = basename.lower()
    _require(lower_basename != ".env" and not lower_basename.startswith(".env."))
    _require(not lower_basename.endswith((".pem", ".p12", ".pfx", ".key")))
    digest, size = PINNED_SOURCE_PINS[value]
    _require(HEX256.fullmatch(digest) is not None)
    _require(type(size) is int and 0 < size <= PINNED_SOURCE_MAX_BYTES)
    return ref, digest, size


def _pinned_open_flags() -> tuple[int, int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    _require(os.name == "posix")
    _require(type(nofollow) is int and nofollow != 0)
    _require(type(directory) is int and directory != 0)
    _require(type(nonblock) is int and nonblock != 0)
    _require(os.open in getattr(os, "supports_dir_fd", set()))
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | directory | nofollow | nonblock | cloexec
    file_flags = os.O_RDONLY | nofollow | nonblock | cloexec
    return directory_flags, file_flags, nofollow


def _open_pinned_root(root: Path, directory_flags: int) -> int:
    _require(str(root) == PINNED_SOURCE_ROOT)
    opened: list[int] = []
    current_fd = -1
    try:
        current_fd = os.open(os.sep, directory_flags)
        opened.append(current_fd)
        for part in Path(PINNED_SOURCE_ROOT).parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            opened.append(next_fd)
            os.close(current_fd)
            opened[-2] = -1
            current_fd = next_fd
        info = os.fstat(current_fd)
        _require(stat.S_ISDIR(info.st_mode))
        _require(info.st_dev == PINNED_SOURCE_ROOT_DEVICE)
        _require(info.st_ino == PINNED_SOURCE_ROOT_INODE)
        opened[-1] = -1
        result = current_fd
        current_fd = -1
        return result
    except InvalidRecord:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise InvalidRecord from exc
    finally:
        if current_fd >= 0:
            try:
                os.close(current_fd)
            except OSError:
                pass
        for fd in reversed(opened):
            if fd >= 0 and fd != current_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _pinned_stat_signature(info: os.stat_result) -> tuple[int, ...]:
    try:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    except AttributeError as exc:
        raise InvalidRecord from exc


def _read_pinned_source(root: Path, value: Any, record_digest: Any) -> bytes:
    ref, compiled_digest, expected_size = _pinned_source_components(value)
    _require(isinstance(record_digest, str) and HEX256.fullmatch(record_digest) is not None)
    _require(record_digest == compiled_digest)
    directory_flags, file_flags, _nofollow = _pinned_open_flags()

    descriptors: list[int] = []
    try:
        root_fd = _open_pinned_root(root, directory_flags)
        descriptors.append(root_fd)
        parent_fd = root_fd
        for part in ref.parts[:-1]:
            parent_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            descriptors.append(parent_fd)
            _require(stat.S_ISDIR(os.fstat(parent_fd).st_mode))

        file_fd = os.open(ref.parts[-1], file_flags, dir_fd=parent_fd)
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        _require(stat.S_ISREG(before.st_mode))
        _require(before.st_nlink == 1)
        _require(0 <= before.st_size <= PINNED_SOURCE_MAX_BYTES)
        _require(before.st_size == expected_size)

        contents = bytearray()
        while len(contents) <= expected_size:
            remaining = expected_size + 1 - len(contents)
            chunk = os.read(file_fd, min(65536, remaining))
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(file_fd)
        _require(_pinned_stat_signature(before) == _pinned_stat_signature(after))
        _require(len(contents) == expected_size)
        data = bytes(contents)
        _require(_digest(data) == compiled_digest)
        _require(_digest(data) == record_digest)
        return data
    except InvalidRecord:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise InvalidRecord from exc
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass


def _plain_root(value: str) -> Path:
    root = Path(value).absolute()
    _require(root.is_absolute())
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise InvalidRecord from exc
        _require(not current.is_symlink())
        if current != root:
            _require(current.is_dir())
    _require(root.is_dir())
    return root


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_matches(data: bytes, expected: Any) -> None:
    _require(isinstance(expected, str) and HEX256.fullmatch(expected) is not None)
    _require(_digest(data) == expected)


def _json_bytes(data: bytes) -> Any:
    _require(len(data) <= MAX_JSON_BYTES)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRecord from exc


def _string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _native_baseline_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_chat_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "chatgpt.com"
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and "/c/" in parsed.path
        )
    except ValueError:
        return False


def _response_body_and_metadata(artifact: bytes) -> tuple[bytes, dict[str, Any]]:
    opening = b"```json\n"
    closing = b"\n```\n\n"
    start = artifact.find(opening)
    _require(start >= 0)
    metadata_start = start + len(opening)
    end = artifact.find(closing, metadata_start)
    _require(end >= 0)
    metadata = _json_bytes(artifact[metadata_start:end])
    _require(isinstance(metadata, dict))
    body_with_formatter_newline = artifact[end + len(closing) :]
    _require(body_with_formatter_newline.endswith(b"\n"))
    body = body_with_formatter_newline[:-1]
    return body, metadata


def _verify_sources(root: Path, record: dict[str, Any]) -> str:
    scope = record.get("scope")
    _require(isinstance(scope, dict) and isinstance(scope.get("description"), str))
    refs = scope.get("source_files")
    sources = record.get("sources")
    _require(_string_list(refs) and isinstance(sources, list) and bool(sources))
    seen: set[str] = set()
    source_paths: list[str] = []
    for entry in sources:
        _require(isinstance(entry, dict))
        path = entry.get("path")
        _require(isinstance(path, str))
        _require(path not in seen)
        seen.add(path)
        source_paths.append(path)
        if path in PINNED_SOURCE_PINS:
            contents = _read_pinned_source(root, path, entry.get("sha256"))
        else:
            _safe_relative(path)
            contents = _read_relative(root, path)
        _hash_matches(contents, entry.get("sha256"))
    _require(refs == source_paths)

    package = record.get("package")
    _require(isinstance(package, dict))
    package_bytes = _read_relative(root, package.get("path"))
    _hash_matches(package_bytes, package.get("sha256"))
    _require(isinstance(package.get("sha256"), str))
    return package["sha256"]


def _verify_optional_evidence(root: Path, provenance: dict[str, Any]) -> None:
    path = provenance.get("evidence_path")
    expected = provenance.get("evidence_sha256")
    if path is None and expected is None:
        return
    _require(isinstance(path, str))
    data = _read_relative(root, path)
    _hash_matches(data, expected)


def _validate_harvest_argv(value: Any, manifest_path: str) -> None:
    _require(isinstance(value, list) and len(value) >= 3)
    _require(all(isinstance(item, str) and bool(item) for item in value))
    _require(all("\x00" not in item for item in value))

    native_callers = {
        "pack_and_ask.py",
        "run_native_review_private.py",
        "run_native_review_bound_model.py",
    }
    first = value[0]
    first_name = PurePosixPath(first).name
    if first == "python3" or (
        first_name == "python3" and (first.startswith("/") or first.startswith("~/"))
    ):
        index = 1
        if index < len(value) and value[index] == "-B":
            index += 1
        _require(index < len(value))
        script = value[index]
        index += 1
    else:
        script = first
        index = 1

    _require("://" not in script and "\\" not in script and not any(c.isspace() for c in script))
    _require(PurePosixPath(script).name in native_callers)

    harvest_positions = [index for index, item in enumerate(value) if item == "--harvest"]
    _require(len(harvest_positions) == 1)
    harvest_index = harvest_positions[0]
    _require(harvest_index >= index and harvest_index + 1 < len(value))
    _require(value[harvest_index + 1] == manifest_path)

    seen_harvest = False
    seen_out_dir = False
    while index < len(value):
        option = value[index]
        _require(index + 1 < len(value))
        operand = value[index + 1]
        if option == "--harvest":
            _require(not seen_harvest and operand == manifest_path)
            seen_harvest = True
        elif option == "--out-dir":
            _require(not seen_out_dir)
            _safe_relative(operand)
            seen_out_dir = True
        else:
            raise InvalidRecord
        index += 2
    _require(seen_harvest)


def _verify_native_review(root: Path, review: dict[str, Any], package_sha256: str) -> None:
    _require(review.get("requested_reviewer") == "native web ChatGPT")
    _require(isinstance(review.get("requested_model"), str) and bool(review["requested_model"]))
    _require(review.get("requested_effort") == "pro")
    _require(review.get("submission_status") == "completed")
    _require(isinstance(review.get("project"), str) and bool(review["project"]))
    _require(_valid_chat_url(review.get("conversation_url")))
    provenance = review.get("selection_provenance")
    _require(isinstance(provenance, dict) and provenance.get("verified_pre_send") is True)
    _require(isinstance(provenance.get("method"), str) and bool(provenance["method"]))
    _verify_optional_evidence(root, provenance)

    manifest_path = review.get("manifest_path")
    manifest_bytes = _read_relative(root, manifest_path, size_limit=MAX_JSON_BYTES)
    _hash_matches(manifest_bytes, review.get("manifest_sha256"))
    manifest = _json_bytes(manifest_bytes)
    _require(isinstance(manifest, dict))
    _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 2)
    _require(manifest.get("original_run_bound") is True)
    _require(isinstance(manifest.get("project"), str) and bool(manifest["project"]))
    _require(manifest.get("project") == review.get("project"))
    _require(isinstance(manifest.get("run_id"), str) and bool(manifest["run_id"]))
    _require(_valid_chat_url(manifest.get("chat_url")))
    _require(manifest["chat_url"] == review["conversation_url"])
    _require(_string_list(manifest.get("sent_user_ids"), nonempty=True))
    _require(_string_list(manifest.get("assistant_ids"), nonempty=True))
    _require(_native_baseline_list(manifest.get("baseline_user_ids")))
    _require(_native_baseline_list(manifest.get("baseline_assistant_ids")))
    _require(manifest.get("pack_sha256") == package_sha256)
    _require(manifest.get("phase") == "COMPLETE")
    _require(isinstance(manifest.get("response_sha256"), str))
    _require(HEX256.fullmatch(manifest["response_sha256"]) is not None)

    identity = review.get("manifest_identity")
    _require(isinstance(identity, dict))
    for field in (
        "run_id",
        "chat_url",
        "original_run_bound",
        "sent_user_ids",
        "assistant_ids",
        "baseline_user_ids",
        "baseline_assistant_ids",
        "phase",
        "response_sha256",
    ):
        _require(identity.get(field) == manifest.get(field))

    _validate_harvest_argv(review.get("harvest_argv"), manifest_path)

    artifact_path = review.get("response_artifact_path")
    artifact = _read_relative(root, artifact_path)
    _hash_matches(artifact, review.get("response_artifact_sha256"))
    response_body, wrapper_metadata = _response_body_and_metadata(artifact)
    _hash_matches(response_body, manifest["response_sha256"])
    for field in ("run_id", "chat_url", "original_run_bound", "sent_user_ids", "assistant_ids"):
        _require(wrapper_metadata.get(field) == manifest.get(field))

    model_verification = manifest.get("model_verification")
    _require(isinstance(model_verification, dict))
    _require(wrapper_metadata.get("model_verification") == model_verification)
    _require(model_verification.get("actual_model_verification") == "selected_radio")
    observed_model = model_verification.get("observed_selection")
    observed_effort = model_verification.get("effort")
    observed_display = model_verification.get("actual_display")
    _require(isinstance(observed_model, str) and bool(observed_model))
    _require(isinstance(observed_effort, str) and bool(observed_effort))
    _require(isinstance(observed_display, str) and bool(observed_display))
    _require(review.get("observed_model") == observed_model)
    _require(review.get("observed_effort") == observed_effort)
    _require(review.get("observed_display") == observed_display)
    _require(observed_model == review["requested_model"])
    _require(provenance.get("observed_model") == observed_model)
    _require(provenance.get("observed_effort") == observed_effort)
    _require(provenance.get("observed_display") == observed_display)

    fallback = review.get("fallback_used")
    _require(type(fallback) is bool)
    if not fallback:
        _require(observed_effort == review["requested_effort"])
        _require(review.get("fallback_reason") is None)
        _require(review.get("fallback_label") is None)
        _require(review.get("quota_evidence") is None)
        _require(review.get("pro_unavailable_evidence") is None)
    else:
        reason = review.get("fallback_reason")
        if reason == "quota_exhausted":
            _require(review.get("fallback_label") in {"Very high", "Extra high"})
            _require(observed_effort in {"very_high", "extra_high"})
            _require(review.get("pro_unavailable_evidence") is None)
            evidence = review.get("quota_evidence")
        else:
            _require(reason == "pro_option_unavailable")
            _require(review.get("fallback_label") == "Slider maximum")
            slider = model_verification.get("slider")
            _require(isinstance(slider, list) and len(slider) == 3)
            _require(all(type(value) is int for value in slider))
            minimum, selected, maximum = slider
            _require(minimum == 0 and selected == maximum and 0 <= maximum < 4)
            maximum_effort = {0: "instant", 1: "standard", 2: "high", 3: "extra_high"}[maximum]
            _require(observed_effort == maximum_effort)
            _require(review.get("quota_evidence") is None)
            evidence = review.get("pro_unavailable_evidence")
        _require(isinstance(evidence, dict))
        evidence_bytes = _read_relative(root, evidence.get("path"))
        _hash_matches(evidence_bytes, evidence.get("sha256"))


def _verify_gemini_review(root: Path, review: dict[str, Any]) -> None:
    expected = "google-antigravity/gemini-3.8-flash"
    _require(review.get("requested_reviewer") == expected)
    _require(review.get("requested_model") == expected)
    _require(review.get("requested_effort") == "ultra")
    _require(review.get("observed_model") == expected)
    _require(review.get("observed_effort") == "ultra")
    _require(review.get("fallback_used") is False)
    _require(review.get("fallback_reason") is None)
    _require(review.get("submission_status") == "completed")
    _require(isinstance(review.get("project"), str) and bool(review["project"]))
    _require(isinstance(review.get("conversation_id"), str) and bool(review["conversation_id"]))
    response_path = review.get("response_path")
    response = _read_relative(root, response_path)
    _hash_matches(response, review.get("response_sha256"))
    provenance = review.get("selection_provenance")
    _require(isinstance(provenance, dict) and provenance.get("verified_pre_send") is True)
    _require(isinstance(provenance.get("method"), str) and bool(provenance["method"]))
    _verify_optional_evidence(root, provenance)
    _require(isinstance(review.get("observed_display"), str) and bool(review["observed_display"]))
    _require(provenance.get("observed_model") == expected)
    _require(provenance.get("observed_effort") == "ultra")
    _require(provenance.get("observed_display") == review.get("observed_display"))


def validate(root_value: str, record_value: str) -> None:
    root = _plain_root(root_value)
    record_bytes = _read_relative(root, record_value, size_limit=MAX_JSON_BYTES)
    record = _json_bytes(record_bytes)
    _require(isinstance(record, dict))
    _require(type(record.get("schema_version")) is int and record["schema_version"] == 1)
    _require(isinstance(record.get("task_id"), str) and bool(record["task_id"]))
    package_sha256 = _verify_sources(root, record)
    reviews = record.get("reviews")
    _require(isinstance(reviews, dict))
    requirements = record.get("review_requirements")
    _require(isinstance(requirements, dict))
    _require(set(requirements) == {"native_web", "gemini"})
    native_requirement = requirements.get("native_web")
    _require(isinstance(native_requirement, dict))
    _require(native_requirement.get("required") is True)
    _require(native_requirement.get("not_applicable_reason") is None)
    _require(native_requirement.get("not_applicable_scope") is None)
    for name, requirement in requirements.items():
        _require(isinstance(requirement, dict))
        required = requirement.get("required")
        _require(type(required) is bool)
        if required:
            _require(requirement.get("not_applicable_reason") is None)
            _require(requirement.get("not_applicable_scope") is None)
        elif name == "gemini":
            _require(
                isinstance(requirement.get("not_applicable_reason"), str)
                and bool(requirement["not_applicable_reason"].strip())
            )
            scope = record.get("scope")
            _require(isinstance(scope, dict))
            _require(
                isinstance(requirement.get("not_applicable_scope"), str)
                and requirement["not_applicable_scope"] == scope.get("description")
                and bool(requirement["not_applicable_scope"].strip())
            )
    native = reviews.get("native_web")
    gemini = reviews.get("gemini")
    _require(isinstance(native, dict) and isinstance(gemini, dict))
    _verify_native_review(root, native, package_sha256)
    if requirements["gemini"]["required"]:
        _verify_gemini_review(root, gemini)
    else:
        _require(gemini.get("submission_status") == "not_applicable")
        _require(gemini.get("not_applicable_reason") == requirements["gemini"]["not_applicable_reason"])
        _require(gemini.get("not_applicable_scope") == requirements["gemini"]["not_applicable_scope"])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["--help"], ["-h"]):
        print("usage: state_check.py --root ROOT --record RELATIVE_PATH")
        return 0
    if len(args) != 4 or args[0] != "--root" or args[2] != "--record":
        print("state_check: validation failed", file=sys.stderr)
        return 2
    try:
        validate(args[1], args[3])
    except Exception:
        print("state_check: validation failed", file=sys.stderr)
        return 2
    print('{"ok":true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
