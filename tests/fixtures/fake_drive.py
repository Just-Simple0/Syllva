"""Provider-free Drive reader/writer fixture for Phase 2 tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from uls.adapters.drive.binding import (
    ActivityInstructionBinding,
    InMemorySourceBindingBackend,
    SourceBindingRecord,
)
from uls.domain.errors import SourceUnavailableError
from uls.domain.source_ref import SourceFingerprint, SourceRef


class FakeDriveReader:
    def __init__(
        self,
        *,
        source: Mapping[str, Any] | None = None,
        derived: Mapping[str, Any] | None = None,
        fingerprints: Mapping[str, SourceFingerprint | Mapping[str, Any]] | None = None,
        bindings: Sequence[SourceBindingRecord | ActivityInstructionBinding | Mapping[str, Any]] | None = None,
        events: list[Any] | None = None,
    ) -> None:
        self.source = dict(source or {})
        self.derived = dict(derived or {})
        self.fingerprints = {
            key: _fingerprint(value) for key, value in (fingerprints or {}).items()
        }
        self.events = events if events is not None else []
        if bindings is None:
            # This is explicit fixture setup, kept separate from graph
            # records/front matter.  Production code never infers this table.
            inferred: list[SourceBindingRecord] = []
            for key in self.derived:
                entity_id = _default_entity_for_derivative(key)
                if entity_id is None:
                    continue
                ref = SourceRef("google_drive", key)
                inferred.append(SourceBindingRecord(entity_id, key, ref, ref))
            bindings = inferred
        self.source_bindings = InMemorySourceBindingBackend(bindings)

    def read_derived(self, source_ref: SourceRef | str | Any) -> Any:
        key = _key(source_ref)
        self.events.append(("read_derived", key))
        if isinstance(source_ref, SourceRef):
            matches = [
                record for record in self.source_bindings.records
                if record.source_ref.identity == source_ref.identity
            ]
            activity_matches = [
                record for record in self.source_bindings.activity_records
                if record.source_ref.identity == source_ref.identity
            ]
            derivative_ids = {
                record.derivative_ref.identity for record in matches
            } | {
                record.derivative_ref.identity for record in activity_matches
            }
            if len(derivative_ids) > 1:
                raise SourceUnavailableError("origin has ambiguous registered derivatives")
            if derivative_ids:
                key = next(iter(derivative_ids))[1]
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

    def lookup_source_binding(self, entity_id: str, normalized_source_url: str) -> list[SourceBindingRecord]:
        return self.source_bindings.lookup_source_binding(entity_id, normalized_source_url)

    def register_source_binding(
        self,
        record: SourceBindingRecord | ActivityInstructionBinding | Mapping[str, Any],
    ) -> None:
        self.source_bindings.register(record)

    def lookup_activity_instruction_binding(
        self,
        activity_id: str,
        instructions_source_url: str,
        normalized_instructions_url: str,
    ) -> list[ActivityInstructionBinding]:
        return self.source_bindings.lookup_activity_instruction_binding(
            activity_id, instructions_source_url, normalized_instructions_url
        )

    def register_activity_instruction_binding(
        self,
        record: ActivityInstructionBinding | Mapping[str, Any],
    ) -> None:
        self.source_bindings.register(record)



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


def _default_entity_for_derivative(key: str) -> str | None:
    lowered = key.casefold()
    if lowered == "transcript-05":
        return "COMP319-S05"
    if lowered == "material-m03":
        return "COMP319-M03"
    if lowered.startswith("activity-"):
        suffix = lowered.removeprefix("activity-")
        if suffix.isdigit() and len(suffix) == 2:
            return f"COMP319-A{suffix}"
    return None


__all__ = [
    "FakeDrive",
    "FakeDriveAdapter",
    "FakeDriveReader",
    "FakeDriveWorker",
    "FakeDriveWriter",
]
