"""The evaluation questions as LangSmith datasets, and experiments that score the real system on
them, so every change to retrieval, prompts or models can be compared run against run.

Four datasets, built from the files in ``data/evaluation`` (the same cases ``run_eval.py`` uses):

  tier0-retrieval      does search put a correct article in the top results?  (embeddings only)
  tier0-knowledge      does the agent answer, citing a correct article?
  tier0-gaps           does the agent decline questions the knowledge base cannot answer?
  tier0-conversations  do scripted conversations (routing, tickets, status, safety) behave?
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tier0.evalrun.cases import ConversationCase, GapCase, KnowledgeCase
from tier0.rag.evaluation import (
    RetrievalCase,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)

DATASETS = {
    "retrieval": "tier0-retrieval",
    "knowledge": "tier0-knowledge",
    "gaps": "tier0-gaps",
    "conversations": "tier0-conversations",
}
DESCRIPTIONS = {
    "retrieval": "Questions and the knowledge-base articles that answer them (search only).",
    "knowledge": "Questions the knowledge base should answer, citing one of the listed articles.",
    "gaps": "Questions about topics deliberately missing: the agent must offer a ticket.",
    "conversations": "Scripted conversations and what must be true afterwards.",
}
RETRIEVAL_KS = (1, 3, 5)

Target = Callable[[dict[str, Any]], dict[str, Any]]
Evaluation = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ExampleSpec:
    """One example of a dataset, with an id that stays the same each time it is uploaded."""

    case_id: str
    inputs: dict[str, Any]
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def example_id(dataset: str, case_id: str) -> uuid.UUID:
    """Stable, so uploading again updates an example instead of adding a copy."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"tier0:{dataset}:{case_id}")


# --- building the datasets from the case files -----------------------------------------------


def retrieval_examples(cases: Sequence[RetrievalCase]) -> list[ExampleSpec]:
    return [
        ExampleSpec(
            c.id,
            {"id": c.id, "question": c.query},
            {"relevant_ids": list(c.relevant_ids)},
            {"case_id": c.id, "kind": c.kind},
        )
        for c in cases
    ]


def knowledge_examples(cases: Sequence[KnowledgeCase]) -> list[ExampleSpec]:
    return [
        ExampleSpec(
            c.id,
            {"id": c.id, "question": c.question},
            {"relevant_ids": list(c.relevant_ids)},
            {"case_id": c.id},
        )
        for c in cases
    ]


def gap_examples(cases: Sequence[GapCase]) -> list[ExampleSpec]:
    return [
        ExampleSpec(
            c.id,
            {"id": c.id, "topic": c.topic, "question": c.question},
            {"should_answer": False},
            {"case_id": c.id, "topic": c.topic},
        )
        for c in cases
    ]


def conversation_examples(cases: Sequence[ConversationCase]) -> list[ExampleSpec]:
    return [
        ExampleSpec(
            c.id,
            {"id": c.id, "kind": c.kind, "case": c.model_dump(mode="json")},
            {"must_pass": True},
            {"case_id": c.id, "kind": c.kind},
        )
        for c in cases
    ]


class DatasetClient(Protocol):
    """The part of ``langsmith.Client`` that uploading uses."""

    def has_dataset(self, *, dataset_name: str) -> bool: ...
    def create_dataset(self, dataset_name: str, *, description: str | None = None) -> Any: ...
    def read_dataset(self, *, dataset_name: str) -> Any: ...
    def list_examples(self, *, dataset_id: Any, **kwargs: Any) -> Any: ...
    def create_examples(self, *, dataset_id: Any, examples: list[dict[str, Any]]) -> Any: ...
    def update_examples(self, *, dataset_id: Any, updates: list[dict[str, Any]]) -> Any: ...
    def delete_examples(self, *, example_ids: list[Any]) -> Any: ...


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    created: int
    updated: int
    removed: int


def sync_dataset(
    client: DatasetClient, key: str, specs: Sequence[ExampleSpec], *, limit: int | None = None
) -> SyncResult:
    """Make the LangSmith dataset hold exactly these examples: add new ones, refresh existing
    ones, remove ones whose case no longer exists. Safe to run again and again."""
    name = DATASETS[key]
    specs = list(specs)[:limit] if limit else list(specs)
    if not client.has_dataset(dataset_name=name):
        client.create_dataset(name, description=DESCRIPTIONS[key])
    dataset = client.read_dataset(dataset_name=name)
    existing = {str(e.id) for e in client.list_examples(dataset_id=dataset.id)}
    wanted = {str(example_id(name, s.case_id)): s for s in specs}

    new = [
        {"id": eid, "inputs": s.inputs, "outputs": s.outputs, "metadata": s.metadata}
        for eid, s in wanted.items()
        if eid not in existing
    ]
    changed = [
        {"id": eid, "inputs": s.inputs, "outputs": s.outputs, "metadata": s.metadata}
        for eid, s in wanted.items()
        if eid in existing
    ]
    stale = [eid for eid in existing if eid not in wanted]
    if new:
        client.create_examples(dataset_id=dataset.id, examples=new)
    if changed:
        client.update_examples(dataset_id=dataset.id, updates=changed)
    if stale and not limit:  # a capped upload is a sample: it must not delete the rest
        client.delete_examples(example_ids=stale)
    return SyncResult(name, len(new), len(changed), len(stale) if not limit else 0)


