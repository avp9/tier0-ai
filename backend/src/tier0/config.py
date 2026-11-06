from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tier0.tickets.models import validate_email


class DemoUser(BaseModel):
    """A demo account created at start-up from the DEMO_USERS secret."""

    email: str
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=256)

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
    login_max_failures_per_email: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    chat_messages_per_minute: int = Field(default=20, ge=1)

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

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def cookie_secure(self) -> bool:
        """Browsers only send Secure cookies over HTTPS; local development uses plain HTTP."""
        return not self.is_local

    def missing_chat_settings(self) -> list[str]:
        """Environment variables that chat needs but that are not set."""
        required = {
            "OPENAI_API_KEY": self.openai_api_key,
            "JIRA_SERVER": self.jira_server,
            "JIRA_USERNAME": self.jira_username,
            "JIRA_API_TOKEN": self.jira_api_token,
        }
        return [name for name, value in required.items() if not value]
