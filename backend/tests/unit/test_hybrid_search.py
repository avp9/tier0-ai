"""Keyword (SPLADE) search, reranking and their fusion, with fake models (no downloads)."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tracing_support import make_settings

from tier0.agent.factory import open_retriever, score_threshold
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.rerank import FakeReranker, sigmoid
from tier0.rag.retriever import HybridSearch, Retriever, reciprocal_rank_fusion
from tier0.rag.schema import KnowledgeRecord
from tier0.rag.sparse import (
    FakeSparseEncoder,
    SparseIndex,
    SparseIndexError,
    SparseVector,
    read_sparse_meta,
)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"


def record(doc_id: str, title: str, content: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        document_id=doc_id, title=title, category="IT Support", source="test", content=content
    )


RECORDS = [
    record("a", "Passwords", "Reset your password from the self service portal."),
    record("b", "Travel", "The hotel limit for business travel is 150 euros per night."),
    record("c", "Printers", "Install the printer driver from the software centre."),
    record("d", "Holidays", "BuzzTech observes ten public holidays each year."),
]


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    build_index(RECORDS, FakeEmbedder(), tmp_path / "idx", FakeSparseEncoder())
    return tmp_path / "idx"


def hybrid_for(index_dir: Path, reranker: Any = None) -> HybridSearch:
    return HybridSearch(
        SparseIndex.load(index_dir), FakeSparseEncoder(), reranker or FakeReranker()
    )


def test_a_question_finds_the_articles_sharing_its_words_best_first() -> None:
    encoder = FakeSparseEncoder()
    index = SparseIndex(
        [r.document_id for r in RECORDS],
        encoder.encode_documents([r.content for r in RECORDS]),
        model_id=encoder.model_id,
        index_hash="h",
    )
    found = index.search(encoder.encode_query("hotel limit for travel"), 3)
    assert found[0][0] == "b" and found[0][1] > 0
    assert index.search(encoder.encode_query("zzzz qqqq"), 3) == []  # nothing shared, no results


def test_an_empty_vector_and_asking_for_more_than_exists_are_fine() -> None:
    index = SparseIndex(
        ["a"],
        [SparseVector(np.array([1], np.int32), np.array([2.0], np.float32))],
        model_id="m",
        index_hash="h",
    )
    empty = SparseVector(np.zeros(0, np.int32), np.zeros(0, np.float32))
    assert index.search(empty, 5) == []
    assert index.search(SparseVector(np.array([1], np.int32), np.array([1.0], np.float32)), 99) == [
        ("a", 2.0)
    ]


def test_the_keyword_index_survives_being_saved_and_loaded(index_dir: Path) -> None:
    loaded = SparseIndex.load(index_dir)
    assert len(loaded) == len(RECORDS) and loaded.model_id == "fake-sparse"
    assert loaded.search(FakeSparseEncoder().encode_query("printer driver"), 1)[0][0] == "c"


def test_a_missing_or_broken_keyword_index_is_reported_not_crashed(tmp_path: Path) -> None:
    with pytest.raises(SparseIndexError):
        SparseIndex.load(tmp_path)
    assert read_sparse_meta(tmp_path) is None
    build_index(RECORDS, FakeEmbedder(), tmp_path / "i", None)  # no keyword index asked for
    assert read_sparse_meta(tmp_path / "i") is None


def test_the_keyword_index_is_only_rebuilt_when_something_changed(tmp_path: Path) -> None:
    calls: list[int] = []

    class Counting(FakeSparseEncoder):
        def encode_documents(self, texts: Any) -> list[SparseVector]:
            calls.append(len(texts))
            return super().encode_documents(texts)

    target = tmp_path / "idx"
    first = build_index(RECORDS, FakeEmbedder(), target, Counting())
    again = build_index(RECORDS, FakeEmbedder(), target, Counting())
    assert first.sparse_built and not again.sparse_built and calls == [4]
    changed = [*RECORDS, record("e", "New", "A brand new article about badges.")]
    third = build_index(changed, FakeEmbedder(), target, Counting())
    assert third.sparse_built and calls == [4, 5]
    assert len(SparseIndex.load(target)) == 5


def test_an_index_added_after_the_dense_one_is_built_without_rebuilding_the_dense_one(
    tmp_path: Path,
) -> None:
    target = tmp_path / "idx"
    build_index(RECORDS, FakeEmbedder(), target)
    result = build_index(RECORDS, FakeEmbedder(), target, FakeSparseEncoder())
    assert result.skipped and result.sparse_built


def test_fusion_puts_what_both_lists_like_first() -> None:
    assert reciprocal_rank_fusion(["x", "a", "b"], ["a", "y", "x"])[:2] == ["a", "x"]
    assert reciprocal_rank_fusion([], []) == []
    assert reciprocal_rank_fusion(["a"], []) == ["a"]


def test_the_sigmoid_is_a_zero_to_one_score_without_overflowing() -> None:
    assert sigmoid(0) == 0.5 and sigmoid(-1000) == 0.0 and sigmoid(1000) == 1.0
    assert sigmoid(-2) < 0.12 < sigmoid(-1.9)


def test_a_hybrid_search_reranks_and_reports_the_rerankers_scores(index_dir: Path) -> None:
    retriever = Retriever.open(index_dir, FakeEmbedder(), hybrid_for(index_dir))
    hits = retriever.search("what is the hotel limit for business travel", k=3)
    assert hits[0].document_id == "b"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True) and 0.0 < scores[0] <= 1.0
    assert retriever.reranks
    assert retriever.fingerprint.endswith("|fake-sparse|fake-rerank")


def test_an_article_only_the_keyword_search_finds_is_still_considered(index_dir: Path) -> None:
    class Dense(FakeEmbedder):
        """Embeddings that match nothing, so only the keyword search can find the answer."""

        def embed(self, texts: Any) -> list[list[float]]:
            return [[1.0] + [0.0] * 255 for _ in texts]

    retriever = Retriever.open(index_dir, Dense(), hybrid_for(index_dir))
    assert "d" in [h.document_id for h in retriever.search("public holidays", k=4)]


def test_a_plain_retriever_is_unchanged(index_dir: Path) -> None:
    retriever = Retriever.open(index_dir, FakeEmbedder())
    assert not retriever.reranks and "fake-sparse" not in retriever.fingerprint
    assert len(retriever.search("password", k=2)) == 2


def test_if_the_reranker_breaks_that_question_is_still_answered_by_dense_search(
    index_dir: Path,
) -> None:
    class Broken(FakeReranker):
        def score(self, query: str, passages: Any) -> list[float]:
            raise RuntimeError("model crashed")

    retriever = Retriever.open(index_dir, FakeEmbedder(), hybrid_for(index_dir, Broken()))
    assert len(retriever.search("password reset", k=2)) == 2


def test_a_keyword_index_from_a_different_index_is_refused(tmp_path: Path) -> None:
    build_index(RECORDS, FakeEmbedder(), tmp_path / "one", FakeSparseEncoder())
    build_index(RECORDS[:2], FakeEmbedder(), tmp_path / "two", FakeSparseEncoder())
    with pytest.raises(SparseIndexError):
        Retriever.open(tmp_path / "one", FakeEmbedder(), hybrid_for(tmp_path / "two"))


def test_the_factory_uses_hybrid_when_it_can_and_the_right_threshold(index_dir: Path) -> None:
    settings = make_settings(
        search_mode="hybrid_rerank", llm_provider="fake", vector_store_path=str(index_dir)
    )
    retriever = open_retriever(settings, FakeEmbedder())
    assert retriever.reranks
    assert score_threshold(settings, retriever) == settings.rerank_score_threshold


def test_the_factory_falls_back_to_dense_search_when_the_keyword_index_is_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    build_index(RECORDS, FakeEmbedder(), tmp_path / "idx")  # no keyword index
    settings = make_settings(
        search_mode="hybrid_rerank", llm_provider="fake", vector_store_path=str(tmp_path / "idx")
    )
    retriever = open_retriever(settings, FakeEmbedder())
    assert not retriever.reranks
    assert score_threshold(settings, retriever) == settings.retrieval_score_threshold
    assert "not available" in caplog.text


def test_dense_is_the_default_search_mode() -> None:
    assert make_settings().search_mode == "dense"
    assert make_settings().effective_score_threshold == make_settings().retrieval_score_threshold


def test_the_fixture_knowledge_base_gets_a_keyword_index(tmp_path: Path) -> None:
    records = load_knowledge_base(FIXTURE_DIR)
    result = build_index(records, FakeEmbedder(), tmp_path / "i", FakeSparseEncoder())
    assert result.sparse_built and len(SparseIndex.load(tmp_path / "i")) == len(records)


def test_every_search_variant_can_be_compared_on_one_question(index_dir: Path) -> None:
    retriever = Retriever.open(index_dir, FakeEmbedder(), hybrid_for(index_dir))
    stages = retriever.search_stages("hotel limit for business travel", k=2)
    assert list(stages) == [
        "dense",
        "splade",
        "dense+splade",
        "dense+rerank",
        "dense+splade+rerank",
    ]
    assert stages["splade"][0].document_id == "b"
    assert stages["dense+splade+rerank"][0].document_id == "b"
    assert all(len(hits) <= 2 for hits in stages.values())
    with pytest.raises(ValueError):
        Retriever.open(index_dir, FakeEmbedder()).search_stages("x")
