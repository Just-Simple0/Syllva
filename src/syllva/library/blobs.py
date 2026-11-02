"""Content-addressed blob store (implementation spec §3.2).

* key: lowercase hex SHA-256 of the bytes; path ``blobs/<first 2>/<64 hex>``;
* writes go to ``tmp/``, are fsynced, hash-checked, then ``os.replace``d into
  place; an existing blob makes the write a no-op (idempotent);
* stored files are read-only (0444 / FILE_ATTRIBUTE_READONLY);
* keys are validated before any path is built, so a key can never escape
  ``blobs/``.

Reference counting and garbage collection live in the database (``blob_refs``);
this module only stores and returns bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

DEFAULT_MAX_BYTES = 1 << 30  # 1 GiB (spec §3.2)
_CHUNK = 1 << 20
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class BlobError(RuntimeError):
    code = "BLOB_ERROR"


class BlobTooLarge(BlobError):
    code = "UNSUPPORTED_SIZE"


class BlobNotFound(BlobError):
    code = "BLOB_NOT_FOUND"


class BlobCorrupt(BlobError):
    code = "BLOB_CORRUPT"


class InvalidBlobKey(BlobError):
    code = "INVALID_BLOB_KEY"


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    size_bytes: int
    created: bool  # False when the blob already existed


def validate_key(sha256: str) -> str:
    if not isinstance(sha256, str) or not _SHA256_HEX.fullmatch(sha256):
        raise InvalidBlobKey("blob key must be 64 lowercase hex characters")
    return sha256


class BlobStore:
    def __init__(self, blobs_dir: Path, tmp_dir: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.blobs_dir = Path(blobs_dir)
        self.tmp_dir = Path(tmp_dir)
        self.max_bytes = max_bytes

    def path_for(self, sha256: str) -> Path:
        key = validate_key(sha256)
        return self.blobs_dir / key[:2] / key

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put_bytes(self, data: bytes) -> StoredBlob:
        return self.put_chunks([data])

    def put_stream(self, stream: BinaryIO) -> StoredBlob:
        return self.put_chunks(iter(lambda: stream.read(_CHUNK), b""))

    def put_chunks(self, chunks: Iterable[bytes]) -> StoredBlob:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(prefix="blob-", dir=self.tmp_dir)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise BlobTooLarge(f"blob exceeds {self.max_bytes} bytes")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            key = digest.hexdigest()
            final = self.path_for(key)
            if final.is_file():
                tmp_path.unlink()
                return StoredBlob(key, size, created=False)
            if _sha256_file(tmp_path) != key:  # re-read what is actually on disk
                raise BlobCorrupt("temporary blob changed while it was written")
            final.parent.mkdir(parents=True, exist_ok=True)
            _make_read_only(tmp_path)
            try:
                os.replace(tmp_path, final)
            except OSError as exc:
                if final.is_file():  # a concurrent writer stored the same content first
                    _remove_quietly(tmp_path)
                    return StoredBlob(key, size, created=False)
                raise BlobError(f"could not store blob: {exc}") from exc
            _fsync_dir(final.parent)
            return StoredBlob(key, size, created=True)
        except BaseException:
            _remove_quietly(tmp_path)
            raise

    def open(self, sha256: str) -> BinaryIO:
        path = self.path_for(sha256)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise BlobNotFound(sha256) from exc

    def read_bytes(self, sha256: str, *, verify: bool = False) -> bytes:
        with self.open(sha256) as handle:
            data = handle.read()
        if verify and hashlib.sha256(data).hexdigest() != sha256:
            raise BlobCorrupt(sha256)
        return data

    def verify(self, sha256: str) -> bool:
        path = self.path_for(sha256)
        if not path.is_file():
            raise BlobNotFound(sha256)
        return _sha256_file(path) == sha256

    def delete(self, sha256: str) -> bool:
        """Remove a blob file. Callers must have checked ``blob_refs`` first."""

        path = self.path_for(sha256)
        if not path.exists():
            return False
        _make_writable(path)
        path.unlink()
        return True

    def clean_tmp(self) -> int:
        """Remove leftovers from interrupted writes (run at start-up)."""

        removed = 0
        if self.tmp_dir.is_dir():
            for leftover in self.tmp_dir.glob("blob-*"):
                _remove_quietly(leftover)
                removed += 1
        return removed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _remove_quietly(path: Path) -> None:
    try:
        _make_writable(path)
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
