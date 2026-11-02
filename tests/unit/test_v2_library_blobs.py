"""Blob store (implementation spec §3.2)."""

from __future__ import annotations

import hashlib
import io
import os
import stat
from pathlib import Path

import pytest

from syllva.library.blobs import BlobCorrupt, BlobNotFound, BlobStore, BlobTooLarge, InvalidBlobKey

pytestmark = pytest.mark.unit


def _store(tmp_path: Path, **kwargs: int) -> BlobStore:
    return BlobStore(tmp_path / "blobs", tmp_path / "tmp", **kwargs)


def test_put_is_content_addressed_idempotent_and_read_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.put_bytes(b"hello")
    key = hashlib.sha256(b"hello").hexdigest()
    assert first.sha256 == key and first.size_bytes == 5 and first.created
    path = store.path_for(key)
    assert path == tmp_path / "blobs" / key[:2] / key
    assert not (os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    again = store.put_stream(io.BytesIO(b"hello"))
    assert again.sha256 == key and not again.created
    assert store.read_bytes(key, verify=True) == b"hello"
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize("bad", ["../etc/passwd", "A" * 64, "0" * 63, "g" * 64, "", "0" * 64 + "/x"])
def test_invalid_keys_never_build_a_path(tmp_path: Path, bad: str) -> None:
    with pytest.raises(InvalidBlobKey):
        _store(tmp_path).path_for(bad)


def test_size_limit_leaves_no_partial_files(tmp_path: Path) -> None:
    store = _store(tmp_path, max_bytes=4)
    with pytest.raises(BlobTooLarge) as info:
        store.put_chunks([b"ab", b"cde"])
    assert info.value.code == "UNSUPPORTED_SIZE"
    assert list((tmp_path / "tmp").iterdir()) == []
    assert not any((tmp_path / "blobs").rglob("*")) if (tmp_path / "blobs").exists() else True


def test_missing_and_corrupt_blobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(BlobNotFound):
        store.read_bytes("0" * 64)
    key = store.put_bytes(b"data").sha256
    path = store.path_for(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    path.write_bytes(b"tampered")
    assert store.verify(key) is False
    with pytest.raises(BlobCorrupt):
        store.read_bytes(key, verify=True)


def test_delete_and_tmp_cleanup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = store.put_bytes(b"gone").sha256
    assert store.delete(key) is True and not store.exists(key)
    assert store.delete(key) is False
    (tmp_path / "tmp" / "blob-leftover").write_bytes(b"x")
    assert store.clean_tmp() == 1
