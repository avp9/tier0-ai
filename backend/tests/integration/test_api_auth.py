import logging
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tier0.api.app import create_app
from tier0.auth import passwords
from tier0.auth.sessions import COOKIE_NAME, SessionCodec
from tier0.config import DemoUser, Settings
from tier0.db.migrate import current_revision, downgrade_to_base, head_revision, upgrade_with_lock

SECRET = "s" * 40
ALICE, BOB = "alice@buzztech.example", "bob@buzztech.example"
ALICE_PW, BOB_PW = "alice-demo-pass-1", "bob-demo-pass-22"


def demo_users() -> list[DemoUser]:
    return [
        DemoUser.model_validate({"email": ALICE, "name": "Alice Demo", "password": ALICE_PW}),
        DemoUser.model_validate({"email": BOB, "name": "Bob Demo", "password": BOB_PW}),
    ]


def make_settings(url: str, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": url,
        "session_secret": SECRET,
        "demo_users": demo_users(),
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def client(migrated_database: str) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(migrated_database))) as test_client:
        yield test_client


def login(c: TestClient, email: str = ALICE, password: str = ALICE_PW, **kwargs: Any) -> Any:
    return c.post("/api/auth/login", json={"email": email, "password": password}, **kwargs)


# --- start-up and seeding -------------------------------------------------------------


def test_demo_users_are_created_with_hashed_passwords(
    client: TestClient, migrated_database: str
) -> None:
    with psycopg.connect(migrated_database) as connection:
        rows = connection.execute(
            "SELECT email, password_hash FROM users ORDER BY email"
        ).fetchall()
    assert [r[0] for r in rows] == [ALICE, BOB]
    assert all(r[1].startswith("$argon2") for r in rows)
    assert ALICE_PW not in "".join(r[1] for r in rows)


def test_restarting_neither_duplicates_nor_overwrites_existing_users(
    migrated_database: str,
) -> None:
    with TestClient(create_app(make_settings(migrated_database))):
        pass
    changed = [
        DemoUser.model_validate(
            {"email": ALICE, "name": "Renamed", "password": "a-brand-new-pass-9"}
        )
    ]
    with TestClient(create_app(make_settings(migrated_database, demo_users=changed))) as c:
        assert login(c).status_code == 204  # the original password still works
        assert login(c, password="a-brand-new-pass-9").status_code == 401  # noqa: S106
        assert c.get("/api/auth/me").json()["display_name"] == "Alice Demo"
    with psycopg.connect(migrated_database) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (2,)


def test_a_fresh_database_is_migrated_when_the_app_starts(empty_database: str) -> None:
    assert current_revision(empty_database) is None
    with TestClient(create_app(make_settings(empty_database))) as c:
        assert c.get("/api/ready").status_code == 200
        assert login(c).status_code == 204
    assert current_revision(empty_database) == head_revision()


def test_servers_starting_at_the_same_moment_do_not_clash_over_the_migration(
    empty_database: str,
) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            upgrade_with_lock(empty_database)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and current_revision(empty_database) == head_revision()


# --- login ---------------------------------------------------------------------------


def test_a_successful_login_sets_a_locked_down_session_cookie(client: TestClient) -> None:
    response = login(client)
    assert response.status_code == 204 and response.content == b""
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    for attribute in ("httponly", "samesite=lax", "path=/", "max-age=28800"):
        assert attribute in cookie.lower()
    assert "secure" not in cookie.lower()  # local development runs over plain http


def test_outside_local_development_the_cookie_is_secure_and_hsts_is_sent(
    migrated_database: str,
) -> None:
    app = create_app(make_settings(migrated_database, app_env="production"))
    with TestClient(app) as c:
        response = login(c)
        assert "secure" in response.headers["set-cookie"].lower()
        assert "max-age=31536000" in c.get("/api/health").headers["strict-transport-security"]
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_local_development_keeps_the_api_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert "strict-transport-security" not in client.get("/api/health").headers


def test_email_is_case_and_space_insensitive_on_login(client: TestClient) -> None:
    assert login(client, "  ALICE@Buzztech.Example ").status_code == 204


def test_wrong_passwords_and_unknown_accounts_look_identical(client: TestClient) -> None:
    wrong = login(client, ALICE, "not-the-password")
    unknown = login(client, "nobody@buzztech.example", ALICE_PW)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password"}
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers


def test_an_unknown_account_still_costs_a_password_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    spent: list[str] = []
    monkeypatch.setattr(
        "tier0.api.routes_auth.spend_verification_time", lambda password: spent.append(password)
    )
    login(client, "nobody@buzztech.example", "guess")
    assert spent == ["guess"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": ALICE},
        {"password": ALICE_PW},
        {"email": "a" * 300 + "@b.co", "password": "x"},
        {"email": ALICE, "password": "p" * 257},
    ],
)
def test_malformed_or_oversized_logins_are_rejected_before_any_hashing(
    client: TestClient, body: dict[str, str]
) -> None:
    assert client.post("/api/auth/login", json=body).status_code == 422


def test_no_secret_reaches_responses_or_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        failed = login(client, ALICE, "wrong-guess-password")
        ok = login(client)
        me = client.get("/api/auth/me")
    everything = caplog.text + failed.text + ok.text + me.text
    for secret in ("wrong-guess-password", ALICE_PW, SECRET, "argon2"):
        assert secret not in everything
    assert set(me.json()) == {"email", "display_name"}


