"""Intake classification v2 taxonomy (docs/plans/intake-classification-v2.md §2, §5, §6.1).

Everything here is pure data: the document Kinds, the ``origin`` axis that
carries ownership independently of Kind, the AI-owned chunk tag vocabulary,
the exact HUMAN Kind option set for the v2 Notion profiles, the Materials
``Type`` initial-value table and the handling-mode matrix.  Versions are
recorded on every classification record so that a later rule change never
silently revalidates an old decision.
"""

from __future__ import annotations

import io
from enum import Enum
from typing import Final

RULE_TABLE_VERSION: Final[str] = "icv2-r14"
TAG_VOCAB_VERSION: Final[str] = "icv2-tags-1"
TAG_RULE_VERSION: Final[str] = "icv2-tagrules-1"
QUESTION_SET_VERSION: Final[str] = "icv2-questions-1"
S4_PAYLOAD_POLICY_VERSION: Final[str] = "icv2-s4payload-1"


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class Kind(_ValueEnum):
    """Document-level Kind: the single primary purpose of a source (§2.1)."""

    TRANSCRIPT = "TRANSCRIPT"
    LECTURE_SLIDES = "LECTURE_SLIDES"
    LAB_MATERIAL = "LAB_MATERIAL"
    PROVIDED_CODE = "PROVIDED_CODE"
    ASSIGNMENT_BRIEF = "ASSIGNMENT_BRIEF"
    ASSIGNMENT_RESOURCE = "ASSIGNMENT_RESOURCE"
    SETUP_GUIDE = "SETUP_GUIDE"
    COURSE_INFO = "COURSE_INFO"
    SUPPLEMENT = "SUPPLEMENT"
    EXAM = "EXAM"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    RECORDING = "RECORDING"
    UNSUPPORTED = "UNSUPPORTED"


class Origin(_ValueEnum):
    """Who produced the bytes; never derived from Kind (§2.4)."""

    PROFESSOR_SOURCE = "PROFESSOR_SOURCE"
    USER_AUTHORED_TRANSCRIPT = "USER_AUTHORED_TRANSCRIPT"
    USER_NOTE = "USER_NOTE"
    STUDENT_SUBMISSION = "STUDENT_SUBMISSION"
    UNKNOWN = "UNKNOWN"


class ChunkTag(_ValueEnum):
    """AI-owned chunk tags (§2.2)."""

    ASSIGNMENT = "assignment"
    EXAMPLE_CODE = "example_code"
    EXERCISE = "exercise"
    EXAM_HINT = "exam_hint"
    SETUP_STEP = "setup_step"
    SCHEDULE = "schedule"
    GRADING = "grading"
    SUBMISSION_GUIDE = "submission_guide"
    REFERENCE = "reference"
    RESOURCE_LINK = "resource_link"


class HandlingMode(_ValueEnum):
    """Authoritative processing mode (§6.1 matrix)."""

    NORMALIZE = "NORMALIZE"
    REGISTER_OPAQUE_NO_RETRIEVAL = "REGISTER_OPAQUE_NO_RETRIEVAL"
    METADATA_ONLY = "METADATA_ONLY"
    S3 = "S3"


class DecisionType(_ValueEnum):
    RULE = "rule"
    MODEL = "model"
    HUMAN = "human"


# Kinds the S2 classifier may choose between (§3.2): never the three that are
# decided purely by source type or the terminal UNSUPPORTED.
S2_CHOICE_KINDS: Final[tuple[Kind, ...]] = (
    Kind.TRANSCRIPT, Kind.LECTURE_SLIDES, Kind.LAB_MATERIAL, Kind.PROVIDED_CODE,
    Kind.ASSIGNMENT_BRIEF, Kind.ASSIGNMENT_RESOURCE, Kind.SETUP_GUIDE, Kind.COURSE_INFO,
    Kind.SUPPLEMENT, Kind.EXAM,
)

# Exact HUMAN Input Request ``Kind`` option set for the v2 Notion profiles
# (§5, r4 M5).  ``MATERIAL_PDF`` stays as the HUMAN-compatible legacy input and
# is never produced by the automatic path.
FILE_KINDS_V2: Final[tuple[str, ...]] = (
    "TRANSCRIPT", "LECTURE_SLIDES", "LAB_MATERIAL", "PROVIDED_CODE", "ASSIGNMENT_BRIEF",
    "ASSIGNMENT_RESOURCE", "SETUP_GUIDE", "COURSE_INFO", "SUPPLEMENT", "EXAM", "MATERIAL_PDF",
)

