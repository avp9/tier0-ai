from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from jira.exceptions import JIRAError

from tier0.tickets.config import ConfigError, JiraConfig
from tier0.tickets.errors import (
    TicketNotFound,
    TicketOutcomeUnknown,
    TicketRejected,
    TicketServiceUnavailable,
)
from tier0.tickets.jira_adapter import JiraAdapter, classify_error, default_client_factory
from tier0.tickets.models import TicketDraft

CONFIG = JiraConfig(
    server="https://example.atlassian.net",
    username="bot@x.com",
    api_token="secret-token",  # noqa: S106
    environment="staging",
)
EMAIL = "alice@buzztech.example"


def issue(key: str, *, owner: str = EMAIL, labels: list[str] | None = None, **extra: Any) -> Any:
    fields = {
        "summary": extra.get("summary", "VPN down"),
        "status": {"name": extra.get("status", "In Progress")},
        "updated": extra.get("updated", "2026-01-15T14:30:00.000+0000"),
        "labels": labels if labels is not None else ["tier0-env:staging"],
        CONFIG.requester_field_id: owner,
    }
    return SimpleNamespace(key=key, raw={"fields": fields})


def comment(body: str, *, author: str = "Dana", public: bool | None = None) -> Any:
    raw: dict[str, Any] = {
        "body": body,
        "author": {"displayName": author},
        "created": "2026-01-16T09:00:00.000+0000",
    }
    if public is not None:
        raw["jsdPublic"] = public
    return SimpleNamespace(raw=raw)


class FakeJira:
    def __init__(self, issues: list[Any] | None = None, comments: list[Any] | None = None) -> None:
        self.issues, self.comment_list = issues or [], comments or []
        self.created: list[dict[str, Any]] = []
        self.searches: list[tuple[str, int, str]] = []
        self.error: Exception | None = None

    def create_issue(self, fields: dict[str, Any]) -> Any:
        if self.error:
            raise self.error
        self.created.append(fields)
        return SimpleNamespace(key="SUP-7")

    def search_issues(self, jql_str: str, maxResults: int, fields: str) -> Any:  # noqa: N803
        if self.error:
            raise self.error
        self.searches.append((jql_str, maxResults, fields))
        return self.issues

    def comments(self, issue: str) -> Any:
        if self.error:
            raise self.error
        return self.comment_list


def adapter_with(client: FakeJira, config: JiraConfig = CONFIG) -> JiraAdapter:
    return JiraAdapter(config, client_factory=lambda _config: client)


def draft(**overrides: Any) -> TicketDraft:
    base = {
        "requester_email": EMAIL,
        "summary": "VPN down",
        "description": "Cannot connect.",
        "category": "IT",
    }
    return TicketDraft.model_validate({**base, **overrides})


# --- configuration and connection -------------------------------------------------------


def test_the_client_is_created_lazily_and_once() -> None:
    created: list[JiraConfig] = []

    def factory(config: JiraConfig) -> FakeJira:
        created.append(config)
        return FakeJira()

    adapter = JiraAdapter(CONFIG, client_factory=factory)
    assert created == []
    adapter.find_tickets(EMAIL)
    adapter.find_tickets(EMAIL)
    assert len(created) == 1


def test_an_atlassian_cloud_site_is_marked_cloud_so_search_uses_the_current_endpoint() -> None:
    # Without this the library calls the removed search endpoint and Jira answers HTTP 410.
    client = default_client_factory(CONFIG)
    assert client.deploymentType == "Cloud"  # type: ignore[attr-defined]


def test_a_self_hosted_server_is_not_marked_cloud() -> None:
    config = JiraConfig(
        server="https://jira.internal.example",
        username="bot@x.com",
        api_token="secret-token",  # noqa: S106
        environment="staging",
    )
    assert default_client_factory(config).deploymentType is None  # type: ignore[attr-defined]


def test_config_requires_credentials_and_names_whats_missing() -> None:
    with pytest.raises(ConfigError, match="JIRA_API_TOKEN.*") as raised:
        JiraConfig.from_env({"JIRA_SERVER": "https://x", "JIRA_USERNAME": "u"})
    assert "JIRA_SERVER" not in str(raised.value)


def test_config_defaults_and_overrides() -> None:
    config = JiraConfig.from_env(
        {
            "JIRA_SERVER": "https://x.atlassian.net/",
            "JIRA_USERNAME": "u",
            "JIRA_API_TOKEN": "t",
            "APP_ENV": "prod",
            "JIRA_PROJECT_KEY": "HELP",
        }
    )
    assert config.server == "https://x.atlassian.net" and config.project_key == "HELP"
    assert config.env_label == "tier0-env:prod" and config.requester_field_id == "customfield_10062"


