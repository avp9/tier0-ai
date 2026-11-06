"""Inspect what the retriever returns, or measure it against the evaluation cases.

Examples:
  uv run python scripts/inspect_retrieval.py "how do I reset my password" -k 5
  uv run python scripts/inspect_retrieval.py --evaluate
Use --embedder fake for an index built with scripts/build_index.py --embedder fake.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tier0.rag.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from tier0.rag.evaluation import (
    RetrievalReport,
    SeparationReport,
    evaluate_gap_separation,
    evaluate_retrieval,
    load_cases,
    suggest_threshold,
)
from tier0.rag.gaps import load_gap_topics
from tier0.rag.retriever import Retriever

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_embedder(name: str, model: str) -> Embedder:
    if name == "fake":
        return FakeEmbedder()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY is not set (use --embedder fake for a fake-embedding index)")
    return OpenAIEmbedder(api_key=api_key, model=model)


def print_hits(retriever: Retriever, query: str, k: int) -> None:
    print(f"Query: {query}\n")
    for rank, hit in enumerate(retriever.search(query, k=k), start=1):
        print(f"{rank}. [{hit.score:.3f}] {hit.document_id}  ({hit.category})")
        print(f"   {hit.title}")
        print(f"   {hit.content[:160].replace(chr(10), ' ')}")


def print_report(report: RetrievalReport, separation: SeparationReport) -> None:
    print(f"Retrieval cases: {len(report.results)}")
    for k in report.ks:
        print(
            f"  k={k}:  Recall@{k} {report.mean_recall[k]:.3f}   Hit@{k} {report.hit_rate[k]:.3f}"
        )
    print(f"  MRR {report.mrr:.3f}")
    for kind, per_k in sorted(report.by_kind.items()):
        cells = "  ".join(f"Hit@{k} {rate:.2f}" for k, rate in sorted(per_k.items()))
        print(f"  {kind:<11} {cells}")
    largest = max(report.ks)
    for miss in report.misses(largest):
        print(f"  MISS@{largest} {miss.case.id}: {miss.case.query}")
    print("\nKnowledge Gap separation (top-1 similarity):")
    print(
        f"  answerable questions: min {separation.answerable_min:.3f}  "
        f"median {separation.answerable_median:.3f}"
    )
    print(
        f"  gap questions:        max {separation.gap_max:.3f}  median {separation.gap_median:.3f}"
    )
    verdict = "yes" if separation.separable else "no, scores overlap"
    print(f"  one threshold separates them: {verdict}")
    suggestion = suggest_threshold(
        separation.answerable_scores, [g.top_score for g in separation.gap_scores]
    )
    print(
        f"  suggested RETRIEVAL_SCORE_THRESHOLD {suggestion.threshold:.3f} "
        f"(keeps {suggestion.answerable_kept:.0%} of answerable, "
        f"rejects {suggestion.gaps_rejected:.0%} of gap questions)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("query", nargs="?", help="question to search for")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--evaluate", action="store_true", help="score the evaluation cases")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument("--embedder", choices=["openai", "fake"], default="openai")
    parser.add_argument(
        "--model", default=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    parser.add_argument(
        "--cases", type=Path, default=REPO_ROOT / "data" / "evaluation" / "retrieval_cases.json"
    )
    parser.add_argument(
        "--gaps", type=Path, default=REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"
    )
    args = parser.parse_args()
    if not args.query and not args.evaluate:
        parser.error("give a query, or use --evaluate")

    retriever = Retriever.open(args.index_dir, make_embedder(args.embedder, args.model))
    if args.query:
        print_hits(retriever, args.query, args.k)
    if args.evaluate:
        report = evaluate_retrieval(load_cases(args.cases), retriever)
        separation = evaluate_gap_separation(load_gap_topics(args.gaps), report, retriever)
        print_report(report, separation)


if __name__ == "__main__":
    main()
