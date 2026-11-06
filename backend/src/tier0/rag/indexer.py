from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.api.shared_system_client import SharedSystemClient

from tier0.rag.embeddings import Embedder
from tier0.rag.schema import KnowledgeRecord

COLLECTION_NAME = "knowledge_base"
INDEX_SCHEMA_VERSION = 1
BATCH_SIZE = 256


class IndexNotFoundError(Exception):
    """No usable index exists at the given location."""


@dataclass(frozen=True)
class BuildResult:
    skipped: bool
    record_count: int
    content_hash: str


def compute_content_hash(records: list[KnowledgeRecord], model_id: str) -> str:
    """Identify exactly what an index contains: the records, the embedding model, the layout."""
    ordered = sorted(records, key=lambda r: r.document_id)
    payload = json.dumps(
        {
            "schema": INDEX_SCHEMA_VERSION,
            "model": model_id,
            "records": [r.model_dump(mode="json") for r in ordered],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _metadata(record: KnowledgeRecord) -> dict[str, str | bool]:
    return {
        "document_id": record.document_id,
        "title": record.title,
        "category": record.category,
        "source": record.source,
        "synthetic": record.synthetic,
        "last_updated": record.last_updated.isoformat() if record.last_updated else "",
    }


def open_collection(index_dir: Path) -> Collection:
    if not (index_dir / "chroma.sqlite3").is_file():
        raise IndexNotFoundError(f"{index_dir}: no index found")
    client = chromadb.PersistentClient(path=str(index_dir))
    try:
        return client.get_collection(COLLECTION_NAME, embedding_function=None)
    except Exception as error:
        raise IndexNotFoundError(f"{index_dir}: collection {COLLECTION_NAME!r} missing") from error


def read_index_hash(index_dir: Path) -> tuple[str, int] | None:
    """Return (content_hash, record_count) of an existing index, or None."""
    try:
        collection = open_collection(index_dir)
    except IndexNotFoundError:
        return None
    stored = (collection.metadata or {}).get("content_hash")
    return (str(stored), collection.count()) if stored else None


def build_index(records: list[KnowledgeRecord], embedder: Embedder, index_dir: Path) -> BuildResult:
    if not records:
        raise ValueError("cannot build an index from zero records")
    content_hash = compute_content_hash(records, embedder.model_id)

    existing = read_index_hash(index_dir)
    if existing == (content_hash, len(records)):
        return BuildResult(skipped=True, record_count=len(records), content_hash=content_hash)

    staging = index_dir.with_name(index_dir.name + ".building")
    shutil.rmtree(staging, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        client = chromadb.PersistentClient(path=str(staging))
        collection = client.create_collection(
            COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "content_hash": content_hash,
                "embedding_model": embedder.model_id,
                "schema_version": INDEX_SCHEMA_VERSION,
            },
            embedding_function=None,
        )
        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start : start + BATCH_SIZE]
            vectors: list[Sequence[float]] = list(embedder.embed([r.content for r in batch]))
            collection.add(
                ids=[r.document_id for r in batch],
                documents=[r.content for r in batch],
                embeddings=vectors,
                metadatas=[_metadata(r) for r in batch],
            )
        SharedSystemClient.clear_system_cache()
        shutil.rmtree(index_dir, ignore_errors=True)
        staging.rename(index_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildResult(skipped=False, record_count=len(records), content_hash=content_hash)
