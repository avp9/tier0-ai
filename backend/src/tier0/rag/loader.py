from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tier0.rag.schema import KnowledgeRecord

TITLE_MAX_CHARS = 120


class KnowledgeBaseError(Exception):
    """The knowledge base files are missing, malformed, or inconsistent."""


def stable_document_id(source: str, question: str, answer: str) -> str:
    digest = hashlib.sha256(f"{source}\x00{question}\x00{answer}".encode()).hexdigest()
    return f"qa-{digest[:16]}"


def _qa_to_document(raw: dict[str, Any]) -> dict[str, Any]:
    question = str(raw.get("question") or "").strip()
    answer = str(raw.get("answer") or "").strip()
    source = str(raw.get("source") or "").strip()
    return {
        "document_id": stable_document_id(source, question, answer),
        "title": question[:TITLE_MAX_CHARS],
        "category": raw.get("category"),
        "source": source,
        "content": f"Question: {question}\nAnswer: {answer}" if question and answer else "",
    }


def _read_json(path: Path) -> Any:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise KnowledgeBaseError(f"{path}: cannot read JSON ({error})") from error


def _knowledge_files(directory: Path) -> list[Path]:
    files = [*directory.rglob("*.json"), *directory.rglob("*.json.gz")]
    return sorted(files)


def _raw_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    data = _read_json(path)
    if not isinstance(data, list):
        raise KnowledgeBaseError(f"{path}: expected a JSON list of records")
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise KnowledgeBaseError(f"{path}[{index}]: expected an object")
        yield index, item


def load_knowledge_base(directory: Path) -> list[KnowledgeRecord]:
    """Load and validate every knowledge file under ``directory``.

    Two shapes are accepted: document records (the KnowledgeRecord fields) and
    question/answer pairs, which receive a stable content-derived document_id.
    """
    if not directory.is_dir():
        raise KnowledgeBaseError(f"{directory}: not a directory")
    files = _knowledge_files(directory)
    if not files:
        raise KnowledgeBaseError(f"{directory}: no knowledge base files found")

    records: list[KnowledgeRecord] = []
    seen: dict[str, str] = {}
    for path in files:
        for index, raw in _raw_records(path):
            where = f"{path}[{index}]"
            document = _qa_to_document(raw) if "question" in raw else raw
            try:
                record = KnowledgeRecord.model_validate(document)
            except ValidationError as error:
                raise KnowledgeBaseError(f"{where}: {error}") from error
            if record.document_id in seen:
                raise KnowledgeBaseError(
                    f"{where}: duplicate document_id {record.document_id!r} "
                    f"(first seen at {seen[record.document_id]})"
                )
            seen[record.document_id] = where
            records.append(record)
    return records
