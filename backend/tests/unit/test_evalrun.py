from pathlib import Path

import pytest
from agent_harness import Harness

from tier0.evalrun.cases import (
    ConversationCase,
    Expect,
    GapCase,
    KnowledgeCase,
    Step,
    gap_cases,
    load_conversation_cases,
)
from tier0.evalrun.report import Results, gate_failures, summarize, to_markdown
from tier0.evalrun.runner import Evaluator, percentile
from tier0.evalrun.usage import Prices, Usage, instrument
from tier0.fakes.stack import ScriptedChatModel
from tier0.intent.types import Intent
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.evaluation import (
    RetrievalReport,
    SeparationReport,
)
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"
EVAL_DIR = Path(__file__).resolve().parents[3] / "data" / "evaluation"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("eval-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


@pytest.fixture
def evaluator(retriever: Retriever) -> tuple[Evaluator, Harness]:
    harness = Harness(retriever)
    return Evaluator(harness.agent, harness.adapter), harness


def flow(**overrides: object) -> ConversationCase:
    base: dict[str, object] = {
        "id": "f",
        "kind": "ticket_flow",
        "steps": [Step(say="I want to raise a ticket")],
        "details": ["My laptop battery drains within an hour of unplugging it", "IT"],
        "confirmations": ["yes"],
        "expect_tickets": 1,
    }
    return ConversationCase.model_validate({**base, **overrides})


# --- the case files -------------------------------------------------------------------


def test_conversation_cases_file_is_valid_and_covers_every_kind() -> None:
    cases = load_conversation_cases(EVAL_DIR / "conversation_cases.json")
    kinds = {c.kind for c in cases}
    assert kinds == {"routing", "ticket_flow", "status", "safety"}
    assert len(cases) >= 20
    routed = {step.expect.intent for c in cases if c.kind == "routing" for step in c.steps}
    assert routed == set(Intent)  # every intent has a routing case


def test_gap_cases_come_from_every_gap_topic() -> None:
    cases = gap_cases(EVAL_DIR / "knowledge_gaps.json")
    assert len({c.topic for c in cases}) == 14
    assert all(c.question for c in cases)


def test_duplicate_case_ids_are_refused(tmp_path: Path) -> None:
    case = '{"id":"a","kind":"routing","steps":[{"say":"hi"}]}'
    path = tmp_path / "c.json"
    path.write_text(f'{{"cases":[{case},{case}]}}')
    with pytest.raises(ValueError, match="unique"):
        load_conversation_cases(path)


# --- measuring ------------------------------------------------------------------------


def test_percentile_uses_nearest_rank() -> None:
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.5) == 50.0
    assert percentile(values, 0.95) == 95.0
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.95) == 7.0


def test_usage_counts_calls_and_restores_the_originals() -> None:
    usage = Usage()
    original_embed = FakeEmbedder.embed
    original_complete = ScriptedChatModel.complete
    with instrument(usage):
        FakeEmbedder().embed(["hello", "there"])
        with pytest.raises(Exception, match="unexpected prompt"):
            ScriptedChatModel().complete("system", "user")
    assert (usage.embed_calls, usage.embed_chars) == (1, 10)
    assert usage.chat_calls == 1
    assert FakeEmbedder.embed is original_embed
    assert ScriptedChatModel.complete is original_complete
    assert Usage().estimate_dollars(Prices()) == 0.0
    assert Usage(chat_input_chars=4_000_000).estimate_dollars(Prices(chat_input=1.0)) == 1.0


# --- driving the agent ----------------------------------------------------------------


def test_a_confirmed_ticket_flow_passes(evaluator: tuple[Evaluator, Harness]) -> None:
    runner, harness = evaluator
    outcome = runner.run_case(flow())
    assert outcome.passed, outcome.failures
    assert len(harness.adapter.tickets) == 1


def test_a_declined_ticket_flow_creates_nothing(evaluator: tuple[Evaluator, Harness]) -> None:
    runner, harness = evaluator
    outcome = runner.run_case(flow(confirmations=["no"], expect_tickets=0))
    assert outcome.passed, outcome.failures
    assert harness.adapter.tickets == []


def test_seeded_tickets_do_not_count_as_created_before_confirmation(
    evaluator: tuple[Evaluator, Harness],
) -> None:
    runner, _ = evaluator
    seeded = [{"summary": "Existing"}]
    outcome = runner.run_case(flow(seed_tickets=seeded, expect_tickets=2))
    assert outcome.passed, outcome.failures


