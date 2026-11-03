"""Structured logging with redaction (spec §13.1, I9, C-20).

``log_event(code, **fields)`` is the only way the Core logs. Field names that
look like secrets or content are replaced by ``<redacted>``; the pattern extends
§13.1 with the R2 protocol names (bootstrap, mac, nonce, sid, csrf, session —
R2 plan rev 2, errata E26). Protocol lines are never logged, only their type and
size. Values are truncated; ``Secret`` objects render as ``<secret>``.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACT = re.compile(
    r"secret|token|password|cookie|authorization|prompt|response|body|bootstrap|mac|nonce|sid|csrf|session|path",
    re.IGNORECASE,
)
MAX_VALUE = 200
RETENTION_DAYS = 7
MAX_FILE_BYTES = 10 * 1024 * 1024

_lock = threading.Lock()
_log_dir: Path | None = None


def configure(log_dir: Path | None) -> None:
    global _log_dir
    with _lock:
        _log_dir = log_dir
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            _prune(log_dir)


def _prune(log_dir: Path) -> None:
    today = datetime.now(UTC).date()
    for path in log_dir.glob("syllva-*.jsonl*"):
        try:
            day = datetime.strptime(path.name[7:15] + "+0000", "%Y%m%d%z").date()
        except ValueError:
            continue
        if (today - day).days > RETENTION_DAYS:
            path.unlink(missing_ok=True)


def sanitize(value: Any, key: str = "") -> Any:
    if key and REDACT.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def log_event(code: str, **fields: Any) -> None:
    record = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "code": code, **sanitize(fields)}
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _lock:
        if _log_dir is None:
            return
        target = _log_dir / f"syllva-{datetime.now(UTC):%Y%m%d}.jsonl"
        if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
            target.rename(target.with_suffix(f".jsonl.{datetime.now(UTC):%H%M%S}"))
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
