from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from psycopg_pool import ConnectionPool

from tier0.auth.passwords import hash_password
from tier0.config import DemoUser, Gender

SELECT_BY_EMAIL = """
    SELECT id::text AS id, email, display_name, gender, password_hash FROM users WHERE email = %s
"""
SELECT_BY_ID = "SELECT id::text AS id, email, display_name, gender FROM users WHERE id = %s::uuid"
INSERT_IF_MISSING = """
    INSERT INTO users (email, display_name, password_hash, gender) VALUES (%s, %s, %s, %s)
    ON CONFLICT (email) DO NOTHING
    RETURNING id::text AS id
"""
SET_GENDER_IF_UNSET = "UPDATE users SET gender = %s WHERE email = %s AND gender IS NULL"


@dataclass(frozen=True)
class User:
    id: str
    email: str
    display_name: str
    gender: Gender | None = None


class UserStore:
    """Reads and creates users. The pool must use dict rows and autocommit (see ``make_pool``)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_with_hash(self, email: str) -> tuple[User, str] | None:
        record = self._one(SELECT_BY_EMAIL, (email.strip().lower(),))
        if record is None:
            return None
        return _user(record), record["password_hash"]

    def get(self, user_id: str) -> User | None:
        try:
            record = self._one(SELECT_BY_ID, (user_id,))
        except Exception:  # a malformed id in a forged token is simply "no such user"
            return None
        return _user(record) if record else None

    def add_if_missing(
        self, email: str, display_name: str, password_hash: str, gender: Gender | None = None
    ) -> bool:
        record = self._one(INSERT_IF_MISSING, (email.lower(), display_name, password_hash, gender))
        return record is not None

    def set_gender_if_unset(self, email: str, gender: Gender) -> None:
        """Fill in a missing gender; one that is already set is never changed."""
        with self._pool.connection() as connection:
            connection.execute(SET_GENDER_IF_UNSET, (gender, email.lower()))

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            return cast("dict[str, Any] | None", connection.execute(sql, params).fetchone())


def _user(record: dict[str, Any]) -> User:
    return User(
        record["id"],
        record["email"],
        record["display_name"],
        cast("Gender | None", record["gender"]),
    )


def seed_demo_users(store: UserStore, demo_users: Sequence[DemoUser]) -> int:
    """Create any demo account that does not exist yet; never change an existing one.

    Changing a password in the secret therefore does not alter an account that already exists.
    The one exception is a gender that was never set (accounts made before it existed): it is
    filled in, and a gender that is already set is left alone.
    """
    created = 0
    for demo in demo_users:
        password_hash = hash_password(demo.password.get_secret_value())
        if store.add_if_missing(demo.email, demo.name, password_hash, demo.gender):
            created += 1
        elif demo.gender:
            store.set_gender_if_unset(demo.email, demo.gender)
    return created
