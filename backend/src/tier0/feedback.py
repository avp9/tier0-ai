"""Thumbs up or down on an answer.

Every turn has an id. The browser is given that id signed for the person it was issued to, so a
vote can only be cast on an answer the server really gave that person: nobody can vote on someone
else's answer, or on an id they made up. Votes are stored here, and also sent to LangSmith, against
the turn's trace, when tracing is on.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Any, cast

from psycopg_pool import ConnectionPool

GOOD, BAD = 1, 0
SIGNATURE_CHARS = 24
UPSERT = """
    INSERT INTO answer_feedback (trace_id, requester_email, score, comment)
    VALUES (%s::uuid, %s, %s, %s)
    ON CONFLICT (trace_id) DO UPDATE
        SET score = EXCLUDED.score, comment = EXCLUDED.comment, updated_at = now()
    WHERE answer_feedback.requester_email = EXCLUDED.requester_email
"""
SELECT = (
    "SELECT trace_id::text AS trace_id, score, comment FROM answer_feedback "
    "WHERE trace_id = %s::uuid AND requester_email = %s"
)


def _signature(secret: str, trace_id: str, email: str) -> str:
    message = f"feedback:{trace_id}:{email.strip().lower()}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:SIGNATURE_CHARS]


def issue_token(secret: str, trace_id: str, email: str) -> str:
    """What the browser holds for one answer: the turn's id, proof it was issued to this person."""
    return f"{trace_id}.{_signature(secret, trace_id, email)}"


def read_token(secret: str, token: str, email: str) -> str | None:
    """The turn id in a token, if the server issued it to this person; otherwise None."""
    trace_id, _, signature = token.partition(".")
    try:
        uuid.UUID(trace_id)
    except ValueError:
        return None
    expected = _signature(secret, trace_id, email)
    return trace_id if hmac.compare_digest(signature, expected) else None


@dataclass(frozen=True)
class Vote:
    trace_id: str
    good: bool
    comment: str | None


class FeedbackStore:
    """Reads and writes votes. The pool must use dict rows and autocommit (see ``make_pool``)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def save(self, trace_id: str, email: str, *, good: bool, comment: str | None) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                UPSERT, (trace_id, email.lower(), GOOD if good else BAD, comment or None)
            )

    def clear(self, trace_id: str, email: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "DELETE FROM answer_feedback WHERE trace_id = %s::uuid AND requester_email = %s",
                (trace_id, email.lower()),
            )

    def get(self, trace_id: str, email: str) -> Vote | None:
        with self._pool.connection() as connection:
            row = cast(
                "dict[str, Any] | None",
                connection.execute(SELECT, (trace_id, email.lower())).fetchone(),
            )
        return Vote(row["trace_id"], row["score"] == GOOD, row["comment"]) if row else None

    def all(self) -> list[Vote]:
        """Every vote. Only the test controls use this."""
        with self._pool.connection() as connection:
            rows = cast(
                "list[dict[str, Any]]",
                connection.execute(
                    "SELECT trace_id::text AS trace_id, score, comment FROM answer_feedback "
                    "ORDER BY updated_at"
                ).fetchall(),
            )
        return [Vote(r["trace_id"], r["score"] == GOOD, r["comment"]) for r in rows]

    def delete_all(self) -> None:
        """Forget every vote. Only the test controls use this."""
        with self._pool.connection() as connection:
            connection.execute("DELETE FROM answer_feedback")
