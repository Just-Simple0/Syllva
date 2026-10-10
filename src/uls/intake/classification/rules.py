"""Deterministic S1 rules and the assignment axes (plan §2.3, §3.1).

The rule table is evaluated in fixed priority levels.  A level that yields
exactly one Kind decides; two or more candidates at the same level defer to
S2 (or S3 when the origin may not be sent to a model); no candidate at any
level is also deferred.  Decisions carry a rule id and the table version and
never a probability.  Rule ids and the table version are persisted as the
provenance of every automatic decision, so they are part of the contract
checked by the observed-item fixture.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from .taxonomy import (
    CODE_EXTENSIONS,
    FORBIDDEN_EXTENSIONS,
    RULE_TABLE_VERSION,
    TABULAR_EXTENSIONS,
    DecisionType,
    Kind,
    Origin,
)

TRANSCRIPT_RULE_MIME = "text/markdown"

# Canvas resource kinds as observed by the read-only probe (§6.3).
CANVAS_ASSIGNMENT = "assignment"
CANVAS_ANNOUNCEMENT = "announcement"
CANVAS_MODULE_ITEM = "module_item"
CANVAS_EXTERNAL_TOOL = "ExternalTool"

TRANSCRIPT_FILENAME = re.compile(
    r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})_(?P<course>.+?)_(?P<week>\d+)주차(?:\.md)+$"
)
_DATE_ONLY_TITLE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_WEEK_MODULE = re.compile(r"^(\d+)주차$")
_WEEK_IN_NAME = re.compile(r"_(\d+)주차")
_ISO_DATE = re.compile(r"(\d{4})([-.])(\d{2})\2(\d{2})")  # one separator, YYYY.MM.DD or YYYY-MM-DD (r14 R2)

# Exactly the plan §3.1 P0 title signal; English "installer" guides stay P3 SETUP_GUIDE (r15 R2).
_P0_INSTALLER_TITLE = re.compile(r"설치\s*프로그램")
_P0_INSTALL_WORD = re.compile(r"설치|install|setup", re.IGNORECASE)
_P2_COURSE_INFO = re.compile(r"과정소개|강의소개|강의계획|syllabus", re.IGNORECASE)
_P2_EXAM = re.compile(r"기출|중간고사|기말고사")
_P3_LECTURE = re.compile(r"Lec\.\d+|week\d+_chap\d+|Chapter\s*\d+", re.IGNORECASE)
_P3_LAB = re.compile(r"week\d+_lab|실습", re.IGNORECASE)
_P3_SETUP = re.compile(r"설치|install|setup", re.IGNORECASE)
_P3_SUPPLEMENT = re.compile(r"Appendix|부록", re.IGNORECASE)
_P3_CODE = re.compile(r"소스\s*(파일|코드)")
_P4_ASSIGNMENT_RESOURCE_TITLE = re.compile(r"과제|assignment|dataset|데이터\s*셋|data\s*set", re.IGNORECASE)


@dataclass(frozen=True)
class RuleDecision:
    """Outcome of the S1 table for one item."""

    kind: Kind | None
    rule_id: str
    priority: str
    candidates: tuple[Kind, ...] = ()
    rule_table_version: str = RULE_TABLE_VERSION
    decision_type: DecisionType = DecisionType.RULE

    @property
    def decided(self) -> bool:
        return self.kind is not None

    @property
    def terminal_unsupported(self) -> bool:
        return self.kind is Kind.UNSUPPORTED


@dataclass(frozen=True)
class ItemSignals:
    """Source-independent inputs to the rule table (S0 output)."""

    title: str
    extension: str | None = None
    mime_type: str | None = None
    origin: Origin = Origin.UNKNOWN
    canvas_resource_kind: str | None = None
    canvas_item_type: str | None = None
    canvas_attachment_of: str | None = None  # resource kind the attachment hangs off
    from_upload_folder: bool = False


def _extension(title: str, explicit: str | None) -> str:
    if explicit:
        return explicit.lower().lstrip(".")
    name = title.strip().lower()
    return name.rsplit(".", 1)[1] if "." in name and not name.endswith(".") else ""


def _valid_date_title(title: str) -> bool:
    match = _DATE_ONLY_TITLE.match(title)
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def classify_by_rules(signals: ItemSignals) -> RuleDecision:
    """Apply the P0–P4 table (§3.1) and return a decision or a deferral."""

    title = signals.title.strip()
    ext = _extension(title, signals.extension)
    mime = (signals.mime_type or "").lower()
    is_assignment_attachment = signals.canvas_attachment_of == CANVAS_ASSIGNMENT

    # P0 — forbidden binaries: terminal, no download, no model, no Material.
    if ext in FORBIDDEN_EXTENSIONS:
        return RuleDecision(Kind.UNSUPPORTED, "P0:forbidden_extension", "P0")
    if _P0_INSTALLER_TITLE.search(title):
        return RuleDecision(Kind.UNSUPPORTED, "P0:installer_binary_title", "P0")
    if ext == "zip" and _P0_INSTALL_WORD.search(title):
        return RuleDecision(Kind.UNSUPPORTED, "P0:installer_archive", "P0")

    # P1 — trusted source type (never for attachments, which are Drive files).
    if not signals.canvas_attachment_of:
        if signals.canvas_resource_kind == CANVAS_ASSIGNMENT:
            return RuleDecision(Kind.ASSIGNMENT_BRIEF, "P1:canvas_assignment", "P1")
        if signals.canvas_resource_kind == CANVAS_ANNOUNCEMENT:
            return RuleDecision(Kind.ANNOUNCEMENT, "P1:canvas_announcement", "P1")
        if signals.canvas_resource_kind == CANVAS_MODULE_ITEM:
            if (signals.canvas_item_type or "") == "Assignment":
                return RuleDecision(Kind.ASSIGNMENT_BRIEF, "P1:canvas_assignment", "P1")
            if (signals.canvas_item_type or "") == CANVAS_EXTERNAL_TOOL and _valid_date_title(title):
                return RuleDecision(Kind.RECORDING, "P1:date_only_title", "P1")

    # P2 — explicit identifiers.
    p2: list[tuple[Kind, str]] = []
    if _P2_COURSE_INFO.search(title):
        p2.append((Kind.COURSE_INFO, "P2:course_info"))
    if _P2_EXAM.search(title):
        p2.append((Kind.EXAM, "P2:exam_file"))
    if len(p2) == 1:
        return RuleDecision(p2[0][0], p2[0][1], "P2")
    if len(p2) > 1:
        return RuleDecision(None, "P2:multiple", "P2", tuple(k for k, _ in p2))

    # P3 — title rules.
    p3: list[tuple[Kind, str]] = []
    # The transcript filename rule needs exactly the text/markdown MIME of plan §3.1
    # P3; text/plain stays processable in handling_mode (§6.1) but never decides in S1,
    # and an unknown MIME leaves the item to S2/S3 (P-A r2 #1, r14 R3).
    if signals.from_upload_folder and TRANSCRIPT_FILENAME.match(title) and mime == TRANSCRIPT_RULE_MIME:
        p3.append((Kind.TRANSCRIPT, "P3:transcript_filename"))
    if _P3_LECTURE.search(title):
        p3.append((Kind.LECTURE_SLIDES, "P3:lecture"))
    # A verified Canvas Assignment attachment titled "실습…" is ambiguous between
    # ASSIGNMENT_BRIEF and LAB_MATERIAL (r13 O2): the generic lab rule is skipped.
    if _P3_LAB.search(title) and not is_assignment_attachment:
        p3.append((Kind.LAB_MATERIAL, "P3:lab"))
    if _P3_SETUP.search(title):
        p3.append((Kind.SETUP_GUIDE, "P3:setup"))
    if _P3_SUPPLEMENT.search(title):
        p3.append((Kind.SUPPLEMENT, "P3:appendix"))
    if _P3_CODE.search(title):
        p3.append((Kind.PROVIDED_CODE, "P3:code"))
    if len(p3) == 1:
        return RuleDecision(p3[0][0], p3[0][1], "P3")
    if len(p3) > 1:
        return RuleDecision(None, "P3:multiple", "P3", tuple(k for k, _ in p3))

    # P4 — extension rules (exact plan list; tabular only with a provenance or title signal).
    if ext in CODE_EXTENSIONS:
        return RuleDecision(Kind.PROVIDED_CODE, "P4:code_extension", "P4")
    if ext in TABULAR_EXTENSIONS:
        if is_assignment_attachment:
            return RuleDecision(
                Kind.ASSIGNMENT_RESOURCE, "P4:tabular_assignment_attachment", "P4"
            )
        if _P4_ASSIGNMENT_RESOURCE_TITLE.search(title):
            return RuleDecision(Kind.ASSIGNMENT_RESOURCE, "P4:tabular_title_signal", "P4")
    return RuleDecision(None, "S2:no_single_rule", "none")


# ---------------------------------------------------------------------------
# Assignment axes (§2.3): course, week, date.


@dataclass(frozen=True)
class CourseAliasIndex:
    """Alias → course_key map with duplicate aliases disabled (§2.3)."""

    resolved: Mapping[str, str]
    disabled: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(cls, entries: Iterable[tuple[str, Iterable[str]]]) -> CourseAliasIndex:
        """``entries`` yields (course_key, aliases).  Duplicates across courses are disabled."""

        owners: dict[str, set[str]] = {}
        for course_key, aliases in entries:
            for alias in aliases:
                key = _normalize_alias(alias)
                if key:
                    owners.setdefault(key, set()).add(course_key)
        resolved = {alias: next(iter(keys)) for alias, keys in owners.items() if len(keys) == 1}
        disabled = frozenset(alias for alias, keys in owners.items() if len(keys) > 1)
        return cls(resolved, disabled)

    def resolve(self, token: str) -> str | None:
        key = _normalize_alias(token)
        if not key or key in self.disabled:
            return None
        return self.resolved.get(key)


def _normalize_alias(value: str) -> str:
    return "".join(value.split()).lower()


def course_aliases_from_config(
    course_key: str, name: str, code: str, extra: Iterable[str] = ()
) -> list[str]:
    """Deterministic aliases for one configured course: name sans section, code, extras."""

    aliases = [code] if code else []
    bare = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    if bare:
        aliases.append(bare)
    aliases.extend(a for a in extra if isinstance(a, str) and a.strip())
    return aliases


def week_from_module_name(name: str | None) -> int | None:
    if not name:
        return None
    match = _WEEK_MODULE.match(name.strip())
    return int(match.group(1)) if match else None


def week_from_filename(name: str | None) -> int | None:
    """The one explicit ``_N주차`` week in a file name; conflicting weeks are None (r7 R4)."""

    if not name:
        return None
    weeks = {int(m) for m in _WEEK_IN_NAME.findall(name)}
    return weeks.pop() if len(weeks) == 1 else None


def date_from_text(text: str | None) -> date | None:
    """The one valid ``YYYY.MM.DD``/``YYYY-MM-DD`` in the text; absent, invalid or
    conflicting dates are None so an ambiguous name never fixes a date (r7 R4)."""

    if not text:
        return None
    found: set[date] = set()
    for year, _sep, month, day in _ISO_DATE.findall(text):
        try:
            found.add(date(int(year), int(month), int(day)))
        except ValueError:
            return None
    return found.pop() if len(found) == 1 else None


def transcript_signals(filename: str, index: CourseAliasIndex) -> dict[str, object]:
    """Course/week/date from an upload-folder transcript name (empty when no match)."""

    match = TRANSCRIPT_FILENAME.match(filename.strip())
    if not match:
        return {}
    course_key = index.resolve(match.group("course"))
    parsed = date_from_text(
        f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
    )
    return {
        "course_key": course_key,
        "week": int(match.group("week")),
        "date": parsed,
        "course_token": match.group("course"),
    }


__all__ = [
    "CANVAS_ANNOUNCEMENT",
    "CANVAS_ASSIGNMENT",
    "CANVAS_EXTERNAL_TOOL",
    "CANVAS_MODULE_ITEM",
    "TRANSCRIPT_FILENAME",
    "CourseAliasIndex",
    "ItemSignals",
    "RuleDecision",
    "classify_by_rules",
    "course_aliases_from_config",
    "date_from_text",
    "transcript_signals",
    "week_from_filename",
    "week_from_module_name",
]
