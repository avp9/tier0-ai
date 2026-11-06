from __future__ import annotations

from typing import Protocol

from tier0.tickets.models import CreatedTicket, TicketDetails, TicketDraft, TicketSummary


class TicketAdapter(Protocol):
    """The ticketing platform, behind the small surface the agent needs.

    Every read is scoped to one requester; implementations must never return another
    requester's tickets.
    """

    def create_ticket(self, draft: TicketDraft) -> CreatedTicket:
        """Raises TicketServiceUnavailable, TicketRejected or TicketOutcomeUnknown."""
        ...

    def find_tickets(self, requester_email: str) -> list[TicketSummary]:
        """The requester's tickets, newest first. Raises TicketServiceUnavailable."""
        ...

    def get_ticket(self, key: str, requester_email: str) -> TicketDetails:
        """Raises TicketNotFound if the key does not belong to the requester."""
        ...

    def find_by_submission_id(
        self, submission_id: str, requester_email: str
    ) -> TicketSummary | None:
        """Look a ticket up by the id it was created with, to resolve an unknown outcome."""
        ...