def test_the_api_token_never_appears_in_a_repr() -> None:
    assert "secret-token" not in repr(CONFIG)


# --- creating tickets -------------------------------------------------------------------


def test_create_sends_the_expected_fields_and_returns_key_and_url() -> None:
    client = FakeJira()
    created = adapter_with(client).create_ticket(
        draft(priority="Critical", submission_id="3f2b8c1e-aaaa-bbbb")
    )
    assert (created.key, created.url) == ("SUP-7", "https://example.atlassian.net/browse/SUP-7")
    (fields,) = client.created
    assert fields["project"] == {"key": "SUP"} and fields["issuetype"] == {"name": "Task"}
    assert fields["summary"] == "VPN down"  # no "Bug: " prefix
    assert fields["description"] == "Cannot connect."
    assert fields["customfield_10062"] == EMAIL
    assert fields["labels"] == [
        "tier0-env:staging",
        "tier0-cat:IT",
        "tier0-priority:Critical",
        "tier0-sub:3f2b8c1e-aaaa-bbbb",
    ]
    assert "priority" not in fields  # only when explicitly enabled for this Jira project


def test_priority_field_is_set_only_when_enabled_and_maps_critical_to_highest() -> None:
    config = JiraConfig(**{**CONFIG.__dict__, "set_priority_field": True})
    client = FakeJira()
    adapter_with(client, config).create_ticket(draft(priority="Critical"))
    assert client.created[0]["priority"] == {"name": "Highest"}


def test_project_key_field_ids_and_issue_type_come_from_config() -> None:
    config = JiraConfig(
        **{
            **CONFIG.__dict__,
            "project_key": "HELP",
            "issue_type": "Task",
            "requester_field_id": "customfield_999",
        }
    )
    client = FakeJira()
    adapter_with(client, config).create_ticket(draft())
    assert client.created[0]["project"] == {"key": "HELP"}
    assert client.created[0]["customfield_999"] == EMAIL


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (JIRAError(status_code=400, text="Field 'x' is required"), TicketRejected),
        (JIRAError(status_code=404, text="project not found"), TicketRejected),
        (JIRAError(status_code=401, text="nope"), TicketServiceUnavailable),
        (JIRAError(status_code=403, text="nope"), TicketServiceUnavailable),
        (JIRAError(status_code=429, text="slow down"), TicketServiceUnavailable),
        (JIRAError(status_code=503, text="maintenance"), TicketServiceUnavailable),
        (JIRAError(status_code=500, text="boom"), TicketOutcomeUnknown),
        (JIRAError(status_code=502, text="bad gateway"), TicketOutcomeUnknown),
        (JIRAError(status_code=504, text="timeout"), TicketOutcomeUnknown),
        (requests.exceptions.ConnectTimeout("no route"), TicketServiceUnavailable),
        (requests.exceptions.ReadTimeout("slow"), TicketOutcomeUnknown),
        (requests.exceptions.ConnectionError("Connection aborted."), TicketOutcomeUnknown),
        (requests.exceptions.ConnectionError("Connection reset by peer"), TicketOutcomeUnknown),
        (
            requests.exceptions.ConnectionError("Name or service not known"),
            TicketServiceUnavailable,
        ),
    ],
)
def test_create_failures_are_classified_by_what_may_have_happened(
    error: Exception, expected: type[Exception]
) -> None:
    client = FakeJira()
    client.error = error
    with pytest.raises(expected):
        adapter_with(client).create_ticket(draft())


def test_a_rejection_carries_a_trimmed_platform_message() -> None:
    error = classify_error(JIRAError(status_code=400, text="x" * 1000), write=True)
    assert isinstance(error, TicketRejected) and len(str(error)) == 300


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (JIRAError(status_code=500, text="boom"), TicketServiceUnavailable),
        (requests.exceptions.ReadTimeout("slow"), TicketServiceUnavailable),
        (requests.exceptions.ConnectionError("Connection aborted."), TicketServiceUnavailable),
    ],
)
def test_the_same_failures_on_reads_are_just_unavailable(
    error: Exception, expected: type[Exception]
) -> None:
    client = FakeJira()
    client.error = error
    with pytest.raises(expected):
        adapter_with(client).find_tickets(EMAIL)


