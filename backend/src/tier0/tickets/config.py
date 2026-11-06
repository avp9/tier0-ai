from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

ENV_LABEL_PREFIX = "tier0-env:"
SUBMISSION_LABEL_PREFIX = "tier0-sub:"


class ConfigError(Exception):
    """Required configuration is missing."""


@dataclass(frozen=True)
class JiraConfig:
    server: str
    username: str
    api_token: str = field(repr=False)
    environment: str = "local"
    project_key: str = "SUP"
    requester_field_id: str = "customfield_10062"
    requester_field_name: str = "Requester Email"
    issue_type: str = "Task"
    set_priority_field: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 20.0
    max_results: int = 50

    @property
    def env_label(self) -> str:
        return f"{ENV_LABEL_PREFIX}{self.environment}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> JiraConfig:
        required = ("JIRA_SERVER", "JIRA_USERNAME", "JIRA_API_TOKEN")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            server=env["JIRA_SERVER"].rstrip("/"),
            username=env["JIRA_USERNAME"],
            api_token=env["JIRA_API_TOKEN"],
            environment=env.get("APP_ENV", "local"),
            project_key=env.get("JIRA_PROJECT_KEY", "SUP"),
            requester_field_id=env.get("JIRA_REQUESTER_FIELD_ID", "customfield_10062"),
            requester_field_name=env.get("JIRA_REQUESTER_FIELD_NAME", "Requester Email"),
            issue_type=env.get("JIRA_ISSUE_TYPE", "Task"),
            set_priority_field=env.get("JIRA_SET_PRIORITY_FIELD", "false").lower() == "true",
        )
