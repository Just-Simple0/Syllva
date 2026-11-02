"""Natural-language entity resolution over the v2 catalog (implementation spec §9.3, design §7.2, C-14).

Midterm use showed the v1.2 resolver could not find anything without exact ids
("TCP", "Chapter 04", "4주차", dates and course names all failed). Because v2
agents have no tools, choosing the right sources IS answer quality, so this
resolver must handle how students actually ask.

Order (spec §9.3):

1. normalise (NFC, casefold, whitespace, intent words);
2. course: name / code / key / alias contained in the query, or a one-letter
   typo of a name token; a ``course_hint`` restricts the search;
3. session: "N주차" (both the session number AND the calendar week are offered,
   because file names and the LMS calendar disagreed by one week in practice),
   "N강/N회차/N번째 강의", "S01", "week N", dates (MM.DD, M/D, YYYY-MM-DD, M월 D일);
4. material: title tokens, chapter numbers ("Chapter 04" ↔ "04_05", "ch4", "4장"),
   kind words (기출, 전사문, 과제, ...);
5. otherwise ``none`` (a concept question: the caller asks for a course or runs
   concept search).

Several candidates are ALWAYS returned as ``ambiguous`` with a resolution
handle; one is never picked silently.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

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
_INTENT_WORDS = frozenset(
    {"정리", "정리해줘", "설명", "설명해줘", "요약", "요약해줘", "알려줘", "알려", "summarize", "summary",
     "explain", "explanation", "please", "강의", "자료", "보여줘", "찾아줘", "뭐야", "뭐였지"}
)
_WEEK_RE = re.compile(r"(\d{1,2})\s*주\s*차")
_WEEK_EN_RE = re.compile(r"\bweek\s*(\d{1,2})\b")
_SESSION_RE = re.compile(r"(\d{1,2})\s*(?:강(?![가-힣])|회차|번째\s*강의)")
_SESSION_ID_RE = re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![0-9])")
_DATE_PATTERNS = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(r"(?<![\d.])(?P<m>\d{1,2})\.(?P<d>\d{1,2})(?![\d.])"),
    re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])"),
    re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
)
_CHAPTER_RE = re.compile(r"(?:chapter|chap|ch)\.?\s*0*(\d{1,3})|0*(\d{1,3})\s*장(?![가-힣])")
_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")
_PARTICLES = ("에서", "으로", "에게", "의", "을", "를", "은", "는", "이", "가", "에", "도", "로", "와", "과")


def strip_intent_words(text: str) -> str:
    return " ".join(t for t in text.split(" ") if t.strip("?!.,") not in _INTENT_WORDS)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold()).strip()


def compact(text: str) -> str:
    return re.sub(r"[\s_\-.()\[\]]+", "", norm(text))


def _strip_particle(token: str) -> str:
    for particle in _PARTICLES:
        if len(token) > len(particle) + 1 and token.endswith(particle):
            return token[: -len(particle)]
    return token


@dataclass(frozen=True)
class NaturalResolution:
    status: Status
    entity: ResolvedEntity | None = None
    resolution_id: str | None = None
    candidates: tuple[ResolutionCandidate, ...] = ()
    reason: str | None = None
    course_key: str | None = None  # course the query was narrowed to, when known


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


class NaturalResolver:
    def __init__(
        self,
        reader: StateReader,
        ephemeral: object,
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
        text = norm(strip_intent_words(norm(query)))
        flat = compact(text)

        if course_hint is not None:
            scoped = [c for c in courses.values() if c.key == course_hint]
            if not scoped:
                return NaturalResolution("none", reason="UNKNOWN_COURSE_HINT")
            course_ids = {scoped[0].id}
        else:
            course_ids = self._match_courses(courses.values(), text, flat)

        session_hits = self._match_sessions(courses, sessions, text, course_ids)
        course_words = {compact(n) for c in courses.values() for n in c.names()}
        material_hits = self._match_materials(materials, text, course_ids, session_hits, course_words)

        if material_hits is not None:
            return self._finish([("material", m.material_id, self._material_label(courses, m), reason) for m, reason in material_hits], courses, course_ids)
        if session_hits is not None:
            return self._finish([("session", s.session_id, self._session_label(courses, s), reason) for s, reason in session_hits], courses, course_ids)
        if course_ids:
            return self._finish([("course", courses[i].key, courses[i].name, "course name") for i in sorted(course_ids)], courses, course_ids)
        return NaturalResolution("none", reason="NO_ENTITY_CUE")

    # -- matching -------------------------------------------------------------------

    @staticmethod
    def _match_courses(courses: Iterable[_Course], text: str, flat: str) -> set[int]:
        hits: set[int] = set()
        tokens = [_strip_particle(t) for t in _TOKEN_RE.findall(text)]
        for course in courses:
            for name in course.names():
                key = compact(name)
                if len(key) >= 2 and key in flat:
                    hits.add(course.id)
                    break
            else:
                name_tokens = {t for name in course.names() for t in _TOKEN_RE.findall(norm(name)) if len(t) >= 3}
                whole = {compact(name) for name in course.names() if len(compact(name)) >= 3}
                for token in tokens:
                    if len(token) >= 3 and difflib.get_close_matches(token, list(name_tokens | whole), n=1, cutoff=0.8):
                        hits.add(course.id)
                        break
        # a longer name that contains a shorter one wins ("알고리즘2" over "알고리즘")
        if len(hits) > 1:
            best = max(len(compact(n)) for c in courses if c.id in hits for n in c.names() if compact(n) in flat) if any(
                compact(n) in flat for c in courses if c.id in hits for n in c.names()
            ) else 0
            if best:
                hits = {c.id for c in courses if c.id in hits and any(len(compact(n)) == best and compact(n) in flat for n in c.names())}
        return hits

    def _match_sessions(
        self, courses: Mapping[int, _Course], sessions: list[_Session], text: str, course_ids: set[int]
    ) -> list[tuple[_Session, str]] | None:
        pool = [s for s in sessions if not course_ids or s.course_id in course_ids]
        found: dict[str, tuple[_Session, str]] = {}
        cue = False

        def add(items: Iterable[_Session], reason: str) -> None:
            for item in items:
                found.setdefault(item.session_id, (item, reason))

        for regex in (_WEEK_RE, _WEEK_EN_RE):
            for match in regex.finditer(text):
                cue = True
                week = int(match.group(1))
                add((s for s in pool if s.number == week), f"session number {week}")
                add((s for s in pool if self._calendar_week(courses, sessions, s) == week), f"calendar week {week}")
        for regex in (_SESSION_RE, _SESSION_ID_RE):
            for match in regex.finditer(text):
                cue = True
                number = int(match.group(1))
                add((s for s in pool if s.number == number), f"session number {number}")
        for day in self._dates(courses, course_ids, text):
            cue = True
            add((s for s in pool if s.held_on == day), f"held on {day.isoformat()}")
        if not cue:
            return None
        return list(found.values())

    def _calendar_week(self, courses: Mapping[int, _Course], sessions: list[_Session], session: _Session) -> int | None:
        if session.held_on is None:
            return None
        course = courses.get(session.course_id)
        start = self.semester_starts.get(course.semester) if course else None
        if start is None:
            dated = [s.held_on for s in sessions if s.course_id == session.course_id and s.held_on is not None]
            if not dated:
                return None
            start = min(dated)
        monday = start - timedelta(days=start.weekday())
        return (session.held_on - monday).days // 7 + 1

    def _dates(self, courses: Mapping[int, _Course], course_ids: set[int], text: str) -> list[date]:
        years = {int(c.semester[:4]) for c in courses.values() if (not course_ids or c.id in course_ids) and c.semester[:4].isdigit()}
        default_year = max(years) if years else self.today.year
        found: list[date] = []
        for pattern in _DATE_PATTERNS:
            for match in pattern.finditer(text):
                year = int(match.group("y")) if "y" in pattern.groupindex else default_year
                try:
                    found.append(date(year, int(match.group("m")), int(match.group("d"))))
                except ValueError:
                    continue
        return found

    def _match_materials(
        self,
        materials: list[_Material],
        text: str,
        course_ids: set[int],
        session_hits: list[tuple[_Session, str]] | None,
        course_words: set[str],
    ) -> list[tuple[_Material, str]] | None:
        pool = [m for m in materials if not course_ids or m.course_id in course_ids]
        if session_hits:
            in_sessions = {s.session_id for s, _ in session_hits}
            pool = [m for m in pool if m.session_id in in_sessions] or pool
        kinds = {kind for kind, words in _KIND_WORDS.items() for t in _TOKEN_RE.findall(text) if _strip_particle(t) in words}
        chapters = {int(a or b) for a, b in _CHAPTER_RE.findall(text)}
        stop = {w for words in _KIND_WORDS.values() for w in words}
        words = [
            _strip_particle(t)
            for t in _TOKEN_RE.findall(_CHAPTER_RE.sub(" ", _WEEK_RE.sub(" ", text)))
            if len(_strip_particle(t)) >= 2 and _strip_particle(t) not in stop and not t.isdigit()
            # words that belong to a course name or code identify the course, not a material
            and not any(_strip_particle(t) in name for name in course_words)
            and not _SESSION_ID_RE.fullmatch(t)
        ]
        if not (kinds or chapters or words):
            return None
        scored: list[tuple[int, _Material, str]] = []
        for material in pool:
            title = norm(material.title)
            title_numbers = {int(n) for n in re.findall(r"\d+", title)}
            score, reasons = 0, []
            if kinds and material.kind in kinds:
                score += 2
                reasons.append(f"kind {material.kind}")
            if chapters and chapters & title_numbers:
                score += 3
                reasons.append("chapter " + ",".join(str(c) for c in sorted(chapters & title_numbers)))
            for word in words:
                if word in title or word in compact(title):
                    score += 2
                    reasons.append(f"title has '{word}'")
            if kinds and material.kind not in kinds:
                continue  # the user named a kind; other kinds are not answers
            if chapters and not chapters & title_numbers:
                continue
            if score:
                scored.append((score, material, "; ".join(reasons)))
        if not scored:
            # words that are only course names or session cues are not material cues
            return None if not (kinds or chapters) else []
        top = max(score for score, _, _ in scored)
        return [(m, reason) for score, m, reason in scored if score == top]

    # -- results ------------------------------------------------------------------------

    @staticmethod
    def _session_label(courses: Mapping[int, _Course], s: _Session) -> str:
        course = courses.get(s.course_id)
        bits = [course.name if course else "", f"{s.number}회차" if s.number else "", s.held_on.isoformat() if s.held_on else "", s.title or ""]
        return " · ".join(b for b in bits if b)

    @staticmethod
    def _material_label(courses: Mapping[int, _Course], m: _Material) -> str:
        course = courses.get(m.course_id)
        return f"{course.name if course else ''} · {m.title}"

    def _finish(self, items: list[tuple[str, str, str, str]], courses: Mapping[int, _Course], course_ids: set[int]) -> NaturalResolution:
        course_key = courses[next(iter(course_ids))].key if len(course_ids) == 1 else None
        if not items:
            return NaturalResolution("none", reason="NO_MATCH", course_key=course_key)
        if len(items) == 1:
            entity_type, entity_id, label, reason = items[0]
            return NaturalResolution(
                "resolved", entity=ResolvedEntity(entity_type, entity_id, label, reason=reason), course_key=course_key
            )
        candidates = [ResolutionCandidate("", t, i, label, reason) for t, i, label, reason in items[:MAX_CANDIDATES]]
        handle = self.ephemeral.create_resolution(candidates, self.ttl)  # type: ignore[attr-defined]
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
