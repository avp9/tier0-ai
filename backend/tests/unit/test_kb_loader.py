import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tier0.rag.loader import KnowledgeBaseError, load_knowledge_base, stable_document_id
from tier0.rag.schema import KnowledgeRecord

DOC: dict[str, Any] = {
    "document_id": "onb-900",
    "title": "Day-One Checklist",
    "category": "Onboarding",
    "source": "Test Guide",
    "content": "Arrive at 9:30 AM.",
    "last_updated": "2026-01-15",
    "synthetic": True,
}
QA: dict[str, Any] = {
    "ticketid": "TICKET_1",
    "question": "How do I reset my password?",
    "answer": "Use the self-service portal.",
    "category": "IT Support",
    "source": "test/dataset",
    "resolution_time": None,
    "subcategory": " ",
}


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_document_records_load_with_typed_fields(tmp_path: Path) -> None:
    write(tmp_path / "docs.json", [DOC])
    (record,) = load_knowledge_base(tmp_path)
    assert record.document_id == "onb-900"
    assert record.last_updated is not None and record.last_updated.year == 2026
    assert record.synthetic is True


def test_qa_pairs_get_stable_ids_and_combined_content(tmp_path: Path) -> None:
    with gzip.open(tmp_path / "qa.json.gz", "wt", encoding="utf-8") as handle:
        json.dump([QA], handle)
    (record,) = load_knowledge_base(tmp_path)
    assert record.document_id == stable_document_id("test/dataset", QA["question"], QA["answer"])
    assert record.title == "How do I reset my password?"
    assert (
        record.content
        == "Question: How do I reset my password?\nAnswer: Use the self-service portal."
    )
    assert record.last_updated is None and record.synthetic is False


def test_document_id_is_deterministic_and_content_sensitive() -> None:
    first = stable_document_id("s", "q", "a")
    assert first == stable_document_id("s", "q", "a")
    assert first != stable_document_id("s", "q", "b")
    assert first != stable_document_id("t", "q", "a")


def test_long_questions_are_truncated_in_the_title(tmp_path: Path) -> None:
    write(tmp_path / "qa.json", [{**QA, "question": "x" * 500}])
    (record,) = load_knowledge_base(tmp_path)
    assert len(record.title) == 120
    assert "x" * 500 in record.content


def test_both_shapes_can_coexist_in_subfolders(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub" / "docs.json", [DOC])
    write(tmp_path / "qa.json", [QA])
    assert len(load_knowledge_base(tmp_path)) == 2


def test_duplicate_document_ids_are_rejected(tmp_path: Path) -> None:
    write(tmp_path / "a.json", [DOC])
    write(tmp_path / "b.json", [DOC])
    with pytest.raises(KnowledgeBaseError, match="duplicate document_id 'onb-900'"):
        load_knowledge_base(tmp_path)


def test_identical_qa_pairs_are_rejected_as_duplicates(tmp_path: Path) -> None:
    write(tmp_path / "qa.json", [QA, {**QA, "ticketid": "TICKET_2"}])
    with pytest.raises(KnowledgeBaseError, match="duplicate document_id"):
        load_knowledge_base(tmp_path)


@pytest.mark.parametrize("field", ["content", "title", "source"])
def test_empty_required_text_is_rejected(tmp_path: Path, field: str) -> None:
    write(tmp_path / "docs.json", [{**DOC, field: "   "}])
    with pytest.raises(KnowledgeBaseError, match=field):
        load_knowledge_base(tmp_path)


@pytest.mark.parametrize("field", ["question", "answer"])
def test_qa_with_empty_question_or_answer_is_rejected(tmp_path: Path, field: str) -> None:
    write(tmp_path / "qa.json", [{**QA, field: ""}])
    with pytest.raises(KnowledgeBaseError, match="content"):
        load_knowledge_base(tmp_path)


def test_unknown_category_and_bad_date_and_extra_fields_are_rejected(tmp_path: Path) -> None:
    for bad in ({"category": "Cafeteria"}, {"last_updated": "yesterday"}, {"colour": "red"}):
        write(tmp_path / "docs.json", [{**DOC, **bad}])
        with pytest.raises(KnowledgeBaseError, match=r"docs\.json\[0\]"):
            load_knowledge_base(tmp_path)


def test_errors_name_the_file_and_record_index(tmp_path: Path) -> None:
    write(tmp_path / "docs.json", [DOC, {**DOC, "document_id": "onb-901", "content": ""}])
    with pytest.raises(KnowledgeBaseError, match=r"docs\.json\[1\]"):
        load_knowledge_base(tmp_path)


def test_malformed_json_and_wrong_shapes_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(KnowledgeBaseError, match="cannot read JSON"):
        load_knowledge_base(tmp_path)
    write(tmp_path / "broken.json", {"not": "a list"})
    with pytest.raises(KnowledgeBaseError, match="expected a JSON list"):
        load_knowledge_base(tmp_path)
    write(tmp_path / "broken.json", ["a string"])
    with pytest.raises(KnowledgeBaseError, match="expected an object"):
        load_knowledge_base(tmp_path)


def test_missing_or_empty_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeBaseError, match="not a directory"):
        load_knowledge_base(tmp_path / "missing")
    with pytest.raises(KnowledgeBaseError, match="no knowledge base files"):
        load_knowledge_base(tmp_path)


def test_records_are_immutable() -> None:
    record = KnowledgeRecord.model_validate(DOC)
    with pytest.raises(ValueError):
        record.title = "changed"
