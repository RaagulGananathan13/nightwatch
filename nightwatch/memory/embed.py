"""A small lexical embedding: hashed word and word-pair features, weighted by rarity.

Incidents are mostly told apart by exact identifiers: error types (``KeyError``), flag names
(``error_rate``), alarm names, runbooks, "deploy" versus "capacity". Matching those words works
well, needs no model and no API key, and costs nothing (ADR 0014). ``embed`` turns text into a
sparse vector of hashed features. ``rank`` weights features by how rare they are among the
stored incidents (IDF), so words every incident shares, like "shoplite", count for little.

The vectors live in DynamoDB next to the incident they describe. A neural embedding could
replace this later; ``rank`` only needs vectors and a similarity.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter
from collections.abc import Sequence

DIMENSIONS = 4096
_WORD = re.compile(r"[a-z][a-z0-9_]{1,40}")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "after",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
        "which",
        "while",
        "not",
        "no",
        "than",
        "then",
        "there",
        "these",
        "those",
        "will",
    ]
)

Vector = dict[int, float]


def tokens(text: str) -> list[str]:
    """Lower-case words, keeping identifiers such as ``table_name`` whole."""
    return [word for word in _WORD.findall(text.lower()) if word not in _STOP]


def embed(text: str) -> Vector:
    """Sparse feature counts (1 + log of the count) for single words and adjacent pairs."""
    words = tokens(text)
    features = Counter(_bucket(word) for word in words)
    features.update(_bucket(f"{a} {b}") for a, b in itertools.pairwise(words))
    return {index: round(1.0 + math.log(count), 4) for index, count in features.items()}


def rank(query: Vector, documents: Sequence[Vector]) -> list[float]:
    """Cosine similarity of the query with each document, after IDF weighting."""
    total = len(documents)
    seen: Counter[int] = Counter()
    for document in documents:
        seen.update(document.keys())

    def weigh(vector: Vector) -> Vector:
        weighted = {
            index: value * (math.log((1 + total) / (1 + seen[index])) + 1.0)
            for index, value in vector.items()
        }
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        return {index: value / norm for index, value in weighted.items()}

    asked = weigh(query)
    return [
        round(sum(value * asked.get(index, 0.0) for index, value in weigh(document).items()), 4)
        for document in documents
    ]


def _bucket(feature: str) -> int:
    """A stable hash (Python's own ``hash`` changes between processes)."""
    digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % DIMENSIONS
