"""Query-mode chunk scoring (implementation spec §8.8.2).

Score = BM25-like weighted any-term match + adjacency bonus. A chunk does NOT
need every query term (the legacy ``select_chunks`` all-terms rule is not used).
Tokens come from ``retrieval.lexical.tokenize`` (NFC + casefold first), with
Korean particles stripped (``TCP는`` matches ``TCP``) and single-character
non-digit tokens dropped as noise. IDF is computed over the candidate chunks of
this request only, so a term present in every chunk carries little weight.

Deterministic: no model, no randomness; ties are broken by the caller's order.
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise

from syllva.retrieval.lexical import tokenize
from syllva.retrieval.natural import strip_particle

_K1 = 1.2
_B = 0.75
_ADJACENCY_BONUS = 0.5


def terms(text: str) -> list[str]:
    out: list[str] = []
    for token in tokenize(unicodedata.normalize("NFC", text)):
        token = strip_particle(token)
        if len(token) > 1 or token.isdigit():
            out.append(token)
    return out


def score_texts(query: str, texts: Sequence[str]) -> list[float]:
    """Score each text against ``query``; 0.0 means no query term occurs in it."""

    wanted = list(dict.fromkeys(terms(query)))
    if not wanted or not texts:
        return [0.0] * len(texts)
    docs = [terms(text) for text in texts]
    counts = [Counter(doc) for doc in docs]
    n = len(docs)
    average = sum(len(doc) for doc in docs) / n or 1.0
    idf = {}
    for term in wanted:
        df = sum(1 for c in counts if term in c)
        idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    pairs = set(pairwise(wanted))
    scores: list[float] = []
    for doc, count in zip(docs, counts, strict=True):
        score = 0.0
        for term in wanted:
            tf = count.get(term, 0)
            if tf:
                score += idf[term] * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * len(doc) / average))
        if score and pairs:
            adjacent = {pair for pair in pairwise(doc) if pair in pairs}
            score += _ADJACENCY_BONUS * len(adjacent)
        scores.append(score)
    return scores


__all__ = ["score_texts", "terms"]
