"""S0 → S1 → S3 classification outcome for one Drive intake item (plan §3, P-B1).

This module is pure: it takes the observed signals, the deterministic rule
table, the course alias index, the effective semester range and the active
recording-calendar projection, and returns an immutable outcome.  It never
talks to a provider.  S2 (Jev) is a later slice: while it is unavailable the
undecided items fall through to S3 with the fixed ``CLASSIFIER_DISABLED``
note, exactly as plan §4 prescribes for an unspecified credential.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Final

from .calendar import CalendarMatch, CourseCalendar, MatchStatus, SemesterRange, match_transcript
from .rules import (
    TRANSCRIPT_FILENAME,
    CourseAliasIndex,
    ItemSignals,
    RuleDecision,
    classify_by_rules,
    transcript_signals,
    week_from_filename,
)
from .taxonomy import (
    MATERIAL_TYPE_INITIAL,
    RULE_TABLE_VERSION,
    DecisionType,
    HandlingMode,
    Kind,
    Origin,
    handling_mode,
)

# Fixed suggestion-note codes (plan §3.3 "고정 사유 코드"); never free text.
NOTE_CLASSIFIER_DISABLED: Final[str] = "CLASSIFIER_DISABLED"
NOTE_NO_SINGLE_RULE: Final[str] = "NO_SINGLE_RULE"
NOTE_COURSE_UNRESOLVED: Final[str] = "COURSE_UNRESOLVED"
NOTE_CALENDAR_MISMATCH: Final[str] = "CALENDAR_MISMATCH"
NOTE_NO_CALENDAR: Final[str] = "NO_CALENDAR"
NOTE_CALENDAR_AMBIGUOUS: Final[str] = "CALENDAR_AMBIGUOUS"
NOTE_FORMAT_KIND_MISMATCH: Final[str] = "FORMAT_KIND_MISMATCH"
NOTE_SOURCE_TOO_LARGE: Final[str] = "SOURCE_TOO_LARGE"
NOTE_SOURCE_UNAVAILABLE: Final[str] = "SOURCE_UNAVAILABLE"
NOTE_UNSUPPORTED: Final[str] = "UNSUPPORTED_FORMAT"
NOTE_AUTO_UNAVAILABLE: Final[str] = "AUTO_NOT_ENABLED"


@dataclass(frozen=True)
class SourceProbe:
    """What is known about the actual bytes (plan §3.4 R3, §6.1)."""

    payload: bytes | None          # whole bounded payload when available
    complete: bool                 # payload is the entire file
    too_large: bool = False        # size exceeded ``max_source_bytes``
    unavailable: bool = False      # download/identity readback failed

    @property
    def byte_sha256(self) -> str | None:
        return None if self.payload is None or not self.complete else hashlib.sha256(self.payload).hexdigest()

    @property
    def byte_md5(self) -> str | None:
        return None if self.payload is None or not self.complete else hashlib.md5(self.payload, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class ClassificationOutcome:
    """Immutable S0–S3 result; ``automation_eligible`` is always False in P-B1."""

    kind: Kind | None
    origin: Origin
    decision_type: DecisionType | None
    rule_id: str
    rule_table_version: str
    candidates: tuple[Kind, ...]
    course_key: str | None
    course_basis: str | None          # "upload_folder" | "config_alias" | None
    week: int | None
    recorded_date: date | None
    calendar: CalendarMatch | None
    handling: HandlingMode | None
    notes: tuple[str, ...]
    suggestion_source: str | None
    semester: SemesterRange | None

    @property
    def decided(self) -> bool:
        return self.kind is not None

    @property
    def needs_human(self) -> bool:
        """P-B1: every item still needs a HUMAN request (AUTO arrives in P-B2)."""

        return True

    @property
    def material_type_initial(self) -> str | None:
        return None if self.kind is None else MATERIAL_TYPE_INITIAL.get(self.kind)

    def suggestion_fields(self) -> dict[str, object]:
        """The §3.3 suggestion values (local form; the writer wires them)."""

        return {
            "suggested_course_key": self.course_key,
            "suggested_kind": None if self.kind is None else self.kind.value,
            "suggested_date": None if self.recorded_date is None else self.recorded_date.isoformat(),
            "suggested_week": self.week,
            "suggestion_source": self.suggestion_source,
            "suggestion_note": ";".join(self.notes) if self.notes else None,
        }


def classify_upload_item(
    *,
    name: str,
    mime_type: str | None,
    from_upload_folder: bool,
    explicit_course_key: str | None,
    origin: Origin | None,
    alias_index: CourseAliasIndex,
    semester: SemesterRange | None,
    calendar: CourseCalendar | None,
    probe: SourceProbe | None,
    s2_available: bool = False,
) -> ClassificationOutcome:
    """Run S0/S1 and the assignment axes for one Drive file.

    S0 (§2.4) is decided before and independently of the Kind: ``origin`` is
    PROFESSOR_SOURCE only when the caller verified a Canvas→Drive binding
    (exact ids, revision and bytes); otherwise an upload-folder file whose
    name follows the transcript pattern is USER_AUTHORED_TRANSCRIPT whatever
    its MIME, and everything else is UNKNOWN.
    """

    if origin is None:
        origin = (
            Origin.USER_AUTHORED_TRANSCRIPT
            if from_upload_folder and TRANSCRIPT_FILENAME.match(name.strip())
            else Origin.UNKNOWN
        )
    signals = ItemSignals(
        name,
        mime_type=mime_type,
        origin=origin,
        from_upload_folder=from_upload_folder,
    )
    decision: RuleDecision = classify_by_rules(signals)
    notes: list[str] = []
    kind = decision.kind
    # --- assignment axes -------------------------------------------------------
    course_key: str | None = None
    course_basis: str | None = None
    week: int | None = None
    recorded_date: date | None = None
    transcript_axes = transcript_signals(name, alias_index) if from_upload_folder else {}
    if explicit_course_key:
        course_key, course_basis = explicit_course_key, "upload_folder"
    elif transcript_axes.get("course_key"):
        course_key, course_basis = str(transcript_axes["course_key"]), "config_alias"
    if transcript_axes:
        week_axis = transcript_axes.get("week")
        week = week_axis if isinstance(week_axis, int) and not isinstance(week_axis, bool) else None
        parsed = transcript_axes.get("date")
        recorded_date = parsed if isinstance(parsed, date) else None
    else:
        week = week_from_filename(name)
    resolved_origin = origin
    # --- decision type / S3 reasons ------------------------------------------------
    decision_type: DecisionType | None = DecisionType.RULE if decision.decided else None
    if kind is None:
        notes.append(NOTE_NO_SINGLE_RULE)
        if not s2_available:
            notes.append(NOTE_CLASSIFIER_DISABLED)
    elif kind is Kind.UNSUPPORTED:
        notes.append(NOTE_UNSUPPORTED)
    if kind is not None and kind is not Kind.UNSUPPORTED and course_key is None:
        notes.append(NOTE_COURSE_UNRESOLVED)
    # --- calendar (TRANSCRIPT only) -----------------------------------------------
    calendar_match: CalendarMatch | None = None
    if kind is Kind.TRANSCRIPT and course_key is not None:
        calendar_match = match_transcript(
            calendar, transcript_date=recorded_date, transcript_week=week, semester=semester
        )
        if calendar_match.status is MatchStatus.MISMATCH:
            notes.append(NOTE_CALENDAR_MISMATCH)
        elif calendar_match.status is MatchStatus.NO_CALENDAR:
            notes.append(NOTE_NO_CALENDAR)
        elif calendar_match.status is MatchStatus.AMBIGUOUS:
            notes.append(NOTE_CALENDAR_AMBIGUOUS)
        elif calendar_match.week is not None and week is None:
            week = calendar_match.week
    # --- handling mode (§6.1 matrix) ---------------------------------------------
    handling: HandlingMode | None = None
    if kind is not None:
        extension = name.rsplit(".", 1)[1].lower() if "." in name else None
        if probe is None or probe.unavailable:
            handling = HandlingMode.S3
            if probe is not None and probe.unavailable:
                notes.append(NOTE_SOURCE_UNAVAILABLE)
        elif probe.too_large:
            handling = HandlingMode.S3
            notes.append(NOTE_SOURCE_TOO_LARGE)
        else:
            handling = handling_mode(
                kind, mime_type=mime_type, extension=extension,
                head=probe.payload, payload_complete=probe.complete,
            )
            if handling is HandlingMode.S3:
                notes.append(NOTE_FORMAT_KIND_MISMATCH)
    # P-B1 has no automatic execution path: the item always goes to a HUMAN draft.
    notes.append(NOTE_AUTO_UNAVAILABLE)
    suggestion_source = (
        f"rule:{decision.rule_id}" if decision.decided
        else "calendar" if calendar_match is not None and calendar_match.status is MatchStatus.MATCHED
        else None
    )
    return ClassificationOutcome(
        kind=kind,
        origin=resolved_origin,
        decision_type=decision_type,
        rule_id=decision.rule_id,
        rule_table_version=RULE_TABLE_VERSION,
        candidates=decision.candidates,
        course_key=course_key,
        course_basis=course_basis,
        week=week,
        recorded_date=recorded_date,
        calendar=calendar_match,
        handling=handling,
        notes=tuple(dict.fromkeys(notes)),
        suggestion_source=suggestion_source,
        semester=semester,
    )


__all__ = [
    "NOTE_AUTO_UNAVAILABLE",
    "NOTE_CALENDAR_AMBIGUOUS",
    "NOTE_CALENDAR_MISMATCH",
    "NOTE_CLASSIFIER_DISABLED",
    "NOTE_COURSE_UNRESOLVED",
    "NOTE_FORMAT_KIND_MISMATCH",
    "NOTE_NO_CALENDAR",
    "NOTE_NO_SINGLE_RULE",
    "NOTE_SOURCE_TOO_LARGE",
    "NOTE_SOURCE_UNAVAILABLE",
    "NOTE_UNSUPPORTED",
    "ClassificationOutcome",
    "SourceProbe",
    "classify_upload_item",
]
