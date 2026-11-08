"""The generated question-and-answer set: quality rules, files, and its LangSmith scoring."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tier0.evalrun import langsmith_eval as ls
from tier0.evalrun.cases import GapCase, gap_cases
from tier0.evalrun.qa_dataset import (
    Plan,
    QAExample,
    build_dataset,
    classify,
    generate_one,
    load_jsonl,
    parse_generated,
    pick,
    problems_with,
    write_csv,
    write_jsonl,
)
from tier0.llm.fake import FakeChatModel
from tier0.rag.schema import KnowledgeRecord

EVAL_DIR = Path(__file__).resolve().parents[3] / "data" / "evaluation"


def record(
    doc_id: str,
    title: str,
    content: str,
    *,
    synthetic: bool = True,
    category: Any = "HR Policy",
) -> KnowledgeRecord:
    return KnowledgeRecord(
        document_id=doc_id,
        title=title,
        category=category,
        source="test",
        content=content,
        synthetic=synthetic,
    )


LEAVE = record("hr-1", "Casual Leave", "Employees get 12 days of casual leave each year.")
PROBATION = record("hr-2", "How long is the probation period", "Probation lasts six months.")


def reply_with(question: str) -> str:
    return json.dumps(
        {
            "question": question,
            "reference_answer": "Employees receive 12 days of casual leave per year.",
            "key_facts": ["12 days per year"],
        }
    )


def test_a_generated_reply_is_read_and_bad_ones_are_refused() -> None:
    assert parse_generated(reply_with("How many casual leave days do I get?")) is not None
    assert parse_generated("not json at all") is None
    assert parse_generated('{"question": "q"}') is None


def test_questions_that_copy_the_title_or_name_the_article_are_rejected() -> None:
    long_answer = "Employees receive twelve days each year."
    assert problems_with(
        "How long is the probation period?", long_answer, ["x"], PROBATION, "direct"
    )
    assert problems_with(
        "What does the article say about leave days?", long_answer, ["x"], LEAVE, "direct"
    )
    assert not problems_with(
        "How many days off can I take for personal reasons?", long_answer, ["x"], LEAVE, "direct"
    )
    assert problems_with("a b", long_answer, ["x"], LEAVE, "direct")  # too short
    assert not problems_with("casual leave days", long_answer, ["x"], LEAVE, "keyword")
    assert problems_with("How many days off do I get?", "short", ["x"], LEAVE, "direct")
    assert problems_with("How many days off do I get?", long_answer, [], LEAVE, "direct")


def test_a_rejected_attempt_is_retried_and_then_given_up() -> None:
    copy = reply_with("How long is the probation period?")
    replies = iter([copy, reply_with("How many months until I am confirmed?")])
    chat = FakeChatModel(lambda _s, _u, _j: next(replies))
    assert generate_one(chat, PROBATION, "direct") is not None  # second attempt passes
    stubborn = FakeChatModel(lambda _s, _u, _j: copy)
    assert generate_one(stubborn, PROBATION, "direct") is None


def test_the_plan_picks_the_requested_mix_reproducibly() -> None:
    records = [record(f"hr-{i}", f"Title {i}", "x" * 50) for i in range(10)]
    records += [
        record(f"qa-{i}", f"Problem number {i}", "y" * 400, synthetic=False, category="IT Support")
        for i in range(30)
    ]
    plan = Plan(handbook=10, handbook_scenarios=4, hr_qa=0, it_tickets=6, seed=1)
    first = pick(records, plan)
    assert first == pick(records, plan)
    styles = [style for _, style in first]
    assert styles.count("direct") == 10 and styles.count("scenario") == 4
    assert len(styles) == 10 + 4 + 6
    assert classify(records[0]) == "handbook"


def test_building_the_set_adds_neighbours_and_the_unanswerable_questions() -> None:
    chat = FakeChatModel(lambda _s, _u, _j: reply_with("How many days off can I take each year?"))
    gaps = [GapCase(id="g1", topic="projector", question="The projector will not turn on.")]
    examples = build_dataset(
        chat,
        [LEAVE],
        gaps,
        Plan(handbook=1, handbook_scenarios=0, hr_qa=0, it_tickets=0),
        neighbours=lambda doc_id: ["hr-copy", doc_id],
    )
    assert [e.should_answer for e in examples] == [True, False]
    assert examples[0].relevant_ids == ["hr-1", "hr-copy"]  # no duplicates
    assert examples[1].reference_answer == "" and examples[1].question_type == "unanswerable"


def test_the_files_round_trip_and_the_csv_is_flat(tmp_path: Path) -> None:
    examples = [
        QAExample(
            "a", "Q one?", "A one.", ["f1", "f2"], ["x", "y"], True, "handbook", "direct", "HR"
        ),
        QAExample("b", "Q two?", "", [], [], False, "gap", "unanswerable", "projector"),
    ]
    write_jsonl(tmp_path / "d.jsonl", examples)
    write_csv(tmp_path / "d.csv", examples)
    again = load_jsonl(tmp_path / "d.jsonl")
    assert again[0].key_facts == ["f1", "f2"] and not again[1].should_answer
    header, first, _ = (tmp_path / "d.csv").read_text().splitlines()[:3]
    assert header.startswith("id,question,reference_answer,key_facts,relevant_ids,should_answer")
    assert "f1 | f2" in first and "x | y" in first and ",true," in first


def test_the_committed_dataset_is_complete_and_well_formed() -> None:
    examples = load_jsonl(EVAL_DIR / "qa_dataset.jsonl")
    assert len({e.id for e in examples}) == len(examples)
    answerable = [e for e in examples if e.should_answer]
    assert len(answerable) >= 100 and len(examples) - len(answerable) == len(
        gap_cases(EVAL_DIR / "knowledge_gaps.json")
    )
    for e in answerable:
        assert e.reference_answer and e.key_facts and e.relevant_ids and len(e.question) >= 8
    csv_rows = (EVAL_DIR / "qa_dataset.csv").read_text(encoding="utf-8").splitlines()
    assert len(csv_rows) == len(examples) + 1 or "\n" in csv_rows[1]  # multi-line cells allowed


# --- the experiment's scoring ----------------------------------------------------------------

REFERENCE = {
    "should_answer": True,
    "reference_answer": "12 days.",
    "key_facts": ["12 days", "per year"],
    "relevant_ids": ["hr-1"],
}


def test_the_qa_target_returns_the_reply_and_what_it_cited() -> None:
    class Driver:
        def run_knowledge(self, case: Any) -> Any:
            return SimpleNamespace(
                reply="12 days.", answered=True, escalated=False, sources=["hr-1"], seconds=1.234
            )

    out = ls.qa_target(Driver())({"id": "q1", "question": "How many?"})  # type: ignore[arg-type]
    assert out["reply"] == "12 days." and out["cited_ids"] == ["hr-1"] and out["seconds"] == 1.23


def test_behaviour_and_source_scores_for_answerable_and_unanswerable_questions() -> None:
    answered = {"answered": True, "escalated": False, "cited_ids": ["hr-1"]}
    assert ls.qa_behaviour(answered, REFERENCE)["score"] == 1.0
    assert ls.qa_source(answered, REFERENCE)["score"] == 1.0
    assert ls.qa_source({**answered, "cited_ids": ["other"]}, REFERENCE)["score"] == 0.0
    refused = {"answered": False, "escalated": True, "cited_ids": []}
    assert ls.qa_behaviour(refused, REFERENCE)["score"] == 0.0  # should have answered
    gap = {"should_answer": False}
    assert ls.qa_behaviour(refused, gap)["score"] == 1.0
    assert ls.qa_behaviour(answered, gap)["score"] == 0.0  # answered what it should decline
    assert ls.qa_source(answered, gap) == {"results": []}  # not applicable


def test_the_judge_scores_key_facts_and_contradictions() -> None:
    def judge(reply: dict[str, Any]) -> Any:
        return ls.qa_correctness(FakeChatModel(lambda _s, _u, _j: json.dumps(reply)))

    outputs = {"question": "How many?", "reply": "12 days a year."}
    full = judge({"covered": [True, True], "contradicts": False, "reason": "ok"})
    scores = {r["key"]: r["score"] for r in full(outputs, REFERENCE)["results"]}
    assert scores == {"key_fact_coverage": 1.0, "correct": 1.0}
    half = judge({"covered": [True], "contradicts": False})(outputs, REFERENCE)
    scores = {r["key"]: r["score"] for r in half["results"]}
    assert scores == {"key_fact_coverage": 0.5, "correct": 0.0}  # a missing verdict is "no"
    wrong = judge({"covered": [True, True], "contradicts": True})(outputs, REFERENCE)
    assert [r["score"] for r in wrong["results"]] == [1.0, 0.0]
    skipped = judge({"covered": []})(outputs, {**REFERENCE, "should_answer": False})
    assert skipped == {"results": []}
    broken = FakeChatModel(lambda _s, _u, _j: "not json")
    result = ls.qa_correctness(broken)(outputs, REFERENCE)
    assert [r["score"] for r in result["results"]] == [0.0, 0.0]


def test_the_qa_dataset_is_uploaded_like_the_others() -> None:
    specs = ls.qa_examples(load_jsonl(EVAL_DIR / "qa_dataset.jsonl"))
    assert ls.DATASETS["qa"] == "tier0-qa" and len(specs) > 100
    assert "reference_answer" not in specs[0].inputs  # the model never sees the answer
    assert len(ls.evaluators_for("qa")) == 2
    assert len(ls.evaluators_for("qa", FakeChatModel(lambda _s, _u, _j: "{}"))) == 3
