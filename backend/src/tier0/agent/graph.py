from __future__ import annotations

import re
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_config, get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from tier0.agent import replies
from tier0.agent.state import WorkflowStatus, transition, workflow_of
from tier0.agent.ticket_flow import TicketCreationFlow, TicketFlowState, add_ticket_flow_nodes
from tier0.intent.types import (
    Continuity,
    Intent,
    IntentClassifier,
    IntentUnavailable,
    Turn,
)
from tier0.rag.answering import AnswerService, Outcome
from tier0.rag.rewrite import QuestionRewriter
from tier0.tickets.confirmation import Reply, interpret, wants_to_cancel, wants_to_finish
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.status import (
    NOT_FOUND_REPLY,
    StatusOutcome,
    TicketStatusService,
    choose_ticket,
    format_choices,
)
from tier0.tickets.submission import TicketSubmissionService

CONFIDENCE_THRESHOLD = 0.6
MAX_SELECTION_TRIES = 2
_TICKET_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9_]+-\d+\b")

S = WorkflowStatus


@dataclass(frozen=True)
class AgentDependencies:
    classifier: IntentClassifier
    answers: AnswerService
    rewriter: QuestionRewriter
    drafter: TicketDrafter
    submission: TicketSubmissionService
    status: TicketStatusService


def _assistant(text: str) -> Turn:
    return Turn(role="assistant", content=text)


def _user(text: str) -> Turn:
    return Turn(role="user", content=text)


STREAM_TOKENS = "stream_tokens"  # set in the run config when the caller wants the reply as it forms


def _token_sink() -> Callable[[str], None] | None:
    """Where to send reply text as it is written, or None when the caller wants it whole."""
    if not get_config().get("configurable", {}).get(STREAM_TOKENS):
        return None
    writer = get_stream_writer()
    return lambda text: writer({"token": text})


def _say(reply: str) -> dict[str, Any]:
    """A final reply for this turn; the conversation is idle again."""
    return {"reply": reply, "turns": [_assistant(reply)], "workflow": S.IDLE}


