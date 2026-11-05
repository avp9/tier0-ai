"""Remove knowledge-base entries that cover a Knowledge Gap topic.

Run after tools/build_qa_dataset.py: uv run python scripts/prune_knowledge_gaps.py
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

from tier0.rag.gaps import load_gap_topics, matching_topic_ids

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "knowledge_base" / "qa_dataset.json.gz"
GAPS = REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"


def main() -> None:
    topics = load_gap_topics(GAPS)
    with gzip.open(DATASET, "rt", encoding="utf-8") as handle:
        records: list[dict[str, str | None]] = json.load(handle)

    kept: list[dict[str, str | None]] = []
    removed_per_topic: Counter[str] = Counter()
    for record in records:
        hits = matching_topic_ids(topics, record["question"] or "", record["answer"] or "")
        if hits:
            removed_per_topic.update(hits)
        else:
            kept.append(record)

    with gzip.open(DATASET, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(kept, handle, ensure_ascii=False, separators=(",", ":"))

    print(
        f"Records before: {len(records)}  removed: {len(records) - len(kept)}  after: {len(kept)}"
    )
    for topic in topics:
        print(f"  {removed_per_topic[topic.id]:4d}  {topic.id}")


if __name__ == "__main__":
    main()
