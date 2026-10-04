from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
SOURCE_PATH = "src/uls/settings/credential_roles.py"
SYNTHETIC_SOURCE = b"# reviewed synthetic source fixture\n"


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load local checker fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE = _load_module("checker_baseline_fixture", HERE / "checker_baseline.py")
CANDIDATE = _load_module("candidate_checker_fixture", HERE / "state_check.py")


@contextmanager
def _bind_candidate(root: Path, pins: dict[str, tuple[str, int]]):
    root = root.resolve()
    root_stat = root.stat()
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(CANDIDATE, "PINNED_SOURCE_ROOT", str(root))
        )
        stack.enter_context(
            mock.patch.object(
                CANDIDATE, "PINNED_SOURCE_ROOT_DEVICE", root_stat.st_dev
            )
        )
        stack.enter_context(
            mock.patch.object(
                CANDIDATE, "PINNED_SOURCE_ROOT_INODE", root_stat.st_ino
            )
        )
        stack.enter_context(
            mock.patch.object(
                CANDIDATE,
                "PINNED_SOURCE_PINS",
                CANDIDATE.MappingProxyType(pins),
            )
        )
        yield root


@contextmanager
def _spy_open():
    real_open = os.open
    calls: list[tuple[object, object]] = []

    def tracked_open(path, *args, **kwargs):
        calls.append((path, kwargs.get("dir_fd")))
        return real_open(path, *args, **kwargs)

    supported = set(os.supports_dir_fd)
    supported.add(tracked_open)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(CANDIDATE.os, "open", tracked_open))
        stack.enter_context(
            mock.patch.object(CANDIDATE.os, "supports_dir_fd", supported)
        )
        yield calls


@contextmanager
def _spy_descriptor_lifecycle():
    real_open = os.open
    real_close = os.close
    active: set[int] = set()
    duplicate_closes: list[int] = []

    def tracked_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        active.add(fd)
        return fd

    def tracked_close(fd: int) -> None:
        if fd in active:
            active.remove(fd)
        else:
            duplicate_closes.append(fd)
        real_close(fd)

    supported = set(os.supports_dir_fd)
    supported.add(tracked_open)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(CANDIDATE.os, "open", tracked_open))
        stack.enter_context(
            mock.patch.object(CANDIDATE.os, "supports_dir_fd", supported)
        )
        stack.enter_context(mock.patch.object(CANDIDATE.os, "close", tracked_close))
        yield active, duplicate_closes


