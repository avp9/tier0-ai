import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_harness import ALICE, ANSWERABLE, BOB, GAP, Harness
from fastapi.testclient import TestClient

from tier0.agent.factory import ChatNotConfigured
from tier0.api.app import create_app
from tier0.config import DemoUser, Settings
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"
SECRET = "s" * 40
PASSWORDS = {ALICE: "alice-demo-pass-1", BOB: "bob-demo-pass-22"}
NOT_FOUND = {"detail": "Conversation not found"}


def make_settings(url: str, **overrides: Any) -> Settings:
    users = [
        DemoUser.model_validate({"email": e, "name": e.split("@")[0], "password": p})
        for e, p in PASSWORDS.items()
    ]
    base: dict[str, Any] = {"database_url": url, "session_secret": SECRET, "demo_users": users}
    return Settings(**{**base, **overrides})


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("chat-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


@pytest.fixture
def harness(retriever: Retriever) -> Harness:
    return Harness(retriever)


@pytest.fixture
def app(migrated_database: str, harness: Harness) -> Any:
    return create_app(make_settings(migrated_database), agent=harness.agent)


@pytest.fixture
def alice(app: Any) -> Iterator[TestClient]:
    with TestClient(app) as client:
        sign_in(client, ALICE)
        yield client


def sign_in(client: TestClient, email: str) -> None:
    body = {"email": email, "password": PASSWORDS[email]}
    assert client.post("/api/auth/login", json=body).status_code == 204


def chat(client: TestClient, message: str, thread_id: str | None = None, **extra: Any) -> Any:
    body: dict[str, Any] = {"message": message, **extra}
    if thread_id:
        body["thread_id"] = thread_id
    return client.post("/api/chat", json=body)


# --- authentication ------------------------------------------------------------------


def test_chat_requires_a_signed_in_employee(app: Any) -> None:
    with TestClient(app) as anonymous:
        assert chat(anonymous, "hello").status_code == 401
        assert anonymous.get(f"/api/chat/{uuid.uuid4()}").status_code == 401


# --- the response contract -----------------------------------------------------------


def test_a_knowledge_answer_has_the_documented_shape(alice: TestClient) -> None:
    response = chat(alice, ANSWERABLE)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"thread_id", "reply", "intent", "sources", "pending_action", "ticket"}
    uuid.UUID(body["thread_id"])  # the server created a real UUID
    assert body["reply"].endswith("Sources: Documents to Bring on Your First Day")
    assert (
        body["intent"] == "knowledge" and body["pending_action"] is None and body["ticket"] is None
    )
    (source,) = body["sources"]
    assert {k: source[k] for k in ("document_id", "title", "category", "source")} == {
        "document_id": "onb-002",
        "title": "Documents to Bring on Your First Day",
        "category": "Onboarding",
        "source": "BuzzTech New Hire Guide (synthetic)",
    }
    assert 0 <= source["score"] <= 1


def test_the_whole_ticket_journey_over_http(alice: TestClient, harness: Harness) -> None:
    offer = chat(alice, GAP).json()
    assert offer["pending_action"]["type"] == "offer_ticket"
    assert "support ticket" in offer["pending_action"]["prompt"]
    thread = offer["thread_id"]

    preview = chat(alice, "yes", thread).json()
    action = preview["pending_action"]
    assert action["type"] == "confirm_ticket" and preview["thread_id"] == thread
    assert action["preview"]["requester_email"] == ALICE
    assert "gym" in action["preview"]["title"].lower() and action["preview"]["category"] == "HR"
    assert action["preview"]["priority"] is None and harness.adapter.tickets == []

    done = chat(alice, "yes", thread).json()
    assert done["pending_action"] is None
    assert done["ticket"] == {"ticket_key": "SUP-1", "url": done["ticket"]["url"], "status": None}
    assert done["ticket"]["url"].endswith("/browse/SUP-1") and "SUP-1" in done["reply"]
    assert [t.draft.requester_email for t in harness.adapter.tickets] == [ALICE]


def test_the_ticket_list_offers_titles_and_statuses_to_choose_from(
    alice: TestClient, harness: Harness
) -> None:
    first = harness.add_ticket(ALICE, "Laptop slow")
    second = harness.add_ticket(ALICE, "Printer offline")
    listing = chat(alice, "show my tickets").json()
    action = listing["pending_action"]
    assert action["type"] == "select_ticket"
    assert action["options"] == [
        {"ticket_key": second, "title": "Printer offline", "status": "Open"},
        {"ticket_key": first, "title": "Laptop slow", "status": "Open"},
    ]
    picked = chat(alice, second, listing["thread_id"]).json()
    assert "Printer offline" in picked["reply"] and picked["pending_action"] is None


def test_small_talk_and_unknown_intent_values_map_to_names(alice: TestClient) -> None:
    assert chat(alice, "hello").json()["intent"] == "smalltalk"
    assert chat(alice, "How do I make pizza?").json()["intent"] == "out_of_scope"
    assert chat(alice, "help").json()["intent"] == "ambiguous"


def test_the_requester_is_always_the_session_never_the_request(
    alice: TestClient, harness: Harness
) -> None:
    thread = chat(alice, "raise a ticket: my VPN is down", requester_email=BOB, email=BOB).json()
    chat(alice, "yes", thread["thread_id"])
    assert [t.draft.requester_email for t in harness.adapter.tickets] == [ALICE]
    assert thread["pending_action"]["preview"]["requester_email"] == ALICE


# --- ownership -----------------------------------------------------------------------


