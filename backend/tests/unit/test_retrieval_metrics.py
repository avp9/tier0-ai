import math

import pytest

from tier0.rag.evaluation import (
    GapScore,
    SeparationReport,
    hit_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

RELEVANT = {"a", "b"}


def test_recall_counts_relevant_documents_found_in_the_top_k() -> None:
    assert recall_at_k(["a", "x", "b"], RELEVANT, 1) == 0.5
    assert recall_at_k(["a", "x", "b"], RELEVANT, 3) == 1.0
    assert recall_at_k(["x", "y", "z"], RELEVANT, 3) == 0.0


def test_precision_divides_by_what_was_retrieved() -> None:
    assert precision_at_k(["a", "x", "b", "y"], RELEVANT, 4) == 0.5
    assert precision_at_k(["a"], RELEVANT, 5) == 1.0
    assert precision_at_k([], RELEVANT, 3) == 0.0


def test_duplicates_are_not_double_counted() -> None:
    assert recall_at_k(["a", "a", "a"], RELEVANT, 3) == 0.5


def test_hit_is_true_when_any_relevant_document_is_in_the_top_k() -> None:
    assert hit_at_k(["x", "b"], RELEVANT, 2)
    assert not hit_at_k(["x", "b"], RELEVANT, 1)


def test_reciprocal_rank_uses_the_first_relevant_position() -> None:
    assert reciprocal_rank(["x", "x", "b", "a"], RELEVANT) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a"], RELEVANT) == 1.0
    assert reciprocal_rank(["x"], RELEVANT) == 0.0


def test_empty_relevant_set_scores_zero_rather_than_dividing_by_zero() -> None:
    assert recall_at_k(["a"], set(), 3) == 0.0


def gap(score: float) -> GapScore:
    return GapScore(topic_id="t", question="q", top_score=score, top_title="title")


def test_separation_report_summarises_both_distributions() -> None:
    report = SeparationReport(gap_scores=[gap(0.2), gap(0.4)], answerable_scores=[0.6, 0.8, 0.7])
    assert math.isclose(report.gap_max, 0.4)
    assert math.isclose(report.gap_median, 0.3)
    assert math.isclose(report.answerable_min, 0.6)
    assert math.isclose(report.answerable_median, 0.7)
    assert report.separable


def test_overlapping_scores_are_reported_as_not_separable() -> None:
    report = SeparationReport(gap_scores=[gap(0.7)], answerable_scores=[0.6, 0.9])
    assert not report.separable


def test_empty_separation_report_does_not_crash() -> None:
    report = SeparationReport(gap_scores=[], answerable_scores=[])
    assert report.gap_max == report.gap_median == report.answerable_min == 0.0
