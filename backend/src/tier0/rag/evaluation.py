from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tier0.rag.gaps import GapTopic
from tier0.rag.retriever import Retriever

DEFAULT_KS = (1, 3, 5)


class RetrievalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_ids: tuple[str, ...] = Field(min_length=1)
    kind: Literal["handbook", "it", "compliance"]


def load_cases(path: Path) -> list[RetrievalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [RetrievalCase.model_validate(item) for item in raw["cases"]]


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    top = list(retrieved[:k])
    return len(set(top) & relevant) / len(top) if top else 0.0


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & relevant) / len(relevant) if relevant else 0.0


def hit_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> bool:
    return bool(set(retrieved[:k]) & relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    for position, document_id in enumerate(retrieved, start=1):
        if document_id in relevant:
            return 1.0 / position
    return 0.0


@dataclass(frozen=True)
class CaseResult:
    case: RetrievalCase
    retrieved_ids: tuple[str, ...]
    top_score: float
    recall: dict[int, float]
    hit: dict[int, bool]
    reciprocal_rank: float


@dataclass(frozen=True)
class RetrievalReport:
    ks: tuple[int, ...]
    results: list[CaseResult]
    mean_recall: dict[int, float] = field(default_factory=dict)
    hit_rate: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    by_kind: dict[str, dict[int, float]] = field(default_factory=dict)  # kind -> k -> hit rate

    def misses(self, k: int) -> list[CaseResult]:
        return [r for r in self.results if not r.hit[k]]


def evaluate_retrieval(
    cases: Sequence[RetrievalCase], retriever: Retriever, ks: Sequence[int] = DEFAULT_KS
) -> RetrievalReport:
    max_k = max(ks)
    results: list[CaseResult] = []
    for case in cases:
        hits = retriever.search(case.query, k=max_k)
        ids = [h.document_id for h in hits]
        relevant = set(case.relevant_ids)
        results.append(
            CaseResult(
                case=case,
                retrieved_ids=tuple(ids),
                top_score=hits[0].score if hits else 0.0,
                recall={k: recall_at_k(ids, relevant, k) for k in ks},
                hit={k: hit_at_k(ids, relevant, k) for k in ks},
                reciprocal_rank=reciprocal_rank(ids, relevant),
            )
        )
    count = len(results) or 1
    by_kind_hits: dict[str, dict[int, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        for k in ks:
            by_kind_hits[r.case.kind][k].append(r.hit[k])
    return RetrievalReport(
        ks=tuple(ks),
        results=results,
        mean_recall={k: sum(r.recall[k] for r in results) / count for k in ks},
        hit_rate={k: sum(r.hit[k] for r in results) / count for k in ks},
        mrr=sum(r.reciprocal_rank for r in results) / count,
        by_kind={
            kind: {k: sum(values) / len(values) for k, values in per_k.items()}
            for kind, per_k in by_kind_hits.items()
        },
    )


@dataclass(frozen=True)
class GapScore:
    topic_id: str
    question: str
    top_score: float
    top_title: str


@dataclass(frozen=True)
class SeparationReport:
    """How well top-1 similarity separates answerable questions from Knowledge Gap questions."""

    gap_scores: list[GapScore]
    answerable_scores: list[float]

    @property
    def gap_max(self) -> float:
        return max((g.top_score for g in self.gap_scores), default=0.0)

    @property
    def gap_median(self) -> float:
        return statistics.median(g.top_score for g in self.gap_scores) if self.gap_scores else 0.0

    @property
    def answerable_min(self) -> float:
        return min(self.answerable_scores, default=0.0)

    @property
    def answerable_median(self) -> float:
        return statistics.median(self.answerable_scores) if self.answerable_scores else 0.0

    @property
    def separable(self) -> bool:
        """True if one threshold could split every gap question from every answerable one."""
        return self.gap_max < self.answerable_min


def evaluate_gap_separation(
    topics: Sequence[GapTopic], report: RetrievalReport, retriever: Retriever
) -> SeparationReport:
    gap_scores: list[GapScore] = []
    for topic in topics:
        for question in topic.example_questions:
            hits = retriever.search(question, k=1)
            top = hits[0] if hits else None
            gap_scores.append(
                GapScore(
                    topic_id=topic.id,
                    question=question,
                    top_score=top.score if top else 0.0,
                    top_title=top.title if top else "",
                )
            )
    return SeparationReport(
        gap_scores=gap_scores, answerable_scores=[r.top_score for r in report.results]
    )


@dataclass(frozen=True)
class ThresholdSuggestion:
    threshold: float
    separable: bool
    answerable_kept: float  # share of answerable questions at or above the threshold
    gaps_rejected: float  # share of gap questions below the threshold


def suggest_threshold(
    answerable_scores: Sequence[float], gap_scores: Sequence[float]
) -> ThresholdSuggestion:
    """Pick the top-1 similarity cut-off that best splits answerable from gap questions."""
    if not answerable_scores or not gap_scores:
        raise ValueError("need both answerable and gap scores to suggest a threshold")

    def shares(t: float) -> tuple[float, float]:
        kept = sum(s >= t for s in answerable_scores) / len(answerable_scores)
        rejected = sum(s < t for s in gap_scores) / len(gap_scores)
        return kept, rejected

    if max(gap_scores) < min(answerable_scores):
        midpoint = (max(gap_scores) + min(answerable_scores)) / 2
        kept, rejected = shares(midpoint)
        return ThresholdSuggestion(midpoint, True, kept, rejected)

    best = min(set(answerable_scores) | set(gap_scores))
    best_balance = -1.0
    for candidate in sorted(set(answerable_scores) | set(gap_scores)):
        kept, rejected = shares(candidate)
        balance = (kept + rejected) / 2
        if balance > best_balance:
            best, best_balance = candidate, balance
    kept, rejected = shares(best)
    return ThresholdSuggestion(best, False, kept, rejected)
