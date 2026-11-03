"""Canonical JSON for ``request_hash`` (spec §6.2; RFC 8785-compatible for the values used here).

UTF-8, keys sorted by code point, no whitespace, integers only (no floats), strings
NFC. This module is the only implementation; the Shell never recomputes it.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValueError("floats are not allowed in canonical JSON")  # noqa: TRY004 - a value error for callers
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("keys must be strings")  # noqa: TRY004 - a value error for callers
            out[unicodedata.normalize("NFC", key)] = _normalize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise ValueError(f"unsupported type {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
