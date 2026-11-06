from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Only these classes may be rebuilt from stored checkpoints. Anything else is refused, so a
# tampered checkpoint cannot make the app instantiate arbitrary classes.
ALLOWED_STATE_TYPES: list[tuple[str, str]] = [
    ("tier0.intent.types", "Turn"),
    ("tier0.intent.types", "Intent"),
    ("tier0.intent.types", "Domain"),
    ("tier0.intent.types", "Continuity"),
    ("tier0.intent.types", "IntentResult"),
    ("tier0.agent.state", "WorkflowStatus"),
    ("tier0.agent.state", "PendingTicket"),
    ("tier0.rag.answerability", "AnswerabilityDecision"),
    ("tier0.rag.answerability", "ScoreSignal"),
    ("tier0.rag.answerability", "SufficiencySignal"),
    ("tier0.tickets.models", "TicketSummary"),
    ("tier0.tickets.models", "CreatedTicket"),
    ("tier0.rag.answering", "Source"),
]


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_STATE_TYPES)


def make_pool(database_url: str, *, max_size: int = 5) -> ConnectionPool:
    """A connection pool configured the way the LangGraph Postgres saver requires."""
    pool = ConnectionPool(
        database_url,
        max_size=max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=True, timeout=10)
    return pool


def make_checkpointer(pool: ConnectionPool) -> PostgresSaver:
    """A Postgres-backed checkpointer; creates its own tables on first use (idempotent)."""
    saver = PostgresSaver(pool, serde=make_serializer())  # type: ignore[arg-type]
    saver.setup()
    return saver
