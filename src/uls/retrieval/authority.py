"""Source authority policy per retrieval intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from uls.domain.enums import RetrievalIntent, SourceAuthority, authority_rank


SOURCE_CLASS_TO_AUTHORITY: dict[str, SourceAuthority] = {
    "professor_material": SourceAuthority.PROFESSOR_MATERIAL,
    "material": SourceAuthority.PROFESSOR_MATERIAL,
    "professor_transcript": SourceAuthority.PROFESSOR_TRANSCRIPT,
    "transcript": SourceAuthority.PROFESSOR_TRANSCRIPT,
    "official": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "official_activity": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "official_exam": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "official_activity_or_exam": SourceAuthority.OFFICIAL_ACTIVITY_OR_EXAM,
    "professor_source": SourceAuthority.PROFESSOR_MATERIAL,
    "user": SourceAuthority.USER_SOURCE,
    "user_source": SourceAuthority.USER_SOURCE,
    "supplemental": SourceAuthority.SUPPLEMENTAL_REFERENCE,
    "supplemental_reference": SourceAuthority.SUPPLEMENTAL_REFERENCE,
    "ai": SourceAuthority.AI_ENRICHMENT,
    "ai_enrichment": SourceAuthority.AI_ENRICHMENT,
    "external": SourceAuthority.EXTERNAL,
}


@dataclass(frozen=True)
class AuthorityPolicy:
    """Allowed source classes plus deterministic metadata mapping."""

    intent: RetrievalIntent | str
    allowed_source_classes: frozenset[str]
    include_ai_factual: bool = False
    include_user: bool = False
    include_supplemental: bool = False

    def allows(self, source_class: str) -> bool:
        return source_class.casefold() in self.allowed_source_classes

    def authority_for(self, source_class: str) -> SourceAuthority:
        return authority_for(source_class)


def authority_for(source_class: str) -> SourceAuthority:
    """Map a source class to its authority; unknown classes fail closed."""

    if not isinstance(source_class, str):
        raise TypeError("source_class must be a string")
    key = source_class.casefold()
    if key not in SOURCE_CLASS_TO_AUTHORITY:
        raise ValueError(f"unknown source class: {source_class!r}")
    return SOURCE_CLASS_TO_AUTHORITY[key]


def policy_for_session(
    *,
    include_provisional: bool = False,
    include_user: bool = True,
) -> AuthorityPolicy:
    allowed = {"professor_transcript", "professor_material"}
    if include_provisional:
        # Provisional affects relation verification, not authority rank.  The
        # same professor-material authority is annotated on the item.
        allowed.add("professor_material")
    if include_user:
        allowed.add("user_source")
    return AuthorityPolicy(
        RetrievalIntent.SESSION,
        frozenset(allowed),
        include_user=include_user,
    )


def policy_for_concept(*, include_textbook: bool = False) -> AuthorityPolicy:
    allowed = {"professor_material", "professor_transcript"}
    if include_textbook:
        allowed.add("supplemental_reference")
    return AuthorityPolicy(
        RetrievalIntent.CONCEPT,
        frozenset(allowed),
        include_supplemental=include_textbook,
    )


def policy_for_exam(*, scope_confirmed: bool, include_provisional: bool = False) -> AuthorityPolicy:
    allowed = {"professor_material", "professor_transcript", "official_exam"}
    return AuthorityPolicy(
        RetrievalIntent.EXAM,
        frozenset(allowed),
    )


def policy_for_activity() -> AuthorityPolicy:
    return AuthorityPolicy(
        RetrievalIntent.ACTIVITY,
        frozenset({"official_activity", "professor_material", "professor_transcript"}),
    )


def policy_for_user_note() -> AuthorityPolicy:
    return AuthorityPolicy(
        RetrievalIntent.USER_NOTE,
        frozenset({"user_source", "professor_material", "professor_transcript"}),
        include_user=True,
    )


def policy_for_verify() -> AuthorityPolicy:
    return AuthorityPolicy(
        RetrievalIntent.VERIFY,
        frozenset({"professor_material", "professor_transcript", "official_activity", "official_exam"}),
    )


def sort_by_authority(items: Iterable[object], source_class_getter) -> list[object]:
    """Optional presentation helper; fetch order remains the engine's order."""

    return sorted(items, key=lambda item: authority_rank(authority_for(source_class_getter(item))))


__all__ = [
    "AuthorityPolicy",
    "SOURCE_CLASS_TO_AUTHORITY",
    "authority_for",
    "policy_for_activity",
    "policy_for_concept",
    "policy_for_exam",
    "policy_for_session",
    "policy_for_user_note",
    "policy_for_verify",
    "sort_by_authority",
]
