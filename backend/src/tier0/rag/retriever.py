from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from chromadb.api.models.Collection import Collection

from tier0.rag.embeddings import Embedder
from tier0.rag.indexer import open_collection


class IndexMismatchError(Exception):
    """The index was built with a different embedding model than the one querying it."""


@dataclass(frozen=True)
class Hit:
    document_id: str
    title: str
    category: str
    source: str
    content: str
    score: float  # cosine similarity: 1.0 identical, 0.0 unrelated


class Retriever:
    def __init__(self, collection: Collection, embedder: Embedder) -> None:
        built_with = (collection.metadata or {}).get("embedding_model")
        if built_with != embedder.model_id:
            raise IndexMismatchError(
                f"index was built with {built_with!r} but queries use {embedder.model_id!r}"
            )
        self._collection = collection
        self._embedder = embedder

    @classmethod
    def open(cls, index_dir: Path, embedder: Embedder) -> Retriever:
        return cls(open_collection(index_dir), embedder)

    @property
    def fingerprint(self) -> str:
        """Identifies exactly what this retriever searches: the index contents and the model."""
        stored = (self._collection.metadata or {}).get("content_hash", "")
        return f"{stored}|{self._embedder.model_id}"

    def embed(self, query: str) -> list[float]:
        """The embedding of a question, so a caller that needs it can search with it too."""
        return list(self._embedder.embed([query])[0])

    def search(self, query: str, k: int = 5, *, vector: Sequence[float] | None = None) -> list[Hit]:
        """The best matches. Pass ``vector`` (from ``embed``) to avoid embedding the query twice."""
        if k < 1:
            raise ValueError("k must be at least 1")
        if vector is None:
            vector = self._embedder.embed([query])[0]
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=k,
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
