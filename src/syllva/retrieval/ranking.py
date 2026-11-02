"""Query-mode chunk scoring (implementation spec §8.8.2).

Score = BM25-like weighted any-term match + adjacency bonus. A chunk does NOT
need every query term (the legacy ``select_chunks`` all-terms rule is not used).
Tokens come from ``retrieval.lexical.tokenize`` (NFC + casefold first). Each
token is indexed in its surface form and with a trailing Korean particle
removed (``TCP는`` and ``큐의`` match ``TCP`` and ``큐``); adjacency uses the
particle-free form. Single Latin letters are dropped as noise; single Hangul
syllables (``힙``, ``큐``) and digits are kept. IDF is computed over the candidate chunks of
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
from syllva.retrieval.natural import _PARTICLES, strip_particle

_K1 = 1.2
_B = 0.75
_ADJACENCY_BONUS = 0.5


def _keep(token: str) -> bool:
    return len(token) > 1 or token.isdigit() or "가" <= token <= "힣"


def _base(token: str) -> str:
    stripped = strip_particle(token)
    if stripped == token:  # strip_particle keeps a 2-syllable stem minimum; ranking also tries 1 syllable
        for particle in _PARTICLES:
            if len(token) == len(particle) + 1 and token.endswith(particle) and "가" <= token[0] <= "힣":
                return token[0]
    return stripped


def _forms(text: str) -> list[tuple[str, str]]:
    """(particle-free form, surface form) per kept token, in text order."""

    out: list[tuple[str, str]] = []
    for token in tokenize(unicodedata.normalize("NFC", text)):
        base = _base(token)
        if _keep(base):
            out.append((base, token))
    return out


def terms(text: str) -> list[str]:
    return [base for base, _ in _forms(text)]


def score_texts(query: str, texts: Sequence[str]) -> list[float]:
    """Score each text against ``query``; 0.0 means no query term occurs in it."""

    query_forms = _forms(query)
    wanted = list(dict.fromkeys(form for pair in query_forms for form in pair))
    if not wanted or not texts:
        return [0.0] * len(texts)
    doc_forms = [_forms(text) for text in texts]
    docs = [[base for base, _ in forms] for forms in doc_forms]
    counts = [Counter(f for pair in forms for f in set(pair)) for forms in doc_forms]
    n = len(docs)
    average = sum(len(doc) for doc in docs) / n or 1.0
    idf = {}
    for term in wanted:
        df = sum(1 for c in counts if term in c)
        idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    pairs = set(pairwise(base for base, _ in query_forms))
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