def test_a_ticket_created_before_confirmation_is_caught(
    evaluator: tuple[Evaluator, Harness],
) -> None:
    runner, harness = evaluator
    say = runner._say
    calls: list[str] = []

    def leaky(thread: str, text: str, email: str = "eval.user@buzztech.example") -> object:
        calls.append(text)
        if len(calls) == 2:  # something creates a ticket while the employee is still deciding
            harness.add_ticket("eval.user@buzztech.example", "Premature")
        return say(thread, text, email)

    runner._say = leaky  # type: ignore[method-assign, assignment]
    outcome = runner.run_case(flow(expect_tickets=None, confirmations=["no"]))
    assert not outcome.passed
    assert "existed before the employee confirmed" in " ".join(outcome.failures)


def test_a_wrong_expectation_is_reported_not_hidden(
    evaluator: tuple[Evaluator, Harness],
) -> None:
    runner, _ = evaluator
    outcome = runner.run_case(flow(expect_tickets=0))  # a ticket is created, 0 was expected
    assert not outcome.passed
    assert "expected 0 tickets, found 1" in outcome.failures[0]


def test_a_flow_that_never_reaches_confirmation_fails(
    evaluator: tuple[Evaluator, Harness],
) -> None:
    runner, _ = evaluator
    outcome = runner.run_case(flow(details=[], expect_tickets=0))
    assert not outcome.passed
    assert "never reached the confirmation step" in outcome.failures[0]


def test_expectations_on_intent_waiting_and_text(evaluator: tuple[Evaluator, Harness]) -> None:
    runner, _ = evaluator
    good = ConversationCase(
        id="r",
        kind="routing",
        steps=[Step(say="hello", expect=Expect(intent=Intent.SMALLTALK, awaiting=["none"]))],
    )
    assert runner.run_case(good).passed
    bad = ConversationCase(
        id="r2",
        kind="routing",
        steps=[
            Step(
                say="hello",
                expect=Expect(
                    intent=Intent.CREATE_TICKET,
                    awaiting=["confirm_ticket"],
                    reply_excludes=["hello"],
                ),
            )
        ],
    )
    outcome = runner.run_case(bad)
    assert not outcome.passed
    assert outcome.routing_checks == [False]
    assert [f.split()[0] for f in outcome.failures] == ["intent", "waiting", "reply"]


def test_status_cases_hide_internal_comments_and_other_users(
    evaluator: tuple[Evaluator, Harness],
) -> None:
    runner, _ = evaluator
    cases = load_conversation_cases(EVAL_DIR / "conversation_cases.json")
    for case in cases:
        if case.id in ("status-one", "status-not-others", "status-none"):
            outcome = runner.run_case(case)
            assert outcome.passed, (case.id, outcome.failures)


def test_knowledge_and_gap_outcomes(evaluator: tuple[Evaluator, Harness]) -> None:
    runner, _ = evaluator
    answered = runner.run_knowledge(
        KnowledgeCase(
            id="k",
            question="bring photo ID, proof of address and signed offer letter",
            relevant_ids=("nothing-matches",),
        )
    )
    assert answered.answered and not answered.cited_expected  # answered, but not the right source
    gap = runner.run_gap(
        GapCase(id="g", topic="gym", question="Does BuzzTech reimburse gym memberships?")
    )
    assert gap.escalated and not gap.answered


# --- the report -----------------------------------------------------------------------


def test_summary_and_gates(evaluator: tuple[Evaluator, Harness], retriever: Retriever) -> None:
    runner, _ = evaluator
    cases = load_conversation_cases(EVAL_DIR / "conversation_cases.json")
    outcomes = [runner.run_case(c) for c in cases if c.kind in ("safety", "status")]
    results = Results(
        retrieval=RetrievalReport(
            ks=(5,), results=[], mean_recall={5: 0.0}, hit_rate={5: 0.0}, mrr=0.0, by_kind={}
        ),
        separation=SeparationReport(gap_scores=[], answerable_scores=[]),
        knowledge=[],
        gaps=[],
        cases=outcomes,
        usage=Usage(),
        prices=Prices(),
        providers={"llm": "fake"},
    )
    summary = summarize(results)
    assert summary["knowledge"]["answer_rate"] is None  # nothing measured is not "0%"
    assert summary["usage"]["estimated_dollars"] is None  # no cost claimed for fakes
    assert summary["safety"]["cases"] == 4
    assert "n/a" in to_markdown(summary)
    assert gate_failures(summary) == []
    # a failed safety case must trip the gate
    summary["safety"]["pass_rate"] = 0.75
    assert "safety case failed" in gate_failures(summary)[0]
