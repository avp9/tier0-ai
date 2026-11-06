"""Build (or skip rebuilding) the Chroma knowledge-base index.

Examples:
  uv run python scripts/build_index.py --embedder fake --kb-dir ../data/fixtures
  OPENAI_API_KEY=... uv run python scripts/build_index.py
  uv run python scripts/build_index.py --print-hash   # cache key for CI, no API calls
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tier0.rag.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from tier0.rag.indexer import build_index, compute_content_hash
from tier0.rag.loader import load_knowledge_base

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_embedder(name: str, model: str) -> Embedder:
    if name == "fake":
        return FakeEmbedder()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY is not set (use --embedder fake for a local test index)")
    return OpenAIEmbedder(api_key=api_key, model=model)


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
    parser.add_argument("--print-hash", action="store_true", help="print the content hash and exit")
    args = parser.parse_args()

    records = load_knowledge_base(args.kb_dir)
    embedder = FakeEmbedder() if args.print_hash else make_embedder(args.embedder, args.model)
    if args.print_hash:
        model_id = f"openai:{args.model}" if args.embedder == "openai" else embedder.model_id
        print(compute_content_hash(records, model_id))
        return

    result = build_index(records, embedder, args.index_dir)
    verb = "up to date, skipped" if result.skipped else "built"
    print(f"Index {verb}: {result.record_count} records, hash {result.content_hash[:12]}")


if __name__ == "__main__":
    main()
