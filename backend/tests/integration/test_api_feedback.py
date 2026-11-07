import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_harness import ALICE, ANSWERABLE, BOB, GAP, Harness
from fastapi.testclient import TestClient
from test_api_chat import STREAM, chat, make_settings, parse_sse, sign_in
from tracing_support import CapturingSession, make_tracing

from tier0.api.app import create_app
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("fb-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


@pytest.fixture
def app(migrated_database: str, retriever: Retriever, session: CapturingSession) -> Any:
    tracing, _ = make_tracing(session)
    harness = Harness(retriever, tracing=tracing)
    return create_app(make_settings(migrated_database), agent=harness.agent, tracing=tracing)


@pytest.fixture
def alice(app: Any) -> Iterator[TestClient]:
    with TestClient(app) as client:
        sign_in(client, ALICE)
        yield client


def vote(client: TestClient, token: str, kind: str = "up", **extra: Any) -> Any:
    return client.post("/api/feedback", json={"token": token, "vote": kind, **extra})


def answer(client: TestClient) -> dict[str, Any]:
    reply = chat(client, ANSWERABLE).json()
    assert reply["feedback_token"]
    return dict(reply)


def feedback_calls(session: CapturingSession, method: str) -> list[Any]:
    return [b for m, u, b in session.calls if m == method and u.startswith("/feedback")]


def stored(migrated_database: str) -> list[tuple[str, int, str | None]]:
    import psycopg

    with psycopg.connect(migrated_database) as connection:
        rows = connection.execute(
            "SELECT trace_id::text, score, comment FROM answer_feedback ORDER BY updated_at"
        ).fetchall()
    return [(str(r[0]), int(r[1]), r[2]) for r in rows]


# --- which replies can be voted on ----------------------------------------------------------


def test_a_finished_answer_comes_with_a_token_to_vote_on_it(alice: TestClient) -> None:
    reply = answer(alice)
    token = reply["feedback_token"]
    assert uuid.UUID(token.split(".")[0]) and len(token) < 80


def test_an_answer_the_knowledge_base_could_not_give_can_be_voted_on_too(alice: TestClient) -> None:
    reply = chat(alice, GAP).json()
    assert reply["pending_action"]["type"] == "offer_ticket" and reply["feedback_token"]


def test_a_question_to_the_employee_about_a_ticket_preview_has_no_vote(alice: TestClient) -> None:
    thread = chat(alice, GAP).json()["thread_id"]
    preview = chat(alice, "yes", thread).json()
    assert preview["pending_action"]["type"] == "confirm_ticket"
    assert preview["feedback_token"] is None


def test_each_answer_has_its_own_token(alice: TestClient) -> None:
    assert answer(alice)["feedback_token"] != answer(alice)["feedback_token"]


def test_a_streamed_answer_carries_a_token_in_its_final_event(alice: TestClient) -> None:
    streamed = alice.post("/api/chat", json={"message": ANSWERABLE}, headers=STREAM)
    done = parse_sse(streamed.text)[-1][1]
    assert done["feedback_token"]


# --- voting ---------------------------------------------------------------------------------


def test_a_thumbs_up_is_stored_and_sent_to_langsmith_against_the_turn(
    alice: TestClient, session: CapturingSession, migrated_database: str
) -> None:
    reply = answer(alice)
    response = vote(alice, reply["feedback_token"], "up", comment="Spot on")
    assert response.status_code == 200 and response.json() == {"vote": "up"}
    trace_id = reply["feedback_token"].split(".")[0]
    assert stored(migrated_database) == [(trace_id, 1, "Spot on")]
    (sent,) = feedback_calls(session, "POST")
    assert sent["run_id"] == trace_id and sent["score"] == 1.0 and sent["key"] == "user_score"


def test_a_thumbs_down_scores_zero(alice: TestClient, session: CapturingSession) -> None:
    reply = answer(alice)
    vote(alice, reply["feedback_token"], "down")
    (sent,) = feedback_calls(session, "POST")
    assert sent["score"] == 0.0


def test_changing_a_vote_replaces_it_everywhere(
    alice: TestClient, session: CapturingSession, migrated_database: str
) -> None:
    token = answer(alice)["feedback_token"]
    vote(alice, token, "up")
    vote(alice, token, "down", comment="Actually wrong")
    assert [(s, c) for _, s, c in stored(migrated_database)] == [(0, "Actually wrong")]
    assert len(feedback_calls(session, "POST")) == 1  # one vote was created
    assert feedback_calls(session, "PATCH")[-1]["score"] == 0.0  # and then changed


def test_taking_a_vote_back_removes_it_everywhere(
    alice: TestClient, session: CapturingSession, migrated_database: str
) -> None:
    token = answer(alice)["feedback_token"]
    vote(alice, token, "up")
    response = vote(alice, token, "none")
    assert response.status_code == 200 and response.json() == {"vote": "none"}
    assert stored(migrated_database) == []
    assert any(m == "DELETE" and u.startswith("/feedback/") for m, u, _ in session.calls)


def test_voting_still_works_when_tracing_is_off(
    migrated_database: str, retriever: Retriever
) -> None:
    app = create_app(make_settings(migrated_database), agent=Harness(retriever).agent)
    with TestClient(app) as client:
        sign_in(client, ALICE)
        token = answer(client)["feedback_token"]
        assert vote(client, token, "up").status_code == 200
    assert len(stored(migrated_database)) == 1


def test_a_comment_with_personal_details_is_masked_in_langsmith(
    alice: TestClient, session: CapturingSession
) -> None:
    token = answer(alice)["feedback_token"]
    vote(alice, token, "down", comment="wrong, mail me at alice@buzztech.example or 07700900123")
    sent = str(feedback_calls(session, "POST"))
    assert "alice@buzztech" not in sent and "07700900123" not in sent


def test_a_langsmith_failure_never_fails_the_vote(
    migrated_database: str, retriever: Retriever, no_sleep: None
) -> None:
    import requests

    failing = CapturingSession(fail=requests.ConnectionError("down"))
    tracing, _ = make_tracing(failing)
    harness = Harness(retriever)  # answers are not traced here; only the vote goes out
    app = create_app(make_settings(migrated_database), agent=harness.agent, tracing=tracing)
    with TestClient(app) as client:
        sign_in(client, ALICE)
        token = answer(client)["feedback_token"]
        assert vote(client, token, "up").status_code == 200
    assert len(stored(migrated_database)) == 1


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("langsmith.client.time.sleep", lambda _seconds: None)


# --- who may vote ---------------------------------------------------------------------------


def test_nobody_can_vote_on_an_answer_someone_else_was_given(
    app: Any, migrated_database: str
) -> None:
    with TestClient(app) as alice_client:
        sign_in(alice_client, ALICE)
        token = answer(alice_client)["feedback_token"]
    with TestClient(app) as bob_client:
        sign_in(bob_client, BOB)
        response = vote(bob_client, token, "down")
        assert response.status_code == 404
        assert response.json() == {"detail": "That answer is not one this account was given."}
    assert stored(migrated_database) == []


@pytest.mark.parametrize("change", ["tamper-signature", "other-turn", "invented"])
def test_a_forged_token_is_refused(alice: TestClient, change: str, migrated_database: str) -> None:
    token = answer(alice)["feedback_token"]
    trace, _, signature = token.partition(".")
    forged = {
        "tamper-signature": f"{trace}.{'0' * len(signature)}",
        "other-turn": f"{uuid.uuid4()}.{signature}",
        "invented": f"{uuid.uuid4()}.{'a' * len(signature)}",
    }[change]
    assert vote(alice, forged).status_code == 404
    assert stored(migrated_database) == []


def test_a_vote_needs_a_signed_in_employee(app: Any) -> None:
    with TestClient(app) as client:
        assert vote(client, f"{uuid.uuid4()}.{'a' * 24}").status_code == 401


def test_a_vote_from_another_website_is_refused(alice: TestClient) -> None:
    token = answer(alice)["feedback_token"]
    response = alice.post(
        "/api/feedback",
        json={"token": token, "vote": "up"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"vote": "up"},
        {"token": "short", "vote": "up"},
        {"token": "x" * 36, "vote": "meh"},
        {"token": "x" * 200, "vote": "up"},
    ],
)
def test_a_malformed_vote_is_refused(alice: TestClient, body: dict[str, Any]) -> None:
    assert alice.post("/api/feedback", json=body).status_code == 422


def test_a_comment_cannot_be_enormous(alice: TestClient) -> None:
    token = answer(alice)["feedback_token"]
    assert vote(alice, token, "up", comment="x" * 501).status_code == 422
    assert vote(alice, token, "up", comment="x" * 500).status_code == 200
