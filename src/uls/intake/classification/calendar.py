"""Recording calendar projection and transcript matching (plan §2.3).

The calendar is a metadata index built from the latest active Canvas
``RECORDING`` observations of one course.  Entries are validated against a
weekly cadence anchored at the smallest week; anything further than six days
from its expected date is an anomaly, and entries whose dates are not
monotonic in week order are marked ambiguous.  A calendar is ``AMBIGUOUS``
as a whole (no matching at all) when the collection was incomplete, the
anchor is not unique or outside the semester, or fewer than two valid
entries exist.  Matching itself is a pure function so the worker can record
the exact basis hash it decided on and re-verify it before any write.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final

CADENCE_TOLERANCE_DAYS: Final[int] = 6


class EntryStatus(str, Enum):
    CALENDAR = "CALENDAR"
    ANOMALY = "ANOMALY"
    AMBIGUOUS = "AMBIGUOUS"

    def __str__(self) -> str:
        return str(self.value)


class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    NO_CALENDAR = "NO_CALENDAR"
    AMBIGUOUS = "AMBIGUOUS"
    MISMATCH = "MISMATCH"

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class SemesterRange:
    """Effective semester date range and where it came from (§3.4 semester_range_basis)."""

    start: date
    end: date
    source: str  # "config" | "canvas_term"

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def basis(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "source": self.source}


@dataclass(frozen=True)
class RecordingEntry:
    """One active (latest revision) recording observation."""

    canvas_course_id: int
    resource_id: str
    observation_revision: int
    week: int
    recorded_on: date


@dataclass(frozen=True)
class CalendarEntry:
    entry: RecordingEntry
    status: EntryStatus
    expected: date | None = None


@dataclass(frozen=True)
class CourseCalendar:
    course_key: str
    entries: tuple[CalendarEntry, ...]
    ambiguous: bool
    reason: str | None
    semester: SemesterRange | None

    def revision_hash(self) -> str:
        """Canonical hash of everything a match decision depended on (§3.4)."""

        payload = {
            "course_key": self.course_key,
            "ambiguous": self.ambiguous,
            "reason": self.reason,
            "semester": self.semester.basis() if self.semester else None,
            "entries": [
                [e.entry.canvas_course_id, e.entry.resource_id, e.entry.observation_revision,
                 e.entry.week, e.entry.recorded_on.isoformat(), e.status.value]
                for e in sorted(
                    self.entries,
                    key=lambda e: (e.entry.week, e.entry.recorded_on.isoformat(),
                                   e.entry.resource_id),
                )
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def build_calendar(
    course_key: str,
    entries: list[RecordingEntry],
    *,
    semester: SemesterRange | None,
    collection_complete: bool,
) -> CourseCalendar:
    """Project active observations into a validated calendar (§2.3, r4 M2, r6 R1)."""

    def ambiguous(reason: str) -> CourseCalendar:
        return CourseCalendar(
            course_key,
            tuple(CalendarEntry(e, EntryStatus.AMBIGUOUS) for e in entries),
            True, reason, semester,
        )

    if not collection_complete:
        return ambiguous("COLLECTION_INCOMPLETE")
    if semester is None:
        return ambiguous("SEMESTER_RANGE_UNKNOWN")
    if not entries:
        return CourseCalendar(course_key, (), False, None, semester)
    min_week = min(e.week for e in entries)
    anchors = {e.recorded_on for e in entries if e.week == min_week}
    if len(anchors) != 1:
        return ambiguous("ANCHOR_NOT_UNIQUE")
    anchor_date = next(iter(anchors))
    if not semester.contains(anchor_date):
        return ambiguous("ANCHOR_OUTSIDE_SEMESTER")
    projected: list[CalendarEntry] = []
    for recording in entries:
        expected = anchor_date + timedelta(days=7 * (recording.week - min_week))
        in_semester = semester.contains(recording.recorded_on)
        on_cadence = abs((recording.recorded_on - expected).days) <= CADENCE_TOLERANCE_DAYS
        status = EntryStatus.CALENDAR if in_semester and on_cadence else EntryStatus.ANOMALY
        projected.append(CalendarEntry(recording, status, expected))
    # Week order must be date order: entries of a lower week recorded after an
    # entry of a higher week are mutual conflicts and never match (r1 R3 of P-A).
    valid = [e for e in projected if e.status is EntryStatus.CALENDAR]
    conflicted: set[tuple[str, int]] = set()
    for earlier in valid:
        for later in valid:
            if earlier.entry.week < later.entry.week and (
                earlier.entry.recorded_on >= later.entry.recorded_on
            ):
                conflicted.add((earlier.entry.resource_id, earlier.entry.observation_revision))
                conflicted.add((later.entry.resource_id, later.entry.observation_revision))
    # Two or more active observations on one date never match, whatever their
    # cadence status (M3/R1, P-A r2 #2): every entry of a duplicated date is AMBIGUOUS.
    seen: dict[date, int] = {}
    for projected_entry in projected:
        seen[projected_entry.entry.recorded_on] = seen.get(projected_entry.entry.recorded_on, 0) + 1
    final: list[CalendarEntry] = []
    for projected_entry in projected:
        key = (projected_entry.entry.resource_id, projected_entry.entry.observation_revision)
        if seen[projected_entry.entry.recorded_on] > 1 or (
            projected_entry.status is EntryStatus.CALENDAR and key in conflicted
        ):
            final.append(CalendarEntry(projected_entry.entry, EntryStatus.AMBIGUOUS,
                                       projected_entry.expected))
        else:
            final.append(projected_entry)
    if sum(1 for e in final if e.status is EntryStatus.CALENDAR) < 2:
        return ambiguous("TOO_FEW_VALID_ENTRIES")
    return CourseCalendar(course_key, tuple(final), False, None, semester)


@dataclass(frozen=True)
class CalendarMatch:
    status: MatchStatus
    reason: str
    week: int | None = None
    entry: RecordingEntry | None = None


def match_transcript(
    calendar: CourseCalendar | None,
    *,
    transcript_date: date | None,
    transcript_week: int | None,
    semester: SemesterRange | None = None,
) -> CalendarMatch:
    """Decide the transcript↔recording match with the fixed failure priority (§10 O2).

    Order: semester range (from ``semester`` or the calendar's own range) →
    calendar presence → ambiguity → date → week.  Without any range basis the
    range step is skipped rather than guessed.
    """

    if transcript_date is None:
        return CalendarMatch(MatchStatus.MISMATCH, "NO_DATE")
    effective_range = semester or (calendar.semester if calendar is not None else None)
    if effective_range is not None and not effective_range.contains(transcript_date):
        return CalendarMatch(MatchStatus.MISMATCH, "OUTSIDE_SEMESTER")
    if calendar is None:
        return CalendarMatch(MatchStatus.NO_CALENDAR, "NO_CALENDAR")
    if calendar.ambiguous:
        # An ambiguous calendar keeps its reason even when it holds no entries
        # (incomplete collection, unknown semester): never NO_CALENDAR (r10 R6).
        return CalendarMatch(MatchStatus.AMBIGUOUS, calendar.reason or "AMBIGUOUS")
    if not calendar.entries:
        return CalendarMatch(MatchStatus.NO_CALENDAR, "NO_CALENDAR")
    same_day = [e for e in calendar.entries if e.entry.recorded_on == transcript_date]
    candidates = [e for e in same_day if e.status is EntryStatus.CALENDAR]
    if len(candidates) != 1:
        if any(e.status is EntryStatus.AMBIGUOUS for e in same_day):
            return CalendarMatch(MatchStatus.AMBIGUOUS, "DUPLICATE_DATE")
        if any(e.status is EntryStatus.ANOMALY for e in same_day):
            return CalendarMatch(MatchStatus.MISMATCH, "DATE_ANOMALY")
        return CalendarMatch(MatchStatus.MISMATCH, "DATE_NOT_IN_CALENDAR")
    entry = candidates[0].entry
    if transcript_week is not None and transcript_week != entry.week:
        return CalendarMatch(MatchStatus.MISMATCH, "WEEK_MISMATCH", entry.week, entry)
    return CalendarMatch(MatchStatus.MATCHED, "MATCHED", entry.week, entry)


__all__ = [
    "CADENCE_TOLERANCE_DAYS",
    "CalendarEntry",
    "CalendarMatch",
    "CourseCalendar",
    "EntryStatus",
    "MatchStatus",
    "RecordingEntry",
    "SemesterRange",
    "build_calendar",
    "match_transcript",
]
