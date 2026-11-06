from collections import Counter
from pathlib import Path

import pytest

from tier0.rag.evaluation import RetrievalCase, load_cases
from tier0.rag.gaps import load_gap_topics, matching_topic_ids
from tier0.rag.loader import load_knowledge_base

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES_FILE = REPO_ROOT / "data" / "evaluation" / "retrieval_cases.json"


@pytest.fixture(scope="module")
def cases() -> list[RetrievalCase]:
    return load_cases(CASES_FILE)


def test_between_thirty_and_fifty_cases(cases: list[RetrievalCase]) -> None:
    assert 30 <= len(cases) <= 50


def test_ids_and_queries_are_unique(cases: list[RetrievalCase]) -> None:
    assert len({c.id for c in cases}) == len(cases)
    assert len({c.query for c in cases}) == len(cases)


def test_every_kind_is_represented(cases: list[RetrievalCase]) -> None:
    kinds = Counter(c.kind for c in cases)
    assert set(kinds) == {"handbook", "it", "compliance"}
    assert kinds["handbook"] >= 20


def test_every_expected_document_exists_in_the_knowledge_base(cases: list[RetrievalCase]) -> None:
    known = {r.document_id for r in load_knowledge_base(REPO_ROOT / "data" / "knowledge_base")}
    missing = [(c.id, i) for c in cases for i in c.relevant_ids if i not in known]
    assert not missing, missing


def test_handbook_cases_point_at_handbook_entries(cases: list[RetrievalCase]) -> None:
    for case in cases:
        if case.kind == "handbook":
            assert all(not i.startswith("qa-") for i in case.relevant_ids), case.id


def test_answerable_queries_are_not_knowledge_gap_questions(cases: list[RetrievalCase]) -> None:
    topics = load_gap_topics(REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json")
    for case in cases:
        assert not matching_topic_ids(topics, case.query), case.id