class Conversation:
    """The nodes that route one employee message to the right capability."""

    def __init__(self, deps: AgentDependencies) -> None:
        self._deps = deps

    # --- start of a turn --------------------------------------------------------------

    def ingest(self, state: TicketFlowState) -> dict[str, Any]:
        # A new turn only starts when the previous one finished, so we are idle.
        return {
            "turns": [_user(state["user_message"])],
            "workflow": S.IDLE,
            "reply": "",
            "sources": [],
            "created_ticket": None,
            "answer_decision": None,
            "ticket_choices": [],
            "selection_attempts": 0,
        }

    def classify(self, state: TicketFlowState) -> dict[str, Any]:
        history = state["turns"][:-1]
        try:
            intent = self._deps.classifier.classify(state["user_message"], history)
        except IntentUnavailable:
            return {"intent": None, **_say(replies.CLASSIFIER_UNAVAILABLE)}
        return {"intent": intent}

    @staticmethod
    def after_classify(state: TicketFlowState) -> str:
        intent = state.get("intent")
        if intent is None:
            return END
        if intent.intent is Intent.CREATE_TICKET:
            return "ticket_flow" if intent.is_confident(CONFIDENCE_THRESHOLD) else "confirm_intent"
        return {
            Intent.KNOWLEDGE_QUESTION: "answer",
            Intent.TICKET_STATUS: "status",
            Intent.SMALLTALK: "smalltalk",
            Intent.OUT_OF_SCOPE: "out_of_scope",
            Intent.AMBIGUOUS: "clarify",
        }[intent.intent]

    # --- answering questions ----------------------------------------------------------

    def answer(self, state: TicketFlowState) -> dict[str, Any]:
        intent = state["intent"]
        message = state["user_message"]
        question = message
        if intent is not None and intent.continuity is Continuity.CONTINUATION:
            question = self._deps.rewriter.rewrite(message, state["turns"][:-1])
        result = self._deps.answers.answer(question, on_token=_token_sink())
        update: dict[str, Any] = {
            "answer_decision": result.decision,
            "sources": result.sources,
            "reply": result.reply,
        }
        if result.outcome is Outcome.ANSWERED:
            update["turns"] = [_assistant(result.reply)]
        else:  # no reliable answer: offer a ticket and wait for the employee's reply
            update["workflow"] = transition(workflow_of(state), S.OFFERED_TICKET)
        return update

    @staticmethod
    def after_answer(state: TicketFlowState) -> str:
        return "offer" if workflow_of(state) is S.OFFERED_TICKET else END

    def confirm_intent(self, state: TicketFlowState) -> dict[str, Any]:
        """A shaky "create a ticket" is checked with the employee before anything is drafted."""
        return {
            "reply": replies.CONFIRM_TICKET_INTENT,
            "workflow": transition(workflow_of(state), S.OFFERED_TICKET),
        }

    def offer(self, state: TicketFlowState) -> dict[str, Any]:
        prompt = state["reply"]
        answer = interrupt({"type": "offer_ticket", "prompt": prompt})
        return {
            "turns": [_assistant(prompt), _user(answer)],
            "user_message": answer,
            "confirmation": interpret(answer).value,
        }

    @staticmethod
    def after_offer(state: TicketFlowState) -> str:
        return {Reply.YES.value: "ticket_flow", Reply.NO.value: "decline_offer"}.get(
            state.get("confirmation", ""), "reroute"
        )

    @staticmethod
    def decline_offer(state: TicketFlowState) -> dict[str, Any]:
        return {**_say(replies.OFFER_DECLINED), "confirmation": ""}

    @staticmethod
    def reroute(state: TicketFlowState) -> dict[str, Any]:
        """The employee ignored the question and said something new: treat it as a new request."""
        return {
            "workflow": S.IDLE,
            "confirmation": "",
            "reply": "",
            "sources": [],
            "answer_decision": None,
            "ticket_choices": [],
            "selection_attempts": 0,
        }

    # --- ticket status ----------------------------------------------------------------

    def status(self, state: TicketFlowState) -> dict[str, Any]:
        email = state["requester_email"]
        service = self._deps.status
        typed_key = _TICKET_KEY.search(state["user_message"])
        if typed_key:
            result = service.details(typed_key.group().upper(), email)
            if result.outcome is StatusOutcome.NOT_FOUND:
                result = service.lookup(email)
                result = result.model_copy(update={"reply": f"{NOT_FOUND_REPLY}\n\n{result.reply}"})
        else:
            result = service.lookup(email)

        if result.outcome is StatusOutcome.MULTIPLE:
            return {
                "reply": result.reply,
                "ticket_choices": result.tickets,
                "workflow": transition(workflow_of(state), S.AWAITING_TICKET_SELECTION),
                "selection_attempts": 0,
            }
        return {**_say(result.reply), "ticket_choices": []}

    @staticmethod
    def after_status(state: TicketFlowState) -> str:
        return "select" if workflow_of(state) is S.AWAITING_TICKET_SELECTION else END

    def select(self, state: TicketFlowState) -> dict[str, Any]:
        prompt = state["reply"]
        choices = state.get("ticket_choices", [])
        answer = interrupt(
            {"type": "select_ticket", "prompt": prompt, "options": [t.key for t in choices]}
        )
        chosen = choose_ticket(answer, choices).ticket
        return {
            "turns": [_assistant(prompt), _user(answer)],
            "user_message": answer,
            "selected_ticket_key": chosen.key if chosen else None,
            "selection_attempts": state.get("selection_attempts", 0) + 1,
        }

    @staticmethod
    def after_select(state: TicketFlowState) -> str:
        if state.get("selected_ticket_key"):
            return "show_details"
        message = state.get("user_message", "")
        if wants_to_cancel(message) or wants_to_finish(message):
            return "cancel_selection"
        if state.get("selection_attempts", 0) >= MAX_SELECTION_TRIES:
            return "reroute"
        return "reselect"

    @staticmethod
    def reselect(state: TicketFlowState) -> dict[str, Any]:
        choices = state.get("ticket_choices", [])
        return {"reply": replies.RESELECT + format_choices(choices)}

    def show_details(self, state: TicketFlowState) -> dict[str, Any]:
        """Show the chosen ticket, then offer the same list again: they may want to look at others.

        The details become the next question's prompt (the ``select`` step records them in the
        conversation when it is answered), and the employee leaves the list by saying they are done.
        """
        key = str(state.get("selected_ticket_key"))
        result = self._deps.status.details(key, state["requester_email"])
        return {"reply": result.reply, "selected_ticket_key": None, "selection_attempts": 0}

    @staticmethod
    def cancel_selection(state: TicketFlowState) -> dict[str, Any]:
        return {**_say(replies.SELECTION_CANCELLED), "ticket_choices": []}

    # --- everything else --------------------------------------------------------------

    @staticmethod
    def smalltalk(state: TicketFlowState) -> dict[str, Any]:
        return _say(replies.smalltalk_reply(state["user_message"]))

    @staticmethod
    def out_of_scope(state: TicketFlowState) -> dict[str, Any]:
        return _say(replies.OUT_OF_SCOPE)

    @staticmethod
    def clarify(state: TicketFlowState) -> dict[str, Any]:
        return _say(replies.CLARIFY)


