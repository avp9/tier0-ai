from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from tier0.tickets.base import TicketAdapter
from tier0.tickets.errors import TicketNotFound, TicketServiceUnavailable
from tier0.tickets.models import TicketComment, TicketDetails, TicketSummary

SHOWN_COMMENTS = 3
COMMENT_CHARS = 300

NONE_REPLY = "I couldn't find any support tickets raised from your account."
UNAVAILABLE_REPLY = (
    "I couldn't reach the ticketing system just now, so I can't check your tickets. "
    "Please try again in a few minutes."
)
NOT_FOUND_REPLY = "I couldn't find that ticket among yours. Please pick one from the list."
CHOOSE_REPLY = (
    "Which ticket would you like details on? Reply with its key (for example SUP-12) or its number."
)
NO_UPDATES = "No updates from the support team yet."

_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "7th": 7, "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9, "tenth": 10, "10th": 10,
}  # fmt: skip
_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9_]+-\d+\b")
_BARE_NUMBER = re.compile(r"^\W*(\d{1,2})\W*$")
_PREFIXED_NUMBER = re.compile(
    r"(?<![A-Za-z0-9-])(?:number|no\.|option|ticket|#)\s*#?\s*(\d{1,2})(?![\d-])", re.I
)


class StatusOutcome(StrEnum):
    NONE_FOUND = "none_found"
    ONE = "one"
    MULTIPLE = "multiple"
    SELECTED = "selected"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class StatusResult(BaseModel):
    outcome: StatusOutcome
    reply: str
    tickets: list[TicketSummary] = Field(default_factory=list)  # the choices, when MULTIPLE
    details: TicketDetails | None = None


def _when(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC") if value else ""


def _line(ticket: TicketSummary) -> str:
    updated = f", updated {_when(ticket.updated_at)}" if ticket.updated_at else ""
    return f"{ticket.key}: {ticket.title} [{ticket.status}{updated}]"


def _comment_line(comment: TicketComment) -> str:
    body = " ".join(comment.body.split())
    if len(body) > COMMENT_CHARS:
        body = body[: COMMENT_CHARS - 1] + "…"
    when = f" ({_when(comment.created_at)})" if comment.created_at else ""
    return f"- {comment.author}{when}: {body}"


def format_details(details: TicketDetails) -> str:
    ticket = details.summary
    updated = f" Last updated {_when(ticket.updated_at)}." if ticket.updated_at else ""
    head = f"Ticket {ticket.key}: {ticket.title}\nStatus: {ticket.status}.{updated}\n{ticket.url}"
    if not details.comments:
        return f"{head}\n\n{NO_UPDATES}"
    latest = details.comments[-SHOWN_COMMENTS:]
    return f"{head}\n\nLatest updates from the support team:\n" + "\n".join(
        _comment_line(c) for c in latest
    )


def format_choices(tickets: list[TicketSummary]) -> str:
    lines = [f"{number}. {_line(t)}" for number, t in enumerate(tickets, start=1)]
    return f"You have {len(tickets)} tickets:\n" + "\n".join(lines) + f"\n\n{CHOOSE_REPLY}"


class TicketStatusService:
    def __init__(self, adapter: TicketAdapter) -> None:
        self._adapter = adapter

    def lookup(self, requester_email: str) -> StatusResult:
        """Answer "what is the status of my tickets?" for the signed-in requester."""
        try:
            tickets = self._adapter.find_tickets(requester_email)
        except TicketServiceUnavailable:
            return StatusResult(outcome=StatusOutcome.UNAVAILABLE, reply=UNAVAILABLE_REPLY)
        if not tickets:
            return StatusResult(outcome=StatusOutcome.NONE_FOUND, reply=NONE_REPLY)
        if len(tickets) == 1:
            return self._selected(tickets[0].key, requester_email, StatusOutcome.ONE)
        return StatusResult(
            outcome=StatusOutcome.MULTIPLE, reply=format_choices(tickets), tickets=tickets
        )

    def details(self, key: str, requester_email: str) -> StatusResult:
        """Details for one of the requester's tickets, after they chose it from a list."""
        return self._selected(key, requester_email, StatusOutcome.SELECTED)

    def _selected(self, key: str, requester_email: str, outcome: StatusOutcome) -> StatusResult:
        try:
            details = self._adapter.get_ticket(key, requester_email)
        except TicketServiceUnavailable:
            return StatusResult(outcome=StatusOutcome.UNAVAILABLE, reply=UNAVAILABLE_REPLY)
        except TicketNotFound:
            return StatusResult(outcome=StatusOutcome.NOT_FOUND, reply=NOT_FOUND_REPLY)
        return StatusResult(outcome=outcome, reply=format_details(details), details=details)


@dataclass(frozen=True)
class Selection:
    ticket: TicketSummary | None
    reason: str  # "key", "position" or why nothing was chosen


def choose_ticket(text: str, options: list[TicketSummary]) -> Selection:
    """Work out which listed ticket the employee means; never guess between several.

    Every reference in the message (a key, an ordinal such as "second", a number) must
    point at the same single ticket, otherwise nothing is chosen and the agent asks again.
    """
    by_key = {t.key.casefold(): t for t in options}
    chosen: dict[str, TicketSummary] = {}
    unknown_key = out_of_range = False

    keys = {m.group().casefold() for m in _KEY.finditer(text)}
    for key in keys:
        if key in by_key:
            chosen[by_key[key].key] = by_key[key]
        else:
            unknown_key = True

    words = re.findall(r"[a-z0-9]+", text.lower())
    positions = {_ORDINALS[w] for w in words if w in _ORDINALS}
    positions |= {int(m.group(1)) for m in _PREFIXED_NUMBER.finditer(text)}
    if bare := _BARE_NUMBER.match(text):
        positions.add(int(bare.group(1)))
    for position in positions:
        if 1 <= position <= len(options):
            chosen[options[position - 1].key] = options[position - 1]
        else:
            out_of_range = True

    if unknown_key:
        return Selection(None, "unknown_key")
    if out_of_range:
        return Selection(None, "out_of_range")
    if len(chosen) > 1:
        return Selection(None, "ambiguous")
    if chosen:
        return Selection(next(iter(chosen.values())), "key" if keys else "position")
    return Selection(None, "not_understood")
