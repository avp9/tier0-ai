"""Help choose SEMANTIC_CACHE_SIMILARITY: how similar are paraphrases, and how similar are
questions that only look alike?

  uv run python scripts/inspect_semantic_cache.py
  uv run python scripts/inspect_semantic_cache.py --embedder fake      # no API key needed

Questions that mean the same should score above the setting (so they share an answer); questions
that merely look alike must score below it (so they never do). A good setting sits in the gap.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from tier0.rag.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder

REPO_ROOT = Path(__file__).resolve().parents[2]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def score_pairs(embedder: Embedder, pairs: list[list[str]]) -> list[tuple[float, str, str]]:
    texts = [text for pair in pairs for text in pair]
    vectors = embedder.embed(texts)
    return [
        (cosine(vectors[2 * i], vectors[2 * i + 1]), first, second)
        for i, (first, second) in enumerate(pairs)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--embedder", choices=["openai", "fake"], default="openai")
    parser.add_argument(
        "--model", default=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    parser.add_argument(
        "--pairs", type=Path, default=REPO_ROOT / "data" / "evaluation" / "cache_pairs.json"
    )
    parser.add_argument("--current", type=float, default=None, help="a setting to test")
    args = parser.parse_args()

    if args.embedder == "fake":
        embedder: Embedder = FakeEmbedder()
    else:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            sys.exit("OPENAI_API_KEY is not set (use --embedder fake to try it without)")
        embedder = OpenAIEmbedder(api_key=key, model=args.model)

    data = json.loads(args.pairs.read_text(encoding="utf-8"))
    same = sorted(score_pairs(embedder, data["same"]), reverse=True)
    different = sorted(score_pairs(embedder, data["different"]), reverse=True)

    print(f"Embedding model: {embedder.model_id}\n")
    print("SAME meaning (should share an answer):")
    for score, first, second in same:
        print(f"  {score:.3f}  {first!r}  ~  {second!r}")
    print("\nDIFFERENT meaning (must NOT share an answer):")
    for score, first, second in different:
        print(f"  {score:.3f}  {first!r}  ~  {second!r}")

    highest_wrong = different[0][0]
    lowest_right = same[-1][0]
    print(f"\nHighest score between questions that must not match: {highest_wrong:.3f}")
    print(f"Lowest score between questions that should match:    {lowest_right:.3f}")
    # Clear of the highest wrong match by a margin, because new questions will not all be as
    # tame as these examples.
    safe = math.ceil((highest_wrong + 0.05) * 100) / 100
    hits = sum(score >= safe for score, _, _ in same)
    print(
        f"\nSafest setting: SEMANTIC_CACHE_SIMILARITY={safe:.3f} "
        f"(no wrong matches here; reuses {hits} of {len(same)} paraphrases)"
    )
    if args.current is not None:
        wrong = [(s, a, b) for s, a, b in different if s >= args.current]
        right = sum(score >= args.current for score, _, _ in same)
        print(
            f"At {args.current:.3f}: {right} of {len(same)} paraphrases reused, "
            f"{len(wrong)} wrong matches"
        )
        for score, first, second in wrong:
            print(f"    WRONG {score:.3f}  {first!r}  ~  {second!r}")


if __name__ == "__main__":
    main()
