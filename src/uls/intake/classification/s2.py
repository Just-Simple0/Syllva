"""S2 — Jev Choice classification of S1-undecided items (plan §3.2, §4; P-C2).

Pure helpers: option set, excerpt extraction, outbound minimization with a final re-check,
the confidence decision and the cache key.  Nothing here performs I/O or holds a secret."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from uls.adapters.llm.typesafe_choice import MODEL, ChoiceQuestion

from .taxonomy import S2_CHOICE_KINDS, Kind

PROMPT_VERSION: Final[str] = "icv2-s2-prompt-1"
OPTION_SET_VERSION: Final[str] = "icv2-s2-options-1"
QUESTION_NAME: Final[str] = "kind"
NO_MATCH: Final[str] = "no_match"
OPTIONS: Final[tuple[str, ...]] = (*(kind.value for kind in S2_CHOICE_KINDS), NO_MATCH)
MIN_CONFIDENCE: Final[float] = 0.80
MIN_TOP_PROBABILITY: Final[float] = 0.70
EXCERPT_LIMITS: Final[tuple[int, ...]] = (6144, 3072, 1536, 768, 0)
CODE_LINES: Final[int] = 120
PDF_PAGES: Final[int] = 2
CODE_EXTENSIONS: Final[frozenset[str]] = frozenset({"c", "cpp", "h", "py", "java", "js", "sql"})
TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset({"md", "txt", "csv", "json"})

NOTE_ORIGIN_INELIGIBLE: Final[str] = "S2_ORIGIN_INELIGIBLE"
NOTE_PII_UNCERTAIN: Final[str] = "PII_UNCERTAIN"
NOTE_LOW_CONFIDENCE: Final[str] = "LOW_CONFIDENCE"
NOTE_NO_MATCH: Final[str] = "CLASSIFIER_NO_MATCH"
NOTE_REQUEST_TOO_LARGE: Final[str] = "S2_REQUEST_TOO_LARGE"

INSTRUCTIONS: Final[str] = (
    "Classify this university course file by its main purpose. LECTURE_SLIDES: lecture slides or notes; "
    "LAB_MATERIAL: lab or practice material; PROVIDED_CODE: source code given by the professor; "
    "ASSIGNMENT_BRIEF: assignment description; ASSIGNMENT_RESOURCE: data or files supplied for an assignment; "
    "SETUP_GUIDE: environment installation or setup guide; COURSE_INFO: syllabus or course introduction; "
    "SUPPLEMENT: appendix or supplementary reading; EXAM: past exam or exam material; "
    "TRANSCRIPT: lecture transcript. Use no_match if none fits."
)
QUESTION: Final[ChoiceQuestion] = ChoiceQuestion(QUESTION_NAME, INSTRUCTIONS, OPTIONS)

# --- outbound minimization (plan §3.2) -------------------------------------------------------
# Every string is first canonicalized (NFKC, invisible characters removed, angle brackets removed so
# source text can never forge one of our placeholders), then identifiers are replaced, and the
# result is checked again by independent rules; anything still suspicious blocks the send.
_URL = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.\-]*://|//(?=[\w\-]+\.)|\bwww\.)\S+"
    r"|(?<![\w.\-])(?:[^\W_]+(?:-[^\W_]+)*\.)+[^\W\d_]{2,24}(?=[/?#:])\S*"
    r"|(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?=[/?#:]|$)\S*"
    r"|(?<![\w.\-])localhost(?=[/?#:]|$)\S*"
    r"|\S*\?\S*=\S*|\S+:\d{2,5}/\S*")
_LABEL = r"(?:학번|이름|성명|성\s*명|Student\s*ID|Name)"
# A label's whole value (to the end of the line) goes: "Name: John Smith" must not leave "Smith".
_LABELLED = re.compile(
    r"((?:학번|이름|성명|성\s*명)|(?:Student\s*ID|Name))((?<=[가-힣])[ \t]+|\s*[:：=\-–—]+\s*)[^\r\n]*",
    re.IGNORECASE)
_STUDENT_ID_SPACE = re.compile(r"(?i)\b(Student\s*ID)([ \t]+)(?=\S*\d)\S+")
# Explicit credential assignments / headers and well-known token shapes.
_SECRET_NAME = r"(?:[\w\-]*(?:api[_\-\s]?key|token|secret|passw(?:or)?d|pwd|credential|private[_\-\s]?key)[\w\-]*|authorization)"
_SECRET_ASSIGN = re.compile(rf"(?i)\b({_SECRET_NAME})(\s*[:=]\s*)[^\r\n]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)(\s+)[^\s]+")
_TOKEN_SHAPE = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[abprs]|AKIA|ASIA|AIza|ya29|eyJ)[\w\-.]{8,}"
    r"|\b(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{32,}")
# A group of digits with ANY run of separators between them (spaces, dashes of every kind, underscores).
_DIGIT_RUN = re.compile(r"\d(?:[^\w]|_|\d)*")
_ID_CONTEXT = re.compile(r"(?i)(?:학번|번호|id|student|no)[\W_]*$")
MAX_RUN_DIGITS: Final[int] = 7
PLACEHOLDERS: Final[tuple[str, ...]] = ("<url>", "<email>", "<id>", "<redacted>")
_LABEL_VALUE_LEFT = re.compile(
    rf"(?i){_LABEL}(?:(?<=[가-힣])[ \t]+|\s*[:：=\-–—]+\s*)(?!<redacted>$)\S", re.MULTILINE)
_STUDENT_ID_LEFT = re.compile(r"(?i)\bStudent\s*ID[ \t]+(?=\S*\d)(?!<redacted>)\S")
_SECRET_LEFT = re.compile(rf"(?i)\b{_SECRET_NAME}\s*[:=]\s*(?!<redacted>$)\S|\b(?:bearer|basic)\s+(?!<redacted>)\S", re.MULTILINE)
_TOKEN_EDGE = "()[]{}'\",;"


class PiiUncertain(Exception):
    """The final payload still looks like it carries an identifier; nothing is sent."""


def _canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Co", "Cn") and (ch in "\n\t" or ord(ch) >= 32))
    return text.replace("<", " ").replace(">", " ")


def _is_plain_date(run: str) -> bool:
    parts = re.fullmatch(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", run)
    if parts is None:
        return False
    try:
        datetime.date(int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        return False
    return True


def _digit_runs(text: str, *, replace: bool) -> tuple[str, bool]:
    """7+ digits in one group that is not a valid plain date (outside an ID context) are an identifier."""

    found = False

    def swap(match: re.Match[str]) -> str:
        nonlocal found
        raw = match.group(0)
        run = raw.rstrip()
        trimmed = run.rstrip("".join(ch for ch in run if not ch.isdigit()))
        trailer = raw[len(trimmed):]
        if sum(ch.isdigit() for ch in trimmed) < MAX_RUN_DIGITS:
            return raw
        if _is_plain_date(trimmed) and not _ID_CONTEXT.search(text[max(0, match.start() - 16):match.start()]):
            return raw
        found = True
        return "<id>" + trailer

    return _DIGIT_RUN.sub(swap, text), found


def _replace_tokens(text: str) -> str:
    """Emails and URLs are replaced as WHOLE whitespace-delimited tokens, so overlapping shapes
    ("jane@univ.ac.kr/path") cannot leave a fragment."""

    pieces = re.split(r"(\s+)", text)
    for index, piece in enumerate(pieces):
        if not piece or piece.isspace():
            continue
        core = piece.strip(_TOKEN_EDGE)
        if "@" in core:
            pieces[index] = "<email>"
        elif _URL.search(core):
            pieces[index] = "<url>"
    return "".join(pieces)


def minimize(text: str) -> str:
    text = _replace_tokens(_canonical(text))
    text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _TOKEN_SHAPE.sub("<redacted>", text)
    text = _LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _STUDENT_ID_SPACE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    return _digit_runs(text, replace=True)[0]


def still_suspicious(text: str) -> bool:
    """Independent final check of an already minimized string."""

    stripped = text
    for placeholder in PLACEHOLDERS:
        stripped = stripped.replace(placeholder, " ")
    return bool(
        "<" in stripped or ">" in stripped or "@" in stripped
        or _URL.search(stripped) or _LABEL_VALUE_LEFT.search(text) or _SECRET_LEFT.search(text) or _STUDENT_ID_LEFT.search(text)
        or _TOKEN_SHAPE.search(stripped) or _digit_runs(stripped, replace=False)[1]
        or re.search(r"(?i)(?:https?|ftp)://|www\.", stripped))


def _decode(payload: bytes) -> str | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def extract_excerpt(name: str, mime_type: str | None, payload: bytes | None) -> str:
    """The raw (not yet minimized) excerpt: text ≤ 6 KB, code first 120 lines, PDF first 2 pages."""

    if not payload:
        return ""
    extension = name.rsplit(".", 1)[1].lower() if "." in name else ""
    if payload[:4] == b"%PDF" or mime_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(payload))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:PDF_PAGES])
        except Exception:  # noqa: BLE001 - no parser or a damaged PDF means no excerpt
            return ""
    if extension in CODE_EXTENSIONS or extension in TEXT_EXTENSIONS or (mime_type or "").startswith("text/"):
        text = _decode(payload[: 4 * EXCERPT_LIMITS[0]])
        if text is None:
            return ""
        if extension in CODE_EXTENSIONS:
            text = "\n".join(text.splitlines()[:CODE_LINES])
        return text
    return ""


def _cut(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore") if limit else ""


@dataclass(frozen=True)
class S2Payload:
    state: Mapping[str, Any]
    excerpt: str                # the exact (minimized) excerpt that is sent
    snapshot_sha256: str        # of the excerpt bytes
    payload_fingerprint: str    # of the final serialized state


def build_payload(
    *, title: str, extension: str, origin: str, course_key: str | None, module_week: int | None,
    item_type: str | None, excerpt: str, limit: int,
) -> S2Payload:
    sent = minimize(_cut(excerpt, limit))
    state: dict[str, Any] = {
        "title": minimize(title), "extension": minimize(extension), "origin": origin,
        "course_key": None if course_key is None else minimize(course_key),
        "module_week": module_week, "item_type": None if item_type is None else minimize(item_type),
        "excerpt": sent,
    }
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if any(still_suspicious(value) for value in state.values() if isinstance(value, str)):
        raise PiiUncertain
    return S2Payload(
        state, sent, hashlib.sha256(sent.encode("utf-8")).hexdigest(),
        hashlib.sha256(serialized.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class S2Decision:
    kind: Kind | None
    note: str | None
    choice: str
    confidence: float
    probabilities: Mapping[str, float]

    def record(self) -> dict[str, Any]:
        return {"type": "model", "model": MODEL, "prompt_version": PROMPT_VERSION,
                "option_set_version": OPTION_SET_VERSION, "choice": self.choice,
                "probabilities": dict(self.probabilities), "confidence": self.confidence}


def decide(choice: str, confidence: float, probabilities: Mapping[str, float]) -> S2Decision:
    if choice == NO_MATCH:
        return S2Decision(None, NOTE_NO_MATCH, choice, confidence, probabilities)
    try:
        kind = Kind(choice)
    except ValueError:
        return S2Decision(None, NOTE_NO_MATCH, choice, confidence, probabilities)
    if kind not in S2_CHOICE_KINDS:
        return S2Decision(None, NOTE_NO_MATCH, choice, confidence, probabilities)
    if confidence < MIN_CONFIDENCE or probabilities.get(choice, 0.0) < MIN_TOP_PROBABILITY:
        return S2Decision(None, NOTE_LOW_CONFIDENCE, choice, confidence, probabilities)
    return S2Decision(kind, None, choice, confidence, probabilities)


def cache_key(
    *, payload: S2Payload, byte_sha256: str, source_version: int, title: str, course_key: str | None,
    module_week: int | None, item_type: str | None, origin: str,
) -> str:
    material = json.dumps([
        payload.snapshot_sha256, payload.payload_fingerprint, byte_sha256, source_version, title, course_key,
        module_week, item_type, origin, OPTION_SET_VERSION, PROMPT_VERSION, MODEL,
    ], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class S2Result:
    """DECIDED (a Kind), HUMAN (S3 with fixed notes) or DEFERRED (budget exhausted)."""

    status: str
    kind: Kind | None = None
    decision: Mapping[str, Any] | None = None
    notes: tuple[str, ...] = ()
    snapshot_sha256: str | None = None
