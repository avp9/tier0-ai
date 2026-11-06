from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_harness import ALICE
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api_chat import FIXTURE_DIR, make_settings, sign_in

from tier0.api.app import create_app
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base

TOKEN = "e2e-test-token"  # noqa: S105
HEADERS = {"X-E2E-Token": TOKEN}


@pytest.fixture(scope="module")
def index_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("e2e-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), path)
    return path


def fake_settings(url: str, index: Path, **extra: Any) -> Any:
    values: dict[str, Any] = {
        "app_env": "e2e",
        "llm_provider": "fake",
        "ticketing_provider": "fake",
        "e2e_token": TOKEN,
        "vector_store_path": str(index),
        **extra,
    }
    return make_settings(url, **values)


@pytest.fixture
def client(migrated_database: str, index_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(fake_settings(migrated_database, index_path))) as c:
        yield c


def say(client: TestClient, message: str, thread_id: str | None = None) -> Any:
    body: dict[str, Any] = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_fake_stack_boots_and_creates_a_ticket_after_confirmation(client: TestClient) -> None:
    sign_in(client, ALICE)
    turn = say(client, "I want to raise a ticket")
    thread = turn["thread_id"]
    for _ in range(4):
        if (turn["pending_action"] or {}).get("type") != "collect_ticket_details":
            break
        turn = say(client, "My VPN keeps disconnecting. IT.", thread)
    assert turn["pending_action"]["type"] == "confirm_ticket"
    assert client.get("/api/_e2e/tickets", headers=HEADERS).json() == []
    done = say(client, "yes", thread)
    assert done["ticket"]["ticket_key"].startswith("SUP-")
    tickets = client.get("/api/_e2e/tickets", headers=HEADERS).json()
    assert [t["requester_email"] for t in tickets] == [ALICE]


def test_seeded_ticket_is_visible_to_its_requester(client: TestClient) -> None:
    body = {"email": ALICE, "summary": "Laptop will not boot", "status": "In Progress"}
    assert client.post("/api/_e2e/tickets", json=body, headers=HEADERS).status_code == 201
    sign_in(client, ALICE)
    reply = say(client, "What is the status of my ticket?")["reply"]
    assert "Laptop will not boot" in reply


def test_a_scripted_outage_is_reported_and_reset_clears_it(client: TestClient) -> None:
    sign_in(client, ALICE)
    arm = client.post("/api/_e2e/fail", json={"target": "classifier"}, headers=HEADERS)
    assert arm.status_code == 200
    assert say(client, "How do I reset my password?")["reply"]
    assert client.post("/api/_e2e/reset", headers=HEADERS).json() == {"status": "reset"}
    assert say(client, "How do I reset my password?")["reply"]


@pytest.mark.parametrize("headers", [{}, {"X-E2E-Token": "wrong"}])
def test_controls_hide_behind_the_token(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/api/_e2e/tickets", headers=headers).status_code == 404
    assert client.post("/api/_e2e/reset", headers=headers).status_code == 404


def test_controls_do_not_exist_outside_the_e2e_environment(
    migrated_database: str, index_path: Path
) -> None:
    settings = fake_settings(migrated_database, index_path, app_env="local")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/_e2e/tickets", headers=HEADERS).status_code == 404
        assert c.get("/api/health").status_code in (200, 503)


def test_fake_providers_are_refused_in_production(migrated_database: str) -> None:
    for field in ("llm_provider", "ticketing_provider"):
        with pytest.raises(ValidationError):
            make_settings(migrated_database, app_env="production", **{field: "fake"})