# File Intake / Materials ``AI Kind`` option set: every Kind the automatic
# path can record.
AI_KIND_OPTIONS: Final[tuple[str, ...]] = tuple(kind.value for kind in Kind)
ORIGIN_OPTIONS: Final[tuple[str, ...]] = tuple(origin.value for origin in Origin)

# Materials.Type is USER owned; the automatic path only supplies the creation
# time initial value (§2.5, §5).  Human-chosen Material Role always wins.
MATERIAL_TYPE_INITIAL: Final[dict[Kind, str]] = {
    Kind.LECTURE_SLIDES: "Lecture Slides",
    Kind.LAB_MATERIAL: "Professor Notes",
    Kind.ASSIGNMENT_BRIEF: "Professor Notes",
    Kind.COURSE_INFO: "Syllabus",
    Kind.SUPPLEMENT: "Supplementary",
    Kind.PROVIDED_CODE: "Reference",
    Kind.ASSIGNMENT_RESOURCE: "Reference",
    Kind.SETUP_GUIDE: "Reference",
    Kind.EXAM: "Reference",
}

# HUMAN Material Role option set for the v2 profiles = Materials.Type set
# (§5, r3 R4).  TRANSCRIPT forbids a role; every Material Kind requires one.
MATERIAL_ROLES_V2: Final[tuple[str, ...]] = (
    "Lecture Slides", "Professor Notes", "Syllabus", "Textbook", "Reference", "Supplementary",
)

# Kinds whose Drive bytes become a normalized, searchable Material.
PDF_MATERIAL_KINDS: Final[frozenset[Kind]] = frozenset({
    Kind.LECTURE_SLIDES, Kind.SUPPLEMENT, Kind.COURSE_INFO, Kind.SETUP_GUIDE, Kind.EXAM,
    Kind.LAB_MATERIAL, Kind.ASSIGNMENT_BRIEF,
})
# Kinds registered as opaque Materials without normalization or retrieval.
OPAQUE_KINDS: Final[frozenset[Kind]] = frozenset({Kind.PROVIDED_CODE, Kind.ASSIGNMENT_RESOURCE})
# Kinds that are assignment content for the retrieval fail-closed rules (§7).
ASSIGNMENT_KINDS: Final[frozenset[Kind]] = frozenset({
    Kind.ASSIGNMENT_BRIEF, Kind.ASSIGNMENT_RESOURCE,
})
# Kinds that can mix concept text with assignment instructions and therefore
# need COMPLETE chunk tags before default search exposure (§7, r5 R9, r6 R4).
MIXABLE_KINDS: Final[frozenset[Kind]] = frozenset({
    Kind.TRANSCRIPT, Kind.LAB_MATERIAL, Kind.LECTURE_SLIDES, Kind.SUPPLEMENT, Kind.COURSE_INFO,
    Kind.SETUP_GUIDE, Kind.EXAM, Kind.ANNOUNCEMENT,
})

