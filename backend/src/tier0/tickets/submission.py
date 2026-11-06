from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from tier0.agent.state import PendingTicket
from tier0.tickets.base import TicketAdapter
from tier0.tickets.errors import (
    TicketError,
    TicketOutcomeUnknown,
    TicketRejected,
    TicketServiceUnavailable,
)
from tier0.tickets.models import Category, CreatedTicket, TicketDraft

log = logging.getLogger(__name__)

IN_PROGRESS_WINDOW = timedelta(seconds=30)
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class SubmissionStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


LIVE_STATUSES = frozenset(
    {SubmissionStatus.PENDING, SubmissionStatus.SUBMITTED, SubmissionStatus.UNKNOWN}
)


@dataclass(frozen=True)
class Submission:
    id: str
    thread_id: str
    requester_email: str
    payload_hash: str
    status: SubmissionStatus
    updated_at: datetime
    ticket_key: str | None = None


class SubmissionStore(Protocol):
    def begin(
        self, thread_id: str, requester_email: str, payload_hash: str
    ) -> tuple[Submission, bool]:
        """The live submission for this conversation and content, creating one if none exists.

        Returns (submission, created_now).
        """
        ...

    def live(self, thread_id: str, payload_hash: str) -> Submission | None:
        """The live submission for this conversation and content, if any. Creates nothing."""
        ...

    def mark_submitted(self, submission_id: str, ticket_key: str) -> None: ...

    def mark(self, submission_id: str, status: SubmissionStatus) -> None: ...

    def get(self, submission_id: str) -> Submission | None: ...


class InMemorySubmissionStore:
    """Same rules as the database, for fast tests."""

    def __init__(self, clock: Clock = utc_now) -> None:
        self.rows: dict[str, Submission] = {}
        self._clock = clock

    def live(self, thread_id: str, payload_hash: str) -> Submission | None:
        for row in self.rows.values():
            if (
                row.thread_id == thread_id
                and row.payload_hash == payload_hash
                and row.status in LIVE_STATUSES
            ):
                return row
        return None

    def begin(
        self, thread_id: str, requester_email: str, payload_hash: str
    ) -> tuple[Submission, bool]:
        existing = self.live(thread_id, payload_hash)
        if existing:
            return existing, False
        row = Submission(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            requester_email=requester_email.lower(),
            payload_hash=payload_hash,
            status=SubmissionStatus.PENDING,
            updated_at=self._clock(),
        )
        self.rows[row.id] = row
        return row, True

    def mark_submitted(self, submission_id: str, ticket_key: str) -> None:
        self.rows[submission_id] = replace(
            self.rows[submission_id],
            status=SubmissionStatus.SUBMITTED,
            ticket_key=ticket_key,
            updated_at=self._clock(),
        )

    def mark(self, submission_id: str, status: SubmissionStatus) -> None:
        if status is SubmissionStatus.SUBMITTED:
            raise ValueError("use mark_submitted so the ticket key is recorded")
        self.rows[submission_id] = replace(
            self.rows[submission_id], status=status, ticket_key=None, updated_at=self._clock()
        )

    def get(self, submission_id: str) -> Submission | None:
        return self.rows.get(submission_id)


