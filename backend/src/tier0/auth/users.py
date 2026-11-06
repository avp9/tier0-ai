from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from psycopg_pool import ConnectionPool

from tier0.auth.passwords import hash_password
from tier0.config import DemoUser

SELECT_BY_EMAIL = (
    "SELECT id::text AS id, email, display_name, password_hash FROM users WHERE email = %s"
)
SELECT_BY_ID = "SELECT id::text AS id, email, display_name FROM users WHERE id = %s::uuid"
INSERT_IF_MISSING = """
    INSERT INTO users (email, display_name, password_hash) VALUES (%s, %s, %s)
    ON CONFLICT (email) DO NOTHING
    RETURNING id::text AS id
"""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    display_name: str


class UserStore:
    """Reads and creates users. The pool must use dict rows and autocommit (see ``make_pool``)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_with_hash(self, email: str) -> tuple[User, str] | None:
        record = self._one(SELECT_BY_EMAIL, (email.strip().lower(),))
        if record is None:
            return None
        user = User(record["id"], record["email"], record["display_name"])
        return user, record["password_hash"]

    def get(self, user_id: str) -> User | None:
        try:
            record = self._one(SELECT_BY_ID, (user_id,))
        except Exception:  # a malformed id in a forged token is simply "no such user"
            return None
        return User(record["id"], record["email"], record["display_name"]) if record else None

    def add_if_missing(self, email: str, display_name: str, password_hash: str) -> bool:
        record = self._one(INSERT_IF_MISSING, (email.lower(), display_name, password_hash))
        return record is not None

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            return cast("dict[str, Any] | None", connection.execute(sql, params).fetchone())


def seed_demo_users(store: UserStore, demo_users: Sequence[DemoUser]) -> int:
    """Create any demo account that does not exist yet; never change an existing one.

    Changing a password in the secret therefore does not alter an account that already exists.
    """
    created = 0
    for demo in demo_users:
        password_hash = hash_password(demo.password.get_secret_value())
        if store.add_if_missing(demo.email, demo.name, password_hash):
            created += 1
    return created