def test_someone_elses_conversation_is_not_found_not_forbidden(app: Any) -> None:
    with TestClient(app) as one, TestClient(app) as two:
        sign_in(one, ALICE)
        sign_in(two, BOB)
        thread = chat(one, GAP).json()["thread_id"]

        stolen = chat(two, "yes", thread)
        peek = two.get(f"/api/chat/{thread}")
        missing = two.get(f"/api/chat/{uuid.uuid4()}")
        assert stolen.status_code == peek.status_code == missing.status_code == 404
        assert stolen.json() == peek.json() == missing.json() == NOT_FOUND

        resumed = chat(one, "yes", thread).json()  # Alice's conversation is untouched
        assert resumed["pending_action"]["type"] == "confirm_ticket"


# --- history -------------------------------------------------------------------------


def test_history_lists_the_messages_in_order_and_the_open_question(alice: TestClient) -> None:
    thread = chat(alice, ANSWERABLE).json()["thread_id"]
    chat(alice, GAP, thread)
    history = alice.get(f"/api/chat/{thread}").json()
    assert history["thread_id"] == thread
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user"]
    assert history["messages"][0]["content"] == ANSWERABLE
    assert history["messages"][2]["content"] == GAP
    assert history["pending_action"]["type"] == "offer_ticket"  # the question not yet in messages

    chat(alice, "no", thread)
    finished = alice.get(f"/api/chat/{thread}").json()
    roles = [m["role"] for m in finished["messages"]]
    assert finished["pending_action"] is None
    assert roles == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]  # offer included


def test_history_of_a_confirmation_includes_the_preview(alice: TestClient) -> None:
    thread = chat(alice, "raise a ticket: my VPN is down").json()["thread_id"]
    pending = alice.get(f"/api/chat/{thread}").json()["pending_action"]
    assert pending["type"] == "confirm_ticket" and pending["preview"]["requester_email"] == ALICE


# --- input validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "   \n"},
        {"message": "x" * 2001},
        {"message": "hello", "thread_id": "not-a-uuid"},
        {"message": "hello", "thread_id": "123"},
        {"message": 5},
    ],
)
def test_invalid_requests_are_rejected_before_the_agent_runs(
    alice: TestClient, harness: Harness, body: dict[str, Any]
) -> None:
    assert alice.post("/api/chat", json=body).status_code == 422
    assert harness.classifier.calls == []


def test_a_malformed_thread_id_in_the_url_is_rejected(alice: TestClient) -> None:
    assert alice.get("/api/chat/not-a-uuid").status_code == 422


def test_messages_are_trimmed(alice: TestClient, harness: Harness) -> None:
    chat(alice, "   hello   ")
    assert harness.classifier.calls[0][0] == "hello"


# --- protections ---------------------------------------------------------------------


def test_each_employee_has_their_own_message_allowance(
    migrated_database: str, harness: Harness
) -> None:
    app = create_app(
        make_settings(migrated_database, chat_messages_per_minute=3), agent=harness.agent
    )
    with TestClient(app) as one, TestClient(app) as two:
        sign_in(one, ALICE)
        sign_in(two, BOB)
        assert [chat(one, "hello").status_code for _ in range(3)] == [200, 200, 200]
        limited = chat(one, "hello")
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
        assert chat(two, "hello").status_code == 200


def test_a_rate_limited_message_never_reaches_the_agent(
    migrated_database: str, harness: Harness
) -> None:
    app = create_app(
        make_settings(migrated_database, chat_messages_per_minute=1), agent=harness.agent
    )
    with TestClient(app) as c:
        sign_in(c, ALICE)
        chat(c, "hello")
        chat(c, "hello")
    assert len(harness.classifier.calls) == 1


def test_posts_from_another_site_are_refused_before_the_agent_runs(
    alice: TestClient, harness: Harness
) -> None:
    refused = alice.post(
        "/api/chat", json={"message": "hello"}, headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403 and harness.classifier.calls == []


def test_a_double_clicked_confirmation_creates_one_ticket(
    alice: TestClient, harness: Harness
) -> None:
    thread = chat(alice, "raise a ticket: my VPN is down").json()["thread_id"]
    statuses: list[int] = []

    def confirm() -> None:
        statuses.append(chat(alice, "yes", thread).status_code)

    clicks = [threading.Thread(target=confirm) for _ in range(2)]
    for click in clicks:
        click.start()
    for click in clicks:
        click.join()
    assert statuses == [200, 200]
    assert harness.adapter.create_calls == 1 and len(harness.adapter.tickets) == 1


# --- failures and configuration ------------------------------------------------------


def test_without_a_configured_agent_chat_says_so_but_sign_in_still_works(
    migrated_database: str,
) -> None:
    with TestClient(create_app(make_settings(migrated_database))) as c:
        sign_in(c, ALICE)
        unavailable = chat(c, "hello")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "Chat is not available right now"}
        assert c.get(f"/api/chat/{uuid.uuid4()}").status_code == 503
        assert c.get("/api/auth/me").status_code == 200


def test_a_server_outside_local_development_refuses_to_start_without_chat_settings(
    migrated_database: str,
) -> None:
    app = create_app(make_settings(migrated_database, app_env="staging"))
    with pytest.raises(ChatNotConfigured) as raised, TestClient(app):
        pass
    message = str(raised.value)
    assert "OPENAI_API_KEY" in message and "JIRA_API_TOKEN" in message
    assert SECRET not in message


def test_an_unexpected_error_returns_a_generic_message_with_no_internals(
    app: Any, harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(**_: Any) -> None:
        raise RuntimeError("db password=hunter2 at 10.0.0.5")

    monkeypatch.setattr(harness.agent, "send", explode)
    with TestClient(app, raise_server_exceptions=False) as c:
        sign_in(c, ALICE)
        response = chat(c, "hello")
    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong. Please try again."}
    assert "hunter2" not in response.text and "10.0.0.5" not in response.text
