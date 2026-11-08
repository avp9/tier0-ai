"""Build (or skip rebuilding) the Chroma knowledge-base index.

Examples:
  uv run python scripts/build_index.py --embedder fake --kb-dir ../data/fixtures
  OPENAI_API_KEY=... uv run python scripts/build_index.py
  uv run python scripts/build_index.py --sparse none  # skip the slow keyword (SPLADE) index
  uv run python scripts/build_index.py --print-hash   # cache key for CI, no API calls

The keyword index (data/.../sparse) is what SEARCH_MODE=hybrid_rerank searches with. It takes
minutes for a large knowledge base (about 13 for 21,000 articles on a laptop) but is skipped when
it already matches the articles.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from tier0.rag.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from tier0.rag.indexer import build_index, compute_content_hash
from tier0.rag.loader import load_knowledge_base
from tier0.rag.sparse import FakeSparseEncoder, SparseEncoder, SpladeEncoder

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_embedder(name: str, model: str) -> Embedder:
    if name == "fake":
        return FakeEmbedder()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY is not set (use --embedder fake for a local test index)")
    return OpenAIEmbedder(api_key=api_key, model=model)


def make_sparse(name: str) -> SparseEncoder | None:
    if name == "none":
        return None
    return FakeSparseEncoder() if name == "fake" else SpladeEncoder()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--kb-dir", type=Path, default=REPO_ROOT / "data" / "knowledge_base")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument("--embedder", choices=["openai", "fake"], default="openai")
    parser.add_argument(
        "--model", default=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    parser.add_argument(
        "--sparse",
        choices=["splade", "fake", "none"],
        default=None,
        help="keyword index to build too (default: splade, or fake with --embedder fake)",
    )
    parser.add_argument("--print-hash", action="store_true", help="print the content hash and exit")
    args = parser.parse_args()

    records = load_knowledge_base(args.kb_dir)
    sparse_name = args.sparse or ("fake" if args.embedder == "fake" else "splade")
    sparse = make_sparse(sparse_name)
    embedder = FakeEmbedder() if args.print_hash else make_embedder(args.embedder, args.model)
    if args.print_hash:
        model_id = f"openai:{args.model}" if args.embedder == "openai" else embedder.model_id
        key = compute_content_hash(records, model_id)
        if sparse is not None:  # a new keyword model must also start a new cached index
            key = hashlib.sha256(f"{key}|{sparse.model_id}".encode()).hexdigest()
        print(key)
        return

    result = build_index(records, embedder, args.index_dir, sparse)
    verb = "up to date, skipped" if result.skipped else "built"
    print(f"Index {verb}: {result.record_count} records, hash {result.content_hash[:12]}")
    if sparse is not None:
        print(
            f"Keyword index ({sparse.model_id}): {'built' if result.sparse_built else 'up to date'}"
        )


if __name__ == "__main__":
    main()
