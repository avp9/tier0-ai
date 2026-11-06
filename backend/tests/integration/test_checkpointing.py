from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from tier0.agent.checkpoint import make_checkpointer, make_pool
from tier0.agent.state import AgentState, PendingTicket, WorkflowStatus, initial_state, workflow_of
from tier0.intent.types import Continuity, Domain, Intent, IntentResult, Turn
from tier0.rag.answerability import AnswerabilityDecision
from tier0.tickets.models import TicketSummary

ALICE = "alice@buzztech.example"


def node(state: AgentState) -> dict[str, Any]:
    n = len(state["turns"])
    return {
        "turns": [Turn(role="user", content=f"message {n}")],
        "intent": IntentResult(
            intent=Intent.CREATE_TICKET,
            domain=Domain.IT,
            continuity=Continuity.CONTINUATION,
            confidence=0.9,
            source="jev",
        ),
        "workflow": WorkflowStatus.AWAITING_TICKET_CONFIRMATION,
        "pending_ticket": PendingTicket(summary="VPN down", category="IT"),
        "answer_decision": AnswerabilityDecision(answerable=False, reason="low_score"),
        "ticket_choices": [TicketSummary(key="SUP-1", title="t", status="Open", url="https://x/1")],
    }


def build_app(pool: ConnectionPool, node_fn: Any = node) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("step", node_fn)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    return graph.compile(checkpointer=make_checkpointer(pool))


def thread(name: str) -> Any:
    return {"configurable": {"thread_id": name}}


@pytest.fixture
def pool(migrated_database: str) -> Iterator[ConnectionPool]:
    pool = make_pool(migrated_database)
    yield pool
    pool.close()


def test_a_conversation_continues_across_turns_with_typed_state(pool: ConnectionPool) -> None:
    app = build_app(pool)
    app.invoke(initial_state(ALICE), thread("t1"))
    app.invoke({"user_message": "second turn"}, thread("t1"))
    state = app.get_state(thread("t1")).values

    assert [t.content for t in state["turns"]] == ["message 0", "message 1"]
    assert state["requester_email"] == ALICE
    assert (
        isinstance(state["intent"], IntentResult) and state["intent"].intent is Intent.CREATE_TICKET
    )
    assert isinstance(state["pending_ticket"], PendingTicket)
    assert isinstance(state["answer_decision"], AnswerabilityDecision)
    assert isinstance(state["ticket_choices"][0], TicketSummary)
    assert workflow_of(state) is WorkflowStatus.AWAITING_TICKET_CONFIRMATION


def test_state_survives_a_restart(migrated_database: str) -> None:
    first_pool = make_pool(migrated_database)
    build_app(first_pool).invoke(initial_state(ALICE), thread("t1"))
    first_pool.close()

    second_pool = make_pool(migrated_database)  # a new process, same database
    try:
        app = build_app(second_pool)
        assert len(app.get_state(thread("t1")).values["turns"]) == 1
        app.invoke({"user_message": "after restart"}, thread("t1"))
        assert len(app.get_state(thread("t1")).values["turns"]) == 2
    finally:
        second_pool.close()


def test_conversations_are_isolated_by_thread(pool: ConnectionPool) -> None:
    app = build_app(pool)
    app.invoke(initial_state(ALICE), thread("alice-thread"))
    app.invoke(initial_state("bob@buzztech.example"), thread("bob-thread"))
    assert app.get_state(thread("alice-thread")).values["requester_email"] == ALICE
    assert app.get_state(thread("bob-thread")).values["requester_email"] == "bob@buzztech.example"
    assert app.get_state(thread("unknown-thread")).values == {}


def test_setting_up_the_checkpointer_twice_is_harmless(pool: ConnectionPool) -> None:
    make_checkpointer(pool)
    make_checkpointer(pool)
    build_app(pool).invoke(initial_state(ALICE), thread("t1"))


def test_a_class_outside_the_allowlist_is_never_rebuilt_from_a_checkpoint(
    pool: ConnectionPool, caplog: pytest.LogCaptureFixture
) -> None:
    class Stranger(BaseModel):
        x: int = 1

    def sneaky(state: AgentState) -> dict[str, Any]:
        return {"pending_ticket": Stranger()}

    app = build_app(pool, sneaky)
    app.invoke(initial_state(ALICE), thread("t1"))
    restored = app.get_state(thread("t1")).values["pending_ticket"]
    assert not isinstance(restored, Stranger)
    assert restored == {"x": 1}  # inert data, not an instance
    assert "Blocked deserialization" in caplog.text


def test_the_checkpoint_tables_live_beside_ours_in_the_same_database(
    pool: ConnectionPool, migrated_database: str
) -> None:
    make_checkpointer(pool)
    with psycopg.connect(migrated_database) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
    assert {"checkpoints", "checkpoint_writes", "users", "ticket_submissions"} <= names
