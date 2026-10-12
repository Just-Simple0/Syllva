"""Section tags (S4) and the assignment gate of the retrieval filter (plan §2.2, §7; P-D).

Pure and deterministic: decisions are computed from the current derivative chunks at read
time, never stored, so a changed source or rule version can never reuse an old decision.
Only the required ``assignment`` question gates retrieval; every other tag is an informational
label and never an authority (``exam_hint`` does not confirm an exam scope, ``assignment`` does
not verify a Material Usage)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from uls.intake.classification.taxonomy import (
    QUESTION_SET_VERSION,
    TAG_RULE_VERSION,
    TAG_VOCAB_VERSION,
)

YES: Final[str] = "YES"
NO: Final[str] = "NO"
UNRESOLVED: Final[str] = "UNRESOLVED"
STALE: Final[str] = "STALE"
NORMAL: Final[str] = "NORMAL"
NO_ANSWER_GENERATION: Final[str] = "NO_ANSWER_GENERATION"
ASSIGNMENT_KINDS: Final[frozenset[str]] = frozenset({"ASSIGNMENT_BRIEF", "ASSIGNMENT_RESOURCE"})
ALWAYS_UNRESOLVED_KINDS: Final[frozenset[str]] = frozenset({"LAB_MATERIAL"})
OFFICIAL_ACTIVITY_CLASS: Final[str] = "official_activity"
SIGNAL_WITHHELD_CODE: Final[str] = "SIGNAL_ASSIGNMENT_WITHHELD"

# Strong assignment vocabulary: Latin words match on word boundaries, Hangul by substring.
_STRONG_LATIN = (
    r"assignments?", r"homework", r"hw\s?\d+", r"deliverables?", r"due(?!\s+to\b)", r"deadlines?", r"submit(?:ted|ting|s)?",
    r"submission", r"upload(?:ed|ing|s)?", r"hand[\s\-]?in", r"hand[\s\-]?out\s+(?:the\s+)?report",
    r"turn[\s\-]?in", r"problem\s+set", r"lab\s+report", r"term\s+project", r"grading\s+criteria",
)
_STRONG_HANGUL = ("과제", "숙제", "제출", "마감", "기한", "업로드", "레포트", "리포트", "보고서", "배점", "채점 기준", "평가 기준")
_LATIN = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_STRONG_LATIN) + r")(?![A-Za-z0-9])", re.IGNORECASE)
_HANGUL = re.compile("|".join(re.escape(word) for word in _STRONG_HANGUL))

# Informational labels only (never used to filter or to claim authority).
_LABELS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("example_code", re.compile(r"```|^\s{4,}\S", re.MULTILINE)),
    ("exercise", re.compile(r"(?i)\b(?:exercise|practice|implement)\b|실습|연습|구현")),
    ("exam_hint", re.compile(r"(?i)\b(?:exam|midterm|final)\b|시험|중간고사|기말고사")),
    ("setup_step", re.compile(r"(?i)\b(?:install|setup|configure)\b|설치|환경 설정")),
    ("schedule", re.compile(r"(?i)\b(?:schedule|week\s*\d+)\b|일정|주차")),
    ("grading", re.compile(r"(?i)\b(?:grading|grade|points?)\b|성적|채점|배점")),
    ("submission_guide", re.compile(r"(?i)\bsubmission\b|제출 방법")),
    ("reference", re.compile(r"(?i)\b(?:reference|bibliography)\b|참고 문헌|참고문헌")),
    ("resource_link", re.compile(r"(?i)https?://|www\.")),
)


def _canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Co", "Cn"))


def has_strong_assignment_signal(text: str) -> bool:
    canonical = _canonical(text)
    return bool(_LATIN.search(canonical) or _HANGUL.search(canonical))


def chunk_id_for(doc_id: str, locator: Any, content: str) -> str:
    material = f"{doc_id}|{locator}|{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChunkDecision:
    chunk_id: str
    locator: Any
    assignment: str            # YES | NO | UNRESOLVED
    labels: tuple[str, ...]    # informational only


@dataclass(frozen=True)
class DocumentTags:
    doc_id: str
    doc_kind: str | None
    decisions: tuple[ChunkDecision, ...]
    versions: tuple[str, str, str]

    @property
    def complete(self) -> bool:
        return bool(self.decisions) and all(item.assignment in (YES, NO) for item in self.decisions)

    @property
    def status(self) -> str:
        return "COMPLETE" if self.complete else "PARTIAL"


def assess_document(doc_id: str, chunks: Iterable[Any], doc_kind: str | None) -> DocumentTags:
    """Assignment decisions for every non-empty chunk of the current derivative."""

    decisions: list[ChunkDecision] = []
    for chunk in chunks:
        content = str(getattr(chunk, "content", ""))
        locator = getattr(chunk, "locator", None)
        if not content.strip():
            decisions.append(ChunkDecision(chunk_id_for(doc_id, locator, content), locator, NO, ()))
            continue
        if doc_kind in ALWAYS_UNRESOLVED_KINDS:
            verdict = UNRESOLVED       # an instruction-heavy document cannot be certified by rules
        elif has_strong_assignment_signal(content):
            verdict = YES
        else:
            verdict = NO
        labels = tuple(name for name, pattern in _LABELS if pattern.search(content))
        decisions.append(ChunkDecision(chunk_id_for(doc_id, locator, content), locator, verdict, labels))
    return DocumentTags(doc_id, doc_kind, tuple(decisions),
                        (TAG_VOCAB_VERSION, QUESTION_SET_VERSION, TAG_RULE_VERSION))


def document_blocked(doc_kind: str | None, material_type: str | None, *, is_material: bool) -> bool:
    """Whole-document exclusion from the assignment-excluding paths (D4)."""

    if doc_kind in ASSIGNMENT_KINDS:
        return True
    return bool(is_material and not doc_kind and (material_type or "").strip().casefold() == "professor notes")


def locators_overlap(a: Any, b: Any) -> bool:
    """Same entity and intersecting numeric ranges (the subtype is ignored: more overlap is safer)."""

    try:
        if getattr(a, "entity_id", None) != getattr(b, "entity_id", None):
            return False
        if hasattr(a, "start_page") and hasattr(b, "start_page"):
            return bool(a.start_page <= b.end_page and b.start_page <= a.end_page)
        if hasattr(a, "start_seconds") and hasattr(b, "start_seconds"):
            return bool(a.start_seconds <= b.end_seconds and b.start_seconds <= a.end_seconds)
    except Exception:  # noqa: BLE001 - an unreadable locator overlaps everything (fail closed)
        return True
    return False


def overlapping(decisions: Sequence[ChunkDecision], locator: Any) -> list[ChunkDecision]:
    """Decisions of every chunk whose locator overlaps ``locator``."""

    return [item for item in decisions if locators_overlap(item.locator, locator)]


def aggregate_decision(items: Sequence[ChunkDecision]) -> str:
    """Safety-first aggregate over the overlapping chunks: STALE > UNRESOLVED > YES > NO."""

    if not items:
        return UNRESOLVED          # an empty overlap set cannot be certified
    values = {item.assignment for item in items}
    for verdict in (STALE, UNRESOLVED, YES):
        if verdict in values:
            return verdict
    return NO


def usage_policy_for(
    decision: str, *, doc_kind: str | None = None, source_class: str | None = None,
) -> str:
    if source_class == OFFICIAL_ACTIVITY_CLASS or doc_kind in ASSIGNMENT_KINDS:
        return NO_ANSWER_GENERATION
    return NORMAL if decision == NO else NO_ANSWER_GENERATION


def signal_locators(value: Any) -> list[str]:
    """Every string stored under a key that names a locator, anywhere inside a signal."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "locator" in str(key).casefold():
                found.extend(text for text in (item if isinstance(item, (list, tuple)) else [item]) if isinstance(text, str))
            else:
                found.extend(signal_locators(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(signal_locators(item))
    return found


def signal_has_assignment_text(value: Any) -> bool:
    """True when any string inside an enrichment signal carries a strong assignment word (D10)."""

    if isinstance(value, str):
        return has_strong_assignment_signal(value)
    if isinstance(value, dict):
        return any(signal_has_assignment_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(signal_has_assignment_text(item) for item in value)
    return False
