from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_harness import ALICE, BOB, Harness
from diagnostics_support import ScriptedRetriever, write_evaluation_files
from fastapi.testclient import TestClient
from test_api_chat import make_settings, sign_in

from tier0.api.app import create_app
from tier0.rag.diagnostics import MIN_RERUN_SECONDS, RetrievalDiagnostics
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("diag-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


class Clock:
    now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def client_for(
    migrated_database: str, retriever: Retriever, diagnostics: RetrievalDiagnostics | None
) -> Any:
    agent = Harness(retriever).agent
    return TestClient(
        create_app(make_settings(migrated_database), agent=agent, diagnostics=diagnostics)
    )


@pytest.fixture
def scripted(
    migrated_database: str, retriever: Retriever, tmp_path: Path, clock: Clock
) -> Iterator[Any]:
    scripted_retriever = ScriptedRetriever()
    service = RetrievalDiagnostics(
        scripted_retriever,  # type: ignore[arg-type]
        write_evaluation_files(tmp_path),
        current_threshold=0.3,
        embedding_model="test-model",
        now=clock,
    )
    with client_for(migrated_database, retriever, service) as client:
        sign_in(client, ALICE)
        client.scripted = scripted_retriever
        yield client


def test_the_check_needs_a_signed_in_employee(
    migrated_database: str, retriever: Retriever, tmp_path: Path
) -> None:
    service = RetrievalDiagnostics(
        ScriptedRetriever(),  # type: ignore[arg-type]
        write_evaluation_files(tmp_path),
        current_threshold=0.3,
        embedding_model="m",
    )
    with client_for(migrated_database, retriever, service) as anonymous:
        assert anonymous.get("/api/diagnostics/retrieval").status_code == 401


def test_a_signed_in_employee_gets_the_report(scripted: Any) -> None:
    response = scripted.get("/api/diagnostics/retrieval")
    assert response.status_code == 200
    body = response.json()
    assert body["cases"] == 3 and body["gap_questions"] == 2
    assert body["embedding_model"] == "test-model"
    assert [r["k"] for r in body["ranks"]] == [1, 3, 5]
    assert set(body["ranks"][0]) == {"k", "hit", "recall"}
    assert body["threshold"]["current"] == 0.3
    assert body["misses"] == [{"id": "c3", "query": "never found"}]
    assert {row["kind"] for row in body["by_kind"]} == {"it", "handbook"}


def test_everyone_signed_in_may_read_it_and_shares_the_cached_run(
    scripted: Any, migrated_database: str, retriever: Retriever
) -> None:
    assert scripted.get("/api/diagnostics/retrieval").status_code == 200
    searches = len(scripted.scripted.searches)
    assert scripted.get("/api/diagnostics/retrieval").status_code == 200
    assert len(scripted.scripted.searches) == searches  # no second run: the result was kept


def test_run_again_too_soon_is_a_429_with_a_retry_after(scripted: Any) -> None:
    scripted.get("/api/diagnostics/retrieval")
    response = scripted.get("/api/diagnostics/retrieval?refresh=true")
    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= MIN_RERUN_SECONDS
    assert "Try again in" in response.json()["detail"]


def test_run_again_works_after_the_wait(scripted: Any, clock: Clock) -> None:
    scripted.get("/api/diagnostics/retrieval")
    searches = len(scripted.scripted.searches)
    clock.now += MIN_RERUN_SECONDS + 1
    assert scripted.get("/api/diagnostics/retrieval?refresh=true").status_code == 200
    assert len(scripted.scripted.searches) == 2 * searches


def test_a_deployment_without_the_evaluation_questions_says_so(
    migrated_database: str, retriever: Retriever, tmp_path: Path
) -> None:
    service = RetrievalDiagnostics(
        ScriptedRetriever(),  # type: ignore[arg-type]
        tmp_path / "missing",
        current_threshold=0.3,
        embedding_model="m",
    )
    with client_for(migrated_database, retriever, service) as client:
        sign_in(client, BOB)
        response = client.get("/api/diagnostics/retrieval")
        assert response.status_code == 404
        assert response.json() == {
            "detail": "The evaluation questions are not part of this deployment."
        }


def test_when_chat_is_not_running_the_check_is_unavailable(
    migrated_database: str, retriever: Retriever
) -> None:
    with client_for(migrated_database, retriever, None) as client:
        sign_in(client, ALICE)
        response = client.get("/api/diagnostics/retrieval")
        assert response.status_code == 503


def test_a_failure_while_running_is_reported_without_details(
    migrated_database: str, retriever: Retriever, tmp_path: Path
) -> None:
    class Exploding(ScriptedRetriever):
        def search(self, query: str, k: int = 5) -> Any:
            raise RuntimeError("secret internal detail: key sk-123")

    service = RetrievalDiagnostics(
        Exploding(),  # type: ignore[arg-type]
        write_evaluation_files(tmp_path),
        current_threshold=0.3,
        embedding_model="m",
    )
    with client_for(migrated_database, retriever, service) as client:
        sign_in(client, ALICE)
        response = client.get("/api/diagnostics/retrieval")
        assert response.status_code == 503
        assert "secret" not in response.text and "sk-123" not in response.text
