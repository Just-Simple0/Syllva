from __future__ import annotations

import copy
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
import test_state_check as prior

CANDIDATE = prior.CANDIDATE
DOCUMENT_PATH = "docs/plans/credential-secret-file-launcher.md"
DOCUMENT_BYTES = b"Synthetic reviewed Markdown source fixture.\n"
DOCUMENT_SHA256 = hashlib.sha256(DOCUMENT_BYTES).hexdigest()
COMPILED_DOCUMENT_SHA256 = "c94b94b19e98bc928969d359f59ad0f01ba48b527eb751d8f4c75b20f4568fa8"
COMPILED_DOCUMENT_SIZE = 48413


@contextmanager
def _repo_root():
    with tempfile.TemporaryDirectory(prefix="document-pin-") as temp:
        root = (Path(temp) / "repo").resolve()
        root.mkdir()
        yield root


@contextmanager
def _bind_document_pin(root: Path, contents: bytes = DOCUMENT_BYTES):
    digest = hashlib.sha256(contents).hexdigest()
    pin = {DOCUMENT_PATH: (digest, len(contents))}
    with prior._bind_candidate(root, {}), mock.patch.object(
        CANDIDATE, "PINNED_DOCUMENT_PINS", CANDIDATE.MappingProxyType(pin)
    ):
        yield digest


def _valid_record(
    root: Path,
    source_path: str = DOCUMENT_PATH,
    source_bytes: bytes = DOCUMENT_BYTES,
    *,
    gemini_required: bool = False,
):
    return prior._make_valid_record(
        root,
        source_path=source_path,
        source_data=source_bytes,
        gemini_required=gemini_required,
    )


def test_compiled_document_pin_is_one_exact_independent_entry():
    assert dict(CANDIDATE.PINNED_DOCUMENT_PINS) == {
        DOCUMENT_PATH: (COMPILED_DOCUMENT_SHA256, COMPILED_DOCUMENT_SIZE)
    }
    assert DOCUMENT_PATH not in CANDIDATE.PINNED_SOURCE_PINS
    assert len(CANDIDATE.PINNED_SOURCE_PINS) == 9
    assert CANDIDATE.PINNED_SOURCE_ROOT == "/Users/admin/Project/Syllva"
    assert CANDIDATE.PINNED_SOURCE_ROOT_DEVICE == 16777230
    assert CANDIDATE.PINNED_SOURCE_ROOT_INODE == 22556999


def test_exact_document_source_passes_full_synthetic_record():
    with _repo_root() as root:
        _valid_record(root)
        with _bind_document_pin(root):
            CANDIDATE.validate(str(root), "task.json")


def test_existing_plain_markdown_source_remains_allowed():
    with _repo_root() as root:
        _valid_record(root, source_path="docs/ordinary-plan.md")
        with _bind_document_pin(root):
            CANDIDATE.validate(str(root), "task.json")


def test_other_sensitive_markdown_is_not_a_privileged_document_pin():
    unknown = "docs/plans/credential-secret-other.md"
    with _repo_root() as root:
        _valid_record(root, source_path=unknown)
        with _bind_document_pin(root):
            with pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE.validate(str(root), "task.json")
            with pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._pinned_source_components("docs/plans/unregistered.md")


@pytest.mark.parametrize(
    "artifact_kind",
    ("package", "manifest", "native_response", "evidence", "gemini_response"),
)
def test_pinned_document_cannot_be_reused_as_an_artifact(artifact_kind):
    with _repo_root() as root:
        record = _valid_record(
            root,
            gemini_required=(artifact_kind == "gemini_response"),
        )
        trial = copy.deepcopy(record)
        if artifact_kind == "package":
            trial["package"] = {"path": DOCUMENT_PATH, "sha256": DOCUMENT_SHA256}
        elif artifact_kind == "manifest":
            trial["reviews"]["native_web"]["manifest_path"] = DOCUMENT_PATH
            trial["reviews"]["native_web"]["manifest_sha256"] = DOCUMENT_SHA256
        elif artifact_kind == "native_response":
            trial["reviews"]["native_web"]["response_artifact_path"] = DOCUMENT_PATH
            trial["reviews"]["native_web"]["response_artifact_sha256"] = DOCUMENT_SHA256
        elif artifact_kind == "evidence":
            provenance = trial["reviews"]["native_web"]["selection_provenance"]
            provenance["evidence_path"] = DOCUMENT_PATH
            provenance["evidence_sha256"] = DOCUMENT_SHA256
        else:
            trial["reviews"]["gemini"]["response_path"] = DOCUMENT_PATH
            trial["reviews"]["gemini"]["response_sha256"] = DOCUMENT_SHA256
        prior._write_json(root, "task.json", trial)

        with _bind_document_pin(root), pytest.raises(CANDIDATE.InvalidRecord):
            CANDIDATE.validate(str(root), "task.json")


