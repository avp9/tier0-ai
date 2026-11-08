"""Re-reading the best few matches with a model that sees the question and the article together.

A cross-encoder is slower than the dense or SPLADE search (it reads each candidate in full) but
much better at telling a truly relevant article from a merely similar one, so it only looks at the
~20 candidates the faster searches produced. Its scores are also far better at separating
questions the knowledge base can answer from ones it cannot.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

DEFAULT_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


class Reranker(Protocol):
    @property
    def model_id(self) -> str: ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """How well each passage answers the question, 0 to 1 (higher is better)."""
        ...


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


class CrossEncoderReranker:
    """A small cross-encoder through ONNX Runtime (fastembed): CPU only, same everywhere."""

    def __init__(
        self,
        model: str = DEFAULT_RERANK_MODEL,
        *,
        threads: int | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._model_name = model
        self._threads = threads
        self._cache_dir = cache_dir
        self._model: Any = None

    @property
    def model_id(self) -> str:
        return f"cross-encoder:{self._model_name}"

    def _load(self) -> Any:
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(
                self._model_name, threads=self._threads, cache_dir=self._cache_dir
            )
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        # The model gives a raw score (roughly -12 to +11); a sigmoid puts it on 0 to 1.
        return [sigmoid(float(s)) for s in self._load().rerank(query, list(passages))]


class FakeReranker:
    """The share of the question's words found in the passage: deterministic, for tests."""

    @property
    def model_id(self) -> str:
        return "fake-rerank"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        wanted = set(re.findall(r"[a-z0-9]+", query.lower()))
        if not wanted:
            return [0.0 for _ in passages]
        return [
            len(wanted & set(re.findall(r"[a-z0-9]+", p.lower()))) / len(wanted) for p in passages
        ]
