import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from agent_harness import ALICE, ANSWERABLE, GAP, Harness
from fastapi.testclient import TestClient
from test_api_chat import chat, make_settings, retriever, sign_in  # noqa: F401

from tier0.api.app import create_app
from tier0.observability.logs import CorrelationFilter, JsonFormatter

MARKER = "ZEBRA-MARKER-98765"


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG)  # even debug output must stay clean
    yield stream
    root.handlers[:], root.level = saved[0], saved[1]


@pytest.fixture
def client(migrated_database: str, retriever: Any) -> Iterator[TestClient]:  # noqa: F811
    harness = Harness(retriever)
    with TestClient(create_app(make_settings(migrated_database), agent=harness.agent)) as c:
        c.harness = harness  # type: ignore[attr-defined]
        yield c


def events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    generated = client.get("/api/health").headers["X-Request-ID"]
    assert len(generated) == 36
    assert (
        client.get("/api/health", headers={"X-Request-ID": "trace-12345678"}).headers[
            "X-Request-ID"
        ]
        == "trace-12345678"
    )
    forged = client.get("/api/health", headers={"X-Request-ID": "bad id\nforged"})
    assert forged.headers["X-Request-ID"] != "bad id\nforged"


def test_a_chat_turn_is_logged_with_ids_and_never_with_the_message(
    client: TestClient, captured: io.StringIO
) -> None:
    sign_in(client, ALICE)
    response = chat(client, f"{MARKER} {ANSWERABLE}")
    thread = response.json()["thread_id"]
    text = captured.getvalue()
    assert MARKER not in text and ANSWERABLE not in text  # the question is never logged
    assert ALICE not in text  # the full email is never logged
    turn = next(e for e in events(captured) if e["message"] == "chat turn")
    assert turn["thread_id"] == thread
    assert turn["message_chars"] == len(f"{MARKER} {ANSWERABLE}")
    assert turn["intent"] == "KNOWLEDGE_QUESTION" and turn["sources"] >= 1
    access = next(
        e for e in events(captured) if e["message"] == "request" and e["path"] == "/api/chat"
    )
    assert access["request_id"] == turn["request_id"] == response.headers["X-Request-ID"]
    assert access["status"] == 200 and access["duration_ms"] >= 0


def test_replies_are_never_logged_either(client: TestClient, captured: io.StringIO) -> None:
    sign_in(client, ALICE)
    reply = chat(client, GAP).json()["reply"]
    assert reply and reply not in captured.getvalue()


def test_failed_logins_are_logged_masked_and_without_the_password(
    client: TestClient, captured: io.StringIO
) -> None:
    body = {"email": ALICE, "password": "wrong-Password-123"}
    assert client.post("/api/auth/login", json=body).status_code == 401
    text = captured.getvalue()
    failure = next(e for e in events(captured) if e["message"] == "login failed")
    assert failure["email"] == "a***@buzztech.example"
    assert "wrong-Password-123" not in text and ALICE not in text


def test_a_created_ticket_is_logged_under_the_same_request(
    client: TestClient, captured: io.StringIO
) -> None:
    sign_in(client, ALICE)
    thread = chat(client, "I want to raise a ticket").json()["thread_id"]
    chat(client, "My laptop battery drains within an hour of unplugging it", thread)
    final = chat(client, "yes", thread)
    assert final.json()["ticket"]["ticket_key"].startswith("SUP-")
    submission = next(e for e in events(captured) if e["message"].startswith("ticket submission"))
    assert submission["outcome"] == "created" and submission["ticket_key"].startswith("SUP-")
    assert submission["request_id"] == final.headers["X-Request-ID"]
    assert submission["thread_id"] == thread


def test_health_checks_do_not_flood_the_log(client: TestClient, captured: io.StringIO) -> None:
    client.get("/api/health")
    assert not [e for e in events(captured) if e.get("path") == "/api/health"]


def test_an_unexpected_error_is_logged_with_its_request_id(
    client: TestClient, captured: io.StringIO
) -> None:
    sign_in(client, ALICE)

    def boom(**_: object) -> None:
        raise RuntimeError(f"exploded for {ALICE}")

    client.app.state.agent.send = boom  # type: ignore[attr-defined]
    response = TestClient(client.app, raise_server_exceptions=False, cookies=client.cookies).post(
        "/api/chat", json={"message": "hi"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 500
    error = next(e for e in events(captured) if e["level"] == "ERROR")
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "RuntimeError" in error["exception"] and ALICE not in captured.getvalue()
