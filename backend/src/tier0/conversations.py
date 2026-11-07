from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg_pool import ConnectionPool

TITLE_CHARS = 80
DEFAULT_LIMIT = 50

# The title is the employee's first message; later messages only move the chat to the top.
TOUCH = """
    INSERT INTO conversations (thread_id, requester_email, title)
    VALUES (%s, %s, %s)
    ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()
    WHERE conversations.requester_email = EXCLUDED.requester_email
"""
RECENT = """
    SELECT thread_id, title, updated_at FROM conversations
    WHERE requester_email = %s ORDER BY updated_at DESC LIMIT %s
"""


@dataclass(frozen=True)
class Conversation:
    thread_id: str
    title: str
    updated_at: datetime


def make_title(message: str) -> str:
    """One line from the first message, cut to a length that fits a sidebar row."""
    text = " ".join(message.split())
    if len(text) <= TITLE_CHARS:
        return text or "New chat"
    return text[: TITLE_CHARS - 1].rstrip() + "…"


class ConversationStore:
    """Which chats an employee has had, newest first. Uses the pool of ``make_pool``."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def touch(self, thread_id: str, email: str, first_message: str) -> None:
        """Record a finished turn: add the chat if it is new, otherwise move it to the top."""
        with self._pool.connection() as connection:
            connection.execute(TOUCH, (thread_id, email.lower(), make_title(first_message)))

    def recent(self, email: str, limit: int = DEFAULT_LIMIT) -> list[Conversation]:
        with self._pool.connection() as connection:
            rows = cast(
                "list[dict[str, Any]]",
                connection.execute(RECENT, (email.lower(), limit)).fetchall(),
            )
        return [Conversation(r["thread_id"], r["title"], r["updated_at"]) for r in rows]

    def clear(self) -> None:
        """Forget every chat. Only the test controls use this."""
        with self._pool.connection() as connection:
            connection.execute("DELETE FROM conversations")
