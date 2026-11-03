"""Human-readable gate summaries (spec §6.5): pure functions of (action, params, view).

Params carry only ids and enum values; display names come from the database
(``view``), are NFC-normalized, stripped of control characters and line breaks,
and cut to 80 characters. The dialog always shows ``target_id`` next to them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

MAX_LABEL = 80
# exactly the activities.submission_status values (R3 plan §6.5; the R2 table had "LATE" and no EXCUSED)
SUBMISSION_LABELS = {
    "NOT_SUBMITTED": "제출 안 함", "SUBMITTED": "제출함", "LATE_SUBMITTED": "늦게 제출함", "EXCUSED": "제출 면제",
}
ARTIFACT_LABELS = {"summary": "요약", "question": "문제", "answer": "답안", "grading": "채점", "classification_hint": "분류 제안"}


def label(text: object) -> str:
    value = unicodedata.normalize("NFC", str(text or ""))
    value = re.sub(r"[\r\n\t  ]", " ", value)
    value = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
    # quotes/brackets would let a display name imitate the sentence around it (review of PR #50)
    value = re.sub(r"[\"'“”‘’()\[\]{}<>「」『』]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= MAX_LABEL else value[: MAX_LABEL - 1] + "…"


def due_label(due_at: object) -> str:
    """``마감 YYYY-MM-DD HH:MM`` in the timestamp's own offset, or ``마감 없음``."""

    text = str(due_at or "")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", text)
    return f"마감 {match.group(1)} {match.group(2)}" if match else "마감 없음"


def summarize(action: str, params: Mapping[str, Any], view: Mapping[str, Any]) -> str:
    if action == "VERIFY_MATERIAL":
        return f"자료 {params['material_id']} \"{label(view.get('title'))}\"({label(view.get('course'))})를 '검증됨'으로 표시합니다."
    if action == "CONFIRM_EXAM_SCOPE":
        return (f"{label(view.get('course'))} {label(view.get('exam_title'))}({params['exam_id']})의 범위를 "
                f"자료 {len(params['material_ids'])}개로 확정합니다.")
    if action == "SET_SUBMISSION_STATUS":
        status = SUBMISSION_LABELS[str(params["status"])]
        return (f"과제 {params['activity_id']} \"{label(view.get('activity_title'))}\"({due_label(view.get('due_at'))})의 "
                f"제출 상태를 '{status}'으로 바꿉니다.")
    if action == "ADOPT_AI_ARTIFACT":
        kind = ARTIFACT_LABELS.get(str(view.get("kind")), "산출물")
        return f"AI {kind} {params['artifact_id']}을(를) 내 노트로 채택합니다."
    raise ValueError(f"unknown action {action!r}")
