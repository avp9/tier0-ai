from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

OPENAI_BATCH_SIZE = 256
FAKE_DIMENSIONS = 256
_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic bag-of-words embedder: similar wording gives similar vectors.

    Needs no network or API key, so tests and CI can build and query a real index.
    """

    model_id = f"fake-bow-{FAKE_DIMENSIONS}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * FAKE_DIMENSIONS
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % FAKE_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, max_retries=5, timeout=60.0)
        self._model = model

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), OPENAI_BATCH_SIZE):
            batch = texts[start : start + OPENAI_BATCH_SIZE]
            response = self._client.embeddings.create(model=self._model, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors
