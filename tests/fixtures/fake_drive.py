"""Provider-free Drive reader/writer fixture for Phase 2 tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from uls.domain.source_ref import SourceFingerprint, SourceRef


class FakeDriveReader:
    def __init__(
        self,
        *,
        source: Mapping[str, Any] | None = None,
        derived: Mapping[str, Any] | None = None,
        fingerprints: Mapping[str, SourceFingerprint | Mapping[str, Any]] | None = None,
        events: list[Any] | None = None,
    ) -> None:
        self.source = dict(source or {})
        self.derived = dict(derived or {})
        self.fingerprints = {
            key: _fingerprint(value) for key, value in (fingerprints or {}).items()
        }
        self.events = events if events is not None else []

    def read_derived(self, source_ref: SourceRef | str | Any) -> Any:
        key = _key(source_ref)
        self.events.append(("read_derived", key))
        if key not in self.derived:
            raise KeyError(key)
        return self.derived[key]

    def read_source(self, source_ref: SourceRef | str | Any) -> Any:
        """Optional worker/test helper for the canonical source body."""

        key = _key(source_ref)
        self.events.append(("read_source", key))
        if key not in self.source:
            raise KeyError(key)
        return self.source[key]

    def get_current_fingerprint(self, entity_id_or_source_ref: SourceRef | str | Any) -> SourceFingerprint | None:
        key = _key(entity_id_or_source_ref)
        value = self.fingerprints.get(key)
        if value is not None:
            return value
        # Fixtures commonly key the fingerprint by entity while the Notion
        # record stores a Drive file ID, and vice versa.
        if isinstance(entity_id_or_source_ref, SourceRef):
            return self.fingerprints.get(entity_id_or_source_ref.file_id)
        return None



class FakeDriveWriter(FakeDriveReader):
    """Separate worker-side writer used only by ingestion ordering tests."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._staged: dict[str, Any] = {}

    def write_staged_derived(self, source_ref: SourceRef | str, content: str) -> str:
        ref = f"staged:{_key(source_ref)}"
        self._staged[ref] = content
        self.events.append(("stage", ref))
        return ref

    def validate_derived(self, staged_ref: str) -> bool:
        self.events.append(("validate", staged_ref))
        return staged_ref in self._staged

    def replace_derived_file_atomically(self, staged_ref: str) -> str:
        self.events.append(("publish", staged_ref))
        if staged_ref not in self._staged:
            raise KeyError(staged_ref)
        key = staged_ref.removeprefix("staged:")
        self.derived[key] = self._staged[staged_ref]
        return f"derived:{key}"


FakeDrive = FakeDriveReader
FakeDriveAdapter = FakeDriveReader
FakeDriveWorker = FakeDriveWriter


def _key(value: Any) -> str:
    if isinstance(value, SourceRef):
        return value.file_id
    if isinstance(value, Mapping):
        return str(value.get("file_id", value.get("id")))
    return str(value)


def _fingerprint(value: SourceFingerprint | Mapping[str, Any] | Sequence[Any]) -> SourceFingerprint:
    if isinstance(value, SourceFingerprint):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        return SourceFingerprint(int(value[0]), str(value[1]))
    return SourceFingerprint(int(value["source_version"]), str(value["source_hash"]))


__all__ = [
    "FakeDrive",
    "FakeDriveAdapter",
    "FakeDriveReader",
    "FakeDriveWorker",
    "FakeDriveWriter",
]
