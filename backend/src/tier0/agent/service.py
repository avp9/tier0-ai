from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from tier0.agent.graph import STREAM_TOKENS, AgentDependencies, build_conversation_graph
from tier0.agent.state import PendingTicket, WorkflowStatus
from tier0.intent.types import IntentResult, Turn
from tier0.observability.tracing import Tracing
from tier0.rag.answering import Source
from tier0.tickets.drafting import TicketEdit
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


class NothingToEdit(Exception):
    """The conversation is not showing a ticket preview, so there is nothing to edit."""


class EditNotAllowed(Exception):
    """The ticket was already sent (or may have been), so it can no longer be changed."""


class ChatTurn(BaseModel):
    thread_id: str
    reply: str
    awaiting: Awaiting | None = None
    options: list[str] = Field(default_factory=list)
    preview: str | None = None
    editable: bool = False  # the preview can still be changed (nothing has been sent yet)
    cached: bool = False  # the reply was reused from an earlier, similar question
    trace_id: str | None = None  # identifies this turn (its LangSmith run, and what a vote is for)
    sources: list[Source] = Field(default_factory=list)
    created_ticket: CreatedTicket | None = None
    intent: IntentResult | None = None
    workflow: WorkflowStatus = WorkflowStatus.IDLE
    pending_ticket: PendingTicket | None = None
    ticket_choices: list[TicketSummary] = Field(default_factory=list)


@dataclass(frozen=True)
class TokenEvent:
    """A piece of the reply text, sent while the reply is still being written."""

    text: str


class ThreadView(BaseModel):
    """A conversation as the employee has seen it so far."""

    thread_id: str
    messages: list[Turn] = Field(default_factory=list)
    pending: ChatTurn | None = None


