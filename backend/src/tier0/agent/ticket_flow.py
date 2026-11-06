from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from tier0.agent.state import (
    AgentState,
    PendingTicket,
    WorkflowStatus,
    transition,
    workflow_of,
)
from tier0.intent.types import Turn
from tier0.tickets.confirmation import Reply, interpret, wants_to_cancel
from tier0.tickets.drafting import TicketDrafter, format_preview, next_question
from tier0.tickets.models import Category, CreatedTicket
from tier0.tickets.submission import TicketSubmissionService

MAX_DETAIL_QUESTIONS = 4
MAX_CONFIRMATIONS = 5

CLARIFY_REPLY = (
    "I need a clear answer before I create anything. "
    "Reply yes to create the ticket, or no to cancel."
)
CANCELLED_REPLY = (
    "No problem, I haven't created a ticket. If you change your mind, just ask me to raise one."
)
GAVE_UP_DETAILS_REPLY = (
    "I couldn't gather enough detail to raise a ticket, so I haven't created one. "
    "Please contact the service desk directly, or ask me again when you're ready."
)
GAVE_UP_CONFIRM_REPLY = (
    "I haven't received a clear yes, so I haven't created a ticket. "
    "Ask me again whenever you'd like to raise one."
)


class TicketFlowState(AgentState, total=False):
    confirmation: str
    confirmation_attempts: int
    detail_questions: int


def _assistant(text: str) -> Turn:
    return Turn(role="assistant", content=text)


def _user(text: str) -> Turn:
    return Turn(role="user", content=text)


def _category_hint(state: AgentState) -> Category | None:
    intent = state.get("intent")
    if intent is None:
        return None
    if intent.domain.value == "IT":
        return "IT"
    if intent.domain.value == "HR":
        return "HR"
    return None


class TicketCreationFlow:
    """Collect details, show a preview, wait for an explicit yes, then create the ticket once.

    Each pause is a LangGraph ``interrupt``: the graph stops, the question is returned to the
    caller, and the next message resumes it, even after a restart (the state is checkpointed).
    """

    def __init__(self, drafter: TicketDrafter, submission: TicketSubmissionService) -> None:
        self._drafter = drafter
        self._submission = submission

    # --- nodes ------------------------------------------------------------------------

    def draft(self, state: TicketFlowState) -> dict[str, Any]:
        pending = state.get("pending_ticket") or PendingTicket()
        updated = self._drafter.draft(pending, state.get("turns", []), _category_hint(state))
        status = transition(workflow_of(state), WorkflowStatus.COLLECTING_TICKET_FIELDS)
        return {"pending_ticket": updated, "workflow": status}

    def prepare_question(self, state: TicketFlowState) -> dict[str, Any]:
        """Record what is about to be asked, so the saved state is correct while we wait."""
        question = next_question(state.get("pending_ticket") or PendingTicket()) or ""
        return {"reply": question}

    def ask(self, state: TicketFlowState) -> dict[str, Any]:
        question = state.get("reply", "")
        answer = interrupt(
            {"type": "collect_ticket_details", "prompt": question, "question": question}
        )
        return {
            "turns": [_assistant(question), _user(answer)],
            "user_message": answer,
            "detail_questions": state.get("detail_questions", 0) + 1,
        }

    def preview(self, state: TicketFlowState) -> dict[str, Any]:
        """Show the ticket and enter the confirmation step before waiting for the reply."""
        pending = state.get("pending_ticket") or PendingTicket()
        status = transition(workflow_of(state), WorkflowStatus.AWAITING_TICKET_CONFIRMATION)
        return {"reply": format_preview(state["requester_email"], pending), "workflow": status}

    def confirm(self, state: TicketFlowState) -> dict[str, Any]:
        pending = state.get("pending_ticket") or PendingTicket()
        prompt = state.get("reply", "")
        answer = interrupt(
            {
                "type": "confirm_ticket",
                "prompt": prompt,
                "preview": format_preview(state["requester_email"], pending),
            }
        )
        return {
            "turns": [_assistant(prompt), _user(answer)],
            "user_message": answer,
            "confirmation": interpret(answer).value,
            "confirmation_attempts": state.get("confirmation_attempts", 0) + 1,
        }

    def reprompt(self, state: TicketFlowState) -> dict[str, Any]:
        return {"reply": CLARIFY_REPLY}

    def submit(self, state: TicketFlowState, config: RunnableConfig) -> dict[str, Any]:
        # Raises IllegalTransition unless the employee is at the confirmation step.
        transition(workflow_of(state), WorkflowStatus.SUBMITTING_TICKET)
        result = self._submission.submit(
            thread_id=str(config["configurable"]["thread_id"]),
            requester_email=state["requester_email"],
            ticket=state.get("pending_ticket") or PendingTicket(),
        )
        if result.retryable:
            return {
                "workflow": transition(
                    WorkflowStatus.SUBMITTING_TICKET, WorkflowStatus.AWAITING_TICKET_CONFIRMATION
                ),
                "reply": result.reply,
            }
        update = self._finish(result.reply)
        if result.has_ticket and result.ticket_key:
            update["created_ticket"] = CreatedTicket(
                key=result.ticket_key, url=result.ticket_url or ""
            )
        return update

    def cancel(self, state: TicketFlowState) -> dict[str, Any]:
        return self._finish(CANCELLED_REPLY)

    def give_up(self, state: TicketFlowState) -> dict[str, Any]:
        asked_details = workflow_of(state) is WorkflowStatus.COLLECTING_TICKET_FIELDS
        return self._finish(GAVE_UP_DETAILS_REPLY if asked_details else GAVE_UP_CONFIRM_REPLY)

    @staticmethod
    def _finish(reply: str) -> dict[str, Any]:
        return {
            "turns": [_assistant(reply)],
            "reply": reply,
            "workflow": WorkflowStatus.IDLE,
            "pending_ticket": None,
            "confirmation": "",
            "confirmation_attempts": 0,
            "detail_questions": 0,
        }

    # --- routing ----------------------------------------------------------------------

    @staticmethod
    def after_draft(state: TicketFlowState) -> Literal["prepare_question", "preview", "give_up"]:
        if next_question(state.get("pending_ticket") or PendingTicket()) is None:
            return "preview"
        if state.get("detail_questions", 0) >= MAX_DETAIL_QUESTIONS:
            return "give_up"
        return "prepare_question"

    @staticmethod
    def after_ask(state: TicketFlowState) -> Literal["draft", "cancel"]:
        """Leave the flow on an unmistakable "never mind"; any other reply is a detail."""
        return "cancel" if wants_to_cancel(state.get("user_message", "")) else "draft"

    @staticmethod
    def after_confirm(state: TicketFlowState) -> Literal["submit", "cancel", "reprompt", "give_up"]:
        answer = Reply(state.get("confirmation", Reply.UNCLEAR.value))
        if answer is Reply.YES:
            return "submit"
        if answer is Reply.NO:
            return "cancel"
        if state.get("confirmation_attempts", 0) >= MAX_CONFIRMATIONS:
            return "give_up"
        return "reprompt"

    @staticmethod
    def after_submit(state: TicketFlowState) -> Literal["confirm", "give_up", "__end__"]:
        if workflow_of(state) is not WorkflowStatus.AWAITING_TICKET_CONFIRMATION:
            return "__end__"
        if state.get("confirmation_attempts", 0) >= MAX_CONFIRMATIONS:
            return "give_up"
        return "confirm"


