from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import requests
from jira import JIRA
from jira.exceptions import JIRAError

from tier0.tickets.config import SUBMISSION_LABEL_PREFIX, JiraConfig
from tier0.tickets.errors import (
    TicketError,
    TicketNotFound,
    TicketOutcomeUnknown,
    TicketRejected,
    TicketServiceUnavailable,
)
from tier0.tickets.jql import quote
from tier0.tickets.models import (
    CreatedTicket,
    TicketComment,
    TicketDetails,
    TicketDraft,
    TicketSummary,
    validate_email,
    validate_submission_id,
    validate_ticket_key,
)

PRIORITY_NAMES = {"Low": "Low", "Medium": "Medium", "High": "High", "Critical": "Highest"}
MAX_COMMENTS = 10
MAX_COMMENT_CHARS = 1000
MAX_ERROR_CHARS = 300
_UNAVAILABLE_STATUSES = {429, 503}
_AUTH_STATUSES = {401, 403}
_REJECTED_STATUSES = {400, 404, 405, 409, 413, 415, 422}


class JiraClient(Protocol):
    """The part of ``jira.JIRA`` this adapter uses."""

    def create_issue(self, fields: dict[str, Any]) -> Any: ...

    def search_issues(self, jql_str: str, maxResults: int, fields: str) -> Any: ...  # noqa: N803

    def comments(self, issue: str) -> Any: ...


def default_client_factory(config: JiraConfig) -> JiraClient:
    client = JIRA(
        server=config.server,
        basic_auth=(config.username, config.api_token),
        timeout=(config.connect_timeout, config.read_timeout),
        max_retries=0,
        get_server_info=False,
    )
    # Skipping the server-info call (to keep start-up offline) leaves the library unsure whether
    # this is Jira Cloud. Atlassian removed the old search endpoint (HTTP 410), and the library
    # only uses the replacement once it knows the site is Cloud, so say so for atlassian.net.
    host = urlparse(config.server).hostname or ""
    if host.endswith(".atlassian.net"):
        client.deploymentType = "Cloud"
    return cast(JiraClient, client)


def classify_error(error: Exception, *, write: bool) -> TicketError:
    """Map a transport or API failure to a typed error.

    For writes, a failure after the request may have reached the server is "unknown",
    never "unavailable": claiming nothing happened could duplicate a ticket on retry.
    """
    if isinstance(error, JIRAError):
        status = error.status_code or 0
        if status in _AUTH_STATUSES:
            return TicketServiceUnavailable("ticketing authentication or permission problem")
        if status in _UNAVAILABLE_STATUSES:
            return TicketServiceUnavailable(f"ticketing service unavailable (HTTP {status})")
        if status in _REJECTED_STATUSES:
            return TicketRejected((error.text or "request rejected")[:MAX_ERROR_CHARS])
        if status >= 500:
            message = f"ticketing server error (HTTP {status})"
            return TicketOutcomeUnknown(message) if write else TicketServiceUnavailable(message)
        message = f"unexpected ticketing response (HTTP {status})"
        # Status 0 means no HTTP answer was read, so a write may or may not have arrived.
        return (
            TicketOutcomeUnknown(message)
            if write and status == 0
            else TicketServiceUnavailable(message)
        )

    if isinstance(error, requests.exceptions.ConnectTimeout):
        return TicketServiceUnavailable("could not connect to the ticketing service")
    if isinstance(error, requests.exceptions.Timeout):
        message = "ticketing service timed out"
        return TicketOutcomeUnknown(message) if write else TicketServiceUnavailable(message)
    if isinstance(error, requests.exceptions.ConnectionError):
        dropped_midway = any(w in str(error).lower() for w in ("aborted", "reset", "disconnected"))
        if write and dropped_midway:
            return TicketOutcomeUnknown("connection to the ticketing service dropped")
        return TicketServiceUnavailable("could not reach the ticketing service")
    if write:  # some other failure after the request may have been sent
        return TicketOutcomeUnknown("ticketing request failed after it may have been sent")
    return TicketServiceUnavailable("ticketing request failed")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for parse in (
        lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f%z"),
        datetime.fromisoformat,
    ):
        try:
            return parse(value)
        except ValueError:
            continue
    return None


