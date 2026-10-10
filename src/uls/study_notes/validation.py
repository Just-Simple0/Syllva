"""Structural draft validation only (plan section 8).

Deterministic checks: SOURCE/AI/USER provenance structure; required
sections (goals, key concepts, worked examples, misconceptions, exercises
with folded solutions); referenced locators belong to the prepared
manifest and are syntactically valid; required solution-fold structure;
manifest/generation freshness (checked by the caller, not here).

This module NEVER claims to detect fabrication, semantic entailment,
factual correctness, pedagogical quality, or hallucination -- only
structural presence. 'Fabricated proof' is explicitly not a possible
outcome here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from uls.domain.errors import LocatorParseError
from uls.domain.models import parse_locator

_REQUIRED_MARKERS = (
    "SOURCE",
    "AI",
)

_REQUIRED_SECTIONS = (
    "학습 목표",
    "핵심 개념",
    "예제",
    "오개념",
    "연습문제",
)

_FOLD_MARKER = re.compile(r"<details>.*?</details>", re.DOTALL)
_LOCATOR_CANDIDATE_PATTERN = re.compile(
    r"\b[A-Z0-9]+-[A-Z][0-9]+:[^\s<>()\[\]{}]+"
)


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: str | None = None
    missing_sections: tuple[str, ...] = field(default_factory=tuple)
    invalid_locators: tuple[str, ...] = field(default_factory=tuple)


def _extract_locators(draft_text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    malformed: list[str] = []
    for match in _LOCATOR_CANDIDATE_PATTERN.finditer(draft_text):
        candidate = match.group(0).rstrip(".,;!?")
        try:
            parse_locator(candidate)
        except LocatorParseError:
            malformed.append(candidate)
        else:
            valid.append(candidate)
    return valid, malformed


def validate_draft_structure(
    draft_text: str, *, manifest_locators: Sequence[str],
) -> ValidationResult:
    """Deterministic structural checks only. See module docstring for scope.

    manifest_locators must be the exact locator set the prepared evidence
    manifest authorized -- a referenced locator outside that set fails"""
    if not isinstance(draft_text, str) or not draft_text.strip():
        return ValidationResult(accepted=False, reason="draft is empty")

    missing_markers = [m for m in _REQUIRED_MARKERS if m not in draft_text]
    if missing_markers:
        return ValidationResult(
            accepted=False,
            reason=f"missing provenance markers: {', '.join(missing_markers)}",
        )

    missing_sections = tuple(s for s in _REQUIRED_SECTIONS if s not in draft_text)
    if missing_sections:
        return ValidationResult(
            accepted=False, reason="missing required sections",
            missing_sections=missing_sections,
        )

    if not _FOLD_MARKER.search(draft_text):
        return ValidationResult(
            accepted=False, reason="exercises must use a folded <details> solution structure",
        )

    allowed = set(manifest_locators)
    found, malformed = _extract_locators(draft_text)
    invalid = tuple(sorted({*malformed, *(loc for loc in found if loc not in allowed)}))
    if invalid:
        return ValidationResult(
            accepted=False, reason="draft references locators outside the prepared evidence manifest",
            invalid_locators=invalid,
        )

    if not found:
        return ValidationResult(
            accepted=False, reason="no evidence-grounded locator was found for any worked example",
        )

    return ValidationResult(accepted=True)


__all__ = ["ValidationResult", "validate_draft_structure"]