# --- rate limiting -------------------------------------------------------------------


def test_repeated_failures_lock_the_account_even_against_the_right_password(
    client: TestClient,
) -> None:
    for _ in range(5):
        assert login(client, ALICE, "wrong").status_code == 401
    blocked = login(client)  # now with the correct password
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert "set-cookie" not in blocked.headers
    assert login(client, BOB, BOB_PW).status_code == 204  # others are unaffected


def test_a_successful_login_resets_the_failure_count(client: TestClient) -> None:
    for _ in range(3):
        login(client, ALICE, "wrong")
    assert login(client).status_code == 204
    for _ in range(3):
        assert login(client, ALICE, "wrong").status_code == 401  # not locked: count restarted


# --- sessions ------------------------------------------------------------------------


def test_me_returns_the_signed_in_employee_only(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    login(client)
    assert client.get("/api/auth/me").json() == {"email": ALICE, "display_name": "Alice Demo"}


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "x" * 200])
def test_bad_cookies_are_not_authentication(client: TestClient, token: str) -> None:
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/api/auth/me").status_code == 401


def test_cookies_signed_with_another_secret_are_rejected(client: TestClient) -> None:
    # No login first: the cookie jar would otherwise hold the real cookie too and send both.
    forged = SessionCodec("z" * 40, 3600).issue("00000000-0000-0000-0000-000000000000")
    client.cookies.set(COOKIE_NAME, forged)
    assert client.get("/api/auth/me").status_code == 401


def test_a_valid_token_for_a_malformed_user_id_is_just_unauthenticated(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, SessionCodec(SECRET, 3600).issue("not-a-uuid"))
    assert client.get("/api/auth/me").status_code == 401


def test_sessions_expire(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    login(client)
    assert client.get("/api/auth/me").status_code == 200
    monkeypatch.setattr(time, "time", lambda: now + 8 * 3600 + 60)
    assert client.get("/api/auth/me").status_code == 401


def test_removing_a_user_ends_their_session_immediately(
    client: TestClient, migrated_database: str
) -> None:
    login(client)
    assert client.get("/api/auth/me").status_code == 200
    with psycopg.connect(migrated_database, autocommit=True) as connection:
        connection.execute("DELETE FROM users WHERE email = %s", (ALICE,))
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_the_cookie_and_works_even_without_a_session(client: TestClient) -> None:
    login(client)
    out = client.post("/api/auth/logout")
    assert out.status_code == 204 and "max-age=0" in out.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204


def test_two_employees_have_independent_sessions(migrated_database: str) -> None:
    app = create_app(make_settings(migrated_database))
    with TestClient(app) as one, TestClient(app) as two:
        login(one)
        login(two, BOB, BOB_PW)
        assert one.get("/api/auth/me").json()["email"] == ALICE
        assert two.get("/api/auth/me").json()["email"] == BOB


# --- cross-site requests -------------------------------------------------------------


def test_requests_from_another_site_are_refused(client: TestClient) -> None:
    hostile = {"Origin": "https://evil.example"}
    assert login(client, headers=hostile).status_code == 403
    assert client.post("/api/auth/logout", headers=hostile).status_code == 403
    assert login(client, headers={"Origin": "null"}).status_code == 403


def test_requests_from_the_same_site_or_non_browser_clients_are_allowed(client: TestClient) -> None:
    assert login(client, headers={"Origin": "http://testserver"}).status_code == 204
    assert login(client).status_code == 204  # no Origin header: not a cross-site browser request


# --- health, readiness and headers ---------------------------------------------------


def test_health_needs_no_login(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_ready_when_the_database_is_current(client: TestClient) -> None:
    assert client.get("/api/ready").json() == {"status": "ready"}


def test_not_ready_when_the_database_is_unreachable_and_no_detail_leaks(client: TestClient) -> None:
    def broken() -> bool:
        raise RuntimeError("password=hunter2 host=10.0.0.5")

    client.app.state.readiness_checks["database"] = broken  # type: ignore[attr-defined]
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failing": ["database"]}
    assert "hunter2" not in response.text


def test_not_ready_when_the_schema_is_behind_this_build(
    client: TestClient, migrated_database: str
) -> None:
    downgrade_to_base(migrated_database)
    response = client.get("/api/ready")
    assert response.status_code == 503 and response.json()["failing"] == ["migrations"]


def test_every_response_carries_basic_security_headers(client: TestClient) -> None:
    for response in (client.get("/api/health"), client.get("/api/auth/me"), login(client)):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"


def test_unknown_routes_are_404_not_a_login_page(client: TestClient) -> None:
    assert client.get("/api/nothing").status_code == 404
    assert re.search(r"not found", client.get("/api/nothing").text, re.I)


def test_password_hashing_is_not_skipped_for_real_logins(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = passwords.verify_password

    def counting(stored_hash: str, password: str) -> bool:
        calls.append(1)
        return original(stored_hash, password)

    monkeypatch.setattr("tier0.api.routes_auth.verify_password", counting)
    login(client)
    assert calls == [1]


def test_the_server_entry_point_boots_from_environment_variables_alone(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tier0.api.app import create_app_from_env

    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv(
        "DEMO_USERS", json.dumps([{"email": ALICE, "name": "Alice", "password": ALICE_PW}])
    )
    with TestClient(create_app_from_env()) as c:
        assert login(c).status_code == 204
        assert c.get("/api/auth/me").json()["email"] == ALICE
