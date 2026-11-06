"""Hostile and broken conditions against the real app and database."""

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from agent_harness import ALICE, ANSWERABLE, Harness
from fastapi.testclient import TestClient
from test_api_chat import PASSWORDS, chat, make_settings, retriever, sign_in  # noqa: F401

from tier0.api.app import create_app


@pytest.fixture
def harness(retriever: Any) -> Harness:  # noqa: F811
    return Harness(retriever)


def client_for(url: str, harness: Harness, **settings: Any) -> TestClient:
    return TestClient(create_app(make_settings(url, **settings), agent=harness.agent))


@pytest.fixture
def client(migrated_database: str, harness: Harness) -> Iterator[TestClient]:
    with client_for(migrated_database, harness) as c:
        sign_in(c, ALICE)
        yield c


# --- odd input ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "has a \x00 NUL byte",
        "\x00",
        "emoji 😀 and RTL ‮evil‬ and zero-width ​",
        "line\r\nbreaks\tand\x1b[31m escape codes",
        "'; DROP TABLE users; --",
        '{"thread_id": "x"} <script>alert(1)</script>',
        "𝔘𝔫𝔦𝔠𝔬𝔡𝔢" * 100,
    ],
)
def test_unusual_text_is_handled_without_a_server_error(client: TestClient, message: str) -> None:
    response = chat(client, message)
    assert response.status_code == 200, response.text
    thread = response.json()["thread_id"]
    # and the conversation can be read back afterwards (it was saved without choking)
    assert client.get(f"/api/chat/{thread}").status_code == 200


def test_message_length_boundaries(client: TestClient) -> None:
    assert chat(client, "x" * 2000).status_code == 200
    assert chat(client, "x" * 2001).status_code == 422
    assert chat(client, "   ").status_code == 422
    assert chat(client, "").status_code == 422


@pytest.mark.parametrize("thread_id", ["not-a-uuid", "../../etc/passwd", "1" * 100, "null", ""])
def test_a_malformed_thread_id_is_a_clean_422(client: TestClient, thread_id: str) -> None:
    assert chat(client, "hi", thread_id).status_code in (422, 200)  # "" means: no thread
    assert client.get(f"/api/chat/{thread_id or 'x'}").status_code in (404, 422)


def test_the_wrong_json_shapes_are_rejected_not_crashed(client: TestClient) -> None:
    headers = {"Origin": "http://testserver"}
    bodies: list[Any] = [[], "text", 5, {"message": 5}, {"message": ["a"]}, {"message": None}, {}]
    for body in bodies:
        assert client.post("/api/chat", json=body, headers=headers).status_code == 422
    assert client.post("/api/chat", content=b"{not json", headers=headers).status_code == 422


def test_a_huge_body_is_refused_before_it_is_read(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        content=b'{"message": "' + b"a" * 200_000 + b'"}',
        headers={"Origin": "http://testserver", "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_the_body_limit_is_configurable(migrated_database: str, harness: Harness) -> None:
    with client_for(migrated_database, harness, max_request_bytes=1024) as c:
        sign_in(c, ALICE)
        assert chat(c, "x" * 500).status_code == 200
        assert chat(c, "x" * 1500).status_code == 413


# --- login throttling behind a proxy --------------------------------------------------


def attempt(c: TestClient, email: str, forwarded: str | None = None) -> int:
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    body: dict[str, str] = {"email": email, "password": "wrong-password-123"}
    return c.post("/api/auth/login", json=body, headers=headers).status_code


def test_behind_one_proxy_each_real_client_is_throttled_separately(
    migrated_database: str, harness: Harness
) -> None:
    settings = {"trusted_proxy_hops": 1, "login_max_failures_per_ip": 3}
    with client_for(migrated_database, harness, **settings) as c:
        for n in range(3):
            assert attempt(c, f"nobody{n}@buzztech.example", "198.51.100.7") == 401
        assert attempt(c, "nobody9@buzztech.example", "198.51.100.7") == 429  # that client is out
        # a different real client is unaffected: the proxy's own address is not shared by everyone
        assert attempt(c, "nobody9@buzztech.example", "198.51.100.8") == 401
        # and forging a different left-hand entry does not reset the limit
        assert attempt(c, "nobody9@buzztech.example", "1.2.3.4, 198.51.100.7") == 429


def test_without_a_trusted_proxy_the_forwarding_header_is_ignored(
    migrated_database: str, harness: Harness
) -> None:
    with client_for(migrated_database, harness, login_max_failures_per_ip=3) as c:
        for n in range(3):
            assert attempt(c, f"nobody{n}@buzztech.example", f"203.0.113.{n}") == 401
        # spoofing a fresh address each time does not evade the limit
        assert attempt(c, "nobody9@buzztech.example", "203.0.113.99") == 429


# --- concurrency ---------------------------------------------------------------------


def test_simultaneous_confirmations_create_exactly_one_ticket(
    migrated_database: str, harness: Harness
) -> None:
    with client_for(migrated_database, harness) as c:
        sign_in(c, ALICE)
        thread = chat(c, "I want to raise a ticket").json()["thread_id"]
        reply = chat(c, "My laptop battery drains within an hour of unplugging it", thread).json()
        assert reply["pending_action"]["type"] == "confirm_ticket"

        statuses: list[int] = []
        start = threading.Barrier(8)

        def confirm() -> None:
            worker = TestClient(c.app)
            worker.cookies = c.cookies
            start.wait()
            statuses.append(chat(worker, "yes", thread).status_code)

        threads = [threading.Thread(target=confirm) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(code == 200 for code in statuses), statuses
        assert len(harness.adapter.tickets) == 1


def test_one_users_flood_does_not_block_another(migrated_database: str, harness: Harness) -> None:
    with client_for(migrated_database, harness, chat_messages_per_minute=2) as alice:
        sign_in(alice, ALICE)
        assert [chat(alice, ANSWERABLE).status_code for _ in range(3)] == [200, 200, 429]
        bob = TestClient(alice.app)
        sign_in(bob, "bob@buzztech.example")
        assert chat(bob, ANSWERABLE).status_code == 200


# --- the database goes away -----------------------------------------------------------


def test_a_database_outage_is_a_generic_error_with_an_id_and_no_details(
    client: TestClient,
) -> None:
    client.app.state.pool.close()  # type: ignore[attr-defined]
    failing = TestClient(client.app, raise_server_exceptions=False, cookies=client.cookies)
    response = failing.post(
        "/api/chat", json={"message": "hi"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code in (401, 500, 503)
    text = response.text.lower()
    assert "psycopg" not in text and "postgres" not in text and "traceback" not in text
    assert "X-Request-ID" in response.headers
    ready = failing.get("/api/ready")
    assert ready.status_code == 503 and "postgres" not in ready.text.lower()
