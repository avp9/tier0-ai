import threading
import time
from pathlib import Path
from typing import Any

import pytest
from diagnostics_support import ScriptedRetriever, write_evaluation_files

from tier0.rag.diagnostics import (
    CACHE_SECONDS,
    MIN_RERUN_SECONDS,
    EvaluationDataMissing,
    RetrievalDiagnostics,
    TooSoon,
    build_report,
)

EVALUATION_COST = 5  # three cases and two gap questions, one search each


def report_for(tmp_path: Path) -> Any:
    return build_report(
        ScriptedRetriever(),  # type: ignore[arg-type]
        write_evaluation_files(tmp_path),
        current_threshold=0.3,
        embedding_model="test-model",
    )


def test_the_report_carries_the_evaluation_numbers(tmp_path: Path) -> None:
    report = report_for(tmp_path)
    assert (report.cases, report.gap_questions, report.embedding_model) == (3, 2, "test-model")
    by_k = {r.k: r for r in report.ranks}
    assert sorted(by_k) == [1, 3, 5]
    assert by_k[1].hit == pytest.approx(1 / 3) and by_k[1].recall == pytest.approx(1 / 3)
    assert by_k[3].hit == pytest.approx(2 / 3) and by_k[5].hit == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx((1 + 1 / 3 + 0) / 3)


def test_the_report_breaks_results_down_by_topic_and_lists_the_misses(tmp_path: Path) -> None:
    report = report_for(tmp_path)
    kinds = {row.kind: {m.k: m.hit for m in row.hit} for row in report.by_kind}
    assert kinds == {"handbook": {1: 0.0, 3: 0.0, 5: 0.0}, "it": {1: 0.5, 3: 1.0, 5: 1.0}}
    assert report.misses_at == 5
    assert [(m.id, m.query) for m in report.misses] == [("c3", "never found")]


def test_the_report_says_how_well_scores_separate_answerable_from_gap_questions(
    tmp_path: Path,
) -> None:
    separation = report_for(tmp_path).separation
    assert separation.answerable_min == pytest.approx(0.9 - 0.0) or separation.answerable_min < 0.9
    assert separation.gap_max > separation.answerable_min  # 0.5 against a lowest 0.4: they overlap
    assert separation.separable is False


def test_the_report_suggests_a_threshold_next_to_the_current_one(tmp_path: Path) -> None:
    advice = report_for(tmp_path).threshold
    assert advice.current == 0.3 and 0.0 < advice.suggested < 1.0
    assert 0.0 <= advice.answerable_kept <= 1.0 and 0.0 <= advice.gaps_rejected <= 1.0


def test_missing_evaluation_files_are_reported_not_crashed_on(tmp_path: Path) -> None:
    with pytest.raises(EvaluationDataMissing):
        build_report(
            ScriptedRetriever(),  # type: ignore[arg-type]
            tmp_path,
            current_threshold=0.3,
            embedding_model="m",
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def diagnostics(tmp_path: Path, clock: Clock) -> tuple[RetrievalDiagnostics, ScriptedRetriever]:
    retriever = ScriptedRetriever()
    return (
        RetrievalDiagnostics(
            retriever,  # type: ignore[arg-type]
            write_evaluation_files(tmp_path),
            current_threshold=0.3,
            embedding_model="m",
            now=clock,
        ),
        retriever,
    )


def test_a_recent_result_is_reused_without_calling_the_embedding_service_again(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, retriever = diagnostics(tmp_path, clock)
    first = service.report()
    clock.now += CACHE_SECONDS - 1
    assert service.report() is first
    assert len(retriever.searches) == EVALUATION_COST


def test_an_old_result_is_run_again(tmp_path: Path) -> None:
    clock = Clock()
    service, retriever = diagnostics(tmp_path, clock)
    service.report()
    clock.now += CACHE_SECONDS + 1
    service.report()
    assert len(retriever.searches) == 2 * EVALUATION_COST


def test_run_again_is_refused_when_it_comes_too_soon_and_says_how_long_to_wait(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, retriever = diagnostics(tmp_path, clock)
    service.report()
    clock.now += 10
    with pytest.raises(TooSoon) as soon:
        service.report(refresh=True)
    assert 0 < soon.value.retry_after <= MIN_RERUN_SECONDS
    assert len(retriever.searches) == EVALUATION_COST  # nothing was spent


def test_run_again_works_once_enough_time_has_passed(tmp_path: Path) -> None:
    clock = Clock()
    service, retriever = diagnostics(tmp_path, clock)
    service.report()
    clock.now += MIN_RERUN_SECONDS + 1
    service.report(refresh=True)
    assert len(retriever.searches) == 2 * EVALUATION_COST


def test_the_first_ever_run_may_ask_for_a_refresh(tmp_path: Path) -> None:
    service, retriever = diagnostics(tmp_path, Clock())
    service.report(refresh=True)
    assert len(retriever.searches) == EVALUATION_COST


def test_people_asking_at_the_same_moment_share_one_run(tmp_path: Path) -> None:
    class Slow(ScriptedRetriever):
        def search(self, query: str, k: int = 5) -> Any:
            time.sleep(0.02)
            return super().search(query, k)

    retriever = Slow()
    service = RetrievalDiagnostics(
        retriever,  # type: ignore[arg-type]
        write_evaluation_files(tmp_path),
        current_threshold=0.3,
        embedding_model="m",
    )
    results: list[Any] = []
    threads = [threading.Thread(target=lambda: results.append(service.report())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4 and all(r is results[0] for r in results)
    assert len(retriever.searches) == EVALUATION_COST


def test_it_knows_whether_the_evaluation_files_exist(tmp_path: Path) -> None:
    service = RetrievalDiagnostics(
        ScriptedRetriever(),  # type: ignore[arg-type]
        tmp_path / "nothing-here",
        current_threshold=0.3,
        embedding_model="m",
    )
    assert service.available is False
    assert diagnostics(tmp_path, Clock())[0].available is True