def build_conversation_graph(
    deps: AgentDependencies, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    c = Conversation(deps)
    graph = StateGraph(TicketFlowState)
    nodes = {
        "ingest": c.ingest, "classify": c.classify, "answer": c.answer,
        "confirm_intent": c.confirm_intent, "offer": c.offer, "decline_offer": c.decline_offer,
        "reroute": c.reroute, "status": c.status, "select": c.select,
        "reselect": c.reselect, "show_details": c.show_details,
        "cancel_selection": c.cancel_selection, "smalltalk": c.smalltalk,
        "out_of_scope": c.out_of_scope, "clarify": c.clarify,
    }  # fmt: skip
    for name, node in nodes.items():
        graph.add_node(name, node)
    ticket = add_ticket_flow_nodes(graph, TicketCreationFlow(deps.drafter, deps.submission),
                                   prefix="ticket_")  # fmt: skip

    def route(*names: str) -> dict[Hashable, str]:
        """Path-function results map to themselves, except the ticket flow's entry node."""
        return {n: (ticket if n == "ticket_flow" else END if n == END else n) for n in names}

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "classify")
    graph.add_conditional_edges(
        "classify", c.after_classify,
        route("answer", "ticket_flow", "confirm_intent", "status", "smalltalk", "out_of_scope",
              "clarify", END),
    )  # fmt: skip
    graph.add_conditional_edges("answer", c.after_answer, route("offer", END))
    graph.add_edge("confirm_intent", "offer")
    graph.add_conditional_edges(
        "offer", c.after_offer, route("ticket_flow", "decline_offer", "reroute")
    )
    graph.add_edge("reroute", "classify")
    graph.add_conditional_edges("status", c.after_status, route("select", END))
    graph.add_conditional_edges(
        "select", c.after_select,
        route("show_details", "cancel_selection", "reselect", "reroute"),
    )  # fmt: skip
    graph.add_edge("reselect", "select")
    graph.add_edge("show_details", "select")  # the list stays until the employee is done with it
    for terminal in (
        "decline_offer",
        "cancel_selection",
        "smalltalk",
        "out_of_scope",
        "clarify",
    ):
        graph.add_edge(terminal, END)  # fmt: skip
    return graph.compile(checkpointer=checkpointer)
