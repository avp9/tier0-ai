from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from tier0.tickets.errors import TicketError, TicketNotFound, TicketOutcomeUnknown
from tier0.tickets.models import (
    CreatedTicket,
    TicketComment,
    TicketDetails,
    TicketDraft,
    TicketSummary,
    validate_email,
    validate_submission_id,
    validate_ticket_key,
)


@dataclass
class StoredTicket:
    key: str
    draft: TicketDraft
    status: str = "Open"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    comments: list[tuple[TicketComment, bool]] = field(default_factory=list)  # (comment, internal)


class FakeTicketAdapter:
    """In-memory ticketing platform for tests and CI.

    Like the real platform it does not deduplicate: creating twice makes two tickets.
    """

    def __init__(self, project_key: str = "SUP", base_url: str = "https://tickets.example.invalid"):
        self.project_key = project_key
        self.base_url = base_url
        self.tickets: list[StoredTicket] = []
        self.create_calls = 0
        self._next_number = 1
        self._failure: TicketError | None = None
        self._lose_next_response = False

    def reset(self) -> None:
        """Forget every ticket and pending failure (used between end-to-end tests)."""
        self.tickets.clear()
        self.create_calls = 0
        self._next_number = 1
        self._failure = None
        self._lose_next_response = False

    def fail_next(self, error: TicketError) -> None:
        """The next call (create or read) raises ``error``; a create then stores nothing."""
        self._failure = error

    def lose_next_create_response(self) -> None:
        """The next create succeeds on the platform but the caller sees an unknown outcome."""
        self._lose_next_response = True

    def set_status(self, key: str, status: str) -> None:
        ticket = self._by_key(key)
        ticket.status, ticket.updated_at = status, datetime.now(UTC)

    def add_comment(self, key: str, author: str, body: str, *, internal: bool = False) -> None:
        ticket = self._by_key(key)
        comment = TicketComment(author=author, body=body, created_at=datetime.now(UTC))
        ticket.comments.append((comment, internal))

    def create_ticket(self, draft: TicketDraft) -> CreatedTicket:
        self.create_calls += 1
        self._raise_if_failing()
        key = f"{self.project_key}-{self._next_number}"
        self._next_number += 1
        self.tickets.append(StoredTicket(key=key, draft=draft))
        if self._lose_next_response:
            self._lose_next_response = False
            raise TicketOutcomeUnknown("response lost")
        return CreatedTicket(key=key, url=self._url(key))

    def find_tickets(self, requester_email: str) -> list[TicketSummary]:
        self._raise_if_failing()
        return [self._summary(t) for t in self._owned(requester_email)]

    def get_ticket(self, key: str, requester_email: str) -> TicketDetails:
        validate_ticket_key(key)
        self._raise_if_failing()
        for ticket in self._owned(requester_email):
            if ticket.key == key:
                public = [c for c, internal in ticket.comments if not internal]
                return TicketDetails(summary=self._summary(ticket), comments=public)
        raise TicketNotFound(key)

    def find_by_submission_id(
        self, submission_id: str, requester_email: str
    ) -> TicketSummary | None:
        validate_submission_id(submission_id)
        self._raise_if_failing()
        for ticket in self._owned(requester_email):
            if ticket.draft.submission_id == submission_id:
                return self._summary(ticket)
        return None

    def _owned(self, requester_email: str) -> list[StoredTicket]:
        email = validate_email(requester_email).casefold()
        mine = [t for t in self.tickets if t.draft.requester_email.casefold() == email]
        return sorted(mine, key=lambda t: t.updated_at, reverse=True)

    def _by_key(self, key: str) -> StoredTicket:
        for ticket in self.tickets:
            if ticket.key == key:
                return ticket
        raise KeyError(key)

    def _summary(self, ticket: StoredTicket) -> TicketSummary:
        return TicketSummary(
            key=ticket.key,
            title=ticket.draft.summary,
            status=ticket.status,
            updated_at=ticket.updated_at,
            url=self._url(ticket.key),
        )

    def _url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"

    def _raise_if_failing(self) -> None:
        if self._failure is not None:
            error, self._failure = self._failure, None
            raise error
