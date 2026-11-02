"""Natural-language entity resolution over the v2 catalog (implementation spec §9.3, design §7.2, C-14).

Midterm use showed the v1.2 resolver could not find anything without exact ids
("TCP", "Chapter 04", "4주차", dates and course names all failed). Because v2
agents have no tools, choosing the right sources IS answer quality, so this
resolver must handle how students actually ask — and must never pick one of
several plausible entities silently (design §5.3, §7.2).

Pipeline:

1. normalise (NFC, casefold, whitespace);
2. extract CUES from the full text first (so intent-word removal cannot break
   them): weeks ("N주차", "week N" — offered as BOTH session number and calendar
   week, because file names and the LMS calendar disagreed by one week in
   practice), sessions ("N강", "N강의", "N회차", "N번째", "S01"), dates (MM.DD, M/D,
   ISO, "M월 D일"), chapters ("Chapter 04", "ch4", "4장");
3. COURSES are matched on the text with those cue spans masked, by TOKEN
   boundaries: a name matches when a run of consecutive query tokens (the last
   one may carry a Korean particle) concatenates exactly to the name. Short
   aliases such as "DB"/"OS" therefore never match inside other words, and
   "알고리즘 2강" means course "알고리즘" + session 2, not course "알고리즘2".
   When two matches overlap the longer one wins ("알고리즘2" over "알고리즘");
   separate mentions of different courses are kept as several courses.
   One-letter typos are accepted only when the digits are identical.
   Known limit: a course-name digit glued to a cue ("알고리즘2강") is read as the cue
   ("알고리즘" + 2강); with a space ("알고리즘2 2강") both are understood;
4. MATERIALS: kind words narrow hard; chapter numbers and title words are
   scored separately and, when they point at different materials, all of them
   are offered (conflicting cues are ambiguous, not a pick);
5. result: ``resolved`` for exactly one candidate, ``ambiguous`` (always via an
   ``EphemeralStore`` handle with per-candidate reasons) for several, ``none``
   otherwise. Free words that match nothing are treated as a concept question:
   with one known course the result is ``none`` with ``course_key`` set and
   reason ``CONCEPT_QUERY`` (the caller runs concept search in that course).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Literal

from syllva.ephemeral.base import EphemeralStore
from syllva.ephemeral.models import ResolutionCandidate, ResolvedEntity
from syllva.state.v2.reader import StateReader

Status = Literal["resolved", "ambiguous", "none"]
MAX_CANDIDATES = 20

_KIND_WORDS: dict[str, frozenset[str]] = {
    "EXAM": frozenset({"기출", "기출문제", "시험지", "exam", "midterm", "final", "중간고사", "기말고사"}),
    "TRANSCRIPT": frozenset({"전사문", "녹취", "스크립트", "transcript", "녹음"}),
    "LECTURE_SLIDES": frozenset({"강의자료", "슬라이드", "ppt", "slides", "slide", "강의노트"}),
    "ASSIGNMENT_BRIEF": frozenset({"과제", "숙제", "hw", "homework", "assignment"}),
    "LAB_MATERIAL": frozenset({"실습", "lab"}),
}
_ALL_KIND_WORDS = frozenset(w for words in _KIND_WORDS.values() for w in words)
_INTENT_WORDS = frozenset(
    {"정리", "정리해줘", "설명", "설명해줘", "요약", "요약해줘", "알려줘", "알려", "summarize", "summary",
     "explain", "explanation", "please", "강의", "자료", "보여줘", "찾아줘", "뭐야", "뭐였지", "내용"}
)
_PARTICLES = ("이랑", "에서", "으로", "에게", "하고", "이나", "랑", "의", "을", "를", "은", "는", "이", "가", "에", "도", "로", "와", "과", "나")

_WEEK_RES = (re.compile(r"(?<!\d)(\d{1,2})\s*주\s*차"), re.compile(r"\bweek\s*(\d{1,2})\b"))
_SESSION_RES = (
    re.compile(r"(?<!\d)(\d{1,2})\s*(?:강(?:의)?|회차|번째(?:\s*강의)?)(?![가-힣])"),
    re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![0-9])"),
)
_DATE_RES = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(r"(?<![\d.])(?P<m>\d{1,2})\.(?P<d>\d{1,2})(?![\d.])"),
    re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])"),
    re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
)
# "ch" needs a word boundary on the left so course codes (MECH201, ARCH3) and words
# (which 4, branch 3) are not chapters.
_CHAPTER_RE = re.compile(r"(?<![a-z])(?:chapter|chap|ch)\.?\s*0*(\d{1,3})(?!\d)|(?<!\d)0*(\d{1,3})\s*장(?![가-힣])")
# Chapter numbers a TITLE declares: explicit chapter words, or a leading number group
# like "04_05 ..." / "Chapter 04_05". Other digits in a title (course names such as
# 알고리즘2, session ids like S02, years) are NOT chapters.
_TITLE_GROUP_RE = re.compile(r"(?<!\w)0*(\d{1,3})((?:_0*\d{1,3})+)(?!\d)")
_EXTENSION_RE = re.compile(r"\.[a-z0-9]{2,5}$")
_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold()).strip()


def compact(text: str) -> str:
    return "".join(_TOKEN_RE.findall(norm(text)))


def strip_particle(token: str) -> str:
    for particle in _PARTICLES:
        if len(token) > len(particle) + 1 and token.endswith(particle):
            return token[: -len(particle)]
    return token


def title_chapters(title: str) -> set[int]:
    text = norm(title)
    found = {int(a or b) for a, b in _CHAPTER_RE.findall(text)}
    for first, rest in _TITLE_GROUP_RE.findall(text):
        numbers = [int(first), *(int(n) for n in re.findall(r"\d+", rest))]
        if len(numbers) == 2 and 1 <= numbers[0] <= 12 and 13 <= numbers[1] <= 31:
            continue  # "09_17 ..." is a date, not chapters 9 and 17
        found.update(numbers)
    return found


def title_text(title: str) -> str:
    """Normalised title without its file extension (so 'pdf'/'txt' are not title words)."""

    return _EXTENSION_RE.sub("", norm(title))


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


@dataclass(frozen=True)
class NaturalResolution:
    status: Status
    entity: ResolvedEntity | None = None
    resolution_id: str | None = None
    candidates: tuple[ResolutionCandidate, ...] = ()
    reason: str | None = None
    course_key: str | None = None  # the single course the query was narrowed to, when known
    # words the resolver did not use to pick the entity (e.g. a concept inside a session:
    # "알고리즘2 2강 다익스트라"); the caller may run concept search with them in that scope
    unused_words: tuple[str, ...] = ()


@dataclass
class _Course:
    id: int
    key: str
    name: str
    code: str | None
    semester: str
    aliases: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [n for n in (self.name, self.code, self.key, *self.aliases) if n]


@dataclass(frozen=True)
class _Session:
    session_id: str
    course_id: int
    number: int | None
    held_on: date | None
    title: str | None


@dataclass(frozen=True)
class _Material:
    material_id: str
    course_id: int
    session_id: str | None
    title: str
    kind: str


@dataclass(frozen=True)
class _Cues:
    weeks: tuple[int, ...]
    sessions: tuple[int, ...]
    dates: tuple[tuple[int | None, int, int], ...]  # (year or None, month, day)
    chapters: tuple[int, ...]
    masked: str  # text with every cue span blanked out

    @property
    def any_session_cue(self) -> bool:
        return bool(self.weeks or self.sessions or self.dates)


def extract_cues(text: str) -> _Cues:
    spans: list[tuple[int, int]] = []
    weeks: list[int] = []
    sessions: list[int] = []
    dates: list[tuple[int | None, int, int]] = []
    chapters: list[int] = []

    def free(start: int) -> bool:
        return not any(a <= start < b for a, b in spans)

    for regex in _WEEK_RES:
        for m in regex.finditer(text):
            weeks.append(int(m.group(1)))
            spans.append(m.span())
    for regex in _SESSION_RES:
        for m in regex.finditer(text):
            if free(m.start()):
                sessions.append(int(m.group(1)))
                spans.append(m.span())
    for regex in _DATE_RES:
        for m in regex.finditer(text):
            if free(m.start()):
                year = int(m.group("y")) if "y" in regex.groupindex else None
                dates.append((year, int(m.group("m")), int(m.group("d"))))
                spans.append(m.span())
    for m in _CHAPTER_RE.finditer(text):
        if free(m.start()):
            chapters.append(int(m.group(1) or m.group(2)))
            spans.append(m.span())
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return _Cues(tuple(weeks), tuple(sessions), tuple(dates), tuple(chapters), "".join(chars))


class NaturalResolver:
    def __init__(
        self,
        reader: StateReader,
        ephemeral: EphemeralStore,
        *,
        today: date | None = None,
        semester_starts: Mapping[str, date] | None = None,
        resolution_ttl_seconds: int = 900,
    ) -> None:
        self.reader = reader
        self.ephemeral = ephemeral
        self.today = today or date.today()
        self.semester_starts = dict(semester_starts or {})
        self.ttl = resolution_ttl_seconds

    # -- catalog ----------------------------------------------------------------

    def _load(self) -> tuple[dict[int, _Course], list[_Session], list[_Material]]:
        courses = {
            int(r["id"]): _Course(int(r["id"]), str(r["course_key"]), str(r["name"]), r["code"], str(r["semester"]))
            for r in self.reader.fetch_all("SELECT id, course_key, name, code, semester FROM courses")
        }
        for r in self.reader.fetch_all("SELECT course_id, alias_nfc FROM course_aliases"):
            if int(r["course_id"]) in courses:
                courses[int(r["course_id"])].aliases.append(str(r["alias_nfc"]))
        sessions = [
            _Session(str(r["session_id"]), int(r["course_id"]), r["number"], _parse_date(r["held_on"]), r["title"])
            for r in self.reader.fetch_all("SELECT session_id, course_id, number, held_on, title FROM sessions")
        ]
        materials = [
            _Material(str(r["material_id"]), int(r["course_id"]), r["session_id"], str(r["title"]), str(r["kind"]))
            for r in self.reader.fetch_all(
                "SELECT m.material_id, m.course_id, s.session_id, f.display_name AS title, m.kind "
                "FROM materials m JOIN source_files f ON f.id = m.source_file_id "
                "LEFT JOIN sessions s ON s.id = m.session_id WHERE f.deleted_at IS NULL"
            )
        ]
        return courses, sessions, materials

    # -- entry point --------------------------------------------------------------

    def resolve(self, query: str, *, course_hint: str | None = None) -> NaturalResolution:
        courses, sessions, materials = self._load()
        cues = extract_cues(norm(query))
        tokens = _TOKEN_RE.findall(cues.masked)

        consumed: set[int] = set()
        if course_hint is not None:
            scoped = [c for c in courses.values() if c.key == course_hint]
            if not scoped:
                return NaturalResolution("none", reason="UNKNOWN_COURSE_HINT")
            course_ids = {scoped[0].id}
        else:
            course_ids, consumed = self._match_courses(list(courses.values()), tokens)
        course_key = courses[next(iter(course_ids))].key if len(course_ids) == 1 else None

        free = [strip_particle(t) for i, t in enumerate(tokens) if i not in consumed]
        free = [t for t in free if t not in _INTENT_WORDS]
        kinds = {kind for kind, words in _KIND_WORDS.items() for t in free if t in words}
        words = [t for t in free if len(t) >= 2 and t not in _ALL_KIND_WORDS and not t.isdigit()]

        session_hits = self._match_sessions(courses, sessions, cues, course_ids) if cues.any_session_cue else None
        if session_hits is not None and not session_hits:
            return NaturalResolution("none", reason="NO_MATCH", course_key=course_key)

        if kinds or cues.chapters or words:
            material_hits = self._match_materials(materials, course_ids, session_hits, kinds, cues.chapters, words)
            if not material_hits and session_hits and words and not (kinds or cues.chapters):
                # the title words point at materials of OTHER sessions: conflicting cues,
                # offer the sessions and those materials instead of returning a session silently
                elsewhere = self._match_materials(materials, course_ids, None, kinds, (), words)
                if elsewhere:
                    items = [("session", s.session_id, self._session_label(courses, s), r) for s, r in session_hits]
                    items += [("material", m.material_id, self._material_label(courses, m), r) for m, r in elsewhere]
                    return self._finish(items, course_key, force_choice=True)
            if material_hits:
                return self._finish(
                    [("material", m.material_id, self._material_label(courses, m), r) for m, r in material_hits], course_key
                )
            if kinds or cues.chapters:
                return NaturalResolution("none", reason="NO_MATCH", course_key=course_key)
            # only free words that match no material: a concept question
            if session_hits is None and len(course_ids) == 1:
                return NaturalResolution("none", reason="CONCEPT_QUERY", course_key=course_key)
            if session_hits is None and not course_ids:
                return NaturalResolution("none", reason="NO_ENTITY_CUE")

        if session_hits is not None:
            result = self._finish(
                [("session", s.session_id, self._session_label(courses, s), r) for s, r in session_hits], course_key
            )
            return replace(result, unused_words=tuple(words))
        if course_ids:
            return self._finish(
                [("course", courses[i].key, courses[i].name, "course name") for i in sorted(course_ids)], course_key
            )
        return NaturalResolution("none", reason="NO_ENTITY_CUE")

    # -- matching -------------------------------------------------------------------

    @staticmethod
    def _match_courses(courses: list[_Course], tokens: list[str]) -> tuple[set[int], set[int]]:
        """Token-boundary course matching; returns (course ids, consumed token indexes)."""

        spans: list[tuple[int, int, int]] = []  # (start, end exclusive, course id)
        for course in courses:
            names = {compact(n) for n in course.names() if compact(n)}
            for start in range(len(tokens)):
                joined = ""
                for end in range(start, min(start + 5, len(tokens))):
                    joined += tokens[end]
                    if joined in names or strip_particle(joined) in names:
                        spans.append((start, end + 1, course.id))
        if not spans:
            # one-letter typo of a whole name or name token (>= 3 chars, identical digits)
            for course in courses:
                targets = {compact(n) for n in course.names() if len(compact(n)) >= 3}
                targets |= {t for n in course.names() for t in _TOKEN_RE.findall(norm(n)) if len(t) >= 3}
                for index, token in enumerate(tokens):
                    candidate = strip_particle(token)
                    if len(candidate) < 3:
                        continue
                    close = difflib.get_close_matches(candidate, list(targets), n=3, cutoff=0.8)
                    if any(_digits(t) == _digits(candidate) for t in close):
                        spans.append((index, index + 1, course.id))
        # Overlapping spans of different courses: only the strictly longer one survives
        # ("알고리즘 2"/"알고리즘2" over "알고리즘"). Separate mentions are all kept.
        spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
        kept: list[tuple[int, int, int]] = []
        for span in spans:
            length = span[1] - span[0]
            shadowed = any(
                span[0] < k[1] and k[0] < span[1] and k[2] != span[2] and (k[1] - k[0]) > length for k in kept
            )
            if not shadowed:
                kept.append(span)
        return {k[2] for k in kept}, {i for start, end, _ in kept for i in range(start, end)}

    def _match_sessions(
        self, courses: Mapping[int, _Course], sessions: list[_Session], cues: _Cues, course_ids: set[int]
    ) -> list[tuple[_Session, str]]:
        pool = [s for s in sessions if not course_ids or s.course_id in course_ids]
        starts = self._course_starts(courses, sessions)
        found: dict[str, tuple[_Session, str]] = {}

        def add(session: _Session, reason: str) -> None:
            found.setdefault(session.session_id, (session, reason))

        for week in cues.weeks:
            for s in pool:
                if s.number == week:
                    add(s, f"session number {week}")
            for s in pool:
                if self._calendar_week(starts, s) == week:
                    add(s, f"calendar week {week}")
        for number in cues.sessions:
            for s in pool:
                if s.number == number:
                    add(s, f"session number {number}")
        for day in self._dates(courses, course_ids, cues):
            for s in pool:
                if s.held_on == day:
                    add(s, f"held on {day.isoformat()}")
        return list(found.values())

    def _course_starts(self, courses: Mapping[int, _Course], sessions: list[_Session]) -> dict[int, date]:
        starts: dict[int, date] = {}
        for course in courses.values():
            configured = self.semester_starts.get(course.semester)
            dated = [s.held_on for s in sessions if s.course_id == course.id and s.held_on is not None]
            if configured is not None:
                starts[course.id] = configured
            elif dated:
                starts[course.id] = min(dated)
        return starts

    @staticmethod
    def _calendar_week(starts: Mapping[int, date], session: _Session) -> int | None:
        start = starts.get(session.course_id)
        if session.held_on is None or start is None:
            return None
        monday = start - timedelta(days=start.weekday())
        return (session.held_on - monday).days // 7 + 1

    def _dates(self, courses: Mapping[int, _Course], course_ids: set[int], cues: _Cues) -> list[date]:
        years = {
            int(c.semester[:4])
            for c in courses.values()
            if (not course_ids or c.id in course_ids) and c.semester[:4].isdigit()
        }
        default_year = max(years) if years else self.today.year
        found: list[date] = []
        for year, month, day in cues.dates:
            try:
                found.append(date(year or default_year, month, day))
            except ValueError:
                continue
        return found

    @staticmethod
    def _match_materials(
        materials: list[_Material],
        course_ids: set[int],
        session_hits: list[tuple[_Session, str]] | None,
        kinds: set[str],
        chapters: tuple[int, ...],
        words: list[str],
    ) -> list[tuple[_Material, str]]:
        pool = [m for m in materials if not course_ids or m.course_id in course_ids]
        if session_hits:
            in_sessions = {s.session_id for s, _ in session_hits}
            pool = [m for m in pool if m.session_id in in_sessions]
        if kinds:
            pool = [m for m in pool if m.kind in kinds]
        wanted = set(chapters)

        def title_words(m: _Material) -> list[str]:
            title = title_text(m.title)
            flat = compact(title)
            return [w for w in words if w in title or w in flat]

        by_chapter = [m for m in pool if wanted & title_chapters(m.title)] if wanted else []
        scored = [(len(title_words(m)), m) for m in pool]
        top = max((score for score, _ in scored), default=0)
        by_words = [m for score, m in scored if top and score == top]

        if wanted and words:
            both = [m for m in by_chapter if m in by_words]
            chosen = both or by_chapter + [m for m in by_words if m not in by_chapter]  # conflicting cues: offer all
        elif wanted:
            chosen = by_chapter
        elif words:
            chosen = by_words
        else:
            chosen = pool  # kind words only

        def reason(m: _Material) -> str:
            bits = []
            if kinds:
                bits.append(f"kind {m.kind}")
            if m in by_chapter:
                bits.append("chapter " + ",".join(str(c) for c in sorted(wanted)))
            if m in by_words:
                bits.append("title words " + ", ".join(title_words(m)))
            return "; ".join(bits) or "matches the request"

        return [(m, reason(m)) for m in chosen]

    # -- results ------------------------------------------------------------------------

    @staticmethod
    def _session_label(courses: Mapping[int, _Course], s: _Session) -> str:
        course = courses.get(s.course_id)
        bits = [
            course.name if course else "",
            f"{s.number}회차" if s.number else "",
            s.held_on.isoformat() if s.held_on else "",
            s.title or "",
        ]
        return " · ".join(b for b in bits if b)

    @staticmethod
    def _material_label(courses: Mapping[int, _Course], m: _Material) -> str:
        course = courses.get(m.course_id)
        return f"{course.name if course else ''} · {m.title}"

    def _finish(
        self, items: list[tuple[str, str, str, str]], course_key: str | None, *, force_choice: bool = False
    ) -> NaturalResolution:
        if not items:
            return NaturalResolution("none", reason="NO_MATCH", course_key=course_key)
        if force_choice and len(items) < 2:
            raise ValueError("force_choice needs at least two candidates")
        if len(items) == 1:
            entity_type, entity_id, label, reason = items[0]
            return NaturalResolution(
                "resolved", entity=ResolvedEntity(entity_type, entity_id, label, reason=reason), course_key=course_key
            )
        candidates = [ResolutionCandidate("", t, i, label, reason) for t, i, label, reason in items[:MAX_CANDIDATES]]
        handle = self.ephemeral.create_resolution(candidates, self.ttl)
        return NaturalResolution(
            "ambiguous", resolution_id=handle.resolution_id, candidates=tuple(handle.candidates), course_key=course_key
        )


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
