from __future__ import annotations

import threading
from enum import StrEnum
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from tier0.agent.graph import AgentDependencies, build_conversation_graph
from tier0.agent.state import PendingTicket, WorkflowStatus
from tier0.intent.types import IntentResult, Turn
from tier0.rag.answering import Source
from tier0.tickets.models import CreatedTicket, TicketSummary, validate_email

MAX_MESSAGE_CHARS = 2000
LOCK_STRIPES = 256


class Awaiting(StrEnum):
    """What the agent is waiting for the employee to answer."""

    OFFER_TICKET = "offer_ticket"
    TICKET_DETAILS = "collect_ticket_details"
    CONFIRM_TICKET = "confirm_ticket"
    SELECT_TICKET = "select_ticket"


class ThreadNotFound(Exception):
    """No such conversation for this employee (it may not exist, or may be someone else's)."""


class ThreadOwnershipError(ThreadNotFound):
    """The conversation belongs to a different employee."""


class ChatTurn(BaseModel):
    thread_id: str
    reply: str
    awaiting: Awaiting | None = None
    options: list[str] = Field(default_factory=list)
    preview: str | None = None
    sources: list[Source] = Field(default_factory=list)
    created_ticket: CreatedTicket | None = None
    intent: IntentResult | None = None
    workflow: WorkflowStatus = WorkflowStatus.IDLE
    pending_ticket: PendingTicket | None = None
    ticket_choices: list[TicketSummary] = Field(default_factory=list)


class ThreadView(BaseModel):
    """A conversation as the employee has seen it so far."""

    thread_id: str
    messages: list[Turn] = Field(default_factory=list)
    pending: ChatTurn | None = None


class AgentService:
    """Send one employee message and get the agent's reply, resuming a paused conversation."""

    def __init__(self, graph: CompiledStateGraph[Any, Any, Any, Any]) -> None:
        self._graph = graph
        # One lock per stripe of threads: two messages on the same conversation never run at once.
        self._locks = [threading.Lock() for _ in range(LOCK_STRIPES)]

    def _lock_for(self, thread_id: str) -> threading.Lock:
        return self._locks[hash(thread_id) % LOCK_STRIPES]

    def send(self, *, thread_id: str, requester_email: str, message: str) -> ChatTurn:
        email = validate_email(requester_email).lower()
        text = message.strip()
        if not text:
            raise ValueError("message must not be empty")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message must be at most {MAX_MESSAGE_CHARS} characters")

        config: Any = {"configurable": {"thread_id": thread_id}}
        with self._lock_for(thread_id):
            snapshot = self._graph.get_state(config)
            owner = snapshot.values.get("requester_email")
            if owner and str(owner).lower() != email:
                raise ThreadOwnershipError(thread_id)

            if snapshot.interrupts:  # the agent is waiting for exactly this message
                result = self._graph.invoke(Command(resume=text), config)
            else:
                result = self._graph.invoke(
                    {"requester_email": email, "user_message": text}, config
                )
            saved = dict(self._graph.get_state(config).values)
        return self._turn(thread_id, saved, result.get("__interrupt__"))

    def history(self, *, thread_id: str, requester_email: str) -> ThreadView:
        """The conversation so far. Raises ThreadNotFound unless it exists and is theirs."""
        email = validate_email(requester_email).lower()
        config: Any = {"configurable": {"thread_id": thread_id}}
        snapshot = self._graph.get_state(config)
        saved = dict(snapshot.values)
        owner = saved.get("requester_email")
        if not owner or str(owner).lower() != email:
            raise ThreadNotFound(thread_id)
        pending = self._turn(thread_id, saved, snapshot.interrupts) if snapshot.interrupts else None
        return ThreadView(thread_id=thread_id, messages=saved.get("turns", []), pending=pending)

    @staticmethod
    def _turn(thread_id: str, result: dict[str, Any], waiting: Any) -> ChatTurn:
        """Build the reply from the saved state, so it matches what a restart would see."""
        base: dict[str, Any] = {
            "thread_id": thread_id,
            "sources": result.get("sources") or [],
            "created_ticket": result.get("created_ticket"),
            "intent": result.get("intent"),
            "workflow": WorkflowStatus(result.get("workflow", WorkflowStatus.IDLE)),
            "pending_ticket": result.get("pending_ticket"),
            "ticket_choices": result.get("ticket_choices") or [],
        }
        if waiting:
            payload = dict(waiting[0].value)
            return ChatTurn(
                reply=payload["prompt"],
                awaiting=Awaiting(payload["type"]),
                options=payload.get("options", []),
                preview=payload.get("preview"),
                **base,
            )
        return ChatTurn(reply=result.get("reply", ""), **base)


def build_agent(deps: AgentDependencies, checkpointer: BaseCheckpointSaver[Any]) -> AgentService:
    return AgentService(build_conversation_graph(deps, checkpointer))
