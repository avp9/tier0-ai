"""Generate the question-and-answer evaluation set from the knowledge base.

  uv run --env-file ../.env python scripts/generate_eval_dataset.py   # about 150 model calls
  uv run --env-file ../.env python scripts/generate_eval_dataset.py --limit 5  # trial

Writes data/evaluation/qa_dataset.jsonl and qa_dataset.csv. Questions are written by the OpenAI
model from sampled articles (a few cents); they are NOT in the articles' own words. Articles that
are near copies of the source count as correct sources too (so search is not marked wrong for
finding an equally good duplicate). Questions about topics the knowledge base lacks come from
data/evaluation/knowledge_gaps.json. Review the output before relying on it: a model wrote it.

Upload to LangSmith:  uv run --env-file ../.env python scripts/langsmith_eval.py upload
  (or, in the LangSmith app: Datasets > New dataset > Upload CSV, using qa_dataset.csv with
   question as input and reference_answer, key_facts, relevant_ids, should_answer as outputs).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from tier0.evalrun.cases import gap_cases
from tier0.evalrun.qa_dataset import Plan, build_dataset, write_csv, write_jsonl
from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.indexer import open_collection
from tier0.rag.loader import load_knowledge_base

REPO_ROOT = Path(__file__).resolve().parents[2]
SIMILAR = 0.93  # cosine similarity at which another article is as good a source as the original


def neighbour_finder(index_dir: Path):  # type: ignore[no-untyped-def]
    collection = open_collection(index_dir)

    def find(document_id: str) -> list[str]:
        stored = collection.get(ids=[document_id], include=["embeddings"])
        embeddings = stored["embeddings"]
        if embeddings is None or len(embeddings) == 0:
            return []
        result = collection.query(
            query_embeddings=[list(embeddings[0])], n_results=8, include=["distances"]
        )
        ids = (result["ids"] or [[]])[0]
        distances = (result["distances"] or [[]])[0]
        return [
            i for i, d in zip(ids, distances, strict=True) if i != document_id and 1 - d >= SIMILAR
        ]

    return find


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--kb-dir", type=Path, default=REPO_ROOT / "data" / "knowledge_base")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / ".data" / "chroma")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "evaluation")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL_NAME") or "gpt-4o-mini")
    parser.add_argument(
        "--gaps", type=Path, default=REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--limit", type=int, default=0, help="only this many of each kind (a trial)"
    )
    args = parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is not set")

    plan = Plan(seed=args.seed)
    if args.limit:
        plan = Plan(
            handbook=args.limit,
            handbook_scenarios=args.limit,
            hr_qa=args.limit,
            it_tickets=args.limit,
            seed=args.seed,
        )
    examples = build_dataset(
        OpenAIChatModel(api_key=key, model=args.model),
        load_knowledge_base(args.kb_dir),
        gap_cases(args.gaps),
        plan,
        neighbours=neighbour_finder(args.index_dir) if args.index_dir.exists() else None,
        progress=lambda line: print(line, flush=True),
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_trial" if args.limit else ""
    write_jsonl(args.out_dir / f"qa_dataset{suffix}.jsonl", examples)
    write_csv(args.out_dir / f"qa_dataset{suffix}.csv", examples)
    kinds = Counter(f"{e.source_kind}/{e.question_type}" for e in examples)
    print(f"\n{len(examples)} examples written to {args.out_dir}")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind:24} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
