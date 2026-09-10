"""Trusted graph-to-source binding for normalized derivatives.

The graph's normalized URL is a pointer, not a source identity.  This module
requires a separately registered provider/state association before a
derivative can be used as evidence or as an approval dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from uls.domain.errors import ProviderUnavailableError, SourceUnavailableError, UlsError
from uls.domain.source_ref import SourceRef


@dataclass(frozen=True)
class SourceBindingRecord:
    """One trusted normalized-derivative to originating-source association."""

    entity_id: str
    normalized_source_url: str | SourceRef
    derivative_ref: SourceRef
    source_ref: SourceRef

    @property
    def originating_ref(self) -> SourceRef:
        return self.source_ref

    @property
    def canonical_source_ref(self) -> SourceRef:
        return self.source_ref


@runtime_checkable
class SourceBindingBackend(Protocol):
    """Read-only trusted association lookup."""

    def lookup_source_binding(
        self,
        entity_id: str,
        normalized_source_url: str,
    ) -> SourceBindingRecord | Mapping[str, Any] | Sequence[SourceBindingRecord | Mapping[str, Any]] | None:
        ...


@runtime_checkable
class SourceBindingResolver(Protocol):
    """Resolve a graph derivative pointer to its canonical originating ref."""

    def resolve_derivative_ref(self, entity_id: str, normalized_source_url: str) -> SourceRef:
        ...


class ValidatedSourceBindingResolver:
    """Fail-closed resolver backed by independently registered provenance."""

    def __init__(self, backend: SourceBindingBackend | Any) -> None:
        self.backend = backend

    def resolve_derivative_ref(self, entity_id: str, normalized_source_url: str) -> SourceRef:
        if not _nonempty(entity_id) or not _nonempty(normalized_source_url):
            raise SourceUnavailableError("normalized derivative binding input is invalid")
        lookup = getattr(self.backend, "lookup_source_binding", None)
        if not callable(lookup):
            raise SourceUnavailableError("trusted source binding lookup is unavailable")
        try:
            raw = lookup(entity_id, normalized_source_url)
        except (KeyError, LookupError, FileNotFoundError) as exc:
            raise SourceUnavailableError("trusted source binding is missing") from exc
        except UlsError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError("trusted source binding lookup is unavailable") from exc
        records = _records(raw)
        if len(records) != 1:
            raise SourceUnavailableError("trusted source binding is missing or ambiguous")
        record = _coerce_record(records[0])
        if record is None:
            raise SourceUnavailableError("trusted source binding is malformed")
        if record.entity_id != entity_id:
            raise SourceUnavailableError("trusted source binding entity mismatch")
        if not _same_pointer(record.normalized_source_url, normalized_source_url, record.derivative_ref):
            raise SourceUnavailableError("trusted source binding derivative mismatch")
        if record.derivative_ref.identity != _pointer_identity(normalized_source_url, record.derivative_ref):
            raise SourceUnavailableError("trusted derivative identity mismatch")
        if _looks_like_url(record.source_ref.file_id):
            # A raw normalized URL must never become an originating file ID.
            raise SourceUnavailableError("trusted source binding has a URL as file_id")
        return record.source_ref


class InMemorySourceBindingBackend:
    """Small strict backend useful for provider-neutral tests and local fakes."""

    def __init__(self, records: Sequence[SourceBindingRecord | Mapping[str, Any]] = ()) -> None:
        self.records: list[SourceBindingRecord] = []
        for value in records:
            record = _coerce_record(value)
            if record is None:
                raise ValueError("invalid source binding record")
            self.records.append(record)

    def register(self, record: SourceBindingRecord | Mapping[str, Any]) -> None:
        value = _coerce_record(record)
        if value is None:
            raise ValueError("invalid source binding record")
        self.records.append(value)

    def lookup_source_binding(self, entity_id: str, normalized_source_url: str) -> list[SourceBindingRecord]:
        return [
            record
            for record in self.records
            if record.entity_id == entity_id
            and _same_pointer(record.normalized_source_url, normalized_source_url, record.derivative_ref)
        ]


def _records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _coerce_record(value: Any) -> SourceBindingRecord | None:
    if isinstance(value, SourceBindingRecord):
        # Dataclass construction is intentionally lightweight and SourceRef
        # itself is a value object without a validation hook.  A typed record
        # is therefore untrusted input at this boundary just like a mapping.
        entity_id = value.entity_id
        pointer = value.normalized_source_url
        derivative = value.derivative_ref
        source = value.source_ref
        derivative_ref = _strict_source_ref(derivative)
        source_ref = _strict_source_ref(source)
        if not isinstance(entity_id, str) or not entity_id.strip():
            return None
        pointer_value = _pointer_text(pointer)
        if pointer_value is None or derivative_ref is None or source_ref is None:
            return None
        return SourceBindingRecord(
            entity_id.strip(), pointer_value, derivative_ref, source_ref
        )
    if not isinstance(value, Mapping):
        return None
    entity_id = value.get("entity_id", value.get("entityId"))
    pointer = value.get(
        "normalized_source_url",
        value.get("normalized_url", value.get("normalized_source_ref")),
    )
    derivative = value.get("derivative_ref", value.get("normalized_ref"))
    source = value.get(
        "source_ref",
        value.get("originating_ref", value.get("originating_source_ref")),
    )
    derivative_ref = _strict_source_ref(derivative)
    source_ref = _strict_source_ref(source)
    if not isinstance(entity_id, str) or not entity_id.strip():
        return None
    pointer_value = _pointer_text(pointer)
    if pointer_value is None or derivative_ref is None or source_ref is None:
        return None
    return SourceBindingRecord(
        entity_id.strip(), pointer_value, derivative_ref, source_ref
    )


def _strict_source_ref(value: Any) -> SourceRef | None:
    provider: Any
    file_id: Any
    web_url: Any
    if isinstance(value, SourceRef):
        provider, file_id, web_url = value.provider, value.file_id, value.web_url
    elif isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id")
        web_url = value.get("web_url")
    else:
        return None
    if not isinstance(provider, str) or not isinstance(file_id, str):
        return None
    ref = SourceRef(provider.strip(), file_id.strip(), web_url if isinstance(web_url, str) else None)
    if not _nonempty(ref.provider) or not _nonempty(ref.file_id):
        return None
    if _looks_like_url(ref.file_id):
        return None
    return ref


def _pointer_text(value: Any) -> str | None:
    if isinstance(value, SourceRef):
        return value.web_url or value.file_id
    if isinstance(value, Mapping):
        url = value.get("url", value.get("web_url"))
        if isinstance(url, str) and url.strip():
            return url.strip()
        provider = value.get("provider")
        file_id = value.get("file_id")
        if isinstance(provider, str) and isinstance(file_id, str) and file_id.strip():
            return file_id.strip()
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _same_pointer(left: str | SourceRef, right: str, derivative_ref: SourceRef) -> bool:
    left_text = _pointer_text(left)
    if left_text is None:
        return False
    return (
        _pointer_identity(left_text, derivative_ref) == derivative_ref.identity
        and _pointer_identity(right.strip(), derivative_ref) == derivative_ref.identity
    )


def _pointer_identity(pointer: str, derivative_ref: SourceRef) -> tuple[str, str]:
    # The graph pointer may be a URL or a provider file ID.  A URL is checked
    # against the registered derivative's navigational URL; a bare ID is
    # checked against the registered derivative file ID.
    if pointer == derivative_ref.file_id:
        return derivative_ref.identity
    # Recognize only a provider-supported file route, never a generic URL
    # with its query removed. The association still comes from the trusted
    # record; URL parsing cannot manufacture an originating SourceRef.
    if derivative_ref.provider == "google_drive":
        try:
            parsed = urlsplit(pointer)
        except ValueError:
            return ("", "")
        if parsed.hostname == "drive.google.com":
            parts = parsed.path.split("/")
            if (
                parsed.scheme == "https"
                and parsed.netloc == "drive.google.com"
                and len(parts) in {4, 5}
                and parts[1:3] == ["file", "d"]
                and (len(parts) == 4 or parts[4] in {"", "view", "preview", "edit"})
                and parts[3] == derivative_ref.file_id
            ):
                return derivative_ref.identity
            return ("", "")
    if derivative_ref.web_url == pointer:
        return derivative_ref.identity
    # A URL pointer must not be silently converted into a file identity.  The
    # mismatch is reported by the caller.
    return ("", "")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _looks_like_url(value: str) -> bool:
    lowered = value.casefold()
    return lowered.startswith(("http://", "https://"))


__all__ = [
    "InMemorySourceBindingBackend",
    "SourceBindingBackend",
    "SourceBindingRecord",
    "SourceBindingResolver",
    "ValidatedSourceBindingResolver",
]
