"""Bounded deterministic lexical matching used by the Phase 2 slice."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LexicalMatch:
    value: Any
    score: int
    matched_terms: tuple[str, ...]


def tokenize(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(re.findall(r"[0-9A-Za-z가-힣]+", value.casefold()))


def lexical_score(query: str, text: str) -> int:
    wanted = set(tokenize(query))
    available = set(tokenize(text))
    return sum(1 for term in wanted if term in available)


def bounded_match(
    query: str,
    candidates: list[Any],
    *,
    text_getter=lambda value: str(value),
    limit: int = 20,
) -> list[LexicalMatch]:
    wanted = tokenize(query)
    results: list[LexicalMatch] = []
    for candidate in candidates:
        candidate_text = text_getter(candidate)
        matched = tuple(term for term in wanted if term in tokenize(candidate_text))
        if matched:
            results.append(LexicalMatch(candidate, len(set(matched)), matched))
    results.sort(key=lambda item: (-item.score, str(item.value)))
    return results[:limit]


__all__ = ["LexicalMatch", "bounded_match", "lexical_score", "tokenize"]
