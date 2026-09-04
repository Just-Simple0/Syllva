"""Small provider-shape compatibility helpers for read-only retrieval.

The fakes and a real Notion adapter may expose flat dictionaries, Notion-like
``properties`` dictionaries, or simple objects.  These helpers normalize that
shape without importing a provider SDK or a write-capable adapter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from uls.adapters.notion.base import normalize_alias, parse_aliases
from uls.domain.source_ref import SourceFingerprint, SourceRef


def normal_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _field_spellings(name: str) -> set[str]:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).casefold()
    return {normal_key(name), normal_key(snake), normal_key(name.replace(" ", "_"))}


def unwrap(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "value" in value and len(value) == 1:
            return unwrap(value["value"])
        for property_name in ("title", "rich_text", "select", "status", "number", "checkbox", "date"):
            if property_name in value and len(value) <= 2:
                inner = value[property_name]
                if property_name in {"title", "rich_text"} and isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
                    pieces: list[str] = []
                    for item in inner:
                        if isinstance(item, Mapping):
                            text = item.get("plain_text")
                            if text is None and isinstance(item.get("text"), Mapping):
                                text = item["text"].get("content")
                            if text is not None:
                                pieces.append(str(text))
                        elif item is not None:
                            pieces.append(str(item))
                    return "".join(pieces)
                return unwrap(inner)
        if "name" in value and len(value) <= 2:
            return value["name"]
        if "plain_text" in value and len(value) <= 2:
            return value["plain_text"]
        if "content" in value and len(value) <= 2:
            return value["content"]
    return value


def properties(record: Any) -> Any:
    if isinstance(record, Mapping) and isinstance(record.get("properties"), Mapping):
        return record["properties"]
    candidate = getattr(record, "properties", None)
    if isinstance(candidate, Mapping):
        return candidate
    return record


def field(record: Any, *names: str, default: Any = None) -> Any:
    source = properties(record)
    wanted = set().union(*(_field_spellings(name) for name in names))
    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, str) and normal_key(key) in wanted:
                return unwrap(value)
    else:
        for name in names:
            for candidate in (
                name,
                name.casefold(),
                name.replace(" ", "_"),
                name.casefold().replace(" ", "_"),
            ):
                if hasattr(source, candidate):
                    return unwrap(getattr(source, candidate))
    return default


def raw_field(record: Any, *names: str, default: Any = None) -> Any:
    source = properties(record)
    wanted = set().union(*(_field_spellings(name) for name in names))
    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, str) and normal_key(key) in wanted:
                return value
    else:
        for name in names:
            for candidate in (
                name,
                name.casefold(),
                name.replace(" ", "_"),
                name.casefold().replace(" ", "_"),
            ):
                if hasattr(source, candidate):
                    return getattr(source, candidate)
    return default


def text(value: Any, default: str | None = None) -> str | None:
    value = unwrap(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is not None and not isinstance(value, (Mapping, Sequence)):
        return str(value)
    return default


def record_id(record: Any) -> str | None:
    value = text(field(record, "ID", "Entity ID", "id", "entity_id"))
    if value:
        return value
    if isinstance(record, Mapping):
        return text(record.get("id", record.get("entity_id")))
    return text(getattr(record, "id", getattr(record, "entity_id", None)))


def record_label(record: Any) -> str:
    value = text(field(record, "Name", "Title", "name", "title"), default=None)
    if value:
        return value
    if isinstance(record, Mapping):
        return text(record.get("name", record.get("title")), default="") or ""
    return text(getattr(record, "name", getattr(record, "title", None)), default="") or ""


def record_aliases(record: Any) -> list[str]:
    raw = raw_field(record, "Aliases", "aliases")
    parsed: list[str] = []
    for value in _alias_values(raw):
        parsed.extend(parse_aliases(value))
    for name in ("ID", "Entity ID", "Name"):
        value = text(field(record, name))
        if value:
            parsed.append(value)
    # Preserve first occurrence order while removing duplicates.
    result: list[str] = []
    seen: set[str] = set()
    for alias in parsed:
        key = normalize_alias(alias)
        if key and key not in seen:
            result.append(alias)
            seen.add(key)
    return result


def _alias_values(value: Any) -> list[str]:
    """Extract Rich-text chunks before applying the shared alias parser."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        if "value" in value and len(value) == 1:
            return _alias_values(value["value"])
        for key in ("rich_text", "title"):
            if key in value:
                return _alias_values(value[key])
        text_value = value.get("plain_text")
        if text_value is None and isinstance(value.get("text"), Mapping):
            text_value = value["text"].get("content")
        if text_value is not None:
            return [str(text_value)]
        return []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result: list[str] = []
        for item in value:
            result.extend(_alias_values(item))
        return result
    return [str(value)]


