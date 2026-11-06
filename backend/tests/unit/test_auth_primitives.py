import json
import time
from typing import Any

import pytest
from pydantic import ValidationError

from tier0.auth.passwords import hash_password, spend_verification_time, verify_password
from tier0.auth.ratelimit import LoginRateLimiter
from tier0.auth.sessions import SessionCodec
from tier0.config import DemoUser, Settings

SECRET = "x" * 40
BASE: dict[str, Any] = {"database_url": "postgresql://u@localhost/db", "session_secret": SECRET}


# --- settings -------------------------------------------------------------------------


def settings(**overrides: Any) -> Settings:
    return Settings(**{**BASE, **overrides})


def test_settings_need_a_database_and_a_long_session_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DATABASE_URL", "SESSION_SECRET", "DEMO_USERS", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="session_secret"):
        settings(session_secret="too-short")  # noqa: S106


def test_defaults_are_safe_and_local_means_plain_http_cookies() -> None:
    local = settings()
    assert local.is_local and not local.cookie_secure
    assert local.session_max_age_hours == 8 and local.run_migrations_on_start
    assert settings(app_env="production").cookie_secure
    assert settings(app_env="staging").cookie_secure


def test_demo_users_are_read_from_a_json_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = [{"email": "Alice@Buzztech.Example", "name": "Alice", "password": "a-long-demo-pass"}]
    monkeypatch.setenv("DATABASE_URL", BASE["database_url"])
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DEMO_USERS", json.dumps(users))
    parsed = Settings().demo_users  # type: ignore[call-arg]
    assert [u.email for u in parsed] == ["alice@buzztech.example"]  # lowercased
    assert parsed[0].password.get_secret_value() == "a-long-demo-pass"


@pytest.mark.parametrize(
    "bad",
    [
        {"email": "not-an-email", "name": "A", "password": "a-long-demo-pass"},
        {"email": "a@b.co", "name": "", "password": "a-long-demo-pass"},
        {"email": "a@b.co", "name": "A", "password": "short"},
    ],
)
def test_weak_or_malformed_demo_users_are_rejected(bad: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        DemoUser.model_validate(bad)


def test_secrets_never_appear_when_settings_are_printed() -> None:
    demo = DemoUser.model_validate({"email": "a@b.co", "name": "A", "password": "a-long-demo-pass"})
    shown = repr(settings(demo_users=[demo])) + str(settings()) + repr(demo)
    assert SECRET not in shown and "a-long-demo-pass" not in shown


# --- passwords ------------------------------------------------------------------------


def test_passwords_are_hashed_salted_and_verified() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first.startswith("$argon2") and first != second and "correct" not in first
    assert verify_password(first, "correct horse")
    assert not verify_password(first, "Correct horse")


def test_a_corrupt_stored_hash_is_a_failed_login_not_a_crash() -> None:
    assert not verify_password("not-a-hash", "anything")
    assert not verify_password("", "anything")


def test_unknown_accounts_still_cost_a_verification() -> None:
    started = time.perf_counter()
    spend_verification_time("whatever")
    assert time.perf_counter() - started > 0.001  # real hashing work happened


# --- session tokens -------------------------------------------------------------------


def test_a_session_token_round_trips_to_the_user_id() -> None:
    codec = SessionCodec(SECRET, 3600)
    assert codec.read(codec.issue("user-1")) == "user-1"


def test_tokens_from_another_secret_or_tampered_with_are_rejected() -> None:
    token = SessionCodec(SECRET, 3600).issue("user-1")
    assert SessionCodec("y" * 40, 3600).read(token) is None
    head, _, signature = token.rpartition(".")
    assert SessionCodec(SECRET, 3600).read(f"{head}.{signature[:-2]}AA") is None
    for junk in ("", "garbage", "a.b.c", token + "x", token.upper()):
        assert SessionCodec(SECRET, 3600).read(junk) is None


def test_tokens_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    codec = SessionCodec(SECRET, 3600)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    token = codec.issue("user-1")
    monkeypatch.setattr(time, "time", lambda: now + 3599)
    assert codec.read(token) == "user-1"
    monkeypatch.setattr(time, "time", lambda: now + 3601)
    assert codec.read(token) is None


def test_a_token_with_no_user_id_is_rejected() -> None:
    codec = SessionCodec(SECRET, 3600)
    forged = codec._serializer.dumps({"u": ""})
    assert codec.read(forged) is None
    assert codec.read(codec._serializer.dumps(["not", "a", "dict"])) is None
    assert codec.read(codec._serializer.dumps({"u": 5})) is None


# --- rate limiting --------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock, per_email: int = 3, per_ip: int = 5) -> LoginRateLimiter:
    return LoginRateLimiter(per_email, per_ip, window_seconds=600, clock=clock)


def test_attempts_are_allowed_until_the_limit_then_blocked_with_a_wait() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        assert rl.retry_after("a@b.co", "1.1.1.1") is None
        rl.record_failure("a@b.co", "1.1.1.1")
    wait = rl.retry_after("a@b.co", "1.1.1.1")
    assert wait == 600
    clock.now += 100
    assert rl.retry_after("a@b.co", "1.1.1.1") == 500


def test_the_block_lifts_when_the_window_passes() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.record_failure("a@b.co", "1.1.1.1")
    clock.now += 601
    assert rl.retry_after("a@b.co", "1.1.1.1") is None


def test_one_account_being_attacked_does_not_lock_out_others_from_the_same_place() -> None:
    rl = limiter(Clock())
    for _ in range(3):
        rl.record_failure("victim@b.co", "9.9.9.9")
    assert rl.retry_after("victim@b.co", "2.2.2.2") is not None
    assert rl.retry_after("other@b.co", "9.9.9.9") is None


def test_many_accounts_from_one_address_hit_the_address_limit() -> None:
    rl = limiter(Clock(), per_email=10, per_ip=5)
    for n in range(5):
        rl.record_failure(f"user{n}@b.co", "6.6.6.6")
    assert rl.retry_after("fresh@b.co", "6.6.6.6") is not None
    assert rl.retry_after("fresh@b.co", "7.7.7.7") is None


def test_a_successful_login_clears_that_accounts_failures_only() -> None:
    rl = limiter(Clock(), per_email=3, per_ip=100)
    for _ in range(2):
        rl.record_failure("a@b.co", "1.1.1.1")
        rl.record_failure("c@b.co", "1.1.1.1")
    rl.record_success("a@b.co")
    rl.record_failure("a@b.co", "1.1.1.1")
    rl.record_failure("c@b.co", "1.1.1.1")
    assert rl.retry_after("a@b.co", "1.1.1.1") is None
    assert rl.retry_after("c@b.co", "1.1.1.1") is not None


def test_email_case_does_not_dodge_the_limit() -> None:
    rl = limiter(Clock())
    for email in ("A@b.co", "a@B.co", "A@B.CO"):
        rl.record_failure(email, "1.1.1.1")
    assert rl.retry_after("a@b.co", "5.5.5.5") is not None


def test_old_records_are_pruned_so_memory_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import tier0.auth.ratelimit as module

    monkeypatch.setattr(module, "_PRUNE_AT", 50)
    clock = Clock()
    rl = limiter(clock)
    for n in range(40):
        rl.record_failure(f"u{n}@b.co", f"10.0.0.{n}")
    clock.now += 700  # everything above is now stale
    for n in range(40, 60):
        rl.record_failure(f"u{n}@b.co", f"10.0.1.{n}")
    assert len(rl._failures) < 100
