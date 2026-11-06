from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, TypedDict

from pydantic import BaseModel

from tier0.intent.types import IntentResult, Turn
from tier0.rag.answerability import AnswerabilityDecision
from tier0.rag.answering import Source
from tier0.tickets.models import Category, CreatedTicket, Priority, TicketSummary


class WorkflowStatus(StrEnum):
    """Where the conversation is in a multi-step workflow."""

    IDLE = "idle"
    OFFERED_TICKET = "offered_ticket"
    COLLECTING_TICKET_FIELDS = "collecting_ticket_fields"
    AWAITING_TICKET_CONFIRMATION = "awaiting_ticket_confirmation"
    SUBMITTING_TICKET = "submitting_ticket"
    AWAITING_TICKET_SELECTION = "awaiting_ticket_selection"


S = WorkflowStatus
ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    S.IDLE: frozenset({S.IDLE, S.OFFERED_TICKET, S.COLLECTING_TICKET_FIELDS,
                       S.AWAITING_TICKET_SELECTION}),
    S.OFFERED_TICKET: frozenset({S.OFFERED_TICKET, S.COLLECTING_TICKET_FIELDS, S.IDLE}),
    S.COLLECTING_TICKET_FIELDS: frozenset({S.COLLECTING_TICKET_FIELDS,
                                           S.AWAITING_TICKET_CONFIRMATION, S.IDLE}),
    S.AWAITING_TICKET_CONFIRMATION: frozenset({S.AWAITING_TICKET_CONFIRMATION,
                                               S.COLLECTING_TICKET_FIELDS, S.SUBMITTING_TICKET,
                                               S.IDLE}),
    S.SUBMITTING_TICKET: frozenset({S.IDLE, S.AWAITING_TICKET_CONFIRMATION}),
    S.AWAITING_TICKET_SELECTION: frozenset({S.AWAITING_TICKET_SELECTION, S.IDLE}),
}  # fmt: skip


class IllegalTransition(Exception):
    """A workflow step was attempted from a state that does not allow it."""


def transition(current: WorkflowStatus, target: WorkflowStatus) -> WorkflowStatus:
    """Return ``target`` if the move is allowed.

    The key guarantee: a ticket can only be submitted from AWAITING_TICKET_CONFIRMATION.
    """
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"cannot move from {current.value} to {target.value}")
    return target


class PendingTicket(BaseModel):
    """A ticket being put together. The requester's email comes from the login, not from here."""

    summary: str | None = None
    description: str | None = None
    category: Category | None = None
    priority: Priority | None = None
    submission_id: str | None = None

    def missing_fields(self) -> list[str]:
        required = {"summary": self.summary, "description": self.description}
        return [name for name, value in required.items() if not (value and value.strip())]

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields()


class AgentState(TypedDict, total=False):
    requester_email: str
    turns: Annotated[list[Turn], operator.add]
    user_message: str
    intent: IntentResult | None
    workflow: WorkflowStatus
    answer_decision: AnswerabilityDecision | None
    pending_ticket: PendingTicket | None
    ticket_choices: list[TicketSummary]
    selected_ticket_key: str | None
    selection_attempts: int
    sources: list[Source]
    created_ticket: CreatedTicket | None
    reply: str


def workflow_of(state: AgentState) -> WorkflowStatus:
    """The workflow status as an enum. Checkpoints return it as a plain string, so coerce."""
    return WorkflowStatus(state.get("workflow", WorkflowStatus.IDLE))


def initial_state(requester_email: str) -> AgentState:
    return AgentState(
        requester_email=requester_email,
        turns=[],
        workflow=WorkflowStatus.IDLE,
        intent=None,
        answer_decision=None,
        pending_ticket=None,
        ticket_choices=[],
        selected_ticket_key=None,
        selection_attempts=0,
        sources=[],
        created_ticket=None,
        reply="",
    )
