from pathlib import Path

import pytest

from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.evaluation import (
    RetrievalCase,
    evaluate_gap_separation,
    evaluate_retrieval,
    load_cases,
)
from tier0.rag.gaps import load_gap_topics
from tier0.rag.indexer import IndexNotFoundError, build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import IndexMismatchError, Retriever

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"


class OtherModelEmbedder(FakeEmbedder):
    model_id = "fake-other-model"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


def test_search_returns_k_hits_best_first(retriever: Retriever) -> None:
    hits = retriever.search("I cannot reset my password in the self-service portal", k=4)
    assert len(hits) == 4
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert "password" in hits[0].title.lower()


def test_hits_carry_the_record_fields(retriever: Retriever) -> None:
    (hit,) = retriever.search("bring photo ID, proof of address and signed offer letter", k=1)
    assert hit.document_id == "onb-002"
    assert hit.category == "Onboarding" and hit.title == "Documents to Bring on Your First Day"
    assert hit.source and "government-issued photo ID" in hit.content
    assert 0.0 < hit.score <= 1.0


def test_k_must_be_positive(retriever: Retriever) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        retriever.search("anything", k=0)


def test_an_index_built_with_another_model_is_refused(tmp_path: Path) -> None:
    index = tmp_path / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), OtherModelEmbedder(), index)
    with pytest.raises(IndexMismatchError, match="fake-other-model"):
        Retriever.open(index, FakeEmbedder())


def test_opening_a_missing_index_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(IndexNotFoundError):
        Retriever.open(tmp_path / "nothing", FakeEmbedder())


def fixture_cases() -> list[RetrievalCase]:
    """Evaluation cases whose expected documents are all present in the fixture KB."""
    present = {r.document_id for r in load_knowledge_base(FIXTURE_DIR)}
    cases = load_cases(REPO_ROOT / "data" / "evaluation" / "retrieval_cases.json")
    return [c for c in cases if set(c.relevant_ids) <= present]


def test_evaluation_scores_cases_against_a_real_index(retriever: Retriever) -> None:
    cases = fixture_cases()
    assert len(cases) >= 10
    report = evaluate_retrieval(cases, retriever)
    assert len(report.results) == len(cases)
    assert report.ks == (1, 3, 5)
    assert report.mean_recall[5] >= report.mean_recall[3] >= report.mean_recall[1]
    assert report.hit_rate[5] >= 0.8
    assert 0.0 < report.mrr <= 1.0
    assert set(report.by_kind) <= {"handbook", "it", "compliance"}
    assert all(not r.hit[5] for r in report.misses(5))


def test_a_perfect_and_a_hopeless_case_score_one_and_zero(retriever: Retriever) -> None:
    perfect = RetrievalCase(
        id="p",
        query="bring photo ID, proof of address and signed offer letter",
        relevant_ids=("onb-002",),
        kind="handbook",
    )
    hopeless = RetrievalCase(
        id="h",
        query="bring photo ID, proof of address and signed offer letter",
        relevant_ids=("no-such-id",),
        kind="it",
    )
    report = evaluate_retrieval([perfect, hopeless], retriever, ks=(1, 3))
    assert report.results[0].recall[1] == 1.0 and report.results[0].reciprocal_rank == 1.0
    assert report.results[1].recall[3] == 0.0 and report.results[1].reciprocal_rank == 0.0
    assert [m.case.id for m in report.misses(3)] == ["h"]


def test_gap_separation_scores_every_gap_question(retriever: Retriever) -> None:
    topics = load_gap_topics(REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json")
    report = evaluate_retrieval(fixture_cases(), retriever)
    separation = evaluate_gap_separation(topics, report, retriever)
    assert len(separation.gap_scores) == sum(len(t.example_questions) for t in topics)
    assert all(0.0 <= g.top_score <= 1.0 and g.top_title for g in separation.gap_scores)
    assert len(separation.answerable_scores) == len(report.results)
