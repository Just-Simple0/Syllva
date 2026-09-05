"""Session-derived exam-signal classification helpers.

This module does not model an Exam entity or write scope confirmation.  Exam
signals are ordinary AI-owned session enrichment and remain subject to the
same evidence/freshness rules as every other signal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_EXAM_TERMS = (
    "exam",
    "midterm",
    "final",
    "quiz",
    "test",
    "시험",
    "중간고사",
    "기말고사",
    "퀴즈",
    "출제",
)


def is_exam_signal(value: Any) -> bool:
    """Return whether a session-derived candidate is exam-related."""

    if isinstance(value, Mapping):
        kind = value.get("kind", value.get("type"))
        if isinstance(kind, str) and kind.casefold().replace("_", "") in {
            "examsignal",
            "examsignals",
        }:
            return True
        text = " ".join(
            str(value.get(key, ""))
            for key in ("content", "text", "topic", "description", "title")
        )
    else:
        text = str(value)
    folded = text.casefold()
    return any(term in folded for term in _EXAM_TERMS)


def classify_exam_signals(values: Iterable[Any] | Mapping[str, Any]) -> tuple[Any, ...]:
    """Extract exam-related candidates from a session result or signal list.

    The function is intentionally a classifier, not an authority decision: it
    never sets ``Scope Confirmed`` and never creates an Exam record.
    """

    if isinstance(values, Mapping):
        direct = values.get("exam_signals")
        if isinstance(direct, (list, tuple)):
            return tuple(direct)
        values = values.get("signals", values.get("items", ()))
    return tuple(value for value in values if is_exam_signal(value))


extract_exam_signals = classify_exam_signals


__all__ = ["classify_exam_signals", "extract_exam_signals", "is_exam_signal"]
