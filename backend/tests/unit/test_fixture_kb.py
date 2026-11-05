import re
from pathlib import Path

from tier0.rag.gaps import load_gap_topics, matching_topic_ids
from tier0.rag.loader import load_knowledge_base

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"
KB_DIR = REPO_ROOT / "data" / "knowledge_base"
GAPS_FILE = REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"


def test_fixture_is_small_and_covers_every_category() -> None:
    records = load_knowledge_base(FIXTURE_DIR)
    assert 55 <= len(records) <= 70
    assert {r.category for r in records} == {
        "IT Support",
        "HR Policy",
        "Onboarding",
        "Payroll & Compensation",
    }


def test_fixture_matches_the_real_knowledge_base() -> None:
    real = {r.document_id: r for r in load_knowledge_base(KB_DIR)}
    for record in load_knowledge_base(FIXTURE_DIR):
        assert real.get(record.document_id) == record, record.document_id


def test_fixture_answers_are_real_answers_not_labels() -> None:
    label_only = re.compile(r"Answer: Ticket category: [^\n]*$")
    for record in load_knowledge_base(FIXTURE_DIR):
        assert not label_only.search(record.content), record.document_id


def test_fixture_contains_no_knowledge_gap_content() -> None:
    topics = load_gap_topics(GAPS_FILE)
    for record in load_knowledge_base(FIXTURE_DIR):
        assert not matching_topic_ids(topics, record.title, record.content), record.document_id