def payload_hash(requester_email: str, ticket: PendingTicket) -> str:
    """Identify a ticket's content, so the same request in the same conversation is one ticket."""
    canonical = json.dumps(
        [
            requester_email.strip().lower(),
            (ticket.summary or "").strip(),
            (ticket.description or "").strip(),
            ticket.category,
            ticket.priority,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class SubmitOutcome(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    ALREADY_CREATED = "already_created"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SubmitResult:
    outcome: SubmitOutcome
    reply: str
    ticket_key: str | None = None
    ticket_url: str | None = None

    @property
    def retryable(self) -> bool:
        return self.outcome in (
            SubmitOutcome.UNKNOWN,
            SubmitOutcome.UNAVAILABLE,
            SubmitOutcome.IN_PROGRESS,
        )

    @property
    def has_ticket(self) -> bool:
        return self.outcome in (SubmitOutcome.CREATED, SubmitOutcome.ALREADY_CREATED)


def created_reply(key: str, url: str | None) -> str:
    where = f" You can follow it here: {url}" if url else ""
    return f"Done. I've created ticket {key}.{where} Ask me for its status any time."


def already_reply(key: str, url: str | None) -> str:
    where = f" ({url})" if url else ""
    return f"That ticket already exists: {key}{where}. I haven't created a second one."


IN_PROGRESS_REPLY = (
    "I'm already creating that ticket. Give me a moment, then ask me for its status."
)
UNKNOWN_REPLY = (
    "I sent your request, but the ticketing system didn't confirm it, so I can't tell you yet "
    "whether the ticket was created. I won't create a duplicate. Reply yes and I'll check again "
    "and only create it if it isn't there, or no to leave it."
)
UNAVAILABLE_REPLY = (
    "I couldn't create your ticket because the ticketing system isn't reachable right now. "
    "Nothing was created. Reply yes to try again, or no to cancel."
)
REJECTED_REPLY = (
    "The ticketing system couldn't accept this ticket, so nothing was created. "
    "Please contact the service desk directly so they can help."
)


class TicketSubmissionService:
    def __init__(
        self, adapter: TicketAdapter, store: SubmissionStore, clock: Clock = utc_now
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._clock = clock

    def submit(
        self, *, thread_id: str, requester_email: str, ticket: PendingTicket
    ) -> SubmitResult:
        """Create the ticket exactly once, however often this is called for the same content."""
        summary, description, category = self._require_complete(ticket)
        email = requester_email.strip().lower()
        row, created_now = self._store.begin(thread_id, email, payload_hash(email, ticket))

        if row.status is SubmissionStatus.SUBMITTED and row.ticket_key:
            return self._already(row.ticket_key, email)

        if not created_now:
            if row.status is SubmissionStatus.PENDING and (
                self._clock() - row.updated_at < IN_PROGRESS_WINDOW
            ):
                # Another attempt is creating this very ticket right now (a double click).
                return SubmitResult(SubmitOutcome.IN_PROGRESS, IN_PROGRESS_REPLY)
            # An earlier attempt left no clear result. Look before creating anything.
            try:
                found = self._adapter.find_by_submission_id(row.id, email)
            except TicketServiceUnavailable:
                return SubmitResult(SubmitOutcome.UNAVAILABLE, UNAVAILABLE_REPLY)
            if found:
                self._store.mark_submitted(row.id, found.key)
                return self._already(found.key, email)

        draft = TicketDraft(
            requester_email=email,
            summary=summary,
            description=description,
            category=category,
            priority=ticket.priority,
            submission_id=row.id,
        )
        try:
            created = self._adapter.create_ticket(draft)
        except TicketOutcomeUnknown:
            self._store.mark(row.id, SubmissionStatus.UNKNOWN)
            return self._reconcile_once(row.id, email)
        except TicketServiceUnavailable:
            self._store.mark(row.id, SubmissionStatus.FAILED)
            return SubmitResult(SubmitOutcome.UNAVAILABLE, UNAVAILABLE_REPLY)
        except TicketRejected as error:
            log.warning("ticket rejected by platform: %s", error)
            self._store.mark(row.id, SubmissionStatus.FAILED)
            return SubmitResult(SubmitOutcome.REJECTED, REJECTED_REPLY)
        return self._record(row.id, created)

    @staticmethod
    def _require_complete(ticket: PendingTicket) -> tuple[str, str, Category]:
        if not ticket.is_complete or ticket.category is None:
            raise ValueError("a ticket needs a summary, description and category before submission")
        return str(ticket.summary).strip(), str(ticket.description).strip(), ticket.category

    def _record(self, submission_id: str, created: CreatedTicket) -> SubmitResult:
        self._store.mark_submitted(submission_id, created.key)
        return SubmitResult(
            SubmitOutcome.CREATED, created_reply(created.key, created.url), created.key, created.url
        )

    def _already(self, key: str, email: str) -> SubmitResult:
        url: str | None = None
        try:
            url = self._adapter.get_ticket(key, email).summary.url
        except TicketError:
            pass
        return SubmitResult(SubmitOutcome.ALREADY_CREATED, already_reply(key, url), key, url)

    def _reconcile_once(self, submission_id: str, email: str) -> SubmitResult:
        try:
            found = self._adapter.find_by_submission_id(submission_id, email)
        except TicketError:
            found = None
        if found:
            self._store.mark_submitted(submission_id, found.key)
            return SubmitResult(
                SubmitOutcome.CREATED, created_reply(found.key, found.url), found.key, found.url
            )
        return SubmitResult(SubmitOutcome.UNKNOWN, UNKNOWN_REPLY)
