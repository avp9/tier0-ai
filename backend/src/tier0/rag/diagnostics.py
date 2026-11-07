from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from tier0.rag.evaluation import (
    evaluate_gap_separation,
    evaluate_retrieval,
    load_cases,
    suggest_threshold,
)
from tier0.rag.gaps import load_gap_topics
from tier0.rag.retriever import Retriever

log = logging.getLogger(__name__)

CASES_FILE = "retrieval_cases.json"
GAPS_FILE = "knowledge_gaps.json"
CACHE_SECONDS = 600  # a result this old is still shown without re-running the check
MIN_RERUN_SECONDS = 30  # "run again" cannot be used more often than this

# Where the evaluation questions live when running from a checkout: <repo>/data/evaluation.
REPO_EVALUATION_DIR = Path(__file__).resolve().parents[4] / "data" / "evaluation"


class RankMetric(BaseModel):
    k: int
    hit: float  # share of questions with a correct article in the top k
    recall: float  # average share of the correct articles found in the top k


class KindRow(BaseModel):
    kind: str
    hit: list[RankMetric]  # only ``k`` and ``hit`` are meaningful per topic; recall repeats hit


class MissedQuestion(BaseModel):
    id: str
    query: str


class Separation(BaseModel):
    answerable_min: float
    answerable_median: float
    gap_max: float
    gap_median: float
    separable: bool


class ThresholdAdvice(BaseModel):
    current: float
    suggested: float
    answerable_kept: float
    gaps_rejected: float
    separable: bool


class RetrievalReportOut(BaseModel):
    """The output of ``inspect_retrieval.py --evaluate``, as data."""

    generated_at: datetime
    duration_s: float
    embedding_model: str
    cases: int
    gap_questions: int
    ranks: list[RankMetric]
    mrr: float
    by_kind: list[KindRow]
    misses_at: int
    misses: list[MissedQuestion]
    separation: Separation
    threshold: ThresholdAdvice


class EvaluationDataMissing(Exception):
    """This deployment does not include the evaluation questions."""


class TooSoon(Exception):
    """The check was run again before it is allowed to be."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"wait {retry_after} seconds")
        self.retry_after = retry_after


def build_report(
    retriever: Retriever,
    evaluation_dir: Path,
    *,
    current_threshold: float,
    embedding_model: str,
    clock: Callable[[], float] = time.perf_counter,
) -> RetrievalReportOut:
    """Run the retrieval evaluation and gap-separation check and describe the result."""
    cases_path, gaps_path = evaluation_dir / CASES_FILE, evaluation_dir / GAPS_FILE
    if not cases_path.is_file() or not gaps_path.is_file():
        raise EvaluationDataMissing(str(evaluation_dir))
    started = clock()
    report = evaluate_retrieval(load_cases(cases_path), retriever)
    separation = evaluate_gap_separation(load_gap_topics(gaps_path), report, retriever)
    advice = suggest_threshold(
        separation.answerable_scores, [g.top_score for g in separation.gap_scores]
    )
    largest = max(report.ks)
    return RetrievalReportOut(
        generated_at=datetime.now(UTC),
        duration_s=round(clock() - started, 1),
        embedding_model=embedding_model,
        cases=len(report.results),
        gap_questions=len(separation.gap_scores),
        ranks=[
            RankMetric(k=k, hit=report.hit_rate[k], recall=report.mean_recall[k]) for k in report.ks
        ],
        mrr=report.mrr,
        by_kind=[
            KindRow(
                kind=kind,
                hit=[RankMetric(k=k, hit=rate, recall=rate) for k, rate in sorted(per_k.items())],
            )
            for kind, per_k in sorted(report.by_kind.items())
        ],
        misses_at=largest,
        misses=[MissedQuestion(id=m.case.id, query=m.case.query) for m in report.misses(largest)],
        separation=Separation(
            answerable_min=separation.answerable_min,
            answerable_median=separation.answerable_median,
            gap_max=separation.gap_max,
            gap_median=separation.gap_median,
            separable=separation.separable,
        ),
        threshold=ThresholdAdvice(
            current=current_threshold,
            suggested=advice.threshold,
            answerable_kept=advice.answerable_kept,
            gaps_rejected=advice.gaps_rejected,
            separable=advice.separable,
        ),
    )


class RetrievalDiagnostics:
    """Runs the check on demand and remembers the answer, because it calls the embedding service
    once per question: a recent result is reused, and "run again" is limited."""

    def __init__(
        self,
        retriever: Retriever,
        evaluation_dir: Path,
        *,
        current_threshold: float,
        embedding_model: str,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._retriever = retriever
        self._dir = evaluation_dir
        self._threshold = current_threshold
        self._model = embedding_model
        self._now = now
        self._lock = threading.Lock()  # one run at a time; the rest wait and share its result
        self._cached: RetrievalReportOut | None = None
        self._cached_at = 0.0

    @property
    def available(self) -> bool:
        return (self._dir / CASES_FILE).is_file() and (self._dir / GAPS_FILE).is_file()

    def report(self, *, refresh: bool = False) -> RetrievalReportOut:
        with self._lock:
            age = self._now() - self._cached_at
            if self._cached is not None:
                if refresh and age < MIN_RERUN_SECONDS:
                    raise TooSoon(min(MIN_RERUN_SECONDS, int(MIN_RERUN_SECONDS - age) + 1))
                if not refresh and age < CACHE_SECONDS:
                    return self._cached
            self._cached = build_report(
                self._retriever,
                self._dir,
                current_threshold=self._threshold,
                embedding_model=self._model,
            )
            self._cached_at = self._now()
            log.info("retrieval check finished", extra={"fields": {"cases": self._cached.cases}})
            return self._cached
