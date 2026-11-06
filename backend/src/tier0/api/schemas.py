from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from tier0.agent.service import MAX_MESSAGE_CHARS, Awaiting, ChatTurn, ThreadView
from tier0.intent.types import Intent, Turn
from tier0.rag.answering import Source

IntentName = Literal[
    "knowledge", "create_ticket", "ticket_status", "smalltalk", "out_of_scope", "ambiguous"
]
INTENT_NAMES: dict[Intent, IntentName] = {
    Intent.KNOWLEDGE_QUESTION: "knowledge",
    Intent.CREATE_TICKET: "create_ticket",
    Intent.TICKET_STATUS: "ticket_status",
    Intent.SMALLTALK: "smalltalk",
    Intent.OUT_OF_SCOPE: "out_of_scope",
    Intent.AMBIGUOUS: "ambiguous",
}


class ChatRequest(BaseModel):
    thread_id: UUID | None = None
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
    ]


class TicketPreview(BaseModel):
    requester_email: str
    title: str | None
    description: str | None
    category: Literal["IT", "HR"] | None
    priority: Literal["Low", "Medium", "High", "Critical"] | None


class TicketOption(BaseModel):
    ticket_key: str
    title: str
    status: str


class PendingAction(BaseModel):
    """What the agent is waiting for. The UI answers by sending an ordinary message."""

    type: Literal["offer_ticket", "collect_ticket_details", "confirm_ticket", "select_ticket"]
    prompt: str
    preview: TicketPreview | None = None
    options: list[TicketOption] = Field(default_factory=list)


class TicketRef(BaseModel):
    ticket_key: str
    url: str
    status: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    intent: IntentName | None = None
    sources: list[Source] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    ticket: TicketRef | None = None


class HistoryResponse(BaseModel):
    thread_id: str
    messages: list[Turn]
    pending_action: PendingAction | None = None


def _pending_action(turn: ChatTurn, requester_email: str) -> PendingAction | None:
    if turn.awaiting is None:
        return None
    action = PendingAction(type=turn.awaiting.value, prompt=turn.reply)
    if turn.awaiting is Awaiting.CONFIRM_TICKET and turn.pending_ticket is not None:
        ticket = turn.pending_ticket
        action.preview = TicketPreview(
            requester_email=requester_email,
            title=ticket.summary,
            description=ticket.description,
            category=ticket.category,
            priority=ticket.priority,
        )
    if turn.awaiting is Awaiting.SELECT_TICKET:
        action.options = [
            TicketOption(ticket_key=t.key, title=t.title, status=t.status)
            for t in turn.ticket_choices
        ]
    return action


def to_chat_response(turn: ChatTurn, requester_email: str) -> ChatResponse:
    created = turn.created_ticket
    return ChatResponse(
        thread_id=turn.thread_id,
        reply=turn.reply,
        intent=INTENT_NAMES[turn.intent.intent] if turn.intent else None,
        sources=turn.sources,
        pending_action=_pending_action(turn, requester_email),
        ticket=TicketRef(ticket_key=created.key, url=created.url) if created else None,
    )


def to_history_response(view: ThreadView, requester_email: str) -> HistoryResponse:
    return HistoryResponse(
        thread_id=view.thread_id,
        messages=view.messages,
        pending_action=_pending_action(view.pending, requester_email) if view.pending else None,
    )
