"""Readable Input Request suggestion text: what a person sees next to the fields they fill.

These two Notion properties are written for people and never read back by the worker, so the
machine codes stay at the end in parentheses for diagnosis.
"""

from __future__ import annotations

from collections.abc import Iterable

_NOTE_TEXT = {
    "NO_CALENDAR": "Canvas에 이 과목 녹화 일정이 없음",
    "CALENDAR_MISMATCH": "Canvas 녹화 일정과 날짜·주차가 다름",
    "CALENDAR_AMBIGUOUS": "Canvas 녹화 일정이 모호함",
    "CANVAS_WEEK_CONFLICT": "Canvas 주차 정보가 서로 다름",
    "COURSE_UNRESOLVED": "과목을 정하지 못함",
    "NO_SINGLE_RULE": "규칙으로 종류를 정하지 못함",
    "LOW_CONFIDENCE": "AI 판단 확신이 낮음",
    "CLASSIFIER_NO_MATCH": "AI가 맞는 종류를 찾지 못함",
    "CLASSIFIER_DISABLED": "AI 분류가 꺼져 있음",
    "PII_UNCERTAIN": "개인정보 가능성 때문에 AI에 보내지 않음",
    "SOURCE_TOO_LARGE": "파일이 너무 큼",
    "SOURCE_UNAVAILABLE": "파일을 읽지 못함",
    "UNSUPPORTED_FORMAT": "지원하지 않는 형식",
    "FORMAT_KIND_MISMATCH": "파일 형식과 종류가 맞지 않음",
    "AUTO_NOT_ENABLED": "자동 처리가 꺼져 있음",
    "SESSION_OCCUPIED": "그 날짜 Session에 다른 전사문이 있음",
}


def readable_note(note: str) -> str:
    codes = [code for code in note.split(";") if code]
    words = [_NOTE_TEXT[code] for code in codes if code in _NOTE_TEXT]
    return f"{' · '.join(words)} ({note})" if words else note


def readable_course(course_key: str, courses: Iterable[tuple[str, str]]) -> str:
    names = {key: name for key, name in courses}
    name = names.get(course_key)
    return f"{name} — {course_key}" if name else course_key


__all__ = ["readable_course", "readable_note"]