# --- what each experiment runs ------------------------------------------------------------------


class Searcher(Protocol):
    def search(self, query: str, k: int = 5) -> Any: ...


class Driver(Protocol):
    """The part of ``Evaluator`` the experiments use."""

    def run_knowledge(self, case: KnowledgeCase) -> Any: ...
    def run_gap(self, case: GapCase) -> Any: ...
    def run_case(self, case: ConversationCase) -> Any: ...


def retrieval_target(retriever: Searcher, k: int = max(RETRIEVAL_KS)) -> Target:
    def run(inputs: dict[str, Any]) -> dict[str, Any]:
        hits = retriever.search(inputs["question"], k=k)
        return {
            "retrieved_ids": [h.document_id for h in hits],
            "top_score": hits[0].score if hits else 0.0,
        }

    return run


def knowledge_target(driver: Driver) -> Target:
    def run(inputs: dict[str, Any]) -> dict[str, Any]:
        outcome = driver.run_knowledge(
            KnowledgeCase(id=inputs["id"], question=inputs["question"], relevant_ids=())
        )
        return {
            "answered": outcome.answered,
            "cited_ids": list(outcome.sources),
            "routed_correctly": outcome.routed_correctly,
            "seconds": round(outcome.seconds, 2),
        }

    return run


def gap_target(driver: Driver) -> Target:
    def run(inputs: dict[str, Any]) -> dict[str, Any]:
        outcome = driver.run_gap(
            GapCase(id=inputs["id"], topic=inputs["topic"], question=inputs["question"])
        )
        return {
            "escalated": outcome.escalated,
            "answered": outcome.answered,
            "routed_correctly": outcome.routed_correctly,
            "seconds": round(outcome.seconds, 2),
        }

    return run


def conversation_target(driver: Driver) -> Target:
    def run(inputs: dict[str, Any]) -> dict[str, Any]:
        outcome = driver.run_case(ConversationCase.model_validate(inputs["case"]))
        return {
            "passed": outcome.passed,
            "failures": list(outcome.failures),
            "seconds": round(sum(outcome.latencies), 2),
        }

    return run


# --- scoring: each takes what the target produced and what the dataset says is right -------------


def _score(key: str, value: bool | float, comment: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"key": key, "score": float(value)}
    if comment:
        result["comment"] = comment
    return result


def hit_evaluator(k: int) -> Evaluation:
    def hit(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
        found = hit_at_k(outputs["retrieved_ids"], set(reference_outputs["relevant_ids"]), k)
        return _score(f"hit@{k}", found)

    hit.__name__ = f"hit_at_{k}"
    return hit


def recall_evaluator(k: int) -> Evaluation:
    def recall(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
        value = recall_at_k(outputs["retrieved_ids"], set(reference_outputs["relevant_ids"]), k)
        return _score(f"recall@{k}", value)

    recall.__name__ = f"recall_at_{k}"
    return recall


def reciprocal_rank_evaluator(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    value = reciprocal_rank(outputs["retrieved_ids"], set(reference_outputs["relevant_ids"]))
    return _score("reciprocal_rank", value)


def answered(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    return _score("answered", outputs["answered"])


def cited_a_correct_article(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    relevant = set(reference_outputs["relevant_ids"])
    return _score("cited_correct_article", any(i in relevant for i in outputs["cited_ids"]))


def routed_as_a_question(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    return _score("routed_correctly", outputs["routed_correctly"])


def declined_correctly(
    outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    ok = bool(outputs["escalated"]) and not outputs["answered"]
    comment = None if ok else "answered, or did not offer a ticket"
    return _score("declined_correctly", ok, comment)


def passed(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    return _score("passed", outputs["passed"], "; ".join(outputs["failures"]) or None)


def evaluators_for(key: str) -> list[Evaluation]:
    table: dict[str, list[Evaluation]] = {
        "retrieval": [
            *(hit_evaluator(k) for k in RETRIEVAL_KS),
            *(recall_evaluator(k) for k in RETRIEVAL_KS),
            reciprocal_rank_evaluator,
        ],
        "knowledge": [answered, cited_a_correct_article, routed_as_a_question],
        "gaps": [declined_correctly, routed_as_a_question],
        "conversations": [passed],
    }
    return table[key]
