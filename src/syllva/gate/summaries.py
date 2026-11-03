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
SUBMISSION_LABELS = {"SUBMITTED": "제출함", "NOT_SUBMITTED": "제출 안 함", "LATE": "늦게 제출함"}
ARTIFACT_LABELS = {"summary": "요약", "question": "문제", "answer": "답안", "grading": "채점", "classification_hint": "분류 제안"}


def label(text: object) -> str:
    value = unicodedata.normalize("NFC", str(text or ""))
    value = re.sub(r"[\r\n\t  ]", " ", value)
    value = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= MAX_LABEL else value[: MAX_LABEL - 1] + "…"


def summarize(action: str, params: Mapping[str, Any], view: Mapping[str, Any]) -> str:
    if action == "VERIFY_MATERIAL":
        return f"자료 {params['material_id']} \"{label(view.get('title'))}\"({label(view.get('course'))})를 '검증됨'으로 표시합니다."
    if action == "CONFIRM_EXAM_SCOPE":
        return (f"{label(view.get('course'))} {label(view.get('exam_title'))}({params['exam_id']})의 범위를 "
                f"자료 {len(params['material_ids'])}개로 확정합니다.")
    if action == "SET_SUBMISSION_STATUS":
        status = SUBMISSION_LABELS[str(params["status"])]
        return f"과제 {params['activity_id']} \"{label(view.get('activity_title'))}\"의 제출 상태를 '{status}'으로 바꿉니다."
    if action == "ADOPT_AI_ARTIFACT":
        kind = ARTIFACT_LABELS.get(str(view.get("kind")), "산출물")
        return f"AI {kind} {params['artifact_id']}을(를) 내 노트로 채택합니다."
    raise ValueError(f"unknown action {action!r}")