class AgentService:
    """Send one employee message and get the agent's reply, resuming a paused conversation."""

    def __init__(
        self, graph: CompiledStateGraph[Any, Any, Any, Any], tracing: Tracing | None = None
    ) -> None:
        self._graph = graph
        self._tracing = tracing
        # One lock per stripe of threads: two messages on the same conversation never run at once.
        self._locks = [threading.Lock() for _ in range(LOCK_STRIPES)]

    def _lock_for(self, thread_id: str) -> threading.Lock:
        return self._locks[hash(thread_id) % LOCK_STRIPES]

    @staticmethod
    def _clean(requester_email: str, message: str, *, needs_text: bool = True) -> tuple[str, str]:
        email = validate_email(requester_email).lower()
        text = message.strip()
        if needs_text and not text:
            raise ValueError("message must not be empty")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message must be at most {MAX_MESSAGE_CHARS} characters")
        return email, text

    def _check_owner(self, config: Any, email: str, thread_id: str) -> Any:
        snapshot = self._graph.get_state(config)
        owner = snapshot.values.get("requester_email")
        if owner and str(owner).lower() != email:
            raise ThreadOwnershipError(thread_id)
        return snapshot

    def _run_config(
        self, thread_id: str, user_id: str | None, *, stream_tokens: bool = False
    ) -> tuple[Any, str]:
        """How to run the graph for this turn, and the id that names the turn.

        Every turn has an id (a vote on an answer refers to it); with tracing on it is also the id
        of the LangSmith run, and the run carries the conversation, the tracer and its labels.
        """
        trace_id = str(uuid.uuid4())
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if stream_tokens:
            configurable[STREAM_TOKENS] = True
        config: dict[str, Any] = {"configurable": configurable, "run_id": uuid.UUID(trace_id)}
        if self._tracing is not None:
            config.update(self._tracing.config(thread_id=thread_id, user_id=user_id))
        return config, trace_id

    @staticmethod
    def _input(snapshot: Any, email: str, text: str, edit: TicketEdit | None = None) -> Any:
        if edit is not None:
            return Command(resume={"edit": edit.model_dump(mode="json")})
        if snapshot.interrupts:  # the agent is waiting for exactly this message
            return Command(resume=text)
        return {"requester_email": email, "user_message": text}

    @staticmethod
    def _check_edit(snapshot: Any) -> None:
        """An edit is only meaningful at the ticket preview, and only before anything was sent."""
        waiting = snapshot.interrupts
        payload = waiting[0].value if waiting else {}
        if not isinstance(payload, dict) or payload.get("type") != "confirm_ticket":
            raise NothingToEdit("there is no ticket preview to edit")
        if not payload.get("editable", False):
            raise EditNotAllowed("the ticket was already sent and can no longer be edited")

    def send(
        self,
        *,
        thread_id: str,
        requester_email: str,
        message: str = "",
        ticket_edit: TicketEdit | None = None,
        user_id: str | None = None,
    ) -> ChatTurn:
        email, text = self._clean(requester_email, message, needs_text=ticket_edit is None)
        config: Any = {"configurable": {"thread_id": thread_id}}
        run_config, trace_id = self._run_config(thread_id, user_id)
        with self._lock_for(thread_id):
            snapshot = self._check_owner(config, email, thread_id)
            if ticket_edit is not None:
                self._check_edit(snapshot)
            result = self._graph.invoke(self._input(snapshot, email, text, ticket_edit), run_config)
            saved = dict(self._graph.get_state(config).values)
        return self._turn(thread_id, saved, result.get("__interrupt__"), trace_id)

    def stream(
        self,
        *,
        thread_id: str,
        requester_email: str,
        message: str = "",
        ticket_edit: TicketEdit | None = None,
        user_id: str | None = None,
    ) -> Iterator[TokenEvent | ChatTurn]:
        """Like ``send``, but yields the reply text as it is written, then the finished turn.

        Problems with the request (empty message, someone else's conversation) are raised here,
        before anything is yielded, so the caller can still answer with an error status.
        """
        email, text = self._clean(requester_email, message, needs_text=ticket_edit is None)
        snapshot = self._check_owner({"configurable": {"thread_id": thread_id}}, email, thread_id)
        if ticket_edit is not None:
            self._check_edit(snapshot)
        return self._events(thread_id, email, text, ticket_edit, user_id)

    def _events(
        self,
        thread_id: str,
        email: str,
        text: str,
        ticket_edit: TicketEdit | None = None,
        user_id: str | None = None,
    ) -> Iterator[TokenEvent | ChatTurn]:
        config: Any = {"configurable": {"thread_id": thread_id, STREAM_TOKENS: True}}
        run_config, trace_id = self._run_config(thread_id, user_id, stream_tokens=True)
        with self._lock_for(thread_id):
            snapshot = self._check_owner(config, email, thread_id)
            if ticket_edit is not None:
                self._check_edit(snapshot)  # the state may have moved on since the request began
            for chunk in self._graph.stream(
                self._input(snapshot, email, text, ticket_edit), run_config, stream_mode="custom"
            ):
                token = chunk.get("token") if isinstance(chunk, dict) else None
                if token:
                    yield TokenEvent(text=str(token))
            final = self._graph.get_state(config)
            turn = self._turn(thread_id, dict(final.values), final.interrupts, trace_id)
        yield turn

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
    def _turn(
        thread_id: str, result: dict[str, Any], waiting: Any, trace_id: str | None = None
    ) -> ChatTurn:
        """Build the reply from the saved state, so it matches what a restart would see."""
        base: dict[str, Any] = {
            "thread_id": thread_id,
            "trace_id": trace_id,
            "sources": result.get("sources") or [],
            "cached": bool(result.get("answer_cached", False)),
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
                editable=bool(payload.get("editable", False)),
                **base,
            )
        return ChatTurn(reply=result.get("reply", ""), **base)


def build_agent(
    deps: AgentDependencies,
    checkpointer: BaseCheckpointSaver[Any],
    tracing: Tracing | None = None,
) -> AgentService:
    return AgentService(build_conversation_graph(deps, checkpointer), tracing)
