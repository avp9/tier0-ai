from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from chromadb.api.models.Collection import Collection

from tier0.observability.tracing import note, traced
from tier0.rag.embeddings import Embedder
from tier0.rag.indexer import open_collection
from tier0.rag.rerank import Reranker
from tier0.rag.sparse import SparseEncoder, SparseIndex, SparseIndexError

log = logging.getLogger(__name__)

CANDIDATES = 20  # how many matches each search contributes, and how many the reranker reads
FUSION_K = 60  # reciprocal rank fusion: a smaller number favours the very top of each list


class IndexMismatchError(Exception):
    """The index was built with a different embedding model than the one querying it."""


@dataclass(frozen=True)
class Hit:
    document_id: str
    title: str
    category: str
    source: str
    content: str
    # 0 to 1, higher is better. Cosine similarity for a plain search; the reranker's score (its
    # opinion of how well the article answers the question) for a hybrid one.
    score: float

    def for_trace(self) -> dict[str, Any]:
        """How a match shows in a trace: the text (cut short) and what it is."""
        return {
            "page_content": self.content[:600],
            "metadata": {
                "document_id": self.document_id,
                "title": self.title,
                "category": self.category,
                "source": self.source,
                "score": round(self.score, 3),
            },
        }


@dataclass(frozen=True)
class HybridSearch:
    """Keyword (SPLADE) search to complement the dense one, and a reranker to settle the order."""

    index: SparseIndex
    encoder: SparseEncoder
    reranker: Reranker


