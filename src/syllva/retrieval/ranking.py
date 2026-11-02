"""Query-mode chunk scoring (implementation spec §8.8.2).

Score = BM25-like weighted any-term match + adjacency bonus. A chunk does NOT
need every query term (the legacy ``select_chunks`` all-terms rule is not used).
Tokens come from ``retrieval.lexical.tokenize`` (NFC + casefold first) and
are matched on ONE form: the token with a trailing Korean particle removed
(``TCP는`` -> ``tcp``; the stem keeps at least two syllables, as in the
resolver, so ``정도`` is never read as ``정`` + particle). Single Latin letters and
single-syllable Korean function words (``이``, ``수``, ``것`` ...) are dropped;
other single syllables (``힙``, ``큐``) and digits are kept. A single-syllable
QUERY term also matches a two-syllable document token made of that syllable plus
a particle (query ``큐`` matches ``큐는``); the reverse is not done, because a
two-syllable word in a query (``정의``) is a word, not a stem plus particle.
IDF is computed over the candidate chunks of
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
from syllva.retrieval.natural import PARTICLES, strip_particle

_K1 = 1.2
_B = 0.75
_ADJACENCY_BONUS = 0.5


_FUNCTION_SYLLABLES = frozenset("이그저것수등및더또좀잘안못때데중")


def _keep(token: str) -> bool:
    if len(token) > 1 or token.isdigit():
        return True
    return "가" <= token <= "힣" and token not in _FUNCTION_SYLLABLES


def _particle_stem(token: str) -> str | None:
    """``큐는`` -> ``큐``: the syllable before a one-syllable particle in a 2-syllable token."""

    if len(token) == 2 and "가" <= token[0] <= "힣" and token[1:] in PARTICLES:
        return token[0]
    return None


def terms(text: str) -> list[str]:
    out: list[str] = []
    for token in tokenize(unicodedata.normalize("NFC", text)):
        base = strip_particle(token)
        if _keep(base):
            out.append(base)
    return out


def score_texts(query: str, texts: Sequence[str]) -> list[float]:
    """Score each text against ``query``; 0.0 means no query term occurs in it."""

    wanted = list(dict.fromkeys(terms(query)))
    if not wanted or not texts:
        return [0.0] * len(texts)
    docs = [terms(text) for text in texts]
    counts: list[Counter[str]] = []
    for doc in docs:
        count = Counter(doc)
        for token in doc:  # query-side single syllables also count "syllable + particle" tokens
            stem = _particle_stem(token)
            if stem is not None and stem in wanted:
                count[stem] += 1
        counts.append(count)
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
