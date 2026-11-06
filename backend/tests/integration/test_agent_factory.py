from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_pool
from tier0.agent.factory import (
    ChatNotConfigured,
    build_agent_from_settings,
    build_dependencies,
    jira_config,
)
from tier0.agent.service import AgentService
from tier0.config import Settings
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import IndexNotFoundError, build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import IndexMismatchError

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"
OPENAI_KEY, JIRA_TOKEN = "sk-test-openai-key", "jira-token-value"


class OpenAIShapedFake(FakeEmbedder):
    """A fake whose model id matches the real embedder, so the index passes the model check."""

    model_id = "openai:text-embedding-3-small"


def configured(url: str, index: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": url,
        "session_secret": "s" * 40,
        "openai_api_key": OPENAI_KEY,
        "jira_server": "https://example.atlassian.net/",
        "jira_username": "bot@example.com",
        "jira_api_token": JIRA_TOKEN,
        "vector_store_path": str(index),
        "app_env": "staging",
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def pool(migrated_database: str) -> Iterator[ConnectionPool]:
    pool = make_pool(migrated_database)
    yield pool
    pool.close()


@pytest.fixture
def index(tmp_path: Path) -> Path:
    path = tmp_path / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), OpenAIShapedFake(), path)
    return path


def test_missing_settings_are_named_but_never_their_values(
    migrated_database: str, pool: ConnectionPool, tmp_path: Path
) -> None:
    settings = Settings(database_url=migrated_database, session_secret="s" * 40)  # type: ignore[arg-type]
    with pytest.raises(ChatNotConfigured) as raised:
        build_agent_from_settings(settings, pool)
    message = str(raised.value)
    for name in ("OPENAI_API_KEY", "JIRA_SERVER", "JIRA_USERNAME", "JIRA_API_TOKEN"):
        assert name in message


def test_partial_configuration_lists_only_what_is_missing(
    migrated_database: str, tmp_path: Path
) -> None:
    settings = configured(migrated_database, tmp_path, jira_api_token=None)
    assert settings.missing_chat_settings() == ["JIRA_API_TOKEN"]


def test_a_complete_configuration_builds_a_working_agent_without_any_network(
    migrated_database: str, pool: ConnectionPool, index: Path
) -> None:
    built = build_agent_from_settings(
        configured(migrated_database, index), pool, checkpointer=MemorySaver()
    )
    assert isinstance(built.service, AgentService) and built.index_check() is True


def test_a_missing_index_is_reported(
    migrated_database: str, pool: ConnectionPool, tmp_path: Path
) -> None:
    with pytest.raises(IndexNotFoundError):
        build_agent_from_settings(
            configured(migrated_database, tmp_path / "nothing"), pool, checkpointer=MemorySaver()
        )


def test_an_index_built_with_another_embedding_model_is_refused(
    migrated_database: str, pool: ConnectionPool, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), other)  # model: fake-bow-256
    with pytest.raises(IndexMismatchError, match="fake-bow-256"):
        build_agent_from_settings(
            configured(migrated_database, other), pool, checkpointer=MemorySaver()
        )


def test_the_classifier_chain_prefers_jev_when_its_key_is_set(
    migrated_database: str, pool: ConnectionPool, index: Path
) -> None:
    both, _ = build_dependencies(
        configured(migrated_database, index, typesafe_api_key="t-key"), pool
    )
    only_openai, _ = build_dependencies(configured(migrated_database, index), pool)
    assert [c.name for c in both.classifier._classifiers] == ["jev", "openai"]  # type: ignore[attr-defined]
    assert [c.name for c in only_openai.classifier._classifiers] == ["openai"]  # type: ignore[attr-defined]


def test_retrieval_settings_reach_the_answer_service(
    migrated_database: str, pool: ConnectionPool, index: Path
) -> None:
    settings = configured(
        migrated_database, index, retrieval_top_k=7, retrieval_score_threshold=0.42
    )
    deps, _ = build_dependencies(settings, pool)
    assert deps.answers._config.top_k == 7
    assert deps.answers._config.score_threshold == 0.42


def test_jira_settings_map_to_the_adapter_config_and_environment_label(
    migrated_database: str, tmp_path: Path
) -> None:
    config = jira_config(
        configured(migrated_database, tmp_path, app_env="prod", jira_project_key="HELP")
    )
    assert config.server == "https://example.atlassian.net"  # trailing slash removed
    assert config.project_key == "HELP" and config.env_label == "tier0-env:prod"
    assert JIRA_TOKEN not in repr(config)


def test_no_secret_value_appears_in_settings_output(migrated_database: str, tmp_path: Path) -> None:
    shown = repr(configured(migrated_database, tmp_path)) + str(
        configured(migrated_database, tmp_path)
    )
    assert OPENAI_KEY not in shown and JIRA_TOKEN not in shown


# --- the whole server, built from settings alone --------------------------------------


def test_a_fully_configured_server_is_ready_and_notices_when_its_index_disappears(
    migrated_database: str, index: Path
) -> None:
    import shutil

    from fastapi.testclient import TestClient

    from tier0.api.app import create_app

    app = create_app(configured(migrated_database, index))  # builds the real agent
    with TestClient(app) as client:
        assert client.get("/api/ready").json() == {"status": "ready"}
        assert app.state.agent is not None
        assert "knowledge_index" in app.state.readiness_checks

        shutil.rmtree(index)  # the knowledge base vanishes under a running server
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "failing": ["knowledge_index"]}
        assert client.get("/api/health").status_code == 200  # still alive, just not ready


def test_a_missing_index_stops_a_deployed_server_but_only_warns_locally(
    migrated_database: str, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from tier0.api.app import create_app

    missing = tmp_path / "no-index"
    with (
        pytest.raises(IndexNotFoundError),
        TestClient(create_app(configured(migrated_database, missing, app_env="production"))),
    ):
        pass
    with TestClient(create_app(configured(migrated_database, missing, app_env="local"))) as client:
        assert client.app.state.agent is None  # type: ignore[attr-defined]
        assert client.get("/api/ready").status_code == 200  # sign-in work can continue
