import gzip
import json
from pathlib import Path

from tier0.rag.normalize import is_borrowed_source, rewrite_company, should_drop

DATASET = Path(__file__).resolve().parents[3] / "data" / "knowledge_base" / "qa_dataset.json.gz"


def test_company_name_is_rewritten_in_all_spellings() -> None:
    assert rewrite_company("Kreeda Labs provides POSH training.") == (
        "BuzzTech Solutions provides POSH training."
    )
    assert rewrite_company("kreeda labs' policy") == "BuzzTech Solutions' policy"
    assert rewrite_company("KreedaLabs values trust") == "BuzzTech Solutions values trust"


def test_company_emails_become_fictional_addresses() -> None:
    assert rewrite_company("Write to ethics@kreedalabs.com.") == "Write to ethics@buzztech.example."


def test_unrelated_text_is_unchanged() -> None:
    text = "Reset your password from the login page."
    assert rewrite_company(text) == text


def test_entries_naming_individuals_are_dropped() -> None:
    assert should_drop("Who approved this?", "Mr. Saurabh Bhansali, Founder & CEO, approved it.")
    assert should_drop("Who drafted it?", "The Sr. HR Executive drafted it.")


def test_entries_on_handbook_topics_are_dropped() -> None:
    assert should_drop("How long is a harassment inquiry?", "Up to 90 days.")
    assert should_drop("Can I carry forward comp off?", "No.")
    assert should_drop("Can I work from home?", "Yes.")


def test_other_compliance_entries_are_kept() -> None:
    assert not should_drop("What is the gift policy?", "Gifts above a set value must be declined.")


def test_only_borrowed_source_is_normalised() -> None:
    assert is_borrowed_source("strova-ai/hr-policies-qa-dataset")
    assert not is_borrowed_source("parth1609/IT_ticket_dataset")


def test_committed_knowledge_base_has_no_borrowed_identity() -> None:
    with gzip.open(DATASET, "rt", encoding="utf-8") as handle:
        records = json.load(handle)
    borrowed = [r for r in records if is_borrowed_source(r["source"])]
    assert borrowed, "expected some HR entries to remain"
    for record in borrowed:
        text = f"{record['question']} {record['answer']}"
        assert "kreeda" not in text.lower(), record["question"]
        assert not should_drop(record["question"], record["answer"]), record["question"]