class JiraAdapter:
    def __init__(
        self,
        config: JiraConfig,
        client_factory: Callable[[JiraConfig], JiraClient] = default_client_factory,
    ) -> None:
        self._config = config
        self._factory = client_factory
        self._client: JiraClient | None = None

    @property
    def _jira(self) -> JiraClient:
        if self._client is None:
            self._client = self._factory(self._config)
        return self._client

    def url_for(self, key: str) -> str:
        return f"{self._config.server}/browse/{key}"

    def create_ticket(self, draft: TicketDraft) -> CreatedTicket:
        try:
            issue = self._jira.create_issue(fields=self.build_fields(draft))
        except (JIRAError, requests.exceptions.RequestException) as error:
            raise classify_error(error, write=True) from error
        try:
            key = str(issue.key)
        except AttributeError as error:  # created, but the answer is unusable
            raise TicketOutcomeUnknown("ticketing response did not name the new ticket") from error
        if not key or key == "None":
            raise TicketOutcomeUnknown("ticketing response did not name the new ticket")
        return CreatedTicket(key=key, url=self.url_for(key))

    def find_tickets(self, requester_email: str) -> list[TicketSummary]:
        email = validate_email(requester_email)
        return self._search(f"{self._scope(email)} ORDER BY updated DESC", email)

    def get_ticket(self, key: str, requester_email: str) -> TicketDetails:
        email = validate_email(requester_email)
        validate_ticket_key(key)
        found = self._search(f"{self._scope(email)} AND key = {quote(key)}", email)
        if not found:
            raise TicketNotFound(key)
        try:
            raw_comments = self._jira.comments(key)
        except (JIRAError, requests.exceptions.RequestException) as error:
            raise classify_error(error, write=False) from error
        try:
            comments = self._comments(raw_comments)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise TicketServiceUnavailable("unexpected ticketing response") from error
        return TicketDetails(summary=found[0], comments=comments)

    def find_by_submission_id(
        self, submission_id: str, requester_email: str
    ) -> TicketSummary | None:
        email = validate_email(requester_email)
        label = f"{SUBMISSION_LABEL_PREFIX}{validate_submission_id(submission_id)}"
        found = self._search(f"{self._scope(email)} AND labels = {quote(label)}", email)
        return found[0] if found else None

    def build_fields(self, draft: TicketDraft) -> dict[str, Any]:
        config = self._config
        labels = [config.env_label, f"tier0-cat:{draft.category}"]
        if draft.priority:
            labels.append(f"tier0-priority:{draft.priority}")
        if draft.submission_id:
            labels.append(f"{SUBMISSION_LABEL_PREFIX}{draft.submission_id}")
        fields: dict[str, Any] = {
            "project": {"key": config.project_key},
            "summary": draft.summary,
            "description": draft.description,
            "issuetype": {"name": config.issue_type},
            config.requester_field_id: draft.requester_email,
            "labels": labels,
        }
        if draft.priority and config.set_priority_field:
            fields["priority"] = {"name": PRIORITY_NAMES[draft.priority]}
        return fields

    def _scope(self, email: str) -> str:
        config = self._config
        return (
            f"project = {quote(config.project_key)} "
            f"AND {quote(config.requester_field_name)} = {quote(email)} "
            f"AND labels = {quote(config.env_label)}"
        )

    def _search(self, jql: str, email: str) -> list[TicketSummary]:
        config = self._config
        fields = f"summary,status,updated,labels,{config.requester_field_id}"
        try:
            issues = self._jira.search_issues(jql, maxResults=config.max_results, fields=fields)
        except (JIRAError, requests.exceptions.RequestException) as error:
            raise classify_error(error, write=False) from error
        try:
            return self._summaries(issues, email)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise TicketServiceUnavailable("unexpected ticketing response") from error

    def _summaries(self, issues: Any, email: str) -> list[TicketSummary]:
        config = self._config
        summaries: list[TicketSummary] = []
        for issue in issues:
            raw_fields = issue.raw.get("fields") or {}
            owner = str(raw_fields.get(config.requester_field_id) or "")
            labels = raw_fields.get("labels") or []
            if not isinstance(labels, list):
                continue
            # Re-check ownership and environment here: the query is a filter, not a guarantee.
            if owner.casefold() != email.casefold() or config.env_label not in labels:
                continue
            summaries.append(
                TicketSummary(
                    key=str(issue.key),
                    title=str(raw_fields.get("summary") or ""),
                    status=str((raw_fields.get("status") or {}).get("name") or "Unknown"),
                    updated_at=_parse_time(raw_fields.get("updated")),
                    url=self.url_for(str(issue.key)),
                )
            )
        return summaries

    @staticmethod
    def _comments(raw_comments: Any) -> list[TicketComment]:
        public = [c for c in raw_comments if c.raw.get("jsdPublic") is not False]
        return [
            TicketComment(
                author=str(c.raw.get("author", {}).get("displayName") or "Support"),
                body=str(c.raw.get("body") or "")[:MAX_COMMENT_CHARS],
                created_at=_parse_time(c.raw.get("created")),
            )
            for c in public[-MAX_COMMENTS:]
        ]
