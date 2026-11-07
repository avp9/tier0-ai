from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tier0.tickets.models import validate_email

# Environments that are not real deployments: plain-HTTP cookies, API docs, tolerant chat start-up,
# and the only places fake providers may run.
LOCAL_ENVS = frozenset({"local", "e2e"})


Gender = Literal["male", "female"]


class DemoUser(BaseModel):
    """A demo account created at start-up from the DEMO_USERS secret."""

    email: str
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=256)
    gender: Gender | None = None  # picks the avatar; unset shows a neutral one

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, value: str) -> str:
        return validate_email(value).lower()


class Settings(BaseSettings):
    """Application settings, read from environment variables."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "local"
    database_url: str
    session_secret: SecretStr = Field(min_length=32)
    session_max_age_hours: int = Field(default=8, ge=1, le=72)
    demo_users: list[DemoUser] = Field(default_factory=list)
    run_migrations_on_start: bool = True
    static_dir: str | None = None  # the built web app (frontend/dist) to serve, if any
    login_max_failures_per_email: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    chat_messages_per_minute: int = Field(default=20, ge=1)
    # How many reverse proxies sit in front of the app (0 locally; 1 behind Container Apps ingress).
    # The client address is then the Nth entry from the right of X-Forwarded-For, which a caller
    # cannot forge because each proxy appends to the header.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=3)
    max_request_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["auto", "json", "text"] = "auto"  # auto: text locally, JSON elsewhere

    llm_provider: Literal["openai", "fake"] = "openai"
    ticketing_provider: Literal["jira", "fake"] = "jira"
    e2e_token: SecretStr | None = None

    # Chat: language models, knowledge index and ticketing. Optional so the server can start
    # for sign-in work without them; see ``missing_chat_settings``.
    openai_api_key: SecretStr | None = None
    openai_model_name: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    typesafe_api_key: SecretStr | None = None
    vector_store_path: str = ".data/chroma"
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_score_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    jira_server: str | None = None
    jira_username: str | None = None
    jira_api_token: SecretStr | None = None
    jira_project_key: str = "SUP"
    jira_requester_field_id: str = "customfield_10062"
    jira_requester_field_name: str = "Requester Email"
    jira_issue_type: str = "Task"
    jira_set_priority_field: bool = False

    @model_validator(mode="after")
    def _fakes_only_outside_deployments(self) -> Settings:
        if (self.llm_provider == "fake" or self.ticketing_provider == "fake") and not self.is_local:
            raise ValueError("fake providers are only allowed when APP_ENV is local or e2e")
        return self

    @property
    def log_json(self) -> bool:
        return self.log_format == "json" or (self.log_format == "auto" and not self.is_local)

    @property
    def is_local(self) -> bool:
        return self.app_env in LOCAL_ENVS

    @property
    def e2e_controls_enabled(self) -> bool:
        """Test-only endpoints exist only in the isolated e2e environment, with a token."""
        return (
            self.app_env == "e2e"
            and self.llm_provider == "fake"
            and self.ticketing_provider == "fake"
            and self.e2e_token is not None
        )

    @property
    def cookie_secure(self) -> bool:
        """Browsers only send Secure cookies over HTTPS; local development uses plain HTTP."""
        return not self.is_local

    def missing_chat_settings(self) -> list[str]:
        """Environment variables that chat needs but that are not set."""
        required: dict[str, object] = {}
        if self.llm_provider == "openai":
            required["OPENAI_API_KEY"] = self.openai_api_key
        if self.ticketing_provider == "jira":
            required["JIRA_SERVER"] = self.jira_server
            required["JIRA_USERNAME"] = self.jira_username
            required["JIRA_API_TOKEN"] = self.jira_api_token
        return [name for name, value in required.items() if not value]
