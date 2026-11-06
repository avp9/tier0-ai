from __future__ import annotations


class TicketError(Exception):
    """Base class for ticketing failures."""


class TicketServiceUnavailable(TicketError):
    """The platform could not be reached or refused service. Nothing was created."""


class TicketRejected(TicketError):
    """The platform understood the request and refused it (for example invalid fields)."""


class TicketOutcomeUnknown(TicketError):
    """A create request was sent but its result is unknown: the ticket may or may not exist.

    Callers must look the ticket up by its submission id before retrying.
    """


class TicketNotFound(TicketError):
    """No ticket with that key belongs to the requester."""
