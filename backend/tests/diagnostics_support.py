"""A tiny evaluation set and a scripted retriever, shared by the diagnostics tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tier0.rag.retriever import Hit

# question -> ids returned best first (all with the same, falling scores)
RESULTS: dict[str, list[str]] = {
    "found first": ["a", "x", "y"],  # relevant a: rank 1
    "found third": ["x", "y", "b"],  # relevant b: rank 3
    "never found": ["x", "y", "z"],  # relevant c: not in the results
    "gap one": ["x"],
    "gap two": ["y"],
}
TOP_SCORES = {
    "found first": 0.9,
    "found third": 0.6,
    "never found": 0.4,
    "gap one": 0.5,
    "gap two": 0.2,
}


class ScriptedRetriever:
    def __init__(self) -> None:
        self.searches: list[str] = []

    def search(self, query: str, k: int = 5) -> list[Hit]:
        self.searches.append(query)
        top = TOP_SCORES[query]
        return [
            Hit(
                document_id=document_id,
                title=f"Title {document_id}",
                category="IT Support",
                source="test",
                content="content",
                score=top - 0.01 * rank,
            )
            for rank, document_id in enumerate(RESULTS[query][:k])
        ]


def write_evaluation_files(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = [
        {"id": "c1", "query": "found first", "relevant_ids": ["a"], "kind": "it"},
        {"id": "c2", "query": "found third", "relevant_ids": ["b"], "kind": "it"},
        {"id": "c3", "query": "never found", "relevant_ids": ["c"], "kind": "handbook"},
    ]
    (directory / "retrieval_cases.json").write_text(json.dumps({"cases": cases}))
    topics = [
        {
            "id": "t1",
            "name": "Gap topic",
            "domain": "IT",
            "patterns": ["zzz"],
            "example_questions": ["gap one", "gap two"],
        }
    ]
    (directory / "knowledge_gaps.json").write_text(json.dumps({"topics": topics}))
    return directory