def exact_alias_match(record: Any, query: str) -> bool:
    def comparison(value: str) -> str:
        return re.sub(r"\s+", " ", normalize_alias(value))

    wanted = comparison(query)
    return bool(wanted) and any(comparison(alias) == wanted for alias in record_aliases(record))


def is_truthy(value: Any) -> bool:
    value = unwrap(value)
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "y", "on", "t", "verified", "approve"}
    return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0)


def is_strict_true(value: Any) -> bool:
    """Return ``True`` only for an actual boolean ``True`` checkbox value.

    ``is_truthy`` remains useful for non-authorizing compatibility fields, but
    retrieval's ``Verified`` gate is a Checkbox contract.  String and numeric
    representations must therefore fail closed instead of being promoted to
    verified evidence.
    """

    return unwrap(value) is True


def relation_id(value: Any) -> str | None:
    value = unwrap(value)
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("id", "ID", "entity_id", "Entity ID", "page_id"):
            result = relation_id(value.get(key))
            if result:
                return result
        for key in ("relation", "people", "results"):
            if key in value:
                result = relation_id(value[key])
                if result:
                    return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            result = relation_id(item)
            if result:
                return result
    if value is not None:
        for name in ("id", "ID", "entity_id", "page_id"):
            if hasattr(value, name):
                result = relation_id(getattr(value, name))
                if result:
                    return result
    return None


def coerce_source_ref(value: Any, *, default_provider: str = "google_drive") -> SourceRef | None:
    if isinstance(value, SourceRef):
        return value
    value = unwrap(value)
    if isinstance(value, Mapping):
        provider = value.get("provider", default_provider)
        file_id = value.get(
            "file_id",
            value.get("id", value.get("fileId", value.get("url"))),
        )
        if isinstance(provider, str) and isinstance(file_id, str) and file_id.strip():
            web_url = value.get("web_url", value.get("url"))
            return SourceRef(provider.strip(), file_id.strip(), web_url if isinstance(web_url, str) else None)
    if value is not None and hasattr(value, "provider") and hasattr(value, "file_id"):
        provider = getattr(value, "provider")
        file_id = getattr(value, "file_id")
        web_url = getattr(value, "web_url", None)
        if isinstance(provider, str) and isinstance(file_id, str) and file_id.strip():
            return SourceRef(
                provider.strip(),
                file_id.strip(),
                web_url if isinstance(web_url, str) else None,
            )
    if isinstance(value, str) and value.strip():
        return SourceRef(default_provider, value.strip())
    return None


def coerce_fingerprint(value: Any) -> SourceFingerprint | None:
    if isinstance(value, SourceFingerprint):
        return value
    value = unwrap(value)
    if isinstance(value, Mapping):
        version = value.get("source_version", value.get("version"))
        source_hash = value.get("source_hash", value.get("hash"))
    elif value is not None and hasattr(value, "source_version") and hasattr(value, "source_hash"):
        version = getattr(value, "source_version")
        source_hash = getattr(value, "source_hash")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        version, source_hash = value
    else:
        return None
    if isinstance(version, str) and version.strip().isdigit():
        version = int(version.strip())
    if isinstance(version, float) and version.is_integer():
        version = int(version)
    if (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 1
        and isinstance(source_hash, str)
        and source_hash
    ):
        return SourceFingerprint(version, source_hash.strip())
    return None


def record_fingerprint(record: Any) -> SourceFingerprint | None:
    direct = coerce_fingerprint(field(record, "Fingerprint", "Source Fingerprint", "source_fingerprint"))
    if direct is not None:
        return direct
    version = field(record, "Source Version", "Current Source Version", "source_version")
    source_hash = field(record, "Source Hash", "source_hash", "Hash")
    return coerce_fingerprint({"source_version": version, "source_hash": source_hash})


def relation_records(value: Any) -> list[Any]:
    value = unwrap(value)
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


__all__ = [
    "coerce_fingerprint",
    "coerce_source_ref",
    "exact_alias_match",
    "field",
    "is_truthy",
    "is_strict_true",
    "normal_key",
    "properties",
    "raw_field",
    "record_aliases",
    "record_fingerprint",
    "record_id",
    "record_label",
    "relation_id",
    "relation_records",
    "text",
    "unwrap",
]
