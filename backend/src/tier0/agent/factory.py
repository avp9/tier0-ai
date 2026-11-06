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
from tier0.fakes.stack import FakeControls, RuleBasedClassifier, ScriptedChatModel
from tier0.intent.factory import build_classifier
from tier0.intent.types import IntentClassifier
from tier0.llm.base import ChatModel
from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.answerability import AnswerabilityConfig
from tier0.rag.answering import AnswerService
from tier0.rag.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from tier0.rag.indexer import open_collection
from tier0.rag.retriever import Retriever
from tier0.rag.rewrite import QuestionRewriter
from tier0.tickets.base import TicketAdapter
from tier0.tickets.config import JiraConfig
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.jira_adapter import JiraAdapter
from tier0.tickets.postgres_store import PostgresSubmissionStore
from tier0.tickets.status import TicketStatusService
from tier0.tickets.submission import SubmissionStore, TicketSubmissionService


class ChatNotConfigured(Exception):
    """Chat cannot start because required settings are missing."""


@dataclass(frozen=True)
class BuiltAgent:
    service: AgentService
    index_check: Callable[[], bool]
    controls: FakeControls | None = None


@dataclass(frozen=True)
class Components:
    deps: AgentDependencies
    retriever: Retriever
    controls: FakeControls | None


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


def build_components(
    settings: Settings,
    pool: ConnectionPool | None,
    *,
    submission_store: SubmissionStore | None = None,
) -> Components:
    """Wire the agent's parts. Without a database, pass an in-memory ``submission_store``."""
    if submission_store is None:
        if pool is None:
            raise ChatNotConfigured("a database pool or a submission store is required")
        submission_store = PostgresSubmissionStore(pool)
    missing = settings.missing_chat_settings()
    if missing:
        raise ChatNotConfigured(f"chat needs these settings: {', '.join(missing)}")

    fake_chat: ScriptedChatModel | None = None
    fake_classifier: RuleBasedClassifier | None = None
    embedder: Embedder
    chat: ChatModel
    classifier: IntentClassifier
    if settings.llm_provider == "fake":
        embedder = FakeEmbedder()
        chat = fake_chat = ScriptedChatModel()
        classifier = fake_classifier = RuleBasedClassifier()
    else:
        if settings.openai_api_key is None:
            raise ChatNotConfigured("chat needs these settings: OPENAI_API_KEY")
        key = settings.openai_api_key.get_secret_value()
        embedder = OpenAIEmbedder(api_key=key, model=settings.embedding_model)
        chat = OpenAIChatModel(api_key=key, model=settings.openai_model_name)
        classifier = build_classifier(
            {
                "OPENAI_API_KEY": key,
                "OPENAI_MODEL_NAME": settings.openai_model_name,
                "TYPESAFE_API_KEY": (
                    settings.typesafe_api_key.get_secret_value()
                    if settings.typesafe_api_key
                    else ""
                ),
            }
        )

    fake_adapter: FakeTicketAdapter | None = None
    adapter: TicketAdapter
    if settings.ticketing_provider == "fake":
        adapter = fake_adapter = FakeTicketAdapter(
            project_key=settings.jira_project_key, base_url="https://tickets.e2e.invalid"
        )
    else:
        adapter = JiraAdapter(jira_config(settings))

    retriever = Retriever.open(Path(settings.vector_store_path), embedder)
    deps = AgentDependencies(
        classifier=classifier,
        answers=AnswerService(
            retriever,
            chat,
            AnswerabilityConfig(
                score_threshold=settings.retrieval_score_threshold, top_k=settings.retrieval_top_k
            ),
        ),
        rewriter=QuestionRewriter(chat),
        drafter=TicketDrafter(chat),
        submission=TicketSubmissionService(adapter, submission_store),
        status=TicketStatusService(adapter),
    )
    controls = (
        FakeControls(adapter=fake_adapter, chat=fake_chat, classifier=fake_classifier)
        if (fake_adapter or fake_chat)
        else None
    )
    return Components(deps=deps, retriever=retriever, controls=controls)


def build_dependencies(
    settings: Settings, pool: ConnectionPool
) -> tuple[AgentDependencies, Retriever]:
    parts = build_components(settings, pool)
    return parts.deps, parts.retriever


def build_agent_from_settings(
    settings: Settings, pool: ConnectionPool, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> BuiltAgent:
    """Wire the agent. Raises ChatNotConfigured, IndexNotFoundError or IndexMismatchError."""
    parts = build_components(settings, pool)
    service = build_agent(parts.deps, checkpointer or make_checkpointer(pool))
    index_dir = Path(settings.vector_store_path)

    def index_check() -> bool:
        return open_collection(index_dir).count() > 0

    return BuiltAgent(service=service, index_check=index_check, controls=parts.controls)
