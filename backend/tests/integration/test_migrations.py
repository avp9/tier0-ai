import psycopg
import pytest

from tier0.db.migrate import (
    current_revision,
    downgrade_to_base,
    head_revision,
    upgrade_to_head,
)

EMAIL = "alice@buzztech.example"


def tables(url: str) -> set[str]:
    with psycopg.connect(url) as connection:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def submit(
    url: str,
    *,
    thread: str = "t1",
    payload: str = "h1",
    status: str = "pending",
    key: str | None = None,
    email: str = EMAIL,
) -> None:
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO ticket_submissions (thread_id, requester_email, payload_hash, status, "
            "ticket_key) VALUES (%s, %s, %s, %s, %s)",
            (thread, email, payload, status, key),
        )


def test_upgrade_creates_the_tables_and_is_repeatable(empty_database: str) -> None:
    assert current_revision(empty_database) is None
    upgrade_to_head(empty_database)
    upgrade_to_head(empty_database)
    assert {"users", "ticket_submissions"} <= tables(empty_database)
    assert current_revision(empty_database) == head_revision()


def test_downgrade_removes_the_tables(migrated_database: str) -> None:
    downgrade_to_base(migrated_database)
    assert not {"users", "ticket_submissions"} & tables(migrated_database)


def test_percent_encoded_passwords_in_the_url_survive_alembic_config() -> None:
    from tier0.db.migrate import _config

    config = _config("postgresql://u:p%40ss%25@localhost/db")
    assert (
        config.get_main_option("sqlalchemy.url") == "postgresql+psycopg://u:p%40ss%25@localhost/db"
    )


def test_user_emails_are_unique_and_lowercase(migrated_database: str) -> None:
    def add(email: str) -> None:
        with psycopg.connect(migrated_database, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO users (email, display_name, password_hash) VALUES (%s, 'A', 'x')",
                (email,),
            )

    add(EMAIL)
    with pytest.raises(psycopg.errors.UniqueViolation):
        add(EMAIL)
    with pytest.raises(psycopg.errors.CheckViolation):
        add("Mixed@Case.example")


def test_users_get_an_id_and_timestamp_by_default(migrated_database: str) -> None:
    with psycopg.connect(migrated_database, autocommit=True) as connection:
        row = connection.execute(
            "INSERT INTO users (email, display_name, password_hash) VALUES (%s, 'A', 'x') "
            "RETURNING id, created_at",
            (EMAIL,),
        ).fetchone()
    assert row is not None and row[0] is not None and row[1] is not None


def test_a_submission_status_must_be_a_known_value(migrated_database: str) -> None:
    for status in ("pending", "unknown", "failed", "cancelled"):
        submit(migrated_database, payload=f"h-{status}", status=status)
    submit(migrated_database, payload="h-done", status="submitted", key="SUP-1")
    with pytest.raises(psycopg.errors.CheckViolation):
        submit(migrated_database, payload="h-bad", status="shipped")


def test_a_ticket_key_exists_exactly_when_the_submission_succeeded(migrated_database: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        submit(migrated_database, status="submitted", key=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        submit(migrated_database, payload="h2", status="pending", key="SUP-9")


def test_the_same_ticket_cannot_be_submitted_twice_in_one_conversation(
    migrated_database: str,
) -> None:
    submit(migrated_database, status="pending")
    with pytest.raises(psycopg.errors.UniqueViolation):
        submit(migrated_database, status="unknown")  # same thread and content, still live
    submit(migrated_database, thread="t2")  # another conversation is fine
    submit(migrated_database, payload="h-other")  # different content is fine


def test_a_cancelled_or_failed_submission_does_not_block_a_fresh_one(
    migrated_database: str,
) -> None:
    submit(migrated_database, status="cancelled")
    submit(migrated_database, status="failed")
    submit(migrated_database, status="pending")  # now the live one
    submit(migrated_database, status="cancelled")  # many dead ones are fine


def test_submission_emails_must_be_lowercase(migrated_database: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        submit(migrated_database, email="Alice@Buzztech.example")
