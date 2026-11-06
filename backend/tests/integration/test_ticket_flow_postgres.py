import json
from typing import Any

import pytest
from langgraph.types import Command

from tier0.agent.checkpoint import make_checkpointer, make_pool
from tier0.agent.state import WorkflowStatus, initial_state, workflow_of
from tier0.agent.ticket_flow import build_ticket_flow
from tier0.intent.types import Continuity, Domain, Intent, IntentResult, Turn
from tier0.llm.fake import FakeChatModel
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.postgres_store import PostgresSubmissionStore
from tier0.tickets.submission import SubmissionStatus, TicketSubmissionService

ALICE = "alice@buzztech.example"
CFG: Any = {"configurable": {"thread_id": "restart-thread"}}


def llm(_system: str, user: str, _json_mode: bool) -> str:
    said = [t["content"] for t in json.loads(user)["conversation"] if t["role"] == "user"]
    return json.dumps({"summary": said[-1][:60], "reported": said, "category": None})


def app_on(database_url: str, adapter: FakeTicketAdapter) -> tuple[Any, Any]:
    """A freshly started application process using the shared database and ticket system."""
    pool = make_pool(database_url)
    service = TicketSubmissionService(adapter, PostgresSubmissionStore(pool))
    app = build_ticket_flow(TicketDrafter(FakeChatModel(llm)), service, make_checkpointer(pool))
    return app, pool


def first_message() -> dict[str, Any]:
    state: dict[str, Any] = dict(initial_state(ALICE))
    state["turns"] = [Turn(role="user", content="My VPN keeps dropping at home")]
    state["intent"] = IntentResult(
        intent=Intent.CREATE_TICKET,
        domain=Domain.IT,
        continuity=Continuity.NEW,
        confidence=0.9,
        source="jev",
    )
    return state


def test_the_employee_can_confirm_after_the_server_restarts(migrated_database: str) -> None:
    adapter = FakeTicketAdapter()  # the ticketing platform outlives our server

    app, pool = app_on(migrated_database, adapter)
    paused = app.invoke(first_message(), CFG)
    assert paused["__interrupt__"][0].value["type"] == "confirm_ticket"
    assert adapter.tickets == []
    pool.close()  # the server goes away while the employee is deciding

    app, pool = app_on(migrated_database, adapter)  # a new process starts
    try:
        saved = app.get_state(CFG).values
        assert workflow_of(saved) is WorkflowStatus.AWAITING_TICKET_CONFIRMATION
        assert "VPN keeps dropping" in saved["reply"] and saved["requester_email"] == ALICE

        done = app.invoke(Command(resume="yes"), CFG)
        assert len(adapter.tickets) == 1 and "SUP-1" in done["reply"]
        row = PostgresSubmissionStore(pool).get(adapter.tickets[0].draft.submission_id or "")
        assert row is not None and row.status is SubmissionStatus.SUBMITTED
        assert row.ticket_key == "SUP-1"
    finally:
        pool.close()


def test_a_second_yes_after_completion_cannot_create_another_ticket(migrated_database: str) -> None:
    adapter = FakeTicketAdapter()
    app, pool = app_on(migrated_database, adapter)
    try:
        app.invoke(first_message(), CFG)
        app.invoke(Command(resume="yes"), CFG)
        assert len(adapter.tickets) == 1
        again = app.invoke(Command(resume="yes"), CFG)  # a double click repeats the confirmation
        assert "SUP-1" in again["reply"]  # a no-op: it just reports the original ticket
        assert len(adapter.tickets) == 1 and adapter.create_calls == 1
    finally:
        pool.close()


def test_two_conversations_with_identical_text_make_two_separate_tickets(
    migrated_database: str,
) -> None:
    adapter = FakeTicketAdapter()
    app, pool = app_on(migrated_database, adapter)
    try:
        for thread in ("thread-a", "thread-b"):
            cfg: Any = {"configurable": {"thread_id": thread}}
            app.invoke(first_message(), cfg)
            app.invoke(Command(resume="yes"), cfg)
        assert len(adapter.tickets) == 2
    finally:
        pool.close()


@pytest.mark.parametrize("answer", ["no", "maybe"])
def test_no_and_unclear_answers_leave_the_database_without_a_submission(
    migrated_database: str, answer: str
) -> None:
    adapter = FakeTicketAdapter()
    app, pool = app_on(migrated_database, adapter)
    try:
        app.invoke(first_message(), CFG)
        app.invoke(Command(resume=answer), CFG)
        assert adapter.tickets == []
        with pool.connection() as connection:
            count = connection.execute("SELECT count(*) AS n FROM ticket_submissions").fetchone()
        assert count == {"n": 0}
    finally:
        pool.close()
