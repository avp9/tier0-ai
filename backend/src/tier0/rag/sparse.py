"""Keyword-style search that still understands related words (SPLADE), and the on-disk index.

A SPLADE model turns a text into a sparse vector: a weight for each of ~30,000 vocabulary terms,
including related terms the text never used ("hotel" also lights up "accommodation"). Matching a
question to an article is then a sum over the terms they share, so exact words, codes and names
count for much more than they do in a dense embedding.

The article vectors are computed once, when the index is built (slow: minutes for a large
knowledge base), and stored next to the Chroma index. Searching them needs no model call for the
articles, only one small one for the question.
"""

from __future__ import annotations

import json
import re
import shutil
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

SPARSE_DIR = "sparse"
SPARSE_SCHEMA_VERSION = 1
DEFAULT_SPLADE_MODEL = "prithivida/Splade_PP_en_v1"
ENCODE_BATCH = 16


class SparseIndexError(Exception):
    """The keyword index is missing, unreadable, or was built for a different index."""


@dataclass(frozen=True)
class SparseVector:
    indices: npt.NDArray[np.int32]
    values: npt.NDArray[np.float32]


class SparseEncoder(Protocol):
    @property
    def model_id(self) -> str: ...

    def encode_documents(self, texts: Sequence[str]) -> list[SparseVector]: ...

    def encode_query(self, text: str) -> SparseVector: ...


class SpladeEncoder:
    """SPLADE through ONNX Runtime (fastembed): the same on a Mac and on Linux, CPU only."""

    def __init__(
        self,
        model: str = DEFAULT_SPLADE_MODEL,
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
        return f"splade:{self._model_name}"

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(
                self._model_name, threads=self._threads, cache_dir=self._cache_dir
            )
        return self._model

    @staticmethod
    def _convert(embedding: Any) -> SparseVector:
        return SparseVector(
            np.asarray(embedding.indices, dtype=np.int32),
            np.asarray(embedding.values, dtype=np.float32),
        )

    def encode_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [self._convert(e) for e in self._load().embed(list(texts), batch_size=ENCODE_BATCH)]

    def encode_query(self, text: str) -> SparseVector:
        return self._convert(next(iter(self._load().query_embed(text))))


class FakeSparseEncoder:
    """Hashed words with their counts: deterministic, no model, for tests and offline demos."""

    VOCABULARY = 30522

    @property
    def model_id(self) -> str:
        return "fake-sparse"

    def _encode(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            term = zlib.crc32(word.encode()) % self.VOCABULARY
            counts[term] = counts.get(term, 0.0) + 1.0
        terms = sorted(counts)
        return SparseVector(
            np.array(terms, dtype=np.int32), np.array([counts[t] for t in terms], dtype=np.float32)
        )

    def encode_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [self._encode(t) for t in texts]

    def encode_query(self, text: str) -> SparseVector:
        return self._encode(text)


class SparseIndex:
    """Article vectors laid out by term, so a question only touches the articles sharing a term."""

    def __init__(
        self,
        ids: list[str],
        vectors: Sequence[SparseVector],
        *,
        model_id: str,
        index_hash: str,
    ) -> None:
        if len(ids) != len(vectors):
            raise ValueError("one vector per article is required")
        self.ids = ids
        self.model_id = model_id
        self.index_hash = index_hash
        sizes = np.array([len(v.indices) for v in vectors], dtype=np.int64)
        self._indptr = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
        self._indices = (
            np.concatenate([v.indices for v in vectors]) if len(vectors) else np.zeros(0, np.int32)
        ).astype(np.int32)
        self._values = (
            np.concatenate([v.values for v in vectors]) if len(vectors) else np.zeros(0, np.float32)
        ).astype(np.float32)
        owner = np.repeat(np.arange(len(ids), dtype=np.int32), sizes)
        order = np.argsort(self._indices, kind="stable")
        self._terms = self._indices[order]
        self._owners = owner[order]
        self._weights = self._values[order]

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: SparseVector, n: int) -> list[tuple[str, float]]:
        """The ``n`` articles whose vectors overlap the question's most, best first."""
        scores = np.zeros(len(self.ids), dtype=np.float32)
        for term, weight in zip(query.indices, query.values, strict=True):
            lo = np.searchsorted(self._terms, term, side="left")
            hi = np.searchsorted(self._terms, term, side="right")
            if hi > lo:
                scores[self._owners[lo:hi]] += weight * self._weights[lo:hi]
        n = min(n, len(self.ids))
        if n == 0:
            return []
        top = np.argpartition(-scores, n - 1)[:n]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [(self.ids[i], float(scores[i])) for i in top if scores[i] > 0]

    # --- on disk ----------------------------------------------------------------------------

    def save(self, index_dir: Path) -> None:
        """Write atomically: a half-written index is never seen."""
        target = index_dir / SPARSE_DIR
        staging = index_dir / f"{SPARSE_DIR}.building"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            np.savez(
                staging / "vectors.npz",
                indptr=self._indptr,
                indices=self._indices,
                values=self._values,
            )
            (staging / "meta.json").write_text(
                json.dumps(
                    {
                        "schema": SPARSE_SCHEMA_VERSION,
                        "model_id": self.model_id,
                        "index_hash": self.index_hash,
                        "ids": self.ids,
                    }
                ),
                encoding="utf-8",
            )
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @classmethod
    def load(cls, index_dir: Path) -> SparseIndex:
        folder = index_dir / SPARSE_DIR
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            with np.load(folder / "vectors.npz", allow_pickle=False) as arrays:
                indptr, indices, values = arrays["indptr"], arrays["indices"], arrays["values"]
        except (OSError, ValueError, KeyError) as error:
            raise SparseIndexError(f"{folder}: no keyword index found") from error
        if meta.get("schema") != SPARSE_SCHEMA_VERSION:
            raise SparseIndexError(f"{folder}: unsupported keyword index version")
        vectors = [
            SparseVector(indices[a:b], values[a:b])
            for a, b in zip(indptr[:-1], indptr[1:], strict=True)
        ]
        return cls(meta["ids"], vectors, model_id=meta["model_id"], index_hash=meta["index_hash"])


def read_sparse_meta(index_dir: Path) -> tuple[str, str] | None:
    """(model_id, index_hash) of the stored keyword index, or None if there is none."""
    try:
        meta = json.loads((index_dir / SPARSE_DIR / "meta.json").read_text(encoding="utf-8"))
        if meta.get("schema") != SPARSE_SCHEMA_VERSION:
            return None
        return str(meta["model_id"]), str(meta["index_hash"])
    except (OSError, ValueError, KeyError):
        return None
