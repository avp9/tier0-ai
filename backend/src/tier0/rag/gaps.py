from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GapTopic:
    id: str
    name: str
    domain: str
    patterns: tuple[re.Pattern[str], ...]
    example_questions: tuple[str, ...]

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


def load_gap_topics(path: Path) -> list[GapTopic]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        GapTopic(
            id=topic["id"],
            name=topic["name"],
            domain=topic["domain"],
            patterns=tuple(re.compile(p, re.IGNORECASE) for p in topic["patterns"]),
            example_questions=tuple(topic["example_questions"]),
        )
        for topic in raw["topics"]
    ]


def matching_topic_ids(topics: list[GapTopic], *texts: str) -> list[str]:
    combined = " ".join(texts)
    return [topic.id for topic in topics if topic.matches(combined)]
