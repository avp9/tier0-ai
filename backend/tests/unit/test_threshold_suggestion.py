import pytest

from tier0.rag.evaluation import suggest_threshold


def test_clean_separation_puts_the_threshold_between_the_groups() -> None:
    suggestion = suggest_threshold([0.6, 0.7, 0.8], [0.2, 0.3])
    assert suggestion.separable
    assert suggestion.threshold == pytest.approx(0.45)
    assert suggestion.answerable_kept == 1.0 and suggestion.gaps_rejected == 1.0


def test_overlap_picks_the_best_compromise_and_says_so() -> None:
    suggestion = suggest_threshold([0.4, 0.5, 0.6, 0.7], [0.2, 0.3, 0.45])
    assert not suggestion.separable
    assert suggestion.threshold == pytest.approx(0.5)
    assert suggestion.answerable_kept == 0.75 and suggestion.gaps_rejected == 1.0


def test_needs_both_groups() -> None:
    with pytest.raises(ValueError, match="both"):
        suggest_threshold([], [0.1])
    with pytest.raises(ValueError, match="both"):
        suggest_threshold([0.5], [])
