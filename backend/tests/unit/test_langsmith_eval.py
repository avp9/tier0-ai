"""The LangSmith evaluation module: datasets, targets and scores, with no network."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tier0.evalrun import langsmith_eval as ls
from tier0.evalrun.cases import (
    GapCase,
    KnowledgeCase,
    gap_cases,
    knowledge_cases,
    load_conversation_cases,
)
from tier0.rag.evaluation import load_cases

EVAL_DIR = Path(__file__).resolve().parents[3] / "data" / "evaluation"


class FakeDatasets:
    """Remembers what would be in the LangSmith dataset."""

    def __init__(self) -> None:
        self.datasets: dict[str, str] = {}
        self.examples: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self.datasets

    def create_dataset(self, dataset_name: str, *, description: str | None = None) -> Any:
        self.datasets[dataset_name] = description or ""
        self.calls.append("create_dataset")

    def read_dataset(self, *, dataset_name: str) -> Any:
        return SimpleNamespace(id="ds-1", name=dataset_name)

    def list_examples(self, *, dataset_id: Any, **kwargs: Any) -> Any:
        return [SimpleNamespace(id=i) for i in self.examples]

    def create_examples(self, *, dataset_id: Any, examples: list[dict[str, Any]]) -> Any:
        self.calls.append("create")
        for e in examples:
            self.examples[e["id"]] = e

    def update_examples(self, *, dataset_id: Any, updates: list[dict[str, Any]]) -> Any:
        self.calls.append("update")
        for e in updates:
            self.examples[e["id"]] = e

    def delete_examples(self, *, example_ids: list[Any]) -> Any:
        self.calls.append("delete")
        for i in example_ids:
            del self.examples[i]


def specs() -> list[ls.ExampleSpec]:
    return ls.retrieval_examples(load_cases(EVAL_DIR / "retrieval_cases.json"))


def test_every_case_file_becomes_examples_with_the_answer_kept_apart_from_the_question() -> None:
    retrieval = load_cases(EVAL_DIR / "retrieval_cases.json")
    built = {
        "retrieval": ls.retrieval_examples(retrieval),
        "knowledge": ls.knowledge_examples(knowledge_cases(retrieval)),
        "gaps": ls.gap_examples(gap_cases(EVAL_DIR / "knowledge_gaps.json")),
        "conversations": ls.conversation_examples(
            load_conversation_cases(EVAL_DIR / "conversation_cases.json")
        ),
    }
    for key, examples in built.items():
        assert examples, key
        assert len({e.case_id for e in examples}) == len(examples)  # ids are unique
    assert "relevant_ids" not in built["retrieval"][0].inputs  # the model never sees the answer
    assert built["retrieval"][0].outputs["relevant_ids"]
    assert built["gaps"][0].outputs == {"should_answer": False}


def test_example_ids_do_not_change_between_uploads() -> None:
    assert ls.example_id("tier0-x", "a") == ls.example_id("tier0-x", "a")
    assert ls.example_id("tier0-x", "a") != ls.example_id("tier0-x", "b")
    assert ls.example_id("tier0-x", "a") != ls.example_id("tier0-y", "a")


def test_uploading_twice_adds_nothing_new_and_a_removed_case_is_deleted() -> None:
    client = FakeDatasets()
    items = specs()
    first = ls.sync_dataset(client, "retrieval", items)
    assert (first.created, first.updated, first.removed) == (len(items), 0, 0)
    second = ls.sync_dataset(client, "retrieval", items)
    assert (second.created, second.updated, second.removed) == (0, len(items), 0)
    assert client.calls.count("create_dataset") == 1
    third = ls.sync_dataset(client, "retrieval", items[:-1])
    assert third.removed == 1 and len(client.examples) == len(items) - 1


def test_a_capped_upload_is_a_sample_and_deletes_nothing() -> None:
    client = FakeDatasets()
    items = specs()
    ls.sync_dataset(client, "retrieval", items)
    result = ls.sync_dataset(client, "retrieval", items, limit=2)
    assert result.removed == 0 and len(client.examples) == len(items)


class Hit:
    def __init__(self, document_id: str, score: float) -> None:
        self.document_id, self.score = document_id, score


def test_the_retrieval_target_returns_the_ids_in_order() -> None:
    class Searcher:
        def search(self, query: str, k: int = 5) -> list[Hit]:
            assert (query, k) == ("reset password", 5)
            return [Hit("a", 0.9), Hit("b", 0.5)]

    out = ls.retrieval_target(Searcher())({"id": "r1", "question": "reset password"})
    assert out == {"retrieved_ids": ["a", "b"], "top_score": 0.9}
    empty = ls.retrieval_target(SimpleNamespace(search=lambda q, k=5: []))({"question": "x"})
    assert empty["top_score"] == 0.0


def test_retrieval_scores() -> None:
    outputs, reference = {"retrieved_ids": ["x", "good", "y"]}, {"relevant_ids": ["good"]}
    results = {e.__name__: e(outputs, reference) for e in ls.evaluators_for("retrieval")}
    assert results["hit_at_1"]["score"] == 0.0 and results["hit_at_1"]["key"] == "hit@1"
    assert results["hit_at_3"]["score"] == 1.0
    assert results["recall_at_3"]["score"] == 1.0
    assert results["reciprocal_rank_evaluator"]["score"] == 0.5


def test_knowledge_target_and_scores() -> None:
    seen: list[KnowledgeCase] = []

    class Driver:
        def run_knowledge(self, case: KnowledgeCase) -> Any:
            seen.append(case)
            return SimpleNamespace(
                answered=True, sources=["kb1"], routed_correctly=True, seconds=1.234
            )

    out = ls.knowledge_target(Driver())({"id": "k1", "question": "How?"})  # type: ignore[arg-type]
    assert out == {
        "answered": True,
        "cited_ids": ["kb1"],
        "routed_correctly": True,
        "seconds": 1.23,
    }
    assert seen[0].question == "How?"
    scores = {e.__name__: e(out, {"relevant_ids": ["kb1"]}) for e in ls.evaluators_for("knowledge")}
    assert [s["score"] for s in scores.values()] == [1.0, 1.0, 1.0]
    wrong = ls.cited_a_correct_article(out, {"relevant_ids": ["other"]})
    assert wrong["score"] == 0.0


def test_gap_target_and_the_decline_score() -> None:
    class Driver:
        def run_gap(self, case: GapCase) -> Any:
            return SimpleNamespace(
                escalated=True, answered=False, routed_correctly=True, seconds=0.5
            )

    out = ls.gap_target(Driver())({"id": "g1", "topic": "t", "question": "q"})  # type: ignore[arg-type]
    assert ls.declined_correctly(out, {})["score"] == 1.0
    bad = ls.declined_correctly({"escalated": False, "answered": True}, {})
    assert bad["score"] == 0.0 and bad["comment"]


def test_conversation_target_runs_the_stored_case_and_reports_failures() -> None:
    case = load_conversation_cases(EVAL_DIR / "conversation_cases.json")[0]
    ran: list[str] = []

    class Driver:
        def run_case(self, c: Any) -> Any:
            ran.append(c.id)
            return SimpleNamespace(passed=False, failures=["no ticket"], latencies=[1.0, 0.5])

    inputs = ls.conversation_examples([case])[0].inputs
    out = ls.conversation_target(Driver())(inputs)  # type: ignore[arg-type]
    assert ran == [case.id] and out["seconds"] == 1.5
    score = ls.passed(out, {})
    assert score["score"] == 0.0 and score["comment"] == "no ticket"
