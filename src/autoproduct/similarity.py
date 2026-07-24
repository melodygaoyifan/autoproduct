"""Dependency-free lexical similarity — the embedding-free matcher.

Skill/block matching wanted embeddings, but the system must work with
zero extra providers configured (the founder has ONE key, maybe none for
embeddings). TF-IDF cosine over unicode word tokens plus CJK unigrams and
bigrams covers the actual need: paraphrase-tolerant ranking of a query
against a small catalog, in either 中文 or English. If a real embedding
provider lands later it slots behind `rank()` without touching callers.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"[a-z0-9_]+")


def _is_cjk(ch: str) -> bool:
    return "CJK" in unicodedata.name(ch, "")


def tokenize(text: str) -> list[str]:
    """Latin words lowercased; CJK runs as unigrams AND bigrams (bigrams
    carry meaning, unigrams catch one-char overlap like 付 in 付款/付钱)."""
    tokens: list[str] = []
    lowered = text.lower()
    tokens += _WORD.findall(lowered)
    run: list[str] = []
    for ch in lowered + " ":
        if _is_cjk(ch):
            run.append(ch)
            continue
        if run:
            tokens += run
            tokens += ["".join(run[i : i + 2]) for i in range(len(run) - 1)]
            run = []
    return tokens


def rank(query: str, docs: list[str]) -> list[tuple[int, float]]:
    """Cosine-ranked (index, score) pairs, best first, zero-score dropped."""
    doc_tokens = [Counter(tokenize(d)) for d in docs]
    q_tokens = Counter(tokenize(query))
    n = len(docs)
    df = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))

    def idf(tok: str) -> float:
        return math.log((1 + n) / (1 + df[tok])) + 1.0

    def vec(tokens: Counter) -> dict[str, float]:
        return {t: c * idf(t) for t, c in tokens.items()}

    qv = vec(q_tokens)
    qnorm = math.sqrt(sum(w * w for w in qv.values())) or 1.0
    scored = []
    for i, tokens in enumerate(doc_tokens):
        dv = vec(tokens)
        dnorm = math.sqrt(sum(w * w for w in dv.values())) or 1.0
        dot = sum(w * dv.get(t, 0.0) for t, w in qv.items())
        score = dot / (qnorm * dnorm)
        if score > 0:
            scored.append((i, round(score, 4)))
    return sorted(scored, key=lambda p: -p[1])