# Exact plan §3.1 P4 list.
CODE_EXTENSIONS: Final[frozenset[str]] = frozenset({"c", "cpp", "h", "py", "java", "js", "sql", "ipynb"})
TABULAR_EXTENSIONS: Final[frozenset[str]] = frozenset({"csv", "json", "xlsx"})
FORBIDDEN_EXTENSIONS: Final[frozenset[str]] = frozenset({"exe", "dmg", "pkg", "msi", "iso"})
TEXT_MIME_TYPES: Final[frozenset[str]] = frozenset({"text/markdown", "text/plain"})
PDF_MIME: Final[str] = "application/pdf"
PDF_MAGIC: Final[bytes] = b"%PDF"
# Opaque registration (§6.1): the MIME must agree with the extension and the bytes.
# Per-extension compatible MIME types; ``text/plain`` is accepted for every text
# format, any other MIME must belong to that language (P-A r3 #6).
CODE_MIME_TYPES_BY_EXTENSION: Final[dict[str, frozenset[str]]] = {
    "c": frozenset({"text/plain", "text/x-c", "text/x-csrc"}),
    "h": frozenset({"text/plain", "text/x-c", "text/x-chdr"}),
    "cpp": frozenset({"text/plain", "text/x-c", "text/x-c++src"}),
    "py": frozenset({"text/plain", "text/x-python", "text/x-script.python"}),
    "java": frozenset({"text/plain", "text/x-java", "text/x-java-source"}),
    "js": frozenset({"text/plain", "text/javascript", "application/javascript", "application/x-javascript"}),
    "sql": frozenset({"text/plain", "application/sql", "application/x-sql", "text/x-sql"}),
    "ipynb": frozenset({"application/json", "application/x-ipynb+json"}),
}
CODE_MIME_TYPES: Final[frozenset[str]] = frozenset().union(*CODE_MIME_TYPES_BY_EXTENSION.values())
TABULAR_MIME_TYPES: Final[dict[str, frozenset[str]]] = {
    "csv": frozenset({"text/csv", "text/plain", "application/csv"}),
    "json": frozenset({"application/json", "text/json", "text/plain"}),
    "xlsx": frozenset({
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
}
ZIP_MAGIC: Final[bytes] = b"PK\x03\x04"


def kind_from_value(value: object) -> Kind | None:
    if isinstance(value, Kind):
        return value
    if isinstance(value, str):
        try:
            return Kind(value.strip().upper())
        except ValueError:
            return None
    return None


def material_type_initial(kind: Kind) -> str | None:
    """Creation-time Materials.Type for an automatic Material (§5 table)."""

    return MATERIAL_TYPE_INITIAL.get(kind)


# Signatures of well-known non-text containers that still decode as UTF-8 (r12 R2).
_BINARY_SIGNATURES: Final[tuple[bytes, ...]] = (
    PDF_MAGIC, b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"\x7fELF", b"\x1f\x8b", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"%!PS", b"\xfe\xed\xfa",
    b"\xca\xfe\xba\xbe", b"Rar!", b"7z\xbc\xaf", b"BZh", b"\xfd7zXZ", b"SQLite format 3",
)
# C0 control bytes other than TAB/LF/CR/FF never appear in source or tabular text.
_CONTROL_BYTES: Final[frozenset[int]] = frozenset(
    set(range(0x09)) | {0x0B} | set(range(0x0E, 0x20)) | {0x7F}
)


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True
    if any(head.startswith(signature) for signature in _BINARY_SIGNATURES):
        return False
    if any(byte in _CONTROL_BYTES for byte in head):
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


_XLSX_REQUIRED_PARTS: Final[frozenset[str]] = frozenset({"[Content_Types].xml", "xl/workbook.xml"})


_INVALID: Final[object] = object()


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-standard JSON constant {name}")


def _parsed_json(payload: bytes) -> object:
    """The decoded strict JSON document (no NaN/Infinity), or ``_INVALID``."""

    import json

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _INVALID


def _notebook_ok(payload: bytes) -> bool:
    """An ipynb is a JSON object with a ``cells`` list and an integer ``nbformat``."""

    document = _parsed_json(payload)
    return (
        isinstance(document, dict)
        and isinstance(document.get("cells"), list)
        and type(document.get("nbformat")) is int
    )


# Decompression bounds for the XLSX container check (P-A r5 #5).
XLSX_MAX_ENTRY_BYTES: Final[int] = 50 * 1024 * 1024
XLSX_MAX_TOTAL_BYTES: Final[int] = 200 * 1024 * 1024
_XLSX_PART_LIMIT: Final[int] = 8 * 1024 * 1024
_XLSX_ROOT_TAGS: Final[dict[str, str]] = {"[Content_Types].xml": "Types", "xl/workbook.xml": "workbook"}


def _xml_root_local_name(data: bytes) -> str | None:
    """Local name of the root element, or None when the bytes are not well-formed XML.

    Only a bounded, entity-free document is accepted: a DOCTYPE (entity expansion)
    is refused outright, and the whole document must be well-formed.
    """

    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return None
    import xml.etree.ElementTree as ET

    root: str | None = None
    try:
        # Parse to the end so a truncated tail or trailing garbage is a ParseError
        # (r6 R2); only the root name is kept.
        for _event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
            if root is None and isinstance(element.tag, str):
                root = element.tag.rsplit("}", 1)[-1]
            element.clear()
    except ET.ParseError:
        return None
    return root


def _xlsx_container_ok(payload: bytes) -> bool:
    """A readable ZIP that holds the mandatory OOXML workbook parts."""

    if not payload.startswith(ZIP_MAGIC):
        return False
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                return False  # duplicate members can shadow a mandatory part (r6 R2)
            if not _XLSX_REQUIRED_PARTS <= set(names):
                return False
            # Declared sizes are bounded before anything is inflated (r5 #5).
            if any(info.file_size > XLSX_MAX_ENTRY_BYTES for info in infos):
                return False
            if sum(info.file_size for info in infos) > XLSX_MAX_TOTAL_BYTES:
                return False
            # The mandatory OOXML parts must be real XML with the expected root
            # element; the whole archive must be CRC-clean (r3 #6, r5 #5).
            for part, root in _XLSX_ROOT_TAGS.items():
                if archive.getinfo(part).file_size > _XLSX_PART_LIMIT:
                    return False
                if _xml_root_local_name(archive.read(part)) != root:
                    return False
            return archive.testzip() is None
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return False


def handling_mode(
    kind: Kind | None,
    *,
    mime_type: str | None,
    extension: str | None,
    head: bytes | None,
    payload_complete: bool = False,
) -> HandlingMode:
    """Decide the authoritative processing mode from (Kind, MIME, extension, signature).

    The caller passes the first bytes of the actual download as ``head`` (or
    ``None`` when the bytes are not available, which never yields NORMALIZE
    or an opaque registration).  Formats whose eligibility depends on the whole
    file (JSON, ipynb, XLSX) are only registered when ``payload_complete`` says
    ``head`` is the entire bounded payload; otherwise they are ``S3`` (r5 #4/#5).
    Anything outside the matrix is ``S3`` so a human decides; no Kind is ever
    reinterpreted here (§6.1, r4 M6, r10 R4).
    """

    ext = (extension or "").lower().lstrip(".")
    mime = (mime_type or "").lower()
    if kind is None or kind is Kind.UNSUPPORTED:
        return HandlingMode.S3 if kind is None else HandlingMode.METADATA_ONLY
    if kind in {Kind.RECORDING, Kind.ANNOUNCEMENT}:
        return HandlingMode.METADATA_ONLY
    if head is None:
        return HandlingMode.S3
    if kind is Kind.TRANSCRIPT:
        if mime in TEXT_MIME_TYPES and ext in {"md", "txt"} and _looks_like_text(head):
            return HandlingMode.NORMALIZE
        return HandlingMode.S3
    if kind in PDF_MATERIAL_KINDS:
        if mime == PDF_MIME and ext == "pdf" and head.startswith(PDF_MAGIC):
            return HandlingMode.NORMALIZE
        return HandlingMode.S3
    if kind is Kind.PROVIDED_CODE:
        # Only the declared MIME set qualifies; no prefix matching (P-A r2 #3).
        if ext in CODE_EXTENSIONS and mime in CODE_MIME_TYPES_BY_EXTENSION[ext] and _looks_like_text(head):
            if ext == "ipynb" and not (payload_complete and _notebook_ok(head)):
                return HandlingMode.S3
            return HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
        return HandlingMode.S3
    if kind is Kind.ASSIGNMENT_RESOURCE:
        if ext in TABULAR_EXTENSIONS:
            if mime not in TABULAR_MIME_TYPES[ext]:
                return HandlingMode.S3
            if ext == "xlsx":
                # XLSX needs its real OOXML container, which only the full payload can
                # prove (P-A r2 #3, r5 #5).
                if not (payload_complete and _xlsx_container_ok(head)):
                    return HandlingMode.S3
            elif not _looks_like_text(head):
                return HandlingMode.S3
            elif ext == "json" and not (payload_complete and _parsed_json(head) is not _INVALID):
                # The declared format must hold for the whole bounded payload (r4 #5, r5 #4).
                return HandlingMode.S3
            return HandlingMode.REGISTER_OPAQUE_NO_RETRIEVAL
        if mime == PDF_MIME and ext == "pdf" and head.startswith(PDF_MAGIC):
            return HandlingMode.NORMALIZE
        return HandlingMode.S3
    return HandlingMode.S3


__all__ = [
    "AI_KIND_OPTIONS",
    "ASSIGNMENT_KINDS",
    "CODE_EXTENSIONS",
    "CODE_MIME_TYPES",
    "CODE_MIME_TYPES_BY_EXTENSION",
    "FILE_KINDS_V2",
    "FORBIDDEN_EXTENSIONS",
    "MATERIAL_ROLES_V2",
    "MATERIAL_TYPE_INITIAL",
    "MIXABLE_KINDS",
    "OPAQUE_KINDS",
    "ORIGIN_OPTIONS",
    "PDF_MATERIAL_KINDS",
    "QUESTION_SET_VERSION",
    "RULE_TABLE_VERSION",
    "S2_CHOICE_KINDS",
    "S4_PAYLOAD_POLICY_VERSION",
    "TABULAR_EXTENSIONS",
    "TABULAR_MIME_TYPES",
    "TAG_RULE_VERSION",
    "TAG_VOCAB_VERSION",
    "XLSX_MAX_ENTRY_BYTES",
    "XLSX_MAX_TOTAL_BYTES",
    "ChunkTag",
    "DecisionType",
    "HandlingMode",
    "Kind",
    "Origin",
    "handling_mode",
    "kind_from_value",
    "material_type_initial",
]
