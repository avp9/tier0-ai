"""Make borrowed HR entries safe and consistent for BuzzTech Solutions.

Run after prune_knowledge_gaps.py: uv run python scripts/normalize_company_content.py
Drops entries naming real individuals or contradicting the synthetic handbook, and
rewrites the original company name and contact addresses.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from tier0.rag.normalize import is_borrowed_source, rewrite_company, should_drop

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "knowledge_base" / "qa_dataset.json.gz"


def main() -> None:
    with gzip.open(DATASET, "rt", encoding="utf-8") as handle:
        records: list[dict[str, str | None]] = json.load(handle)

    kept: list[dict[str, str | None]] = []
    dropped = rewritten = 0
    for record in records:
        if not is_borrowed_source(record["source"] or ""):
            kept.append(record)
            continue
        question, answer = record["question"] or "", record["answer"] or ""
        if should_drop(question, answer):
            dropped += 1
            continue
        new_question, new_answer = rewrite_company(question), rewrite_company(answer)
        if (new_question, new_answer) != (question, answer):
            rewritten += 1
        kept.append({**record, "question": new_question, "answer": new_answer})

    with gzip.open(DATASET, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(kept, handle, ensure_ascii=False, separators=(",", ":"))

    print(f"Records before: {len(records)}  dropped: {dropped}  rewritten: {rewritten}")
    print(f"Records after: {len(kept)}")


if __name__ == "__main__":
    main()