def _make_source(root: Path, path: str, data: bytes) -> Path:
    target = root.joinpath(*path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_bytes(root: Path, path: str, data: bytes) -> Path:
    target = root.joinpath(*path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _write_json(root: Path, path: str, value: object) -> bytes:
    data = _json_bytes(value)
    _write_bytes(root, path, data)
    return data


def _manifest_identity(manifest: dict[str, object]) -> dict[str, object]:
    fields = (
        "run_id",
        "chat_url",
        "original_run_bound",
        "sent_user_ids",
        "assistant_ids",
        "baseline_user_ids",
        "baseline_assistant_ids",
        "phase",
        "response_sha256",
    )
    return {field: manifest[field] for field in fields}


def _wrapper_metadata(manifest: dict[str, object]) -> dict[str, object]:
    fields = (
        "run_id",
        "chat_url",
        "original_run_bound",
        "sent_user_ids",
        "assistant_ids",
        "model_verification",
    )
    return {field: manifest[field] for field in fields}


NATIVE_BODY = b"Synthetic Native review response.\n"
NATIVE_MANIFEST = "review/native-manifest.json"
NATIVE_ARTIFACT = "review/native-response.md"
NATIVE_PROJECT = "Synthetic Syllva project"
NATIVE_URL = "https://chatgpt.com/c/synthetic-conversation"
GEMINI_RESPONSE = "review/gemini-response.md"
GEMINI_REASON = "synthetic backend-only review scope"
SCOPE_DESCRIPTION = "Synthetic security-checker fixture"


def _write_native_bundle(
    root: Path,
    record: dict[str, object],
    manifest: dict[str, object],
    body: bytes = NATIVE_BODY,
    metadata: dict[str, object] | None = None,
) -> None:
    review = record["reviews"]["native_web"]
    encoded_manifest = _write_json(root, NATIVE_MANIFEST, manifest)
    review["manifest_sha256"] = hashlib.sha256(encoded_manifest).hexdigest()
    wrapper = _wrapper_metadata(manifest) if metadata is None else metadata
    artifact = (
        b"Synthetic wrapper.\n"
        + b"\x60\x60\x60json\n"
        + _json_bytes(wrapper)
        + b"\n"
        + b"\x60\x60\x60\n\n"
        + body
        + b"\n"
    )
    _write_bytes(root, NATIVE_ARTIFACT, artifact)
    review["response_artifact_sha256"] = hashlib.sha256(artifact).hexdigest()


def _make_valid_record(
    root: Path,
    source_path: str = "src/worker.py",
    source_data: bytes = b"Synthetic public source.\n",
    fallback: str = "none",
    gemini_required: bool = False,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    source_paths = [source_path, "src/worker_helper.py"]
    source_entries = []
    for index, path in enumerate(source_paths):
        data = source_data if index == 0 else b"Synthetic helper source.\n"
        _make_source(root, path, data)
        source_entries.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})

    package = b"Synthetic review package.\n"
    _write_bytes(root, "review/package.txt", package)
    package_digest = hashlib.sha256(package).hexdigest()
    observed_effort = {
        "none": "pro",
        "pro_unavailable": "extra_high",
        "quota": "very_high",
    }[fallback]
    model_verification: dict[str, object] = {
        "actual_model_verification": "selected_radio",
        "observed_selection": "gpt-6-luna",
        "effort": observed_effort,
        "actual_display": "GPT-6 Luna",
    }
    evidence = None
    if fallback == "pro_unavailable":
        model_verification["slider"] = [0, 3, 3]
        evidence_path = "review/pro-unavailable-evidence.txt"
        evidence_data = b"Synthetic slider evidence.\n"
        _write_bytes(root, evidence_path, evidence_data)
        evidence = {"path": evidence_path, "sha256": hashlib.sha256(evidence_data).hexdigest()}
    elif fallback == "quota":
        evidence_path = "review/quota-evidence.txt"
        evidence_data = b"Synthetic quota evidence.\n"
        _write_bytes(root, evidence_path, evidence_data)
        evidence = {"path": evidence_path, "sha256": hashlib.sha256(evidence_data).hexdigest()}

    manifest: dict[str, object] = {
        "schema_version": 2,
        "original_run_bound": True,
        "project": NATIVE_PROJECT,
        "run_id": "synthetic-run-id",
        "chat_url": NATIVE_URL,
        "sent_user_ids": ["synthetic-user-message"],
        "assistant_ids": ["synthetic-assistant-message"],
        "baseline_user_ids": [],
        "baseline_assistant_ids": [],
        "pack_sha256": package_digest,
        "phase": "COMPLETE",
        "response_sha256": hashlib.sha256(NATIVE_BODY).hexdigest(),
        "model_verification": model_verification,
    }
    provenance = {
        "verified_pre_send": True,
        "method": "synthetic picker observation",
        "observed_model": "gpt-6-luna",
        "observed_effort": observed_effort,
        "observed_display": "GPT-6 Luna",
    }
    native_review: dict[str, object] = {
        "requested_reviewer": "native web ChatGPT",
        "requested_model": "gpt-6-luna",
        "requested_effort": "pro",
        "submission_status": "completed",
        "project": NATIVE_PROJECT,
        "conversation_url": NATIVE_URL,
        "selection_provenance": provenance,
        "manifest_path": NATIVE_MANIFEST,
        "manifest_sha256": "",
        "manifest_identity": _manifest_identity(manifest),
        "harvest_argv": [
            "python3",
            "scripts/run_native_review_bound_model.py",
            "--harvest",
            NATIVE_MANIFEST,
            "--out-dir",
            "harvest",
        ],
        "response_artifact_path": NATIVE_ARTIFACT,
        "response_artifact_sha256": "",
        "observed_model": "gpt-6-luna",
        "observed_effort": observed_effort,
        "observed_display": "GPT-6 Luna",
        "fallback_used": fallback != "none",
        "fallback_reason": (
            None
            if fallback == "none"
            else "pro_option_unavailable"
            if fallback == "pro_unavailable"
            else "quota_exhausted"
        ),
        "fallback_label": (
            None
            if fallback == "none"
            else "Slider maximum"
            if fallback == "pro_unavailable"
            else "Very high"
        ),
        "quota_evidence": evidence if fallback == "quota" else None,
        "pro_unavailable_evidence": evidence if fallback == "pro_unavailable" else None,
    }
    _write_native_bundle(
        root, {"reviews": {"native_web": native_review}}, manifest
    )

    if gemini_required:
        gemini_data = b"Synthetic Gemini response.\n"
        _write_bytes(root, GEMINI_RESPONSE, gemini_data)
        gemini_review: dict[str, object] = {
            "requested_reviewer": "google-antigravity/gemini-3.8-flash",
            "requested_model": "google-antigravity/gemini-3.8-flash",
            "requested_effort": "ultra",
            "observed_model": "google-antigravity/gemini-3.8-flash",
            "observed_effort": "ultra",
            "fallback_used": False,
            "fallback_reason": None,
            "submission_status": "completed",
            "project": NATIVE_PROJECT,
            "conversation_id": "synthetic-gemini-conversation",
            "response_path": GEMINI_RESPONSE,
            "response_sha256": hashlib.sha256(gemini_data).hexdigest(),
            "selection_provenance": {
                "verified_pre_send": True,
                "method": "synthetic picker observation",
                "observed_model": "google-antigravity/gemini-3.8-flash",
                "observed_effort": "ultra",
                "observed_display": "Gemini 3.8 Flash",
            },
            "observed_display": "Gemini 3.8 Flash",
        }
        gemini_requirement = {
            "required": True,
            "not_applicable_reason": None,
            "not_applicable_scope": None,
        }
    else:
        gemini_review = {
            "submission_status": "not_applicable",
            "not_applicable_reason": GEMINI_REASON,
            "not_applicable_scope": SCOPE_DESCRIPTION,
        }
        gemini_requirement = {
            "required": False,
            "not_applicable_reason": GEMINI_REASON,
            "not_applicable_scope": SCOPE_DESCRIPTION,
        }

    record: dict[str, object] = {
        "schema_version": 1,
        "task_id": "synthetic-task-id",
        "scope": {"description": SCOPE_DESCRIPTION, "source_files": source_paths},
        "sources": source_entries,
        "package": {"path": "review/package.txt", "sha256": package_digest},
        "review_requirements": {
            "native_web": {
                "required": True,
                "not_applicable_reason": None,
                "not_applicable_scope": None,
            },
            "gemini": gemini_requirement,
        },
        "reviews": {"native_web": native_review, "gemini": gemini_review},
    }
    _write_json(root, "task.json", record)
    return record


def _refresh_manifest(root: Path, record: dict[str, object], manifest: dict[str, object]) -> None:
    review = record["reviews"]["native_web"]
    encoded = _write_json(root, NATIVE_MANIFEST, manifest)
    review["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()


class PinnedSourceSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "repo").resolve()
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compiled_pin_inventory_matches_review_snapshot(self) -> None:
        inventory = json.loads(
            (HERE / "source_pins.json").read_text(encoding="utf-8")
        )
        expected = {
            item["path"]: (item["sha256"], item["size_bytes"])
            for item in inventory["sources"]
        }
        self.assertEqual(dict(CANDIDATE.PINNED_SOURCE_PINS), expected)
        self.assertEqual(inventory["repository_root"], CANDIDATE.PINNED_SOURCE_ROOT)
        self.assertEqual(
            inventory["root_binding"]["device"],
            CANDIDATE.PINNED_SOURCE_ROOT_DEVICE,
        )
        self.assertEqual(
            inventory["root_binding"]["inode"],
            CANDIDATE.PINNED_SOURCE_ROOT_INODE,
        )

    def test_exact_synthetic_pin_reads_and_checks_one_descriptor(self) -> None:
        digest = hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()
        _make_source(self.root, SOURCE_PATH, SYNTHETIC_SOURCE)
        with _bind_candidate(
            self.root, {SOURCE_PATH: (digest, len(SYNTHETIC_SOURCE))}
        ) as root:
            with _spy_descriptor_lifecycle() as (active, duplicate_closes):
                data = CANDIDATE._read_pinned_source(root, SOURCE_PATH, digest)
            self.assertEqual(active, set())
            self.assertEqual(duplicate_closes, [])
        self.assertEqual(data, SYNTHETIC_SOURCE)

    def test_forged_digest_and_unlisted_secret_paths_fail_before_open(self) -> None:
        digest = hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()
        _make_source(self.root, SOURCE_PATH, SYNTHETIC_SOURCE)
        with _bind_candidate(
            self.root, {SOURCE_PATH: (digest, len(SYNTHETIC_SOURCE))}
        ) as root:
            with _spy_open() as attempted:
                rejected_paths = (
                    "src/.env",
                    "src/.env.local",
                    "src/uls/settings/credential_shadow.py",
                    "src/secret/credential_roles.py",
                    "src/private.key",
                    "src/credential.pem",
                    "/tmp/credential_roles.py",
                    "../src/uls/settings/credential_roles.py",
                    "C:/src/uls/settings/credential_roles.py",
                    "src\\uls\\settings\\credential_roles.py",
                    "src/\x00credential_roles.py",
                )
                with self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(root, SOURCE_PATH, "0" * 64)
                for path in rejected_paths:
                    with self.subTest(path=repr(path)), self.assertRaises(
                        CANDIDATE.InvalidRecord
                    ):
                        CANDIDATE._read_pinned_source(root, path, digest)
            self.assertEqual(attempted, [])

    def test_wrong_root_and_unsupported_open_capability_fail_before_open(self) -> None:
        digest = hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()
        _make_source(self.root, SOURCE_PATH, SYNTHETIC_SOURCE)
        wrong_root = (Path(self.temp.name) / "copy").resolve()
        wrong_root.mkdir()
        with _bind_candidate(
            self.root, {SOURCE_PATH: (digest, len(SYNTHETIC_SOURCE))}
        ):
            with _spy_open() as positive_calls:
                self.assertEqual(
                    CANDIDATE._read_pinned_source(self.root, SOURCE_PATH, digest),
                    SYNTHETIC_SOURCE,
                )
                self.assertTrue(
                    any(path == "credential_roles.py" for path, _ in positive_calls)
                )

            with _spy_open() as attempted:
                with self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(wrong_root, SOURCE_PATH, digest)
                self.assertEqual(attempted, [])

                with mock.patch.object(
                    CANDIDATE.os, "O_NOFOLLOW", 0
                ), self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(self.root, SOURCE_PATH, digest)
                self.assertEqual(attempted, [])

                with mock.patch.object(
                    CANDIDATE.os, "supports_dir_fd", set()
                ), self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(self.root, SOURCE_PATH, digest)
                self.assertEqual(attempted, [])

                alias = self.root.with_name("repo-alias")
                alias.symlink_to(self.root, target_is_directory=True)
                with self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(alias, SOURCE_PATH, digest)

    def test_symlink_source_is_rejected_before_content_read(self) -> None:
        digest = hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()
        target = self.root / "fixture-bytes.txt"
        target.write_bytes(SYNTHETIC_SOURCE)
        link = self.root.joinpath(*SOURCE_PATH.split("/"))
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        with _bind_candidate(
            self.root, {SOURCE_PATH: (digest, len(SYNTHETIC_SOURCE))}
        ) as root:
            real_read = os.read
            read_calls: list[int] = []

            def count_read(fd: int, size: int) -> bytes:
                read_calls.append(fd)
                return real_read(fd, size)

            with (
                _spy_open() as open_calls,
                mock.patch.object(CANDIDATE.os, "read", side_effect=count_read),
                self.assertRaises(CANDIDATE.InvalidRecord),
            ):
                CANDIDATE._read_pinned_source(root, SOURCE_PATH, digest)
            self.assertEqual(read_calls, [])
            self.assertTrue(
                any(path == "credential_roles.py" for path, _ in open_calls)
            )


class SyntheticRecordGateTests(unittest.TestCase):
    def _assert_both_reject(self, mutation) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            record = _make_valid_record(root)
            mutation(record)
            _write_json(root, "task.json", record)
            for checker in (BASELINE, CANDIDATE):
                with self.subTest(checker=checker.__name__), self.assertRaises(
                    checker.InvalidRecord
                ):
                    checker.validate(str(root), "task.json")

    def test_full_synthetic_native_and_gemini_na_record_and_cli_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            _make_valid_record(root)
            for checker in (BASELINE, CANDIDATE):
                with self.subTest(checker=checker.__name__):
                    checker.validate(str(root), "task.json")
            output = types.SimpleNamespace(out=None, err=None)
            with mock.patch("sys.stdout", new_callable=lambda: _Capture(output, "out")):
                self.assertEqual(
                    CANDIDATE.main(
                        ["--root", str(root), "--record", "task.json"]
                    ),
                    0,
                )
            self.assertEqual(output.out, '{"ok":true}\n')
            self.assertEqual(
                CANDIDATE.main(["--root", str(root), "task.json"]), 2
            )
            record = json.loads((root / "task.json").read_text(encoding="utf-8"))
            record["schema_version"] = 2
            _write_json(root, "task.json", record)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(
                    CANDIDATE.main(
                        ["--root", str(root), "--record", "task.json"]
                    ),
                    2,
                )
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "state_check: validation failed\n")

    def test_pinned_sensitive_source_exception_is_source_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            record = _make_valid_record(
                root, SOURCE_PATH, SYNTHETIC_SOURCE
            )
            digest = hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()
            pins = {SOURCE_PATH: (digest, len(SYNTHETIC_SOURCE))}
            with _bind_candidate(root, pins):
                CANDIDATE.validate(str(root), "task.json")
                self.assertEqual(
                    CANDIDATE.main(
                        ["--root", str(root), "--record", "task.json"]
                    ),
                    0,
                )
                with self.assertRaises(BASELINE.InvalidRecord):
                    BASELINE.validate(str(root), "task.json")

                record["package"]["path"] = SOURCE_PATH
                record["package"]["sha256"] = digest
                _write_json(root, "task.json", record)
                with self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE.validate(str(root), "task.json")
                with self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_relative(root, SOURCE_PATH)

    def test_record_and_native_gate_mutations_fail_in_baseline_and_candidate(self) -> None:
        mutations = [
            ("record schema", lambda record: record.update(schema_version=2)),
            (
                "ordered source references",
                lambda record: record["scope"]["source_files"].reverse(),
            ),
            (
                "current source digest",
                lambda record: record["sources"][0].update(sha256="0" * 64),
            ),
            (
                "package digest",
                lambda record: record["package"].update(sha256="0" * 64),
            ),
            (
                "native reviewer",
                lambda record: record["reviews"]["native_web"].update(
                    requested_reviewer="other"
                ),
            ),
            (
                "requested model",
                lambda record: record["reviews"]["native_web"].update(
                    requested_model="other"
                ),
            ),
            (
                "requested effort",
                lambda record: record["reviews"]["native_web"].update(
                    requested_effort="high"
                ),
            ),
            (
                "manifest identity",
                lambda record: record["reviews"]["native_web"][
                    "manifest_identity"
                ].update(run_id="forged"),
            ),
            (
                "harvest argv",
                lambda record: record["reviews"]["native_web"].update(
                    harvest_argv=["python3", "unrelated.py", "--harvest", NATIVE_MANIFEST]
                ),
            ),
            (
                "response artifact hash",
                lambda record: record["reviews"]["native_web"].update(
                    response_artifact_sha256="0" * 64
                ),
            ),
            (
                "observed effort",
                lambda record: record["reviews"]["native_web"].update(
                    observed_effort="high"
                ),
            ),
            (
                "observed model",
                lambda record: record["reviews"]["native_web"].update(
                    observed_model="other"
                ),
            ),
            (
                "observed display",
                lambda record: record["reviews"]["native_web"].update(
                    observed_display="other"
                ),
            ),
            (
                "selection provenance",
                lambda record: record["reviews"]["native_web"][
                    "selection_provenance"
                ].update(observed_effort="high"),
            ),
            (
                "conversation URL",
                lambda record: record["reviews"]["native_web"].update(
                    conversation_url="https://example.invalid/not-a-chat"
                ),
            ),
            (
                "Native required gate",
                lambda record: record["review_requirements"]["native_web"].update(
                    required=False
                ),
            ),
            (
                "Gemini N/A reason",
                lambda record: record["reviews"]["gemini"].update(
                    not_applicable_reason="different reason"
                ),
            ),
            (
                "Gemini N/A scope",
                lambda record: record["review_requirements"]["gemini"].update(
                    not_applicable_scope="different scope"
                ),
            ),
        ]
        for name, mutation in mutations:
            with self.subTest(mutation=name):
                self._assert_both_reject(mutation)


