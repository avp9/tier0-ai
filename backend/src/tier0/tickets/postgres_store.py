from __future__ import annotations

from typing import Any, cast

from psycopg_pool import ConnectionPool

from tier0.tickets.submission import Submission, SubmissionStatus

Record = dict[str, Any]

INSERT_LIVE = """
    INSERT INTO ticket_submissions (thread_id, requester_email, payload_hash)
    VALUES (%s, %s, %s)
    ON CONFLICT (thread_id, payload_hash)
        WHERE status IN ('pending', 'submitted', 'unknown') DO NOTHING
    RETURNING id::text AS id, thread_id, requester_email, payload_hash, status, ticket_key,
              updated_at
"""
SELECT_LIVE = """
    SELECT id::text AS id, thread_id, requester_email, payload_hash, status, ticket_key,
           updated_at
    FROM ticket_submissions
    WHERE thread_id = %s AND payload_hash = %s AND status IN ('pending', 'submitted', 'unknown')
"""
SELECT_ONE = """
    SELECT id::text AS id, thread_id, requester_email, payload_hash, status, ticket_key,
           updated_at
    FROM ticket_submissions WHERE id = %s::uuid
"""
UPDATE_STATUS = """
    UPDATE ticket_submissions SET status = %s, ticket_key = %s, updated_at = now()
    WHERE id = %s::uuid
"""


def _submission(record: Record) -> Submission:
    return Submission(
        id=record["id"],
        thread_id=record["thread_id"],
        requester_email=record["requester_email"],
        payload_hash=record["payload_hash"],
        status=SubmissionStatus(record["status"]),
        updated_at=record["updated_at"],
        ticket_key=record["ticket_key"],
    )


class PostgresSubmissionStore:
    """Stores ticket submissions in the ``ticket_submissions`` table.

    The pool must use ``row_factory=dict_row`` and ``autocommit=True`` (see ``make_pool``).
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def begin(
        self, thread_id: str, requester_email: str, payload_hash: str
    ) -> tuple[Submission, bool]:
        # The partial unique index makes concurrent callers converge on one live row.
        for _ in range(3):
            inserted = self._one(INSERT_LIVE, (thread_id, requester_email.lower(), payload_hash))
            if inserted:
                return _submission(inserted), True
            existing = self._one(SELECT_LIVE, (thread_id, payload_hash))
            if existing:
                return _submission(existing), False
        raise RuntimeError("could not create or find a live submission")

    def live(self, thread_id: str, payload_hash: str) -> Submission | None:
        record = self._one(SELECT_LIVE, (thread_id, payload_hash))
        return _submission(record) if record else None

    def mark_submitted(self, submission_id: str, ticket_key: str) -> None:
        self._run(UPDATE_STATUS, (SubmissionStatus.SUBMITTED.value, ticket_key, submission_id))

    def mark(self, submission_id: str, status: SubmissionStatus) -> None:
        if status is SubmissionStatus.SUBMITTED:
            raise ValueError("use mark_submitted so the ticket key is recorded")
        self._run(UPDATE_STATUS, (status.value, None, submission_id))

    def get(self, submission_id: str) -> Submission | None:
        record = self._one(SELECT_ONE, (submission_id,))
        return _submission(record) if record else None

    def _one(self, sql: str, params: tuple[Any, ...]) -> Record | None:
        with self._pool.connection() as connection:
            return cast("Record | None", connection.execute(sql, params).fetchone())

    def _run(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._pool.connection() as connection:
            connection.execute(sql, params)
