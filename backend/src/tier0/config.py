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

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def cookie_secure(self) -> bool:
        """Browsers only send Secure cookies over HTTPS; local development uses plain HTTP."""
        return not self.is_local