def add_ticket_flow_nodes(
    graph: StateGraph[TicketFlowState, None, TicketFlowState, TicketFlowState],
    flow: TicketCreationFlow,
    *,
    prefix: str = "",
) -> str:
    """Register the flow's nodes directly on ``graph`` and return the entry node's name.

    The nodes live in the caller's graph, not in a subgraph, because LangGraph only copies a
    subgraph's progress into the parent's saved state once the subgraph finishes. Directly
    registered nodes keep the saved workflow and reply correct while the employee is deciding.
    """

    def name(short: str) -> str:
        return f"{prefix}{short}"

    for short, node in (
        ("draft", flow.draft), ("prepare_question", flow.prepare_question), ("ask", flow.ask),
        ("preview", flow.preview), ("confirm", flow.confirm), ("reprompt", flow.reprompt),
        ("submit", flow.submit), ("cancel", flow.cancel), ("give_up", flow.give_up),
    ):  # fmt: skip
        graph.add_node(name(short), node)

    def route(*shorts: str) -> dict[Hashable, str]:
        return {short: (END if short == "__end__" else name(short)) for short in shorts}

    graph.add_conditional_edges(
        name("draft"), flow.after_draft, route("prepare_question", "preview", "give_up")
    )
    graph.add_edge(name("prepare_question"), name("ask"))
    graph.add_conditional_edges(name("ask"), flow.after_ask, route("draft", "cancel"))
    graph.add_edge(name("preview"), name("confirm"))
    graph.add_conditional_edges(
        name("confirm"), flow.after_confirm, route("submit", "cancel", "reprompt", "give_up")
    )
    graph.add_edge(name("reprompt"), name("confirm"))
    graph.add_conditional_edges(
        name("submit"), flow.after_submit, route("confirm", "give_up", "__end__")
    )
    graph.add_edge(name("cancel"), END)
    graph.add_edge(name("give_up"), END)
    return name("draft")


def build_ticket_flow(
    drafter: TicketDrafter,
    submission: TicketSubmissionService,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """The ticket flow on its own (the full agent adds the same nodes to its graph)."""
    graph = StateGraph(TicketFlowState)
    entry = add_ticket_flow_nodes(graph, TicketCreationFlow(drafter, submission))
    graph.add_edge(START, entry)
    return graph.compile(checkpointer=checkpointer)
