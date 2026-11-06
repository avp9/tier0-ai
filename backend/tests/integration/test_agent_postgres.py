import json
import re
from pathlib import Path
from typing import Any

from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_checkpointer, make_pool
from tier0.agent.graph import AgentDependencies
from tier0.agent.service import AgentService, Awaiting, ChatTurn, build_agent
from tier0.agent.state import WorkflowStatus
from tier0.intent.types import Continuity, Domain, Intent, IntentResult
from tier0.llm.fake import FakeChatModel
from tier0.rag import answerability, answering, rewrite
from tier0.rag.answerability import AnswerabilityConfig
from tier0.rag.answering import AnswerService
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever
from tier0.tickets import drafting
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.postgres_store import PostgresSubmissionStore
from tier0.tickets.status import TicketStatusService
from tier0.tickets.submission import TicketSubmissionService

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"
ALICE = "alice@buzztech.example"
GAP = "Does BuzzTech reimburse gym memberships?"


class Classifier:
    name = "scripted"

    def classify(self, message: str, history: list[Any]) -> IntentResult:
        return IntentResult(intent=Intent.KNOWLEDGE_QUESTION, domain=Domain.HR,
                            continuity=Continuity.NEW, confidence=0.9, source="jev")  # fmt: skip


def llm(system: str, user: str, _json_mode: bool) -> str:
    if system == answerability.SUFFICIENCY_SYSTEM:
        return json.dumps({"sufficient": True, "supporting_ids": []})
    if system == answering.ANSWER_SYSTEM or system == rewrite.SYSTEM_PROMPT:
        return "ok"
    assert system == drafting.SYSTEM_PROMPT
    said = [t["content"] for t in json.loads(user)["conversation"] if t["role"] == "user"]
    problems = [s for s in said if re.search(r"gym", s)]
    return json.dumps({"summary": problems[-1][:70] if problems else None, "reported": problems})


def start_process(
    database_url: str, adapter: FakeTicketAdapter, index: Path
) -> tuple[AgentService, ConnectionPool]:
    """A freshly started server: new connection pool, new graph, same database and index."""
    pool = make_pool(database_url)
    chat = FakeChatModel(llm)
    answers = AnswerService(
        Retriever.open(index, FakeEmbedder()), chat, AnswerabilityConfig(score_threshold=0.35)
    )
    deps = AgentDependencies(
        classifier=Classifier(),
        answers=answers,
        rewriter=rewrite.QuestionRewriter(chat),
        drafter=TicketDrafter(chat),
        submission=TicketSubmissionService(adapter, PostgresSubmissionStore(pool)),
        status=TicketStatusService(adapter),
    )
    return build_agent(deps, make_checkpointer(pool)), pool


def test_the_whole_conversation_survives_a_server_restart_after_every_message(
    migrated_database: str, tmp_path: Path
) -> None:
    index = tmp_path / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    adapter = FakeTicketAdapter()  # the ticketing platform outlives our server

    def say(text: str) -> ChatTurn:
        agent, pool = start_process(migrated_database, adapter, index)  # restart before each
        try:
            turn: ChatTurn = agent.send(
                thread_id="restart-thread", requester_email=ALICE, message=text
            )
            return turn
        finally:
            pool.close()

    offer = say(GAP)
    assert (
        offer.awaiting is Awaiting.OFFER_TICKET and offer.workflow is WorkflowStatus.OFFERED_TICKET
    )

    preview = say("yes")
    assert preview.awaiting is Awaiting.CONFIRM_TICKET and adapter.tickets == []
    assert preview.workflow is WorkflowStatus.AWAITING_TICKET_CONFIRMATION

    unclear = say("hmm, let me think")
    assert unclear.awaiting is Awaiting.CONFIRM_TICKET and adapter.tickets == []

    done = say("yes")
    assert done.awaiting is None and done.created_ticket is not None
    assert done.created_ticket.key == "SUP-1" and len(adapter.tickets) == 1

    after = say("yes")  # a stray repeat in a finished conversation must not create anything
    assert len(adapter.tickets) == 1 and adapter.create_calls == 1
    assert after.created_ticket is None  # it was handled as a brand-new message
