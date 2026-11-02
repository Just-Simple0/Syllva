"""Canvas attachment download -> private Drive -> canvas_drive_bindings (plan §6.2 r15, P-E).

The fetcher is the ONLY writer of ``canvas_drive_bindings`` (the trusted Canvas download path of
plan §6.3 R5).  Every upload goes through a persisted intent (pe-invariants E7, rev2):

1. the attachment version (key, filename, size, Canvas ``updated_at``) is already settled -> nothing;
2. the Canvas File object is read (course-scoped, exact id/filename/size) and checked for eligibility;
3. an intent for this version exists (an earlier create whose outcome was not seen): recovery is
   allowed only for exactly one marker file whose Drive bytes hash to the intent's Canvas SHA-256;
   no marker file keeps it UPLOAD_UNCERTAIN - a second create is never made;
4. a marker file WITHOUT an intent proves nothing (PROVENANCE_UNVERIFIED);
5. otherwise: Canvas download -> (same bytes as an existing binding of the attachment: nothing new)
   -> destination folder re-verified private -> intent PENDING -> Drive create -> binding -> BOUND.

Discovery hides marker files that have no binding (E9).  Nothing here writes to Notion; Canvas is
only read; no URL, verifier or token is stored or reported.  The worker lock makes this path the
single writer.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..adapters.drive.worker import DRIVE_FOLDER_MIME, require_private_ownership
from ..settings.canvas_checks import CanvasCheckError
from .classification.rules import CANVAS_ANNOUNCEMENT, CANVAS_MODULE_ITEM
from .classification.taxonomy import (
    CODE_EXTENSIONS,
    CODE_MIME_TYPES_BY_EXTENSION,
    PDF_MIME,
    TABULAR_MIME_TYPES,
)

MARKER_ROLE = "CANVAS_ATTACHMENT"
MARKER_ROLE_KEY = "uls_r"
MARKER_DIGEST_KEY = "uls_t"
MAX_FILENAME_CHARS = 200
_BAD_NAME = re.compile(r"[/\\\x00-\x1f\x7f]")
_OCTET = "application/octet-stream"

# Upload MIME per extension (the value handling_mode accepts for that extension, §6.1 matrix).
UPLOAD_MIME: dict[str, str] = {
    "pdf": PDF_MIME,
    **{ext: "text/plain" for ext in CODE_EXTENSIONS if ext != "ipynb"},
    "ipynb": "application/x-ipynb+json",
    "csv": "text/csv",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# Canvas content-types accepted per extension before any byte is downloaded (E2).
_ACCEPTED_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "pdf": frozenset({PDF_MIME}),
    **{ext: CODE_MIME_TYPES_BY_EXTENSION[ext] | {_OCTET} for ext in CODE_EXTENSIONS},
    **{ext: TABULAR_MIME_TYPES[ext] | {_OCTET} for ext in TABULAR_MIME_TYPES},
}

SKIP_CODES = ("UNSUPPORTED_TYPE", "TOO_LARGE", "LOCKED", "BAD_NAME")


@dataclass(frozen=True)
class AttachmentCandidate:
    """One attachment seen in a COMPLETE observation (memory only; E1)."""

    resource_kind: str          # announcement | module_item
    resource_id: str
    observation_revision: int
    attachment_id: str          # Canvas file id (announcement attachment id / module item content_id)
    filename: str | None = None  # known from the announcement listing; None for module File items
    size: int | None = None
    content_type: str | None = None
    locked: bool = False
    updated_at: str | None = None  # the listing's attachment version (announcements)


@dataclass
class FetchReport:
    downloaded: int = 0
    recovered: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    codes: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {"downloaded": self.downloaded, "recovered": self.recovered, "unchanged": self.unchanged,
                "skipped": self.skipped, "failed": self.failed, "last_codes": dict(sorted(self.codes.items()))}


class AttachmentReader(Protocol):
    def file_metadata(self, course_id: int, file_id: str, deadline: float) -> Any: ...

    def download(self, url: str, *, course_id: int, file_id: str, expected_size: int, deadline: float) -> bytes: ...


class _Skip(Exception):
    def __init__(self, code: str, *, failed: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.failed = failed


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def eligibility(filename: str | None, size: Any, content_type: str | None, locked: bool,
                max_bytes: int) -> str | None:
    """None when the attachment may be downloaded, else a fixed skip code (E2; pure)."""

    if locked:
        return "LOCKED"
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > MAX_FILENAME_CHARS
            or _BAD_NAME.search(filename) or filename.strip() in {".", ".."}
            or any(unicodedata.category(char) in ("Cc", "Cf") for char in filename)):
        return "BAD_NAME"           # R-PEI-02: every Unicode control/format character, not only ASCII
    ext = extension_of(filename)
    accepted = _ACCEPTED_CONTENT_TYPES.get(ext)
    if accepted is None or (content_type or "").lower() not in accepted:
        return "UNSUPPORTED_TYPE"
    if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > max_bytes:
        return "TOO_LARGE"
    return None


def attachment_fingerprint(origin: str, course_id: int, candidate: AttachmentCandidate,
                           filename: str, size: int, updated_at: str | None = None) -> str:
    """One attachment VERSION: key, filename, size and the Canvas file's updated_at."""
    return hashlib.sha256(json.dumps(
        ["canvas-attachment.v2", origin, int(course_id), candidate.resource_kind, candidate.resource_id,
         candidate.attachment_id, filename, int(size), updated_at],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def marker_for(fingerprint: str) -> dict[str, str]:
    return {MARKER_ROLE_KEY: MARKER_ROLE, MARKER_DIGEST_KEY: fingerprint}


def is_attachment_marker(app_properties: Mapping[str, str]) -> bool:
    return app_properties.get(MARKER_ROLE_KEY) == MARKER_ROLE


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def candidate_from_announcement(row: Mapping[str, Any], resource_id: str, revision: int) -> AttachmentCandidate | None:
    """One ``attachments[]`` entry of an announcement listing row, or None if it is not readable."""

    file_id = row.get("id")
    if isinstance(file_id, bool) or not isinstance(file_id, int | str) or not str(file_id).isdecimal():
        return None
    size = row.get("size")
    return AttachmentCandidate(
        CANVAS_ANNOUNCEMENT, resource_id, revision, str(file_id),
        filename=_text(row.get("display_name")) or _text(row.get("filename")),
        size=size if isinstance(size, int) and not isinstance(size, bool) else None,
        content_type=_text(row.get("content-type")),
        locked=row.get("locked_for_user") is True or row.get("hidden") is True or row.get("locked") is True,
        updated_at=_text(row.get("updated_at")) or _text(row.get("modified_at")),
    )


def candidate_from_module_item(row: Mapping[str, Any], resource_id: str, revision: int) -> AttachmentCandidate | None:
    if row.get("type") != "File":
        return None
    content_id = row.get("content_id")
    if isinstance(content_id, bool) or not isinstance(content_id, int | str) or not str(content_id).isdecimal():
        return None
    return AttachmentCandidate(CANVAS_MODULE_ITEM, resource_id, revision, str(content_id))


class CanvasAttachmentFetcher:
    def __init__(
        self,
        state: Any,
        drive: Any,
        *,
        origin: str,
        reader_factory: Callable[[], AbstractContextManager[AttachmentReader | None]],
        max_bytes: int,
        monotonic: Callable[[], float] = time.monotonic,
        per_file_seconds: float = 60.0,
    ) -> None:
        self._state = state
        self._drive = drive
        self._origin = origin
        self._factory = reader_factory
        # R-PE-08: an attachment is eligible only if it can also be read back from Drive for recovery.
        drive_limit = getattr(drive, "max_bytes", None)
        self._max_bytes = int(max_bytes) if not isinstance(drive_limit, int) else min(int(max_bytes), drive_limit)
        self._monotonic = monotonic
        self._per_file_seconds = per_file_seconds
        self._recovered = 0

    def fetch_course(self, course_id: int, candidates: Iterable[AttachmentCandidate], folder_id: str,
                     budget: int, report: FetchReport) -> int:
        """Process the candidates of one COMPLETE observation; returns the budget left (E13).
        A failure of one attachment (including a lost create response) never stops the others."""

        pending: list[AttachmentCandidate] = []
        for candidate in candidates:
            try:
                settled = self._settled(course_id, candidate)
            except Exception:  # noqa: BLE001 - H-PEI-01: one unreadable key never stops the others
                report.failed += 1
                report.codes["STATE_UNAVAILABLE"] += 1
                continue
            if settled:
                report.unchanged += 1
                continue
            if candidate.resource_kind == CANVAS_ANNOUNCEMENT or candidate.locked:
                # R-PEI-01: an announcement attachment is always judged on its listing first, so a
                # listing entry without a name or size never costs a Canvas file request.
                code = eligibility(candidate.filename, candidate.size, candidate.content_type,
                                   candidate.locked, self._max_bytes)
                if code is not None:
                    report.skipped += 1
                    report.codes[code] += 1
                    continue
            pending.append(candidate)
        if not pending or budget <= 0:
            return budget
        self._recovered = 0
        try:
            with self._factory() as reader:
                if reader is None:
                    report.codes["NO_CREDENTIAL"] += 1
                    return budget
                for candidate in pending:
                    if budget <= 0:
                        break
                    budget -= 1
                    try:
                        outcome = self._one(reader, course_id, candidate, folder_id)
                    except _Skip as skip:
                        if skip.failed:
                            report.failed += 1
                        else:
                            report.skipped += 1
                        report.codes[skip.code] += 1
                        continue
                    except Exception:  # noqa: BLE001 - one attachment never blocks the others (E13)
                        report.failed += 1
                        report.codes["FETCH_FAILED"] += 1
                        continue
                    if outcome == "recovered":
                        pass                     # counted in _recover() (H-PEI-02)
                    elif outcome == "unchanged":
                        report.unchanged += 1
                    else:
                        report.downloaded += 1
        except Exception:  # noqa: BLE001 - the connection could not be opened: fixed code only
            report.failed += 1
            report.codes["CONNECTION_FAILED"] += 1
        # H-PEI-02: every settled earlier create counts, also one settled on the way to a new version.
        report.recovered += self._recovered
        return budget

    # --- one attachment -------------------------------------------------------------

    def _settled(self, course_id: int, candidate: AttachmentCandidate) -> bool:
        """The attachment version needs no request at all (E7 1, E10)."""

        key = self._key(course_id, candidate)
        intents = [i for i in self._state.list_canvas_attachment_intents(**key)
                   if i["status"] in ("BOUND", "SAME_BYTES")]
        if candidate.filename is None and candidate.size is None:
            # A module File item carries no version in its listing: a settled upload of its file id
            # is final (a file replaced in place under the same id is a documented limitation).
            return bool(intents) or bool(self._state.list_canvas_drive_bindings_for_attachment(**key))
        return any(i["filename"] == candidate.filename and i["size"] == candidate.size
                   and i["canvas_updated_at"] == candidate.updated_at for i in intents)

    def _key(self, course_id: int, candidate: AttachmentCandidate) -> dict[str, Any]:
        """The attachment key A of E1 - including the Canvas origin (R-PE2-04)."""
        return {"origin": self._origin, "canvas_course_id": course_id, "resource_kind": candidate.resource_kind,
                "resource_id": candidate.resource_id, "attachment_id": candidate.attachment_id}

    def _one(self, reader: AttachmentReader, course_id: int, candidate: AttachmentCandidate, folder_id: str) -> str:
        deadline = self._monotonic() + self._per_file_seconds
        try:
            meta = reader.file_metadata(course_id, candidate.attachment_id, deadline)
        except CanvasCheckError as error:
            raise _Skip(f"CANVAS_{error.code}", failed=True) from None
        if not isinstance(meta, Mapping) or str(meta.get("id")) != candidate.attachment_id:
            raise _Skip("FILE_MISMATCH", failed=True)
        # Canvas ``filename`` is URL-escaped; ``display_name`` is the name a person sees.
        filename = _text(meta.get("display_name")) or _text(meta.get("filename"))
        size = meta.get("size")
        content_type = _text(meta.get("content-type"))
        updated_at = _text(meta.get("updated_at")) or _text(meta.get("modified_at"))
        if candidate.filename is not None and (filename != candidate.filename or size != candidate.size):
            raise _Skip("FILE_MISMATCH", failed=True)
        locked = candidate.locked or meta.get("locked_for_user") is True or meta.get("hidden") is True \
            or meta.get("locked") is True
        code = eligibility(filename, size, content_type, locked, self._max_bytes)
        if code is not None:
            raise _Skip(code)
        if not isinstance(filename, str) or isinstance(size, bool) or not isinstance(size, int):
            raise _Skip("BAD_NAME")         # unreachable after eligibility(); keeps the types explicit
        fingerprint = attachment_fingerprint(self._origin, course_id, candidate, filename, size, updated_at)
        marker = marker_for(fingerprint)
        intent = self._state.get_canvas_attachment_intent(fingerprint)
        if intent is not None and intent["status"] in ("BOUND", "SAME_BYTES"):
            return "unchanged"
        if intent is not None and intent["status"] == "CONFLICT":
            raise _Skip("BINDING_CONFLICT", failed=True)
        if intent is not None:
            return self._recover(course_id, candidate, intent, folder_id)
        # R-PE2-01: an earlier version of the same attachment whose create was never seen must be
        # settled first - otherwise a new version could duplicate bytes that already landed.
        for earlier in self._state.list_canvas_attachment_intents(**self._key(course_id, candidate)):
            if earlier["status"] in ("PENDING", "UNCERTAIN"):
                self._recover(course_id, candidate, earlier, folder_id)
        found = [item for item in self._drive.search_marker(marker) if not item.trashed]
        if found:
            # A marker file with no recorded Canvas download proves nothing about its bytes.
            raise _Skip("PROVENANCE_UNVERIFIED", failed=True)
        url = meta.get("url")
        if not isinstance(url, str):
            raise _Skip("DOWNLOAD_URL_NOT_ALLOWED", failed=True)
        try:
            data = reader.download(url, course_id=course_id, file_id=candidate.attachment_id,
                                   expected_size=size, deadline=deadline)
        except CanvasCheckError as error:
            code = {"DESTINATION_NOT_ALLOWED": "DOWNLOAD_HOST_NOT_ALLOWED",
                    "CHECK_NOT_READ_ONLY": "DOWNLOAD_URL_NOT_ALLOWED"}.get(error.code, f"CANVAS_{error.code}")
            raise _Skip(code, failed=True) from None
        if len(data) != size:
            raise _Skip("SIZE_MISMATCH", failed=True)
        sha = hashlib.sha256(data).hexdigest()
        values = {"fingerprint": fingerprint, "origin": self._origin, "canvas_course_id": course_id,
                  "resource_kind": candidate.resource_kind, "resource_id": candidate.resource_id,
                  "observation_revision": candidate.observation_revision, "attachment_id": candidate.attachment_id,
                  "filename": filename, "size": size, "canvas_updated_at": updated_at, "byte_sha256": sha,
                  "folder_id": folder_id}
        same = [b for b in self._state.list_canvas_drive_bindings_for_attachment(**self._key(course_id, candidate))
                if b["byte_sha256"] == sha]
        if same:
            # A new Canvas version with the very same bytes: the bound Drive file already holds them.
            self._state.record_canvas_attachment_intent(**values, status="SAME_BYTES",
                                                        drive_file_id=same[0]["drive_file_id"])
            return "unchanged"
        if self._state.canvas_drive_binding_slot(
                canvas_course_id=course_id, resource_kind=candidate.resource_kind, resource_id=candidate.resource_id,
                observation_revision=candidate.observation_revision, attachment_id=candidate.attachment_id) is not None:
            # The (resource, revision, attachment) slot is already bound (another version or another
            # origin with the same numeric ids): an upload could never be bound - none is made.
            raise _Skip("BINDING_CONFLICT", failed=True)
        self._require_private_folder(folder_id)
        self._state.record_canvas_attachment_intent(**values, status="PENDING")
        try:
            created = self._drive.create_file_with_marker(
                folder_id, filename, UPLOAD_MIME[extension_of(filename)], data, marker)
        except Exception:  # noqa: BLE001 - the outcome is unknown: never create this version again
            self._state.set_canvas_attachment_intent_status(fingerprint, "UNCERTAIN")
            raise _Skip("UPLOAD_UNCERTAIN", failed=True) from None
        self._bind(course_id, candidate, fingerprint, created.file_id, filename, size, sha,
                   candidate.observation_revision)
        return "downloaded"

    def _recover(self, course_id: int, candidate: AttachmentCandidate, intent: Mapping[str, Any],
                 folder_id: str) -> str:
        """Settle an intent whose create outcome was never seen.  Everything comes from the intent
        (what was downloaded, where, under which observation revision - R-PE2-02), nothing from the
        current observation."""

        key = self._key(course_id, candidate)
        if any(str(intent[name]) != str(value) for name, value in key.items()):
            raise _Skip("MARKER_CONFLICT", failed=True)
        filename, size = str(intent["filename"]), int(intent["size"])
        marker = marker_for(str(intent["fingerprint"]))
        found = [item for item in self._drive.search_marker(marker) if not item.trashed]
        if not found:
            # The create may still land or may never have happened: no second create (R-PE-02).
            self._state.set_canvas_attachment_intent_status(intent["fingerprint"], "UNCERTAIN")
            raise _Skip("UPLOAD_UNCERTAIN", failed=True)
        if len(found) > 1:
            raise _Skip("MARKER_CONFLICT", failed=True)
        item = found[0]
        if (item.parents != (intent["folder_id"],) or intent["folder_id"] != folder_id or item.size != size
                or item.app_properties != dict(marker)):
            raise _Skip("MARKER_CONFLICT", failed=True)
        try:
            require_private_ownership(item, context="Canvas attachment")
        except Exception:  # noqa: BLE001 - not provably private: never bound
            raise _Skip("NOT_PRIVATE", failed=True) from None
        data = self._drive.download(item.file_id, max_bytes=size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != intent["byte_sha256"]:
            # The Drive bytes are not the bytes the trusted path downloaded from Canvas.
            raise _Skip("PROVENANCE_UNVERIFIED", failed=True)
        self._bind(course_id, candidate, intent["fingerprint"], item.file_id, filename, size, intent["byte_sha256"],
                   int(intent["observation_revision"]))
        self._recovered += 1
        return "recovered"

    def _require_private_folder(self, folder_id: str) -> None:
        """R-PE-07: the destination is re-read right before the create and must still be a private,
        owner-only, untrashed folder (the create readback is checked again by the adapter)."""

        try:
            folder = self._drive.read_metadata(folder_id)
            if folder.mime_type != DRIVE_FOLDER_MIME or folder.trashed or folder.file_id != folder_id:
                raise ValueError("not a folder")
            require_private_ownership(folder, context="Canvas attachment destination")
        except Exception:  # noqa: BLE001 - fixed code only; nothing is written
            raise _Skip("FOLDER_NOT_PRIVATE", failed=True) from None

    def _bind(self, course_id: int, candidate: AttachmentCandidate, fingerprint: str, drive_file_id: str,
              filename: str, size: int, sha: str, revision: int) -> None:
        try:
            self._state.record_canvas_drive_binding(
                drive_file_id=drive_file_id, canvas_course_id=course_id, resource_kind=candidate.resource_kind,
                resource_id=candidate.resource_id, observation_revision=revision,
                attachment_id=candidate.attachment_id, attachment_filename=filename, attachment_size=size,
                byte_sha256=sha, origin=self._origin)
        except (ValueError, sqlite3.IntegrityError):
            self._state.set_canvas_attachment_intent_status(fingerprint, "CONFLICT", drive_file_id=drive_file_id)
            raise _Skip("BINDING_CONFLICT", failed=True) from None
        self._state.set_canvas_attachment_intent_status(fingerprint, "BOUND", drive_file_id=drive_file_id)


__all__ = [
    "MARKER_ROLE",
    "SKIP_CODES",
    "UPLOAD_MIME",
    "AttachmentCandidate",
    "AttachmentReader",
    "CanvasAttachmentFetcher",
    "FetchReport",
    "attachment_fingerprint",
    "candidate_from_announcement",
    "candidate_from_module_item",
    "eligibility",
    "is_attachment_marker",
    "marker_for",
]