def test_forged_record_digest_is_rejected_before_any_open():
    with _repo_root() as root:
        prior._make_source(root, DOCUMENT_PATH, DOCUMENT_BYTES)
        with _bind_document_pin(root, DOCUMENT_BYTES):
            with prior._spy_open() as attempted, pytest.raises(
                CANDIDATE.InvalidRecord
            ):
                CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, "0" * 64)
            assert attempted == []


def test_wrong_size_is_rejected_before_content_read():
    with _repo_root() as root:
        prior._make_source(root, DOCUMENT_PATH, DOCUMENT_BYTES + b"x")
        with _bind_document_pin(root, DOCUMENT_BYTES):
            real_read = os.read
            reads = []

            def tracked_read(fd, size):
                reads.append(fd)
                return real_read(fd, size)

            with prior._spy_open() as opens, mock.patch.object(
                CANDIDATE.os, "read", side_effect=tracked_read
            ), pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, DOCUMENT_SHA256)
            assert any(path == Path(DOCUMENT_PATH).name for path, _ in opens)
            assert reads == []


def test_wrong_root_fails_before_open_and_bound_root_is_positive_control():
    with _repo_root() as root:
        prior._make_source(root, DOCUMENT_PATH, DOCUMENT_BYTES)
        wrong_root = root.with_name("wrong-root")
        wrong_root.mkdir()
        with _bind_document_pin(root):
            with prior._spy_open() as opens:
                assert (
                    CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, DOCUMENT_SHA256)
                    == DOCUMENT_BYTES
                )
                assert any(path == Path(DOCUMENT_PATH).name for path, _ in opens)

            with prior._spy_open() as opens:
                with pytest.raises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(
                        wrong_root, DOCUMENT_PATH, DOCUMENT_SHA256
                    )
                assert opens == []


def test_same_size_forged_document_bytes_fail_after_hash_read():
    changed_bytes = b"X" * len(DOCUMENT_BYTES)
    with _repo_root() as root:
        prior._make_source(root, DOCUMENT_PATH, changed_bytes)
        with _bind_document_pin(root, DOCUMENT_BYTES):
            real_read = os.read
            reads = []

            def tracked_read(fd, size):
                data = real_read(fd, size)
                if data:
                    reads.append(fd)
                return data

            with mock.patch.object(
                CANDIDATE.os, "read", side_effect=tracked_read
            ), pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, DOCUMENT_SHA256)
            assert reads


def test_document_metadata_change_during_read_fails_closed():
    with _repo_root() as root:
        path = prior._make_source(root, DOCUMENT_PATH, DOCUMENT_BYTES)
        with _bind_document_pin(root, DOCUMENT_BYTES):
            real_read = os.read
            changed = False

            def touch_after_read(fd, size):
                nonlocal changed
                data = real_read(fd, size)
                if data and not changed:
                    info = path.stat()
                    os.utime(
                        path,
                        ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000),
                    )
                    changed = True
                return data

            with mock.patch.object(
                CANDIDATE.os, "read", side_effect=touch_after_read
            ), pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, DOCUMENT_SHA256)
            assert changed


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo"))
def test_document_reader_rejects_link_and_special_file_inputs(kind):
    with _repo_root() as root:
        target = root.joinpath(*DOCUMENT_PATH.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        external = root.parent / "outside-synthetic-document"
        if kind == "symlink":
            external.write_bytes(DOCUMENT_BYTES)
            target.symlink_to(external)
        elif kind == "hardlink":
            external.write_bytes(DOCUMENT_BYTES)
            os.link(external, target)
        else:
            os.mkfifo(target)

        with _bind_document_pin(root, DOCUMENT_BYTES):
            real_read = os.read
            reads = []

            def tracked_read(fd, size):
                reads.append(fd)
                return real_read(fd, size)

            with prior._spy_open() as opens, mock.patch.object(
                CANDIDATE.os, "read", side_effect=tracked_read
            ), pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, DOCUMENT_PATH, DOCUMENT_SHA256)
            assert any(path == Path(DOCUMENT_PATH).name for path, _ in opens)
            assert reads == []