class NativeGateMutationTests(unittest.TestCase):
    def _assert_both_reject(self, root: Path, record: dict[str, object]) -> None:
        _write_json(root, "task.json", record)
        for checker in (BASELINE, CANDIDATE):
            with self.subTest(checker=checker.__name__), self.assertRaises(
                checker.InvalidRecord
            ):
                checker.validate(str(root), "task.json")

    def test_manifest_identity_project_original_run_and_actual_model(self) -> None:
        cases = (
            ("original run", lambda record, manifest: manifest.update(original_run_bound=False)),
            ("project", lambda record, manifest: manifest.update(project="other project")),
            (
                "manifest schema",
                lambda record, manifest: manifest.update(schema_version=1),
            ),
            (
                "package binding",
                lambda record, manifest: manifest.update(pack_sha256="0" * 64),
            ),
            ("completion phase", lambda record, manifest: manifest.update(phase="PENDING")),
            (
                "manifest response body hash",
                lambda record, manifest: (
                    manifest.update(response_sha256="0" * 64),
                    record["reviews"]["native_web"]["manifest_identity"].update(
                        response_sha256="0" * 64
                    ),
                ),
            ),
            (
                "actual model verification",
                lambda record, manifest: manifest["model_verification"].update(
                    actual_model_verification="unverified"
                ),
            ),
            (
                "manifest identity",
                lambda record, manifest: record["reviews"]["native_web"][
                    "manifest_identity"
                ].update(run_id="other run"),
            ),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temp:
                root = (Path(temp) / "repo").resolve()
                root.mkdir()
                record = _make_valid_record(root)
                manifest = json.loads((root / NATIVE_MANIFEST).read_text())
                mutate(record, manifest)
                _write_native_bundle(root, record, manifest)
                self._assert_both_reject(root, record)

    def test_response_wrapper_and_body_hashes_remain_bound(self) -> None:
        for name in ("wrapper", "body"):
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temp:
                root = (Path(temp) / "repo").resolve()
                root.mkdir()
                record = _make_valid_record(root)
                manifest = json.loads((root / NATIVE_MANIFEST).read_text())
                if name == "wrapper":
                    metadata = _wrapper_metadata(manifest)
                    metadata["run_id"] = "different run"
                    _write_native_bundle(
                        root, record, manifest, metadata=metadata
                    )
                else:
                    _write_native_bundle(
                        root, record, manifest, body=b"changed synthetic response\n"
                    )
                self._assert_both_reject(root, record)

    def test_native_fallback_slider_and_both_evidence_modes(self) -> None:
        for fallback in ("pro_unavailable", "quota"):
            with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as temp:
                root = (Path(temp) / "repo").resolve()
                root.mkdir()
                record = _make_valid_record(root, fallback=fallback)
                for checker in (BASELINE, CANDIDATE):
                    checker.validate(str(root), "task.json")

                evidence_field = (
                    "pro_unavailable_evidence"
                    if fallback == "pro_unavailable"
                    else "quota_evidence"
                )
                evidence_path = record["reviews"]["native_web"][evidence_field]["path"]
                (root / evidence_path).write_bytes(b"changed synthetic evidence\n")
                for checker in (BASELINE, CANDIDATE):
                    with self.subTest(checker=checker.__name__), self.assertRaises(
                        checker.InvalidRecord
                    ):
                        checker.validate(str(root), "task.json")

        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            record = _make_valid_record(root, fallback="pro_unavailable")
            manifest = json.loads((root / NATIVE_MANIFEST).read_text())
            manifest["model_verification"]["slider"][1] = 2
            _write_native_bundle(root, record, manifest)
            self._assert_both_reject(root, record)

    def test_required_gemini_success_and_gate_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            _make_valid_record(root, gemini_required=True)
            for checker in (BASELINE, CANDIDATE):
                checker.validate(str(root), "task.json")

        mutations = (
            lambda record: record["reviews"]["gemini"].update(
                requested_model="different model"
            ),
            lambda record: record["reviews"]["gemini"].update(
                observed_effort="high"
            ),
            lambda record: record["reviews"]["gemini"].update(
                response_sha256="0" * 64
            ),
            lambda record: record["reviews"]["gemini"][
                "selection_provenance"
            ].update(verified_pre_send=False),
        )
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as temp:
                root = (Path(temp) / "repo").resolve()
                root.mkdir()
                record = _make_valid_record(root, gemini_required=True)
                mutation(record)
                _write_json(root, "task.json", record)
                for checker in (BASELINE, CANDIDATE):
                    with self.subTest(checker=checker.__name__), self.assertRaises(
                        checker.InvalidRecord
                    ):
                        checker.validate(str(root), "task.json")


class SecurePathBoundaryTests(unittest.TestCase):
    def _read_child(
        self, root: Path, source_path: str, digest: str, source_size: int
    ) -> subprocess.CompletedProcess:
        root_stat = root.stat()
        program = (
            "import hashlib, pathlib, sys, types\n"
            "from pathlib import Path\n"
            "module_path, root_value, source_path, digest, source_size, device, inode = sys.argv[1:]\n"
            "module = types.ModuleType('child_candidate')\n"
            "module.__file__ = module_path\n"
            "source = Path(module_path).read_text(encoding='utf-8')\n"
            "exec(compile(source, module_path, 'exec'), module.__dict__)\n"
            "module.PINNED_SOURCE_ROOT = root_value\n"
            "module.PINNED_SOURCE_ROOT_DEVICE = int(device)\n"
            "module.PINNED_SOURCE_ROOT_INODE = int(inode)\n"
            "module.PINNED_SOURCE_PINS = module.MappingProxyType({"
            "source_path: (digest, int(source_size))})\n"
            "try:\n"
            " module._read_pinned_source(Path(root_value), source_path, digest)\n"
            "except module.InvalidRecord:\n"
            " raise SystemExit(0)\n"
            "raise SystemExit(1)\n"
        )
        return subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(HERE / "state_check.py"),
                str(root),
                source_path,
                digest,
                str(source_size),
                str(root_stat.st_dev),
                str(root_stat.st_ino),
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )

    def test_changed_size_and_hardlink_fail_before_content_read(self) -> None:
        data = b"Synthetic pinned source bytes.\n"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            _make_source(root, SOURCE_PATH, data + b"changed-size")
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                real_read = os.read
                reads: list[int] = []

                def count_read(fd: int, size: int) -> bytes:
                    reads.append(fd)
                    return real_read(fd, size)

                with _spy_open() as opens, mock.patch.object(
                    CANDIDATE.os, "read", side_effect=count_read
                ), self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(bound_root, SOURCE_PATH, digest)
                self.assertTrue(
                    any(path == "credential_roles.py" for path, _ in opens)
                )
                self.assertEqual(reads, [])

        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            target = root / "ordinary-fixture.txt"
            target.write_bytes(data)
            source = root.joinpath(*SOURCE_PATH.split("/"))
            source.parent.mkdir(parents=True)
            os.link(target, source)
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                real_read = os.read
                reads = []

                def count_read(fd: int, size: int) -> bytes:
                    reads.append(fd)
                    return real_read(fd, size)

                with _spy_open() as opens, mock.patch.object(
                    CANDIDATE.os, "read", side_effect=count_read
                ), self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(bound_root, SOURCE_PATH, digest)
                self.assertTrue(
                    any(path == "credential_roles.py" for path, _ in opens)
                )
                self.assertEqual(reads, [])

    def test_file_metadata_change_during_read_fails_closed(self) -> None:
        data = SYNTHETIC_SOURCE
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            source = _make_source(root, SOURCE_PATH, data)
            initial = source.stat()
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                real_read = os.read
                calls: list[int] = []

                def mutate_after_read(fd: int, size: int) -> bytes:
                    result = real_read(fd, size)
                    calls.append(fd)
                    if len(calls) == 1:
                        with source.open("r+b") as stream:
                            stream.seek(0)
                            stream.write(bytes([data[0] ^ 1]) + data[1:])
                        os.utime(
                            source,
                            ns=(
                                initial.st_atime_ns,
                                initial.st_mtime_ns + 2_000_000_000,
                            ),
                        )
                    return result

                with mock.patch.object(
                    CANDIDATE.os, "read", side_effect=mutate_after_read
                ), self.assertRaises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(
                        bound_root, SOURCE_PATH, digest
                    )
                self.assertTrue(calls)

    def test_fifo_is_nonblocking_and_rejected(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO unavailable")
        data = b"# child synthetic source\n"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            source = root.joinpath(*SOURCE_PATH.split("/"))
            source.parent.mkdir(parents=True)
            os.mkfifo(source)
            result = self._read_child(root, SOURCE_PATH, digest, len(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_root_replacement_is_rejected_by_pinned_directory_identity(self) -> None:
        data = SYNTHETIC_SOURCE
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            _make_source(root, SOURCE_PATH, data)
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                original = os.open
                original_close = os.close
                moved = root.with_name("repo-original")
                swapped = False
                source_opens: list[object] = []
                active_fds: set[int] = set()
                duplicate_closes: list[int] = []
                close_errors: list[int] = []

                def replace_root(path, *args, **kwargs):
                    nonlocal swapped
                    if path == root.name and kwargs.get("dir_fd") is not None and not swapped:
                        os.rename(root, moved)
                        root.mkdir()
                        swapped = True
                    if path == "credential_roles.py":
                        source_opens.append(path)
                    fd = original(path, *args, **kwargs)
                    active_fds.add(fd)
                    return fd

                def track_close(fd: int) -> None:
                    if fd not in active_fds:
                        duplicate_closes.append(fd)
                    else:
                        active_fds.remove(fd)
                    try:
                        original_close(fd)
                    except OSError as exc:
                        close_errors.append(exc.errno or -1)
                        raise

                supported = set(os.supports_dir_fd)
                supported.add(replace_root)
                with (
                    mock.patch.object(CANDIDATE.os, "open", replace_root),
                    mock.patch.object(
                        CANDIDATE.os, "supports_dir_fd", supported
                    ),
                    mock.patch.object(CANDIDATE.os, "close", track_close),
                    self.assertRaises(CANDIDATE.InvalidRecord),
                ):
                    CANDIDATE._read_pinned_source(bound_root, SOURCE_PATH, digest)
                self.assertTrue(swapped)
                self.assertEqual(source_opens, [])
                self.assertEqual(active_fds, set())
                self.assertEqual(duplicate_closes, [])
                self.assertEqual(close_errors, [])

    def test_parent_symlink_swap_is_rejected_before_file_read(self) -> None:
        data = SYNTHETIC_SOURCE
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            source = _make_source(root, SOURCE_PATH, data)
            parent = source.parent
            moved = parent.with_name("settings-original")
            replacement = root / "replacement-settings"
            replacement.mkdir()
            _make_source(replacement, "credential_roles.py", b"changed fixture\n")
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                original = os.open
                swapped = False
                reads: list[int] = []

                def replace_parent(path, *args, **kwargs):
                    nonlocal swapped
                    if path == "settings" and kwargs.get("dir_fd") is not None and not swapped:
                        os.rename(parent, moved)
                        parent.symlink_to(replacement, target_is_directory=True)
                        swapped = True
                    return original(path, *args, **kwargs)

                supported = set(os.supports_dir_fd)
                supported.add(replace_parent)
                real_read = os.read

                def count_read(fd: int, size: int) -> bytes:
                    reads.append(fd)
                    return real_read(fd, size)

                with (
                    mock.patch.object(CANDIDATE.os, "open", replace_parent),
                    mock.patch.object(
                        CANDIDATE.os, "supports_dir_fd", supported
                    ),
                    mock.patch.object(
                        CANDIDATE.os, "read", side_effect=count_read
                    ),
                    self.assertRaises(CANDIDATE.InvalidRecord),
                ):
                    CANDIDATE._read_pinned_source(bound_root, SOURCE_PATH, digest)
                self.assertTrue(swapped)
                self.assertEqual(reads, [])

    def test_parent_replacement_after_open_stays_on_held_directory_descriptor(self) -> None:
        data = SYNTHETIC_SOURCE
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            source = _make_source(root, SOURCE_PATH, data)
            parent = source.parent
            moved = parent.with_name("settings-original")
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                original = os.open
                swapped = False
                replacement_data = b"attacker replacement bytes\n"

                def replace_parent_contents(path, *args, **kwargs):
                    nonlocal swapped
                    if (
                        path == "credential_roles.py"
                        and kwargs.get("dir_fd") is not None
                        and not swapped
                    ):
                        os.rename(parent, moved)
                        parent.mkdir()
                        (parent / "credential_roles.py").write_bytes(replacement_data)
                        swapped = True
                    return original(path, *args, **kwargs)

                supported = set(os.supports_dir_fd)
                supported.add(replace_parent_contents)
                with mock.patch.object(
                    CANDIDATE.os, "open", replace_parent_contents
                ), mock.patch.object(
                    CANDIDATE.os, "supports_dir_fd", supported
                ):
                    result = CANDIDATE._read_pinned_source(
                        bound_root, SOURCE_PATH, digest
                    )
                self.assertTrue(swapped)
                self.assertEqual(result, data)
                self.assertEqual(
                    (parent / "credential_roles.py").read_bytes(), replacement_data
                )

    def test_final_regular_file_replacement_fails_digest_on_opened_descriptor(self) -> None:
        data = SYNTHETIC_SOURCE
        digest = hashlib.sha256(data).hexdigest()
        replacement_data = bytes([data[0] ^ 1]) + data[1:]
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "repo").resolve()
            root.mkdir()
            source = _make_source(root, SOURCE_PATH, data)
            old = source.with_name("credential_roles-original.py")
            with _bind_candidate(
                root, {SOURCE_PATH: (digest, len(data))}
            ) as bound_root:
                original = os.open
                swapped = False

                def replace_final(path, *args, **kwargs):
                    nonlocal swapped
                    if path == source.name and kwargs.get("dir_fd") is not None and not swapped:
                        os.rename(source, old)
                        source.write_bytes(replacement_data)
                        swapped = True
                    return original(path, *args, **kwargs)

                supported = set(os.supports_dir_fd)
                supported.add(replace_final)
                with (
                    mock.patch.object(CANDIDATE.os, "open", replace_final),
                    mock.patch.object(
                        CANDIDATE.os, "supports_dir_fd", supported
                    ),
                    self.assertRaises(CANDIDATE.InvalidRecord),
                ):
                    CANDIDATE._read_pinned_source(bound_root, SOURCE_PATH, digest)
                self.assertTrue(swapped)


class _Capture:
    def __init__(self, output: types.SimpleNamespace, field: str) -> None:
        self.output = output
        self.field = field

    def write(self, value: str) -> None:
        previous = getattr(self.output, self.field)
        setattr(self.output, self.field, (previous or "") + value)

    def flush(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
