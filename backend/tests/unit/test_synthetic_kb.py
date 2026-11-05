import json
import re
from pathlib import Path
from typing import Any

import pytest

from tier0.rag.gaps import load_gap_topics, matching_topic_ids

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_DIR = REPO_ROOT / "data" / "knowledge_base" / "synthetic"
GAPS_FILE = REPO_ROOT / "data" / "evaluation" / "knowledge_gaps.json"
REQUIRED_FIELDS = {
    "document_id",
    "title",
    "category",
    "source",
    "content",
    "last_updated",
    "synthetic",
}
CATEGORIES = {"Onboarding", "Payroll & Compensation", "HR Policy"}

# Spec FR-RAG-01 topic coverage: every keyword group must appear in at least one entry.
REQUIRED_TOPICS = {
    "casual leave": r"casual leave",
    "sick leave": r"sick leave",
    "earned leave": r"earned leave",
    "work from home": r"work(ing)? from home",
    "reimbursement": r"reimburs",
    "code of conduct": r"code of conduct",
    "anti-harassment": r"harassment",
    "performance review": r"performance review|annual review",
    "day-one checklist": r"first day",
    "equipment allocation": r"laptop",
    "benefits enrollment": r"enroll",
    "buddy programme": r"buddy",
    "probation": r"probation",
    "pay-day schedule": r"last working day of each month",
    "tax declaration": r"tax declaration",
    "salary structure": r"base pay",
    "stock-option vesting": r"vest",
}


@pytest.fixture(scope="module")
def entries() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for path in sorted(SYNTHETIC_DIR.glob("*.json")):
        loaded.extend(json.loads(path.read_text(encoding="utf-8")))
    return loaded


def test_at_least_fifty_entries(entries: list[dict[str, Any]]) -> None:
    assert len(entries) >= 50


def test_required_fields_and_synthetic_flag(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        assert set(entry) == REQUIRED_FIELDS, entry.get("document_id")
        assert entry["synthetic"] is True
        assert entry["content"].strip() and entry["title"].strip()
        assert entry["category"] in CATEGORIES


def test_document_ids_are_unique(entries: list[dict[str, Any]]) -> None:
    ids = [entry["document_id"] for entry in entries]
    assert len(ids) == len(set(ids))


def test_all_categories_are_covered(entries: list[dict[str, Any]]) -> None:
    assert {entry["category"] for entry in entries} == CATEGORIES


@pytest.mark.parametrize("topic", REQUIRED_TOPICS)
def test_spec_topic_is_covered(entries: list[dict[str, Any]], topic: str) -> None:
    pattern = re.compile(REQUIRED_TOPICS[topic], re.IGNORECASE)
    assert any(pattern.search(e["title"] + " " + e["content"]) for e in entries), topic


def test_no_knowledge_gap_content(entries: list[dict[str, Any]]) -> None:
    topics = load_gap_topics(GAPS_FILE)
    offenders = [
        (entry["document_id"], hits)
        for entry in entries
        if (hits := matching_topic_ids(topics, entry["title"], entry["content"]))
    ]
    assert not offenders, offenders


def test_no_urls_or_contact_details(entries: list[dict[str, Any]]) -> None:
    forbidden = re.compile(r"https?://|www\.|@\w+\.\w+|\+?\d[\d\s().-]{8,}\d")
    for entry in entries:
        text = entry["title"] + " " + entry["content"]
        assert not forbidden.search(text), entry["document_id"]


def test_no_borrowed_company_names(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        assert "kreeda" not in (entry["title"] + entry["content"]).lower()
