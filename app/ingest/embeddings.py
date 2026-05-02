"""
gitgap — Embedding service (Phase 2: character n-gram HashingVectorizer)

Mirrors the eaiou embedding service — same backend, same vector format,
so eaiou Wheelhouse matching can compare gap vectors against author profile
vectors using the same distance metric.

Phase 3 upgrade: swap embed_text() for a neural encoder.
"""

import json
import math

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

_N_FEATURES = 512
_NGRAM_RANGE = (3, 5)

_vectorizer = HashingVectorizer(
    analyzer="char_wb",
    ngram_range=_NGRAM_RANGE,
    n_features=_N_FEATURES,
    norm="l2",
    alternate_sign=False,
    dtype=np.float32,
)


def embed_text(text: str) -> list[float]:
    """Encode text to a dense 512-dim L2-normalised vector."""
    if not text or not text.strip():
        return [0.0] * _N_FEATURES
    sparse = _vectorizer.transform([text])
    return sparse.toarray()[0].tolist()


def cosine_distance(v1: list[float], v2: list[float]) -> float:
    """
    Cosine distance between two L2-normalised dense vectors.
    Handles zero vectors (empty content) — both zero → 0.0, one zero → 1.0.
    """
    mag1 = math.sqrt(sum(x * x for x in v1))
    mag2 = math.sqrt(sum(x * x for x in v2))
    if mag1 == 0.0 and mag2 == 0.0:
        return 0.0
    if mag1 == 0.0 or mag2 == 0.0:
        return 1.0
    dot = sum(a * b for a, b in zip(v1, v2))
    return round(max(0.0, min(1.0, 1.0 - dot / (mag1 * mag2))), 4)


def vector_to_json(v: list[float]) -> str:
    return json.dumps(v)


def json_to_vector(s: str) -> list[float]:
    return json.loads(s) if s else [0.0] * _N_FEATURES
