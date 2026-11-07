import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

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
from tier0.rag.semantic_cache import InMemorySemanticCache
from tier0.tickets.errors import TicketOutcomeUnknown

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


def parse_sse(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


STREAM = {"Accept": "text/event-stream"}


# --- streaming ------------------------------------------------------------------------


def test_a_streamed_answer_sends_pieces_then_the_same_turn_the_json_endpoint_gives(
    alice: TestClient,
) -> None:
    response = alice.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    *tokens, (name, done) = parse_sse(response.text)
    assert name == "done" and tokens and all(kind == "token" for kind, _ in tokens)
    assert "".join(data["text"] for _, data in tokens) == "Here is what the handbook says."
    plain = chat(alice, ANSWERABLE).json()
    ignore = {"thread_id": None, "feedback_token": None}  # each turn has its own
    assert {**done, **ignore} == {**plain, **ignore}


def test_a_streamed_reply_the_model_did_not_write_is_just_the_final_event(
    alice: TestClient,
) -> None:
    response = alice.post("/api/chat", json={"message": GAP}, headers=STREAM)
    ((name, done),) = parse_sse(response.text)
    assert name == "done" and done["pending_action"]["type"] == "offer_ticket"


def test_streaming_still_refuses_before_sending_anything(alice: TestClient) -> None:
    assert alice.post("/api/chat", json={"message": "  "}, headers=STREAM).status_code == 422
    first = chat(alice, ANSWERABLE).json()["thread_id"]
    with TestClient(alice.app) as bob:
        sign_in(bob, BOB)
        response = bob.post("/api/chat", json={"message": "hi", "thread_id": first}, headers=STREAM)
    assert response.status_code == 404 and response.json() == NOT_FOUND
    with TestClient(alice.app) as anonymous:
        assert anonymous.post("/api/chat", json={"message": "x"}, headers=STREAM).status_code == 401


def test_a_failure_while_streaming_ends_with_an_error_event_and_no_details(
    alice: TestClient, harness: Harness
) -> None:
    def explode(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("secret internal detail")

    harness.agent._graph.stream = explode  # type: ignore[method-assign]
    response = alice.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
    ((name, data),) = parse_sse(response.text)
    assert name == "error" and "secret" not in response.text
    assert data == {"detail": "Something went wrong. Please try again."}


# --- editing the ticket before it is sent -----------------------------------------------

EDIT = {
    "title": "Gym reimbursement question",
    "description": "Does the company reimburse gym memberships?",
    "category": "HR",
    "priority": "Low",
}


def at_the_preview(client: TestClient) -> str:
    thread = chat(client, GAP).json()["thread_id"]
    preview = chat(client, "yes", thread).json()["pending_action"]
    assert preview["type"] == "confirm_ticket" and preview["editable"] is True
    return cast(str, thread)


def edit_ticket(client: TestClient, thread: str | None, ticket_edit: Any = EDIT, **kw: Any) -> Any:
    body: dict[str, Any] = {"ticket_edit": ticket_edit}
    if thread:
        body["thread_id"] = thread
    return client.post("/api/chat", json=body, **kw)


def test_editing_the_preview_shows_the_changes_and_the_ticket_is_created_with_them(
    alice: TestClient, harness: Harness
) -> None:
    thread = at_the_preview(alice)
    response = edit_ticket(alice, thread)
    assert response.status_code == 200
    action = response.json()["pending_action"]
    assert action["type"] == "confirm_ticket" and action["editable"] is True
    assert action["preview"]["title"] == EDIT["title"]
    assert action["preview"]["description"] == EDIT["description"]
    assert (action["preview"]["category"], action["preview"]["priority"]) == ("HR", "Low")
    assert harness.adapter.tickets == []  # still only a preview

    done = chat(alice, "yes", thread).json()
    assert done["ticket"] is not None
    (stored,) = harness.adapter.tickets
    assert stored.draft.summary == EDIT["title"] and stored.draft.priority == "Low"


def test_the_edited_preview_is_what_a_reload_shows(alice: TestClient) -> None:
    thread = at_the_preview(alice)
    edit_ticket(alice, thread)
    action = alice.get(f"/api/chat/{thread}").json()["pending_action"]
    assert action["preview"]["title"] == EDIT["title"] and action["editable"] is True


def test_a_request_must_carry_a_message_or_an_edit_but_not_both(alice: TestClient) -> None:
    thread = at_the_preview(alice)
    both = {"thread_id": thread, "message": "hi", "ticket_edit": EDIT}
    assert alice.post("/api/chat", json=both).status_code == 422
    assert alice.post("/api/chat", json={"thread_id": thread}).status_code == 422
    assert edit_ticket(alice, None).status_code == 422  # an edit needs its conversation


@pytest.mark.parametrize(
    "change",
    [
        {"title": ""},
        {"title": "x" * 121},
        {"description": "   "},
        {"category": "Finance"},
        {"priority": "Urgent"},
        {"extra": "field"},
    ],
)
def test_an_invalid_edit_is_refused_and_changes_nothing(alice: TestClient, change: Any) -> None:
    thread = at_the_preview(alice)
    assert edit_ticket(alice, thread, {**EDIT, **change}).status_code == 422
    action = alice.get(f"/api/chat/{thread}").json()["pending_action"]
    assert action["preview"]["title"] != EDIT["title"]


def test_there_is_nothing_to_edit_unless_a_preview_is_showing(alice: TestClient) -> None:
    thread = chat(alice, ANSWERABLE).json()["thread_id"]
    response = edit_ticket(alice, thread)
    assert response.status_code == 409
    assert response.json() == {"detail": "There is no ticket to edit right now."}


def test_a_ticket_that_may_already_exist_cannot_be_edited(
    alice: TestClient, harness: Harness
) -> None:
    thread = at_the_preview(alice)
    harness.adapter.fail_next(TicketOutcomeUnknown("no answer"))
    unknown = chat(alice, "yes", thread).json()["pending_action"]
    assert unknown["type"] == "confirm_ticket" and unknown["editable"] is False
    response = edit_ticket(alice, thread)
    assert response.status_code == 409
    assert "already been sent" in response.json()["detail"]
    assert alice.get(f"/api/chat/{thread}").json()["pending_action"]["editable"] is False


def test_nobody_can_edit_someone_elses_ticket(app: Any) -> None:
    with TestClient(app) as alice_client:
        sign_in(alice_client, ALICE)
        thread = at_the_preview(alice_client)
    with TestClient(app) as bob_client:
        sign_in(bob_client, BOB)
        response = edit_ticket(bob_client, thread)
        assert response.status_code == 404 and response.json() == NOT_FOUND


def test_an_edit_can_be_streamed_and_is_refused_up_front_when_not_allowed(
    alice: TestClient,
) -> None:
    thread = at_the_preview(alice)
    streamed = edit_ticket(alice, thread, headers=STREAM)
    assert streamed.status_code == 200
    ((name, done),) = parse_sse(streamed.text)
    assert name == "done" and done["pending_action"]["preview"]["title"] == EDIT["title"]
    other = chat(alice, ANSWERABLE).json()["thread_id"]
    refused = edit_ticket(alice, other, headers=STREAM)
    assert refused.status_code == 409  # a status code, not a stream that fails half way


# --- looking through several tickets -----------------------------------------------------


def listed_tickets(client: TestClient, harness: Harness) -> tuple[str, list[str]]:
    first = harness.add_ticket(ALICE, "Laptop slow")
    second = harness.add_ticket(ALICE, "Printer offline")
    response = chat(client, "show my tickets").json()
    assert response["pending_action"]["type"] == "select_ticket"
    return response["thread_id"], [first, second]


def test_the_list_of_tickets_stays_after_one_is_shown(alice: TestClient, harness: Harness) -> None:
    thread, (first, second) = listed_tickets(alice, harness)
    shown = chat(alice, first, thread).json()
    assert first in shown["reply"] and "Laptop slow" in shown["reply"]
    action = shown["pending_action"]
    assert action["type"] == "select_ticket"
    assert [o["ticket_key"] for o in action["options"]] == [second, first]
    assert {o["title"] for o in action["options"]} == {"Laptop slow", "Printer offline"}


def test_tickets_can_be_looked_at_one_after_another_and_the_list_survives_a_reload(
    alice: TestClient, harness: Harness
) -> None:
    thread, (first, second) = listed_tickets(alice, harness)
    for key in (first, second, first):
        shown = chat(alice, key, thread).json()
        assert key in shown["reply"] and shown["pending_action"]["type"] == "select_ticket"
    reloaded = alice.get(f"/api/chat/{thread}").json()["pending_action"]
    assert reloaded["type"] == "select_ticket" and len(reloaded["options"]) == 2


def test_saying_done_closes_the_list(alice: TestClient, harness: Harness) -> None:
    thread, (first, _) = listed_tickets(alice, harness)
    chat(alice, first, thread)
    closed = chat(alice, "done", thread).json()
    assert closed["pending_action"] is None
    assert alice.get(f"/api/chat/{thread}").json()["pending_action"] is None


# --- the semantic cache ---------------------------------------------------------------


@pytest.fixture
def cached_harness(retriever: Retriever) -> Harness:
    return Harness(retriever, cache=InMemorySemanticCache(min_similarity=0.95))


@pytest.fixture
def cached_app(migrated_database: str, cached_harness: Harness) -> Any:
    return create_app(make_settings(migrated_database), agent=cached_harness.agent)


def test_the_same_question_asked_again_is_answered_from_the_cache(
    cached_app: Any, cached_harness: Harness
) -> None:
    with TestClient(cached_app) as client:
        sign_in(client, ALICE)
        first = chat(client, ANSWERABLE).json()
        spent = len(cached_harness.llm.calls)
        second = chat(client, ANSWERABLE).json()
        assert first["cached"] is False and second["cached"] is True
        assert second["reply"] == first["reply"] and second["sources"] == first["sources"]
        assert len(cached_harness.llm.calls) == spent  # no model was called for the second


def test_a_cached_answer_is_still_part_of_the_conversation(
    cached_app: Any, cached_harness: Harness
) -> None:
    with TestClient(cached_app) as client:
        sign_in(client, ALICE)
        chat(client, ANSWERABLE)
        again = chat(client, ANSWERABLE).json()
        history = client.get(f"/api/chat/{again['thread_id']}").json()["messages"]
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert history[1]["content"] == again["reply"]


def test_another_employee_gets_the_same_cached_answer(
    cached_app: Any, cached_harness: Harness
) -> None:
    with TestClient(cached_app) as alice_client:
        sign_in(alice_client, ALICE)
        first = chat(alice_client, ANSWERABLE).json()
    with TestClient(cached_app) as bob_client:
        sign_in(bob_client, BOB)
        second = chat(bob_client, ANSWERABLE).json()
        assert second["cached"] is True and second["reply"] == first["reply"]
        assert second["thread_id"] != first["thread_id"]  # their own conversation


def test_a_streamed_cached_answer_arrives_as_one_done_event(
    cached_app: Any, cached_harness: Harness
) -> None:
    with TestClient(cached_app) as client:
        sign_in(client, ALICE)
        chat(client, ANSWERABLE)
        streamed = client.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
        ((name, done),) = parse_sse(streamed.text)
        assert name == "done" and done["cached"] is True


def test_a_question_the_knowledge_base_cannot_answer_is_never_cached(
    cached_app: Any, cached_harness: Harness
) -> None:
    with TestClient(cached_app) as client:
        sign_in(client, ALICE)
        for _ in range(2):
            response = chat(client, GAP).json()
            assert response["cached"] is False
            assert response["pending_action"]["type"] == "offer_ticket"


def test_nothing_is_cached_unless_a_cache_is_configured(alice: TestClient) -> None:
    chat(alice, ANSWERABLE)
    assert chat(alice, ANSWERABLE).json()["cached"] is False


# --- the chat history ----------------------------------------------------------------


def history(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/conversations")
    assert response.status_code == 200
    return cast("list[dict[str, Any]]", response.json())


def test_the_history_needs_a_signed_in_employee(app: Any) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/conversations").status_code == 401


def test_a_new_employee_has_an_empty_history(alice: TestClient) -> None:
    assert history(alice) == []


def test_a_chat_is_listed_under_its_first_message_and_later_turns_keep_that_title(
    alice: TestClient,
) -> None:
    first = chat(alice, ANSWERABLE).json()["thread_id"]
    chat(alice, GAP, first)
    (item,) = history(alice)
    assert item["thread_id"] == first and item["title"] == ANSWERABLE
    assert set(item) == {"thread_id", "title", "updated_at"}


def test_the_most_recently_used_chat_comes_first(alice: TestClient) -> None:
    older = chat(alice, ANSWERABLE).json()["thread_id"]
    newer = chat(alice, GAP).json()["thread_id"]
    assert [c["thread_id"] for c in history(alice)] == [newer, older]
    chat(alice, ANSWERABLE, older)  # using the older one again moves it to the top
    assert [c["thread_id"] for c in history(alice)] == [older, newer]


def test_an_employee_never_sees_another_employees_chats(app: Any) -> None:
    with TestClient(app) as alice_client:
        sign_in(alice_client, ALICE)
        chat(alice_client, ANSWERABLE)
        assert len(history(alice_client)) == 1
    with TestClient(app) as bob_client:
        sign_in(bob_client, BOB)
        assert history(bob_client) == []


def test_a_streamed_turn_is_recorded_too(alice: TestClient) -> None:
    response = alice.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
    thread = parse_sse(response.text)[-1][1]["thread_id"]
    assert [c["thread_id"] for c in history(alice)] == [thread]


def test_a_failure_to_record_the_history_never_fails_the_chat(
    alice: TestClient, app: Any, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("history is down")

    app.state.conversations.touch = broken
    response = chat(alice, ANSWERABLE)
    assert response.status_code == 200 and response.json()["reply"]
    streamed = alice.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
    assert parse_sse(streamed.text)[-1][0] == "done"
    assert "could not record the conversation" in caplog.text


def test_a_refused_message_is_not_recorded(alice: TestClient) -> None:
    assert alice.post("/api/chat", json={"message": "  "}).status_code == 422
    assert history(alice) == []


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
    assert set(body) == {
        "thread_id",
        "reply",
        "intent",
        "sources",
        "pending_action",
        "ticket",
        "cached",
        "feedback_token",
    }
    assert body["cached"] is False
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
    assert "Printer offline" in picked["reply"]
    assert picked["pending_action"]["options"] == action["options"]  # the list stays


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
