from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_checkpointer
from tier0.agent.graph import AgentDependencies
from tier0.agent.service import AgentService, build_agent
from tier0.config import Settings
from tier0.intent.factory import build_classifier
from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.answerability import AnswerabilityConfig
from tier0.rag.answering import AnswerService
from tier0.rag.embeddings import OpenAIEmbedder
from tier0.rag.indexer import open_collection
from tier0.rag.retriever import Retriever
from tier0.rag.rewrite import QuestionRewriter
from tier0.tickets.config import JiraConfig
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.jira_adapter import JiraAdapter
from tier0.tickets.postgres_store import PostgresSubmissionStore
from tier0.tickets.status import TicketStatusService
from tier0.tickets.submission import TicketSubmissionService


class ChatNotConfigured(Exception):
    """Chat cannot start because required settings are missing."""


@dataclass(frozen=True)
class BuiltAgent:
    service: AgentService
    index_check: Callable[[], bool]


def jira_config(settings: Settings) -> JiraConfig:
    if not (settings.jira_server and settings.jira_username and settings.jira_api_token):
        raise ChatNotConfigured("Jira settings are incomplete")
    return JiraConfig(
        server=settings.jira_server.rstrip("/"),
        username=settings.jira_username,
        api_token=settings.jira_api_token.get_secret_value(),
        environment=settings.app_env,
        project_key=settings.jira_project_key,
        requester_field_id=settings.jira_requester_field_id,
        requester_field_name=settings.jira_requester_field_name,
        issue_type=settings.jira_issue_type,
        set_priority_field=settings.jira_set_priority_field,
    )


def build_dependencies(
    settings: Settings, pool: ConnectionPool
) -> tuple[AgentDependencies, Retriever]:
    missing = settings.missing_chat_settings()
    if missing or settings.openai_api_key is None:
        raise ChatNotConfigured(f"chat needs these settings: {', '.join(missing)}")
    key = settings.openai_api_key.get_secret_value()

    embedder = OpenAIEmbedder(api_key=key, model=settings.embedding_model)
    chat = OpenAIChatModel(api_key=key, model=settings.openai_model_name)
    retriever = Retriever.open(Path(settings.vector_store_path), embedder)
    answers = AnswerService(
        retriever,
        chat,
        AnswerabilityConfig(
            score_threshold=settings.retrieval_score_threshold, top_k=settings.retrieval_top_k
        ),
    )
    jira = JiraAdapter(jira_config(settings))
    classifier_env = {
        "OPENAI_API_KEY": key,
        "OPENAI_MODEL_NAME": settings.openai_model_name,
        "TYPESAFE_API_KEY": (
            settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else ""
        ),
    }
    deps = AgentDependencies(
        classifier=build_classifier(classifier_env),
        answers=answers,
        rewriter=QuestionRewriter(chat),
        drafter=TicketDrafter(chat),
        submission=TicketSubmissionService(jira, PostgresSubmissionStore(pool)),
        status=TicketStatusService(jira),
    )
    return deps, retriever


def build_agent_from_settings(
    settings: Settings, pool: ConnectionPool, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> BuiltAgent:
    """Wire the real agent. Raises ChatNotConfigured, IndexNotFoundError or IndexMismatchError."""
    deps, _ = build_dependencies(settings, pool)
    service = build_agent(deps, checkpointer or make_checkpointer(pool))
    index_dir = Path(settings.vector_store_path)

    def index_check() -> bool:
        return open_collection(index_dir).count() > 0

    return BuiltAgent(service=service, index_check=index_check)
