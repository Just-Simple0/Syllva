"""Job identity helpers (implementation spec §8.1.1)."""

from __future__ import annotations

import hashlib


_FIELD_SEPARATOR = b"\x1f"


def derive_job_key(
    source_file_id: str,
    source_hash: str,
    operation: str,
    processor_version: str,
) -> str:
    """Derive the deterministic key for a source-processing job.

    The byte layout is deliberately explicit instead of relying on JSON or a
    platform-specific string encoding.  This keeps the result stable across
    process restarts and operating systems.
    """

    fields = (source_file_id, source_hash, operation, processor_version)
    for name, value in zip(
        ("source_file_id", "source_hash", "operation", "processor_version"), fields
    ):
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        if not value:
            raise ValueError(f"{name} must not be empty")

    payload = _FIELD_SEPARATOR.join(value.encode("utf-8") for value in fields)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = ["derive_job_key"]
