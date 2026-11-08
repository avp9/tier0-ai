"""Compare the search variants on the same questions: dense, SPLADE, merged, reranked, everything.

  uv run python scripts/compare_search.py                       # the 44 evaluation questions
  uv run python scripts/compare_search.py --queries             # plus who got each one right
  uv run python scripts/compare_search.py "hotel limit for travel"   # one question, top 3 of each

Needs OPENAI_API_KEY (the dense search embeds the question) and a keyword index (build_index.py).
A question counts as found when one of its listed correct articles is in the top results.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tier0.agent.factory import open_search
from tier0.rag.embeddings import OpenAIEmbedder
from tier0.rag.evaluation import hit_at_k, load_cases, recall_at_k, reciprocal_rank
from tier0.rag.retriever import Hit

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGES = ["dense", "splade", "dense+splade", "dense+rerank", "dense+splade+rerank"]
KS = (1, 3, 5)


def short(hits: list[Hit], n: int = 3) -> str:
    return " | ".join(f"{h.title[:42]} ({h.score:.2f})" for h in hits[:n])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("query", nargs="?", help="show the top 3 of every variant for one question")
    parser.add_argument("--queries", action="store_true", help="show each question's first hit")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument(
        "--cases", type=Path, default=REPO_ROOT / "data" / "evaluation" / "retrieval_cases.json"
    )
    parser.add_argument(
        "--model", default=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    args = parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is not set")
    retriever = open_search(
        args.index_dir, OpenAIEmbedder(api_key=key, model=args.model), "hybrid_rerank"
    )
    if not retriever.reranks:
        sys.exit("the keyword index or a model is not available (see the warning above)")

    if args.query:
        for name, hits in retriever.search_stages(args.query).items():
            print(f"{name:21} {short(hits)}")
        return 0

    cases = load_cases(args.cases)
    rows: dict[str, dict[str, float]] = {s: {} for s in STAGES}
    first_hit: dict[str, dict[str, bool]] = {}
    for case in cases:
        relevant = set(case.relevant_ids)
        for stage, hits in retriever.search_stages(case.query, k=5).items():
            ids = [h.document_id for h in hits]
            row = rows[stage]
            for k in KS:
                row[f"hit@{k}"] = row.get(f"hit@{k}", 0.0) + hit_at_k(ids, relevant, k)
            row["recall@5"] = row.get("recall@5", 0.0) + recall_at_k(ids, relevant, 5)
            row["MRR"] = row.get("MRR", 0.0) + reciprocal_rank(ids, relevant)
            first_hit.setdefault(case.id, {})[stage] = hit_at_k(ids, relevant, 1)

    n = len(cases)
    columns = ["hit@1", "hit@3", "hit@5", "recall@5", "MRR"]
    print(f"{n} questions\n")
    print(f"{'':22}" + "".join(f"{c:>10}" for c in columns))
    for stage in STAGES:
        print(f"{stage:22}" + "".join(f"{rows[stage][c] / n:10.3f}" for c in columns))

    if args.queries:
        print(
            "\nfirst hit correct?  ("
            + ", ".join(f"{i + 1}={s}" for i, s in enumerate(STAGES))
            + ")"
        )
        for case in cases:
            marks = "".join("Y" if first_hit[case.id][s] else "." for s in STAGES)
            print(f"  {marks}  {case.id}  {case.query}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
