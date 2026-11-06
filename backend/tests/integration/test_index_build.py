import json
import shutil
from pathlib import Path

import pytest

from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import (
    COLLECTION_NAME,
    IndexNotFoundError,
    build_index,
    compute_content_hash,
    open_collection,
    read_index_hash,
)
from tier0.rag.loader import load_knowledge_base
from tier0.rag.schema import KnowledgeRecord

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"


class OtherModelEmbedder(FakeEmbedder):
    model_id = "fake-other-model"


class ExplodingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding service down")


@pytest.fixture(scope="module")
def records() -> list[KnowledgeRecord]:
    return load_knowledge_base(FIXTURE_DIR)


def test_hash_ignores_record_order_but_not_content_or_model(records: list[KnowledgeRecord]) -> None:
    base = compute_content_hash(records, "m")
    assert base == compute_content_hash(list(reversed(records)), "m")
    assert base != compute_content_hash(records, "other")
    edited = [records[0].model_copy(update={"content": records[0].content + "!"}), *records[1:]]
    assert base != compute_content_hash(edited, "m")


def test_build_creates_a_queryable_index(tmp_path: Path, records: list[KnowledgeRecord]) -> None:
    embedder = FakeEmbedder()
    result = build_index(records, embedder, tmp_path / "chroma")
    assert not result.skipped and result.record_count == len(records)

    collection = open_collection(tmp_path / "chroma")
    assert collection.name == COLLECTION_NAME and collection.count() == len(records)
    assert (collection.metadata or {})["content_hash"] == result.content_hash

    hit = collection.query(
        query_embeddings=embedder.embed(["I forgot my password and cannot reset it"]),  # type: ignore[arg-type]
        n_results=3,
    )
    top_titles = [m["title"] for m in (hit["metadatas"] or [[]])[0]]
    assert any("password" in str(title).lower() for title in top_titles), top_titles


def test_metadata_round_trips(tmp_path: Path, records: list[KnowledgeRecord]) -> None:
    build_index(records, FakeEmbedder(), tmp_path / "chroma")
    collection = open_collection(tmp_path / "chroma")
    synthetic = next(r for r in records if r.synthetic)
    got = collection.get(ids=[synthetic.document_id])
    metadata = (got["metadatas"] or [{}])[0]
    assert metadata["category"] == synthetic.category
    assert metadata["synthetic"] is True
    assert metadata["last_updated"] == synthetic.last_updated.isoformat()  # type: ignore[union-attr]
    assert (got["documents"] or [""])[0] == synthetic.content


def test_second_build_with_same_inputs_is_skipped(
    tmp_path: Path, records: list[KnowledgeRecord]
) -> None:
    first = build_index(records, FakeEmbedder(), tmp_path / "chroma")
    second = build_index(records, FakeEmbedder(), tmp_path / "chroma")
    assert second.skipped and second.content_hash == first.content_hash


def test_changed_content_or_model_triggers_a_rebuild(
    tmp_path: Path, records: list[KnowledgeRecord]
) -> None:
    index = tmp_path / "chroma"
    first = build_index(records, FakeEmbedder(), index)

    edited = [records[0].model_copy(update={"content": "Brand new text."}), *records[1:]]
    second = build_index(edited, FakeEmbedder(), index)
    assert not second.skipped and second.content_hash != first.content_hash
    assert open_collection(index).get(ids=[records[0].document_id])["documents"] == [
        "Brand new text."
    ]

    third = build_index(edited, OtherModelEmbedder(), index)
    assert not third.skipped and read_index_hash(index) == (third.content_hash, len(records))


def test_removed_records_leave_no_stale_entries(
    tmp_path: Path, records: list[KnowledgeRecord]
) -> None:
    index = tmp_path / "chroma"
    build_index(records, FakeEmbedder(), index)
    build_index(records[:10], FakeEmbedder(), index)
    assert open_collection(index).count() == 10


def test_failed_first_build_leaves_nothing_behind(
    tmp_path: Path, records: list[KnowledgeRecord]
) -> None:
    with pytest.raises(RuntimeError, match="embedding service down"):
        build_index(records, ExplodingEmbedder(), tmp_path / "chroma")
    assert list(tmp_path.iterdir()) == []


def test_failed_rebuild_keeps_the_previous_index(
    tmp_path: Path, records: list[KnowledgeRecord]
) -> None:
    index = tmp_path / "chroma"
    good = build_index(records, FakeEmbedder(), index)
    edited = [records[0].model_copy(update={"content": "changed"}), *records[1:]]
    with pytest.raises(RuntimeError):
        build_index(edited, ExplodingEmbedder(), index)
    assert read_index_hash(index) == (good.content_hash, len(records))
    assert not (tmp_path / "chroma.building").exists()


def test_cannot_build_from_zero_records(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zero records"):
        build_index([], FakeEmbedder(), tmp_path / "chroma")


def test_missing_or_foreign_directories_are_not_indexes(tmp_path: Path) -> None:
    with pytest.raises(IndexNotFoundError):
        open_collection(tmp_path / "nothing")
    assert read_index_hash(tmp_path / "nothing") is None
    shutil.copytree(FIXTURE_DIR, tmp_path / "plain")
    assert read_index_hash(tmp_path / "plain") is None


def test_fixture_file_is_valid_json() -> None:
    assert json.loads((FIXTURE_DIR / "kb_fixture.json").read_text(encoding="utf-8"))