def test_unrelated_exceptions_are_not_swallowed() -> None:
    client = FakeJira()
    client.error = KeyError("programming bug")
    with pytest.raises(KeyError):
        adapter_with(client).create_ticket(draft())


# --- looking tickets up -----------------------------------------------------------------


def test_find_tickets_builds_a_scoped_quoted_query_and_maps_results() -> None:
    client = FakeJira([issue("SUP-2", status="Resolved"), issue("SUP-1")])
    found = adapter_with(client).find_tickets(EMAIL)
    (jql, max_results, fields) = client.searches[0]
    assert jql == (
        'project = "SUP" AND "Requester Email" = "alice@buzztech.example" '
        'AND labels = "tier0-env:staging" ORDER BY updated DESC'
    )
    assert max_results == 50 and "labels" in fields and "customfield_10062" in fields
    assert [(t.key, t.status) for t in found] == [("SUP-2", "Resolved"), ("SUP-1", "In Progress")]
    assert found[0].url == "https://example.atlassian.net/browse/SUP-2"
    assert found[0].updated_at == datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_results_for_another_requester_or_environment_are_dropped_even_if_returned() -> None:
    client = FakeJira(
        [
            issue("SUP-1", owner="mallory@buzztech.example"),
            issue("SUP-2", labels=["tier0-env:prod"]),
            issue("SUP-3", labels=[]),
            issue("SUP-4", owner="ALICE@buzztech.example"),
        ]
    )
    assert [t.key for t in adapter_with(client).find_tickets(EMAIL)] == ["SUP-4"]


def test_an_invalid_email_never_reaches_jira() -> None:
    client = FakeJira()
    with pytest.raises(ValueError, match="email"):
        adapter_with(client).find_tickets('x" OR project = "SECRET')
    assert client.searches == []


def test_unparseable_times_become_none_and_missing_status_is_unknown() -> None:
    odd = issue("SUP-1", updated="yesterday")
    odd.raw["fields"]["status"] = None
    (summary,) = adapter_with(FakeJira([odd])).find_tickets(EMAIL)
    assert summary.updated_at is None and summary.status == "Unknown"


def test_get_ticket_scopes_by_key_requester_and_environment() -> None:
    client = FakeJira([issue("SUP-9")], [comment("We are looking into it.")])
    details = adapter_with(client).get_ticket("SUP-9", EMAIL)
    assert client.searches[0][0] == (
        'project = "SUP" AND "Requester Email" = "alice@buzztech.example" '
        'AND labels = "tier0-env:staging" AND key = "SUP-9"'
    )
    assert details.summary.key == "SUP-9"
    assert [(c.author, c.body) for c in details.comments] == [("Dana", "We are looking into it.")]


def test_someone_elses_or_unknown_ticket_is_not_found_without_reading_comments() -> None:
    client = FakeJira([])
    with pytest.raises(TicketNotFound):
        adapter_with(client).get_ticket("SUP-9", EMAIL)
    foreign = FakeJira([issue("SUP-9", owner="mallory@buzztech.example")])
    with pytest.raises(TicketNotFound):
        adapter_with(foreign).get_ticket("SUP-9", EMAIL)


def test_malformed_keys_are_rejected_before_any_query() -> None:
    client = FakeJira()
    with pytest.raises(ValueError, match="key"):
        adapter_with(client).get_ticket('SUP-1" OR key > "A', EMAIL)
    assert client.searches == []


def test_internal_comments_are_never_shown_and_long_threads_are_trimmed() -> None:
    comments = [comment(f"note {i}") for i in range(14)]
    comments.insert(3, comment("internal only", public=False))
    comments.append(comment("x" * 5000, public=True))
    client = FakeJira([issue("SUP-9")], comments)
    shown = adapter_with(client).get_ticket("SUP-9", EMAIL).comments
    assert len(shown) == 10 and all("internal" not in c.body for c in shown)
    assert len(shown[-1].body) == 1000


def test_find_by_submission_id_searches_the_submission_label() -> None:
    client = FakeJira([issue("SUP-5")])
    found = adapter_with(client).find_by_submission_id("3f2b8c1e-aaaa-bbbb", EMAIL)
    assert found is not None and found.key == "SUP-5"
    assert client.searches[0][0].endswith('AND labels = "tier0-sub:3f2b8c1e-aaaa-bbbb"')
    assert adapter_with(FakeJira([])).find_by_submission_id("3f2b8c1e-aaaa-bbbb", EMAIL) is None
    with pytest.raises(ValueError, match="submission"):
        adapter_with(client).find_by_submission_id("bad id!", EMAIL)
