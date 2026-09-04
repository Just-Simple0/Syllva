"""Deterministic source-kind classification for the ingestion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath


class SourceKind(str, Enum):
    TRANSCRIPT = "transcript"
    MATERIAL = "material"
    ACTIVITY = "activity"
    EXAM = "exam"
    USER_ANNOTATION = "user_annotation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceClassification:
    kind: SourceKind
    confidence: float
    reason: str

    @property
    def source_kind(self) -> str:
        return self.kind.value


def classify_source_detailed(
    filename: str | None = None,
    *,
    mime_type: str | None = None,
    content: str | None = None,
) -> SourceClassification:
    """Classify a source using filename/path and explicit marker evidence.

    Classification is routing metadata only.  It never edits source content
    and does not use an LLM.
    """

    name = PurePath(filename or "").name.casefold()
    stem = PurePath(name).stem
    if any(token in name for token in ("transcript", "transcription", "alt", "자막", "강의록")):
        return SourceClassification(SourceKind.TRANSCRIPT, 1.0, "transcript filename marker")
    if any(token in name for token in ("recording", "audio", "lecture", "session", "강의")):
        if mime_type and mime_type.casefold().startswith(("audio/", "video/")):
            return SourceClassification(SourceKind.TRANSCRIPT, 0.9, "recording/audio source")
    if any(token in name for token in ("exam", "midterm", "final", "시험")):
        return SourceClassification(SourceKind.EXAM, 0.95, "exam filename marker")
    if any(token in name for token in ("assignment", "homework", "hw", "activity", "과제")):
        return SourceClassification(SourceKind.ACTIVITY, 0.95, "activity filename marker")
    if any(token in name for token in ("goodnotes", "annotation", "annotated", "필기")):
        return SourceClassification(SourceKind.USER_ANNOTATION, 0.95, "annotation filename marker")
    if mime_type and mime_type.casefold() in {"audio/mpeg", "audio/mp4", "video/mp4", "text/vtt"}:
        return SourceClassification(SourceKind.TRANSCRIPT, 0.75, "transcript-compatible media")
    if content:
        # The marker check is intentionally narrow; arbitrary bracketed prose
        # must not turn an unrelated source into a transcript.
        import re

        if re.search(r"\[\d{1,2}:\d{2}:\d{2}\]", content):
            return SourceClassification(SourceKind.TRANSCRIPT, 0.8, "timestamp marker")
    if stem:
        return SourceClassification(SourceKind.MATERIAL, 0.5, "default academic file")
    return SourceClassification(SourceKind.UNKNOWN, 0.0, "no deterministic source marker")


def classify_source(
    filename: str | None = None,
    *,
    mime_type: str | None = None,
    content: str | None = None,
) -> str:
    """Return the stable lower-case source-kind value."""

    return classify_source_detailed(filename, mime_type=mime_type, content=content).kind.value


classify = classify_source


__all__ = ["SourceClassification", "SourceKind", "classify", "classify_source", "classify_source_detailed"]