def reciprocal_rank_fusion(*rankings: Sequence[str], k: int = FUSION_K) -> list[str]:
    """Merge best-first lists of ids: an id near the top of several lists comes out on top."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores, key=lambda item: (-scores[item], item))


class Retriever:
    def __init__(
        self, collection: Collection, embedder: Embedder, hybrid: HybridSearch | None = None
    ) -> None:
        built_with = (collection.metadata or {}).get("embedding_model")
        if built_with != embedder.model_id:
            raise IndexMismatchError(
                f"index was built with {built_with!r} but queries use {embedder.model_id!r}"
            )
        if hybrid is not None:
            stored = (collection.metadata or {}).get("content_hash")
            if hybrid.index.index_hash != stored:
                raise SparseIndexError("the keyword index was built for a different index")
        self._collection = collection
        self._embedder = embedder
        self._hybrid = hybrid

    @classmethod
    def open(
        cls, index_dir: Path, embedder: Embedder, hybrid: HybridSearch | None = None
    ) -> Retriever:
        return cls(open_collection(index_dir), embedder, hybrid)

    @property
    def reranks(self) -> bool:
        """True when scores are the reranker's (so a different threshold applies)."""
        return self._hybrid is not None

    @property
    def fingerprint(self) -> str:
        """Identifies exactly what this retriever searches: the index contents and the model."""
        stored = (self._collection.metadata or {}).get("content_hash", "")
        base = f"{stored}|{self._embedder.model_id}"
        if self._hybrid is None:
            return base
        return f"{base}|{self._hybrid.encoder.model_id}|{self._hybrid.reranker.model_id}"

    def embed(self, query: str) -> list[float]:
        """The embedding of a question, so a caller that needs it can search with it too."""
        return list(self._embedder.embed([query])[0])

    @traced(
        run_type="retriever",
        name="knowledge_base_search",
        process_inputs=lambda inputs: {"query": inputs.get("query"), "k": inputs.get("k")},
        process_outputs=lambda hits: {"documents": [hit.for_trace() for hit in hits]},
    )
    def search(self, query: str, k: int = 5, *, vector: Sequence[float] | None = None) -> list[Hit]:
        """The best matches. Pass ``vector`` (from ``embed``) to avoid embedding the query twice."""
        if k < 1:
            raise ValueError("k must be at least 1")
        if vector is None:
            vector = self._embedder.embed([query])[0]
        if self._hybrid is None:
            return self._dense(vector, k)
        try:
            hits = self._hybrid_search(query, vector, self._hybrid)
        except Exception:  # the better search failing must not stop questions being answered
            log.warning("hybrid search failed; using the dense search alone", exc_info=True)
            note(search_mode="dense (fallback)")
            return self._dense(vector, k)
        note(search_mode="hybrid_rerank")
        return hits[:k]

    def search_stages(self, query: str, k: int = 5) -> dict[str, list[Hit]]:
        """The same question answered by each search variant, for comparing them side by side.

        dense; splade (keywords alone); fused (dense and splade merged, no reranker); dense_rerank
        (dense candidates re-read); full (fused candidates re-read: what hybrid_rerank serves).
        Only for evaluation: a hybrid retriever is required.
        """
        hybrid = self._hybrid
        if hybrid is None:
            raise ValueError("comparing search variants needs SEARCH_MODE=hybrid_rerank")
        vector = self._embedder.embed([query])[0]
        dense = self._dense(vector, CANDIDATES)
        keyword = hybrid.index.search(hybrid.encoder.encode_query(query), CANDIDATES)
        known = {h.document_id: h for h in dense}
        keyword_hits = self._fetch([i for i, _ in keyword], known)
        keyword_hits = [
            replace(h, score=dict(keyword).get(h.document_id, 0.0)) for h in keyword_hits
        ]
        fused_ids = reciprocal_rank_fusion([h.document_id for h in dense], [i for i, _ in keyword])
        fused = self._fetch(fused_ids[:CANDIDATES], known)

        def rerank(hits: list[Hit]) -> list[Hit]:
            scores = hybrid.reranker.score(query, [h.content for h in hits])
            ranked = sorted(zip(hits, scores, strict=True), key=lambda pair: -pair[1])
            return [replace(h, score=score) for h, score in ranked]

        return {
            "dense": dense[:k],
            "splade": keyword_hits[:k],
            "dense+splade": fused[:k],
            "dense+rerank": rerank(dense)[:k],
            "dense+splade+rerank": rerank(fused)[:k],
        }

    def _dense(self, vector: Sequence[float], n: int) -> list[Hit]:
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        documents = (result["documents"] or [[]])[0]
        metadatas = (result["metadatas"] or [[]])[0]
        distances = (result["distances"] or [[]])[0]
        return [
            Hit(
                document_id=str(meta["document_id"]),
                title=str(meta["title"]),
                category=str(meta["category"]),
                source=str(meta["source"]),
                content=document,
                score=1.0 - distance,
            )
            for document, meta, distance in zip(documents, metadatas, distances, strict=True)
        ]

    def _hybrid_search(
        self, query: str, vector: Sequence[float], hybrid: HybridSearch
    ) -> list[Hit]:
        dense = self._dense(vector, CANDIDATES)
        keyword = hybrid.index.search(hybrid.encoder.encode_query(query), CANDIDATES)
        order = reciprocal_rank_fusion([h.document_id for h in dense], [i for i, _ in keyword])
        known = {h.document_id: h for h in dense}
        candidates = self._fetch(order[:CANDIDATES], known)
        if not candidates:
            return []
        scores = hybrid.reranker.score(query, [c.content for c in candidates])
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
        return [replace(hit, score=score) for hit, score in ranked]

    def _fetch(self, ids: Sequence[str], known: dict[str, Hit]) -> list[Hit]:
        """The articles for these ids, in this order (fetching those not already in hand)."""
        missing = [i for i in ids if i not in known]
        found = dict(known)
        if missing:
            result = self._collection.get(ids=missing, include=["documents", "metadatas"])
            for meta, document in zip(
                result["metadatas"] or [], result["documents"] or [], strict=True
            ):
                found[str(meta["document_id"])] = Hit(
                    document_id=str(meta["document_id"]),
                    title=str(meta["title"]),
                    category=str(meta["category"]),
                    source=str(meta["source"]),
                    content=document,
                    score=0.0,
                )
        return [found[i] for i in ids if i in found]
