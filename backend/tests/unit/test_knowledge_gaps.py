import gzip
import json
from pathlib import Path

import pytest

from tier0.rag.gaps import GapTopic, load_gap_topics, matching_topic_ids

REPO_ROOT = Path(__file__).resolve().parents[3]
GAPS_FILE = REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"
KB_DIR = REPO_ROOT / "data" / "knowledge_base"


@pytest.fixture(scope="module")
def topics() -> list[GapTopic]:
    return load_gap_topics(GAPS_FILE)


def test_topic_count_and_unique_ids(topics: list[GapTopic]) -> None:
    ids = [topic.id for topic in topics]
    assert 10 <= len(topics) <= 15
    assert len(ids) == len(set(ids))


def test_each_topic_has_patterns_and_examples(topics: list[GapTopic]) -> None:
    for topic in topics:
        assert topic.patterns, topic.id
        assert len(topic.example_questions) >= 3, topic.id


def test_spec_examples_are_covered(topics: list[GapTopic]) -> None:
    ids = {topic.id for topic in topics}
    assert {"monitor-flicker", "office-parking"} <= ids


def test_example_questions_match_their_own_topic(topics: list[GapTopic]) -> None:
    for topic in topics:
        for question in topic.example_questions:
            assert topic.matches(question), f"{topic.id}: {question!r}"


def test_example_questions_do_not_match_other_topics(topics: list[GapTopic]) -> None:
    for topic in topics:
        for question in topic.example_questions:
            assert matching_topic_ids(topics, question) == [topic.id], question


def test_matching_is_case_insensitive_and_word_bounded(topics: list[GapTopic]) -> None:
    assert matching_topic_ids(topics, "WHERE IS THE PARKING LOT?") == ["office-parking"]
    assert matching_topic_ids(topics, "How do I reset my password?") == []
    assert matching_topic_ids(topics, "Please reset my carparking token") == []


def test_committed_knowledge_base_has_no_gap_content(topics: list[GapTopic]) -> None:
    offenders: list[str] = []
    with gzip.open(KB_DIR / "qa_dataset.json.gz", "rt", encoding="utf-8") as handle:
        records = json.load(handle)
    for record in records:
        hits = matching_topic_ids(topics, record["question"] or "", record["answer"] or "")
        if hits:
            offenders.append(f"{hits}: {record['question'][:60]}")
    assert not offenders, offenders[:5]
